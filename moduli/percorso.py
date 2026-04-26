"""
Ottimizzazione percorsi con OSRM - Versione semplificata e robusta.

Approccio:
1. OSRM Trip API per ordinamento ottimale nativo (rispetta la reale geometria stradale)
2. Sweep angolare come fallback (sempre lineare geograficamente)
3. Nessuna matrice, niente 2-opt/3-opt complessi

Vantaggi:
- Percorso calcolato direttamente su strade reali
- Codice molto più semplice (~200 righe vs 1700)
- Risultati sempre coerenti con la traccia OSRM
"""
import math
import re
import pandas as pd
import requests
from typing import List, Dict, Any, Tuple, Optional

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
        if 0 <= ore <= 23 and 0 <= minuti <= 59:
            tot = ore * 60 + minuti
            return (tot, tot)

    # FORMATO COMPATTO: 1030, 930 (ma NON anni come 2024, 2025, 2026...)
    match = re.search(r'\b(\d{3,4})\b', testo)
    if match:
        num = match.group(1).zfill(4)
        if not (num.startswith('19') or num.startswith('20')):
            ore, minuti = int(num[:2]), int(num[2:])
            if 0 <= ore <= 23 and 0 <= minuti <= 59:
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
# GESTIONE APPUNTAMENTI CON ORARIO
# =============================================================================

def _estrai_orario(testo: str) -> Optional[Tuple[int, int]]:
    """Estrae (ora_min, ora_max) in minuti da stringa, o None."""
    if not testo:
        return None
    import re
    testo = str(testo)
    
    # Formato "HH:MM-HH:MM" o "HH-HH"
    match = re.search(r'(\d{1,2})[:.]?(\d{2})?\s*[-/]\s*(\d{1,2})[:.]?(\d{2})?', testo)
    if match:
        h1 = int(match.group(1))
        m1 = int(match.group(2)) if match.group(2) else 0
        h2 = int(match.group(3))
        m2 = int(match.group(4)) if match.group(4) else 0
        if 0 <= h1 <= 23 and 0 <= h2 <= 23:
            return (h1 * 60 + m1, h2 * 60 + m2)
    
    # Formato singolo "HH:MM"
    match = re.search(r'(\d{1,2})[:.](\d{2})', testo)
    if match:
        h, m = int(match.group(1)), int(match.group(2))
        if 0 <= h <= 23 and 0 <= m <= 59:
            tot = h * 60 + m
            return (tot, tot)
    
    # Formato "alle HH" o "ore HH"
    match = re.search(r'(?:alle|ore)\s*(\d{1,2})', testo, re.IGNORECASE)
    if match:
        h = int(match.group(1))
        if 6 <= h <= 22:
            tot = h * 60
            return (tot, tot)
    
    return None


def _ordina_con_appuntamenti(
    punti: List[Dict],
    deposito: Dict,
    ora_limite_serale: int
) -> List[Dict]:
    """
    Ordina punti con vincoli di appuntamento.
    
    Strategia:
    1. Separa appuntamenti normali, serali (dopo ora_limite), e punti base
    2. Ordina appuntamenti per orario
    3. Tra appuntamenti, inserisce punti base usando sweep locale
    4. Serali vengono dopo tutti gli altri
    """
    if not punti:
        return []
    
    # Categorizza punti
    app_normali = []  # (ora_min, ora_max, punto)
    app_serali = []
    punti_base = []
    
    for p in punti:
        if p.get('IS_APPUNTAMENTO'):
            orario = _estrai_orario(p.get('NOTE_APP_RAW', '')) or \
                     _estrai_orario(p.get('ORA_APPUNTAMENTO', ''))
            if orario:
                ora_min, ora_max = orario
                if ora_min >= ora_limite_serale:
                    app_serali.append((ora_min, ora_max, p))
                else:
                    app_normali.append((ora_min, ora_max, p))
                p['_ORA_MIN'] = ora_min
                p['_ORA_MAX'] = ora_max
                continue
        punti_base.append(p)
    
    # Ordina appuntamenti per orario
    app_normali.sort(key=lambda x: (x[0], x[1] - x[0]))
    app_serali.sort(key=lambda x: (x[0], x[1] - x[0]))
    
    # Costruisci percorso
    percorso = []
    pos_corrente = deposito
    orario_corrente = ORA_INIZIO_GIORNATA
    tempo_intervento = 20  # minuti
    
    for ora_min, ora_max, app in app_normali:
        # Punti base da inserire prima di questo appuntamento
        if punti_base:
            # Scegli punti raggiungibili (stima con Haversine a 35km/h)
            # Usa coords di routing (access point se presente)
            pos_lat, pos_lon = _coord_routing(pos_corrente)
            app_lat, app_lon = _coord_routing(app)
            da_inserire = []
            for p in punti_base[:]:
                p_lat, p_lon = _coord_routing(p)
                dist = _haversine_km(pos_lat, pos_lon, p_lat, p_lon)
                tempo_andata = (dist / 35) * 60
                dist_a_app = _haversine_km(p_lat, p_lon, app_lat, app_lon)
                tempo_a_app = (dist_a_app / 35) * 60
                
                orario_arrivo_app = orario_corrente + tempo_andata + tempo_intervento + tempo_a_app
                if orario_arrivo_app <= ora_max:
                    da_inserire.append(p)
            
            # Ordina i punti selezionati con sweep dal punto corrente
            if da_inserire:
                ordinati = _ordina_sweep(da_inserire, pos_corrente)
                percorso.extend(ordinati)
                for p in ordinati:
                    punti_base.remove(p)
                    pos_corrente = p
                    orario_corrente += tempo_intervento  # Stima semplice
        
        # Aggiungi appuntamento
        percorso.append(app)
        pos_corrente = app
        orario_corrente = ora_min + tempo_intervento
    
    # Punti base rimanenti: ottimizza con OSRM Trip
    if punti_base:
        ordine = _ottimizza_con_osrm_trip(punti_base, deposito)
        if ordine:
            percorso.extend([punti_base[i] for i in ordine])
        else:
            # Fallback: sweep dal deposito
            percorso.extend(_ordina_sweep(punti_base, deposito))
    
    # Appuntamenti serali alla fine
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
