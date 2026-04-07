# -*- coding: utf-8 -*-
"""
Modulo per il suggerimento intelligente di appuntamenti.

Analizza la pianificazione esistente e suggerisce la giornata e fascia oraria
migliore per un nuovo appuntamento, tenendo conto di:
- Vicinanza geografica con altri contatori già pianificati
- Appuntamenti già fissati (priorità alta)
- Capacità delle fasce orarie (max tappe per fascia)
- Vincoli dell'utente (giorni/orari specifici)
- Sovrapposizioni operatore

Utilizzato da:
- routes/distinte.py (API suggerimento)
"""
import logging
import math
import re
from collections import defaultdict
from datetime import datetime, timedelta

from moduli.zone_geografiche import calcola_distanza_km, is_valid_coordinate
from moduli.fasce_orarie import FASCE_ORARIE, TAPPE_PER_FASCIA
from moduli.percorso import estrai_orario_e_fascia

logger = logging.getLogger('mappa_algeco')

# ============================================================================
# COSTANTI
# ============================================================================

# Peso fattori di scoring (somma = 1.0)
PESO_VICINANZA = 0.35       # Quanto conta la vicinanza a tappe esistenti
PESO_VICINANZA_APP = 0.15   # Bonus per vicinanza a appuntamenti già fissati
PESO_COERENZA_FASCIA = 0.20  # Bonus per coerenza fascia con contatori vicini
PESO_CARICO_FASCIA = 0.10   # Penalità per fasce già piene
PESO_POSIZIONE_PERCORSO = 0.10  # Bonus per posizionamento ottimale nel percorso
PESO_DISTRIBUZIONE = 0.10   # Bonus per distribuzione equilibrata tra giorni

# Raggio massimo per considerare un contatore "vicino" (km)
RAGGIO_VICINANZA_KM = 15.0

# Raggio per vicinanza stretta (decadimento esponenziale)
# Più basso = differenza più marcata tra distanze brevi
RAGGIO_VICINANZA_STRETTA_KM = 1.5

# Soglia distanza MEDIA per considerare il contatore "periferico" rispetto al cluster
SOGLIA_PERIFERICO_KM = 0.4

# Giorni futuri massimi da analizzare
MAX_GIORNI_FUTURI = 30


def _fascia_to_minuti(fascia):
    """Converte fascia '8/11' in (480, 660) minuti."""
    try:
        parti = fascia.split('/')
        return int(parti[0]) * 60, int(parti[1]) * 60
    except (ValueError, IndexError):
        return None, None


def _orario_in_fascia(ora_min, ora_max, fascia_inizio, fascia_fine):
    """Verifica se un orario (min-max in minuti) rientra in una fascia."""
    if ora_min is None or fascia_inizio is None:
        return False
    if ora_max is None or ora_max == ora_min:
        # Orario preciso: deve cadere nella fascia
        return fascia_inizio <= ora_min <= fascia_fine
    # Fascia: deve sovrapporsi
    return ora_min < fascia_fine and ora_max > fascia_inizio


def _conta_tappe_per_fascia(contatori_giorno, operatore=None):
    """
    Conta quante tappe ci sono per ogni fascia oraria in un dato giorno/operatore.
    Se ordine_tappa non è assegnato (999), stima la distribuzione in base al totale.
    
    Returns:
        dict: {fascia: count}, dict: {fascia: [lista contatori]}
    """
    conteggio = {f: 0 for f in FASCE_ORARIE}
    contatori_fascia = {f: [] for f in FASCE_ORARIE}
    
    contatori_filtrati = [
        c for c in contatori_giorno
        if (not operatore or c.get('tecnico', '') == operatore)
        and not c.get('annullato', False)
    ]
    
    # Separa contatori con ordine reale da quelli senza
    con_ordine = [c for c in contatori_filtrati if c.get('ordine_tappa', 999) != 999]
    senza_ordine = [c for c in contatori_filtrati if c.get('ordine_tappa', 999) == 999]
    
    if con_ordine:
        # Usa ordine_tappa reale per quelli che ce l'hanno
        for c in con_ordine:
            ordine = c.get('ordine_tappa', 999)
            fascia_idx = (ordine - 1) // TAPPE_PER_FASCIA
            if 0 <= fascia_idx < len(FASCE_ORARIE):
                fascia = FASCE_ORARIE[fascia_idx]
                conteggio[fascia] += 1
                contatori_fascia[fascia].append(c)
        
        # Distribuisci quelli senza ordine equamente tra le fasce
        if senza_ordine:
            n = len(senza_ordine)
            n_fasce = len(FASCE_ORARIE)
            base = n // n_fasce
            resto = n % n_fasce
            
            idx = 0
            for f_idx, fascia in enumerate(FASCE_ORARIE):
                count = base + (1 if f_idx < resto else 0)
                for _ in range(count):
                    if idx < n:
                        conteggio[fascia] += 1
                        contatori_fascia[fascia].append(senza_ordine[idx])
                        idx += 1
    else:
        # Nessun ordine reale: stima distribuzione equa tra le fasce
        n = len(contatori_filtrati)
        n_fasce = len(FASCE_ORARIE)
        base = n // n_fasce
        resto = n % n_fasce
        
        idx = 0
        for f_idx, fascia in enumerate(FASCE_ORARIE):
            count = base + (1 if f_idx < resto else 0)
            for _ in range(count):
                if idx < n:
                    conteggio[fascia] += 1
                    contatori_fascia[fascia].append(contatori_filtrati[idx])
                    idx += 1
    
    return conteggio, contatori_fascia


def _calcola_distanza_minima(lat, lon, contatori):
    """Calcola la distanza minima e media verso una lista di contatori."""
    if not contatori or lat is None or lon is None:
        return float('inf'), float('inf')
    
    distanze = []
    for c in contatori:
        c_lat, c_lon = c.get('lat'), c.get('lon')
        if c_lat and c_lon and is_valid_coordinate(c_lat, c_lon):
            d = calcola_distanza_km(lat, lon, float(c_lat), float(c_lon))
            distanze.append(d)
    
    if not distanze:
        return float('inf'), float('inf')
    
    return min(distanze), sum(distanze) / len(distanze)


def _score_vicinanza(dist_min, dist_media):
    """Calcola score vicinanza (0-1, 1 = vicinissimo)."""
    if dist_min == float('inf'):
        return 0
    # Score basato su distanza minima con decadimento esponenziale
    score_min = math.exp(-dist_min / RAGGIO_VICINANZA_STRETTA_KM)
    # Score basato su distanza media (meno peso)
    score_media = math.exp(-dist_media / RAGGIO_VICINANZA_KM)
    return 0.7 * score_min + 0.3 * score_media


def _score_carico_fascia(conteggio_fascia, fascia):
    """
    Calcola score carico fascia (0-1, 1 = fascia libera).
    Penalizza fasce già piene o quasi piene.
    """
    tappe = conteggio_fascia.get(fascia, 0)
    if tappe >= TAPPE_PER_FASCIA:
        return 0  # Fascia piena
    return 1.0 - (tappe / TAPPE_PER_FASCIA)


def _fascia_da_orari_lettere(orari_lettere):
    """Parse 'dalle X:00 alle Y:00' in fascia FASCE_ORARIE corrispondente."""
    if not orari_lettere:
        return None
    m = re.search(r'(\d{1,2})[:.]?\d*\s*(?:alle?|[-/])\s*(\d{1,2})', orari_lettere)
    if m:
        start = int(m.group(1))
        end = int(m.group(2))
        fascia_str = f'{start}/{end}'
        if fascia_str in FASCE_ORARIE:
            return fascia_str
    return None


def _fascia_contatore(contatore):
    """Determina la fascia di un contatore: da orari_lettere se disponibile, altrimenti da ordine_tappa."""
    fascia = _fascia_da_orari_lettere(contatore.get('orari_lettere', ''))
    if fascia:
        return fascia
    ordine = contatore.get('ordine_tappa', 999)
    if ordine != 999:
        fascia_idx = (ordine - 1) // TAPPE_PER_FASCIA
        if 0 <= fascia_idx < len(FASCE_ORARIE):
            return FASCE_ORARIE[fascia_idx]
    return None


def _score_posizione_percorso(fascia, dist_media, conteggio_fascia,
                               target_lat, target_lon, deposito):
    """
    Calcola lo score di posizionamento ottimale nel percorso (0-1).
    
    Logica:
    - Se il contatore è vicino al cluster (dist_media < SOGLIA_PERIFERICO_KM) → tutte le fasce ok (0.5 neutro)
    - Se il contatore è periferico (dist_media >= soglia):
      * Preferisci prima mattina (8/11) = prima tappa, per andata
      * O ultima fascia (14/17) = ultima tappa, per ritorno
      * Scegli in base a: quale fascia è più libera, appuntamenti esistenti nelle fasce,
        e vicinanza al deposito (mattina se più vicino al deposito)
    """
    fascia_idx = FASCE_ORARIE.index(fascia) if fascia in FASCE_ORARIE else -1
    if fascia_idx < 0:
        return 0.5
    
    n_fasce = len(FASCE_ORARIE)
    prima_fascia = 0
    ultima_fascia = n_fasce - 1
    
    # Se vicino al cluster, nessuna preferenza di posizione
    if dist_media < SOGLIA_PERIFERICO_KM:
        return 0.5
    
    # Contatore periferico: calcola preferenza mattina vs sera
    # Default: mattina preferita (partenza per il periferico, poi rientra nel cluster)
    pref_mattina = 0.6
    pref_sera = 0.4
    
    # 1) Fascia più libera tra prima e ultima
    tappe_prima = conteggio_fascia.get(FASCE_ORARIE[prima_fascia], 0)
    tappe_ultima = conteggio_fascia.get(FASCE_ORARIE[ultima_fascia], 0)
    
    if tappe_prima >= TAPPE_PER_FASCIA and tappe_ultima < TAPPE_PER_FASCIA:
        # Prima fascia piena, ultima libera → sera
        pref_sera += 0.3
    elif tappe_ultima >= TAPPE_PER_FASCIA and tappe_prima < TAPPE_PER_FASCIA:
        # Ultima piena, prima libera → mattina
        pref_mattina += 0.3
    elif tappe_prima < tappe_ultima:
        pref_mattina += 0.1
    elif tappe_ultima < tappe_prima:
        pref_sera += 0.1
    
    # 2) Vicinanza al deposito (se disponibile): mattina se più vicino al deposito
    if deposito and deposito.get('Lat') and deposito.get('Lon'):
        try:
            dist_deposito = calcola_distanza_km(
                target_lat, target_lon,
                float(deposito['Lat']), float(deposito['Lon'])
            )
            if dist_deposito < RAGGIO_VICINANZA_KM / 2:
                pref_mattina += 0.15  # Vicino al deposito → prima tappa (partenza)
        except (ValueError, TypeError):
            pass
    
    # Determina se preferire mattina o sera
    if pref_mattina >= pref_sera:
        # Preferenza mattina: score decresce verso le fasce centrali, risale un po' per l'ultima
        if fascia_idx == prima_fascia:
            return 0.9
        elif fascia_idx == ultima_fascia:
            return 0.6
        else:
            # Fasce centrali penalizzate
            return 0.3
    else:
        # Preferenza sera: score cresce verso l'ultima fascia
        if fascia_idx == ultima_fascia:
            return 0.9
        elif fascia_idx == prima_fascia:
            return 0.6
        else:
            return 0.3


def suggerisci_appuntamento(contatori_tutti, target_lat, target_lon,
                            vincoli_giorni=None, vincoli_orari=None,
                            deposito=None,
                            ora_inizio_giornata=480, ora_fine_giornata=1020):
    """
    Suggerisce la giornata e fascia oraria migliore per un appuntamento.
    Analizza TUTTI gli operatori attivi (inclusi contatori senza operatore assegnato).
    
    Args:
        contatori_tutti: lista completa di contatori da mappa_data.json
        target_lat, target_lon: coordinate del contatore per cui fissare l'appuntamento
        vincoli_giorni: lista di date ammesse (formato YYYY-MM-DD), None = qualsiasi
        vincoli_orari: tuple (ora_min, ora_max) in minuti, None = qualsiasi
        deposito: dict con Lat/Lon del deposito
        ora_inizio_giornata: inizio giornata in minuti
        ora_fine_giornata: fine giornata in minuti
    
    Returns:
        dict con:
        - suggerimenti: lista di {data, fascia, operatore, score, motivo, warnings}
        - warnings_globali: warnings generali
    """
    if not is_valid_coordinate(target_lat, target_lon):
        return {'suggerimenti': [], 'warnings_globali': ['Coordinate contatore non valide']}
    
    target_lat = float(target_lat)
    target_lon = float(target_lon)
    
    # Raggruppa contatori per data e operatore
    contatori_per_data = defaultdict(list)
    for c in contatori_tutti:
        data = c.get('data', '')
        if data and not c.get('annullato', False):
            contatori_per_data[data].append(c)
    
    # Determina date candidate
    oggi = datetime.now().date()
    date_candidate = set()
    
    for data_str in contatori_per_data:
        try:
            dt = datetime.strptime(data_str, '%Y-%m-%d').date()
            if dt >= oggi:
                date_candidate.add(data_str)
        except ValueError:
            continue
    
    # Aggiungi date future anche se vuote (fino a MAX_GIORNI_FUTURI)
    for i in range(MAX_GIORNI_FUTURI):
        dt = oggi + timedelta(days=i)
        if dt.weekday() < 6:  # Solo giorni lavorativi (lun-sab)
            date_candidate.add(dt.strftime('%Y-%m-%d'))
    
    # Filtra per vincoli giorni
    if vincoli_giorni:
        date_candidate = date_candidate.intersection(set(vincoli_giorni))
    
    if not date_candidate:
        return {'suggerimenti': [], 'warnings_globali': ['Nessuna data candidata trovata']}
    
    # Calcola score per ogni combinazione data/fascia/operatore
    suggerimenti = []
    warnings_globali = []
    
    # Distribuzione tappe tra i giorni (per bonus distribuzione)
    tappe_per_giorno = {d: len(contatori_per_data.get(d, [])) for d in date_candidate}
    max_tappe_giorno = max(tappe_per_giorno.values()) if tappe_per_giorno else 1
    
    # Identifica operatori attivi (da tutte le date, non solo candidate)
    operatori = set()
    for c in contatori_tutti:
        tec = c.get('tecnico', '')
        if tec and tec != 'TUTTI':
            operatori.add(tec)
    if not operatori:
        return {'suggerimenti': [], 'warnings_globali': ['Nessun operatore trovato nella pianificazione']}
    
    for data in sorted(date_candidate):
        contatori_giorno = contatori_per_data.get(data, [])
        
        for operatore in sorted(operatori):
            # Contatori di questo operatore in questa data
            contatori_op = [c for c in contatori_giorno if c.get('tecnico') == operatore]
            
            if not contatori_op and not vincoli_giorni:
                # Nessuna tappa per questo operatore in questo giorno - skip se non vincolato
                continue
            
            # Conteggio tappe per fascia
            conteggio, contatori_fascia = _conta_tappe_per_fascia(contatori_giorno, operatore)
            
            # Filtra fasce per vincoli orari
            fasce_candidate = FASCE_ORARIE[:]
            if vincoli_orari:
                v_min, v_max = vincoli_orari
                fasce_filtrate = []
                for f in fasce_candidate:
                    f_ini, f_fin = _fascia_to_minuti(f)
                    if f_ini is not None and _orario_in_fascia(v_min, v_max, f_ini, f_fin):
                        fasce_filtrate.append(f)
                fasce_candidate = fasce_filtrate
            
            for fascia in fasce_candidate:
                warnings = []
                
                # -- Score vicinanza a tutte le tappe del giorno --
                dist_min_tutte, dist_media_tutte = _calcola_distanza_minima(
                    target_lat, target_lon, contatori_op
                )
                score_vicinanza = _score_vicinanza(dist_min_tutte, dist_media_tutte)
                
                # -- Score vicinanza a appuntamenti già fissati --
                app_giorno = [c for c in contatori_op if c.get('is_appuntamento')]
                dist_min_app, dist_media_app = _calcola_distanza_minima(
                    target_lat, target_lon, app_giorno
                )
                score_vicinanza_app = _score_vicinanza(dist_min_app, dist_media_app)
                # Bonus proporzionale al numero di appuntamenti vicini
                if app_giorno:
                    app_vicini = sum(
                        1 for c in app_giorno
                        if c.get('lat') and c.get('lon') and is_valid_coordinate(c['lat'], c['lon'])
                        and calcola_distanza_km(target_lat, target_lon, float(c['lat']), float(c['lon'])) < RAGGIO_VICINANZA_KM
                    )
                    if app_vicini > 1:
                        score_vicinanza_app = min(1.0, score_vicinanza_app * (1 + 0.15 * (app_vicini - 1)))
                
                # -- Score vicinanza a contatori NELLA STESSA FASCIA --
                # Calcola la fascia a cui appartengono i contatori PIÙ VICINI
                # e preferisci quella fascia (o fasce adiacenti)
                contatori_stessa_fascia = contatori_fascia.get(fascia, [])
                score_coerenza_fascia = 0.0
                
                if contatori_op:
                    # Trova i contatori più vicini con le loro fasce (da orari_lettere o ordine_tappa)
                    vicini_con_fascia = []
                    for c in contatori_op:
                        c_lat, c_lon = c.get('lat'), c.get('lon')
                        if c_lat and c_lon and is_valid_coordinate(c_lat, c_lon):
                            d = calcola_distanza_km(target_lat, target_lon, float(c_lat), float(c_lon))
                            f_c = _fascia_contatore(c)
                            if d < RAGGIO_VICINANZA_KM and f_c:
                                vicini_con_fascia.append((d, f_c))
                    
                    if vicini_con_fascia:
                        # Ordina per distanza, prendi i più vicini
                        vicini_con_fascia.sort(key=lambda x: x[0])
                        top_vicini = vicini_con_fascia[:min(5, len(vicini_con_fascia))]
                        
                        # Peso inversamente proporzionale alla distanza
                        fascia_idx_target = FASCE_ORARIE.index(fascia)
                        peso_totale = 0.0
                        score_pesato = 0.0
                        for dist_v, fascia_v in top_vicini:
                            peso = 1.0 / max(dist_v, 0.05)  # Peso inverso distanza
                            fascia_idx_v = FASCE_ORARIE.index(fascia_v)
                            # Distanza tra fasce (0 = stessa, 1 = adiacente, ...)
                            diff_fasce = abs(fascia_idx_target - fascia_idx_v)
                            # Score: 1.0 se stessa fascia, 0.7 se adiacente, 0.3 se 2 fasce, 0 se >2
                            if diff_fasce == 0:
                                s = 1.0
                            elif diff_fasce == 1:
                                s = 0.7
                            elif diff_fasce == 2:
                                s = 0.3
                            else:
                                s = 0.0
                            score_pesato += peso * s
                            peso_totale += peso
                        
                        if peso_totale > 0:
                            score_coerenza_fascia = score_pesato / peso_totale
                
                # -- Score carico fascia --
                score_carico = _score_carico_fascia(conteggio, fascia)
                if score_carico == 0:
                    warnings.append(f'Fascia {fascia} piena ({TAPPE_PER_FASCIA}/{TAPPE_PER_FASCIA} tappe)')
                elif conteggio.get(fascia, 0) >= TAPPE_PER_FASCIA - 1:
                    warnings.append(f'Fascia {fascia} quasi piena ({conteggio[fascia]}/{TAPPE_PER_FASCIA} tappe)')
                
                # -- Score posizionamento nel percorso --
                score_posizione = _score_posizione_percorso(
                    fascia, dist_media_tutte, conteggio,
                    target_lat, target_lon, deposito
                )
                
                # -- Score distribuzione --
                tappe_oggi = tappe_per_giorno.get(data, 0)
                score_distribuzione = 1.0 - (tappe_oggi / max(max_tappe_giorno, 1))
                
                # -- Score composito --
                score = (
                    PESO_VICINANZA * score_vicinanza +
                    PESO_VICINANZA_APP * score_vicinanza_app +
                    PESO_COERENZA_FASCIA * score_coerenza_fascia +
                    PESO_CARICO_FASCIA * score_carico +
                    PESO_POSIZIONE_PERCORSO * score_posizione +
                    PESO_DISTRIBUZIONE * score_distribuzione
                )
                
                # Penalità forte se fascia piena
                if score_carico == 0:
                    score *= 0.3
                
                # Penalità solo domenica (sabato è lavorativo)
                try:
                    dt_check = datetime.strptime(data, '%Y-%m-%d')
                    if dt_check.weekday() == 6:
                        score *= 0.4
                        warnings.append('Domenica')
                except ValueError:
                    pass
                
                # Costruisci motivo leggibile
                motivo_parti = []
                if dist_min_tutte < RAGGIO_VICINANZA_STRETTA_KM:
                    motivo_parti.append(f'Molto vicino a tappe esistenti ({dist_min_tutte:.1f} km)')
                elif dist_min_tutte < RAGGIO_VICINANZA_KM:
                    motivo_parti.append(f'Vicino a tappe esistenti ({dist_min_tutte:.1f} km)')
                
                if app_giorno and dist_min_app < RAGGIO_VICINANZA_KM:
                    motivo_parti.append(f'Vicino a {len(app_giorno)} appuntamento/i')
                
                if score_coerenza_fascia >= 0.7:
                    motivo_parti.append(f'Stessa fascia dei vicini')
                elif score_coerenza_fascia >= 0.4:
                    motivo_parti.append(f'Fascia adiacente ai vicini')
                
                if dist_media_tutte >= SOGLIA_PERIFERICO_KM:
                    if fascia == FASCE_ORARIE[0]:
                        motivo_parti.append('🌅 Prima tappa (periferico)')
                    elif fascia == FASCE_ORARIE[-1]:
                        motivo_parti.append('🌇 Ultima tappa (periferico)')
                
                tappe_fascia = conteggio.get(fascia, 0)
                motivo_parti.append(f'Fascia {fascia}: {tappe_fascia}/{TAPPE_PER_FASCIA} tappe')
                motivo_parti.append(f'{len(contatori_op)} tappe totali per {operatore}')
                
                suggerimenti.append({
                    'data': data,
                    'fascia': fascia,
                    'operatore': operatore,
                    'score': round(score, 4),
                    'motivo': ' · '.join(motivo_parti),
                    'warnings': warnings,
                    'dettaglio': {
                        'dist_min_km': round(dist_min_tutte, 2) if dist_min_tutte != float('inf') else None,
                        'dist_media_km': round(dist_media_tutte, 2) if dist_media_tutte != float('inf') else None,
                        'tappe_fascia': tappe_fascia,
                        'tappe_giorno': len(contatori_op),
                        'appuntamenti_giorno': len(app_giorno),
                    }
                })
    
    # Ordina per score decrescente
    suggerimenti.sort(key=lambda x: x['score'], reverse=True)
    
    # Deduplicazione: una sola entry per data (la migliore combinazione operatore+fascia)
    # Garantisce massima diversità di giornate nei risultati
    visti_data = set()
    suggerimenti_dedup = []
    for s in suggerimenti:
        if s['data'] not in visti_data:
            suggerimenti_dedup.append(s)
            visti_data.add(s['data'])
    
    return {
        'suggerimenti': suggerimenti_dedup,
        'warnings_globali': warnings_globali,
    }


def verifica_appuntamento(contatori_tutti, pod, data, orario, operatore=None):
    """
    Verifica un appuntamento per conflitti e problemi.
    
    Args:
        contatori_tutti: lista completa contatori
        pod: POD del contatore
        data: data dell'appuntamento (YYYY-MM-DD)
        orario: orario in formato "HH:MM" o "HH:MM-HH:MM"
        operatore: operatore assegnato (opzionale)
    
    Returns:
        dict con:
        - valido: bool
        - warnings: lista di warning
        - errori: lista di errori bloccanti
    """
    warnings = []
    errori = []
    
    # Parse dell'orario
    ora_min, ora_max = estrai_orario_e_fascia(orario)
    
    # Filtra contatori per data
    contatori_giorno = [
        c for c in contatori_tutti
        if c.get('data') == data and not c.get('annullato', False)
    ]
    
    if operatore:
        contatori_op = [c for c in contatori_giorno if c.get('tecnico') == operatore]
    else:
        contatori_op = contatori_giorno
    
    # --- CHECK 1: Sovrapposizione oraria ---
    if ora_min is not None:
        for c in contatori_op:
            if c.get('pod') == pod:
                continue  # Stesso contatore
            
            if not c.get('is_appuntamento'):
                continue
            
            c_ora_min, c_ora_max = estrai_orario_e_fascia(c.get('ora_appuntamento', ''))
            if c_ora_min is None:
                continue
            
            # Controlla sovrapposizione
            app_fine = ora_max if ora_max and ora_max != ora_min else ora_min + 30
            c_fine = c_ora_max if c_ora_max and c_ora_max != c_ora_min else c_ora_min + 30
            
            if ora_min < c_fine and app_fine > c_ora_min:
                tec_label = f" ({c.get('tecnico', '?')})" if not operatore else ""
                errori.append(
                    f"Sovrapposizione con appuntamento {c.get('cliente', c.get('pod', '?'))}"
                    f"{tec_label} alle {c.get('ora_appuntamento', '?')}"
                )
    
    # --- CHECK 2: Fascia piena ---
    conteggio, _ = _conta_tappe_per_fascia(contatori_giorno, operatore)
    
    # Determina in quale fascia cadrebbe l'appuntamento
    if ora_min is not None:
        for fascia in FASCE_ORARIE:
            f_ini, f_fin = _fascia_to_minuti(fascia)
            if f_ini is not None and _orario_in_fascia(ora_min, ora_max, f_ini, f_fin):
                tappe = conteggio.get(fascia, 0)
                if tappe >= TAPPE_PER_FASCIA:
                    warnings.append(
                        f"Fascia {fascia} già piena ({tappe}/{TAPPE_PER_FASCIA} tappe)"
                        f"{' per ' + operatore if operatore else ''}"
                    )
                elif tappe >= TAPPE_PER_FASCIA - 1:
                    warnings.append(
                        f"Fascia {fascia} quasi piena ({tappe}/{TAPPE_PER_FASCIA} tappe)"
                    )
    
    # --- CHECK 3: Troppi appuntamenti per lo stesso operatore ---
    app_op = [c for c in contatori_op if c.get('is_appuntamento')]
    if len(app_op) >= 4:
        warnings.append(
            f"{'Operatore ' + operatore + ' ha' if operatore else 'Ci sono'} "
            f"già {len(app_op)} appuntamenti in questa data"
        )
    
    # --- CHECK 4: Appuntamento fuori orario lavorativo ---
    if ora_min is not None:
        if ora_min < 480:  # Prima delle 8:00
            warnings.append("Appuntamento prima delle 8:00")
        if ora_min > 1020:  # Dopo le 17:00
            warnings.append("Appuntamento dopo le 17:00")
        if ora_max and ora_max > 1080:  # Dopo le 18:00
            warnings.append("Fascia supera le 18:00")
    
    # --- CHECK 5: Giorno non lavorativo ---
    try:
        dt = datetime.strptime(data, '%Y-%m-%d')
        if dt.weekday() == 6:
            warnings.append("Appuntamento di domenica")
    except ValueError:
        pass
    
    # --- CHECK 6: Troppe tappe totali per l'operatore ---
    if operatore:
        tappe_totali = len([c for c in contatori_op if c.get('ordine_tappa', 999) != 999])
        if tappe_totali >= 24:
            warnings.append(f"Operatore {operatore} ha già {tappe_totali} tappe (max consigliato: 24)")
    
    return {
        'valido': len(errori) == 0,
        'warnings': warnings,
        'errori': errori
    }


def analisi_giornata(contatori_tutti, data, operatore=None):
    """
    Restituisce un riepilogo della giornata per aiutare l'utente a scegliere.
    
    Returns:
        dict con: fasce (occupazione), appuntamenti, operatori attivi, warnings
    """
    contatori_giorno = [
        c for c in contatori_tutti
        if c.get('data') == data and not c.get('annullato', False)
    ]
    
    # Per operatore
    operatori_stats = defaultdict(lambda: {'tappe': 0, 'appuntamenti': 0, 'contatori': []})
    
    for c in contatori_giorno:
        tec = c.get('tecnico', 'N/A')
        if tec == 'TUTTI':
            continue
        operatori_stats[tec]['tappe'] += 1
        operatori_stats[tec]['contatori'].append(c)
        if c.get('is_appuntamento'):
            operatori_stats[tec]['appuntamenti'] += 1
    
    # Fasce per operatore
    fasce_per_operatore = {}
    for tec, stats in operatori_stats.items():
        if operatore and tec != operatore:
            continue
        conteggio, _ = _conta_tappe_per_fascia(contatori_giorno, tec)
        fasce_per_operatore[tec] = {
            f: {'tappe': conteggio[f], 'max': TAPPE_PER_FASCIA, 'piena': conteggio[f] >= TAPPE_PER_FASCIA}
            for f in FASCE_ORARIE
        }
    
    # Warnings
    warnings = []
    for tec, stats in operatori_stats.items():
        if stats['tappe'] >= 24:
            warnings.append(f"{tec}: giornata piena ({stats['tappe']} tappe)")
        if stats['appuntamenti'] >= 4:
            warnings.append(f"{tec}: molti appuntamenti ({stats['appuntamenti']})")
    
    # Sovrapposizioni
    sovrapposizioni = _rileva_sovrapposizioni(contatori_giorno)
    for s in sovrapposizioni:
        warnings.append(s)
    
    return {
        'data': data,
        'operatori': {
            tec: {
                'tappe': stats['tappe'],
                'appuntamenti': stats['appuntamenti'],
            }
            for tec, stats in operatori_stats.items()
        },
        'fasce': fasce_per_operatore,
        'warnings': warnings,
        'totale_contatori': len(contatori_giorno),
    }


def _rileva_sovrapposizioni(contatori_giorno):
    """Rileva appuntamenti sovrapposti nella stessa giornata."""
    sovrapposizioni = []
    
    # Raggruppa appuntamenti per operatore
    app_per_operatore = defaultdict(list)
    for c in contatori_giorno:
        if c.get('is_appuntamento') and c.get('ora_appuntamento'):
            tec = c.get('tecnico', 'N/A')
            ora_min, ora_max = estrai_orario_e_fascia(c.get('ora_appuntamento', ''))
            if ora_min is not None:
                app_per_operatore[tec].append({
                    'cliente': c.get('cliente', '?'),
                    'pod': c.get('pod', ''),
                    'ora_min': ora_min,
                    'ora_max': ora_max if ora_max and ora_max != ora_min else ora_min + 30,
                    'orario_str': c.get('ora_appuntamento', ''),
                })
    
    for tec, apps in app_per_operatore.items():
        for i in range(len(apps)):
            for j in range(i + 1, len(apps)):
                a, b = apps[i], apps[j]
                if a['ora_min'] < b['ora_max'] and a['ora_max'] > b['ora_min']:
                    sovrapposizioni.append(
                        f"⚠️ Sovrapposizione {tec}: "
                        f"{a['cliente']} ({a['orario_str']}) ↔ "
                        f"{b['cliente']} ({b['orario_str']})"
                    )
    
    return sovrapposizioni
