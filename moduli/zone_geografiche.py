# -*- coding: utf-8 -*-
"""
Sistema di clustering geografico per identificare zone operative.
Genera un calendario per zona per facilitare la gestione appuntamenti.
"""
import math
import logging
import pandas as pd
from collections import defaultdict
from datetime import datetime

from config import DEPOSITO, PROVINCIA_ATTIVA

logger = logging.getLogger('mappa_algeco')


def calcola_distanza_km(lat1, lon1, lat2, lon2):
    """Calcola distanza in km tra due punti (Haversine)."""
    R = 6371
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))


def is_valid_coordinate(lat, lon):
    """
    Verifica se le coordinate sono valide per l'Italia.
    Lat: 35-48, Lon: 6-19 (con margine di sicurezza)
    """
    if lat is None or lon is None:
        return False
    try:
        lat_f = float(lat)
        lon_f = float(lon)
        return 35 <= lat_f <= 48 and 6 <= lon_f <= 19
    except (ValueError, TypeError):
        return False


def calcola_centroidi_comuni(df):
    """Calcola il centroide (punto medio) di ogni comune."""
    centroidi = {}
    
    for comune in df['COMUNE'].dropna().unique():
        if not comune or comune == '':
            continue
        df_comune = df[df['COMUNE'] == comune]
        # Filtra solo coordinate valide (non nulle E dentro i bounds Italia)
        df_valido = df_comune[
            (df_comune['Lat'].notna()) & 
            (df_comune['Lon'].notna()) &
            (df_comune['Lat'] >= 35) & (df_comune['Lat'] <= 48) &
            (df_comune['Lon'] >= 6) & (df_comune['Lon'] <= 19)
        ]
        
        if len(df_valido) > 0:
            lat_media = df_valido['Lat'].mean()
            lon_media = df_valido['Lon'].mean()
            
            # Verifica finale del centroide calcolato
            if is_valid_coordinate(lat_media, lon_media):
                centroidi[comune] = {
                    'lat': lat_media,
                    'lon': lon_media,
                    'num_contatori': len(df_valido)
                }
            else:
                logger.warning(f"⚠️ Centroide invalido per comune {comune}: lat={lat_media}, lon={lon_media}")
    
    return centroidi


def crea_cluster_zone_dbscan(centroidi, eps_km=10, min_samples=1):
    """
    Raggruppa i comuni in zone geografiche usando DBSCAN.

    DBSCAN (Density-Based Spatial Clustering) raggruppa i punti che sono
    vicini tra loro (entro eps_km) e identifica i punti isolati come rumore.

    Args:
        centroidi: dict {comune: {'lat', 'lon', 'num_contatori'}}
        eps_km: distanza massima (in km) per considerare due comuni nella stessa zona
        min_samples: numero minimo di comuni in un raggio eps per formare un cluster
                     (1 = ogni comune può formare una zona)

    Returns:
        zone: dict {zona_nome: {'comuni', 'centroide_lat', 'centroide_lon'}}
        assegnato: dict {comune: zona_nome}
    """
    if not centroidi:
        return {}, {}

    try:
        from sklearn.cluster import DBSCAN
        import numpy as np
    except ImportError:
        logger.warning("⚠️  scikit-learn non trovato. Installa con: pip install scikit-learn")
        logger.warning("   Utilizzo algoritmo a griglia di fallback...")
        return crea_cluster_zone_griglia(centroidi, dimensione_cella_km=8)

    # Prepara dati per DBSCAN
    # Ordina i comuni in modo deterministico per ottenere sempre gli stessi risultati
    comuni_list = sorted(centroidi.keys())
    coords = np.array([[centroidi[c]['lat'], centroidi[c]['lon']] for c in comuni_list])

    # Calcola matrice delle distanze in km
    # Usiamo una metrica custom per calcolare distanze reali in km
    n = len(coords)
    distance_matrix = np.zeros((n, n))

    for i in range(n):
        for j in range(i + 1, n):
            dist = calcola_distanza_km(
                coords[i][0], coords[i][1],
                coords[j][0], coords[j][1]
            )
            distance_matrix[i][j] = dist
            distance_matrix[j][i] = dist

    # DBSCAN con matrice precomputata
    # eps è già in km, quindi usiamo la matrice direttamente
    clustering = DBSCAN(eps=eps_km, min_samples=min_samples, metric='precomputed')
    labels = clustering.fit_predict(distance_matrix)

    # Converti risultati in zone
    zone = {}
    assegnato = {}

    # Raggruppa comuni per label
    clusters = defaultdict(list)
    for idx, label in enumerate(labels):
        comune = comuni_list[idx]
        if label == -1:
            # Punto rumore (isolato) - crea zona singola
            cluster_id = f"noise_{idx}"
        else:
            cluster_id = f"cluster_{label}"
        clusters[cluster_id].append(comune)

    # Crea zone
    for idx, (cluster_id, comuni_cluster) in enumerate(clusters.items()):
        zona_nome = f"ZONA_{idx + 1}"

        # Calcola centroide zona (pesato per numero contatori)
        lat_sum = 0
        lon_sum = 0
        peso_totale = 0

        for comune in comuni_cluster:
            peso = centroidi[comune]['num_contatori']
            lat_sum += centroidi[comune]['lat'] * peso
            lon_sum += centroidi[comune]['lon'] * peso
            peso_totale += peso

        zone[zona_nome] = {
            'comuni': comuni_cluster,
            'centroide_lat': lat_sum / peso_totale,
            'centroide_lon': lon_sum / peso_totale,
            'num_contatori_totali': peso_totale
        }

        for comune in comuni_cluster:
            assegnato[comune] = zona_nome

    return zone, assegnato


def crea_cluster_zone_griglia(centroidi, dimensione_cella_km=8):
    """
    Raggruppa i comuni in zone geografiche usando una griglia (algoritmo originale).

    Divide l'area in celle di dimensione_cella_km x dimensione_cella_km
    e raggruppa i comuni che cadono nella stessa cella.

    Questo è l'algoritmo di fallback se DBSCAN non è disponibile.
    """
    if not centroidi:
        return {}, {}

    # Converti km in gradi (approssimazione per latitudine italiana ~45°)
    # 1 grado lat ≈ 111 km, 1 grado lon ≈ 78 km (a lat 45°)
    delta_lat = dimensione_cella_km / 111.0
    delta_lon = dimensione_cella_km / 78.0

    # Trova bounds
    lats = [c['lat'] for c in centroidi.values()]
    lons = [c['lon'] for c in centroidi.values()]
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)

    # Assegna ogni comune a una cella
    celle = defaultdict(list)
    for comune, info in centroidi.items():
        # Calcola indice cella
        cella_lat = int((info['lat'] - min_lat) / delta_lat)
        cella_lon = int((info['lon'] - min_lon) / delta_lon)
        celle[(cella_lat, cella_lon)].append(comune)

    # Converti celle in zone
    zone = {}
    assegnato = {}

    for idx, ((cella_lat, cella_lon), comuni_cella) in enumerate(celle.items()):
        zona_nome = f"ZONA_{idx + 1}"

        # Calcola centroide zona
        lats_zona = [centroidi[c]['lat'] for c in comuni_cella]
        lons_zona = [centroidi[c]['lon'] for c in comuni_cella]
        peso_totale = sum(centroidi[c]['num_contatori'] for c in comuni_cella)

        zone[zona_nome] = {
            'comuni': comuni_cella,
            'centroide_lat': sum(lats_zona) / len(lats_zona),
            'centroide_lon': sum(lons_zona) / len(lons_zona),
            'cella': (cella_lat, cella_lon),
            'num_contatori_totali': peso_totale
        }

        for comune in comuni_cella:
            assegnato[comune] = zona_nome

    return zone, assegnato


def unisci_zone_piccole(zone, centroidi, min_contatori=15, max_distanza_km=20):
    """
    Post-processing: unisce zone troppo piccole con quelle vicine.

    Zone con meno di min_contatori vengono unite alla zona più vicina
    (grande o piccola). Le zone piccole vicine tra loro vengono raggruppate
    iterativamente finché non superano min_contatori o non ci sono più merge possibili.

    Args:
        zone: dict delle zone dal clustering
        centroidi: dict dei centroidi comuni
        min_contatori: soglia minima contatori per zona autonoma
        max_distanza_km: distanza massima per considerare un merge

    Returns:
        zone_ottimizzate: dict con zone unite
        assegnato_nuovo: dict aggiornato comune->zona
    """
    if not zone:
        return {}, {}

    def _conta_contatori(zona_info):
        n = zona_info.get('num_contatori_totali', 0)
        if n == 0:
            n = sum(centroidi[c]['num_contatori'] for c in zona_info['comuni'] if c in centroidi)
        return n

    def _ricalcola_centroide(comuni_list):
        lat_sum = lon_sum = peso_totale = 0
        for comune in comuni_list:
            if comune in centroidi:
                peso = max(centroidi[comune]['num_contatori'], 1)
                lat_sum += centroidi[comune]['lat'] * peso
                lon_sum += centroidi[comune]['lon'] * peso
                peso_totale += peso
        if peso_totale > 0:
            return lat_sum / peso_totale, lon_sum / peso_totale
        return None, None

    # Copia lavorativa delle zone
    zone_lavoro = {}
    for nome, info in zone.items():
        zone_lavoro[nome] = {
            'comuni': info['comuni'].copy(),
            'centroide_lat': info['centroide_lat'],
            'centroide_lon': info['centroide_lon'],
            'num_contatori_totali': _conta_contatori(info)
        }

    # Merge iterativo: continua finché ci sono merge possibili
    modificato = True
    while modificato:
        modificato = False

        # Trova tutte le zone piccole
        zone_piccole = [n for n, i in zone_lavoro.items() if i['num_contatori_totali'] < min_contatori]
        if not zone_piccole:
            break

        # Per ogni zona piccola, trova la zona più vicina (grande O piccola)
        miglior_merge = None  # (zona_piccola, zona_target, distanza)
        min_dist_trovata = float('inf')

        for nome_piccola in zone_piccole:
            info_piccola = zone_lavoro[nome_piccola]

            for nome_altra, info_altra in zone_lavoro.items():
                if nome_altra == nome_piccola:
                    continue

                dist = calcola_distanza_km(
                    info_piccola['centroide_lat'], info_piccola['centroide_lon'],
                    info_altra['centroide_lat'], info_altra['centroide_lon']
                )

                if dist <= max_distanza_km and dist < min_dist_trovata:
                    min_dist_trovata = dist
                    miglior_merge = (nome_piccola, nome_altra, dist)

        if miglior_merge:
            nome_piccola, nome_target, dist = miglior_merge
            info_piccola = zone_lavoro[nome_piccola]
            info_target = zone_lavoro[nome_target]

            # Esegui merge
            comuni_uniti = info_target['comuni'] + info_piccola['comuni']
            lat_new, lon_new = _ricalcola_centroide(comuni_uniti)
            cont_new = info_target['num_contatori_totali'] + info_piccola['num_contatori_totali']

            zone_lavoro[nome_target] = {
                'comuni': comuni_uniti,
                'centroide_lat': lat_new if lat_new else info_target['centroide_lat'],
                'centroide_lon': lon_new if lon_new else info_target['centroide_lon'],
                'num_contatori_totali': cont_new
            }
            del zone_lavoro[nome_piccola]
            modificato = True

            logger.info(f"   🔀 Unisco {nome_piccola} ({info_piccola['num_contatori_totali']} cont.) "
                        f"con {nome_target} (dist: {dist:.1f} km)")

    # Fase 2: Raggruppa zone a singolo comune vicine tra loro
    # Zone con 1 solo comune che distano meno di max_distanza_singoli_km vengono unite
    max_distanza_singoli_km = 10
    modificato = True
    while modificato:
        modificato = False

        # Trova zone a singolo comune
        zone_singole = [n for n, i in zone_lavoro.items() if len(i['comuni']) == 1]
        if len(zone_singole) < 2:
            break

        # Trova la coppia di zone singole più vicina
        miglior_merge = None
        min_dist_trovata = float('inf')

        for i_idx in range(len(zone_singole)):
            for j_idx in range(i_idx + 1, len(zone_singole)):
                nome_a = zone_singole[i_idx]
                nome_b = zone_singole[j_idx]
                info_a = zone_lavoro[nome_a]
                info_b = zone_lavoro[nome_b]

                dist = calcola_distanza_km(
                    info_a['centroide_lat'], info_a['centroide_lon'],
                    info_b['centroide_lat'], info_b['centroide_lon']
                )

                if dist <= max_distanza_singoli_km and dist < min_dist_trovata:
                    min_dist_trovata = dist
                    miglior_merge = (nome_a, nome_b, dist)

        if miglior_merge:
            nome_a, nome_b, dist = miglior_merge
            info_a = zone_lavoro[nome_a]
            info_b = zone_lavoro[nome_b]

            comuni_uniti = info_a['comuni'] + info_b['comuni']
            lat_new, lon_new = _ricalcola_centroide(comuni_uniti)
            cont_new = info_a['num_contatori_totali'] + info_b['num_contatori_totali']

            zone_lavoro[nome_a] = {
                'comuni': comuni_uniti,
                'centroide_lat': lat_new if lat_new else info_a['centroide_lat'],
                'centroide_lon': lon_new if lon_new else info_a['centroide_lon'],
                'num_contatori_totali': cont_new
            }
            del zone_lavoro[nome_b]
            modificato = True

            logger.info(f"   🔀 Raggruppo singoli: {nome_a} + {nome_b} (dist: {dist:.1f} km)")

    # Crea mapping comune->zona aggiornato
    assegnato_nuovo = {}
    for zona_nome, zona_info in zone_lavoro.items():
        for comune in zona_info['comuni']:
            assegnato_nuovo[comune] = zona_nome

    return zone_lavoro, assegnato_nuovo


def dividi_zone_grandi(zone, centroidi, max_comuni=25, max_rapporto_comuni_contatori=5.0, max_diametro_km=15):
    """
    Post-processing: divide zone troppo grandi in sotto-zone più bilanciate.

    DBSCAN tende a concatenare transitivamente comuni vicini, creando zone
    che coprono aree enormi. Questa funzione le spezza usando bisection
    geografica ricorsiva (mediana lat/lon).

    Una zona viene divisa se:
    - Ha più di max_comuni comuni, OPPURE
    - Il rapporto comuni/contatori è troppo alto, OPPURE
    - Il diametro geografico (distanza max tra 2 comuni) supera max_diametro_km

    Args:
        zone: dict delle zone dal clustering
        centroidi: dict dei centroidi comuni
        max_comuni: numero massimo di comuni per zona (default 25)
        max_rapporto_comuni_contatori: rapporto max (default 5.0)
        max_diametro_km: diametro massimo della zona in km (default 15)

    Returns:
        zone_divise: dict con zone splittate
        assegnato_nuovo: dict aggiornato comune->zona
    """
    if not zone:
        return {}, {}

    zone_divise = {}
    contatore_nuove_zone = [0]  # mutable per closure

    def _diametro_zona(comuni_lista):
        """Calcola il diametro (distanza max tra 2 comuni qualsiasi) in km."""
        coords = [(centroidi[c]['lat'], centroidi[c]['lon']) for c in comuni_lista if c in centroidi]
        if len(coords) <= 1:
            return 0
        max_dist = 0
        for i in range(len(coords)):
            for j in range(i + 1, len(coords)):
                d = calcola_distanza_km(coords[i][0], coords[i][1], coords[j][0], coords[j][1])
                if d > max_dist:
                    max_dist = d
        return max_dist

    def _deve_dividere(zona_info):
        """Determina se una zona deve essere divisa."""
        n_comuni = len(zona_info['comuni'])
        n_contatori = zona_info.get('num_contatori_totali', 0)
        if n_contatori == 0:
            n_contatori = sum(centroidi[c]['num_contatori'] for c in zona_info['comuni'] if c in centroidi)

        if n_comuni <= 3:
            return False
        if n_comuni > max_comuni:
            return True
        # Rapporto: tanti comuni con pochi contatori → split
        if n_contatori > 0 and n_comuni / n_contatori > max_rapporto_comuni_contatori:
            return True
        # Diametro: zona troppo estesa geograficamente → split
        if n_comuni > 4 and _diametro_zona(zona_info['comuni']) > max_diametro_km:
            return True
        return False

    def _biseziona(comuni_lista):
        """Divide una lista di comuni in due gruppi geografici usando la mediana."""
        if len(comuni_lista) <= 2:
            return [comuni_lista]

        # Calcola spread lat vs lon per scegliere l'asse di split migliore
        lats = [centroidi[c]['lat'] for c in comuni_lista if c in centroidi]
        lons = [centroidi[c]['lon'] for c in comuni_lista if c in centroidi]

        if not lats or not lons:
            return [comuni_lista]

        spread_lat = max(lats) - min(lats)
        spread_lon = max(lons) - min(lons)

        # Split lungo l'asse con maggiore dispersione
        if spread_lat >= spread_lon:
            comuni_ordinati = sorted(comuni_lista, key=lambda c: centroidi[c]['lat'] if c in centroidi else 0)
        else:
            comuni_ordinati = sorted(comuni_lista, key=lambda c: centroidi[c]['lon'] if c in centroidi else 0)

        mid = len(comuni_ordinati) // 2
        return [comuni_ordinati[:mid], comuni_ordinati[mid:]]

    def _split_ricorsivo(comuni_lista, profondita=0):
        """Divide ricorsivamente finché ogni sotto-zona è sotto soglia."""
        if profondita > 5:  # safety: max 5 livelli (32 sotto-zone)
            return [comuni_lista]

        n_comuni = len(comuni_lista)
        n_contatori = sum(centroidi[c]['num_contatori'] for c in comuni_lista if c in centroidi)

        troppi_comuni = n_comuni > max_comuni
        rapporto_alto = n_contatori > 0 and n_comuni / n_contatori > max_rapporto_comuni_contatori and n_comuni > 3
        troppo_estesa = n_comuni > 4 and _diametro_zona(comuni_lista) > max_diametro_km

        if not troppi_comuni and not rapporto_alto and not troppo_estesa:
            return [comuni_lista]

        parti = _biseziona(comuni_lista)
        if len(parti) == 1:
            return parti

        risultato = []
        for parte in parti:
            risultato.extend(_split_ricorsivo(parte, profondita + 1))
        return risultato

    for zona_nome, zona_info in zone.items():
        if not _deve_dividere(zona_info):
            zone_divise[zona_nome] = zona_info.copy()
            continue

        n_originali = len(zona_info['comuni'])
        sotto_zone = _split_ricorsivo(zona_info['comuni'])

        logger.info(f"   ✂️  Divido {zona_nome} ({n_originali} comuni) in {len(sotto_zone)} sotto-zone")

        for sotto_zona_comuni in sotto_zone:
            contatore_nuove_zone[0] += 1
            nuovo_nome = f"ZONA_S{contatore_nuove_zone[0]}"

            # Calcola centroide pesato
            lat_sum = 0
            lon_sum = 0
            peso_totale = 0
            for comune in sotto_zona_comuni:
                if comune in centroidi:
                    peso = centroidi[comune]['num_contatori']
                    lat_sum += centroidi[comune]['lat'] * peso
                    lon_sum += centroidi[comune]['lon'] * peso
                    peso_totale += peso

            if peso_totale > 0:
                zone_divise[nuovo_nome] = {
                    'comuni': sotto_zona_comuni,
                    'centroide_lat': lat_sum / peso_totale,
                    'centroide_lon': lon_sum / peso_totale,
                    'num_contatori_totali': peso_totale
                }
            else:
                # Fallback: media semplice
                lats = [centroidi[c]['lat'] for c in sotto_zona_comuni if c in centroidi]
                lons = [centroidi[c]['lon'] for c in sotto_zona_comuni if c in centroidi]
                if lats and lons:
                    zone_divise[nuovo_nome] = {
                        'comuni': sotto_zona_comuni,
                        'centroide_lat': sum(lats) / len(lats),
                        'centroide_lon': sum(lons) / len(lons),
                        'num_contatori_totali': 0
                    }

    # Ricostruisci mapping comune->zona
    assegnato_nuovo = {}
    for zona_nome, zona_info in zone_divise.items():
        for comune in zona_info['comuni']:
            assegnato_nuovo[comune] = zona_nome

    return zone_divise, assegnato_nuovo


def crea_cluster_zone(centroidi, dimensione_cella_km=8, min_contatori_zona=15):
    """
    Wrapper che crea zone ottimizzate usando DBSCAN + post-processing.

    1. Usa DBSCAN per clustering basato sulla densità geografica
    2. Divide zone troppo grandi (troppi comuni, pochi contatori)
    3. Unisce zone troppo piccole con quelle vicine

    Args:
        centroidi: dict dei centroidi comuni
        dimensione_cella_km: parametro per calcolare eps (distanza massima cluster)
        min_contatori_zona: minimo contatori per zona autonoma (default 15)

    Returns:
        zone_ottimizzate: dict delle zone finali
        assegnato: dict comune->zona
    """
    # Step 1: Clustering DBSCAN
    # eps_km basato su dimensione_cella_km: zona media = 15-20 km diameter
    eps_km = dimensione_cella_km * 0.625  # ~10km per default di 16km, o 5km per 8km
    zone_iniziali, _ = crea_cluster_zone_dbscan(centroidi, eps_km=eps_km, min_samples=1)

    logger.info(f"   🗺️  DBSCAN: {len(zone_iniziali)} zone iniziali (eps={eps_km:.1f} km)")

    # Step 2: Dividi zone troppo grandi (troppi comuni, estensione eccessiva)
    zone_divise, _ = dividi_zone_grandi(
        zone_iniziali,
        centroidi,
        max_comuni=12,
        max_rapporto_comuni_contatori=0.15,
        max_diametro_km=15
    )

    logger.info(f"   ✂️  Dopo split: {len(zone_divise)} zone")

    # Step 3: Post-processing - unisci zone piccole
    max_distanza_merge = dimensione_cella_km * 2.5  # ~20km per default
    zone_ottimizzate, assegnato = unisci_zone_piccole(
        zone_divise,
        centroidi,
        min_contatori=min_contatori_zona,
        max_distanza_km=max_distanza_merge
    )

    logger.info(f"   🔀 Dopo merge: {len(zone_ottimizzate)} zone")

    # Step 4: Re-split zone che il merge ha reso troppo grandi
    zone_finali, assegnato = dividi_zone_grandi(
        zone_ottimizzate,
        centroidi,
        max_comuni=12,
        max_rapporto_comuni_contatori=999,
        max_diametro_km=15
    )

    logger.info(f"   ✅ Ottimizzazione: {len(zone_finali)} zone finali")

    return zone_finali, assegnato


def assegna_nomi_zone(zone, centroidi, deposito):
    """
    Assegna nomi più descrittivi e intuitivi alle zone basandosi sulla posizione.
    Usa 8 direzioni cardinali (N, NE, E, SE, S, SW, W, NW) + CENTRO,
    calcolate rispetto al centro geografico dell'area servita (non il deposito)
    per ottenere direzioni coerenti con la percezione geografica.
    """

    def _calcola_direzione(dlat, dlon, dist):
        """Calcola la direzione cardinale a 8 punti basandosi sull'angolo."""
        if dist < 5:
            return "CENTRO"

        # Angolo in gradi: 0=Nord, 90=Est, 180=Sud, 270=Ovest
        angolo = math.degrees(math.atan2(dlon, dlat)) % 360

        # 8 settori da 45° ciascuno, centrati sulla direzione
        settori = [
            (337.5, 360.0, "NORD"), (0.0, 22.5, "NORD"),
            (22.5, 67.5, "NORD-EST"),
            (67.5, 112.5, "EST"),
            (112.5, 157.5, "SUD-EST"),
            (157.5, 202.5, "SUD"),
            (202.5, 247.5, "SUD-OVEST"),
            (247.5, 292.5, "OVEST"),
            (292.5, 337.5, "NORD-OVEST"),
        ]

        for ang_min, ang_max, nome in settori:
            if ang_min <= angolo < ang_max:
                return nome

        return "NORD"  # fallback

    # Usa il capoluogo di provincia come punto di riferimento per le direzioni
    # Così "SUD" = a sud di Torino/Pavia, che corrisponde alla percezione geografica
    CAPOLUOGHI = {
        'TORINO': (45.0703, 7.6869),   # Piazza Castello, Torino
        'PAVIA':  (45.1847, 9.1582),   # Centro, Pavia
    }
    centro_lat, centro_lon = CAPOLUOGHI.get(PROVINCIA_ATTIVA, (deposito['Lat'], deposito['Lon']))

    logger.info(f"   📍 Riferimento direzioni (capoluogo {PROVINCIA_ATTIVA}): lat={centro_lat:.4f}, lon={centro_lon:.4f}")

    # Calcola distanza e direzione per ogni zona
    zone_con_distanza = []
    for zona_nome, zona_info in zone.items():
        # Distanza dal deposito (operativa, per i percorsi)
        dist_deposito = calcola_distanza_km(
            deposito['Lat'], deposito['Lon'],
            zona_info['centroide_lat'], zona_info['centroide_lon']
        )

        # Direzione dal centro geografico (per i nomi delle zone)
        dist_centro = calcola_distanza_km(
            centro_lat, centro_lon,
            zona_info['centroide_lat'], zona_info['centroide_lon']
        )
        dlat = zona_info['centroide_lat'] - centro_lat
        dlon = zona_info['centroide_lon'] - centro_lon
        direzione = _calcola_direzione(dlat, dlon, dist_centro)

        # Trova il comune principale (quello con più contatori)
        comune_principale = max(
            zona_info['comuni'],
            key=lambda c: centroidi[c]['num_contatori']
        )

        # Conta contatori totali nella zona
        num_contatori_zona = sum(centroidi[c]['num_contatori'] for c in zona_info['comuni'])

        zone_con_distanza.append({
            'zona_id': zona_nome,
            'distanza': dist_deposito,
            'distanza_centro': dist_centro,
            'direzione': direzione,
            'comune_principale': comune_principale,
            'comuni': zona_info['comuni'],
            'centroide_lat': zona_info['centroide_lat'],
            'centroide_lon': zona_info['centroide_lon'],
            'num_contatori': num_contatori_zona,
            'num_comuni': len(zona_info['comuni'])
        })

    # Ordina per distanza dal deposito
    zone_con_distanza.sort(key=lambda x: x['distanza'])

    # Raggruppa per direzione per gestire duplicati
    zone_per_direzione = defaultdict(list)
    for z in zone_con_distanza:
        zone_per_direzione[z['direzione']].append(z)

    # Ordina ogni gruppo per distanza
    for dir_name in zone_per_direzione:
        zone_per_direzione[dir_name].sort(key=lambda x: x['distanza'])

    # Assegna nomi
    zone_rinominate = {}

    for z in zone_con_distanza:
        dir_base = z['direzione']
        gruppo = zone_per_direzione[dir_base]

        # Genera nome
        if len(z['comuni']) == 1:
            # Zona singola: nome semplice con direzione
            nome_nuovo = z['comune_principale'].upper()
        elif z['num_comuni'] <= 2:
            # 2 comuni: mostra entrambi
            comuni_ordinati = sorted(z['comuni'], key=lambda c: -centroidi[c]['num_contatori'])
            nome_nuovo = f"{dir_base} - {' / '.join([c.title() for c in comuni_ordinati])}"
        else:
            comune_title = z['comune_principale'].title()
            nome_nuovo = f"{dir_base} - {comune_title} e dintorni ({z['num_comuni']} comuni)"

        zone_rinominate[nome_nuovo] = {
            'comuni': z['comuni'],
            'distanza_deposito': round(z['distanza'], 1),
            'direzione': dir_base,
            'centroide_lat': z['centroide_lat'],
            'centroide_lon': z['centroide_lon'],
            'num_contatori': z['num_contatori'],
            'num_comuni': z['num_comuni']
        }

    return zone_rinominate


def genera_calendario_zone(df, zone, comune_to_zona):
    """
    Genera il calendario per ogni zona: quali date e quanti contatori.
    """
    calendario = {}
    
    for zona_nome, zona_info in zone.items():
        calendario[zona_nome] = {
            'info': zona_info,
            'date': {},
            'totale_contatori': 0,
            'operatori': set()
        }
    
    # Raggruppa per zona e data
    for _, row in df.iterrows():
        comune = row.get('COMUNE', '')
        data = row.get('DATA_K', '')
        tecnico = row.get('TEC_K', '')
        
        if not comune or not data or data == 'DATA_MANCANTE':
            continue
        
        zona = comune_to_zona.get(comune)
        if not zona:
            continue
        
        if data not in calendario[zona]['date']:
            calendario[zona]['date'][data] = {
                'contatori': 0,
                'appuntamenti': 0,
                'operatori': set()
            }
        
        calendario[zona]['date'][data]['contatori'] += 1
        calendario[zona]['totale_contatori'] += 1
        
        if row.get('IS_APPUNTAMENTO'):
            calendario[zona]['date'][data]['appuntamenti'] += 1
        
        if tecnico and tecnico != 'TUTTI':
            calendario[zona]['date'][data]['operatori'].add(tecnico)
            calendario[zona]['operatori'].add(tecnico)
    
    return calendario


def genera_report_zone(df, filename=None):
    """
    Genera il report completo delle zone geografiche.
    
    Returns:
        dict con tutte le info zone e calendario
    """
    logger.info("=" * 60)
    logger.info("🗺️  ANALISI ZONE GEOGRAFICHE")
    logger.info("=" * 60)
    
    # Filtra solo righe con coordinate valide
    df_valido = df[(df['Lat'].notna()) & (df['Lon'].notna()) & (df['COMUNE'].notna())].copy()
    
    if df_valido.empty:
        logger.warning("⚠️  Nessun dato con coordinate valide")
        return None
    
    # 1. Calcola centroidi comuni
    centroidi = calcola_centroidi_comuni(df_valido)
    logger.info(f"📍 Comuni identificati: {len(centroidi)}")

    # 2. Crea cluster ottimizzato (DBSCAN + merge zone piccole)
    # dimensione_cella_km controlla la granularità:
    # - 10 km = ~4-5 zone molto ampie
    # - 8 km = ~6-8 zone ampie
    # - 5 km = ~10-12 zone medie
    # - 4 km = ~15-20 zone specifiche
    zone, comune_to_zona = crea_cluster_zone(centroidi, dimensione_cella_km=8, min_contatori_zona=10)
    logger.info(f"🗺️  Zone create: {len(zone)}")
    
    # 3. Assegna nomi descrittivi
    zone = assegna_nomi_zone(zone, centroidi, DEPOSITO)
    
    # Aggiorna mapping comune->zona con nuovi nomi
    comune_to_zona_nuovo = {}
    for zona_nome, zona_info in zone.items():
        for comune in zona_info['comuni']:
            comune_to_zona_nuovo[comune] = zona_nome
    
    # 4. Genera calendario
    calendario = genera_calendario_zone(df_valido, zone, comune_to_zona_nuovo)
    
    # 5. Stampa riepilogo
    logger.info("-" * 60)
    logger.info("📅 CALENDARIO PER ZONA")
    logger.info("-" * 60)
    
    for zona_nome in sorted(calendario.keys(), key=lambda z: -calendario[z]['totale_contatori']):
        zona_cal = calendario[zona_nome]
        if zona_cal['totale_contatori'] == 0:
            continue
        
        info = zona_cal['info']
        comuni_list = ", ".join(sorted(info['comuni']))
        
        logger.info(f"🏘️  {zona_nome}")
        logger.info(f"   Comuni: {comuni_list}")
        logger.info(f"   Distanza deposito: {info['distanza_deposito']} km ({info['direzione']})")
        logger.info(f"   Totale contatori: {zona_cal['totale_contatori']}")
        
        if zona_cal['date']:
            logger.info("   Date pianificate:")
            for data in sorted(zona_cal['date'].keys()):
                info_data = zona_cal['date'][data]
                ops = ", ".join(info_data['operatori']) if info_data['operatori'] else "N/A"
                app_str = f" (📞 {info_data['appuntamenti']} app)" if info_data['appuntamenti'] > 0 else ""
                logger.info(f"      📅 {data}: {info_data['contatori']} contatori{app_str} - {ops}")
    
    # 6. Genera file se richiesto
    if filename:
        # Genera HTML interattivo
        html_filename = filename.replace('.xlsx', '.html').replace('.xls', '.html')
        if not html_filename.endswith('.html'):
            html_filename += '.html'
        genera_html_interattivo(df_valido, html_filename, calendario, zone, comune_to_zona_nuovo, centroidi, DEPOSITO)
    
    return {
        'zone': zone,
        'calendario': calendario,
        'comune_to_zona': comune_to_zona_nuovo,
        'centroidi': centroidi
    }


def _genera_excel_zone(calendario, zone, filename):
    """Genera file Excel con calendario zone."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils.dataframe import dataframe_to_rows
        
        wb = Workbook()
        
        # ============================================================
        # FOGLIO 1: Riepilogo Zone
        # ============================================================
        ws1 = wb.active
        ws1.title = "Riepilogo Zone"
        
        headers = ["Zona", "Comuni", "Distanza Deposito (km)", "Direzione", "Tot. Contatori", "Date Attive"]
        for col, h in enumerate(headers, 1):
            cell = ws1.cell(row=1, column=col, value=h)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill(start_color="1F4E79", fill_type="solid")
            cell.alignment = Alignment(horizontal="center")
        
        row = 2
        for zona_nome in sorted(calendario.keys(), key=lambda z: -calendario[z]['totale_contatori']):
            zona_cal = calendario[zona_nome]
            if zona_cal['totale_contatori'] == 0:
                continue
            
            info = zona_cal['info']
            ws1.cell(row=row, column=1, value=zona_nome)
            ws1.cell(row=row, column=2, value=", ".join(sorted(info['comuni'])))
            ws1.cell(row=row, column=3, value=info['distanza_deposito'])
            ws1.cell(row=row, column=4, value=info['direzione'])
            ws1.cell(row=row, column=5, value=zona_cal['totale_contatori'])
            ws1.cell(row=row, column=6, value=len(zona_cal['date']))
            row += 1
        
        # Larghezza colonne
        ws1.column_dimensions['A'].width = 35
        ws1.column_dimensions['B'].width = 50
        ws1.column_dimensions['C'].width = 20
        ws1.column_dimensions['D'].width = 12
        ws1.column_dimensions['E'].width = 15
        ws1.column_dimensions['F'].width = 12
        
        # ============================================================
        # FOGLIO 2: Calendario Dettagliato
        # ============================================================
        ws2 = wb.create_sheet("Calendario Zone")
        
        headers2 = ["Data", "Zona", "Contatori", "Appuntamenti", "Operatori", "Comuni"]
        for col, h in enumerate(headers2, 1):
            cell = ws2.cell(row=1, column=col, value=h)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill(start_color="1F4E79", fill_type="solid")
        
        # Raccogli tutte le righe data-zona
        righe = []
        for zona_nome, zona_cal in calendario.items():
            info = zona_cal['info']
            for data, data_info in zona_cal['date'].items():
                righe.append({
                    'data': data,
                    'zona': zona_nome,
                    'contatori': data_info['contatori'],
                    'appuntamenti': data_info['appuntamenti'],
                    'operatori': ", ".join(data_info['operatori']) if data_info['operatori'] else "N/A",
                    'comuni': ", ".join(sorted(info['comuni']))
                })
        
        # Ordina per data
        righe.sort(key=lambda x: x['data'])
        
        row = 2
        data_precedente = None
        for r in righe:
            # Colora righe alternate per data
            if r['data'] != data_precedente:
                fill_color = "E6F2FF" if row % 4 < 2 else "FFFFFF"
                data_precedente = r['data']
            
            ws2.cell(row=row, column=1, value=r['data'])
            ws2.cell(row=row, column=2, value=r['zona'])
            ws2.cell(row=row, column=3, value=r['contatori'])
            ws2.cell(row=row, column=4, value=r['appuntamenti'])
            ws2.cell(row=row, column=5, value=r['operatori'])
            ws2.cell(row=row, column=6, value=r['comuni'])
            row += 1
        
        ws2.column_dimensions['A'].width = 12
        ws2.column_dimensions['B'].width = 35
        ws2.column_dimensions['C'].width = 12
        ws2.column_dimensions['D'].width = 14
        ws2.column_dimensions['E'].width = 20
        ws2.column_dimensions['F'].width = 50
        
        # ============================================================
        # FOGLIO 3: Ricerca Comune
        # ============================================================
        ws3 = wb.create_sheet("Cerca Comune")
        
        headers3 = ["Comune", "Zona", "Date Pianificate", "Tot. Contatori Zona"]
        for col, h in enumerate(headers3, 1):
            cell = ws3.cell(row=1, column=col, value=h)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill(start_color="1F4E79", fill_type="solid")
        
        row = 2
        for zona_nome, zona_cal in sorted(calendario.items()):
            info = zona_cal['info']
            date_str = ", ".join(sorted(zona_cal['date'].keys()))
            
            for comune in sorted(info['comuni']):
                ws3.cell(row=row, column=1, value=comune)
                ws3.cell(row=row, column=2, value=zona_nome)
                ws3.cell(row=row, column=3, value=date_str)
                ws3.cell(row=row, column=4, value=zona_cal['totale_contatori'])
                row += 1
        
        ws3.column_dimensions['A'].width = 25
        ws3.column_dimensions['B'].width = 35
        ws3.column_dimensions['C'].width = 60
        ws3.column_dimensions['D'].width = 20
        
        # Salva
        wb.save(filename)
        logger.info(f"✅ Report zone salvato: {filename}")
        
    except Exception as e:
        logger.warning(f"⚠️  Errore generazione Excel zone: {e}")


def genera_html_interattivo(df, filename, calendario, zone, comune_to_zona, centroidi, deposito):
    """Genera file HTML interattivo con mappa e ricerca."""
    
    # Colori per le zone
    colori = [
        '#e6194b', '#3cb44b', '#ffe119', '#4363d8', '#f58231', 
        '#911eb4', '#46f0f0', '#f032e6', '#bcf60c', '#fabebe',
        '#008080', '#e6beff', '#9a6324', '#fffac8', '#800000',
        '#aaffc3', '#808000', '#ffd8b1', '#000075', '#808080'
    ]
    
    zona_colori = {}
    for idx, zona_nome in enumerate(zone.keys()):
        zona_colori[zona_nome] = colori[idx % len(colori)]
    
    # Prepara dati JSON per JavaScript
    zone_json = []
    for zona_nome, zona_info in zone.items():
        date_info = []
        if zona_nome in calendario:
            for data, d_info in sorted(calendario[zona_nome]['date'].items()):
                date_info.append({
                    'data': data,
                    'contatori': d_info['contatori'],
                    'appuntamenti': d_info['appuntamenti'],
                    'operatori': list(d_info['operatori'])
                })
        
        zone_json.append({
            'nome': zona_nome,
            'comuni': zona_info['comuni'],
            'lat': zona_info['centroide_lat'],
            'lon': zona_info['centroide_lon'],
            'distanza': zona_info['distanza_deposito'],
            'direzione': zona_info['direzione'],
            'colore': zona_colori[zona_nome],
            'date': date_info,
            'totale': calendario.get(zona_nome, {}).get('totale_contatori', 0)
        })
    
    # Prepara comuni per autocomplete
    comuni_json = []
    for comune, zona in comune_to_zona.items():
        if comune in centroidi:
            comuni_json.append({
                'nome': comune,
                'zona': zona,
                'lat': centroidi[comune]['lat'],
                'lon': centroidi[comune]['lon']
            })
    
    # Converti in JSON string
    import json
    zone_json_str = json.dumps(zone_json, ensure_ascii=False)
    comuni_json_str = json.dumps(comuni_json, ensure_ascii=False)
    
    html = f'''<!DOCTYPE html>
<html lang="it">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="icon" type="image/svg+xml" href="/favicon.svg?v=2">
    <title>Zone Geografiche - {PROVINCIA_ATTIVA}</title>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #f5f5f5; }}
        
        .container {{ display: flex; height: 100vh; }}
        
        .sidebar {{
            width: 400px;
            background: white;
            box-shadow: 2px 0 10px rgba(0,0,0,0.1);
            display: flex;
            flex-direction: column;
            z-index: 1000;
        }}
        
        .header {{
            background: linear-gradient(135deg, #1a5276 0%, #2980b9 100%);
            color: white;
            padding: 20px;
            text-align: center;
        }}
        
        .header h1 {{ font-size: 1.5em; margin-bottom: 5px; }}
        .header p {{ opacity: 0.9; font-size: 0.9em; }}
        
        .search-box {{
            padding: 15px;
            background: #f8f9fa;
            border-bottom: 1px solid #ddd;
        }}
        
        .search-box input {{
            width: 100%;
            padding: 12px 15px;
            border: 2px solid #ddd;
            border-radius: 25px;
            font-size: 16px;
            outline: none;
            transition: border-color 0.3s;
        }}
        
        .search-box input:focus {{ border-color: #2980b9; }}
        
        .results {{
            flex: 1;
            overflow-y: auto;
            padding: 10px;
        }}
        
        .result-card {{
            background: white;
            border-radius: 10px;
            padding: 15px;
            margin-bottom: 10px;
            box-shadow: 0 2px 5px rgba(0,0,0,0.1);
            cursor: pointer;
            transition: transform 0.2s, box-shadow 0.2s;
            border-left: 4px solid #ddd;
        }}
        
        .result-card:hover {{
            transform: translateX(5px);
            box-shadow: 0 4px 10px rgba(0,0,0,0.15);
        }}
        
        .result-card.selected {{
            border-left-color: #2980b9;
            background: #f0f7ff;
        }}
        
        .zona-nome {{
            font-weight: bold;
            font-size: 1.1em;
            color: #333;
            margin-bottom: 5px;
        }}
        
        .zona-comuni {{
            font-size: 0.85em;
            color: #666;
            margin-bottom: 10px;
        }}
        
        .zona-stats {{
            display: flex;
            gap: 15px;
            font-size: 0.85em;
            color: #888;
            margin-bottom: 10px;
        }}
        
        .date-list {{
            background: #f8f9fa;
            border-radius: 8px;
            padding: 10px;
            margin-top: 10px;
        }}
        
        .date-item {{
            display: flex;
            justify-content: space-between;
            padding: 8px 0;
            border-bottom: 1px solid #eee;
            font-size: 0.9em;
        }}
        
        .date-item:last-child {{ border-bottom: none; }}
        
        .date-item .data {{ font-weight: bold; color: #2980b9; }}
        .date-item .info {{ color: #666; }}
        .date-item .app {{ color: #e74c3c; font-weight: bold; }}
        
        #map {{ flex: 1; }}
        
        .legend {{
            background: white;
            padding: 10px 15px;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.2);
            font-size: 12px;
            max-height: 200px;
            overflow-y: auto;
        }}
        
        .legend-title {{ font-weight: bold; margin-bottom: 8px; }}
        .legend-item {{ display: flex; align-items: center; margin: 4px 0; }}
        .legend-color {{ width: 16px; height: 16px; border-radius: 3px; margin-right: 8px; }}
        
        .no-results {{
            text-align: center;
            padding: 40px;
            color: #888;
        }}
        
        .deposito-marker {{
            background: #e74c3c;
            border: 3px solid white;
            border-radius: 50%;
            width: 20px;
            height: 20px;
        }}
        
        @media (max-width: 768px) {{
            .container {{ flex-direction: column; }}
            .sidebar {{ width: 100%; height: 50%; }}
            #map {{ height: 50%; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="sidebar">
            <div class="header">
                <h1>🗺️ Zone Geografiche</h1>
                <p>{PROVINCIA_ATTIVA} - Cerca un comune per vedere le date disponibili</p>
            </div>
            
            <div class="search-box">
                <input type="text" id="searchInput" placeholder="🔍 Cerca comune..." autocomplete="off">
            </div>
            
            <div class="results" id="results">
                <!-- Risultati dinamici -->
            </div>
        </div>
        
        <div id="map"></div>
    </div>
    
    <script>
        // Dati
        const zone = {zone_json_str};
        const comuni = {comuni_json_str};
        const deposito = {{ lat: {deposito['Lat']}, lon: {deposito['Lon']}, nome: "{deposito['Nome']}" }};
        
        // Mappa
        const map = L.map('map').setView([deposito.lat, deposito.lon], 10);
        
        L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
            attribution: '© OpenStreetMap'
        }}).addTo(map);
        
        // Deposito
        const depositoIcon = L.divIcon({{
            className: 'deposito-marker',
            iconSize: [20, 20]
        }});
        L.marker([deposito.lat, deposito.lon], {{ icon: depositoIcon }})
            .bindPopup(`<b>🏠 ${{deposito.nome}}</b>`)
            .addTo(map);
        
        // Layer zone
        const zoneMarkers = {{}};
        const zoneCircles = {{}};
        
        zone.forEach(z => {{
            // Cerchio zona
            const circle = L.circle([z.lat, z.lon], {{
                color: z.colore,
                fillColor: z.colore,
                fillOpacity: 0.2,
                radius: 6000,
                weight: 2
            }}).addTo(map);
            
            circle.bindPopup(createPopupContent(z));
            zoneCircles[z.nome] = circle;
            
            // Marker centro
            const marker = L.circleMarker([z.lat, z.lon], {{
                radius: 8,
                fillColor: z.colore,
                color: 'white',
                weight: 2,
                fillOpacity: 1
            }}).addTo(map);
            
            marker.bindPopup(createPopupContent(z));
            zoneMarkers[z.nome] = marker;
        }});
        
        function createPopupContent(z) {{
            let html = `<b style="color:${{z.colore}}">${{z.nome}}</b><br>`;
            html += `<small>${{z.comuni.join(', ')}}</small><br><br>`;
            html += `<b>📊 ${{z.totale}} contatori</b><br>`;
            if (z.date.length > 0) {{
                html += `<br><b>📅 Date:</b><br>`;
                z.date.slice(0, 5).forEach(d => {{
                    const appStr = d.appuntamenti > 0 ? ` (📞${{d.appuntamenti}})` : '';
                    html += `${{d.data}}: ${{d.contatori}} cont.${{appStr}}<br>`;
                }});
                if (z.date.length > 5) html += `<i>...e altre ${{z.date.length - 5}} date</i>`;
            }}
            return html;
        }}
        
        // Ricerca
        const searchInput = document.getElementById('searchInput');
        const resultsDiv = document.getElementById('results');
        
        function renderResults(filteredZone) {{
            if (filteredZone.length === 0) {{
                resultsDiv.innerHTML = '<div class="no-results">🔍 Nessun risultato</div>';
                return;
            }}
            
            resultsDiv.innerHTML = filteredZone.map(z => `
                <div class="result-card" data-zona="${{z.nome}}" style="border-left-color: ${{z.colore}}">
                    <div class="zona-nome" style="color: ${{z.colore}}">${{z.nome}}</div>
                    <div class="zona-comuni">${{z.comuni.join(', ')}}</div>
                    <div class="zona-stats">
                        <span>📍 ${{z.distanza}} km (${{z.direzione}})</span>
                        <span>📊 ${{z.totale}} contatori</span>
                    </div>
                    ${{z.date.length > 0 ? `
                        <div class="date-list">
                            ${{z.date.map(d => `
                                <div class="date-item">
                                    <span class="data">📅 ${{d.data}}</span>
                                    <span class="info">${{d.contatori}} cont.</span>
                                    ${{d.appuntamenti > 0 ? `<span class="app">📞 ${{d.appuntamenti}}</span>` : ''}}
                                </div>
                            `).join('')}}
                        </div>
                    ` : '<div class="date-list"><i>Nessuna data pianificata</i></div>'}}
                </div>
            `).join('');
            
            // Click handler
            document.querySelectorAll('.result-card').forEach(card => {{
                card.addEventListener('click', () => {{
                    const zonaNome = card.dataset.zona;
                    const z = zone.find(x => x.nome === zonaNome);
                    if (z) {{
                        map.flyTo([z.lat, z.lon], 12);
                        zoneCircles[zonaNome].openPopup();
                        
                        document.querySelectorAll('.result-card').forEach(c => c.classList.remove('selected'));
                        card.classList.add('selected');
                    }}
                }});
            }});
        }}
        
        function searchZone(query) {{
            if (!query) {{
                renderResults(zone.sort((a, b) => b.totale - a.totale));
                return;
            }}
            
            query = query.toLowerCase();
            const filtered = zone.filter(z => 
                z.nome.toLowerCase().includes(query) ||
                z.comuni.some(c => c.toLowerCase().includes(query))
            ).sort((a, b) => b.totale - a.totale);
            
            renderResults(filtered);
            
            // Evidenzia sulla mappa
            if (filtered.length === 1) {{
                const z = filtered[0];
                map.flyTo([z.lat, z.lon], 12);
            }}
        }}
        
        searchInput.addEventListener('input', (e) => searchZone(e.target.value));
        
        // Render iniziale
        renderResults(zone.sort((a, b) => b.totale - a.totale));
        
        // Legenda
        const legend = L.control({{ position: 'bottomright' }});
        legend.onAdd = function(map) {{
            const div = L.DomUtil.create('div', 'legend');
            div.innerHTML = '<div class="legend-title">🗺️ Zone</div>';
            zone.sort((a, b) => b.totale - a.totale).slice(0, 10).forEach(z => {{
                div.innerHTML += `
                    <div class="legend-item">
                        <div class="legend-color" style="background: ${{z.colore}}"></div>
                        ${{z.nome.substring(0, 25)}}${{z.nome.length > 25 ? '...' : ''}}
                    </div>
                `;
            }});
            return div;
        }};
        legend.addTo(map);
    </script>
</body>
</html>'''
    
    # Scrivi file
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(html)
    
    logger.info(f"✅ HTML interattivo salvato: {filename}")


def json_dumps(obj):
    """Converte oggetto Python in JSON per JavaScript."""
    import json
    return json.dumps(obj, ensure_ascii=False)


def trova_date_per_comune(comune, risultato_zone):
    """
    Utility: dato un comune, trova tutte le date in cui il tecnico sarà in zona.
    
    Uso:
        risultato = genera_report_zone(df)
        date = trova_date_per_comune("BUSSOLENO", risultato)
    """
    if not risultato_zone:
        return None
    
    comune_upper = comune.upper().strip()
    zona = risultato_zone['comune_to_zona'].get(comune_upper)
    
    if not zona:
        # Cerca match parziale
        for c, z in risultato_zone['comune_to_zona'].items():
            if comune_upper in c.upper() or c.upper() in comune_upper:
                zona = z
                break
    
    if not zona:
        return None
    
    calendario = risultato_zone['calendario'].get(zona, {})
    return {
        'zona': zona,
        'comuni_zona': risultato_zone['zone'][zona]['comuni'],
        'date': calendario.get('date', {}),
        'totale_contatori': calendario.get('totale_contatori', 0)
    }