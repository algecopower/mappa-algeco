"""
Calcolo distanze e routing con OSRM.
Supporta sia modalità con tecnici che modalità generica.
Include matrice distanze per ottimizzazione percorsi.
"""
import math
import requests
from datetime import datetime

from config import (
    OSRM_SERVER,
    OSRM_TIMEOUT,
    VELOCITA_MEDIA_KMH,
    TEMPO_INTERVENTO_MIN,
    FATTORE_CORREZIONE_HAVERSINE,
    USA_OSRM,
    MATRICOLE_VIP
)

from .cache import genera_hash_dati
from .percorso import ordina_per_fasce_e_vicinanza


# ============================================================================
# HELPER: coordinate per il routing (UPS-style access points)
# ============================================================================

def _coord_routing(p):
    """
    Ritorna (lat, lon) da usare per OSRM:
      Lat_access/Lon_access > lat_access/lon_access > Lat/Lon > lat/lon.
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
    raise ValueError(
        f"Punto senza coordinate valide (eneltel={p.get('Eneltel_Key') or p.get('eneltel') or '?'})"
    )


# ============================================================================
# MATRICE DISTANZE OSRM - Per ottimizzazione percorsi con distanze reali
# ============================================================================

# Limite massimo coordinate per richiesta OSRM Table API
# Il default di OSRM è 100, ma usiamo un valore più conservativo
OSRM_MAX_TABLE_SIZE = 80


def calcola_matrice_distanze_osrm(punti, deposito, profile='car'):
    """
    Calcola matrice delle distanze/durate tra tutti i punti usando OSRM Table API.
    
    La matrice include il deposito come primo elemento (indice 0).
    Usa durata (tempo) come metrica principale per l'ottimizzazione,
    perché considera traffico e condizioni stradali reali.
    
    Se ci sono più di OSRM_MAX_TABLE_SIZE coordinate, usa un approccio
    semplificato con chiamate singole route invece della matrice completa.
    
    Args:
        punti: lista di punti con Lat, Lon
        deposito: punto di partenza/ritorno con Lat, Lon
        profile: profilo OSRM ('car', 'bike', 'foot')
    
    Returns:
        dict con:
        - 'successo': bool
        - 'durate': matrice NxN delle durate in minuti (None se fallito)
        - 'distanze': matrice NxN delle distanze in km (None se fallito)
        - 'fonte': stringa descrittiva
        - 'errore': messaggio errore se fallito
    """
    risultato = {
        'successo': False,
        'durate': None,
        'distanze': None,
        'fonte': 'OSRM Table API',
        'errore': None
    }
    
    if not punti:
        risultato['errore'] = "Nessun punto fornito"
        return risultato
    
    # Costruisci lista coordinate: deposito + tutti i punti.
    # Formato OSRM: lon,lat. Usa Lat_access/Lon_access se presenti.
    dep_lat, dep_lon = _coord_routing(deposito)
    coords = [(dep_lon, dep_lat)]
    for p in punti:
        plat, plon = _coord_routing(p)
        coords.append((plon, plat))
    
    n_coords = len(coords)
    
    # Se troppe coordinate, calcola matrice a blocchi usando OSRM
    if n_coords > OSRM_MAX_TABLE_SIZE:
        return _calcola_matrice_osrm_a_blocchi(punti, deposito, profile)
    
    coords_str = ";".join([f"{lon},{lat}" for lon, lat in coords])
    url = f"{OSRM_SERVER}/table/v1/{profile}/{coords_str}"
    
    params = {
        'annotations': 'duration,distance'  # Richiedi sia durata che distanza
    }
    
    try:
        response = requests.get(url, params=params, timeout=30)  # Timeout aumentato per matrici grandi
        response.raise_for_status()
        data = response.json()
        
        if data.get('code') == 'Ok':
            len(coords)
            
            # Estrai matrice durate (in secondi -> converti in minuti)
            durate_raw = data.get('durations', [])
            if durate_raw:
                risultato['durate'] = []
                for row in durate_raw:
                    risultato['durate'].append([
                        round(d / 60, 1) if d is not None else None 
                        for d in row
                    ])
            
            # Estrai matrice distanze (in metri -> converti in km)
            distanze_raw = data.get('distances', [])
            if distanze_raw:
                risultato['distanze'] = []
                for row in distanze_raw:
                    risultato['distanze'].append([
                        round(d / 1000, 2) if d is not None else None 
                        for d in row
                    ])
            
            risultato['successo'] = True
        else:
            risultato['errore'] = data.get('message', 'Errore sconosciuto OSRM Table')
            
    except requests.exceptions.Timeout:
        risultato['errore'] = "Timeout connessione OSRM Table API"
    except requests.exceptions.RequestException as e:
        # Se errore 400 (troppe coordinate), usa fallback Haversine
        if '400' in str(e):
            print("      ⚠️  OSRM ha rifiutato la richiesta, uso calcolo a blocchi")
            return _calcola_matrice_osrm_a_blocchi(punti, deposito, profile)
        risultato['errore'] = f"Errore connessione: {str(e)}"
    except Exception as e:
        risultato['errore'] = f"Errore generico: {str(e)}"
    
    return risultato


def _calcola_matrice_osrm_a_blocchi(punti, deposito, profile='driving'):
    """
    Calcola matrice distanze usando OSRM a blocchi per superare il limite di coordinate.
    
    Divide le coordinate in blocchi gestibili e calcola le distanze punto-a-punto
    usando l'API route di OSRM invece della Table API.
    
    Per matrici grandi, calcola solo le distanze necessarie:
    - Dal deposito a tutti i punti
    - Tra tutti i punti consecutivi più vicini (nearest neighbor)
    """
    n_punti = len(punti)
    print(f"      🔄 Calcolo matrice {n_punti + 1}x{n_punti + 1} con OSRM a blocchi...")
    
    risultato = {
        'successo': True,
        'durate': None,
        'distanze': None,
        'fonte': 'OSRM (calcolo a blocchi)',
        'errore': None
    }
    
    # Lista completa: deposito (indice 0) + punti.
    # Manteniamo il riferimento ai dict originali cosi' _coord_routing
    # sceglie correttamente Lat_access/Lon_access quando disponibili.
    tutti_punti = [deposito] + list(punti)
    n = len(tutti_punti)
    
    # Inizializza matrici con None
    distanze = [[None] * n for _ in range(n)]
    durate = [[None] * n for _ in range(n)]
    
    # Diagonale = 0
    for i in range(n):
        distanze[i][i] = 0
        durate[i][i] = 0
    
    # Calcola distanze usando la Table API a blocchi
    # Dividi in sotto-matrici di max OSRM_MAX_TABLE_SIZE
    block_size = OSRM_MAX_TABLE_SIZE - 1  # -1 per sicurezza
    
    # Calcola tutte le distanze usando blocchi della Table API
    # Strategia: calcola blocchi quadrati che coprono tutta la matrice
    for i_start in range(0, n, block_size):
        i_end = min(i_start + block_size, n)
        
        for j_start in range(0, n, block_size):
            j_end = min(j_start + block_size, n)
            
            # Estrai coordinate per questo blocco
            sources_idx = list(range(i_start, i_end))
            destinations_idx = list(range(j_start, j_end))
            
            # Unisci gli indici univoci per la chiamata OSRM
            all_idx = sorted(set(sources_idx + destinations_idx))
            
            if len(all_idx) <= 1:
                continue
            
            # Costruisci stringa coordinate (con access point se disponibile)
            coords = []
            for i in all_idx:
                plat, plon = _coord_routing(tutti_punti[i])
                coords.append((plon, plat))
            coords_str = ";".join([f"{lon},{lat}" for lon, lat in coords])
            
            # Mappa indici locali
            idx_to_local = {idx: local for local, idx in enumerate(all_idx)}
            sources_local = [idx_to_local[i] for i in sources_idx]
            destinations_local = [idx_to_local[j] for j in destinations_idx]
            
            url = f"{OSRM_SERVER}/table/v1/{profile}/{coords_str}"
            params = {
                'annotations': 'duration,distance',
                'sources': ';'.join(map(str, sources_local)),
                'destinations': ';'.join(map(str, destinations_local))
            }
            
            try:
                response = requests.get(url, params=params, timeout=30)
                response.raise_for_status()
                data = response.json()
                
                if data.get('code') == 'Ok':
                    durate_raw = data.get('durations', [])
                    distanze_raw = data.get('distances', [])
                    
                    # Copia i risultati nella matrice completa
                    for src_local, src_global in enumerate(sources_idx):
                        for dst_local, dst_global in enumerate(destinations_idx):
                            if durate_raw and src_local < len(durate_raw) and dst_local < len(durate_raw[src_local]):
                                d = durate_raw[src_local][dst_local]
                                if d is not None:
                                    durate[src_global][dst_global] = round(d / 60, 1)
                            
                            if distanze_raw and src_local < len(distanze_raw) and dst_local < len(distanze_raw[src_local]):
                                d = distanze_raw[src_local][dst_local]
                                if d is not None:
                                    distanze[src_global][dst_global] = round(d / 1000, 2)
                                    
            except Exception:
                # Se fallisce un blocco, usa Haversine per quel blocco
                for src_global in sources_idx:
                    for dst_global in destinations_idx:
                        if distanze[src_global][dst_global] is None and src_global != dst_global:
                            slat, slon = _coord_routing(tutti_punti[src_global])
                            dlat, dlon = _coord_routing(tutti_punti[dst_global])
                            dist = calcola_distanza_haversine(slat, slon, dlat, dlon) * FATTORE_CORREZIONE_HAVERSINE
                            distanze[src_global][dst_global] = round(dist, 2)
                            durate[src_global][dst_global] = round(dist / VELOCITA_MEDIA_KMH * 60, 1)
    
    # Riempi eventuali buchi rimasti con Haversine
    for i in range(n):
        for j in range(n):
            if distanze[i][j] is None and i != j:
                ilat, ilon = _coord_routing(tutti_punti[i])
                jlat, jlon = _coord_routing(tutti_punti[j])
                dist = calcola_distanza_haversine(ilat, ilon, jlat, jlon) * FATTORE_CORREZIONE_HAVERSINE
                distanze[i][j] = round(dist, 2)
                durate[i][j] = round(dist / VELOCITA_MEDIA_KMH * 60, 1)
    
    risultato['distanze'] = distanze
    risultato['durate'] = durate
    
    print(f"      ✅ Matrice {n}x{n} calcolata con OSRM a blocchi")
    
    return risultato


# Flag per tracciare se OSRM è stato verificato come non disponibile
_osrm_verified_down = False


def ottieni_matrice_distanze(punti, deposito, usa_osrm=True):
    """
    Ottiene la matrice distanze/durate usando OSRM.

    IMPORTANTE: OSRM è OBBLIGATORIO. Se non disponibile, solleva un'eccezione.
    Assicurarsi che Docker con OSRM sia attivo prima di eseguire.
    
    Se ci sono troppe coordinate per OSRM, usa automaticamente il fallback
    Haversine che approssima le distanze stradali.

    Args:
        punti: lista di punti da visitare
        deposito: punto di partenza/ritorno
        usa_osrm: ignorato (OSRM è sempre richiesto)

    Returns:
        dict con 'durate', 'distanze', 'fonte', 'successo'

    Raises:
        RuntimeError: se OSRM non è disponibile (non per troppe coordinate)
    """
    global _osrm_verified_down

    if not USA_OSRM:
        raise RuntimeError(
            "❌ ERRORE: USA_OSRM è disabilitato in config.py ma OSRM è obbligatorio!\n"
            "   Imposta USA_OSRM = True in config.py"
        )

    if _osrm_verified_down:
        raise RuntimeError(
            "❌ ERRORE: Server OSRM non disponibile!\n"
            "   Avvia Docker con OSRM prima di eseguire:\n"
            "   docker start osrm-server"
        )

    risultato = calcola_matrice_distanze_osrm(punti, deposito)

    if risultato['successo']:
        return risultato
    else:
        # Se l'errore contiene "fallback" o "Haversine", non è un errore di connessione OSRM
        errore = risultato.get('errore', '')
        if 'fallback' in errore.lower() or 'haversine' in errore.lower():
            # Non dovrebbe mai arrivare qui perché il fallback restituisce successo=True
            return risultato
        
        # OSRM non raggiungibile - errore critico
        _osrm_verified_down = True
        raise RuntimeError(
            "❌ ERRORE: Server OSRM non risponde!\n"
            f"   Dettaglio: {errore or 'Connessione fallita'}\n"
            "   Verifica che Docker sia attivo con: docker ps\n"
            "   Avvia OSRM con: docker start osrm-server"
        )


def calcola_distanza_haversine(lat1, lon1, lat2, lon2):
    """Calcola distanza in km tra due punti (linea d'aria)."""
    R = 6371
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a))
    return R * c


def calcola_rotta_osrm(coords_list, profile='car'):
    """Calcola rotta reale usando OSRM."""
    risultato = {
        'successo': False,
        'distanza_km': 0,
        'durata_min': 0,
        'geometria': None,
        'fonte': 'OSRM',
        'errore': None
    }
    
    if len(coords_list) < 2:
        risultato['errore'] = "Servono almeno 2 coordinate"
        return risultato
    
    coords_str = ";".join([f"{lon},{lat}" for lon, lat in coords_list])
    url = f"{OSRM_SERVER}/route/v1/{profile}/{coords_str}"
    
    params = {
        'overview': 'full',
        'geometries': 'geojson',
        'steps': 'false',
        'annotations': 'distance,duration'
    }
    
    try:
        response = requests.get(url, params=params, timeout=OSRM_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        
        if data.get('code') == 'Ok' and data.get('routes'):
            route = data['routes'][0]
            risultato['successo'] = True
            risultato['distanza_km'] = round(route['distance'] / 1000, 2)
            risultato['durata_min'] = round(route['duration'] / 60, 1)
            risultato['geometria'] = route['geometry']['coordinates']
            
            if 'legs' in route:
                risultato['legs'] = []
                for leg in route['legs']:
                    risultato['legs'].append({
                        'distanza_km': round(leg['distance'] / 1000, 2),
                        'durata_min': round(leg['duration'] / 60, 1)
                    })
        else:
            risultato['errore'] = data.get('message', 'Errore sconosciuto OSRM')
            
    except requests.exceptions.Timeout:
        risultato['errore'] = "Timeout connessione OSRM"
    except requests.exceptions.RequestException as e:
        risultato['errore'] = f"Errore connessione: {str(e)}"
    except Exception as e:
        risultato['errore'] = f"Errore generico: {str(e)}"
    
    return risultato


def calcola_geometria_percorso_waypoints(coords_list, profile='car'):
    """
    Geometria stradale OSRM lungo una sequenza fissa di waypoint (lon, lat).

    OSRM Route ha un limite pratico sul numero di coordinate nella URL; se si
    supera (~85), la richiesta viene spezzata in segmenti sovrapposti di un
    punto e le geometrie GeoJSON vengono unite.

    Args:
        coords_list: lista [(lon, lat), ...] ordine di visita (incluso deposito
            di partenza e ritorno se serve il round-trip).
        profile: profilo OSRM (default 'car', come calcola_rotta_osrm).

    Returns:
        (geometria, errore): geometria e' lista [[lon, lat], ...] oppure None.
    """
    if len(coords_list) < 2:
        return None, 'Servono almeno 2 coordinate'

    max_wp = 85

    if len(coords_list) <= max_wp:
        ris = calcola_rotta_osrm(coords_list, profile=profile)
        if ris['successo'] and ris.get('geometria'):
            return ris['geometria'], None
        return None, ris.get('errore') or 'OSRM non disponibile'

    merged = []
    start = 0
    while start < len(coords_list) - 1:
        end_exclusive = min(start + max_wp, len(coords_list))
        chunk = coords_list[start:end_exclusive]
        ris = calcola_rotta_osrm(chunk, profile=profile)
        if not ris['successo'] or not ris.get('geometria'):
            return None, ris.get('errore') or 'OSRM fallito su segmento intermedio'

        g = ris['geometria']
        if not merged:
            merged.extend(g)
        else:
            if g:
                lx, ly = merged[-1][0], merged[-1][1]
                fx, fy = g[0][0], g[0][1]
                if abs(lx - fx) < 1e-7 and abs(ly - fy) < 1e-7:
                    merged.extend(g[1:])
                else:
                    merged.extend(g)
            else:
                return None, 'Geometria OSRM vuota'

        if end_exclusive >= len(coords_list):
            break
        start = end_exclusive - 1

    return merged if merged else None, None


def calcola_distanze_percorso(percorso_ordinato, deposito, usa_osrm=True):
    """Calcola distanze complete per un percorso."""
    stats = {
        'num_tappe': len(percorso_ordinato),
        'distanza_totale_km': 0,
        'durata_guida_min': 0,
        'durata_interventi_min': 0,
        'durata_totale_min': 0,
        'fonte_calcolo': 'N/A',
        'dettaglio_tappe': [],
        'geometria_rotta': None,
        'include_ritorno': True,
        'calcolato_il': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    
    if not percorso_ordinato:
        return stats
    
    # Usa Lat_access/Lon_access se disponibili (UPS-style), altrimenti Lat/Lon
    dep_lat, dep_lon = _coord_routing(deposito)
    coords = [(dep_lon, dep_lat)]
    for punto in percorso_ordinato:
        plat, plon = _coord_routing(punto)
        coords.append((plon, plat))
    coords.append((dep_lon, dep_lat))
    
    osrm_ok = False
    if usa_osrm and USA_OSRM:
        risultato_osrm = calcola_rotta_osrm(coords)
        
        if risultato_osrm['successo']:
            osrm_ok = True
            stats['distanza_totale_km'] = risultato_osrm['distanza_km']
            stats['durata_guida_min'] = risultato_osrm['durata_min']
            stats['geometria_rotta'] = risultato_osrm['geometria']
            stats['fonte_calcolo'] = 'OSRM (rotta stradale reale)'
            
            if 'legs' in risultato_osrm:
                legs = risultato_osrm['legs']
                nomi = ['DEPOSITO'] + [p.get('CLI_K', f"Tappa {i+1}") for i, p in enumerate(percorso_ordinato)] + ['DEPOSITO']
                
                for i, leg in enumerate(legs):
                    stats['dettaglio_tappe'].append({
                        'da': nomi[i],
                        'a': nomi[i+1],
                        'distanza_km': leg['distanza_km'],
                        'durata_min': leg['durata_min']
                    })
    
    if not osrm_ok:
        distanza_linea = 0
        punto_prec = deposito
        
        for idx, punto in enumerate(percorso_ordinato):
            plat_prec, plon_prec = _coord_routing(punto_prec)
            plat_curr, plon_curr = _coord_routing(punto)
            dist = calcola_distanza_haversine(plat_prec, plon_prec, plat_curr, plon_curr)
            distanza_linea += dist
            
            stats['dettaglio_tappe'].append({
                'da': punto_prec.get('CLI_K', punto_prec.get('Nome', 'DEPOSITO')),
                'a': punto.get('CLI_K', f"Tappa {idx+1}"),
                'distanza_km': round(dist * FATTORE_CORREZIONE_HAVERSINE, 2),
                'durata_min': round((dist * FATTORE_CORREZIONE_HAVERSINE) / VELOCITA_MEDIA_KMH * 60, 1)
            })
            punto_prec = punto
        
        plat_prec, plon_prec = _coord_routing(punto_prec)
        dist_ritorno = calcola_distanza_haversine(plat_prec, plon_prec, dep_lat, dep_lon)
        distanza_linea += dist_ritorno
        
        stats['dettaglio_tappe'].append({
            'da': punto_prec.get('CLI_K', 'Ultima tappa'),
            'a': 'DEPOSITO',
            'distanza_km': round(dist_ritorno * FATTORE_CORREZIONE_HAVERSINE, 2),
            'durata_min': round((dist_ritorno * FATTORE_CORREZIONE_HAVERSINE) / VELOCITA_MEDIA_KMH * 60, 1)
        })
        
        stats['distanza_totale_km'] = round(distanza_linea * FATTORE_CORREZIONE_HAVERSINE, 1)
        stats['distanza_linea_aria_km'] = round(distanza_linea, 1)
        stats['durata_guida_min'] = round(stats['distanza_totale_km'] / VELOCITA_MEDIA_KMH * 60, 0)
        stats['fonte_calcolo'] = f'Haversine x{FATTORE_CORREZIONE_HAVERSINE} (stima)'
    
    stats['durata_interventi_min'] = len(percorso_ordinato) * TEMPO_INTERVENTO_MIN
    stats['durata_totale_min'] = stats['durata_guida_min'] + stats['durata_interventi_min']
    
    return stats


def genera_report_distanze_con_cache(df, deposito, cache_distanze, cache_hash):
    """Genera report distanze utilizzando la cache."""
    report_completo = {
        'generato_il': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'per_data': {},
        'totali_globali': {
            'km_totali': 0,
            'ore_guida_totali': 0,
            'contatori_totali': 0
        },
        'statistiche_cache': {
            'da_cache': 0,
            'ricalcolati': 0
        }
    }
    
    # Rileva modalità: con tecnici o generica
    tecnici_nel_df = df['TEC_K'].unique().tolist()
    modalita_generica = tecnici_nel_df == ['TUTTI']
    
    # Lista tecnici da elaborare
    if modalita_generica:
        lista_tecnici = ['TUTTI']
    else:
        # Includi tecnici VIP + 'TUTTI' (contatori senza operatore)
        lista_tecnici = [t for t in tecnici_nel_df if t in MATRICOLE_VIP or t == 'TUTTI']
    
    for data in sorted(df['DATA_K'].unique()):
        # Salta contatori senza data valida (schedulabili, gestiti dal frontend)
        if data == 'DATA_MANCANTE':
            continue
        
        report_completo['per_data'][data] = {
            'tecnici': {},
            'totali_giorno': {'km': 0, 'ore_guida': 0, 'contatori': 0}
        }
        
        df_giorno = df[df['DATA_K'] == data]
        
        for tec in lista_tecnici:
            df_tec = df_giorno[df_giorno['TEC_K'] == tec]
            chiave_cache = f"{data}_{tec}"
            
            if df_tec.empty:
                continue
            
            hash_attuale = genera_hash_dati(df_tec)
            hash_precedente = cache_hash.get(chiave_cache)
            dati_cached = cache_distanze.get(chiave_cache)
            
            if hash_precedente == hash_attuale and dati_cached:
                stats = dati_cached
                stats['da_cache'] = True
                report_completo['statistiche_cache']['da_cache'] += 1
                print(f"   📦 {tec} {data}: da cache ({stats['num_tappe']} tappe, {stats['distanza_totale_km']} km)")
            else:
                motivo = "nuovi dati" if not hash_precedente else "dati modificati"
                print(f"   🔄 {tec} {data}: ricalcolo ({motivo})...", end=" ")
                
                punti_list = df_tec.loc[:, ~df_tec.columns.duplicated()].to_dict('records')
                percorso_ordinato = ordina_per_fasce_e_vicinanza(punti_list, deposito)
                stats = calcola_distanze_percorso(percorso_ordinato, deposito)
                stats['da_cache'] = False
                
                cache_distanze[chiave_cache] = stats
                cache_hash[chiave_cache] = hash_attuale
                
                report_completo['statistiche_cache']['ricalcolati'] += 1
                print(f"✅ {stats['distanza_totale_km']} km")
            
            report_completo['per_data'][data]['tecnici'][tec] = stats
            
            report_completo['per_data'][data]['totali_giorno']['km'] += stats['distanza_totale_km']
            report_completo['per_data'][data]['totali_giorno']['ore_guida'] += stats['durata_guida_min'] / 60
            report_completo['per_data'][data]['totali_giorno']['contatori'] += stats['num_tappe']
            
            report_completo['totali_globali']['km_totali'] += stats['distanza_totale_km']
            report_completo['totali_globali']['ore_guida_totali'] += stats['durata_guida_min'] / 60
            report_completo['totali_globali']['contatori_totali'] += stats['num_tappe']
    
    return report_completo, cache_distanze, cache_hash


def stampa_report_distanze(report):
    """Stampa report formattato con info cache."""
    stats_cache = report.get('statistiche_cache', {})
    da_cache = stats_cache.get('da_cache', 0)
    ricalcolati = stats_cache.get('ricalcolati', 0)
    totale = da_cache + ricalcolati
    
    print("\n" + "="*70)
    print("📊 REPORT DISTANZE E TEMPI")
    print("="*70)
    print(f"Generato: {report['generato_il']}")
    
    if totale > 0:
        percentuale_cache = (da_cache / totale) * 100
        print(f"📦 Cache: {da_cache}/{totale} ({percentuale_cache:.0f}%) | 🔄 Ricalcolati: {ricalcolati}")
    
    for data, dati_giorno in report['per_data'].items():
        print(f"\n{'─'*70}")
        print(f"📅 {data}")
        print(f"{'─'*70}")
        
        for tec, stats in dati_giorno['tecnici'].items():
            ore = int(stats['durata_totale_min'] // 60)
            minuti = int(stats['durata_totale_min'] % 60)
            ore_guida = int(stats['durata_guida_min'] // 60)
            min_guida = int(stats['durata_guida_min'] % 60)
            
            cache_icon = "📦" if stats.get('da_cache') else "🔄"
            
            # In modalità generica, non mostrare "TUTTI" come nome tecnico
            nome_display = "Percorso" if tec == "TUTTI" else f"Tecnico: {tec}"
            
            print(f"\n   {cache_icon} {nome_display}")
            print(f"   ├── 📍 Tappe: {stats['num_tappe']}")
            print(f"   ├── 🚗 Distanza totale: {stats['distanza_totale_km']} km")
            print(f"   ├── ⏱️  Tempo guida: {ore_guida}h {min_guida}min")
            print(f"   ├── 🔧 Tempo interventi: {stats['durata_interventi_min']} min")
            print(f"   ├── ⏰ Tempo TOTALE: {ore}h {minuti}min")
            print(f"   └── 📐 Fonte: {stats['fonte_calcolo']}")
        
        totali = dati_giorno['totali_giorno']
        print(f"\n   📈 TOTALI: {totali['contatori']} contatori | {totali['km']:.1f} km | {totali['ore_guida']:.1f}h guida")
    
    glob = report['totali_globali']
    print(f"\n{'='*70}")
    print(f"📊 TOTALI GLOBALI: {glob['contatori_totali']} contatori | {glob['km_totali']:.1f} km | {glob['ore_guida_totali']:.1f}h")
    print("="*70)