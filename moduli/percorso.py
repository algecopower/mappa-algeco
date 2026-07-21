"""
Ottimizzazione percorsi con OSRM - Versione semplificata e robusta.

Approccio:
1. OSRM Trip API per ordinamento ottimale nativo (rispetta la reale geometria stradale)
2. Strategia "appuntamenti come ancore temporali" per giornate con orari fissi
3. Sweep angolare come fallback (sempre lineare geograficamente)
4. Nessuna matrice, niente 2-opt/3-opt complessi

Vantaggi:
- Percorso calcolato direttamente su strade reali
- Codice molto più semplice (~200 righe vs 1700)
- Risultati sempre coerenti con la traccia OSRM
"""
import logging
import math
import re
import pandas as pd
import requests
from typing import List, Dict, Tuple, Optional

logger = logging.getLogger(__name__)

from config import (
    OSRM_SERVER,
    OSRM_TIMEOUT,
    DEPOSITO,
    ORA_INIZIO_GIORNATA,
    PROVINCIA_ATTIVA,
)


# =============================================================================
# FUNZIONI DI UTILITÀ (compatibilità con codice esistente)
# =============================================================================

def normalizza_eneltel(val):
    """Normalizza codice Eneltel rimuovendo zeri iniziali."""
    if pd.isna(val):
        return None
    s = str(val).strip()
    s = re.sub(r'^0+', '', s)
    return s if s else None


# =============================================================================
# RILEVAMENTO APPUNTAMENTI (con gestione falsi positivi)
# =============================================================================
from moduli.appuntamenti_core import (
    motivo_non_appuntamento,  # noqa: F401
    rileva_appuntamento,      # noqa: F401
)


def estrai_orario_e_fascia(testo):
    """
    Estrae orario e/o fascia da testo.

    Ritorna: (ora_min, ora_max) in minuti dall'inizio del giorno
    - Se orario preciso: ora_min == ora_max (es. 10:30 → (630, 630))
    - Se fascia: ora_min < ora_max (es. 10/12 → (600, 720))
    - Se nessun orario: (None, None)
    """
    if pd.isna(testo) or not testo:
        return (None, None)
    testo = str(testo).strip()

    # FORMATO FASCIA: "dalle HH:MM alle HH:MM" o "dalle HH alle HH"
    match = re.search(r'dalle?\s*(\d{1,2})(?:[:.:](\d{2}))?\s*alle?\s*(\d{1,2})(?:[:.:](\d{2}))?', testo, re.IGNORECASE)
    if match:
        ora_inizio = int(match.group(1))
        min_inizio = int(match.group(2)) if match.group(2) else 0
        ora_fine = int(match.group(3))
        min_fine = int(match.group(4)) if match.group(4) else 0
        tot_inizio = ora_inizio * 60 + min_inizio
        tot_fine = ora_fine * 60 + min_fine
        if 6 <= ora_inizio <= 20 and 6 <= ora_fine <= 20 and tot_inizio < tot_fine:
            return (tot_inizio, tot_fine)

    # FORMATO FASCIA CON KEYWORD: "ORE HH:MM/HH:MM" o "ORE HH:MM-HH:MM"
    match = re.search(r'ore?\s*(\d{1,2})[:.](\d{2})\s*[/\-]\s*(\d{1,2})[:.](\d{2})', testo, re.IGNORECASE)
    if match:
        ora_inizio = int(match.group(1))
        min_inizio = int(match.group(2))
        ora_fine = int(match.group(3))
        min_fine = int(match.group(4))
        tot_inizio = ora_inizio * 60 + min_inizio
        tot_fine = ora_fine * 60 + min_fine
        if 6 <= ora_inizio <= 20 and 6 <= ora_fine <= 20 and tot_inizio < tot_fine:
            return (tot_inizio, tot_fine)

    # FORMATO FASCIA: "HH/HH", "HH-HH", "HH:MM/HH:MM", "HH:MM-HH:MM"
    for match in re.finditer(r'\b(\d{1,2})(?:[:.](\d{2}))?\s*[/\-]\s*(\d{1,2})(?:[:.](\d{2}))?\b', testo):
        ora_inizio = int(match.group(1))
        min_inizio = int(match.group(2)) if match.group(2) else 0
        ora_fine = int(match.group(3))
        min_fine = int(match.group(4)) if match.group(4) else 0
        tot_inizio = ora_inizio * 60 + min_inizio
        tot_fine = ora_fine * 60 + min_fine
        if 6 <= ora_inizio <= 20 and 6 <= ora_fine <= 20 and tot_inizio < tot_fine:
            return (tot_inizio, tot_fine)

    # FORMATO ORARIO PRECISO: HH:MM, HH.MM, HH;MM, HH,MM
    match = re.search(r'(\d{1,2})[:.;,](\d{2})', testo)
    if match:
        ore, minuti = int(match.group(1)), int(match.group(2))
        if 6 <= ore <= 20 and 0 <= minuti <= 59:
            tot = ore * 60 + minuti
            return (tot, tot)

    # FORMATO COMPATTO: 1030, 930 (ma NON anni come 2024, 2025, 2026...)
    match = re.search(r'\b(\d{3,4})\b', testo)
    if match:
        num = match.group(1).zfill(4)
        if not (num.startswith('19') or num.startswith('20')):
            ore, minuti = int(num[:2]), int(num[2:])
            if 6 <= ore <= 20 and 0 <= minuti <= 59:
                tot = ore * 60 + minuti
                return (tot, tot)

    # FORMATO CON KEYWORD: "alle 10", "ore 14", "h9"
    match = re.search(r'(?:alle|ore|h)\s*(\d{1,2})\b', testo, re.IGNORECASE)
    if match:
        ore = int(match.group(1))
        if 6 <= ore <= 20:
            tot = ore * 60
            return (tot, tot)

    return (None, None)


def estrai_orario(testo):
    """Wrapper per retrocompatibilità - ritorna stringa orario o fascia."""
    ora_min, ora_max = estrai_orario_e_fascia(testo)
    if ora_min is None:
        return None
    if ora_min == ora_max:
        return f"{ora_min // 60:02d}:{ora_min % 60:02d}"
    return f"{ora_min // 60:02d}:{ora_min % 60:02d}-{ora_max // 60:02d}:{ora_max % 60:02d}"


def minuti_to_orario(minuti):
    """Converte minuti in stringa HH:MM."""
    if minuti is None:
        return None
    return f"{minuti // 60:02d}:{minuti % 60:02d}"


# =============================================================================
# CALCOLO DISTANZA HAVERSINE (linea d'aria)
# =============================================================================

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distanza in km tra due punti (linea d'aria)."""
    R = 6371
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))


# Alias per retrocompatibilità
calcola_distanza_haversine = _haversine_km


# =============================================================================
# ACCESS POINTS - estrazione coords per il routing
# =============================================================================

_CIVICI_INVALIDI_CLUSTER = {
    '', 'NAN', 'NONE', 'SN', 'SNC', 'S.N.', 'S.N.C.', 'S/N', 'S/NC'
}


def _campo_str(punto: Dict, *chiavi: str) -> str:
    """Restituisce il primo campo non-vuoto tra `chiavi`, strippato. '' se nessuno."""
    for k in chiavi:
        v = punto.get(k)
        if v is None:
            continue
        try:
            if pd.isna(v):
                continue
        except (TypeError, ValueError):
            pass
        s = str(v).strip()
        if s:
            return s
    return ''


def _cluster_indirizzi_uguali(punti: List[Dict]) -> None:
    """
    Allinea in-place le coordinate di routing dei contatori che condividono
    lo stesso indirizzo postale (comune + localita + via + civico), così che
    il TSP li tratti come un'unica fermata e li visiti consecutivamente.

    Motivazione: il geocoder talvolta colloca contatori con stesso indirizzo
    in punti distanti centinaia di metri (es. POD fotovoltaico spostato).
    OSRM, ignaro che siano lo stesso edificio, li infila in cluster diversi
    del percorso. Forzare la stessa coordinata di routing risolve a monte.

    Scrive solo `Lat_access`/`Lon_access`: `Lat`/`Lon` originali restano
    intatte per la visualizzazione su mappa. La modifica è in-memory sui
    dict passati, che provengono da `df.to_dict('records')` e sono già
    copie indipendenti — il FILONE non viene toccato.

    Strategia di scelta del rappresentante: medoide del gruppo
    (il punto del gruppo più vicino al centroide). Per gruppi con
    maggioranza ai medesimi coords il medoide coincide con quella coord.

    Saltati:
      - gruppi con un solo punto
      - punti con civico vuoto / SN / SNC (rischio di accorpare punti
        spuri lungo la stessa via)
    """
    if not punti or len(punti) < 2:
        return

    gruppi: Dict[Tuple[str, str, str, str], List[int]] = {}
    for i, p in enumerate(punti):
        via = _campo_str(p, 'VIA_K', 'via').upper()
        civico = _campo_str(p, 'CIVICO_K', 'civico').upper()
        if not via or civico in _CIVICI_INVALIDI_CLUSTER:
            continue
        comune = _campo_str(p, 'COMUNE', 'comune').upper()
        localita = _campo_str(p, 'LOCALITA', 'localita').upper()
        gruppi.setdefault((comune, localita, via, civico), []).append(i)

    for indici in gruppi.values():
        if len(indici) < 2:
            continue

        coords: List[Tuple[float, float]] = []
        for i in indici:
            try:
                coords.append(_coord_routing(punti[i]))
            except ValueError:
                coords.append(None)

        coords_valide = [c for c in coords if c is not None]
        if len(coords_valide) < 2:
            continue

        # Se sono tutte già identiche, niente da fare
        if len(set(coords_valide)) == 1:
            continue

        # Medoide: punto del gruppo più vicino al centroide
        lat_c = sum(c[0] for c in coords_valide) / len(coords_valide)
        lon_c = sum(c[1] for c in coords_valide) / len(coords_valide)
        medoide = min(
            coords_valide,
            key=lambda c: (c[0] - lat_c) ** 2 + (c[1] - lon_c) ** 2,
        )

        for i in indici:
            punti[i]['Lat_access'] = medoide[0]
            punti[i]['Lon_access'] = medoide[1]


def _coord_routing(p: Dict) -> Tuple[float, float]:
    """
    Ritorna (lat, lon) da usare per il ROUTING.

    Priorita':
      1. Lat_access / Lon_access (snap-by-name OSRM o override manuale)
      2. lat_access / lon_access (varianti minuscole)
      3. Lat / Lon
      4. lat / lon
    """
    candidates = (
        ('Lat_access', 'Lon_access'),
        ('lat_access', 'lon_access'),
        ('Lat', 'Lon'),
        ('lat', 'lon'),
    )
    for klat, klon in candidates:
        lat = p.get(klat)
        lon = p.get(klon)
        if lat is None or lon is None:
            continue
        try:
            lat_f = float(lat)
            lon_f = float(lon)
        except (TypeError, ValueError):
            continue
        if math.isnan(lat_f) or math.isnan(lon_f):
            continue
        return lat_f, lon_f
    raise ValueError(f"Punto senza coordinate valide: {p.get('Eneltel_Key') or p.get('eneltel') or '?'}")


# =============================================================================
# ORDINAMENTO SEMPLICE: SWEEP ANGOLARE
# =============================================================================

def _ordina_sweep(punti: List[Dict], deposito: Dict) -> List[Dict]:
    """
    Ordina punti per angolo dal deposito (algoritmo sweep).
    
    Crea un percorso geograficamente lineare: parte dal deposito,
    ruota attorno coprendo tutti i punti in ordine angolare.
    
    Args:
        punti: lista di dict con 'Lat', 'Lon'
        deposito: dict con 'Lat', 'Lon'
    
    Returns:
        Lista ordinata dei punti
    """
    if not punti:
        return []
    if len(punti) <= 2:
        return list(punti)
    
    lat0, lon0 = _coord_routing(deposito)
    
    # Calcola angolo di ogni punto rispetto al deposito
    punti_con_angolo = []
    for p in punti:
        lat, lon = _coord_routing(p)
        # Angolo in radianti (atan2(dy, dx))
        angolo = math.atan2(lon - lon0, lat - lat0)
        punti_con_angolo.append((angolo, p))
    
    # Ordina per angolo (sweep)
    punti_con_angolo.sort(key=lambda x: x[0])
    
    return [p for _, p in punti_con_angolo]


# =============================================================================
# OSRM TRIP API - Soluzione TSP nativa
# =============================================================================

def _ottimizza_con_osrm_trip(
    punti: List[Dict],
    deposito: Dict,
    ancore: List[int] = None
) -> Optional[List[int]]:
    """
    Usa OSRM Trip API per trovare l'ordine ottimale del percorso.
    
    La Trip API di OSRM risolve il TSP direttamente sulla rete stradale reale,
    considerando la geometria effettiva delle strade, sensi unici, svincoli, etc.
    
    Args:
        punti: lista di punti (dict con 'Lat', 'Lon')
        deposito: punto di partenza e ritorno
        ancore: indici di punti da mantenere fissi (vincoli temporali)
    
    Returns:
        Lista di indici nell'ordine ottimale, o None se fallito
    """
    if not punti:
        return []
    
    # OSRM Trip supporta max 100 waypoint (circa)
    # Per dataset più grandi, dividiamo in segmenti
    if len(punti) > 90:
        return _ottimizza_grande_con_trip(punti, deposito)
    
    # Costruisci coordinate: deposito + punti + deposito (ritorno).
    # Usa Lat_access/Lon_access se disponibili (snap-by-name UPS-style),
    # altrimenti fallback a Lat/Lon. Il deposito non ha access point.
    dep_lat, dep_lon = _coord_routing(deposito)
    coords = [(dep_lon, dep_lat)]
    for p in punti:
        plat, plon = _coord_routing(p)
        coords.append((plon, plat))
    coords.append((dep_lon, dep_lat))
    
    coords_str = ";".join([f"{lon},{lat}" for lon, lat in coords])
    url = f"{OSRM_SERVER}/trip/v1/driving/{coords_str}"
    
    # Parametri per il round-trip che parte e torna al deposito
    params = {
        'roundtrip': 'true',
        'source': 'first',  # Parte dal deposito (indice 0)
        'destination': 'last',  # Torna al deposito (ultimo indice)
        'geometries': 'geojson',
        'overview': 'full',
    }
    
    try:
        response = requests.get(url, params=params, timeout=OSRM_TIMEOUT * 2)
        response.raise_for_status()
        data = response.json()
        
        if data.get('code') != 'Ok':
            return None
        
        # Estrai l'ordine ottimale dalle waypoints
        waypoints = data.get('waypoints', [])
        if len(waypoints) < 2:
            return None
        
        # waypoints[i]['waypoint_index'] = posizione nell'ordine ottimale
        # Costruiamo mappa: waypoint_index -> indice punto originale
        ordine_wp = [(wp.get('waypoint_index', i), i) for i, wp in enumerate(waypoints)]
        ordine_wp.sort(key=lambda x: x[0])
        
        # I waypoint sono: 0=deposito_partenza, 1..n=punti, n+1=deposito_ritorno
        # Ritorniamo gli indici dei punti (escludendo i due deposito)
        risultato = []
        for _, idx_originale in ordine_wp:
            if idx_originale == 0 or idx_originale == len(waypoints) - 1:
                continue  # Salta depositi
            # idx_originale-1 perché i punti partono da indice 1 (dopo deposito partenza)
            risultato.append(idx_originale - 1)
        
        return risultato
        
    except Exception:
        return None


def _ottimizza_grande_con_trip(punti: List[Dict], deposito: Dict) -> List[int]:
    """
    Per dataset grandi (>90 punti), divide in zone geografiche e ottimizza separatamente.
    """
    # Ordina per sweep iniziale per definire zone
    punti_sweep = _ordina_sweep(punti, deposito)
    
    # Dividi in blocchi di ~80 punti con sovrapposizione
    block_size = 80
    overlap = 5
    
    # Mappa da indice sweep a indice originale
    indici_sweep = {id(p): i for i, p in enumerate(punti)}
    
    risultato = []
    processati = set()
    
    for start in range(0, len(punti_sweep), block_size - overlap):
        end = min(start + block_size, len(punti_sweep))
        blocco = punti_sweep[start:end]
        
        # Filtra già processati (tranne sovrapposizione)
        blocco_da_processare = []
        for p in blocco:
            idx = indici_sweep.get(id(p))
            if idx not in processati or len(blocco_da_processare) < overlap:
                blocco_da_processare.append(p)
                processati.add(idx)
        
        if len(blocco_da_processare) < 3:
            continue
        
        # Ottimizza questo blocco
        ordine_blocco = _ottimizza_con_osrm_trip(blocco_da_processare, deposito)
        
        if ordine_blocco:
            # Mappa indici blocco -> indici originali
            indici_blocco = [indici_sweep.get(id(blocco_da_processare[i])) for i in ordine_blocco]
            risultato.extend([i for i in indici_blocco if i is not None])
        else:
            # Fallback: ordine sweep del blocco
            for p in blocco_da_processare:
                idx = indici_sweep.get(id(p))
                if idx is not None and idx not in risultato:
                    risultato.append(idx)
    
    # Assicurati che tutti i punti siano inclusi
    for i in range(len(punti)):
        if i not in risultato:
            risultato.append(i)
    
    return risultato


# =============================================================================
# OSRM TRIP API - Variante "segmento" (start e end fissi)
# =============================================================================

def _ordina_segmento_osrm(
    punti: List[Dict],
    start: Dict,
    end: Dict,
) -> List[Dict]:
    """
    Ordina `punti` lungo il segmento da `start` a `end` usando OSRM Trip
    in modalita' non-roundtrip (source=first, destination=last).

    A differenza di `_ottimizza_con_osrm_trip` (che ritorna al deposito),
    qui start ed end sono punti distinti: utile per riempire un segmento
    tra due ancore (deposito->primo_app, app_i->app_i+1, ultimo_app->deposito).

    Returns:
        Lista dei punti ordinati (esclude start ed end), o sweep dal start
        come fallback se OSRM fallisce.
    """
    if not punti:
        return []
    if len(punti) == 1:
        return list(punti)

    # OSRM Trip ha un limite ~100 waypoint
    if len(punti) > 90:
        return _ordina_sweep(punti, start)

    s_lat, s_lon = _coord_routing(start)
    e_lat, e_lon = _coord_routing(end)

    coords = [(s_lon, s_lat)]
    for p in punti:
        plat, plon = _coord_routing(p)
        coords.append((plon, plat))
    coords.append((e_lon, e_lat))

    coords_str = ";".join([f"{lon},{lat}" for lon, lat in coords])
    url = f"{OSRM_SERVER}/trip/v1/driving/{coords_str}"
    params = {
        'roundtrip': 'false',
        'source': 'first',
        'destination': 'last',
        'overview': 'false',
    }

    try:
        response = requests.get(url, params=params, timeout=OSRM_TIMEOUT * 2)
        response.raise_for_status()
        data = response.json()

        if data.get('code') != 'Ok':
            return _ordina_sweep(punti, start)

        waypoints = data.get('waypoints', [])
        if len(waypoints) < 2:
            return _ordina_sweep(punti, start)

        # waypoints[i]['waypoint_index'] = posizione nell'ordine ottimale
        ordine_wp = [(wp.get('waypoint_index', i), i) for i, wp in enumerate(waypoints)]
        ordine_wp.sort(key=lambda x: x[0])

        # Indici originali: 0=start, 1..n=punti, n+1=end
        risultato: List[Dict] = []
        ultimo_idx = len(waypoints) - 1
        for _, idx in ordine_wp:
            if idx == 0 or idx == ultimo_idx:
                continue
            risultato.append(punti[idx - 1])
        return risultato
    except Exception:
        return _ordina_sweep(punti, start)


# =============================================================================
# GESTIONE APPUNTAMENTI CON ORARIO
# =============================================================================

def _estrai_orario(testo: str) -> Optional[Tuple[int, int]]:
    """
    Estrae (ora_min, ora_max) in minuti da stringa.

    Wrapper che riusa `estrai_orario_e_fascia` (definita in cima al modulo),
    che gestisce correttamente:
      - "dalle 8 alle 12"          -> (480, 720)
      - "dalle 09:00 alle 12:30"   -> (540, 750)
      - "ore 10:30/12:00"          -> (630, 720)
      - "10/12", "10-12"           -> (600, 720)
      - "10:30"                    -> (630, 630)
      - "alle 14"                  -> (840, 840)

    Ritorna None se nessun orario riconosciuto.
    """
    if testo is None:
        return None
    ora_min, ora_max = estrai_orario_e_fascia(testo)
    if ora_min is None:
        return None
    return (ora_min, ora_max)


def _ordina_con_appuntamenti(
    punti: List[Dict],
    deposito: Dict,
    ora_limite_serale: int
) -> List[Dict]:
    """
    Ordina punti con vincoli di appuntamento - strategia "ancore temporali".

    Idea:
    - Gli APPUNTAMENTI sono ancore fisse nel tempo: vanno visitati nell'ordine
      cronologico, e l'arrivo deve essere entro `ora_max` (e idealmente non
      molto dopo `ora_min`).
    - I PUNTI BASE riempiono gli intervalli tra ancore, scelti tra quelli
      "in transito" (con minor deviazione dal segmento) e in numero coerente
      con il tempo disponibile.
    - I residui (non in transito) vanno dopo l'ultimo appuntamento normale.
    - Gli APPUNTAMENTI SERALI restano in coda.

    Differenze rispetto alla versione precedente (che era buggata):
    - Target di arrivo = `ora_min` (non `ora_max`): si rispetta l'inizio
      della fascia, non si "riempie" la fascia con tappe casuali.
    - Numero di tappe inseribili pre-calcolato dal budget temporale,
      con un tempo medio per tappa realistico (viaggio + intervento).
    - Stima cumulativa dell'orario corretta: include tempo di viaggio
      tra punti consecutivi, non solo tempo di intervento.
    - Ordinamento intra-segmento via OSRM Trip non-roundtrip (start->end fissi).
    """
    if not punti:
        return []

    # Tempi (in minuti) usati per le stime: il valore reale viene
    # comunque ricalcolato con OSRM in `stima_orari_arrivo`.
    TEMPO_INTERVENTO = 20
    TEMPO_VIAGGIO_INTER_TAPPA_STIMATO = 10  # tempo medio fra due tappe vicine
    TEMPO_TAPPA_MEDIO = TEMPO_INTERVENTO + TEMPO_VIAGGIO_INTER_TAPPA_STIMATO
    VEL_STIMA_KMH = 35.0

    def _tempo_haversine_min(p1: Dict, p2: Dict) -> float:
        lat1, lon1 = _coord_routing(p1)
        lat2, lon2 = _coord_routing(p2)
        return (_haversine_km(lat1, lon1, lat2, lon2) / VEL_STIMA_KMH) * 60.0

    # ------------------------------------------------------------------
    # FASE 1 - Categorizzazione
    # ------------------------------------------------------------------
    app_normali: List[Tuple[int, int, Dict]] = []
    app_serali: List[Tuple[int, int, Dict]] = []
    punti_base: List[Dict] = []
    app_senza_orario = 0

    for p in punti:
        if p.get('IS_APPUNTAMENTO'):
            # Fallback chain per l'orario: NOTE APP -> ORA_APPUNTAMENTO ->
            # ORARI_LETTERE (la fascia della colonna "Orari" usata per le lettere).
            orario = (
                _estrai_orario(p.get('NOTE_APP_RAW', ''))
                or _estrai_orario(p.get('ORA_APPUNTAMENTO', ''))
                or _estrai_orario(p.get('ORARI_LETTERE', ''))
            )
            if orario:
                ora_min, ora_max = orario
                if ora_min >= ora_limite_serale:
                    app_serali.append((ora_min, ora_max, p))
                else:
                    app_normali.append((ora_min, ora_max, p))
                p['_ORA_MIN'] = ora_min
                p['_ORA_MAX'] = ora_max
                continue
            # IS_APPUNTAMENTO ma orario non parsabile: viene trattato
            # come punto base (l'ottimizzazione TSP lo posiziona dove
            # ha senso geograficamente).
            app_senza_orario += 1
        punti_base.append(p)

    if app_senza_orario:
        logger.warning(
            "      ⚠️  %d appuntamento/i con flag ma orario non parsabile: "
            "trattati come tappe normali",
            app_senza_orario,
        )

    # Ordina per inizio fascia, a parita' priorita' alla fascia piu' stretta
    app_normali.sort(key=lambda x: (x[0], x[1] - x[0]))
    app_serali.sort(key=lambda x: (x[0], x[1] - x[0]))

    # ------------------------------------------------------------------
    # FASE 2 - Costruzione percorso usando appuntamenti come ancore
    # ------------------------------------------------------------------
    percorso: List[Dict] = []
    pos_corrente: Dict = deposito
    orario_corrente: float = float(ORA_INIZIO_GIORNATA)

    for ora_min, ora_max, app in app_normali:
        tempo_diretto = _tempo_haversine_min(pos_corrente, app)

        # Budget di tempo per inserire punti base "in transito":
        # quanto tempo ho prima dell'inizio fascia (target morbido).
        budget_target = (ora_min - orario_corrente) - tempo_diretto
        # Limite hard: non superare ora_max andando attraverso un punto base.
        slack_hard = (ora_max - orario_corrente) - tempo_diretto

        scelti: List[Dict] = []
        if budget_target > TEMPO_TAPPA_MEDIO and slack_hard > 0 and punti_base:
            n_max = int(budget_target // TEMPO_TAPPA_MEDIO)

            pos_lat, pos_lon = _coord_routing(pos_corrente)
            app_lat, app_lon = _coord_routing(app)
            dist_diretto_km = _haversine_km(pos_lat, pos_lon, app_lat, app_lon)

            candidati: List[Tuple[float, Dict]] = []
            for p in punti_base:
                p_lat, p_lon = _coord_routing(p)
                d_pos_p = _haversine_km(pos_lat, pos_lon, p_lat, p_lon)
                d_p_app = _haversine_km(p_lat, p_lon, app_lat, app_lon)
                # Deviazione = quanto allunghi il segmento passando per p
                deviazione_km = (d_pos_p + d_p_app) - dist_diretto_km
                # Tempo extra stimato per il singolo passaggio attraverso p
                tempo_extra_min = (deviazione_km / VEL_STIMA_KMH) * 60.0 + TEMPO_INTERVENTO
                # Hard constraint: anche il SOLO p non deve sforare ora_max
                if tempo_extra_min > slack_hard:
                    continue
                candidati.append((deviazione_km, p))

            candidati.sort(key=lambda x: x[0])
            scelti = [p for _, p in candidati[:n_max]]

        if scelti:
            # Ordina i punti scelti lungo il segmento pos_corrente -> app
            ordinati = _ordina_segmento_osrm(scelti, pos_corrente, app)
            if not ordinati:
                ordinati = _ordina_sweep(scelti, pos_corrente)

            percorso.extend(ordinati)
            for p in ordinati:
                if p in punti_base:
                    punti_base.remove(p)

            # Aggiorna stima cumulativa dell'orario percorrendo il segmento
            pp = pos_corrente
            for p in ordinati:
                orario_corrente += _tempo_haversine_min(pp, p) + TEMPO_INTERVENTO
                pp = p
            pos_corrente = pp

        # Vai all'appuntamento (ancora temporale)
        tempo_a_app = _tempo_haversine_min(pos_corrente, app)
        orario_arrivo = orario_corrente + tempo_a_app
        # Se arriviamo prima di ora_min, aspettiamo; non puo' partire l'intervento
        # prima dell'inizio fascia.
        orario_corrente = max(orario_arrivo, float(ora_min)) + TEMPO_INTERVENTO
        pos_corrente = app
        percorso.append(app)

    # ------------------------------------------------------------------
    # FASE 3 - Punti base rimanenti
    # ------------------------------------------------------------------
    # Vengono inseriti tra l'ultimo appuntamento normale e i serali (o il
    # rientro al deposito), ottimizzati come "segmento" da pos_corrente
    # all'end-point successivo.
    if punti_base:
        if app_serali:
            end_point = app_serali[0][2]
        else:
            end_point = deposito

        ordinati = _ordina_segmento_osrm(punti_base, pos_corrente, end_point)
        if not ordinati:
            # Fallback: vecchio TSP roundtrip dal deposito
            ordine = _ottimizza_con_osrm_trip(punti_base, deposito)
            if ordine:
                ordinati = [punti_base[i] for i in ordine]
            else:
                ordinati = _ordina_sweep(punti_base, pos_corrente)

        percorso.extend(ordinati)
        if ordinati:
            pos_corrente = ordinati[-1]

    # ------------------------------------------------------------------
    # FASE 4 - Appuntamenti serali alla fine, in ordine cronologico
    # ------------------------------------------------------------------
    percorso.extend([app for _, _, app in app_serali])

    return percorso


# =============================================================================
# FUNZIONE PRINCIPALE
# =============================================================================

def ottimizza_percorso(
    punti: List[Dict],
    deposito: Dict = None,
    usa_osrm: bool = True
) -> List[Dict]:
    """
    Ottimizza il percorso tra i punti dati.
    
    Args:
        punti: lista di dict con 'Lat', 'Lon', opzionalmente 'IS_APPUNTAMENTO'
        deposito: punto di partenza (default da config)
        usa_osrm: se True, usa OSRM Trip API (sempre True in produzione)
    
    Returns:
        Lista ordinata dei punti
    """
    if not punti:
        return []

    if len(punti) == 1:
        return list(punti)

    if deposito is None:
        deposito = DEPOSITO

    # Allinea le coordinate di routing dei contatori con stesso indirizzo:
    # evita che il geocoder, piazzando un POD a centinaia di metri dagli
    # altri dello stesso edificio, ne provochi la separazione in cluster
    # distinti del percorso.
    _cluster_indirizzi_uguali(punti)

    # Soglia orario per appuntamenti serali
    ora_limite_serale = 17 * 60 if PROVINCIA_ATTIVA == 'TORINO' else 15 * 60
    
    # Categorizza per presenza appuntamenti
    ha_appuntamenti = any(p.get('IS_APPUNTAMENTO') for p in punti)
    
    if ha_appuntamenti:
        return _ordina_con_appuntamenti(punti, deposito, ora_limite_serale)
    
    # Nessun appuntamento: usa OSRM Trip direttamente
    if usa_osrm:
        ordine = _ottimizza_con_osrm_trip(punti, deposito)
        if ordine:
            return [punti[i] for i in ordine]
    
    # Fallback: sweep angolare (sempre lineare)
    return _ordina_sweep(punti, deposito)


# =============================================================================
# STIMA ORARI DI ARRIVO
# =============================================================================

def stima_orari_arrivo(
    percorso: List[Dict],
    deposito: Dict,
    ora_inizio: int = None,
    tempo_intervento: int = 20
) -> List[Dict]:
    """
    Aggiunge stima orario di arrivo a ogni punto usando OSRM route.
    
    Args:
        percorso: lista ordinata di punti
        deposito: punto di partenza
        ora_inizio: minuti dall'inizio giornata (default da config)
        tempo_intervento: minuti per intervento
    
    Returns:
        Percorso con 'ORARIO_ARRIVO_STIMATO' aggiunto a ogni punto
    """
    if ora_inizio is None:
        ora_inizio = ORA_INIZIO_GIORNATA
    
    orario = ora_inizio
    punto_prec = deposito
    
    for punto in percorso:
        # Calcola tempo di viaggio con OSRM o fallback
        durata = _calcola_durata_osrm(punto_prec, punto)
        orario += durata
        punto['ORARIO_ARRIVO_STIMATO'] = int(orario)
        orario += tempo_intervento
        punto_prec = punto
    
    return percorso


def _calcola_durata_osrm(p1: Dict, p2: Dict) -> float:
    """Calcola durata tra due punti con OSRM route API (fallback Haversine).

    Usa Lat_access/Lon_access se disponibili (UPS-style)."""
    lat1, lon1 = _coord_routing(p1)
    lat2, lon2 = _coord_routing(p2)
    try:
        coords_str = f"{lon1},{lat1};{lon2},{lat2}"
        url = f"{OSRM_SERVER}/route/v1/driving/{coords_str}"
        response = requests.get(url, params={'overview': 'false'}, timeout=OSRM_TIMEOUT)
        data = response.json()
        if data.get('code') == 'Ok' and data.get('routes'):
            return data['routes'][0]['duration'] / 60  # secondi -> minuti
    except Exception:
        pass
    
    # Fallback Haversine a 35 km/h
    dist = _haversine_km(lat1, lon1, lat2, lon2)
    return (dist / 35) * 60


# =============================================================================
# ALIAS PER RETROCOMPATIBILITÀ
# =============================================================================

def ordina_percorso(punti: List[Dict], deposito: Dict = None, usa_osrm_ottimizzazione: bool = True) -> List[Dict]:
    """Alias per ottimizza_percorso."""
    return ottimizza_percorso(punti, deposito, usa_osrm_ottimizzazione)


def ordina_per_fasce_e_vicinanza(punti: List[Dict], deposito: Dict = None) -> List[Dict]:
    """Alias legacy."""
    return ottimizza_percorso(punti, deposito, usa_osrm=True)
