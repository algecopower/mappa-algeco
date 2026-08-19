# -*- coding: utf-8 -*-
"""
Generazione mappa HTML interattiva.
Funzionalità:
- Vista zone con clustering geografico
- Vista dettaglio con percorso ordinato
- Ricerca per comune/cliente/POD/Eneltel
- Filtro LCL con dropdown
- Toggle tema chiaro/scuro
- Toggle ultime 3 cifre Eneltel sui marker
- Sidebar nascondibile
- Auto-refresh per aggiornamenti in tempo reale
"""
import json
import logging
import os
from datetime import datetime

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .safe_json import json_read, json_write

_TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'templates')
_jinja_env = Environment(
    loader=FileSystemLoader(_TEMPLATES_DIR),
    autoescape=select_autoescape(['html']),
)

logger = logging.getLogger('mappa_algeco')

from config import (
    DEPOSITO,
    MATRICOLE_VIP,
    COLORI_TECNICI,
    OPERATORI_DICT,
    PROVINCIA_ATTIVA,
    INTERVALLO_SYNC_SECONDI,
    DATA_FINE,
    ORA_INIZIO_GIORNATA,
    ORA_FINE_GIORNATA
)

from .percorso import ordina_percorso
from .verifica_indirizzi_ui import (
    genera_css_verifica_indirizzi,
    genera_js_verifica_indirizzi,
    genera_html_verifica_indirizzi,
)
from .gestionale import (
    genera_css_gestionale,
    genera_js_gestionale,
    genera_html_gestionale
)
from .zone_geografiche import is_valid_coordinate


def _normalizza_ora_app(valore):
    """
    Restituisce una stringa orario "pulita" o None.

    Gestisce i casi in cui ORA_APPUNTAMENTO arriva da pandas come NaN
    (truthy ma non confrontabile con se' stesso), pd.NA, o stringhe
    fasulle ('nan', 'None', '<NA>'). Senza questa normalizzazione il
    frontend riceve `"nan"` e la mostra come badge nella card.
    """
    if valore is None:
        return None
    # NaN float (nan != nan e' True)
    if isinstance(valore, float) and valore != valore:
        return None
    # pd.NA, pd.NaT e simili: testabili senza importare pandas
    cls_name = type(valore).__name__
    if cls_name in ('NAType', 'NaTType'):
        return None
    s = str(valore).strip()
    if not s or s.lower() in ('nan', 'none', 'null', '<na>', 'nat'):
        return None
    return s


# Cache module-level dei dati pronti per il render Jinja della mappa.
# Popolata da genera_mappa_html() ad ogni sync; letta da render_mappa_corrente().
_cached_render_inputs = None


def genera_mappa_html(df, report, risultato_zone, filename=None):
    """
    Aggiorna i dati della mappa (JSON su disco + cache render in memoria).
    Il parametro `filename` è accettato per retrocompatibilità ma ignorato:
    l'HTML non viene più scritto su disco — viene renderizzato al volo dalla
    route Flask via render_mappa_corrente().
    """
    global _cached_render_inputs

    zone_data = prepara_dati_zone(risultato_zone)
    contatori_data = prepara_dati_contatori(df, report)
    rotte_data = prepara_rotte_per_data(report)

    lcl_list = sorted(set(c['lcl'] for c in contatori_data if c['lcl']))
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')

    # JSON su disco: il frontend li fetcha all'avvio (vedi
    # static/mappa.js:initializeData) e ad ogni refresh SSE.
    salva_config_statica()
    salva_dati_json(zone_data, contatori_data, rotte_data, lcl_list, timestamp)

    # Cache per render_mappa_corrente(): tenere solo dati piccoli (timestamp +
    # opzioni LCL del filtro). I dati pesanti (~110 MB in RAM) vengono fetchati
    # dal frontend via /data/<provincia>/mappa_data.json — l'embedding HTML
    # non serve più, è leftover storico del modo file:// che non usiamo.
    _cached_render_inputs = {
        'lcl_list': lcl_list,
        'timestamp': timestamp,
    }

    logger.info("✅ Mappa aggiornata (cache render leggera + JSON su disco)")
    return None


def render_mappa_corrente():
    """
    Ritorna l'HTML della mappa renderizzato con l'ultima cache di sync.
    L'HTML non contiene più i dati embedded (zone/contatori/rotte): il
    frontend li fetcha asincrono. Se la cache è vuota usa default vuoti.
    """
    if _cached_render_inputs is None:
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        return genera_html_completo([], timestamp=timestamp)
    return genera_html_completo(**_cached_render_inputs)


def prepara_dati_zone(risultato_zone):
    """Prepara dati zone per JavaScript."""
    if not risultato_zone:
        return []

    zone = risultato_zone.get('zone', {})
    calendario = risultato_zone.get('calendario', {})
    centroidi = risultato_zone.get('centroidi', {})

    colori = [
        '#e6194b', '#3cb44b', '#ffe119', '#4363d8', '#f58231',
        '#911eb4', '#46f0f0', '#f032e6', '#bcf60c', '#fabebe',
        '#008080', '#e6beff', '#9a6324', '#fffac8', '#800000',
        '#aaffc3', '#808000', '#ffd8b1', '#000075', '#808080'
    ]

    zone_list = []
    for idx, (zona_nome, zona_info) in enumerate(zone.items()):
        date_info = []

        if zona_nome in calendario:
            for data, d_info in sorted(calendario[zona_nome]['date'].items()):
                date_info.append({
                    'data': data,
                    'contatori': d_info['contatori'],
                    'appuntamenti': d_info['appuntamenti'],
                    'operatori': list(d_info['operatori']) if d_info['operatori'] else []
                })

        # Calcola raggio dinamico basato sulla distanza massima dei comuni dal centroide
        raggio_metri = 4000  # Valore di default
        if centroidi and zona_info['comuni']:
            from moduli.zone_geografiche import calcola_distanza_km

            lat_centro = zona_info['centroide_lat']
            lon_centro = zona_info['centroide_lon']
            max_dist_km = 0

            for comune in zona_info['comuni']:
                if comune.upper() in centroidi:
                    c = centroidi[comune.upper()]
                    dist_km = calcola_distanza_km(lat_centro, lon_centro, c['lat'], c['lon'])
                    max_dist_km = max(max_dist_km, dist_km)

            # Raggio = distanza massima + buffer del 30% per includere bene tutti i comuni
            # Minimo 3km, massimo 25km
            raggio_metri = max(3000, min(25000, (max_dist_km * 1.3) * 1000))

        zone_list.append({
            'nome': zona_nome,
            'comuni': zona_info['comuni'],
            'lat': zona_info['centroide_lat'],
            'lon': zona_info['centroide_lon'],
            'distanza': zona_info.get('distanza_deposito', 0),
            'direzione': zona_info.get('direzione', ''),
            'colore': colori[idx % len(colori)],
            'date': date_info,
            'totale': calendario.get(zona_nome, {}).get('totale_contatori', 0),
            'raggio': int(raggio_metri)
        })

    return zone_list


def prepara_dati_contatori(df, report):
    """
    Prepara dati contatori per JavaScript.
    Include l'ordine pre-calcolato dal Python usando OSRM per distanze reali.
    """
    from datetime import datetime

    contatori = []

    # Calcola range date visibili di default: da oggi a DATA_FINE dal config
    oggi = datetime.now().date()
    # Usa DATA_FINE dal config (formato 'YYYY-MM-DD'), se vuoto = illimitata
    if DATA_FINE:
        data_fine_visibile = datetime.strptime(DATA_FINE, '%Y-%m-%d').date()
    else:
        data_fine_visibile = datetime(9999, 12, 31).date()

    # ================================================================
    # PRE-CALCOLA l'ordine per ogni combinazione data/tecnico
    # usando l'algoritmo Python con OSRM (distanze stradali reali)
    # ================================================================
    ordine_precalcolato = calcola_ordine_percorsi(df)

    for _, row in df.iterrows():
        lat = row.get('Lat')
        lon = row.get('Lon')

        if not lat or not lon:
            continue
        
        # Valida coordinate (devono essere nell'area Italia: lat 35-48, lon 6-19)
        if not is_valid_coordinate(lat, lon):
            eneltel_debug = row.get('Eneltel_Key', 'N/A')
            logger.warning(f"⚠️  Coordinate invalide per contatore {eneltel_debug}: lat={lat}, lon={lon} - IGNORATO")
            continue

        # Determina se il contatore è nel range visibile di default
        data_str = str(row.get('DATA_K', ''))
        # Per contatori senza data valida, metti stringa vuota (non 'DATA_MANCANTE')
        if data_str == 'DATA_MANCANTE':
            data_str = ''
        
        is_annullato = bool(row.get('IS_ANNULLATO', False))
        
        visibile_default = False
        try:
            data_contatore = datetime.strptime(data_str, '%Y-%m-%d').date()
            visibile_default = oggi <= data_contatore <= data_fine_visibile
        except Exception:
            # Se data non valida, NON mostrare di default (sono schedulabili, non visibili normalmente)
            visibile_default = False

        # Ottieni l'ordine pre-calcolato per questo contatore
        eneltel_key = str(row.get('Eneltel_Key', ''))
        tecnico = str(row.get('TEC_K', 'TUTTI'))
        chiave_ordine = f"{data_str}_{tecnico}_{eneltel_key}"
        ordine_tappa = ordine_precalcolato.get(chiave_ordine, 999)

        # Access points UPS-style (lat/lon di routing) - fallback a lat/lon raw
        lat_access = row.get('Lat_access')
        lon_access = row.get('Lon_access')
        access_source = row.get('access_source')
        access_road = row.get('access_road')
        try:
            lat_access_f = float(lat_access) if lat_access is not None else float(lat)
        except (TypeError, ValueError):
            lat_access_f = float(lat)
        try:
            lon_access_f = float(lon_access) if lon_access is not None else float(lon)
        except (TypeError, ValueError):
            lon_access_f = float(lon)

        contatore = {
            'id': str(row.get('Eneltel_Key', '')),
            'eneltel': str(row.get('ENEL_K', row.get('Eneltel_Key', ''))),
            'pod': str(row.get('POD_K', '')),
            'cliente': str(row.get('CLI_K', '')),
            'comune': str(row.get('COMUNE', '')),
            'localita': str(row.get('LOCALITA', '')),
            'via': str(row.get('VIA_K', '')),
            'civico': str(row.get('CIVICO_K', '')),
            'telefono': str(row.get('TEL_K', '')),
            'email': str(row.get('EMAIL_K', '')),
            'note': str(row.get('NOTE_K', '')),
            'note_app': str(row.get('NOTE_APP_RAW', '')),
            'tipo_ce': str(row.get('TIPO_CE_K', '')),
            'posizione_mis': str(row.get('POSIZIONE_MIS', '')),

            'orari_lettere': str(row.get('ORARI_LETTERE', '')) if row.get('ORARI_LETTERE') else '',
            'ora_pianificazione': str(row.get('ORARI_LETTERE', '')) if row.get('ORARI_LETTERE') else '',
            'data': data_str,
            'tecnico': str(row.get('TEC_K', 'TUTTI')),
            'lat': float(lat),
            'lon': float(lon),
            # Access point UPS-style usato per il routing
            'lat_access': lat_access_f,
            'lon_access': lon_access_f,
            'access_source': str(access_source) if access_source else None,
            'access_road': str(access_road) if access_road else None,
            'access_override': access_source == 'override',
            'lcl': str(row.get('LCL', '')),
            'is_appuntamento': bool(row.get('IS_APPUNTAMENTO', False)),
            # NaN di pandas e' truthy ma non si confronta uguale a se stesso:
            # _normalizza_ora_app evita che finisca a UI come la stringa "nan".
            'ora_appuntamento': _normalizza_ora_app(row.get('ORA_APPUNTAMENTO')),
            'visibile_default': visibile_default,
            'ordine_tappa': ordine_tappa,  # Ordine pre-calcolato da Python/OSRM
            'annullato': is_annullato,  # Flag ANNULLATO dal filone
            'smart_test': str(row.get('SMART_TEST_K', ''))  # SMART TEST (Torino)
        }
        contatori.append(contatore)

    return contatori


def calcola_ordine_percorsi(df):
    """
    Pre-calcola l'ordine delle tappe per ogni combinazione data/tecnico.
    Usa l'algoritmo Python con OSRM (stesso usato per Excel/report).
    
    CACHE: Salva i risultati su file e li ricarica se i dati non sono cambiati.

    IMPORTANTE: OSRM deve essere attivo (Docker). Se non disponibile,
    solleva un'eccezione con istruzioni per avviare il container.

    Returns:
        dict: {data_tecnico_eneltel: ordine} per lookup rapido

    Raises:
        RuntimeError: se OSRM non è disponibile
    """
    import sys
    import hashlib
    from datetime import datetime
    
    ordine_map = {}
    
    # Filtra date con tecnici assegnati (include anche 'TUTTI' per contatori senza operatore)
    df_con_tecnici = df[df['TEC_K'].notna() & (df['TEC_K'] != '')]
    
    if df_con_tecnici.empty:
        logger.info("   ℹ️  Nessun tecnico assegnato - ordine non calcolato")
        return ordine_map
    
    # ================================================================
    # OTTIMIZZAZIONE: Calcola solo per date da oggi a DATA_FINE
    # Le date passate non servono, le date future oltre DATA_FINE non sono visibili
    # ================================================================
    oggi = datetime.now().date()
    if DATA_FINE:
        data_fine = datetime.strptime(DATA_FINE, '%Y-%m-%d').date()
    else:
        data_fine = None  # Illimitata
    
    # Conta combinazioni da processare (solo date nel range visibile)
    combinazioni = []
    for data in df_con_tecnici['DATA_K'].unique():
        # Verifica se la data è nel range visibile
        try:
            data_dt = datetime.strptime(str(data), '%Y-%m-%d').date()
            if data_dt < oggi or (data_fine and data_dt > data_fine):
                continue  # Salta date fuori range
        except Exception:
            continue  # Salta date non valide
            
        df_data = df_con_tecnici[df_con_tecnici['DATA_K'] == data]
        for tecnico in df_data['TEC_K'].unique():
            df_tec = df_data[df_data['TEC_K'] == tecnico]
            if not df_tec.empty and len(df_tec) > 1:  # Solo se ci sono almeno 2 contatori
                combinazioni.append((data, tecnico, df_tec))
    
    if not combinazioni:
        logger.info("   ℹ️  Nessun percorso da calcolare per il range date visibile")
        return ordine_map
    
    # ================================================================
    # CACHE: Calcola hash dei dati per verificare se sono cambiati
    # ================================================================
    # VERSIONE ALGORITMO: incrementare quando cambia la logica di ordinamento
    # Questo invalida la cache e forza il ricalcolo con il nuovo algoritmo
    ALGORITMO_VERSIONE = "4.1_access_points"  # v4.1: UPS-style access point per civico (snap-by-name OSRM) + override manuale
    
    # Crea hash basato su: versione algoritmo, date, tecnici, coordinate contatori
    hash_data = [f"ALGO_VERSION:{ALGORITMO_VERSIONE}"]
    for data, tecnico, df_tec in combinazioni:
        for _, row in df_tec.iterrows():
            hash_data.append(f"{data}_{tecnico}_{row.get('Eneltel_Key', '')}_{row.get('Lat', 0):.6f}_{row.get('Lon', 0):.6f}")
    hash_data.sort()
    hash_str = hashlib.md5('|'.join(hash_data).encode()).hexdigest()
    
    # File cache per provincia
    cache_file = os.path.join("cache", f"cache_ordine_percorsi_{PROVINCIA_ATTIVA.lower()}.json")
    
    # Prova a caricare dalla cache
    cache = json_read(cache_file, default=None)
    if cache is not None:
        if cache.get('hash') == hash_str:
            logger.info("   ✅ Ordine percorsi caricato da cache (dati invariati)")
            return cache.get('ordine_map', {})
        else:
            logger.info("   🔄 Dati modificati, ricalcolo ordine percorsi...")
    else:
        logger.info("   🆕 Prima esecuzione, calcolo ordine percorsi...")
    
    # ================================================================
    # CALCOLO: Ordina ogni combinazione data/tecnico
    # ================================================================
    logger.info(f"   🧮 Calcolo ordine percorsi per {len(combinazioni)} combinazioni data/tecnico...")
    
    for idx, (data, tecnico, df_tec) in enumerate(combinazioni):
        try:
            # Progress
            if (idx + 1) % 5 == 0 or idx == 0:
                sys.stdout.write(f"\r      📍 {idx + 1}/{len(combinazioni)}...")
                sys.stdout.flush()
            
            # Ordina con l'algoritmo Python (stesso usato in genera_fasce_orarie.py)
            # Usa OSRM locale per distanze stradali reali
            # Rimuovi colonne duplicate per evitare warning
            df_tec_clean = df_tec.loc[:, ~df_tec.columns.duplicated()]
            punti_list = df_tec_clean.to_dict('records')
            percorso_ordinato = ordina_percorso(punti_list, DEPOSITO, usa_osrm_ottimizzazione=True)
            
            # Salva l'ordine per ogni contatore
            for ordine_idx, punto in enumerate(percorso_ordinato):
                eneltel_key = str(punto.get('Eneltel_Key', ''))
                chiave = f"{data}_{tecnico}_{eneltel_key}"
                ordine_map[chiave] = ordine_idx + 1  # 1-based
                
        except Exception as e:
            # Se fallisce, lascia senza ordine (verrà mostrato errore sulla mappa)
            logger.warning(f"      ⚠️  Errore calcolo ordine per {data}/{tecnico}: {str(e)[:50]}")
            continue
    
    logger.info(f"      ✅ Ordine calcolato per {len(combinazioni)} percorsi")
    
    # ================================================================
    # SALVA CACHE per evitare ricalcoli futuri
    # ================================================================
    if not json_write(cache_file, {
        'hash': hash_str,
        'ordine_map': ordine_map,
        'timestamp': datetime.now().isoformat()
    }):
        logger.warning("   ⚠️  Impossibile salvare cache")
    else:
        logger.info("   💾 Cache ordine percorsi salvata")
    
    return ordine_map


def prepara_rotte_per_data(report):
    """Estrae le geometrie delle rotte dal report."""
    rotte = {}

    per_data = report.get('per_data', {})
    for data, data_info in per_data.items():
        tecnici = data_info.get('tecnici', {})
        rotte[data] = {}

        for tec, tec_info in tecnici.items():
            if tec_info.get('geometria_rotta'):
                rotte[data][tec] = tec_info['geometria_rotta']

    return rotte


def salva_dati_json(zone_data, contatori_data, rotte_data, lcl_list, timestamp=None):
    """
    Salva dati dinamici in file JSON separato.
    Questi dati vengono rigenerati ogni ciclo di sincronizzazione.
    """
    data_dir = f'data/{PROVINCIA_ATTIVA.lower()}'
    os.makedirs(data_dir, exist_ok=True)

    if timestamp is None:
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')

    data = {
        'timestamp': timestamp,
        'zone': zone_data,
        'contatori': contatori_data,
        'rotte': rotte_data,
        'lcl_list': lcl_list
    }

    filepath = os.path.join(data_dir, 'mappa_data.json')
    json_write(filepath, data, separators=(',', ':'))

    logger.info(f"📦 Dati JSON salvati: {filepath}")
    return timestamp


def salva_config_statica():
    """
    Genera file config statico per la provincia.
    Viene generato solo se non esiste già.
    """
    data_dir = f'data/{PROVINCIA_ATTIVA.lower()}'
    filepath = os.path.join(data_dir, 'config.json')

    # Rigenera sempre per riflettere eventuali cambiamenti alle impostazioni

    os.makedirs(data_dir, exist_ok=True)

    config = {
        'deposito': {
            'nome': DEPOSITO['Nome'],
            'lat': DEPOSITO['Lat'],
            'lon': DEPOSITO['Lon']
        },
        'coloriTecnici': COLORI_TECNICI,
        'matricoleVip': MATRICOLE_VIP,
        'operatoriDict': OPERATORI_DICT,
        'provincia': PROVINCIA_ATTIVA,
        'refreshInterval': INTERVALLO_SYNC_SECONDI * 1000 + 5000
    }

    json_write(filepath, config, indent=2)

    logger.info(f"⚙️ Config statica salvata: {filepath}")


def genera_html_completo(lcl_list, timestamp=None):
    """Render mappa via template Jinja templates/mappa.html.

    L'HTML NON contiene piu' i dati pesanti embedded (zone/contatori/rotte):
    il frontend li fetcha asincrono da /data/<provincia>/mappa_data.json.
    Risparmio in RAM ~110 MB per backend.
    """
    if timestamp is None:
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')

    deposito_json = json.dumps({
        'nome': DEPOSITO['Nome'],
        'lat': DEPOSITO['Lat'],
        'lon': DEPOSITO['Lon'],
    }, ensure_ascii=False)
    lcl_options_html = ''.join(
        f'<option value="{lcl}">{lcl}</option>' for lcl in lcl_list
    )

    template = _jinja_env.get_template('mappa.html')
    return template.render(
        provincia=PROVINCIA_ATTIVA,
        provincia_lower=PROVINCIA_ATTIVA.lower(),
        timestamp=timestamp,
        data_fine=DATA_FINE,
        refresh_interval=60000,
        ora_inizio_giornata=ORA_INIZIO_GIORNATA,
        ora_fine_giornata=ORA_FINE_GIORNATA,
        # Placeholders: il frontend li riempie via fetch. Niente embedding pesante.
        zone_json='[]',
        contatori_json='[]',
        rotte_json='{}',
        lcl_json=json.dumps(lcl_list, ensure_ascii=False),
        deposito_json=deposito_json,
        colori_tecnici_json=json.dumps(COLORI_TECNICI, ensure_ascii=False),
        matricole_vip_json=json.dumps(MATRICOLE_VIP, ensure_ascii=False),
        operatori_dict_json=json.dumps(OPERATORI_DICT, ensure_ascii=False),
        lcl_options_html=lcl_options_html,
        css_gestionale=genera_css_gestionale(),
        css_verifica_indirizzi=genera_css_verifica_indirizzi(),
        js_gestionale=genera_js_gestionale(),
        js_verifica_indirizzi=genera_js_verifica_indirizzi(),
        html_gestionale=genera_html_gestionale(),
        html_verifica_indirizzi=genera_html_verifica_indirizzi(),
    )
