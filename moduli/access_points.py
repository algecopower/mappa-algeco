# -*- coding: utf-8 -*-
"""
Access points (UPS-style) per il routing.

Idea:
 - Per ogni contatore con civico valido, calcoliamo un access point
   sulla strada CORRETTA (matching del nome via tramite OSRM /nearest).
 - Le coordinate originali Lat/Lon (centroide del fabbricato) restano
   come marker visivo; gli access point sono usati SOLO per il routing.
 - Override manuale per Eneltel (file JSON) ha priorità assoluta.
 - Cache per (eneltel|via|civico|comune) per evitare chiamate ripetute a OSRM.

Il fallback alle coordinate originali avviene quando:
  - civico mancante / SN / SNC / S.N. / S.N.C. ...
  - OSRM non risponde
  - nessun candidato di nearest ha un nome via "abbastanza simile"
"""
from __future__ import annotations

import logging
import math
import os
import re
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd
import requests

from config import OSRM_SERVER, OSRM_TIMEOUT, PROVINCIA_ATTIVA
from .safe_json import json_modify, json_read, json_write

logger = logging.getLogger('mappa_algeco')


# =============================================================================
# COSTANTI / CONFIGURAZIONE
# =============================================================================

# Soglie per accettare il match di una via candidata
SOGLIA_SIMILARITY = 0.85
DISTANZA_MAX_SNAP_M = 80.0

# Numero di candidati richiesti a OSRM /nearest.
# Serve abbastanza per pescare anche strade "minori" sotto un edificio,
# spesso le prime risposte sono passi carrai/strade interne senza nome.
OSRM_NEAREST_NUMBER = 15

# Prefissi tipici di nomi via italiani da rimuovere prima del match.
# La normalizzazione riduce le abbreviazioni puntate a token "spazianti"
# (es. "c.so" → "c so"), quindi qui elenchiamo le forme post-normalizzazione.
_PREFISSI_VIA = (
    'via', 'viale', 'vle',
    'corso', 'c so', 'cso',
    'piazza', 'p zza', 'pzza',
    'piazzale', 'p le', 'ple',
    'vicolo', 'v lo', 'vlo',
    'largo', 'l go', 'lgo',
    'strada', 'str',
    'borgo', 'borg', 'fraz', 'frazione',
    'localita', 'loc',
    'contrada', 'contr',
    'rotonda', 'rondo',
    'salita', 'discesa', 'galleria', 'galleri',
)

# Civici "non disponibili" da trattare come assenti
_RE_CIVICO_INVALIDO = re.compile(
    r'^\s*(s\.?\s*n\.?\s*c?\.?|n\.?\s*c?\.?|nc|s\.?\s*n\.?|s/n|none|null|nan|-)\s*$',
    re.IGNORECASE,
)

_OVERRIDES_CACHE: Dict[str, Dict[str, Any]] = {}
_OVERRIDES_LOCK = threading.Lock()
_OVERRIDES_LOADED = False


# =============================================================================
# PATH HELPERS
# =============================================================================

def _file_overrides(provincia: Optional[str] = None) -> str:
    prov = (provincia or PROVINCIA_ATTIVA).lower()
    return os.path.join('data', prov, 'access_overrides.json')


def _file_overrides_history(provincia: Optional[str] = None) -> str:
    prov = (provincia or PROVINCIA_ATTIVA).lower()
    return os.path.join('data', prov, 'access_overrides_history.json')


def _file_cache(provincia: Optional[str] = None) -> str:
    prov = (provincia or PROVINCIA_ATTIVA).lower()
    return os.path.join('cache', f'access_points_{prov}.json')


# Quante voci di history conservare per Eneltel (oltre, le più vecchie sono droppate)
HISTORY_MAX_ENTRIES = 10


# =============================================================================
# OVERRIDES (manuali per Eneltel) -- priorità assoluta
# =============================================================================

def _load_overrides() -> Dict[str, Dict[str, Any]]:
    """Carica tutti gli override manuali (per provincia attiva), con caching in-memory."""
    global _OVERRIDES_LOADED
    with _OVERRIDES_LOCK:
        if not _OVERRIDES_LOADED:
            data = json_read(_file_overrides(), default={}) or {}
            if isinstance(data, dict):
                _OVERRIDES_CACHE.clear()
                _OVERRIDES_CACHE.update(data)
            _OVERRIDES_LOADED = True
        return dict(_OVERRIDES_CACHE)


def reload_overrides() -> None:
    """Forza la rilettura del file overrides (utile dopo modifica manuale)."""
    global _OVERRIDES_LOADED
    with _OVERRIDES_LOCK:
        _OVERRIDES_LOADED = False
    _load_overrides()


def get_override(eneltel: str) -> Optional[Dict[str, Any]]:
    """Ritorna l'override per Eneltel (o None)."""
    if not eneltel:
        return None
    return _load_overrides().get(str(eneltel).strip())


def _push_history(eneltel: str, prev_record: Optional[Dict[str, Any]], event: str) -> None:
    """
    Aggiunge una voce di storia per un Eneltel (per supportare l'undo).

    Args:
        eneltel: chiave Eneltel.
        prev_record: lo stato override PRIMA dell'operazione (None = non c'era).
        event: 'replaced' (sovrascritto da nuovo save) | 'deleted' (rimosso).

    Le voci più vecchie oltre HISTORY_MAX_ENTRIES vengono droppate.
    Se prev_record è None (cioè non c'era nulla da salvare), non si aggiunge
    nulla: undo deve restituire qualcosa di significativo.
    """
    if not eneltel or prev_record is None:
        return
    key = str(eneltel).strip()
    snapshot = dict(prev_record)
    snapshot['_event'] = event
    snapshot['_ts_event'] = datetime.utcnow().isoformat(timespec='seconds') + 'Z'

    def _modifier(current):
        if not isinstance(current, dict):
            current = {}
        lst = current.get(key) or []
        if not isinstance(lst, list):
            lst = []
        lst.append(snapshot)
        if len(lst) > HISTORY_MAX_ENTRIES:
            lst = lst[-HISTORY_MAX_ENTRIES:]
        current[key] = lst
        return current

    json_modify(_file_overrides_history(), _modifier, default={}, indent=2)


def _pop_history(eneltel: str) -> Optional[Dict[str, Any]]:
    """
    Estrae e ritorna l'ultima voce di history per Eneltel (o None se vuota).
    """
    if not eneltel:
        return None
    key = str(eneltel).strip()
    popped: List[Optional[Dict[str, Any]]] = [None]

    def _modifier(current):
        if not isinstance(current, dict):
            return None
        lst = current.get(key)
        if not isinstance(lst, list) or not lst:
            return None
        popped[0] = lst.pop()
        if lst:
            current[key] = lst
        else:
            current.pop(key, None)
        return current

    json_modify(_file_overrides_history(), _modifier, default={}, indent=2)
    return popped[0]


def has_history(eneltel: str) -> bool:
    """Ritorna True se esiste almeno una voce di history per Eneltel."""
    if not eneltel:
        return False
    key = str(eneltel).strip()
    data = json_read(_file_overrides_history(), default={}) or {}
    if not isinstance(data, dict):
        return False
    lst = data.get(key)
    return isinstance(lst, list) and len(lst) > 0


def get_history(eneltel: str) -> List[Dict[str, Any]]:
    """Ritorna lo storico (lista) per Eneltel; lista vuota se nessuno."""
    if not eneltel:
        return []
    key = str(eneltel).strip()
    data = json_read(_file_overrides_history(), default={}) or {}
    if not isinstance(data, dict):
        return []
    lst = data.get(key)
    if not isinstance(lst, list):
        return []
    return list(lst)


def salva_override(eneltel: str, lat: float, lon: float, user: Optional[str] = None) -> bool:
    """
    Salva (o aggiorna) un override manuale per Eneltel.
    Ritorna True se il file è stato scritto correttamente.

    Se esiste già un override per questo Eneltel, lo stato precedente viene
    spinto in `access_overrides_history.json` per supportare l'undo.
    """
    if not eneltel:
        return False
    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        return False
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return False

    key = str(eneltel).strip()
    record = {
        'lat': lat,
        'lon': lon,
        'ts': datetime.utcnow().isoformat(timespec='seconds') + 'Z',
    }
    if user:
        record['user'] = str(user)

    prev_holder: List[Optional[Dict[str, Any]]] = [None]

    def _modifier(current):
        if not isinstance(current, dict):
            current = {}
        prev_holder[0] = current.get(key)  # snapshot precedente (può essere None)
        current[key] = record
        return current

    ok, _ = json_modify(_file_overrides(), _modifier, default={}, indent=2)
    if ok:
        with _OVERRIDES_LOCK:
            _OVERRIDES_CACHE[key] = record
        # Spinge lo stato precedente nello storico (anche se None? no: solo se esisteva)
        _push_history(key, prev_holder[0], 'replaced')
    return ok


def rimuovi_override(eneltel: str) -> bool:
    """
    Rimuove un override manuale (torna allo snap automatico).
    Salva lo stato corrente in history per supportare l'undo.
    """
    if not eneltel:
        return False
    key = str(eneltel).strip()
    prev_holder: List[Optional[Dict[str, Any]]] = [None]

    def _modifier(current):
        if not isinstance(current, dict) or key not in current:
            return None  # nessuna scrittura
        prev_holder[0] = current.get(key)
        del current[key]
        return current

    ok, _ = json_modify(_file_overrides(), _modifier, default={}, indent=2)
    if ok:
        with _OVERRIDES_LOCK:
            _OVERRIDES_CACHE.pop(key, None)
        _push_history(key, prev_holder[0], 'deleted')
    return ok


def undo_override(eneltel: str) -> Optional[Dict[str, Any]]:
    """
    Annulla l'ultima modifica all'override per Eneltel ripristinando lo stato
    immediatamente precedente dallo storico.

    Returns:
        Il record ripristinato (con chiavi lat, lon, ts, user), oppure None
        se non c'era nulla da ripristinare.
    """
    if not eneltel:
        return None
    key = str(eneltel).strip()
    snapshot = _pop_history(key)
    if not snapshot:
        return None

    # Pulisci campi tecnici aggiunti da _push_history
    record = {k: v for k, v in snapshot.items() if not k.startswith('_')}
    try:
        record['lat'] = float(record.get('lat'))
        record['lon'] = float(record.get('lon'))
    except (TypeError, ValueError):
        return None
    record['ts'] = datetime.utcnow().isoformat(timespec='seconds') + 'Z'

    def _modifier(current):
        if not isinstance(current, dict):
            current = {}
        current[key] = record
        return current

    ok, _ = json_modify(_file_overrides(), _modifier, default={}, indent=2)
    if not ok:
        return None
    with _OVERRIDES_LOCK:
        _OVERRIDES_CACHE[key] = record
    return record


# =============================================================================
# CACHE auto-snap (per via+civico+comune)
# =============================================================================

_SNAP_CACHE: Dict[str, Dict[str, Any]] = {}
_SNAP_LOCK = threading.Lock()
_SNAP_LOADED = False


def _load_snap_cache() -> None:
    global _SNAP_LOADED
    with _SNAP_LOCK:
        if _SNAP_LOADED:
            return
        data = json_read(_file_cache(), default={}) or {}
        if isinstance(data, dict):
            _SNAP_CACHE.clear()
            _SNAP_CACHE.update(data)
        _SNAP_LOADED = True


def _save_snap_cache() -> None:
    """Persiste l'intera cache snap. Chiamato in batch (non per singolo punto)."""
    with _SNAP_LOCK:
        snapshot = dict(_SNAP_CACHE)
    json_write(_file_cache(), snapshot, indent=None, separators=(',', ':'))


def _cache_key(eneltel: str, via: str, civico: str, comune: str) -> str:
    return f"{(eneltel or '').strip()}|{_normalizza_via(via)}|{(civico or '').strip().lower()}|{(comune or '').strip().lower()}"


# =============================================================================
# UTILITY
# =============================================================================

def _deaccent(s: str) -> str:
    if not s:
        return ''
    nfkd = unicodedata.normalize('NFKD', s)
    return ''.join(c for c in nfkd if not unicodedata.combining(c))


def _normalizza_via(nome: str) -> str:
    """
    Normalizza un nome via per il match:
      - lowercase, deaccento
      - rimuove punteggiatura
      - strip dei prefissi tipici (via, viale, corso, ...)
      - rimozione spazi multipli
    """
    if not nome:
        return ''
    s = _deaccent(str(nome)).lower().strip()
    # Sostituisci punteggiatura con spazi
    s = re.sub(r"[\.,;:'`]+", ' ', s)
    s = re.sub(r"[-_/\\]+", ' ', s)
    s = re.sub(r"\s+", ' ', s).strip()
    # Rimuovi prefissi via/viale/corso/...
    for _ in range(2):  # prefisso doppio (es. "Via Strada Nuova")
        for pref in _PREFISSI_VIA:
            if s.startswith(pref + ' '):
                s = s[len(pref) + 1:].strip()
                break
        else:
            break
    return s


def _civico_valido(civico: Any) -> bool:
    if civico is None:
        return False
    s = str(civico).strip()
    if not s:
        return False
    if _RE_CIVICO_INVALIDO.match(s):
        return False
    return True


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0
    rlat1, rlon1, rlat2, rlon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = rlat2 - rlat1
    dlon = rlon2 - rlon1
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    # Token set Jaccard
    ta = set(a.split())
    tb = set(b.split())
    if ta and tb:
        jaccard = len(ta & tb) / len(ta | tb)
    else:
        jaccard = 0.0
    # SequenceMatcher su stringhe normalizzate
    seq = SequenceMatcher(None, a, b).ratio()
    # "Contiene" bonus: una è sottostringa dell'altra
    contain = 1.0 if (a in b or b in a) else 0.0
    return max(seq, jaccard, contain * 0.95)


# =============================================================================
# OSRM /nearest
# =============================================================================

def _osrm_nearest(lat: float, lon: float, number: int = OSRM_NEAREST_NUMBER) -> List[Dict[str, Any]]:
    """Chiama OSRM /nearest e ritorna la lista di waypoints (può essere vuota)."""
    url = f"{OSRM_SERVER}/nearest/v1/driving/{lon},{lat}"
    try:
        r = requests.get(url, params={'number': number}, timeout=OSRM_TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except Exception as e:  # pragma: no cover -- network
        logger.debug(f"[access_points] OSRM nearest fallito: {e}")
        return []
    if data.get('code') != 'Ok':
        return []
    return data.get('waypoints', []) or []


def _scegli_candidato(
    waypoints: List[Dict[str, Any]],
    lat: float,
    lon: float,
    via_norm: str,
) -> Optional[Tuple[float, float, float, str]]:
    """
    Sceglie tra i waypoints OSRM quello il cui name corrisponde meglio a `via_norm`.

    Ritorna: (lat, lon, similarity, road_name) oppure None.
    """
    if not waypoints or not via_norm:
        return None

    best: Optional[Tuple[float, float, float, str]] = None
    for wp in waypoints:
        loc = wp.get('location') or []
        if len(loc) != 2:
            continue
        wlon, wlat = loc[0], loc[1]
        name = wp.get('name') or ''
        name_norm = _normalizza_via(name)
        sim = _similarity(via_norm, name_norm)
        dist_m = _haversine_m(lat, lon, wlat, wlon)
        if sim < SOGLIA_SIMILARITY:
            continue
        if dist_m > DISTANZA_MAX_SNAP_M:
            continue
        if best is None or sim > best[2]:
            best = (wlat, wlon, sim, name)
    return best


# =============================================================================
# API PUBBLICA
# =============================================================================

def risolvi_access_point(
    lat: float,
    lon: float,
    via: str,
    civico: str,
    eneltel: str = '',
    comune: str = '',
    use_cache: bool = True,
) -> Dict[str, Any]:
    """
    Risolve l'access point per un singolo contatore.

    Returns:
        dict con chiavi:
          - lat, lon: coordinate finali da usare per il routing
          - source: 'override' | 'osrm' | 'fallback' | 'no_civico'
          - road: nome strada candidata (se snap riuscito)
          - similarity: punteggio match (se snap riuscito)
          - distance_m: distanza dallo snap rispetto al lat/lon originale
    """
    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        return {
            'lat': lat, 'lon': lon, 'source': 'fallback',
            'road': None, 'similarity': None, 'distance_m': None,
        }

    # 1) Override manuale per Eneltel
    if eneltel:
        ovr = get_override(str(eneltel).strip())
        if ovr:
            try:
                return {
                    'lat': float(ovr['lat']),
                    'lon': float(ovr['lon']),
                    'source': 'override',
                    'road': None,
                    'similarity': None,
                    'distance_m': _haversine_m(lat, lon, float(ovr['lat']), float(ovr['lon'])),
                }
            except (TypeError, ValueError, KeyError):
                pass

    # 2) Civico mancante / non valido → coords originali
    if not _civico_valido(civico):
        return {
            'lat': lat, 'lon': lon, 'source': 'no_civico',
            'road': None, 'similarity': None, 'distance_m': 0.0,
        }

    via_norm = _normalizza_via(via)
    if not via_norm:
        return {
            'lat': lat, 'lon': lon, 'source': 'fallback',
            'road': None, 'similarity': None, 'distance_m': 0.0,
        }

    # 3) Cache hit?
    cache_key = _cache_key(str(eneltel or ''), via or '', civico or '', comune or '')
    if use_cache:
        _load_snap_cache()
        with _SNAP_LOCK:
            cached = _SNAP_CACHE.get(cache_key)
        if cached:
            try:
                return {
                    'lat': float(cached['lat']),
                    'lon': float(cached['lon']),
                    'source': cached.get('source', 'osrm'),
                    'road': cached.get('road'),
                    'similarity': cached.get('similarity'),
                    'distance_m': cached.get('distance_m'),
                }
            except (TypeError, ValueError, KeyError):
                pass

    # 4) OSRM nearest + match per nome via
    waypoints = _osrm_nearest(lat, lon, OSRM_NEAREST_NUMBER)
    candidato = _scegli_candidato(waypoints, lat, lon, via_norm)

    if candidato:
        wlat, wlon, sim, road = candidato
        result = {
            'lat': wlat,
            'lon': wlon,
            'source': 'osrm',
            'road': road,
            'similarity': round(sim, 3),
            'distance_m': round(_haversine_m(lat, lon, wlat, wlon), 1),
        }
    else:
        result = {
            'lat': lat,
            'lon': lon,
            'source': 'fallback',
            'road': None,
            'similarity': None,
            'distance_m': 0.0,
        }

    # Salva in cache (in memoria; flush opzionale fuori)
    if use_cache:
        with _SNAP_LOCK:
            _SNAP_CACHE[cache_key] = {
                'lat': result['lat'],
                'lon': result['lon'],
                'source': result['source'],
                'road': result['road'],
                'similarity': result['similarity'],
                'distance_m': result['distance_m'],
                'ts': datetime.utcnow().isoformat(timespec='seconds') + 'Z',
            }

    return result


def _estrai_record(p: Any) -> Tuple[Optional[float], Optional[float], str, str, str, str]:
    """
    Estrae (lat, lon, via, civico, eneltel, comune) da un record dict-like
    (sia keys "Lat/Lon/VIA_K/CIVICO_K/Eneltel_Key/COMUNE", sia minuscole "lat/lon/via/civico/eneltel/comune").
    """
    def _get(*keys, default=''):
        for k in keys:
            if k in p and p[k] is not None:
                return p[k]
        return default

    lat = _get('Lat', 'lat', default=None)
    lon = _get('Lon', 'lon', default=None)
    try:
        lat = float(lat) if lat is not None else None
        lon = float(lon) if lon is not None else None
    except (TypeError, ValueError):
        lat, lon = None, None

    via = str(_get('VIA_K', 'via', 'Indirizzo', 'indirizzo', default='') or '').strip()
    civico = str(_get('CIVICO_K', 'civico', 'Num', 'NUM', default='') or '').strip()
    eneltel = str(
        _get('Eneltel_Key', 'eneltel', 'Eneltel', 'ENELTEL', default='') or ''
    ).strip()
    comune = str(_get('COMUNE', 'comune', default='') or '').strip()

    return lat, lon, via, civico, eneltel, comune


def arricchisci_punti(
    punti: Iterable[Any],
    *,
    max_workers: int = 8,
    persisti_cache: bool = True,
) -> List[Any]:
    """
    Arricchisce in-place una lista di record (dict) aggiungendo:
      - Lat_access, Lon_access
      - access_source ('override' | 'osrm' | 'fallback' | 'no_civico')
      - access_road (str | None)

    Funziona sia con dict in stile FILONE (Lat/Lon/VIA_K/CIVICO_K/Eneltel_Key/COMUNE),
    sia con dict in stile mappa_data.json (lat/lon/via/civico/eneltel/comune).

    Compatibile con DataFrame: passa df.to_dict('records') e poi reincolla i valori.
    """
    if punti is None:
        return []

    items = list(punti)
    if not items:
        return items

    _load_snap_cache()
    _load_overrides()

    # Prepara liste indicizzate per parallel I/O
    needed_idx: List[int] = []
    inputs: List[Tuple[float, float, str, str, str, str]] = []

    for i, p in enumerate(items):
        if not isinstance(p, dict):
            continue
        lat, lon, via, civico, eneltel, comune = _estrai_record(p)

        if lat is None or lon is None or (isinstance(lat, float) and math.isnan(lat)) or (isinstance(lon, float) and math.isnan(lon)):
            # Niente coords valide → niente access point
            continue

        # Override sempre prioritario, anche senza civico
        ovr = get_override(eneltel) if eneltel else None
        if ovr:
            try:
                p['Lat_access'] = float(ovr['lat'])
                p['Lon_access'] = float(ovr['lon'])
                p['access_source'] = 'override'
                p['access_road'] = None
                continue
            except (TypeError, ValueError, KeyError):
                pass

        # Civico mancante: copia delle originali, fallback diretto
        if not _civico_valido(civico):
            p['Lat_access'] = lat
            p['Lon_access'] = lon
            p['access_source'] = 'no_civico'
            p['access_road'] = None
            continue

        # Cache hit
        ck = _cache_key(eneltel, via, civico, comune)
        with _SNAP_LOCK:
            cached = _SNAP_CACHE.get(ck)
        if cached:
            try:
                p['Lat_access'] = float(cached['lat'])
                p['Lon_access'] = float(cached['lon'])
                p['access_source'] = cached.get('source', 'osrm')
                p['access_road'] = cached.get('road')
                continue
            except (TypeError, ValueError, KeyError):
                pass

        needed_idx.append(i)
        inputs.append((lat, lon, via, civico, eneltel, comune))

    if needed_idx:
        logger.info(f"   📍 Access points: {len(needed_idx)} da risolvere via OSRM (cache: {len(_SNAP_CACHE)})")

        def _resolve(args):
            lat, lon, via, civico, eneltel, comune = args
            # use_cache=True scrive in cache automaticamente
            return risolvi_access_point(lat, lon, via, civico, eneltel, comune, use_cache=True)

        # Parallel I/O su OSRM locale
        if max_workers and max_workers > 1:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {pool.submit(_resolve, args): idx for args, idx in zip(inputs, needed_idx)}
                for fut in as_completed(futures):
                    idx = futures[fut]
                    try:
                        res = fut.result()
                    except Exception:
                        res = None
                    p = items[idx]
                    if res:
                        p['Lat_access'] = res['lat']
                        p['Lon_access'] = res['lon']
                        p['access_source'] = res['source']
                        p['access_road'] = res.get('road')
                    else:
                        # In caso di errore: fallback a originali
                        lat, lon, *_ = inputs[needed_idx.index(idx)]
                        p['Lat_access'] = lat
                        p['Lon_access'] = lon
                        p['access_source'] = 'fallback'
                        p['access_road'] = None
        else:
            for args, idx in zip(inputs, needed_idx):
                res = _resolve(args)
                p = items[idx]
                p['Lat_access'] = res['lat']
                p['Lon_access'] = res['lon']
                p['access_source'] = res['source']
                p['access_road'] = res.get('road')

        if persisti_cache:
            _save_snap_cache()

    # Statistiche per log
    sources: Dict[str, int] = {}
    for p in items:
        if isinstance(p, dict) and 'access_source' in p:
            sources[p['access_source']] = sources.get(p['access_source'], 0) + 1
    if sources:
        riassunto = ' '.join(f"{k}={v}" for k, v in sorted(sources.items()))
        logger.info(f"   📍 Access points distribuzione: {riassunto}")

    return items


def get_access_lat_lon(p: Dict[str, Any]) -> Tuple[float, float]:
    """
    Helper per leggere le coords di routing da un record:
    usa Lat_access/Lon_access se presenti, altrimenti Lat/Lon (o lat/lon).

    Solleva ValueError se non sono disponibili coordinate valide.
    """
    if not isinstance(p, dict):
        raise ValueError("Record non è un dict")

    candidates = [
        ('Lat_access', 'Lon_access'),
        ('lat_access', 'lon_access'),
        ('Lat', 'Lon'),
        ('lat', 'lon'),
    ]
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

    raise ValueError("Nessuna coordinata valida trovata nel record")


def arricchisci_dataframe(df, *, max_workers: int = 8, persisti_cache: bool = True):
    """
    Versione "DataFrame" di `arricchisci_punti`.

    Aggiunge le colonne `Lat_access`, `Lon_access`, `access_source`,
    `access_road` al DataFrame ORIGINALE (mutazione in-place).
    Lavora sui record convertiti in dict, poi assegna i risultati come
    nuove colonne usando l'indice del DataFrame.

    Returns:
        Il DataFrame modificato (per fluent API).
    """
    if df is None or len(df) == 0:
        return df

    # Convertiamo in records senza alterare df (le colonne duplicate sono
    # gestite dal converter di pandas: l'ultima vince).
    # Per sicurezza usiamo un view senza duplicati solo durante la chiamata.
    if df.columns.duplicated().any():
        df_view = df.loc[:, ~df.columns.duplicated()]
    else:
        df_view = df

    records = df_view.to_dict('records')
    arricchisci_punti(records, max_workers=max_workers, persisti_cache=persisti_cache)

    n = len(records)
    lat_acc = [None] * n
    lon_acc = [None] * n
    src = [None] * n
    road = [None] * n
    for i, r in enumerate(records):
        if not isinstance(r, dict):
            continue
        lat_acc[i] = r.get('Lat_access')
        lon_acc[i] = r.get('Lon_access')
        src[i] = r.get('access_source')
        road[i] = r.get('access_road')

    # Importante: assegnare via index per mantenere l'allineamento del DF originale
    df['Lat_access'] = pd.Series(lat_acc, index=df.index)
    df['Lon_access'] = pd.Series(lon_acc, index=df.index)
    df['access_source'] = pd.Series(src, index=df.index)
    df['access_road'] = pd.Series(road, index=df.index)

    return df


def access_point_per_eneltel(
    eneltel: str,
    *,
    fallback_punto: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Helper per le API: ritorna l'access point corrente per un Eneltel.

    Se è presente un override → source='override'.
    Altrimenti, se viene fornito `fallback_punto` (dict tipo contatore mappa_data),
    calcola/legge dalla cache lo snap automatico.

    Ritorna {lat, lon, source, road?} oppure None.
    """
    if not eneltel:
        return None
    eneltel = str(eneltel).strip()

    ovr = get_override(eneltel)
    if ovr:
        try:
            return {
                'lat': float(ovr['lat']),
                'lon': float(ovr['lon']),
                'source': 'override',
                'road': None,
                'ts': ovr.get('ts'),
            }
        except (TypeError, ValueError, KeyError):
            pass

    if fallback_punto:
        lat, lon, via, civico, _en, comune = _estrai_record(fallback_punto)
        if lat is None or lon is None:
            return None
        res = risolvi_access_point(lat, lon, via, civico, eneltel, comune, use_cache=True)
        return res
    return None
