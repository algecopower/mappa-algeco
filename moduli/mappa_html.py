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
import tempfile
from datetime import datetime
from collections import defaultdict

from .safe_json import json_read, json_write

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

from .percorso import ordina_percorso, calcola_distanza_haversine
from .gestionale import (
    genera_css_gestionale,
    genera_js_gestionale,
    genera_html_gestionale,
    genera_pulsanti_sidebar
)
from .zone_geografiche import is_valid_coordinate


def genera_mappa_html(df, report, risultato_zone, filename=None):
    """
    Genera mappa HTML interattiva completa.
    Genera anche file JSON separati per ottimizzare i refresh.
    """
    if filename is None:
        filename = os.path.join("templates", f"mappa_{PROVINCIA_ATTIVA.lower()}.html")

    # Prepara dati
    zone_data = prepara_dati_zone(risultato_zone)
    contatori_data = prepara_dati_contatori(df, report)
    rotte_data = prepara_rotte_per_data(report)

    # Estrai tutti gli LCL unici
    lcl_list = sorted(set(c['lcl'] for c in contatori_data if c['lcl']))

    # Genera timestamp UNICO per JSON e HTML (alla fine del sync)
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    
    # Salva JSON separati (per refresh ottimizzato via fetch)
    salva_config_statica()
    salva_dati_json(zone_data, contatori_data, rotte_data, lcl_list, timestamp)

    # Genera HTML (con dati embedded per retrocompatibilità file://)
    html = genera_html_completo(zone_data, contatori_data, rotte_data, lcl_list, timestamp)

    # Salva atomicamente (temp file + os.replace)
    os.makedirs(os.path.dirname(filename) or '.', exist_ok=True)
    content = html.encode('utf-8', errors='replace').decode('utf-8')
    dirpath = os.path.dirname(filename) or '.'
    fd, tmp_path = tempfile.mkstemp(dir=dirpath, prefix='.tmp_mappa_', suffix='.html')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, filename)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    logger.info(f"✅ Mappa HTML salvata: {filename}")
    return filename


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
        lcl_set = set()

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
    from datetime import datetime, timedelta

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
            'data': data_str,
            'tecnico': str(row.get('TEC_K', 'TUTTI')),
            'lat': float(lat),
            'lon': float(lon),
            'lcl': str(row.get('LCL', '')),
            'is_appuntamento': bool(row.get('IS_APPUNTAMENTO', False)),
            'ora_appuntamento': str(row.get('ORA_APPUNTAMENTO', '')) if row.get('ORA_APPUNTAMENTO') else None,
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
    import json
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
    ALGORITMO_VERSIONE = "3.0_nearest_geo"  # v3.0: nearest neighbor geografico PURO
    
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
            logger.info(f"   ✅ Ordine percorsi caricato da cache (dati invariati)")
            return cache.get('ordine_map', {})
        else:
            logger.info(f"   🔄 Dati modificati, ricalcolo ordine percorsi...")
    else:
        logger.info(f"   🆕 Prima esecuzione, calcolo ordine percorsi...")
    
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
        logger.warning(f"   ⚠️  Impossibile salvare cache")
    else:
        logger.info(f"   💾 Cache ordine percorsi salvata")
    
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


def genera_html_completo(zone_data, contatori_data, rotte_data, lcl_list, timestamp=None):
    """Genera il codice HTML completo."""
    
    if timestamp is None:
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    
    # Prepara JSON
    zone_json = json.dumps(zone_data, ensure_ascii=False)
    contatori_json = json.dumps(contatori_data, ensure_ascii=False)
    rotte_json = json.dumps(rotte_data, ensure_ascii=False)
    deposito_json = json.dumps({
        'nome': DEPOSITO['Nome'],
        'lat': DEPOSITO['Lat'],
        'lon': DEPOSITO['Lon']
    }, ensure_ascii=False)
    colori_tecnici_json = json.dumps(COLORI_TECNICI, ensure_ascii=False)
    matricole_vip_json = json.dumps(MATRICOLE_VIP, ensure_ascii=False)
    operatori_dict_json = json.dumps(OPERATORI_DICT, ensure_ascii=False)
    lcl_json = json.dumps(lcl_list, ensure_ascii=False)
    
    # Fallback polling ogni 60s (usato solo se SSE non è disponibile)
    refresh_interval = 60000
    
    # Genera opzioni LCL PRIMA della f-string per evitare problemi di concatenazione
    lcl_options_html = ''.join([f'<option value="{lcl}">{lcl}</option>' for lcl in lcl_list])

    html = f'''<!DOCTYPE html>
<html lang="it">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="icon" type="image/svg+xml" href="/favicon.svg?v=2">
    <title>Mappa Contatori - {PROVINCIA_ATTIVA}</title>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {{
            /* Tema scuro (default) */
            --bg-primary: #1a1a2e;
            --bg-secondary: #16213e;
            --bg-tertiary: #0f3460;
            --text-primary: #eee;
            --text-secondary: #aaa;
            --text-muted: #666;
            --accent: #e94560;
            --accent-hover: #ff6b6b;
            --border: #0f3460;
            --shadow: rgba(0,0,0,0.3);
        }}
        
        [data-theme="light"] {{
            --bg-primary: #f5f5f5;
            --bg-secondary: #ffffff;
            --bg-tertiary: #e8e8e8;
            --text-primary: #333;
            --text-secondary: #666;
            --text-muted: #999;
            --accent: #9b59b6;
            --accent-hover: #a855f7;
            --border: #ddd;
            --shadow: rgba(0,0,0,0.1);
        }}
        
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ 
            font-family: 'Inter', 'Segoe UI', Arial, sans-serif; 
            background: var(--bg-primary); 
            color: var(--text-primary);
            transition: all 0.3s ease;
        }}
        
        .container {{ display: flex; height: 100vh; }}
        
        /* Sidebar */
        .sidebar {{
            width: 370px;
            background: linear-gradient(180deg, var(--bg-secondary) 0%, var(--bg-primary) 100%);
            display: flex;
            flex-direction: column;
            z-index: 1000;
            box-shadow: 4px 0 20px var(--shadow);
            transition: all 0.3s ease;
        }}
        
        .sidebar.collapsed {{
            width: 0;
            overflow: hidden;
        }}
        
        .sidebar-toggle {{
            position: absolute;
            left: 370px;
            top: 50%;
            transform: translateY(-50%);
            background: var(--accent);
            color: white;
            border: none;
            padding: 15px 8px;
            border-radius: 0 8px 8px 0;
            cursor: pointer;
            z-index: 1001;
            font-size: 14px;
            transition: all 0.3s ease;
        }}
        
        .sidebar.collapsed + .sidebar-toggle,
        .sidebar-toggle.moved {{
            left: 0;
        }}
        
        .sidebar-toggle:hover {{ background: var(--accent-hover); }}
        
        /* Header */
        .header {{
            background: linear-gradient(135deg, var(--bg-tertiary) 0%, var(--bg-secondary) 100%);
            padding: 10px 14px;
            border-bottom: 1px solid var(--border);
        }}
        
        .header-top {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 6px;
        }}
        
        .header h1 {{ 
            font-size: 1.1em; 
            font-weight: 700;
            color: var(--accent);
        }}
        
        .header p {{ 
            opacity: 0.7; 
            font-size: 0.72em; 
        }}
        
        /* Toggles */
        .toggles {{
            display: flex;
            gap: 6px;
            flex-wrap: wrap;
        }}
        
        .toggle-btn {{
            background: var(--bg-tertiary);
            border: 1px solid var(--border);
            color: var(--text-secondary);
            padding: 4px 9px;
            border-radius: 12px;
            cursor: pointer;
            font-size: 0.7em;
            transition: all 0.2s;
        }}
        
        .toggle-btn:hover {{ border-color: var(--accent); }}
        .toggle-btn.active {{
            background: var(--accent);
            color: white;
            border-color: var(--accent);
        }}

        /* Breadcrumb */
        .breadcrumb {{
            display: none;
            padding: 6px 10px;
            background: var(--bg-tertiary);
            border-bottom: 1px solid var(--border);
            align-items: center;
            gap: 8px;
            flex-wrap: nowrap;
        }}

        .breadcrumb.visible {{ display: flex; }}

        .btn-back {{
            background: var(--accent);
            color: white;
            border: none;
            padding: 0;
            padding-bottom: 2px;
            border-radius: 50%;
            cursor: pointer;
            font-weight: 600;
            font-size: 1.1em;
            transition: background 0.3s, transform 0.3s;
            flex-shrink: 0;
            width: 32px;
            height: 32px;
            display: flex;
            align-items: center;
            justify-content: center;
            line-height: 1;
        }}

        .btn-back:hover {{ background: var(--accent-hover); transform: scale(1.1); }}

        .breadcrumb-text {{
            font-size: 0.75em;
            color: var(--text-secondary);
            flex: 1;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            min-width: 0;
        }}
        .breadcrumb-text strong {{ color: var(--accent); }}

        /* Date Navigation */
        .date-navigation {{
            display: none;
            align-items: center;
            gap: 4px;
            margin-left: auto;
            flex-shrink: 0;
        }}

        .date-navigation.visible {{
            display: flex;
        }}

        .btn-date-nav {{
            background: var(--bg-secondary);
            color: var(--text-primary);
            border: 1px solid var(--border);
            padding: 0;
            border-radius: 50%;
            cursor: pointer;
            font-weight: 600;
            font-size: 0.9em;
            transition: all 0.3s;
            width: 28px;
            height: 28px;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
        }}

        .btn-date-nav:hover {{
            background: var(--accent);
            color: white;
            border-color: var(--accent);
        }}

        .btn-date-nav:disabled {{
            opacity: 0.3;
            cursor: not-allowed;
        }}

        .btn-date-nav:disabled:hover {{
            background: var(--bg-secondary);
            color: var(--text-primary);
            border-color: var(--border);
        }}

        .btn-date-current {{
            background: var(--accent);
            color: white;
            border: none;
            padding: 4px 10px;
            border-radius: 16px;
            cursor: pointer;
            font-weight: 600;
            font-size: 0.7em;
            transition: all 0.3s;
            white-space: nowrap;
            flex-shrink: 0;
        }}

        .btn-date-current:hover {{
            transform: scale(1.05);
            box-shadow: 0 2px 8px rgba(var(--accent-rgb), 0.3);
        }}

        /* Date Picker Modal */
        .date-picker-modal {{
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.35);
            backdrop-filter: blur(8px);
            -webkit-backdrop-filter: blur(8px);
            z-index: 10000;
            align-items: center;
            justify-content: center;
        }}

        .date-picker-modal.visible {{
            display: flex;
        }}

        /* Orari Lettere Modal */
        .orari-lettere-modal {{
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.35);
            backdrop-filter: blur(8px);
            -webkit-backdrop-filter: blur(8px);
            z-index: 10000;
            align-items: center;
            justify-content: center;
        }}

        .orari-lettere-modal.visible {{
            display: flex;
        }}

        .orari-lettere-content {{
            background: var(--bg-primary);
            padding: 24px;
            border-radius: 16px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
            max-width: 440px;
            width: 90%;
            animation: modalSlideUp 0.3s cubic-bezier(0.16, 1, 0.3, 1);
        }}

        .orari-lettere-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
        }}

        .orari-lettere-title {{
            font-size: 1.3em;
            font-weight: 700;
            color: var(--text-primary);
        }}

        .orari-lettere-close {{
            background: var(--bg-tertiary);
            border: none;
            color: var(--text-primary);
            font-size: 1.3em;
            width: 36px;
            height: 36px;
            border-radius: 50%;
            cursor: pointer;
            transition: background 0.3s, color 0.3s, transform 0.3s;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
            line-height: 1;
            padding: 0;
        }}

        .orari-lettere-close:hover {{
            background: var(--accent);
            color: white;
            transform: rotate(90deg);
        }}

        .orari-lettere-form {{
            display: flex;
            flex-direction: column;
            gap: 14px;
        }}

        .orari-lettere-form label {{
            font-weight: 600;
            color: var(--text-secondary);
            font-size: 0.85em;
            margin-bottom: 4px;
        }}

        .orari-lettere-form input[type="date"] {{
            background: var(--bg-secondary);
            border: 2px solid var(--border);
            color: var(--text-primary);
            padding: 10px 14px;
            border-radius: 10px;
            font-size: 1em;
            width: 100%;
            box-sizing: border-box;
        }}

        .orari-lettere-form input[type="date"]:focus {{
            outline: none;
            border-color: var(--accent);
        }}

        .orari-lettere-info {{
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 12px;
            font-size: 0.82em;
            color: var(--text-secondary);
            line-height: 1.5;
        }}

        .orari-lettere-btn {{
            background: linear-gradient(135deg, #3498db 0%, #2980b9 100%);
            color: white;
            border: none;
            padding: 12px;
            border-radius: 10px;
            font-weight: 700;
            font-size: 1em;
            cursor: pointer;
            transition: all 0.3s;
            margin-top: 4px;
        }}

        .orari-lettere-btn:hover {{
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(52, 152, 219, 0.4);
        }}

        .orari-lettere-btn:disabled {{
            opacity: 0.5;
            cursor: not-allowed;
            transform: none;
            box-shadow: none;
        }}

        .orari-lettere-result {{
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 14px;
            font-size: 0.85em;
            color: var(--text-primary);
            line-height: 1.6;
            display: none;
        }}

        .orari-lettere-result.success {{
            border-color: #2ecc71;
            background: rgba(46, 204, 113, 0.1);
        }}

        .orari-lettere-result.error {{
            border-color: #e74c3c;
            background: rgba(231, 76, 60, 0.1);
        }}

        .date-picker-content {{
            background: var(--bg-primary);
            padding: 24px;
            border-radius: 16px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
            max-width: 90%;
            max-height: 80vh;
            overflow-y: auto;
            min-width: 300px;
            animation: modalSlideUp 0.3s cubic-bezier(0.16, 1, 0.3, 1);
        }}

        .date-picker-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
        }}

        .date-picker-title {{
            font-size: 1.3em;
            font-weight: 700;
            color: var(--text-primary);
        }}

        .date-picker-close {{
            background: var(--bg-tertiary);
            border: none;
            color: var(--text-primary);
            font-size: 1.3em;
            width: 36px;
            height: 36px;
            border-radius: 50%;
            cursor: pointer;
            transition: background 0.3s, color 0.3s, transform 0.3s;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
            line-height: 1;
            padding: 0;
        }}

        .date-picker-close:hover {{
            background: var(--accent);
            color: white;
            transform: rotate(90deg);
        }}

        .date-list-picker {{
            display: grid;
            gap: 8px;
        }}

        .date-item-picker {{
            background: var(--bg-secondary);
            border: 2px solid var(--border);
            padding: 12px 16px;
            border-radius: 12px;
            cursor: pointer;
            transition: all 0.3s;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}

        .date-item-picker:hover {{
            background: var(--bg-tertiary);
            border-color: var(--accent);
            transform: translateX(4px);
        }}

        .date-item-picker.current {{
            background: var(--accent);
            border-color: var(--accent);
            color: white;
        }}

        .date-item-picker .date-text {{
            font-weight: 600;
            font-size: 1em;
        }}

        .date-item-picker .date-info {{
            font-size: 0.85em;
            opacity: 0.8;
        }}

        .btn-pin-search {{
            background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
            color: white;
            border: none;
            padding: 6px 12px;
            border-radius: 20px;
            cursor: pointer;
            font-weight: 600;
            font-size: 0.85em;
            transition: all 0.3s;
            display: flex;
            align-items: center;
            gap: 5px;
        }}

        .btn-pin-search:hover {{
            transform: scale(1.05);
            box-shadow: 0 4px 12px rgba(245, 87, 108, 0.4);
        }}
        
        /* Filters */
        .filters {{
            padding: 10px 14px;
            background: rgba(15, 52, 96, 0.3);
            border-bottom: 1px solid var(--border);
        }}
        
        .search-row {{
            display: flex;
            gap: 8px;
            margin-bottom: 8px;
        }}
        
        .search-box {{
            flex: 1;
            position: relative;
        }}

        .search-box input {{
            width: 100%;
            padding: 9px 36px 9px 14px;
            border: 2px solid var(--border);
            border-radius: 20px;
            font-size: 13px;
            outline: none;
            background: var(--bg-secondary);
            color: var(--text-primary);
            transition: all 0.3s;
        }}

        .search-box input:disabled {{
            opacity: 0.5;
            cursor: not-allowed;
        }}

        .search-box input::placeholder {{ color: var(--text-muted); }}
        .search-box input:focus {{
            border-color: var(--accent);
            box-shadow: 0 0 15px rgba(233, 69, 96, 0.2);
        }}

        .search-clear {{
            position: absolute;
            right: 12px;
            top: 50%;
            transform: translateY(-50%);
            background: var(--text-muted);
            color: var(--bg-primary);
            border: none;
            width: 20px;
            height: 20px;
            border-radius: 50%;
            cursor: pointer;
            display: none;
            align-items: center;
            justify-content: center;
            font-size: 12px;
            font-weight: bold;
            transition: all 0.2s;
        }}

        .search-clear:hover {{
            background: var(--accent);
            color: white;
            transform: translateY(-50%) scale(1.1);
        }}

        .search-clear.visible {{
            display: flex;
        }}

        /* Advanced Search Panel */
        .advanced-search-panel {{
            display: none !important;
            margin-top: 10px;
            padding: 12px;
            background: var(--bg-tertiary);
            border-radius: 12px;
            border: 1px solid var(--border);
            max-height: 0;
            overflow: hidden;
            transition: max-height 0.3s ease;
        }}

        .advanced-search-panel.visible {{
            display: block;
            max-height: 400px;
            overflow-y: auto;
        }}

        .advanced-search-grid {{
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 8px;
        }}

        .advanced-search-field {{
            display: flex;
            flex-direction: column;
            gap: 4px;
        }}

        .advanced-search-field label {{
            font-size: 0.8em;
            color: var(--text-secondary);
            font-weight: 600;
        }}

        .advanced-search-field input {{
            padding: 8px 10px;
            border: 2px solid var(--border);
            border-radius: 8px;
            font-size: 13px;
            outline: none;
            background: var(--bg-secondary);
            color: var(--text-primary);
            transition: all 0.3s;
        }}

        .advanced-search-field input:focus {{
            border-color: var(--accent);
        }}

        .advanced-search-actions {{
            margin-top: 8px;
            display: flex;
            gap: 8px;
            justify-content: flex-end;
        }}

        .advanced-search-actions button {{
            padding: 6px 14px;
            border: none;
            border-radius: 8px;
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s;
        }}

        .btn-clear-advanced {{
            background: var(--bg-secondary);
            color: var(--text-secondary);
        }}

        .btn-clear-advanced:hover {{
            background: var(--bg-tertiary);
        }}

        .btn-apply-advanced {{
            background: var(--accent);
            color: white;
        }}

        .btn-apply-advanced:hover {{
            background: #c93d55;
        }}

        .lcl-filter {{
            min-width: 120px;
        }}

        .lcl-filter select {{
            width: 100%;
            padding: 9px 36px 9px 14px;
            border: 2px solid var(--border);
            border-radius: 20px;
            font-size: 13px;
            outline: none;
            background: var(--bg-secondary);
            color: var(--text-primary);
            cursor: pointer;
            transition: all 0.3s;
            appearance: none;
            -webkit-appearance: none;
            -moz-appearance: none;
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%23888' d='M6 9L1 4h10z'/%3E%3C/svg%3E");
            background-repeat: no-repeat;
            background-position: right 14px center;
        }}

        .lcl-filter select:focus {{
            border-color: var(--accent);
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%23e84c65' d='M6 9L1 4h10z'/%3E%3C/svg%3E");
        }}

        .lcl-filter select:hover {{
            border-color: var(--accent);
        }}

        /* Date Filter */
        #dataFilter {{
            width: 100%;
            padding: 9px 36px 9px 14px;
            border: 2px solid var(--border);
            border-radius: 20px;
            font-size: 13px;
            outline: none;
            background: var(--bg-secondary);
            color: var(--text-primary);
            cursor: pointer;
            transition: all 0.3s;
            appearance: none;
            -webkit-appearance: none;
            -moz-appearance: none;
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%23888' d='M6 9L1 4h10z'/%3E%3C/svg%3E");
            background-repeat: no-repeat;
            background-position: right 14px center;
            font-weight: 500;
        }}

        #dataFilter:focus {{
            border-color: var(--accent);
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%23e84c65' d='M6 9L1 4h10z'/%3E%3C/svg%3E");
        }}

        #dataFilter:hover {{
            border-color: var(--accent);
        }}

        /* All Dates Toggle */
        .detail-filters {{
            display: flex;
            gap: 8px;
            margin-top: 8px;
        }}

        .detail-operatore-container {{
            position: relative;
            width: 100%;
        }}
        
        .detail-operatore-trigger {{
            width: 100%;
            padding: 7px 32px 7px 12px;
            border: 2px solid var(--border);
            border-radius: 20px;
            font-size: 12px;
            outline: none;
            background: var(--bg-secondary);
            color: var(--text-primary);
            cursor: pointer;
            transition: all 0.3s;
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%23888' d='M6 9L1 4h10z'/%3E%3C/svg%3E");
            background-repeat: no-repeat;
            background-position: right 12px center;
            font-weight: 500;
            text-align: left;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            user-select: none;
        }}
        
        .detail-operatore-trigger:hover {{
            border-color: var(--accent);
        }}
        
        .detail-operatore-trigger.active {{
            border-color: var(--accent);
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%23e84c65' d='M6 3l5 5H1z'/%3E%3C/svg%3E");
        }}
        
        .detail-operatore-dropdown {{
            display: none;
            position: absolute;
            top: calc(100% + 4px);
            left: 0;
            right: 0;
            background: var(--bg-secondary);
            border: 2px solid var(--accent);
            border-radius: 12px;
            z-index: 1000;
            max-height: 260px;
            overflow-y: auto;
            box-shadow: 0 8px 24px rgba(0,0,0,0.15);
        }}
        
        .detail-operatore-dropdown.open {{
            display: block;
        }}
        
        .detail-operatore-item {{
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 8px 12px;
            cursor: pointer;
            transition: background 0.15s;
            font-size: 12px;
            font-weight: 500;
            color: var(--text-primary);
        }}
        
        .detail-operatore-item:first-child {{
            border-radius: 10px 10px 0 0;
        }}
        
        .detail-operatore-item:last-child {{
            border-radius: 0 0 10px 10px;
        }}
        
        .detail-operatore-item:hover {{
            background: var(--bg-tertiary);
        }}
        
        .detail-operatore-item input[type="checkbox"] {{
            width: 15px;
            height: 15px;
            cursor: pointer;
            accent-color: var(--accent);
            flex-shrink: 0;
        }}
        
        .detail-operatore-item .op-color-dot {{
            width: 11px;
            height: 11px;
            border-radius: 3px;
            flex-shrink: 0;
        }}

        .filter-chip {{
            flex: 1;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 5px;
            padding: 5px 8px;
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            border-radius: 7px;
            cursor: pointer;
            transition: all 0.2s;
            user-select: none;
            font-size: 11px;
        }}

        .filter-chip:hover {{
            border-color: var(--accent);
            background: var(--bg-tertiary);
        }}

        .filter-chip input[type="checkbox"] {{
            width: 12px;
            height: 12px;
            margin: 0;
            accent-color: var(--accent);
        }}

        .filter-chip:has(input:checked) {{
            background: rgba(231, 76, 60, 0.2);
            border-color: var(--accent);
        }}

        .schedulabili-chip {{
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            border-radius: 7px;
            padding: 5px 10px;
            font-size: 11px;
            transition: all 0.2s;
        }}
        .schedulabili-chip:hover {{
            border-color: var(--accent-secondary, #06b6d4);
            background: var(--bg-tertiary);
        }}
        .schedulabili-chip:has(input:checked),
        .schedulabili-chip.active {{
            background: rgba(245, 158, 11, 0.2);
            border-color: var(--accent-secondary, #06b6d4);
        }}
        .schedulabile-item {{
            border-left: 3px solid var(--accent-secondary, #06b6d4) !important;
        }}
        .zone-schedulabili-badge {{
            cursor: pointer;
            padding: 4px 8px;
            border-radius: 6px;
            transition: all 0.2s;
            user-select: none;
        }}
        .zone-schedulabili-badge:hover {{
            background: rgba(245, 158, 11, 0.15);
        }}
        .zone-schedulabili-badge.active {{
            background: rgba(245, 158, 11, 0.2);
        }}
        .zone-sched-inline-list {{
            display: none;
            margin-top: 4px;
        }}
        .zone-sched-inline-list.visible {{
            display: block;
        }}
        /* Pannello filtri schedulabili */
        .sched-filters-panel {{
            display: none;
            flex-direction: column;
            gap: 6px;
            padding: 8px 10px;
            margin-top: 4px;
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            border-radius: 8px;
            animation: slideDown 0.2s ease;
        }}
        .sched-filters-panel.visible {{
            display: flex;
        }}
        .sched-filter-row {{
            display: flex;
            align-items: center;
            gap: 6px;
        }}
        .sched-filter-row label {{
            font-size: 0.75rem;
            color: var(--text-muted);
            white-space: nowrap;
            min-width: 80px;
        }}
        .sched-filter-row select {{
            flex: 1;
            min-width: 0;
            padding: 4px 8px;
            border-radius: 6px;
            border: 1px solid var(--border);
            background: var(--bg-tertiary);
            color: var(--text);
            font-size: 0.78rem;
            cursor: pointer;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        .sched-filter-chips {{
            display: flex;
            flex-wrap: wrap;
            gap: 4px;
        }}
        .sched-filter-chip {{
            padding: 3px 8px;
            border-radius: 12px;
            border: 1px solid var(--border);
            background: var(--bg-tertiary);
            color: var(--text-muted);
            font-size: 0.72rem;
            cursor: pointer;
            transition: all 0.15s;
            user-select: none;
        }}
        .sched-filter-chip:hover {{
            border-color: var(--accent-secondary, #06b6d4);
        }}
        .sched-filter-chip.active {{
            background: rgba(6, 182, 212, 0.2);
            border-color: var(--accent-secondary, #06b6d4);
            color: var(--text);
        }}
        .sched-filters-count {{
            font-size: 0.72rem;
            color: var(--text-muted);
            text-align: right;
            padding-top: 2px;
        }}
        .sched-filters-count strong {{
            color: var(--accent-secondary, #06b6d4);
        }}
        @keyframes pulse-marker {{
            0%, 100% {{ transform: scale(1); }}
            50% {{ transform: scale(1.2); }}
        }}

        /* Dropdown operatore multi-select per percorso giornata */
        .operatore-filter-container {{
            display: none;
            min-width: 170px;
            position: relative;
        }}
        
        .operatore-filter-container.visible {{
            display: block;
        }}
        
        .operatore-multi-trigger {{
            width: 100%;
            padding: 9px 36px 9px 14px;
            border: 2px solid var(--border);
            border-radius: 20px;
            font-size: 13px;
            outline: none;
            background: var(--bg-secondary);
            color: var(--text-primary);
            cursor: pointer;
            transition: all 0.3s;
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%23888' d='M6 9L1 4h10z'/%3E%3C/svg%3E");
            background-repeat: no-repeat;
            background-position: right 14px center;
            font-weight: 500;
            text-align: left;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            user-select: none;
        }}
        
        .operatore-multi-trigger:hover {{
            border-color: var(--accent);
        }}
        
        .operatore-multi-trigger.active {{
            border-color: var(--accent);
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%23e84c65' d='M6 3l5 5H1z'/%3E%3C/svg%3E");
        }}
        
        .operatore-multi-dropdown {{
            display: none;
            position: absolute;
            top: calc(100% + 4px);
            left: 0;
            right: 0;
            background: var(--bg-secondary);
            border: 2px solid var(--accent);
            border-radius: 12px;
            z-index: 1000;
            max-height: 260px;
            overflow-y: auto;
            box-shadow: 0 8px 24px rgba(0,0,0,0.15);
        }}
        
        .operatore-multi-dropdown.open {{
            display: block;
        }}
        
        .operatore-multi-item {{
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 8px 12px;
            cursor: pointer;
            transition: background 0.15s;
            font-size: 13px;
            font-weight: 500;
            color: var(--text-primary);
        }}
        
        .operatore-multi-item:first-child {{
            border-radius: 10px 10px 0 0;
        }}
        
        .operatore-multi-item:last-child {{
            border-radius: 0 0 10px 10px;
        }}
        
        .operatore-multi-item:hover {{
            background: var(--bg-tertiary);
        }}
        
        .operatore-multi-item input[type="checkbox"] {{
            width: 16px;
            height: 16px;
            cursor: pointer;
            accent-color: var(--accent);
            flex-shrink: 0;
        }}
        
        .operatore-multi-item .op-color-dot {{
            width: 12px;
            height: 12px;
            border-radius: 3px;
            flex-shrink: 0;
        }}

        .toggle-label input[type="checkbox"] {{
            width: 20px;
            height: 20px;
            cursor: pointer;
            accent-color: var(--accent);
        }}

        .toggle-text {{
            flex: 1;
            font-size: 14px;
            font-weight: 500;
            color: var(--text-primary);
        }}

        .toggle-label:has(input:checked) {{
            background: var(--accent);
            border-color: var(--accent);
        }}

        .toggle-label:has(input:checked) .toggle-text {{
            color: white;
        }}

        /* Results */
        .results {{
            flex: 1;
            overflow-y: auto;
            padding: 10px;
        }}
        
        .results::-webkit-scrollbar {{ width: 8px; }}
        .results::-webkit-scrollbar-track {{ background: var(--bg-secondary); }}
        .results::-webkit-scrollbar-thumb {{ background: var(--bg-tertiary); border-radius: 4px; }}
        .results::-webkit-scrollbar-thumb:hover {{ background: var(--accent); }}
        
        /* Zone Cards */
        .zone-card {{
            background: linear-gradient(135deg, var(--bg-secondary) 0%, var(--bg-primary) 100%);
            border-radius: 10px;
            padding: 11px 13px;
            margin-bottom: 8px;
            border: 1px solid var(--border);
            cursor: pointer;
            transition: all 0.3s;
        }}
        
        .zone-card:hover {{
            transform: translateX(5px);
            border-color: var(--accent);
            box-shadow: 0 5px 20px rgba(233, 69, 96, 0.15);
        }}
        
        .zone-card.selected {{
            border-color: var(--accent);
            background: linear-gradient(135deg, var(--bg-primary) 0%, var(--bg-tertiary) 100%);
        }}
        
        .zone-header {{
            display: flex;
            align-items: center;
            margin-bottom: 6px;
        }}
        
        .zone-color {{
            width: 12px;
            height: 12px;
            border-radius: 3px;
            margin-right: 8px;
        }}
        
        .zone-nome {{
            font-weight: 600;
            font-size: 0.9em;
            color: var(--text-primary);
            flex: 1;
        }}
        
        .zone-totale {{
            background: var(--accent);
            color: white;
            padding: 3px 8px;
            border-radius: 10px;
            font-size: 0.75em;
            font-weight: 600;
        }}
        
        .zone-comuni {{
            font-size: 0.73em;
            color: var(--text-secondary);
            margin-bottom: 5px;
        }}
        
        .zone-lcl {{
            font-size: 0.7em;
            color: var(--accent);
            margin-bottom: 5px;
        }}
        
        .zone-stats {{
            display: flex;
            gap: 12px;
            font-size: 0.73em;
            color: var(--text-muted);
            margin-bottom: 8px;
        }}
        
        /* Date list */
        .date-list {{
            background: var(--bg-tertiary);
            border-radius: 8px;
            overflow: hidden;
        }}
        
        .date-item {{
            display: grid;
            grid-template-columns: 1fr auto auto auto;
            gap: 8px;
            align-items: center;
            padding: 8px 11px;
            border-bottom: 1px solid var(--border);
            cursor: pointer;
            transition: all 0.2s;
        }}

        .date-item:last-child {{ border-bottom: none; }}
        .date-item:hover {{ background: rgba(233, 69, 96, 0.1); }}

        .date-item .data {{
            font-weight: 600;
            color: var(--accent);
            font-size: 0.82em;
        }}
        .date-item .info {{
            color: var(--text-secondary);
            font-size: 0.78em;
            min-width: 70px;
            text-align: right;
            white-space: nowrap;
        }}
        .date-item .app {{
            color: #f1c40f;
            font-weight: 600;
            font-size: 0.78em;
            min-width: 36px;
            text-align: center;
        }}
        .date-item .arrow {{
            color: var(--accent);
            font-size: 1em;
            text-align: right;
            min-width: 18px;
        }}
        
        .date-expand {{
            text-align: center;
            padding: 7px;
            color: var(--accent);
            cursor: pointer;
            font-size: 0.78em;
            border-top: 1px solid var(--border);
            transition: all 0.2s;
        }}
        
        .date-expand:hover {{
            background: rgba(233, 69, 96, 0.1);
        }}
        
        .date-list.expanded .date-hidden {{
            display: flex !important;
        }}
        
        .date-hidden {{
            display: none !important;
        }}
        
        /* Tappa Cards */
        .tappa-card {{
            background: linear-gradient(135deg, var(--bg-secondary) 0%, var(--bg-primary) 100%);
            border-radius: 8px;
            padding: 10px 12px;
            margin-bottom: 7px;
            border: 1px solid var(--border);
            cursor: pointer;
            transition: all 0.3s;
            position: relative;
        }}
        
        .tappa-card:hover {{
            border-color: var(--accent);
            transform: translateX(3px);
        }}
        
        .tappa-card.appuntamento {{
            border-left: 4px solid var(--accent);
        }}
        
        .tappa-card.deposito {{
            border-left: 4px solid #2ecc71;
            background: linear-gradient(135deg, rgba(46, 204, 113, 0.1) 0%, var(--bg-primary) 100%);
        }}
        
        .tappa-header {{
            display: flex;
            align-items: center;
            margin-bottom: 5px;
        }}
        
        .tappa-edit-btn {{
            background: none;
            border: none;
            cursor: pointer;
            font-size: 1.1em;
            padding: 4px 6px;
            border-radius: 6px;
            opacity: 0.5;
            transition: all 0.2s;
            margin-left: 6px;
            flex-shrink: 0;
        }}
        
        .tappa-edit-btn:hover {{
            opacity: 1;
            background: var(--accent);
            transform: scale(1.15);
        }}
        
        .tappa-numero {{
            background: var(--accent);
            color: white;
            width: 24px;
            height: 24px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 700;
            font-size: 0.75em;
            margin-right: 10px;
        }}
        
        .tappa-card.deposito .tappa-numero {{
            background: #2ecc71;
        }}
        
        .tappa-cliente {{
            font-weight: 600;
            color: var(--text-primary);
            flex: 1;
        }}
        
        .tappa-app-badge {{
            position: absolute;
            bottom: 8px;
            right: 8px;
            background: var(--accent);
            color: white;
            padding: 3px 8px;
            border-radius: 8px;
            font-size: 0.7em;
            font-weight: 600;
            line-height: 1.2;
            display: flex;
            align-items: center;
            gap: 4px;
        }}

        .tappa-app-badge .icon {{
            font-size: 1.1em;
        }}
        
        .tappa-orari-lettere {{
            position: absolute;
            bottom: 8px;
            right: 8px;
            background: rgba(52, 152, 219, 0.25);
            color: #7ec8e3;
            padding: 2px 7px;
            border-radius: 6px;
            font-size: 0.65em;
            font-weight: 500;
            line-height: 1.3;
            display: flex;
            align-items: center;
            gap: 3px;
            border: 1px solid rgba(52, 152, 219, 0.3);
        }}
        
        .tappa-card.appuntamento .tappa-orari-lettere {{
            bottom: 30px;
        }}

        .tappa-tempo {{
            font-size: 0.7em;
            color: #667eea;
            margin-left: 34px;
            margin-bottom: 3px;
            font-weight: 500;
        }}
        
        .tappa-dettagli {{
            font-size: 0.73em;
            color: var(--text-secondary);
            margin-left: 34px;
        }}
        
        .tappa-dettagli div {{ margin-bottom: 3px; }}
        
        /* Tecnico header */
        .tecnico-header {{
            background: linear-gradient(135deg, var(--bg-tertiary) 0%, var(--bg-secondary) 100%);
            padding: 10px 12px;
            margin: 7px 0;
            border-radius: 8px;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        
        .tecnico-color {{
            width: 20px;
            height: 20px;
            border-radius: 50%;
        }}
        
        .tecnico-nome {{
            font-weight: 600;
            color: var(--text-primary);
            flex: 1;
        }}
        
        .tecnico-count {{
            color: var(--text-secondary);
            font-size: 0.85em;
        }}
        
        /* Stats bar */
        .stats-bar {{
            background: var(--bg-tertiary);
            padding: 8px 14px;
            display: flex;
            justify-content: space-around;
            border-top: 1px solid var(--border);
        }}
        
        .stat-item {{
            text-align: center;
        }}
        
        .stat-value {{
            font-size: 1.1em;
            font-weight: 700;
            color: var(--accent);
        }}
        
        .stat-label {{
            font-size: 0.68em;
            color: var(--text-muted);
            text-transform: uppercase;
        }}
        
        /* Map */
        #map {{
            flex: 1;
            background: var(--bg-primary);
            transition: all 0.3s ease;
        }}

        /* Zoom Slider */
        .zoom-slider-container {{
            position: absolute;
            bottom: 20px;
            left: 50%;
            transform: translateX(-50%);
            display: flex;
            align-items: center;
            gap: 10px;
            background: var(--bg-secondary);
            padding: 10px 20px;
            border-radius: 25px;
            box-shadow: 0 4px 15px rgba(0,0,0,0.15);
            z-index: 1000;
            border: 1px solid var(--border-color);
        }}

        .zoom-slider-container .zoom-label {{
            font-size: 18px;
            font-weight: bold;
            color: var(--text-secondary);
            user-select: none;
            width: 28px;
            height: 28px;
            text-align: center;
            line-height: 28px;
            cursor: pointer;
            border-radius: 50%;
            transition: background 0.2s, color 0.2s;
        }}

        .zoom-slider-container .zoom-label:hover {{
            background: var(--bg-tertiary);
            color: var(--text-primary);
        }}

        .zoom-slider-container .zoom-label:active {{
            transform: scale(0.9);
        }}

        .zoom-slider-container .zoom-value {{
            font-size: 12px;
            font-weight: 600;
            color: var(--text-primary);
            min-width: 45px;
            text-align: center;
            background: var(--bg-primary);
            padding: 4px 8px;
            border-radius: 12px;
        }}

        #zoomSlider, #printZoomSlider {{
            -webkit-appearance: none;
            appearance: none;
            width: 200px;
            height: 8px;
            border-radius: 4px;
            background: #4a5568;
            outline: none;
            cursor: pointer;
            border: 1px solid rgba(255,255,255,0.2);
        }}

        #zoomSlider::-webkit-slider-runnable-track,
        #printZoomSlider::-webkit-slider-runnable-track {{
            height: 8px;
            border-radius: 4px;
            background: linear-gradient(to right, #4a5568 0%, #667eea 100%);
        }}

        #zoomSlider::-webkit-slider-thumb,
        #printZoomSlider::-webkit-slider-thumb {{
            -webkit-appearance: none;
            appearance: none;
            width: 22px;
            height: 22px;
            border-radius: 50%;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            cursor: pointer;
            box-shadow: 0 2px 8px rgba(0,0,0,0.3);
            border: 3px solid white;
            transition: transform 0.15s ease;
            margin-top: -7px;
        }}

        #zoomSlider::-webkit-slider-thumb:hover,
        #printZoomSlider::-webkit-slider-thumb:hover {{
            transform: scale(1.15);
        }}

        #zoomSlider::-moz-range-track,
        #printZoomSlider::-moz-range-track {{
            height: 8px;
            border-radius: 4px;
            background: linear-gradient(to right, #4a5568 0%, #667eea 100%);
        }}

        #zoomSlider::-moz-range-thumb,
        #printZoomSlider::-moz-range-thumb {{
            width: 16px;
            height: 16px;
            border-radius: 50%;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            cursor: pointer;
            box-shadow: 0 2px 8px rgba(0,0,0,0.3);
            border: 3px solid white;
        }}

        /* Markers */
        .marker-deposito {{
            background: #2ecc71;
            border: 3px solid white;
            border-radius: 50%;
            width: 24px;
            height: 24px;
            box-shadow: 0 3px 10px rgba(0,0,0,0.3);
        }}
        
        .marker-contatore {{
            border: 3px solid white;
            border-radius: 50%;
            width: 24px;
            height: 24px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.3);
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            font-size: 10px;
            color: white;
        }}
        
        .marker-label {{
            position: absolute;
            top: -20px;
            left: 50%;
            transform: translateX(-50%);
            background: rgba(0,0,0,0.8);
            color: white;
            padding: 2px 6px;
            border-radius: 4px;
            font-size: 10px;
            font-weight: bold;
            white-space: nowrap;
        }}
        
        .marker-appuntamento {{
            background: #e94560 !important;
            animation: pulse 2s infinite;
        }}
        
        @keyframes pulse {{
            0% {{ box-shadow: 0 0 0 0 rgba(233, 69, 96, 0.7); }}
            70% {{ box-shadow: 0 0 0 15px rgba(233, 69, 96, 0); }}
            100% {{ box-shadow: 0 0 0 0 rgba(233, 69, 96, 0); }}
        }}

        /* Toast aggiornamento mappa */
        .mappa-toast {{
            position: fixed;
            bottom: 24px;
            left: 50%;
            transform: translateX(-50%) translateY(80px);
            background: rgba(37, 211, 102, 0.95);
            color: #fff;
            padding: 10px 22px;
            border-radius: 30px;
            font-size: 0.88em;
            font-weight: 600;
            box-shadow: 0 6px 24px rgba(0,0,0,0.25);
            z-index: 20000;
            pointer-events: none;
            opacity: 0;
            transition: transform 0.4s cubic-bezier(0.34, 1.56, 0.64, 1), opacity 0.3s ease;
            backdrop-filter: blur(6px);
        }}
        .mappa-toast.visible {{
            transform: translateX(-50%) translateY(0);
            opacity: 1;
        }}

        /* Marker sovrapposti (cluster) */
        .marker-cluster {{
            position: relative;
        }}

        .marker-cluster-badge {{
            position: absolute;
            top: -8px;
            right: -8px;
            background: #ff6b6b;
            color: white;
            min-width: 18px;
            height: 18px;
            border-radius: 50%;
            font-size: 11px;
            font-weight: bold;
            display: flex;
            align-items: center;
            justify-content: center;
            border: 2px solid white;
            box-shadow: 0 2px 4px rgba(0,0,0,0.3);
        }}

        /* Popup multi-contatore */
        .popup-multi-header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 12px;
            border-radius: 8px;
            margin-bottom: 12px;
            text-align: center;
        }}

        .popup-multi-header-title {{
            font-weight: 700;
            font-size: 1.1em;
            margin-bottom: 4px;
        }}

        .popup-multi-header-subtitle {{
            font-size: 0.85em;
            opacity: 0.9;
        }}

        .popup-multi-list {{
            max-height: 300px;
            overflow-y: auto;
            overflow-x: hidden;
        }}

        .popup-multi-item {{
            padding: 12px;
            border: 1px solid var(--border);
            border-radius: 8px;
            margin-bottom: 8px;
            cursor: pointer;
            transition: all 0.2s ease;
            background: var(--bg-tertiary);
        }}

        .popup-multi-item:hover {{
            background: var(--accent);
            color: white;
        }}

        .popup-multi-item:last-child {{
            margin-bottom: 0;
        }}

        .popup-multi-item-title {{
            font-weight: 600;
            margin-bottom: 4px;
            display: flex;
            align-items: center;
            gap: 8px;
        }}

        .popup-multi-item-num {{
            background: var(--accent);
            color: white;
            width: 22px;
            height: 22px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 12px;
            flex-shrink: 0;
        }}

        .popup-multi-item:hover .popup-multi-item-num {{
            background: white;
            color: var(--accent);
        }}

        .popup-multi-item-info {{
            font-size: 0.85em;
            color: var(--text-secondary);
        }}

        .popup-multi-item:hover .popup-multi-item-info {{
            color: rgba(255,255,255,0.9);
        }}

        .popup-multi-item-app {{
            display: inline-block;
            background: #e94560;
            color: white;
            padding: 2px 6px;
            border-radius: 4px;
            font-size: 0.75em;
            margin-left: 8px;
        }}
        
        /* Popup */
        .leaflet-popup-content-wrapper {{
            background: linear-gradient(135deg, var(--bg-secondary) 0%, var(--bg-primary) 100%);
            color: var(--text-primary);
            border-radius: 12px;
            box-shadow: 0 5px 25px var(--shadow);
        }}
        
        .leaflet-popup-tip {{
            background: var(--bg-secondary);
        }}
        
        .popup-content {{
            min-width: 250px;
        }}
        
        .popup-header {{
            border-bottom: 1px solid var(--border);
            padding-bottom: 10px;
            margin-bottom: 10px;
        }}
        
        .popup-title {{
            font-weight: 700;
            font-size: 1.1em;
            color: var(--accent);
            margin-bottom: 5px;
        }}
        
        .popup-subtitle {{
            font-size: 0.85em;
            color: var(--text-secondary);
        }}
        
        .popup-app {{
            background: var(--accent);
            color: white;
            padding: 8px 12px;
            border-radius: 8px;
            margin-bottom: 10px;
            font-weight: 600;
            text-align: center;
        }}
        
        .popup-travel-time {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 8px 12px;
            border-radius: 8px;
            margin-bottom: 10px;
            font-weight: 500;
            text-align: center;
            font-size: 0.9em;
        }}
        
        .popup-row {{
            display: flex;
            padding: 6px 0;
            border-bottom: 1px solid var(--border);
            font-size: 0.85em;
        }}
        
        .popup-row:last-child {{ border-bottom: none; }}
        
        .popup-label {{
            color: var(--text-secondary);
            width: 80px;
            flex-shrink: 0;
        }}
        
        .popup-value {{
            color: var(--text-primary);
            word-break: break-all;
        }}
        
        /* Update indicator */
        .update-indicator {{
            position: fixed;
            bottom: 20px;
            right: 20px;
            background: var(--accent);
            color: white;
            padding: 10px 20px;
            border-radius: 25px;
            font-size: 0.85em;
            z-index: 9999;
            display: none;
            animation: fadeIn 0.3s;
        }}
        
        @keyframes fadeIn {{
            from {{ opacity: 0; transform: translateY(20px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}

        @keyframes modalSlideUp {{
            from {{ opacity: 0; transform: translateY(24px) scale(0.97); }}
            to {{ opacity: 1; transform: translateY(0) scale(1); }}
        }}
        
        /* No results */
        .no-results {{
            text-align: center;
            padding: 50px 20px;
            color: var(--text-muted);
        }}
        
        .no-results-icon {{
            font-size: 3em;
            margin-bottom: 15px;
        }}

        /* Pin Search Modal */
        .pin-modal {{
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.35);
            backdrop-filter: blur(8px);
            -webkit-backdrop-filter: blur(8px);
            z-index: 10000;
            align-items: center;
            justify-content: center;
        }}

        .pin-modal.visible {{
            display: flex;
        }}

        .pin-modal-content {{
            background: var(--bg-primary);
            border-radius: 16px;
            padding: 24px;
            width: 90%;
            max-width: 600px;
            max-height: 80vh;
            overflow-y: auto;
            box-shadow: 0 10px 40px rgba(0, 0, 0, 0.5);
            border: 1px solid var(--border);
            animation: modalSlideUp 0.3s cubic-bezier(0.16, 1, 0.3, 1);
        }}

        .pin-modal-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
        }}

        .pin-modal-title {{
            font-size: 1.3em;
            font-weight: 700;
            color: var(--accent);
        }}

        .pin-modal-close {{
            background: var(--bg-tertiary);
            color: var(--text-primary);
            border: none;
            width: 32px;
            height: 32px;
            border-radius: 50%;
            cursor: pointer;
            font-size: 1.1em;
            transition: background 0.3s, color 0.3s, transform 0.3s;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
            line-height: 1;
            padding: 0;
        }}

        .pin-modal-close:hover {{
            background: var(--accent);
            color: white;
            transform: rotate(90deg);
        }}

        .pin-search-results {{
            margin-top: 15px;
            max-height: 300px;
            overflow-y: auto;
        }}

        .address-search-container {{
            margin-bottom: 15px;
        }}

        .address-search-hint {{
            font-size: 0.8em;
            color: var(--text-muted);
            margin-top: 8px;
            font-style: italic;
        }}

        .address-search-results {{
            margin-top: 15px;
            max-height: 300px;
            overflow-y: auto;
        }}

        .address-result-item {{
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 12px;
            margin-bottom: 8px;
            cursor: pointer;
            transition: all 0.3s;
        }}

        .address-result-item:hover {{
            border-color: var(--accent);
            background: var(--bg-tertiary);
        }}

        .address-result-name {{
            font-weight: 600;
            color: var(--text-primary);
            margin-bottom: 4px;
        }}

        .address-result-detail {{
            font-size: 0.85em;
            color: var(--text-secondary);
        }}

        .address-search-loading {{
            text-align: center;
            padding: 20px;
            color: var(--text-muted);
        }}

        .address-search-error {{
            text-align: center;
            padding: 15px;
            color: #e74c3c;
            background: rgba(231, 76, 60, 0.1);
            border-radius: 8px;
        }}

        .pin-result-item {{
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 12px;
            margin-bottom: 8px;
            cursor: pointer;
            transition: all 0.3s;
        }}

        .pin-result-item:hover {{
            border-color: var(--accent);
            background: var(--bg-tertiary);
            transform: translateX(5px);
        }}

        .pin-result-item.pinned {{
            border: 2px solid var(--accent);
            background: linear-gradient(135deg, rgba(233, 69, 96, 0.1) 0%, rgba(240, 147, 251, 0.1) 100%);
        }}

        .pin-result-name {{
            font-weight: 600;
            color: var(--text-primary);
            margin-bottom: 4px;
        }}

        .pin-result-info {{
            font-size: 0.85em;
            color: var(--text-secondary);
        }}

        /* Responsive */
        @media (max-width: 900px) {{
            .container {{ flex-direction: column; }}
            .sidebar {{ width: 100%; height: 50%; }}
            .sidebar.collapsed {{ height: 0; }}
            #map {{ height: 50%; }}
            .sidebar-toggle {{
                left: 50%;
                top: auto;
                bottom: 50%;
                transform: translateX(-50%);
                border-radius: 8px 8px 0 0;
                padding: 8px 15px;
            }}
        }}

        /* Print Button */
        .print-button {{
            position: fixed;
            bottom: 12px;
            right: 72px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            padding: 12px 24px;
            border-radius: 25px;
            cursor: pointer;
            font-weight: 600;
            font-size: 14px;
            z-index: 9998;
            box-shadow: 0 4px 15px rgba(102, 126, 234, 0.4);
            transition: all 0.3s ease;
            display: flex;
            align-items: center;
            gap: 8px;
        }}

        .print-button:hover {{
            transform: scale(1.05);
            box-shadow: 0 6px 20px rgba(102, 126, 234, 0.5);
        }}

        .print-button:active {{
            transform: scale(0.98);
        }}

        .print-button.hidden {{
            display: none;
        }}

        /* Print Tappe Button */
        .print-tappe-button {{
            position: fixed;
            bottom: 12px;
            right: 250px;
            background: linear-gradient(135deg, #43b89c 0%, #3a7bd5 100%);
            color: white;
            border: none;
            padding: 12px 24px;
            border-radius: 25px;
            cursor: pointer;
            font-weight: 600;
            font-size: 14px;
            z-index: 9998;
            box-shadow: 0 4px 15px rgba(67, 184, 156, 0.4);
            transition: all 0.3s ease;
            display: flex;
            align-items: center;
            gap: 8px;
        }}

        .print-tappe-button:hover {{
            transform: scale(1.05);
            box-shadow: 0 6px 20px rgba(67, 184, 156, 0.5);
        }}

        .print-tappe-button:active {{
            transform: scale(0.98);
        }}

        .print-tappe-button.hidden {{
            display: none;
        }}

        /* Gestionale FAB Button */
        .gestionale-fab {{
            position: fixed;
            bottom: 12px;
            right: 12px;
            width: 48px;
            height: 48px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 50%;
            cursor: pointer;
            font-size: 20px;
            z-index: 9999;
            box-shadow: 0 4px 15px rgba(102, 126, 234, 0.4);
            transition: all 0.3s ease;
            display: flex;
            align-items: center;
            justify-content: center;
        }}

        [data-theme="dark"] .gestionale-fab {{
            background: linear-gradient(135deg, #e94560 0%, #c0392b 100%);
            box-shadow: 0 4px 15px rgba(233, 69, 96, 0.4);
        }}

        .gestionale-fab:hover {{
            transform: scale(1.1);
            box-shadow: 0 6px 20px rgba(102, 126, 234, 0.55);
        }}

        [data-theme="dark"] .gestionale-fab:hover {{
            box-shadow: 0 6px 20px rgba(233, 69, 96, 0.55);
        }}

        .gestionale-fab:active {{
            transform: scale(0.95);
        }}

        /* Gestionale Panel */
        .gestionale-panel {{
            position: fixed;
            bottom: 70px;
            right: 12px;
            width: 260px;
            max-height: 480px;
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            border-radius: 16px;
            box-shadow: 0 12px 40px var(--shadow);
            z-index: 9999;
            transform: translateY(20px);
            opacity: 0;
            pointer-events: none;
            transition: all 0.3s cubic-bezier(.4,0,.2,1);
            overflow: hidden;
        }}

        .gestionale-panel.visible {{
            transform: translateY(0);
            opacity: 1;
            pointer-events: auto;
        }}

        .gestionale-panel-header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 10px 14px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }}

        [data-theme="dark"] .gestionale-panel-header {{
            background: linear-gradient(135deg, #e94560 0%, #c0392b 100%);
        }}

        .gestionale-panel-title {{
            font-weight: 700;
            font-size: 14px;
        }}

        .gestionale-panel-close {{
            background: rgba(255,255,255,0.2);
            border: none;
            color: white;
            width: 24px;
            height: 24px;
            border-radius: 50%;
            cursor: pointer;
            font-size: 12px;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: background 0.2s;
        }}

        .gestionale-panel-close:hover {{
            background: rgba(255,255,255,0.35);
        }}

        .gestionale-panel-body {{
            padding: 6px;
            display: flex;
            flex-direction: column;
            gap: 4px;
            overflow-y: auto;
            max-height: 380px;
        }}

        .gestionale-panel-btn {{
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 8px 10px;
            border: 1px solid var(--border);
            border-radius: 8px;
            background: var(--bg-secondary);
            cursor: pointer;
            transition: all 0.2s ease;
            text-align: left;
            color: var(--text-primary);
        }}

        .gestionale-panel-btn:hover {{
            background: var(--bg-tertiary);
            border-color: var(--accent);
            box-shadow: 0 2px 8px var(--shadow);
            transform: translateX(4px);
        }}

        .gp-btn-icon {{
            font-size: 18px;
            flex-shrink: 0;
            width: 24px;
            text-align: center;
        }}

        .gp-btn-text {{
            font-weight: 600;
            font-size: 13px;
            display: block;
        }}

        .gp-btn-desc {{
            display: none;
        }}

        @media (max-width: 768px) {{
            .gestionale-fab {{
                bottom: 10px;
                right: 10px;
                width: 44px;
                height: 44px;
                font-size: 18px;
            }}
            .gestionale-panel {{
                bottom: 64px;
                right: 10px;
                left: 10px;
                width: auto;
            }}
        }}

        /* ============================================================ */
        /* SETTINGS MODAL                                               */
        /* ============================================================ */
        .settings-modal {{
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.4);
            backdrop-filter: blur(8px);
            -webkit-backdrop-filter: blur(8px);
            z-index: 15000;
            align-items: center;
            justify-content: center;
        }}

        .settings-modal.visible {{
            display: flex;
        }}

        .settings-content {{
            background: var(--bg-primary);
            padding: 0;
            border-radius: 16px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
            max-width: 640px;
            width: 94%;
            max-height: 85vh;
            display: flex;
            flex-direction: column;
            animation: modalSlideUp 0.3s cubic-bezier(0.16, 1, 0.3, 1);
        }}

        .settings-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 18px 24px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            border-radius: 16px 16px 0 0;
            color: white;
            flex-shrink: 0;
        }}

        [data-theme="dark"] .settings-header {{
            background: linear-gradient(135deg, #e94560 0%, #c0392b 100%);
        }}

        .settings-title {{
            font-size: 1.25em;
            font-weight: 700;
        }}

        .settings-close {{
            background: rgba(255,255,255,0.2);
            border: none;
            color: white;
            font-size: 1.3em;
            width: 36px;
            height: 36px;
            border-radius: 50%;
            cursor: pointer;
            transition: background 0.3s;
            display: flex;
            align-items: center;
            justify-content: center;
        }}

        .settings-close:hover {{
            background: rgba(255,255,255,0.35);
        }}

        .settings-tabs {{
            display: flex;
            border-bottom: 1px solid var(--border);
            padding: 0 16px;
            gap: 2px;
            flex-shrink: 0;
            overflow-x: auto;
            background: var(--bg-secondary);
        }}

        .settings-tab {{
            padding: 10px 14px;
            cursor: pointer;
            border: none;
            background: none;
            color: var(--text-secondary);
            font-size: 13px;
            font-weight: 500;
            border-bottom: 3px solid transparent;
            transition: all 0.2s;
            white-space: nowrap;
        }}

        .settings-tab:hover {{
            color: var(--text-primary);
            background: var(--bg-tertiary);
        }}

        .settings-tab.active {{
            color: var(--accent);
            border-bottom-color: var(--accent);
            font-weight: 600;
        }}

        .settings-body {{
            padding: 20px 24px;
            overflow-y: auto;
            flex: 1;
            position: relative;
        }}

        .settings-loading-overlay {{
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: var(--bg-primary);
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            z-index: 10;
            gap: 16px;
            opacity: 1;
            transition: opacity 0.3s ease;
        }}

        .settings-loading-overlay.hidden {{
            opacity: 0;
            pointer-events: none;
        }}

        .settings-spinner {{
            width: 40px;
            height: 40px;
            border: 4px solid var(--border);
            border-top-color: var(--accent);
            border-radius: 50%;
            animation: settingsSpin 0.8s linear infinite;
        }}

        @keyframes settingsSpin {{
            to {{ transform: rotate(360deg); }}
        }}

        .settings-loading-text {{
            font-size: 13px;
            color: var(--text-secondary);
            font-weight: 500;
        }}

        .settings-section {{
            display: none;
        }}

        .settings-section.active {{
            display: block;
        }}

        .settings-section h3 {{
            font-size: 1em;
            font-weight: 600;
            color: var(--text-primary);
            margin: 0 0 14px 0;
            padding-bottom: 8px;
            border-bottom: 1px solid var(--border);
        }}

        .settings-field {{
            margin-bottom: 14px;
        }}

        .settings-field label {{
            display: block;
            font-size: 12px;
            font-weight: 600;
            color: var(--text-secondary);
            margin-bottom: 5px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}

        .settings-field input[type="text"],
        .settings-field input[type="number"],
        .settings-field input[type="date"],
        .settings-field select {{
            width: 100%;
            padding: 9px 12px;
            border: 1px solid var(--border);
            border-radius: 8px;
            background: var(--bg-secondary);
            color: var(--text-primary);
            font-size: 14px;
            box-sizing: border-box;
            transition: border-color 0.2s;
        }}

        .settings-field select option {{
            background: var(--bg-secondary);
            color: var(--text-primary);
        }}

        .settings-field input:focus,
        .settings-field select:focus {{
            outline: none;
            border-color: var(--accent);
            box-shadow: 0 0 0 3px rgba(var(--accent-rgb), 0.15);
        }}

        .settings-field-row {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 12px;
        }}

        .settings-field-row-3 {{
            display: grid;
            grid-template-columns: 1fr 1fr 1fr;
            gap: 12px;
        }}

        .settings-toggle {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 10px 0;
        }}

        .settings-toggle-label {{
            font-size: 14px;
            color: var(--text-primary);
        }}

        .settings-toggle-desc {{
            font-size: 11px;
            color: var(--text-secondary);
            margin-top: 2px;
        }}

        .settings-switch {{
            position: relative;
            width: 44px;
            height: 24px;
            flex-shrink: 0;
        }}

        .settings-switch input {{
            opacity: 0;
            width: 0;
            height: 0;
        }}

        .settings-switch .slider {{
            position: absolute;
            cursor: pointer;
            top: 0; left: 0; right: 0; bottom: 0;
            background: var(--bg-tertiary);
            border-radius: 24px;
            transition: 0.3s;
            border: 1px solid var(--border);
        }}

        .settings-switch .slider:before {{
            content: "";
            position: absolute;
            height: 18px;
            width: 18px;
            left: 2px;
            bottom: 2px;
            background: white;
            border-radius: 50%;
            transition: 0.3s;
            box-shadow: 0 1px 3px rgba(0,0,0,0.2);
        }}

        .settings-switch input:checked + .slider {{
            background: var(--accent);
            border-color: var(--accent);
        }}

        .settings-switch input:checked + .slider:before {{
            transform: translateX(20px);
        }}

        /* Operators table */
        .settings-operators-table {{
            width: 100%;
            border-collapse: separate;
            border-spacing: 0 4px;
        }}

        .settings-operators-table th {{
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: var(--text-secondary);
            font-weight: 600;
            padding: 4px 8px;
            text-align: left;
        }}

        .settings-operators-table td {{
            padding: 4px 8px;
        }}

        .settings-operators-table input[type="text"] {{
            width: 100%;
            padding: 7px 10px;
            border: 1px solid var(--border);
            border-radius: 6px;
            background: var(--bg-secondary);
            color: var(--text-primary);
            font-size: 13px;
            box-sizing: border-box;
        }}

        .settings-operators-table input[type="color"] {{
            width: 36px;
            height: 32px;
            border: 1px solid var(--border);
            border-radius: 6px;
            background: var(--bg-secondary);
            cursor: pointer;
            padding: 2px;
        }}

        .settings-btn-remove {{
            background: none;
            border: none;
            color: #e74c3c;
            cursor: pointer;
            font-size: 18px;
            padding: 4px 8px;
            border-radius: 6px;
            transition: background 0.2s;
        }}

        .settings-btn-remove:hover {{
            background: rgba(231, 76, 60, 0.1);
        }}

        .settings-btn-add {{
            display: flex;
            align-items: center;
            gap: 6px;
            padding: 8px 14px;
            border: 1px dashed var(--border);
            border-radius: 8px;
            background: none;
            color: var(--accent);
            cursor: pointer;
            font-size: 13px;
            font-weight: 500;
            margin-top: 8px;
            transition: all 0.2s;
        }}

        .settings-btn-add:hover {{
            background: rgba(var(--accent-rgb), 0.08);
            border-color: var(--accent);
        }}

        .settings-footer {{
            display: flex;
            justify-content: flex-end;
            gap: 10px;
            padding: 16px 24px;
            border-top: 1px solid var(--border);
            background: var(--bg-secondary);
            border-radius: 0 0 16px 16px;
            flex-shrink: 0;
        }}

        .settings-btn-cancel {{
            padding: 10px 20px;
            border: 1px solid var(--border);
            border-radius: 8px;
            background: var(--bg-tertiary);
            color: var(--text-primary);
            cursor: pointer;
            font-size: 13px;
            font-weight: 500;
            transition: all 0.2s;
        }}

        .settings-btn-cancel:hover {{
            background: var(--bg-primary);
        }}

        .settings-btn-save {{
            padding: 10px 24px;
            border: none;
            border-radius: 8px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            cursor: pointer;
            font-size: 13px;
            font-weight: 600;
            transition: all 0.2s;
            box-shadow: 0 2px 8px rgba(102, 126, 234, 0.3);
        }}

        [data-theme="dark"] .settings-btn-save {{
            background: linear-gradient(135deg, #e94560 0%, #c0392b 100%);
            box-shadow: 0 2px 8px rgba(233, 69, 96, 0.3);
        }}

        .settings-btn-save:hover {{
            transform: translateY(-1px);
            box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
        }}

        [data-theme="dark"] .settings-btn-save:hover {{
            box-shadow: 0 4px 12px rgba(233, 69, 96, 0.4);
        }}

        .settings-btn-save:disabled {{
            opacity: 0.6;
            cursor: not-allowed;
            transform: none;
        }}

        .settings-status {{
            padding: 10px 14px;
            border-radius: 8px;
            font-size: 13px;
            margin-bottom: 14px;
            display: none;
        }}

        .settings-status.success {{
            display: block;
            background: rgba(46, 204, 113, 0.1);
            color: #27ae60;
            border: 1px solid rgba(46, 204, 113, 0.3);
        }}

        .settings-status.error {{
            display: block;
            background: rgba(231, 76, 60, 0.1);
            color: #e74c3c;
            border: 1px solid rgba(231, 76, 60, 0.3);
        }}

        /* Admin users table */
        .admin-users-table {{
            width: 100%;
            border-collapse: separate;
            border-spacing: 0 3px;
            font-size: 13px;
        }}

        .admin-users-table th {{
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: var(--text-secondary);
            padding: 6px 8px;
            text-align: left;
            font-weight: 600;
        }}

        .admin-users-table td {{
            padding: 8px;
            background: var(--bg-secondary);
            border-top: 1px solid var(--border);
            border-bottom: 1px solid var(--border);
        }}

        .admin-users-table td:first-child {{
            border-left: 1px solid var(--border);
            border-radius: 8px 0 0 8px;
        }}

        .admin-users-table td:last-child {{
            border-right: 1px solid var(--border);
            border-radius: 0 8px 8px 0;
        }}

        .admin-users-table .user-username {{
            font-weight: 600;
        }}

        .admin-badge {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 10px;
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.3px;
        }}

        .admin-badge-admin {{
            background: rgba(102, 126, 234, 0.15);
            color: #667eea;
        }}

        [data-theme="dark"] .admin-badge-admin {{
            background: rgba(233, 69, 96, 0.15);
            color: #e94560;
        }}

        .admin-badge-user {{
            background: rgba(149, 165, 166, 0.15);
            color: #95a5a6;
        }}

        .admin-province-tag {{
            display: inline-block;
            padding: 2px 7px;
            border-radius: 6px;
            font-size: 11px;
            margin: 1px 2px;
            background: rgba(52, 152, 219, 0.12);
            color: #3498db;
            font-weight: 500;
        }}

        .admin-actions-btn {{
            background: none;
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 4px 8px;
            cursor: pointer;
            font-size: 13px;
            color: var(--text-primary);
            transition: all 0.15s;
        }}

        .admin-actions-btn:hover {{
            background: var(--bg-tertiary);
        }}

        .admin-action-menu {{
            position: fixed;
            background: var(--bg-primary);
            border: 1px solid var(--border);
            border-radius: 10px;
            box-shadow: 0 8px 24px rgba(0,0,0,0.15);
            z-index: 16000;
            min-width: 180px;
            padding: 6px;
            display: none;
        }}

        .admin-action-menu.visible {{
            display: block;
        }}

        .admin-action-item {{
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 8px 12px;
            border: none;
            background: none;
            width: 100%;
            text-align: left;
            cursor: pointer;
            font-size: 13px;
            color: var(--text-primary);
            border-radius: 6px;
            transition: background 0.15s;
        }}

        .admin-action-item:hover {{
            background: var(--bg-secondary);
        }}

        .admin-action-item.danger {{
            color: #e74c3c;
        }}

        .admin-action-item.danger:hover {{
            background: rgba(231, 76, 60, 0.08);
        }}

        .settings-tab-admin {{
            border-left: 1px solid var(--border);
            margin-left: 4px;
            padding-left: 12px !important;
        }}

        /* Column mapping table */
        .settings-colonne-table {{
            width: 100%;
            border-collapse: separate;
            border-spacing: 0 4px;
        }}

        .settings-colonne-table th {{
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: var(--text-secondary);
            font-weight: 600;
            padding: 6px 8px;
            text-align: left;
        }}

        .settings-colonne-table td {{
            padding: 4px 8px;
            vertical-align: middle;
        }}

        .settings-colonne-table .col-label {{
            font-weight: 600;
            font-size: 13px;
            color: var(--text-primary);
            white-space: nowrap;
        }}

        .settings-colonne-table .col-desc {{
            font-size: 11px;
            color: var(--text-secondary);
            margin-top: 2px;
        }}

        .settings-colonne-table select {{
            width: 100%;
            padding: 7px 10px;
            border: 1px solid var(--border);
            border-radius: 6px;
            background: var(--bg-secondary);
            color: var(--text-primary);
            font-size: 13px;
            box-sizing: border-box;
            cursor: pointer;
        }}

        .settings-colonne-table select option {{
            background: var(--bg-secondary);
            color: var(--text-primary);
        }}

        .settings-colonne-table select:focus {{
            border-color: var(--accent);
            outline: none;
            box-shadow: 0 0 0 3px rgba(var(--accent-rgb), 0.1);
        }}

        .settings-colonne-table select.col-mapped {{
            border-color: #27ae60;
            background: rgba(46, 204, 113, 0.05);
        }}

        .settings-colonne-table select.col-unmapped {{
            border-color: #e67e22;
            background: rgba(230, 126, 34, 0.05);
        }}

        .settings-colonne-table select.col-custom {{
            border-color: #3498db;
            background: rgba(52, 152, 219, 0.05);
        }}

        .settings-colonne-table .col-status {{
            font-size: 14px;
            text-align: center;
            width: 30px;
        }}

        .settings-colonne-info {{
            display: flex;
            gap: 16px;
            padding: 10px 14px;
            background: var(--bg-secondary);
            border-radius: 8px;
            margin-bottom: 12px;
            font-size: 12px;
            color: var(--text-secondary);
            flex-wrap: wrap;
        }}

        .settings-colonne-info span {{
            display: flex;
            align-items: center;
            gap: 4px;
        }}

        .settings-colonne-loading {{
            text-align: center;
            padding: 30px 0;
            color: var(--text-secondary);
            font-size: 14px;
        }}

        .settings-btn-reset-colonne {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 6px 12px;
            border: 1px solid var(--border);
            border-radius: 6px;
            background: none;
            color: var(--text-secondary);
            cursor: pointer;
            font-size: 12px;
            transition: all 0.2s;
            margin-top: 10px;
        }}

        .settings-btn-reset-colonne:hover {{
            background: rgba(231, 76, 60, 0.08);
            color: #e74c3c;
            border-color: #e74c3c;
        }}

        @media (max-width: 640px) {{
            .settings-content {{
                max-width: 100%;
                width: 100%;
                height: 100%;
                max-height: 100vh;
                border-radius: 0;
            }}
            .settings-header {{ border-radius: 0; }}
            .settings-footer {{ border-radius: 0; }}
            .settings-field-row {{ grid-template-columns: 1fr; }}
            .settings-field-row-3 {{ grid-template-columns: 1fr 1fr; }}
        }}

        /* Print Preview Modal */
        .print-preview-modal {{
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.6);
            backdrop-filter: blur(8px);
            -webkit-backdrop-filter: blur(8px);
            z-index: 20000;
            align-items: center;
            justify-content: center;
        }}

        .print-preview-modal.visible {{
            display: flex;
        }}

        .print-preview-content {{
            background: white;
            border-radius: 12px;
            width: 95%;
            height: 95%;
            display: flex;
            flex-direction: column;
            overflow: hidden;
            animation: modalSlideUp 0.3s cubic-bezier(0.16, 1, 0.3, 1);
        }}

        .print-preview-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 16px 24px;
            background: #f8f9fa;
            border-bottom: 1px solid #e9ecef;
        }}

        .print-preview-title {{
            font-size: 1.2em;
            font-weight: 700;
            color: #333;
        }}

        .print-preview-actions {{
            display: flex;
            gap: 10px;
        }}

        .print-preview-actions button {{
            padding: 10px 20px;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-weight: 600;
            font-size: 14px;
            transition: all 0.3s;
        }}

        .btn-print-confirm {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }}

        .btn-print-confirm:hover {{
            transform: scale(1.02);
        }}

        .btn-print-cancel {{
            background: #e9ecef;
            color: #495057;
        }}

        .btn-print-cancel:hover {{
            background: #dee2e6;
        }}

        .print-preview-map {{
            flex: 1;
            position: relative;
        }}

        #printMap {{
            width: 100%;
            height: 100%;
        }}

        /* Print-specific marker styles */
        .print-cluster-marker {{
            border: 3px solid white;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            font-size: 12px;
            color: white;
            box-shadow: 0 2px 8px rgba(0,0,0,0.4);
        }}

        .print-cluster-badge {{
            position: absolute;
            top: -8px;
            right: -8px;
            background: #ffc107;
            color: #333;
            font-size: 10px;
            font-weight: bold;
            padding: 2px 6px;
            border-radius: 10px;
            box-shadow: 0 1px 4px rgba(0,0,0,0.3);
            min-width: 18px;
            text-align: center;
        }}

        .print-eneltel-label {{
            position: absolute;
            background: white;
            color: #333;
            padding: 4px 10px;
            border-radius: 6px;
            font-size: 13px;
            font-weight: 700;
            white-space: nowrap;
            z-index: 1000;
            box-shadow: 0 2px 8px rgba(0,0,0,0.25);
            border: 2px solid #333;
            font-family: 'Consolas', 'Monaco', monospace;
            letter-spacing: 1px;
        }}

        /* Lista per cluster multipli */
        .print-cluster-list {{
            position: absolute;
            background: white;
            border-radius: 8px;
            z-index: 1000;
            box-shadow: 0 3px 12px rgba(0,0,0,0.2);
            border: 2px solid #333;
            overflow: hidden;
        }}

        .print-cluster-list-item {{
            display: flex;
            align-items: center;
            padding: 3px 8px;
            font-size: 12px;
            font-weight: 700;
            font-family: 'Consolas', 'Monaco', monospace;
            letter-spacing: 1px;
            color: #333;
            border-bottom: 1px solid #eee;
        }}

        .print-cluster-list-item:last-child {{
            border-bottom: none;
        }}

        .print-cluster-list-item .color-dot {{
            width: 8px;
            height: 8px;
            border-radius: 50%;
            margin-right: 6px;
            flex-shrink: 0;
        }}

        .print-label-line {{
            stroke: #333;
            stroke-width: 2;
            fill: none;
            stroke-dasharray: 4, 2;
        }}

        /* Freccia per cluster */
        .print-label-arrow {{
            stroke: #555;
            stroke-width: 2;
            fill: none;
        }}

        /* Print zoom slider specific */
        .print-zoom-slider {{
            bottom: 15px;
        }}

        /* Print media styles */
        @media print {{
            body * {{
                visibility: hidden;
            }}
            .print-preview-map, .print-preview-map * {{
                visibility: visible;
            }}
            .print-preview-map {{
                position: absolute;
                left: 0;
                top: 0;
                width: 100%;
                height: 100%;
            }}
            .print-preview-header {{
                display: none !important;
            }}
            .leaflet-control-zoom,
            .leaflet-control-attribution,
            .zoom-slider-container {{
                display: none !important;
            }}
        }}
        
        /* Loading Overlay */
        .page-loading-overlay {{
            position: fixed;
            top: 0;
            left: 0;
            width: 100vw;
            height: 100vh;
            background: var(--bg-primary, #1a1a2e);
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            z-index: 100000;
            gap: 20px;
            opacity: 1;
            transition: opacity 0.5s ease;
        }}

        .page-loading-overlay.fade-out {{
            opacity: 0;
            pointer-events: none;
        }}

        .page-loading-spinner {{
            width: 52px;
            height: 52px;
            border: 5px solid var(--border, #333);
            border-top-color: var(--accent, #6c5ce7);
            border-radius: 50%;
            animation: pageSpinAnim 0.9s linear infinite;
        }}

        @keyframes pageSpinAnim {{
            to {{ transform: rotate(360deg); }}
        }}

        .page-loading-text {{
            font-size: 15px;
            color: var(--text-secondary, #999);
            font-weight: 500;
            letter-spacing: 0.5px;
        }}

        .page-loading-sub {{
            font-size: 12px;
            color: var(--text-secondary, #666);
            opacity: 0.7;
        }}

        /* CSS Gestionale */
        {genera_css_gestionale()}

        /* ============================================ */
        /* MODALITÀ PIANIFICAZIONE                      */
        /* ============================================ */
        .planning-mode .header-top,
        .planning-mode .toggles,
        .planning-mode .filters,
        .planning-mode .breadcrumb,
        .planning-mode .stats-bar {{
            display: none !important;
        }}
        .planning-mode .header {{
            padding: 0;
            border: none;
            background: none;
        }}
        .planning-mode .sidebar {{
            width: 370px;
        }}
        /* Planning header strip */
        .planning-header {{
            display: none;
            align-items: center;
            gap: 8px;
            padding: 7px 12px;
            background: linear-gradient(135deg, rgba(6,182,212,0.15) 0%, rgba(6,182,212,0.05) 100%);
            border-bottom: 1px solid var(--border);
        }}
        .planning-mode .planning-header {{
            display: flex;
        }}
        .planning-header h2 {{
            margin: 0;
            font-size: 0.92rem;
            color: var(--accent-secondary, #06b6d4);
            white-space: nowrap;
        }}
        .planning-header .btn-exit-planning {{
            margin-left: auto;
            padding: 3px 10px;
            border-radius: 6px;
            border: 1px solid var(--border);
            background: var(--bg-tertiary);
            color: var(--text);
            cursor: pointer;
            font-size: 0.75rem;
            transition: all 0.15s;
        }}
        .planning-header .btn-exit-planning:hover {{
            background: var(--danger, #ef4444);
            color: #fff;
            border-color: var(--danger, #ef4444);
        }}
        /* Planning filters panel */
        .planning-filters {{
            display: none;
            flex-direction: column;
            gap: 0;
            padding: 0;
            border-bottom: 1px solid var(--border);
            max-height: 50vh;
            overflow-y: auto;
        }}
        .planning-mode .planning-filters {{
            display: flex;
        }}
        .planning-filter-section {{
            padding: 6px 12px;
        }}
        .planning-filter-section + .planning-filter-section {{
            border-top: 1px solid var(--border);
        }}
        .planning-filter-section-title {{
            font-size: 0.7rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: var(--text-muted);
            margin-bottom: 5px;
        }}
        .planning-filter-section-title.pian {{
            color: #25d366;
        }}
        .planning-filter-grid {{
            display: grid;
            grid-template-columns: auto 1fr;
            gap: 4px 8px;
            align-items: center;
        }}
        .planning-filter-grid label {{
            font-size: 0.72rem;
            color: var(--text-muted);
            white-space: nowrap;
        }}
        .planning-filter-grid input,
        .planning-filter-grid select {{
            width: 100%;
            min-width: 0;
            padding: 4px 8px;
            border-radius: 6px;
            border: 1px solid var(--border);
            background: var(--bg-tertiary);
            color: var(--text);
            font-size: 0.78rem;
            box-sizing: border-box;
        }}
        .planning-filter-grid input:focus,
        .planning-filter-grid select:focus {{
            border-color: var(--accent-secondary, #06b6d4);
            outline: none;
        }}
        .planning-filter-chips {{
            display: flex;
            flex-wrap: wrap;
            gap: 3px;
        }}
        .planning-filter-chip {{
            padding: 2px 8px;
            border-radius: 10px;
            border: 1px solid var(--border);
            background: var(--bg-tertiary);
            color: var(--text-muted);
            font-size: 0.7rem;
            cursor: pointer;
            transition: all 0.15s;
            user-select: none;
        }}
        .planning-filter-chip:hover {{
            border-color: var(--accent-secondary, #06b6d4);
        }}
        .planning-filter-chip.active {{
            background: rgba(6, 182, 212, 0.2);
            border-color: var(--accent-secondary, #06b6d4);
            color: var(--text);
        }}
        .planning-toggle-label {{
            font-size: 0.78rem;
            color: var(--text-muted);
            padding: 2px 6px;
            border-radius: 8px;
            transition: color 0.15s;
        }}
        .planning-toggle-label:hover {{
            color: var(--text);
        }}
        .planning-toggle-label input[type="checkbox"] {{
            accent-color: var(--accent-secondary, #06b6d4);
            width: 14px;
            height: 14px;
        }}
        .planning-stats {{
            display: none;
            justify-content: space-between;
            align-items: center;
            padding: 3px 12px;
            font-size: 0.72rem;
            color: var(--text-muted);
            border-bottom: 1px solid var(--border);
        }}
        .planning-mode .planning-stats {{
            display: flex;
        }}
        .planning-stats strong {{
            color: var(--accent-secondary, #06b6d4);
        }}
        /* Selection toolbar */
        .planning-selection-bar {{
            display: none;
            align-items: center;
            gap: 4px;
            padding: 5px 8px;
            background: linear-gradient(135deg, rgba(6,182,212,0.08) 0%, rgba(37,211,102,0.05) 100%);
            border-bottom: 1px solid var(--border);
            flex-wrap: nowrap;
            min-height: 0;
        }}
        .planning-mode .planning-selection-bar {{
            display: flex;
        }}
        .planning-sel-tools {{
            display: flex;
            gap: 3px;
            flex-shrink: 0;
        }}
        .planning-sel-tools button {{
            padding: 3px 7px;
            border-radius: 6px;
            border: 1px solid var(--border);
            background: var(--bg-tertiary);
            color: var(--text);
            cursor: pointer;
            font-size: 0.7rem;
            transition: all 0.15s;
            white-space: nowrap;
        }}
        .planning-sel-tools button:hover {{
            border-color: var(--accent-secondary, #06b6d4);
        }}
        .planning-sel-tools button.active {{
            background: rgba(6, 182, 212, 0.25);
            border-color: var(--accent-secondary, #06b6d4);
            color: #fff;
        }}
        .planning-sel-count {{
            font-size: 0.72rem;
            color: var(--text);
            margin-left: auto;
            white-space: nowrap;
        }}
        .planning-sel-count strong {{
            color: var(--accent-secondary, #06b6d4);
        }}
        .btn-assign-bulk {{
            padding: 4px 10px;
            border-radius: 6px;
            border: none;
            background: linear-gradient(135deg, #06b6d4 0%, #0891b2 100%);
            color: #fff;
            cursor: pointer;
            font-size: 0.75rem;
            font-weight: 600;
            transition: all 0.15s;
            white-space: nowrap;
            flex-shrink: 0;
        }}
        .btn-assign-bulk:hover {{
            box-shadow: 0 2px 8px rgba(6,182,212,0.3);
        }}
        .btn-assign-bulk:disabled {{
            opacity: 0.4;
            cursor: not-allowed;
        }}
        /* Planning contatore card in sidebar */
        .planning-card {{
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 6px 8px;
            margin-bottom: 3px;
            cursor: pointer;
            transition: all 0.15s;
            font-size: 0.78rem;
        }}
        .planning-card:hover {{
            border-color: var(--accent-secondary, #06b6d4);
        }}
        .planning-card.selected {{
            background: rgba(6, 182, 212, 0.12);
            border-color: var(--accent-secondary, #06b6d4);
        }}
        .planning-card.pianificato {{
            border-left: 3px solid #25d366;
        }}
        .planning-card.pianificato.selected {{
            border-left-color: #25d366;
        }}
        .planning-card .pc-header {{
            display: flex;
            align-items: center;
            gap: 4px;
        }}
        .planning-card .pc-checkbox {{
            width: 14px;
            height: 14px;
            accent-color: #06b6d4;
            cursor: pointer;
            flex-shrink: 0;
        }}
        .planning-card .pc-nome {{
            font-weight: 600;
            flex: 1;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            font-size: 0.76rem;
        }}
        .planning-card .pc-badges {{
            display: flex;
            gap: 3px;
            flex-shrink: 0;
        }}
        .planning-card .pc-badge {{
            padding: 1px 6px;
            border-radius: 6px;
            font-size: 0.68rem;
            font-weight: 600;
        }}
        .planning-card .pc-badge.phone {{
            background: rgba(37, 211, 102, 0.2);
            color: #25d366;
        }}
        .planning-card .pc-badge.no-phone {{
            background: rgba(239, 68, 68, 0.15);
            color: #ef4444;
        }}
        .planning-card .pc-badge.no-date {{
            background: rgba(6, 182, 212, 0.2);
            color: #06b6d4;
        }}
        .planning-card .pc-badge.old-date {{
            background: rgba(245, 158, 11, 0.2);
            color: #f59e0b;
        }}
        .planning-card .pc-detail {{
            font-size: 0.75rem;
            color: var(--text-muted);
            margin-top: 2px;
            padding-left: 22px;
        }}
        /* Bulk assign modal */
        .bulk-assign-overlay {{
            display: none;
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0,0,0,0.5);
            backdrop-filter: blur(4px);
            z-index: 10000;
            align-items: center;
            justify-content: center;
        }}
        .bulk-assign-overlay.visible {{
            display: flex;
        }}
        .bulk-assign-modal {{
            background: var(--bg-primary);
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 24px;
            width: 380px;
            max-width: 90vw;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
        }}
        .bulk-assign-modal h3 {{
            margin: 0 0 16px 0;
            color: var(--accent-secondary, #06b6d4);
            font-size: 1.1rem;
        }}
        .bulk-assign-field {{
            margin-bottom: 14px;
        }}
        .bulk-assign-field label {{
            display: block;
            font-size: 0.82rem;
            color: var(--text-muted);
            margin-bottom: 4px;
        }}
        .bulk-assign-field input,
        .bulk-assign-field select {{
            width: 100%;
            padding: 10px 12px;
            border-radius: 10px;
            border: 1px solid var(--border);
            background: var(--bg-tertiary);
            color: var(--text);
            font-size: 0.9rem;
            box-sizing: border-box;
        }}
        .bulk-assign-field input:focus,
        .bulk-assign-field select:focus {{
            border-color: var(--accent-secondary, #06b6d4);
            outline: none;
        }}
        .bulk-assign-actions {{
            display: flex;
            gap: 10px;
            margin-top: 18px;
        }}
        .bulk-assign-actions button {{
            flex: 1;
            padding: 10px;
            border-radius: 10px;
            border: none;
            cursor: pointer;
            font-weight: 600;
            font-size: 0.9rem;
            transition: all 0.15s;
        }}
        .bulk-assign-actions .btn-cancel {{
            background: var(--bg-tertiary);
            color: var(--text);
            border: 1px solid var(--border);
        }}
        .bulk-assign-actions .btn-confirm {{
            background: linear-gradient(135deg, #06b6d4 0%, #0891b2 100%);
            color: #fff;
        }}
        .bulk-assign-actions .btn-confirm:hover {{
            transform: scale(1.02);
            box-shadow: 0 4px 12px rgba(6,182,212,0.3);
        }}
        /* Map selection overlays */
        .selection-rect {{
            position: absolute;
            border: 2px dashed #06b6d4;
            background: rgba(6, 182, 212, 0.1);
            pointer-events: none;
            z-index: 1000;
        }}
        .selection-lasso {{
            position: absolute;
            z-index: 1000;
            pointer-events: none;
        }}
        .map-selection-active {{
            cursor: crosshair !important;
        }}
        .map-selection-single {{
            cursor: pointer !important;
        }}
        /* Selected marker highlight */
        .marker-selected {{
            filter: drop-shadow(0 0 6px #06b6d4) drop-shadow(0 0 12px #06b6d4);
        }}
        /* Planning mode toggle button (normal mode) */
        .btn-planning-mode {{
            padding: 5px 10px;
            border-radius: 8px;
            border: 1px solid rgba(6,182,212,0.4);
            background: rgba(6,182,212,0.1);
            color: var(--accent-secondary, #06b6d4);
            cursor: pointer;
            font-size: 0.78rem;
            font-weight: 600;
            transition: all 0.15s;
        }}
        .btn-planning-mode:hover {{
            background: rgba(6,182,212,0.2);
            border-color: #06b6d4;
        }}
    </style>
</head>
<body data-theme="dark">
    <script>
        // Applica tema salvato immediatamente
        var st = localStorage.getItem('algeco_theme');
        if (st) document.body.setAttribute('data-theme', st);
    </script>
    <div class="page-loading-overlay" id="pageLoadingOverlay">
        <div class="page-loading-spinner"></div>
        <div class="page-loading-text">Caricamento mappa...</div>
        <div class="page-loading-sub">{PROVINCIA_ATTIVA}</div>
    </div>
    <div class="container">
        <div class="sidebar" id="sidebar">
            <div class="header">
                <div class="header-top">
                    <div>
                        <h1>🗺️ Mappa Contatori</h1>
                        <p>{PROVINCIA_ATTIVA} - <span id="updateTime">{timestamp[6:8]}/{timestamp[4:6]}/{timestamp[0:4]} {timestamp[8:10]}:{timestamp[10:12]}</span></p>
                    </div>
                </div>
                <div class="toggles">
                    <button class="toggle-btn active" id="viewToggle" onclick="toggleView()">🗺️ Zone</button>
                    <button class="toggle-btn" id="themeToggle" onclick="toggleTheme()">🌙 Tema</button>
                    <button class="toggle-btn" id="searchModeToggle" onclick="toggleAdvancedSearchModal()">🔍 Ricerca Utente</button>
                    <button class="toggle-btn" id="addressSearchToggle" onclick="toggleAddressSearchModal()">📍 Cerca Indirizzo</button>
                    <button class="toggle-btn" id="refreshBtn" onclick="refreshDati()" title="Aggiorna dati">🔄</button>
                    <button class="btn-planning-mode" id="btnPlanningMode" onclick="enterPlanningMode()" title="Modalità Pianificazione">📋 Pianifica</button>
                </div>
            </div>
            
            <!-- Planning Mode Header -->
            <div class="planning-header" id="planningHeader">
                <h2>📋 Pianificazione</h2>
                <span id="planningBadge" style="font-size: 0.78rem; color: var(--text-muted);">0 schedulabili</span>
                <button class="btn-exit-planning" onclick="exitPlanningMode()">✕ Esci</button>
            </div>
            
            <!-- Planning Filters -->
            <div class="planning-filters" id="planningFilters">
                <!-- Toggle schedulabili/pianificati -->
                <div class="planning-filter-section">
                    <div class="planning-filter-chips" style="gap: 10px; justify-content: center; padding: 4px 0;">
                        <label class="planning-toggle-label" style="display:flex;align-items:center;gap:4px;cursor:pointer;user-select:none;">
                            <input type="checkbox" id="planShowSchedulabili" checked onchange="applyPlanningFilters()">
                            <span>📋 Schedulabili</span>
                        </label>
                        <label class="planning-toggle-label" style="display:flex;align-items:center;gap:4px;cursor:pointer;user-select:none;">
                            <input type="checkbox" id="planShowPianificati" checked onchange="onPlanTogglePianificati()">
                            <span>📅 Pianificati</span>
                        </label>
                    </div>
                </div>
                <!-- Sezione filtri comuni -->
                <div class="planning-filter-section">
                    <div class="planning-filter-grid">
                        <label>👤 Nome</label>
                        <input type="text" id="planFilterNome" placeholder="Cerca nominativo..." autocomplete="off" oninput="applyPlanningFilters()">
                        <label>🗺️ Zona</label>
                        <select id="planFilterZona" onchange="onPlanZonaChange()"><option value="">Tutte le zone</option></select>
                        <label>🏘️ Comune</label>
                        <select id="planFilterComune" onchange="applyPlanningFilters()"><option value="">Tutti i comuni</option></select>
                        <label>📞 Telefono</label>
                        <div class="planning-filter-chips">
                            <span class="planning-filter-chip active" data-plan-tel="tutti" onclick="setPlanFilterTel('tutti')">Tutti</span>
                            <span class="planning-filter-chip" data-plan-tel="con" onclick="setPlanFilterTel('con')">Con ☎️</span>
                            <span class="planning-filter-chip" data-plan-tel="senza" onclick="setPlanFilterTel('senza')">Senza</span>
                        </div>
                        <label>⚡ Tipologia</label>
                        <select id="planFilterTipologia" onchange="applyPlanningFilters()"><option value="">Tutte le tipologie</option></select>
                        <label>📍 Posizione</label>
                        <select id="planFilterPosizione" onchange="applyPlanningFilters()"><option value="">Tutte le posizioni</option></select>
                        <label>🏷️ LCL</label>
                        <select id="planFilterLCL" onchange="onPlanLCLChange()"><option value="">Tutti LCL</option></select>
                    </div>
                </div>
                <!-- Sezione filtri pianificati -->
                <div class="planning-filter-section" id="planFiltersPianificatiSection">
                    <div class="planning-filter-section-title pian">📅 Filtri pianificati</div>
                    <div class="planning-filter-grid">
                        <label>📅 Data</label>
                        <select id="planFilterData" onchange="applyPlanningFilters()"><option value="">Tutte le date</option></select>
                        <label>👷 Operatore</label>
                        <select id="planFilterOperatore" onchange="applyPlanningFilters()"><option value="">Tutti gli operatori</option></select>
                    </div>
                </div>
            </div>

            <div class="planning-stats" id="planningStatsRow">
                <span id="planningFilteredCount">Filtrati: <strong>0</strong></span>
                <span id="planningSelectedCount">Selezionati: <strong>0</strong></span>
            </div>
            
            <!-- Selection Toolbar -->
            <div class="planning-selection-bar" id="planningSelBar">
                <div class="planning-sel-tools">
                    <button id="btnSelSingle" onclick="startSelectionMode('single')" title="Selezione singola (clicca marker)">👆</button>
                    <button id="btnSelRect" onclick="startSelectionMode('rect')" title="Selezione rettangolo">▭</button>
                    <button id="btnSelLasso" onclick="startSelectionMode('lasso')" title="Selezione libera">✂</button>
                    <button id="btnSelAll" onclick="selectAllVisible()" title="Seleziona tutti">☑</button>
                    <button id="btnSelNone" onclick="deselectAll()" title="Deseleziona tutti">☐</button>
                </div>
                <span class="planning-sel-count" id="planSelCount"><strong>0</strong> sel.</span>
                <button class="btn-assign-bulk" id="btnAssignBulk" onclick="openBulkAssignModal()" disabled>📅 Assegna</button>
            </div>

            <div class="breadcrumb" id="breadcrumb">
                <button class="btn-back" onclick="tornaAlleZone()" title="Torna alle zone">←</button>
                <span class="breadcrumb-text" id="breadcrumbText"></span>
                <div class="date-navigation" id="dateNavigation">
                    <button class="btn-date-nav" id="btnPrevDate" onclick="navigateDate(-1)" title="Data precedente">◀</button>
                    <button class="btn-date-current" id="btnCurrentDate" onclick="openDatePicker()" title="Seleziona data"><span id="currentDateText"></span></button>
                    <button class="btn-date-nav" id="btnNextDate" onclick="navigateDate(1)" title="Data successiva">▶</button>
                </div>
            </div>
            
            <div class="filters">
                <div class="search-row">
                    <div class="search-box">
                        <input type="text" id="searchInput" placeholder="🔍 Cerca..." autocomplete="off">
                        <button class="search-clear" id="searchClear" onclick="clearSearch()">✕</button>
                    </div>
                    <div class="lcl-filter">
                        <select id="lclFilter" onchange="applicaFiltri()">
                            <option value="">🏷️ Tutti LCL</option>
                            {lcl_options_html}
                        </select>
                    </div>
                </div>
                <div class="detail-filters" id="detailFilters" style="display: none;">
                    <div class="detail-operatore-container">
                        <div class="detail-operatore-trigger" id="detailOperatoreTrigger" onclick="toggleDetailOperatoreDropdown()">👥 Tutti gli operatori</div>
                        <div class="detail-operatore-dropdown" id="detailOperatoreDropdown"></div>
                    </div>
                </div>
                <div class="search-row" id="dataFilterRow" style="margin-top: 10px;">
                    <div class="search-box">
                        <select id="dataFilter" onchange="onDataFilterChange()">
                            <option value="">📅 Tutte le date</option>
                        </select>
                    </div>
                    <div class="operatore-filter-container" id="operatoreFilterContainer">
                        <div class="operatore-multi-trigger" id="operatoreMultiTrigger" onclick="toggleOperatoreDropdown()">👥 Tutti gli operatori</div>
                        <div class="operatore-multi-dropdown" id="operatoreMultiDropdown"></div>
                    </div>
                </div>
                <!-- schedulabili panel rimosso: si accede dalla pianificazione -->

                <!-- Advanced Search Panel -->
                <div class="advanced-search-panel" id="advancedSearchPanel">
                    <div class="advanced-search-grid">
                        <div class="advanced-search-field">
                            <label>👤 Nominativo</label>
                            <input type="text" id="searchNominativo" placeholder="Es: Mario Rossi" autocomplete="off">
                        </div>
                        <div class="advanced-search-field">
                            <label>⚡ POD</label>
                            <input type="text" id="searchPod" placeholder="Es: IT001E..." autocomplete="off">
                        </div>
                        <div class="advanced-search-field">
                            <label>🔢 Eneltel</label>
                            <input type="text" id="searchEneltel" placeholder="Es: 12345678" autocomplete="off">
                        </div>
                        <div class="advanced-search-field">
                            <label>🏘️ Comune</label>
                            <input type="text" id="searchComune" placeholder="Es: Torino" autocomplete="off">
                        </div>
                        <div class="advanced-search-field">
                            <label>📍 Via</label>
                            <input type="text" id="searchVia" placeholder="Es: Via Roma" autocomplete="off">
                        </div>
                        <div class="advanced-search-field">
                            <label>📱 Telefono</label>
                            <input type="text" id="searchTelefono" placeholder="Es: 3331234567" autocomplete="off">
                        </div>
                    </div>
                    <div class="advanced-search-actions">
                        <button class="btn-clear-advanced" onclick="clearAdvancedSearch()">🗑️ Pulisci</button>
                        <button class="btn-apply-advanced" onclick="applicaRicercaAvanzata()">🔍 Cerca</button>
                    </div>
                </div>
            </div>
            
            <div class="results" id="results"></div>
            
            <div class="stats-bar" id="statsBar">
                <div class="stat-item">
                    <div class="stat-value" id="statZone">0</div>
                    <div class="stat-label">Zone</div>
                </div>
                <div class="stat-item">
                    <div class="stat-value" id="statContatori">0</div>
                    <div class="stat-label">Contatori</div>
                </div>
                <div class="stat-item">
                    <div class="stat-value" id="statAppuntamenti">0</div>
                    <div class="stat-label">Appuntamenti</div>
                </div>
            </div>
        </div>
        
        <button class="sidebar-toggle" id="sidebarToggle" onclick="toggleSidebar()">◀</button>

        <div id="map"></div>

        <!-- Zoom Slider -->
        <div class="zoom-slider-container" id="zoomSliderContainer">
            <span class="zoom-label" id="zoomOut" title="Zoom out">−</span>
            <input type="range" id="zoomSlider" min="5" max="19" step="0.25" value="10">
            <span class="zoom-label" id="zoomIn" title="Zoom in">+</span>
            <span class="zoom-value" id="zoomValue">10.0x</span>
        </div>
    </div>

    <!-- Bulk Assign Modal (Planning Mode) -->
    <div class="bulk-assign-overlay" id="bulkAssignOverlay" onclick="if(event.target===this) closeBulkAssignModal()">
        <div class="bulk-assign-modal">
            <h3>📅 Assegna Data e Operatore</h3>
            <p style="font-size: 0.82rem; color: var(--text-muted); margin: 0 0 16px 0;" id="bulkAssignCount">0 contatori selezionati</p>
            <div class="bulk-assign-field">
                <label>📅 Data Pianificata</label>
                <input type="date" id="bulkAssignData">
            </div>
            <div class="bulk-assign-field">
                <label>👷 Operatore</label>
                <select id="bulkAssignOperatore">
                    <option value="">— Nessuno (non modificare) —</option>
                </select>
            </div>
            <div class="bulk-assign-actions">
                <button class="btn-cancel" onclick="closeBulkAssignModal()">Annulla</button>
                <button class="btn-confirm" id="btnBulkConfirm" onclick="executeBulkAssign()">✅ Assegna</button>
            </div>
        </div>
    </div>

    <div class="update-indicator" id="updateIndicator">🔄 Aggiornamento...</div>
    <div class="mappa-toast" id="mappaToast"></div>

    <!-- Print Button -->
    <button class="print-button" id="printButton" onclick="openPrintPreview()">
        🖨️ Stampa Mappa
    </button>

    <!-- Print Tappe Button -->
    <button class="print-tappe-button hidden" id="printTappeButton" onclick="stampaTappe()">
        📋 Stampa Tappe
    </button>

    <!-- Gestionale Button -->
    <button class="gestionale-fab" id="gestionaleFab" onclick="toggleGestionalePanel()" title="Strumenti Gestionale">
        ⚙️
    </button>

    <!-- Gestionale Panel -->
    <div class="gestionale-panel" id="gestionalePanel">
        <div class="gestionale-panel-header">
            <div class="gestionale-panel-title">⚙️ Gestionale</div>
            <button class="gestionale-panel-close" onclick="toggleGestionalePanel()">✕</button>
        </div>
        <div class="gestionale-panel-body">
            <button class="gestionale-panel-btn gp-btn-lettere" onclick="toggleGestionalePanel(); toggleOrariLettereModal()">
                <span class="gp-btn-icon">✉️</span>
                <span class="gp-btn-text">Orari Lettere</span>
                <span class="gp-btn-desc">Genera fasce orarie e scrivi nel FILONE</span>
            </button>
            <button class="gestionale-panel-btn gp-btn-BOXBEAT" onclick="toggleGestionalePanel(); apriModalBOXBEAT()">
                <span class="gp-btn-icon">📦</span>
                <span class="gp-btn-text">Carica Installato</span>
                <span class="gp-btn-desc">Importa file BOXBEAT con esiti</span>
            </button>
            <button class="gestionale-panel-btn gp-btn-sigilli" onclick="toggleGestionalePanel(); apriVistaSigilli()">
                <span class="gp-btn-icon">🔐</span>
                <span class="gp-btn-text">Sigilli / Matricole</span>
                <span class="gp-btn-desc">Inserisci sigilli e matricole installate</span>
            </button>
            <a href="/distinte" target="_blank" class="gestionale-panel-btn gp-btn-distinte" style="text-decoration:none;">
                <span class="gp-btn-icon">📋</span>
                <span class="gp-btn-text">Gestione Distinte</span>
                <span class="gp-btn-desc">Apri gestione distinte in nuova scheda</span>
            </a>
            <a href="/calendario" target="_blank" class="gestionale-panel-btn gp-btn-distinte" style="text-decoration:none;">
                <span class="gp-btn-icon">📅</span>
                <span class="gp-btn-text">Calendario Attività</span>
                <span class="gp-btn-desc">Gestisci attività condivise tra utenti</span>
            </a>
            <a href="/filone" target="_blank" class="gestionale-panel-btn gp-btn-distinte" style="text-decoration:none;">
                <span class="gp-btn-icon">📊</span>
                <span class="gp-btn-text">Vista Excel Filone</span>
                <span class="gp-btn-desc">Modifica il filone come un foglio di calcolo</span>
            </a>
            <a href="/dashboard" target="_blank" class="gestionale-panel-btn gp-btn-distinte" style="text-decoration:none;">
                <span class="gp-btn-icon">📈</span>
                <span class="gp-btn-text">Dashboard</span>
                <span class="gp-btn-desc">Statistiche, report operatori e riepilogo operativo</span>
            </a>
            <button class="gestionale-panel-btn gp-btn-backup" onclick="toggleGestionalePanel(); apriModalBackup()">
                <span class="gp-btn-icon">💾</span>
                <span class="gp-btn-text">Backup / Ripristino</span>
                <span class="gp-btn-desc">Backup giornaliero e ripristino FILONE</span>
            </button>
            <button class="gestionale-panel-btn gp-btn-lettere" onclick="toggleGestionalePanel(); apriImpostazioni()">
                <span class="gp-btn-icon">🔧</span>
                <span class="gp-btn-text">Impostazioni</span>
                <span class="gp-btn-desc">Configura operatori, deposito, percorsi e date</span>
            </button>
            <button class="gestionale-panel-btn gp-btn-logout" onclick="effettuaLogout()" style="border-top: 1px solid var(--border); margin-top: 2px; padding-top: 8px;">
                <span class="gp-btn-icon">🚪</span>
                <span class="gp-btn-text">Disconnetti</span>
                <span class="gp-btn-desc">Esci dall'account corrente</span>
            </button>
        </div>
    </div>

    <!-- Print Preview Modal -->
    <div class="print-preview-modal" id="printPreviewModal" onclick="if(event.target === this) closePrintPreview()">
        <div class="print-preview-content">
            <div class="print-preview-header">
                <div class="print-preview-title">🖨️ Anteprima Stampa - <span id="printPreviewInfo"></span></div>
                <div class="print-preview-actions">
                    <select id="printTecnicoFilter" onchange="onPrintTecnicoChange()" style="padding: 10px 16px; border: 2px solid #e9ecef; border-radius: 8px; font-size: 14px; font-weight: 500; background: white; cursor: pointer; min-width: 180px;">
                        <option value="">👥 Tutti i tecnici</option>
                    </select>
                    <button class="btn-print-cancel" onclick="closePrintPreview()">✕ Chiudi</button>
                    <button class="btn-print-confirm" onclick="executePrint()">🖨️ Stampa</button>
                </div>
            </div>
            <div class="print-preview-map">
                <div id="printMap"></div>
                <svg id="printLabelLines" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; pointer-events: none; z-index: 999;"></svg>
                <!-- Print Zoom Slider -->
                <div class="zoom-slider-container print-zoom-slider" id="printZoomSliderContainer">
                    <span class="zoom-label" id="printZoomOut" title="Zoom out">−</span>
                    <input type="range" id="printZoomSlider" min="5" max="19" step="0.25" value="14">
                    <span class="zoom-label" id="printZoomIn" title="Zoom in">+</span>
                    <span class="zoom-value" id="printZoomValue">14.0x</span>
                </div>
            </div>
        </div>
    </div>

    <!-- Date Picker Modal -->
    <div class="date-picker-modal" id="datePickerModal" onclick="if(event.target === this) closeDatePicker()">
        <div class="date-picker-content">
            <div class="date-picker-header">
                <div class="date-picker-title">📅 Seleziona Data</div>
                <button class="date-picker-close" onclick="closeDatePicker()">✕</button>
            </div>
            <div class="date-list-picker" id="dateListPicker"></div>
        </div>
    </div>

    <!-- Orari Lettere Modal -->
    <div class="orari-lettere-modal" id="orariLettereModal" onclick="if(event.target === this) toggleOrariLettereModal()">
        <div class="orari-lettere-content">
            <div class="orari-lettere-header">
                <div class="orari-lettere-title">✉️ Genera Orari Lettere</div>
                <button class="orari-lettere-close" onclick="toggleOrariLettereModal()">✕</button>
            </div>
            <div class="orari-lettere-form">
                <div>
                    <label>📅 Data inizio</label>
                    <input type="date" id="orariLettereDa">
                </div>
                <div>
                    <label>📅 Data fine</label>
                    <input type="date" id="orariLettereA">
                </div>
                <div>
                    <label>🔢 Tappe per fascia</label>
                    <div style="margin-top:6px;">
                        <label style="display:inline-flex;align-items:center;cursor:pointer;margin-right:16px;font-weight:normal;">
                            <input type="radio" name="tappeFasciaMode" value="auto" checked onchange="document.getElementById('orariLettereTappeFascia').disabled=true;" style="margin-right:4px;"> 🧠 Auto (distribuisci uniformemente)
                        </label>
                        <label style="display:inline-flex;align-items:center;cursor:pointer;font-weight:normal;">
                            <input type="radio" name="tappeFasciaMode" value="manuale" onchange="document.getElementById('orariLettereTappeFascia').disabled=false;" style="margin-right:4px;"> ✍️ Manuale:
                            <input type="number" id="orariLettereTappeFascia" min="1" max="20" value="4" disabled style="width:60px;margin-left:6px;">
                        </label>
                    </div>
                    <small style="display:block;color:#666;margin-top:4px;"><b>Auto</b>: per ogni tecnico/giorno distribuisce i contatori su tutte le 6 fasce in modo uniforme (minimo 4).<br><b>Manuale</b>: usa un numero fisso di contatori per fascia (uguale per tutti).</small>
                </div>
                <div class="orari-lettere-info">
                    ℹ️ Calcola le fasce orarie in base al percorso ottimizzato e le scrive nel FILONE.<br>
                    <strong>Non sovrascrive</strong> celle già compilate.
                </div>
                <button class="orari-lettere-btn" id="orariLettereGenera" onclick="generaOrariLettere()">
                    🚀 Genera e scrivi nel FILONE
                </button>
                <div class="orari-lettere-result" id="orariLettereResult"></div>
            </div>
        </div>
    </div>

    <!-- Advanced Search Modal -->
    <div class="pin-modal" id="advancedSearchModal" onclick="if(event.target === this) toggleAdvancedSearchModal()">
        <div class="pin-modal-content">
            <div class="pin-modal-header">
                <div class="pin-modal-title">🔍 Ricerca Utente</div>
                <button class="pin-modal-close" onclick="toggleAdvancedSearchModal()">✕</button>
            </div>

            <div class="advanced-search-grid">
                <div class="advanced-search-field">
                    <label>👤 Nominativo</label>
                    <input type="text" id="modalSearchNominativo" placeholder="Es: Mario Rossi" autocomplete="off">
                </div>
                <div class="advanced-search-field">
                    <label>⚡ POD</label>
                    <input type="text" id="modalSearchPod" placeholder="Es: IT001E..." autocomplete="off">
                </div>
                <div class="advanced-search-field">
                    <label>🔢 Eneltel</label>
                    <input type="text" id="modalSearchEneltel" placeholder="Es: 12345678" autocomplete="off">
                </div>
                <div class="advanced-search-field">
                    <label>🏘️ Comune</label>
                    <input type="text" id="modalSearchComune" placeholder="Es: Roma" autocomplete="off">
                </div>
                <div class="advanced-search-field">
                    <label>📍 Via</label>
                    <input type="text" id="modalSearchVia" placeholder="Es: Via Roma" autocomplete="off">
                </div>
                <div class="advanced-search-field">
                    <label>📱 Telefono</label>
                    <input type="text" id="modalSearchTelefono" placeholder="Es: 3331234567" autocomplete="off">
                </div>
            </div>

            <div class="advanced-search-actions">
                <button class="btn-clear-advanced" onclick="clearModalAdvancedSearch()">🗑️ Pulisci</button>
                <button class="btn-apply-advanced" onclick="applyModalAdvancedSearch()">🔍 Cerca</button>
            </div>

            <div class="pin-search-results" id="modalSearchResults"></div>
        </div>
    </div>

    <!-- Address Search Modal -->
    <div class="pin-modal" id="addressSearchModal" onclick="if(event.target === this) toggleAddressSearchModal()">
        <div class="pin-modal-content">
            <div class="pin-modal-header">
                <div class="pin-modal-title">📍 Cerca Indirizzo sulla Mappa</div>
                <button class="pin-modal-close" onclick="toggleAddressSearchModal()">✕</button>
            </div>

            <div class="address-search-container">
                <div class="advanced-search-field" style="width: 100%;">
                    <label>🏠 Indirizzo completo</label>
                    <input type="text" id="addressSearchInput" placeholder="Es: Via Roma 15, Torino" autocomplete="off" onkeypress="if(event.key === 'Enter') searchAddress()">
                </div>
                <div class="address-search-hint">
                    Inserisci via, numero civico e comune. Es: "Via Garibaldi 10, Moncalieri"
                </div>
            </div>

            <div class="advanced-search-actions">
                <button class="btn-clear-advanced" onclick="clearAddressSearch()">🗑️ Pulisci</button>
                <button class="btn-apply-advanced" onclick="searchAddress()">🔍 Cerca</button>
            </div>

            <div class="address-search-results" id="addressSearchResults"></div>
        </div>
    </div>

    <!-- Settings Modal -->
    <div class="settings-modal" id="settingsModal" onclick="if(event.target === this) chiudiImpostazioni()">
        <div class="settings-content">
            <div class="settings-header">
                <div class="settings-title">🔧 Impostazioni</div>
                <button class="settings-close" onclick="chiudiImpostazioni()">✕</button>
            </div>
            <div class="settings-tabs">
                <button class="settings-tab active" data-tab="operatori" onclick="switchSettingsTab('operatori')">👥 Operatori</button>
                <button class="settings-tab" data-tab="deposito" onclick="switchSettingsTab('deposito')">🏢 Deposito</button>
                <button class="settings-tab" data-tab="percorso" onclick="switchSettingsTab('percorso')">🛣️ Percorso</button>
                <button class="settings-tab" data-tab="date" onclick="switchSettingsTab('date')">📅 Date</button>
                <button class="settings-tab" data-tab="colonne" onclick="switchSettingsTab('colonne')">📋 Colonne</button>
                <button class="settings-tab" data-tab="sync" onclick="switchSettingsTab('sync')">🔄 Sync</button>
                <button class="settings-tab settings-tab-admin" data-tab="utenti" onclick="switchSettingsTab('utenti')" id="tabUtentiBtn" style="display:none">🔑 Utenti</button>
            </div>
            <div class="settings-body">
                <div class="settings-loading-overlay" id="settingsLoadingOverlay">
                    <div class="settings-spinner"></div>
                    <div class="settings-loading-text">Caricamento impostazioni...</div>
                </div>
                <div id="settingsStatus" class="settings-status"></div>

                <!-- Tab: Operatori -->
                <div class="settings-section active" id="settingsTab_operatori">
                    <h3>Gestione Operatori</h3>
                    <table class="settings-operators-table" id="settingsOperatorsTable">
                        <thead>
                            <tr>
                                <th>Matricola</th>
                                <th>Nome Display</th>
                                <th>Colore</th>
                                <th></th>
                            </tr>
                        </thead>
                        <tbody id="settingsOperatorsBody"></tbody>
                    </table>
                    <button class="settings-btn-add" onclick="aggiungiOperatore()">
                        ➕ Aggiungi operatore
                    </button>
                </div>

                <!-- Tab: Deposito -->
                <div class="settings-section" id="settingsTab_deposito">
                    <h3>Coordinate Deposito</h3>
                    <div class="settings-field">
                        <label>Nome deposito</label>
                        <input type="text" id="settings_deposito_nome">
                    </div>
                    <div class="settings-field-row">
                        <div class="settings-field">
                            <label>Latitudine</label>
                            <input type="number" step="0.0000001" id="settings_deposito_lat">
                        </div>
                        <div class="settings-field">
                            <label>Longitudine</label>
                            <input type="number" step="0.0000001" id="settings_deposito_lon">
                        </div>
                    </div>
                </div>

                <!-- Tab: Percorso -->
                <div class="settings-section" id="settingsTab_percorso">
                    <h3>Parametri Calcolo Percorso</h3>
                    <div class="settings-field-row">
                        <div class="settings-field">
                            <label>Velocità media (km/h)</label>
                            <input type="number" min="1" max="130" id="settings_velocita">
                        </div>
                        <div class="settings-field">
                            <label>Tempo intervento (min)</label>
                            <input type="number" min="1" max="120" id="settings_tempo_intervento">
                        </div>
                    </div>
                    <div class="settings-field-row">
                        <div class="settings-field">
                            <label>Raggio microzona (km)</label>
                            <input type="number" step="0.1" min="0.1" max="5" id="settings_raggio_microzona">
                        </div>
                        <div class="settings-field">
                            <label>Ora inizio giornata</label>
                            <input type="text" placeholder="es. 8:00 o 9:00" id="settings_ora_inizio">
                        </div>
                    </div>
                    <div class="settings-field-row">
                        <div class="settings-field">
                            <label>Ora fine giornata</label>
                            <input type="text" placeholder="es. 17:00 o 18:00" id="settings_ora_fine">
                        </div>
                        <div class="settings-field"></div>
                    </div>
                    <div class="settings-toggle">
                        <div>
                            <div class="settings-toggle-label">Ottimizza per tempo</div>
                            <div class="settings-toggle-desc">Se attivo, preferisce autostrade (più veloci). Se disattivo, minimizza i km.</div>
                        </div>
                        <label class="settings-switch">
                            <input type="checkbox" id="settings_ottimizza_tempo">
                            <span class="slider"></span>
                        </label>
                    </div>
                </div>

                <!-- Tab: Date -->
                <div class="settings-section" id="settingsTab_date">
                    <h3>Intervallo Date Filone</h3>
                    <div class="settings-field-row">
                        <div class="settings-field">
                            <label>Data inizio</label>
                            <input type="date" id="settings_data_inizio">
                        </div>
                        <div class="settings-field">
                            <label>Data fine</label>
                            <input type="date" id="settings_data_fine">
                            <label style="display:inline-flex;align-items:center;gap:6px;margin-top:6px;cursor:pointer;font-size:13px;font-weight:normal;">
                                <input type="checkbox" id="settings_data_fine_illimitata" onchange="toggleDataFineIllimitata(this.checked)">
                                ♾️ Illimitata (fino all'ultima data disponibile)
                            </label>
                        </div>
                    </div>
                    <p style="font-size: 12px; color: var(--text-secondary); margin-top: 8px;">
                        Verranno caricati solo i contatori con data programmata in questo intervallo.
                    </p>
                </div>

                <!-- Tab: Colonne -->
                <div class="settings-section" id="settingsTab_colonne">
                    <h3>Mappatura Colonne Filone</h3>
                    <p style="font-size: 12px; color: var(--text-secondary); margin-bottom: 12px;">
                        
                    </p>Associa le colonne del foglio Google ai campi utilizzati dal programma.
                    <div class="settings-colonne-info" id="colonneInfoLegend">
                        <span>✅ Trovata automaticamente</span>
                        <span>🔵 Mappata manualmente</span>
                        <span>⚠️ Non trovata</span>
                    </div>
                    <div id="colonneTableContainer">
                    </div>
                    <button class="settings-btn-reset-colonne" onclick="resetMappaturaColonne()" id="btnResetColonne" style="display:none;">
                        🔄 Ripristina mappatura predefinita
                    </button>
                </div>

                <!-- Tab: Sync -->
                <div class="settings-section" id="settingsTab_sync">
                    <h3>Sincronizzazione Dati</h3>
                    <div class="settings-field">
                        <label>Intervallo sincronizzazione (secondi)</label>
                        <input type="number" min="30" max="3600" id="settings_intervallo_sync">
                    </div>
                    <p style="font-size: 12px; color: var(--text-secondary); margin-top: 8px;">
                        Ogni quanti secondi il server controlla modifiche sul foglio Google. Minimo 30s, consigliato 180s.
                    </p>
                </div>

                <!-- Tab: Utenti (solo admin) -->
                <div class="settings-section" id="settingsTab_utenti">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
                        <h3 style="margin:0">Gestione Account</h3>
                        <button class="settings-btn-add" onclick="mostraFormNuovoUtente()">➕ Nuovo utente</button>
                    </div>

                    <!-- Form creazione nuovo utente (nascosto di default) -->
                    <div id="formNuovoUtente" style="display:none; background:var(--bg-secondary); border-radius:10px; padding:14px; margin-bottom:14px; border:1px solid var(--border);">
                        <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px;">
                            <div class="settings-field" style="margin-bottom:0">
                                <label>Username</label>
                                <input type="text" id="nuovoUtente_username" placeholder="es: mario" autocomplete="off">
                            </div>
                            <div class="settings-field" style="margin-bottom:0">
                                <label>Password (vuoto = auto)</label>
                                <input type="text" id="nuovoUtente_password" placeholder="min 8 caratteri" autocomplete="new-password">
                            </div>
                        </div>
                        <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-top:10px;">
                            <div class="settings-field" style="margin-bottom:0">
                                <label>Ruolo</label>
                                <select id="nuovoUtente_ruolo">
                                    <option value="user">User</option>
                                    <option value="admin">Admin</option>
                                </select>
                            </div>
                            <div class="settings-field" style="margin-bottom:0">
                                <label>Province</label>
                                <div id="nuovoUtente_province" style="display:flex; gap:10px; align-items:center; height:36px;"></div>
                            </div>
                        </div>
                        <div style="display:flex; gap:8px; justify-content:flex-end; margin-top:12px;">
                            <button class="settings-btn-cancel" style="padding:6px 14px; font-size:13px;" onclick="nascondiFormNuovoUtente()">Annulla</button>
                            <button class="settings-btn-save" style="padding:6px 14px; font-size:13px;" onclick="creaNuovoUtente()">✅ Crea</button>
                        </div>
                    </div>

                    <!-- Password generata -->
                    <div id="passwordGenerataBox" style="display:none; background:var(--bg-secondary); border-radius:10px; padding:14px; margin-bottom:14px; border:2px solid #f39c12;">
                        <div style="display:flex; align-items:center; gap:8px; margin-bottom:6px;">
                            <span style="font-size:18px;">🔐</span>
                            <strong>Password generata per <span id="passwordGenerataUser"></span></strong>
                        </div>
                        <div style="display:flex; align-items:center; gap:8px;">
                            <code id="passwordGenerataValue" style="font-size:16px; background:var(--bg-primary); padding:6px 12px; border-radius:6px; letter-spacing:1px; flex:1; user-select:all;"></code>
                            <button onclick="copiaPasswordGenerata()" style="padding:6px 10px; border-radius:6px; border:1px solid var(--border); background:var(--bg-secondary); cursor:pointer; font-size:14px;" title="Copia">📋</button>
                        </div>
                        <p style="font-size:11px; color:var(--text-secondary); margin-top:6px;">⚠️ Salva questa password, non sarà più visibile!</p>
                    </div>

                    <div id="adminUsersStatus" class="settings-status"></div>

                    <!-- Tabella utenti -->
                    <div id="adminUsersTableContainer">
                        <div style="text-align:center; padding:20px; color:var(--text-secondary);">Caricamento...</div>
                    </div>
                </div>
            </div>
            <div class="settings-footer">
                <button class="settings-btn-cancel" onclick="chiudiImpostazioni()">Annulla</button>
                <button class="settings-btn-save" id="settingsBtnSave" onclick="salvaImpostazioni()">💾 Salva e Sincronizza</button>
            </div>
        </div>
    </div>

    <script>
        // ============================================================
        // DATI - Caricamento ibrido (fetch o embedded)
        // ============================================================
        const PROVINCIA = "{PROVINCIA_ATTIVA.lower()}";
        const DATA_PATH = `./data/${{PROVINCIA}}/mappa_data.json`;
        const CONFIG_PATH = `./data/${{PROVINCIA}}/config.json`;
        const REFRESH_INTERVAL = {refresh_interval};

        // Dati embedded (fallback per file://)
        const EMBEDDED_DATA = {{
            zone: {zone_json},
            contatori: {contatori_json},
            rotte: {rotte_json},
            lcl_list: {lcl_json},
            timestamp: "{timestamp}"
        }};

        const EMBEDDED_CONFIG = {{
            deposito: {deposito_json},
            coloriTecnici: {colori_tecnici_json},
            matricoleVip: {matricole_vip_json},
            operatoriDict: {operatori_dict_json},
            oraInizioGiornata: {ORA_INIZIO_GIORNATA},
            oraFineGiornata: {ORA_FINE_GIORNATA},
            provincia: "{PROVINCIA_ATTIVA}"
        }};

        // Variabili globali dati (inizializzate dopo)
        let zone, contatori, rotte, deposito, coloriTecnici, matricoleVip, operatoriDict, dataTimestamp;
        let contatoriVisibili, zoneVisibili;
        
        // Mappa inversa matricola → nome display
        let reverseOperatoriDict = {{}};
        
        function buildReverseOperatoriDict() {{
            reverseOperatoriDict = {{}};
            if (operatoriDict) {{
                Object.entries(operatoriDict).forEach(([nome, matricola]) => {{
                    reverseOperatoriDict[matricola] = nome;
                }});
            }}
        }}
        
        function getDisplayName(tec) {{
            if (!tec || tec === 'TUTTI') return '📦 Senza Operatore';
            return reverseOperatoriDict[tec] || tec;
        }}
        let dataLoadMode = 'embedded'; // 'fetch' o 'embedded'
        
        // Converte timestamp YYYYMMDDHHMMSS in formato leggibile DD/MM/YYYY HH:MM
        function formatTimestamp(ts) {{
            if (!ts || ts.length < 12) return 'N/A';
            return `${{ts.substring(6,8)}}/${{ts.substring(4,6)}}/${{ts.substring(0,4)}} ${{ts.substring(8,10)}}:${{ts.substring(10,12)}}`;
        }}

        // Usa DATA_FINE dal config (importato dalla variabile Python)
        // Se vuoto = illimitata (mostra tutti i contatori futuri)
        const dataFineVisibile = '{DATA_FINE}' || '9999-12-31';

        // Helper: ottiene la data di oggi in formato YYYY-MM-DD (ricalcolata ogni volta)
        // NOTA: usa componenti locali, NON toISOString() che converte in UTC
        // (in fuso orario UTC+1/+2 toISOString restituisce il giorno precedente)
        function getOggiStr() {{
            const oggi = new Date();
            const y = oggi.getFullYear();
            const m = String(oggi.getMonth() + 1).padStart(2, '0');
            const d = String(oggi.getDate()).padStart(2, '0');
            return `${{y}}-${{m}}-${{d}}`;
        }}

        // Helper: verifica se una data è nel range visibile (usa data corrente)
        function isDataVisibile(dataStr) {{
            const oggiStr = getOggiStr();
            if (dataFineVisibile === '9999-12-31') return dataStr >= oggiStr;
            return dataStr >= oggiStr && dataStr <= dataFineVisibile;
        }}

        // Helper: formatta data da "2026-01-23" a "23/01/2026"
        function formatDate(dataStr) {{
            if (!dataStr || dataStr.length !== 10) return dataStr;
            const parts = dataStr.split('-');
            if (parts.length !== 3) return dataStr;
            return `${{parts[2]}}/${{parts[1]}}/${{parts[0]}}`;
        }}

        // Calcola contatori e zone visibili dai dati caricati
        // NOTA: Ricalcola visibilità in real-time invece di usare visibile_default pre-calcolato
        function updateContatoriVisibili() {{
            const oggiStr = getOggiStr();
            contatoriVisibili = contatori.filter(c => {{
                // Ricalcola visibilità in real-time (solo contatori con data valida da oggi in avanti)
                if (dataFineVisibile === '9999-12-31') return c.data && c.data >= oggiStr;
                return c.data && c.data >= oggiStr && c.data <= dataFineVisibile;
            }});
            zoneVisibili = zone.map(z => {{
                const dateVisibili = z.date.filter(d => isDataVisibile(d.data));
                const totaleVisibile = dateVisibili.reduce((sum, d) => sum + d.contatori, 0);
                return {{
                    ...z,
                    date: dateVisibili,
                    totale: totaleVisibile
                }};
            }}).filter(z => z.date.length > 0);
        }}

        // Calcola contatori schedulabili: senza data o con data passata, esclusi ANNULLATI
        // Mostra SOLO contatori con coordinate valide
        // Applica filtri attivi (tipologia, stato, appuntamento)
        function getContatoriSchedulabili() {{
            const oggiStr = getOggiStr();
            return contatori.filter(c => {{
                if (c.annullato) return false;
                // Escludi contatori senza coordinate valide
                if (!isValidCoordinate(c.lat, c.lon)) return false;
                // Schedulabile: senza data o con data passata
                const isSenzaData = !c.data || c.data === '';
                const isDataPassata = c.data && c.data < oggiStr;
                if (!isSenzaData && !isDataPassata) return false;

                // Filtro tipologia
                if (schedFilterTipologia && (c.tipo_ce || '').toLowerCase() !== schedFilterTipologia.toLowerCase()) return false;

                // Filtro stato
                if (schedFilterStato === 'senza_data' && !isSenzaData) return false;
                if (schedFilterStato === 'data_passata' && !isDataPassata) return false;

                // Filtro posizione misuratore
                if (schedFilterPosizione && normalizePosizione(c.note || '') !== schedFilterPosizione) return false;

                return true;
            }});
        }}

        // Conta schedulabili per una zona (basato sui comuni della zona)
        function countSchedulabiliPerZona(z) {{
            const comuniLower = z.comuni.map(c => c.toLowerCase());
            const schedulabili = getContatoriSchedulabili();
            return schedulabili.filter(c => comuniLower.includes(c.comune.toLowerCase())).length;
        }}

        // Conta schedulabili per un comune
        function countSchedulabiliPerComune(comuneNome) {{
            const nomeLower = comuneNome.toLowerCase();
            const schedulabili = getContatoriSchedulabili();
            return schedulabili.filter(c => c.comune.toLowerCase() === nomeLower).length;
        }}

        // Ottieni schedulabili per una zona
        function getSchedulabiliPerZona(z) {{
            const comuniLower = z.comuni.map(c => c.toLowerCase());
            const schedulabili = getContatoriSchedulabili();
            return schedulabili.filter(c => comuniLower.includes(c.comune.toLowerCase()));
        }}

        // Ottieni schedulabili per un comune
        function getSchedulabiliPerComune(comuneNome) {{
            const nomeLower = comuneNome.toLowerCase();
            const schedulabili = getContatoriSchedulabili();
            return schedulabili.filter(c => c.comune.toLowerCase() === nomeLower);
        }}

        // Carica dati embedded (fallback)
        function loadEmbeddedData() {{
            zone = EMBEDDED_DATA.zone;
            contatori = EMBEDDED_DATA.contatori;
            rotte = EMBEDDED_DATA.rotte;
            dataTimestamp = EMBEDDED_DATA.timestamp;

            deposito = EMBEDDED_CONFIG.deposito;
            coloriTecnici = EMBEDDED_CONFIG.coloriTecnici;
            matricoleVip = EMBEDDED_CONFIG.matricoleVip;
            operatoriDict = EMBEDDED_CONFIG.operatoriDict || {{}};
            buildReverseOperatoriDict();

            dataLoadMode = 'embedded';
            updateContatoriVisibili();
            
            // Aggiorna orario UI con timestamp dei dati
            document.getElementById('updateTime').textContent = formatTimestamp(dataTimestamp);
            
            console.log('📦 Dati caricati da embedded (modalità file://), timestamp:', dataTimestamp);
        }}

        // Inizializza dati (prova fetch, fallback embedded)
        async function initializeData() {{
            // Se file://, usa direttamente embedded
            if (window.location.protocol === 'file:') {{
                loadEmbeddedData();
                return;
            }}

            try {{
                // Prova fetch parallelo con cache-busting
                const cacheBuster = Date.now();
                const [dataResp, configResp] = await Promise.all([
                    fetch(DATA_PATH + '?t=' + cacheBuster, {{ cache: 'no-store' }}),
                    fetch(CONFIG_PATH + '?t=' + cacheBuster, {{ cache: 'no-store' }})
                ]);

                if (!dataResp.ok || !configResp.ok) throw new Error('Fetch failed');

                const data = await dataResp.json();
                const config = await configResp.json();

                zone = data.zone;
                contatori = data.contatori;
                rotte = data.rotte;
                dataTimestamp = data.timestamp;

                deposito = config.deposito;
                coloriTecnici = config.coloriTecnici;
                matricoleVip = config.matricoleVip;
                operatoriDict = config.operatoriDict || {{}};
                buildReverseOperatoriDict();

                dataLoadMode = 'fetch';
                updateContatoriVisibili();
                
                // IMPORTANTE: Aggiorna l'orario UI con il timestamp dei dati (non l'ora corrente)
                document.getElementById('updateTime').textContent = formatTimestamp(dataTimestamp);
                
                console.log('✅ Dati caricati via fetch, timestamp:', dataTimestamp);
            }} catch (e) {{
                console.log('⚠️ Fetch fallito, uso dati embedded:', e.message);
                loadEmbeddedData();
            }}
        }}

        // Inizializza subito con dati embedded (per evitare undefined)
        loadEmbeddedData();
        
        // Stato
        let viewMode = 'zone';  // 'zone' o 'town'
        let currentView = 'zone';
        let currentZona = null;
        let currentData = null;
        let currentOperatore = null;  // Per vista percorso completo operatore
        let currentLCL = '';
        let currentDataFiltro = '';
        let filterAppuntamentiOnly = false;  // Filtro solo appuntamenti
        let filterSchedulabili = false;  // Filtro contatori schedulabili
        let schedFilterTipologia = '';   // Filtro tipologia schedulabili
        let schedFilterStato = 'tutti';  // Filtro stato: tutti, senza_data, data_passata
        let schedFilterPosizione = '';    // Filtro posizione misuratore
        let showEneltelLabels = false;
        let advancedSearchMode = false;
        let markers = [];
        let zoneCircles = [];
        let eneltelLabels = [];
        let routePolylines = [];  // Polyline delle rotte OSRM
        let mostraRotteAttivo = false;

        // Pin & Date Navigation
        let pinnedContatore = null;
        let pinnedMarker = null;
        let tempSchedulabileMarker = null; // Marker temporaneo per contatore schedulabile
        let availableDates = [];
        let currentDateIndex = -1;
        let preserveMapView = false; // Flag per preservare la vista corrente
        let showAllDatesInDetail = false; // Flag per mostrare tutte le date in vista dettaglio
        let detailSelectedOperatori = new Set(); // Operatori selezionati nella vista dettaglio (vuoto = tutti)
        let isAutoRefresh = false; // Flag per evitare reset UI durante auto-refresh
        
        // ============================================================
        // MAPPA
        // ============================================================
        const map = L.map('map', {{
            zoomSnap: 0.25,
            zoomDelta: 0.5,
            wheelPxPerZoomLevel: 120,
            attributionControl: false,
            zoomControl: false,
            preferCanvas: true
        }}).setView([deposito.lat, deposito.lon], 10);
        
        let tileLayer;
        function setMapTiles(theme) {{
            if (tileLayer) map.removeLayer(tileLayer);
            if (theme === 'dark') {{
                tileLayer = L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png', {{
                    attribution: 'Â© OpenStreetMap Â© CARTO',
                    maxZoom: 19
                }});
            }} else {{
                tileLayer = L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
                    attribution: 'Â© OpenStreetMap',
                    maxZoom: 19
                }});
            }}
            tileLayer.addTo(map);
        }}
        setMapTiles(document.body.getAttribute('data-theme') || 'dark');
        // Aggiorna bottone tema se salvato
        (function() {{
            var t = document.body.getAttribute('data-theme');
            if (t === 'light') {{
                var btn = document.getElementById('themeToggle');
                if (btn) {{ btn.textContent = '\u2600\ufe0f Tema'; btn.classList.add('active'); }}
            }}
        }})();

        // ============================================================
        // ZOOM SLIDER
        // ============================================================
        const zoomSlider = document.getElementById('zoomSlider');
        const zoomValue = document.getElementById('zoomValue');

        // Aggiorna slider quando lo zoom cambia (rotellina, doppio click, ecc.)
        map.on('zoomend', function() {{
            const zoom = map.getZoom();
            zoomSlider.value = zoom;
            zoomValue.textContent = zoom.toFixed(1) + 'x';
        }});

        // ============================================================
        // FETCH DATI AGGIORNATI DA SHEETS ALL'APERTURA POPUP
        // ============================================================
        map.on('popupopen', async function(e) {{
            const popup = e.popup;
            const popupEl = popup.getElement();
            if (!popupEl) return;

            const contentEl = popupEl.querySelector('.popup-content[data-eneltel]');
            if (!contentEl) return;

            const eneltel = contentEl.dataset.eneltel;
            if (!eneltel) return;

            try {{
                const resp = await fetch(`${{window.location.origin}}/api/contatore/${{encodeURIComponent(eneltel)}}`);
                if (!resp.ok) throw new Error('Fetch failed');
                const result = await resp.json();

                if (result.contatore) {{
                    const fresh = result.contatore;

                    // Aggiorna il contatore locale nell'array globale
                    const idx = contatori.findIndex(c => c.eneltel === eneltel);
                    const localC = idx >= 0 ? contatori[idx] : null;
                    if (localC) {{
                        localC.cliente = fresh.cliente || localC.cliente;
                        localC.comune = fresh.comune || localC.comune;
                        localC.localita = fresh.localita ?? localC.localita;
                        localC.via = fresh.via || localC.via;
                        localC.civico = fresh.civico ?? localC.civico;
                        localC.telefono = fresh.telefono ?? localC.telefono;
                        localC.email = fresh.email ?? localC.email;
                        localC.note = fresh.note ?? localC.note;
                        localC.note_app = fresh.note_app ?? localC.note_app;
                        localC.tipo_ce = fresh.tipo_ce ?? localC.tipo_ce;
                        localC.orari_lettere = fresh.orari_lettere ?? localC.orari_lettere;
                        localC.lcl = fresh.lcl ?? localC.lcl;
                        localC.pod = fresh.pod || localC.pod;
                        localC.is_appuntamento = fresh.is_appuntamento;
                        localC.ora_appuntamento = fresh.ora_appuntamento;
                        localC.smart_test = fresh.smart_test ?? localC.smart_test;
                    }}

                    // Aggiorna anche il pinnedContatore se è lo stesso
                    if (pinnedContatore && pinnedContatore.eneltel === eneltel && localC) {{
                        Object.assign(pinnedContatore, localC);
                    }}

                    // Ri-renderizza il popup solo se ancora aperto
                    if (popup.isOpen()) {{
                        const num = contentEl.dataset.popupNum || '';
                        const colore = contentEl.dataset.popupColore || '#3498db';
                        const cRef = localC || fresh;
                        popup.setContent(createContatorePopup(cRef, num, colore));
                    }}
                }}
            }} catch (err) {{
                console.warn('⚠️ Aggiornamento popup da Sheets fallito:', err.message);
            }}
        }});

        // Aggiorna mappa quando lo slider viene trascinato
        zoomSlider.addEventListener('input', function() {{
            const zoom = parseFloat(this.value);
            map.setZoom(zoom);
            zoomValue.textContent = zoom.toFixed(1) + 'x';
        }});

        // Inizializza il valore dello slider
        zoomSlider.value = map.getZoom();
        zoomValue.textContent = map.getZoom().toFixed(1) + 'x';

        // Pulsanti zoom +/-
        document.getElementById('zoomOut').addEventListener('click', function() {{
            map.zoomOut(0.5);
        }});
        document.getElementById('zoomIn').addEventListener('click', function() {{
            map.zoomIn(0.5);
        }});
        
        // Deposito
        const depositoIcon = L.divIcon({{
            className: 'marker-deposito',
            iconSize: [24, 24],
            iconAnchor: [12, 12]
        }});
        
        const depositoMarker = L.marker([deposito.lat, deposito.lon], {{ icon: depositoIcon }})
            .bindPopup(`<div class="popup-content"><div class="popup-header"><div class="popup-title">🏠 ${{deposito.nome}}</div><div class="popup-subtitle">Punto di partenza</div></div></div>`)
            .addTo(map);
        
        // ============================================================
        // UTILITÃ€
        // ============================================================
        
        // Valida coordinate geografiche (Italia: lat 35-48, lon 6-19)
        function isValidCoordinate(lat, lon) {{
            return lat != null && lon != null &&
                   !isNaN(lat) && !isNaN(lon) &&
                   lat >= 35 && lat <= 48 &&
                   lon >= 6 && lon <= 19;
        }}
        
        function getColore(tecnico) {{
            return coloriTecnici[tecnico] || '#3498db';
        }}
        
        function clearMap() {{
            // Rimuovi planning layer group in blocco se presente
            if (planningLayerGroup) {{
                map.removeLayer(planningLayerGroup);
                planningLayerGroup = null;
            }}
            markers.forEach(m => map.removeLayer(m));
            markers = [];
            zoneCircles.forEach(c => map.removeLayer(c));
            zoneCircles = [];
            eneltelLabels.forEach(l => map.removeLayer(l));
            eneltelLabels = [];
            // Rimuovi marker temporaneo schedulabile
            if (tempSchedulabileMarker) {{
                map.removeLayer(tempSchedulabileMarker);
                tempSchedulabileMarker = null;
            }}
            // Rimuovi pinnedMarker dalla mappa (verrà ricreato se serve)
            if (pinnedMarker) {{
                map.removeLayer(pinnedMarker);
                pinnedMarker = null;
            }}
            clearRoutes();
        }}

        function clearRoutes() {{
            routePolylines.forEach(p => map.removeLayer(p));
            routePolylines = [];
        }}

        function toggleRotte() {{
            // Funzione mantenuta per compatibilità ma non più usata
            // Le rotte OSRM sono tracciate automaticamente nella vista percorso completo
        }}

        function disegnaRotte() {{
            clearRoutes();

            if (!mostraRotteAttivo || currentView !== 'detail') return;

            const data = availableDates[currentDateIndex];
            if (!data) return;

            const rotteGiorno = rotte[data];
            if (!rotteGiorno) return;

            Object.keys(rotteGiorno).forEach(tec => {{
                const geometry = rotteGiorno[tec];
                if (!geometry || geometry.length < 2) return;

                // Converti da [lon, lat] a [lat, lon] per Leaflet
                const latLngs = geometry.map(([lon, lat]) => [lat, lon]);

                const colore = coloriTecnici[tec] || '#3388ff';

                const polyline = L.polyline(latLngs, {{
                    color: colore,
                    weight: 4,
                    opacity: 0.7
                }}).addTo(map);

                // Tooltip con info tecnico
                polyline.bindTooltip(`Rotta ${{getDisplayName(tec)}}`, {{
                    permanent: false,
                    direction: 'center'
                }});

                routePolylines.push(polyline);
            }});
        }}

        function getLCLsPerZona(zona) {{
            const source = filterSchedulabili ? getContatoriSchedulabili() : contatoriVisibili;
            const comuniLower = zona.comuni.map(c => c.toLowerCase());
            const lcls = new Set();
            source.forEach(c => {{
                if (comuniLower.includes(c.comune.toLowerCase())) {{
                    if (c.lcl && c.lcl.trim() !== '') {{
                        lcls.add(c.lcl);
                    }}
                }}
            }});
            return Array.from(lcls).sort();
        }}

        // ============================================================
        // GESTIONE MARKER SOVRAPPOSTI
        // ============================================================
        
        // Raggruppa contatori con coordinate identiche o molto vicine
        function raggruppaContatoriSovrapposti(listaContatori) {{
            const SOGLIA_METRI = 5; // Contatori entro 5 metri sono considerati sovrapposti
            const gruppi = [];
            const usati = new Set();
            
            listaContatori.forEach((c, i) => {{
                if (usati.has(i)) return;
                
                const gruppo = {{
                    lat: c.lat,
                    lon: c.lon,
                    contatori: [{{ ...c, originalIndex: i }}]
                }};
                usati.add(i);
                
                // Cerca altri contatori vicini
                listaContatori.forEach((altro, j) => {{
                    if (i === j || usati.has(j)) return;
                    
                    // Calcola distanza approssimativa in metri
                    const distMetri = Math.sqrt(
                        Math.pow((c.lat - altro.lat) * 111000, 2) +
                        Math.pow((c.lon - altro.lon) * 111000 * Math.cos(c.lat * Math.PI / 180), 2)
                    );
                    
                    if (distMetri < SOGLIA_METRI) {{
                        gruppo.contatori.push({{ ...altro, originalIndex: j }});
                        usati.add(j);
                    }}
                }});
                
                // Ricalcola centroide se ci sono più contatori
                if (gruppo.contatori.length > 1) {{
                    let latSum = 0, lonSum = 0;
                    gruppo.contatori.forEach(cont => {{
                        latSum += cont.lat;
                        lonSum += cont.lon;
                    }});
                    gruppo.lat = latSum / gruppo.contatori.length;
                    gruppo.lon = lonSum / gruppo.contatori.length;
                }}
                
                gruppi.push(gruppo);
            }});
            
            return gruppi;
        }}

        // Aggiunge marker schedulabili alla mappa con raggruppamento + canvas ibrido
        function aggiungiMarkerSchedulabili(allPoints) {{
            const schedulabili = getContatoriSchedulabili();
            if (schedulabili.length === 0) return;
            schedulabili.forEach(c => {{ c._isSchedulabile = true; }});
            const gruppi = raggruppaContatoriSovrapposti(schedulabili);
            gruppi.forEach(gruppo => {{
                if (gruppo.contatori.length > 1) {{
                    // Gruppo sovrapposto: usa lo stesso sistema popup dei marker normali
                    const marker = creaMarkerGruppo(gruppo, '#06b6d4');
                    marker.addTo(map);
                    markers.push(marker);
                }} else {{
                    // Singolo: L.circleMarker (canvas, molto performante)
                    const c = gruppo.contatori[0];
                    const marker = L.circleMarker([c.lat, c.lon], {{
                        radius: 8,
                        fillColor: '#06b6d4',
                        color: '#ffffff',
                        weight: 2,
                        opacity: 1,
                        fillOpacity: 0.9
                    }})
                        .bindPopup(() => createContatorePopup(c, '📋', '#06b6d4'))
                        .addTo(map);
                    marker.contatore = c;
                    markers.push(marker);
                }}
                allPoints.push([gruppo.lat, gruppo.lon]);
            }});
        }}

        // Crea popup per gruppo di contatori sovrapposti
        function createMultiContatorePopup(gruppo, colore) {{
            const count = gruppo.contatori.length;
            let html = `<div class="popup-content" style="min-width: 280px;">`;
            
            html += `<div class="popup-multi-header">`;
            html += `<div class="popup-multi-header-title">📍 ${{count}} contatori sovrapposti</div>`;
            html += `<div class="popup-multi-header-subtitle">Stessa posizione - clicca per dettagli</div>`;
            html += `</div>`;
            
            html += `<div class="popup-multi-list">`;
            
            gruppo.contatori.forEach((c, idx) => {{
                const isApp = c.is_appuntamento;
                const tappaNum = (c.originalIndex != null && c.originalIndex < 100) ? c.originalIndex + 1 : null;
                html += `<div class="popup-multi-item" data-eneltel="${{c.eneltel}}" style="cursor: pointer;">`;
                html += `<div class="popup-multi-item-title">`;
                if (tappaNum !== null) {{
                    html += `<span class="popup-multi-item-num">${{tappaNum}}</span>`;
                }}
                html += `<span>${{c.cliente || 'N/D'}}</span>`;
                if (isApp) {{
                    html += `<span class="popup-multi-item-app">☎️ APP</span>`;
                }}
                html += `</div>`;
                html += `<div class="popup-multi-item-info">`;
                html += `📍 ${{c.via || 'N/A'}}${{c.localita && c.localita !== c.comune ? ' - ' + c.localita : ''}}<br>`;
                html += `🔢 ${{c.eneltel}} | ⚡ ${{c.pod || 'N/A'}}`;
                html += `</div>`;
                html += `</div>`;
            }});
            
            html += `</div>`;
            html += `</div>`;
            
            return html;
        }}

        // Mostra dettaglio di un singolo contatore dal popup multi
        function mostraDettaglioContatore(eneltel) {{
            // Cerca il contatore
            const c = contatori.find(x => x.eneltel === eneltel);
            if (!c) {{
                console.error('Contatore non trovato:', eneltel);
                return;
            }}
            
            // Chiudi il popup corrente
            map.closePopup();
            
            // Crea un popup temporaneo con i dettagli completi
            const colore = coloriTecnici[c.tecnico] || '#3498db';
            const popup = L.popup({{
                maxWidth: 350,
                className: 'contatore-detail-popup'
            }})
                .setLatLng([c.lat, c.lon])
                .setContent(createContatorePopup(c, '📍', colore))
                .openOn(map);
        }}

        // Crea marker per gruppo sovrapposto con gestione eventi
        function creaMarkerGruppo(gruppo, colore) {{
            const primoContatore = gruppo.contatori[0];
            const numeri = gruppo.contatori.map(c => c.indiceTecnico + 1).join(',');
            const hasAppuntamento = gruppo.contatori.some(c => c.is_appuntamento);
            const allSchedulabili = gruppo.contatori.every(c => c._isSchedulabile);
            const someSchedulabili = gruppo.contatori.some(c => c._isSchedulabile);
            const pulseStyle = hasAppuntamento ? 'animation: pulse 2s infinite;' : '';
            
            let icon;
            if (allSchedulabili) {{
                // Tutti schedulabili: stile arancione con numero contatori
                icon = L.divIcon({{
                    className: '',
                    html: `<div style="background: linear-gradient(135deg, #06b6d4 0%, #0891b2 100%); width: 30px; height: 30px; border-radius: 50%; display: flex; align-items: center; justify-content: center; position: relative; border: 3px solid white; box-shadow: 0 4px 12px rgba(0,0,0,0.4); font-weight: bold; font-size: 13px; color: white; ${{pulseStyle}}">
                        ${{gruppo.contatori.length}}
                    </div>`,
                    iconSize: [30, 30],
                    iconAnchor: [15, 15]
                }});
            }} else if (someSchedulabili) {{
                // Mix: usa colore base ma con bordo arancione per indicare presenza schedulabili
                icon = L.divIcon({{
                    className: '',
                    html: `<div style="background: ${{colore}}; width: 28px; height: 28px; border-radius: 50%; display: flex; align-items: center; justify-content: center; position: relative; border: 3px solid #06b6d4; box-shadow: 0 2px 8px rgba(6,182,212,0.5); font-weight: bold; font-size: 10px; color: white; ${{pulseStyle}}">
                        ${{primoContatore.indiceTecnico + 1}}
                        <span style="position: absolute; top: -8px; right: -8px; background: #ff6b6b; color: white; min-width: 18px; height: 18px; border-radius: 50%; font-size: 11px; font-weight: bold; display: flex; align-items: center; justify-content: center; border: 2px solid white; box-shadow: 0 2px 4px rgba(0,0,0,0.3);">${{gruppo.contatori.length}}</span>
                        ${{showEneltelLabels ? `<span class="marker-label">${{numeri}}</span>` : ''}}
                    </div>`,
                    iconSize: [28, 28],
                    iconAnchor: [14, 14]
                }});
            }} else {{
                // Nessuno schedulabile: stile originale
                icon = L.divIcon({{
                    className: '',
                    html: `<div style="background: ${{colore}}; width: 28px; height: 28px; border-radius: 50%; display: flex; align-items: center; justify-content: center; position: relative; border: 3px solid white; box-shadow: 0 2px 8px rgba(0,0,0,0.3); font-weight: bold; font-size: 10px; color: white; ${{pulseStyle}}">
                        ${{primoContatore.indiceTecnico + 1}}
                        <span style="position: absolute; top: -8px; right: -8px; background: #ff6b6b; color: white; min-width: 18px; height: 18px; border-radius: 50%; font-size: 11px; font-weight: bold; display: flex; align-items: center; justify-content: center; border: 2px solid white; box-shadow: 0 2px 4px rgba(0,0,0,0.3);">${{gruppo.contatori.length}}</span>
                        ${{showEneltelLabels ? `<span class="marker-label">${{numeri}}</span>` : ''}}
                    </div>`,
                    iconSize: [28, 28],
                    iconAnchor: [14, 14]
                }});
            }}
            
            const marker = L.marker([gruppo.lat, gruppo.lon], {{ icon }});
            
            // Crea popup con binding degli eventi
            const popupContent = createMultiContatorePopup(gruppo, colore);
            const popup = L.popup({{ maxWidth: 350 }}).setContent(popupContent);
            
            marker.bindPopup(popup);
            
            // Aggiungi event listener quando il popup si apre
            marker.on('popupopen', function() {{
                // Trova tutti gli elementi cliccabili nel popup
                const items = document.querySelectorAll('.popup-multi-item[data-eneltel]');
                items.forEach(item => {{
                    item.addEventListener('click', function() {{
                        const eneltel = this.getAttribute('data-eneltel');
                        mostraDettaglioContatore(eneltel);
                    }});
                }});
            }});
            
            marker.contatore = primoContatore;
            marker.gruppo = gruppo;
            
            return marker;
        }}
        
        // ============================================================
        // TOGGLES
        // ============================================================
        function toggleTheme() {{
            const body = document.body;
            const currentTheme = body.getAttribute('data-theme');
            const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
            body.setAttribute('data-theme', newTheme);
            setMapTiles(newTheme);
            localStorage.setItem('algeco_theme', newTheme);
            
            const btn = document.getElementById('themeToggle');
            btn.textContent = newTheme === 'dark' ? '🌙 Tema' : '☀️ Tema';
            btn.classList.toggle('active', newTheme === 'light');
        }}
        
        let _refreshWaitingForSSE = false;

        async function refreshDati() {{
            if (_refreshWaitingForSSE) return; // Sync già in corso, ignora
            const btn = document.getElementById('refreshBtn');
            const originalText = btn.textContent;
            
            // Feedback visivo
            btn.textContent = '⏳';
            btn.disabled = true;
            btn.style.opacity = '0.7';
            _refreshWaitingForSSE = true;
            
            // Timeout di sicurezza: se SSE non arriva entro 120s, ripristina
            const safetyTimeout = setTimeout(() => {{
                if (_refreshWaitingForSSE) {{
                    _refreshWaitingForSSE = false;
                    btn.textContent = '⚠️';
                    setTimeout(() => {{
                        btn.textContent = originalText;
                        btn.disabled = false;
                        btn.style.opacity = '1';
                    }}, 3000);
                }}
            }}, 120000);
            
            try {{
                // Triggera sync asincrono — SSE porterà l'aggiornamento in-place
                const response = await fetch('/api/filone/sync', {{ method: 'POST' }});
                const result = await response.json();
                
                if (!result.success) {{
                    clearTimeout(safetyTimeout);
                    _refreshWaitingForSSE = false;
                    alert('Errore durante il refresh: ' + (result.message || 'Sync fallito'));
                    btn.textContent = originalText;
                    btn.disabled = false;
                    btn.style.opacity = '1';
                }}
                // Se success, resta in ⏳ fino a sync_complete via SSE
            }} catch (err) {{
                clearTimeout(safetyTimeout);
                _refreshWaitingForSSE = false;
                console.error('Errore refresh:', err);
                alert('Errore di connessione durante il refresh');
                btn.textContent = originalText;
                btn.disabled = false;
                btn.style.opacity = '1';
            }}
        }}
        
        function _onSyncComplete() {{
            if (!_refreshWaitingForSSE) return;
            _refreshWaitingForSSE = false;
            const btn = document.getElementById('refreshBtn');
            btn.textContent = '✅';
            setTimeout(() => {{
                btn.textContent = '🔄';
                btn.disabled = false;
                btn.style.opacity = '1';
            }}, 3000);
        }}

        function toggleFiltroAppuntamenti() {{
            // Stub - filtro rimosso
        }}

        function populateDetailOperatoreChips() {{
            const dropdown = document.getElementById('detailOperatoreDropdown');
            const trigger = document.getElementById('detailOperatoreTrigger');
            if (!dropdown || !trigger) return;
            
            // Trova tutti gli operatori nei contatori della zona/data corrente
            const zonaInfo = zone.find(z => z.nome === currentZona);
            let contatoriData;
            
            if (zonaInfo) {{
                const comuniZona = zonaInfo.comuni.map(c => c.toLowerCase());
                contatoriData = contatori.filter(c =>
                    c.data === currentData &&
                    comuniZona.includes(c.comune.toLowerCase())
                );
            }} else {{
                contatoriData = contatori.filter(c =>
                    c.data === currentData &&
                    c.comune.toUpperCase() === currentZona.toUpperCase()
                );
            }}
            
            // Applica filtro LCL se attivo
            if (currentLCL) {{
                contatoriData = contatoriData.filter(c => c.lcl === currentLCL);
            }}
            
            // Raggruppa per operatore con conteggio
            const operatoriMap = new Map();
            contatoriData.forEach(c => {{
                const tec = c.tecnico || 'TUTTI';
                operatoriMap.set(tec, (operatoriMap.get(tec) || 0) + 1);
            }});
            
            const operatoriOrdinati = Array.from(operatoriMap.keys()).sort((a, b) => {{
                if (a === 'TUTTI') return 1;
                if (b === 'TUTTI') return -1;
                return a.localeCompare(b);
            }});
            
            // Se solo un operatore, nascondi il filtro
            if (operatoriOrdinati.length <= 1) {{
                dropdown.innerHTML = '';
                trigger.style.display = 'none';
                return;
            }}
            
            trigger.style.display = '';
            
            let html = '';
            operatoriOrdinati.forEach(op => {{
                const count = operatoriMap.get(op);
                const colore = getColore(op);
                const nome = op === 'TUTTI' ? 'Senza Op.' : getDisplayName(op);
                const isChecked = detailSelectedOperatori.size === 0 || detailSelectedOperatori.has(op);
                html += `<label class="detail-operatore-item" onclick="event.stopPropagation()">
                    <input type="checkbox" value="${{op}}" ${{isChecked ? 'checked' : ''}} onchange="onDetailOperatoreCheckChange()">
                    <span class="op-color-dot" style="background:${{colore}}"></span>
                    <span>${{nome}} (${{count}})</span>
                </label>`;
            }});
            
            dropdown.innerHTML = html;
            updateDetailOperatoreTriggerText();
        }}
        
        function toggleDetailOperatoreDropdown() {{
            const dropdown = document.getElementById('detailOperatoreDropdown');
            const trigger = document.getElementById('detailOperatoreTrigger');
            const isOpen = dropdown.classList.contains('open');
            dropdown.classList.toggle('open');
            trigger.classList.toggle('active');
            
            if (!isOpen) {{
                const closeHandler = (e) => {{
                    if (!dropdown.contains(e.target) && e.target !== trigger) {{
                        dropdown.classList.remove('open');
                        trigger.classList.remove('active');
                        document.removeEventListener('click', closeHandler);
                    }}
                }};
                setTimeout(() => document.addEventListener('click', closeHandler), 0);
            }}
        }}
        
        function getDetailSelectedOperatori() {{
            const checkboxes = document.querySelectorAll('#detailOperatoreDropdown input[type="checkbox"]');
            const selected = [];
            checkboxes.forEach(cb => {{ if (cb.checked) selected.push(cb.value); }});
            return selected;
        }}
        
        function updateDetailOperatoreTriggerText() {{
            const trigger = document.getElementById('detailOperatoreTrigger');
            if (!trigger) return;
            const checkboxes = document.querySelectorAll('#detailOperatoreDropdown input[type="checkbox"]');
            const total = checkboxes.length;
            const selected = getDetailSelectedOperatori();
            
            if (selected.length === 0 || selected.length === total) {{
                trigger.textContent = '👥 Tutti gli operatori';
            }} else if (selected.length === 1) {{
                const nome = selected[0] === 'TUTTI' ? 'Senza Op.' : getDisplayName(selected[0]);
                trigger.textContent = '👤 ' + nome;
            }} else {{
                trigger.textContent = '👥 ' + selected.length + ' operatori';
            }}
        }}
        
        function onDetailOperatoreCheckChange() {{
            const checkboxes = document.querySelectorAll('#detailOperatoreDropdown input[type="checkbox"]');
            const total = checkboxes.length;
            const selected = getDetailSelectedOperatori();
            
            detailSelectedOperatori.clear();
            if (selected.length > 0 && selected.length < total) {{
                selected.forEach(op => detailSelectedOperatori.add(op));
            }}
            // Se tutti o nessuno selezionati, set vuoto = mostra tutti
            
            updateDetailOperatoreTriggerText();
            
            // Riesegui la vista dettaglio
            if (currentView === 'detail' && currentData) {{
                preserveMapView = true;
                if (zone.find(z => z.nome === currentZona)) {{
                    apriDettaglio(currentZona, currentData);
                }} else {{
                    apriDettaglioComune(currentZona, currentData);
                }}
                preserveMapView = false;
            }}
        }}

        function toggleSchedulabili() {{
            const checkbox = document.getElementById('filterSchedulabiliCheckbox');
            filterSchedulabili = checkbox.checked;
            
            // Aggiorna stile chip
            const chip = document.getElementById('schedulabiliChip');
            chip.classList.toggle('active', filterSchedulabili);
            
            // Mostra/nascondi pannello filtri
            const filtersPanel = document.getElementById('schedFiltersPanel');
            filtersPanel.classList.toggle('visible', filterSchedulabili);
            
            if (filterSchedulabili) {{
                // Popola dropdown con valori unici dagli schedulabili
                populateSchedTipologiaDropdown();
                populateSchedPosizioneDropdown();
                updateSchedFiltersCount();
            }} else {{
                // Reset filtri
                schedFilterTipologia = '';
                schedFilterStato = 'tutti';
                schedFilterPosizione = '';
            }}
            
            preserveMapView = true;
            if (currentView === 'zone' || currentView === 'towns') {{
                if (viewMode === 'zone') {{
                    mostraVistaZone(document.getElementById('searchInput').value);
                }} else {{
                    mostraVistaComuni(document.getElementById('searchInput').value);
                }}
            }} else if (currentView === 'detail' && currentZona) {{
                // Aggiorna anche la vista dettaglio
                if (showAllDatesInDetail) {{
                    toggleAllDates();
                }} else if (currentData) {{
                    const zona = zone.find(z => z.nome === currentZona);
                    if (zona) {{
                        apriDettaglio(currentZona, currentData);
                    }} else {{
                        apriDettaglioComune(currentZona, currentData);
                    }}
                }}
            }} else if (currentView === 'tuttiOperatori' && currentData) {{
                mostraPercorsoTuttiOperatori(currentData);
            }} else if (currentView === 'multiOperatori' && currentData) {{
                const selOps = getSelectedOperatori();
                if (selOps.length > 0) mostraPercorsoMultiOperatori(selOps, currentData);
                else mostraPercorsoTuttiOperatori(currentData);
            }} else if (currentView === 'operatore' && currentOperatore && currentData) {{
                mostraPercorsoCompletoOperatore(currentOperatore, currentData);
            }}
        }}

        // Popola il dropdown tipologia con i valori unici trovati negli schedulabili
        function populateSchedTipologiaDropdown() {{
            // Salva temporaneamente il filtro e calcola senza filtro tipologia per ottenere tutti i valori
            const savedTipo = schedFilterTipologia;
            schedFilterTipologia = '';
            const allSched = getContatoriSchedulabili();
            schedFilterTipologia = savedTipo;
            
            const tipologieMap = new Map();
            allSched.forEach(c => {{
                if (c.tipo_ce && c.tipo_ce.trim() !== '') {{
                    const key = c.tipo_ce.trim().toLowerCase();
                    if (!tipologieMap.has(key)) tipologieMap.set(key, c.tipo_ce.trim());
                }}
            }});
            
            const select = document.getElementById('schedFilterTipologia');
            const currentVal = select.value;
            select.innerHTML = '<option value="">Tutte</option>';
            Array.from(tipologieMap.values()).sort((a,b) => a.toLowerCase().localeCompare(b.toLowerCase())).forEach(t => {{
                const opt = document.createElement('option');
                opt.value = t;
                opt.textContent = t;
                if (t.toLowerCase() === (currentVal || '').toLowerCase()) opt.selected = true;
                select.appendChild(opt);
            }});
        }}

        function setSchedStato(stato) {{
            schedFilterStato = stato;
            document.querySelectorAll('[data-sched-stato]').forEach(el => {{
                el.classList.toggle('active', el.dataset.schedStato === stato);
            }});
            applySchedFilters();
        }}

        // Normalizza la stringa posizione rimuovendo il prefisso numerico
        function normalizePosizione(val) {{
            if (!val) return '';
            return val.replace(/^[0-9]+ *- */, '').trim().toLowerCase();
        }}

        function populateSchedPosizioneDropdown() {{
            // Salva temporaneamente il filtro e calcola senza filtro posizione
            const savedPos = schedFilterPosizione;
            schedFilterPosizione = '';
            const allSched = getContatoriSchedulabili();
            schedFilterPosizione = savedPos;

            const posizioni = new Map(); // normalized -> display label
            allSched.forEach(c => {{
                const raw = (c.note || '').trim();
                if (!raw) return;
                const norm = normalizePosizione(raw);
                if (norm && !posizioni.has(norm)) {{
                    // Capitalize first letter for display
                    posizioni.set(norm, norm.charAt(0).toUpperCase() + norm.slice(1));
                }}
            }});

            const select = document.getElementById('schedFilterPosizione');
            const currentVal = select.value;
            select.innerHTML = '<option value="">Tutte</option>';
            Array.from(posizioni.entries()).sort((a, b) => a[1].localeCompare(b[1])).forEach(([norm, label]) => {{
                const opt = document.createElement('option');
                opt.value = norm;
                opt.textContent = label;
                if (norm === currentVal) opt.selected = true;
                select.appendChild(opt);
            }});
        }}

        function applySchedFilters() {{
            schedFilterTipologia = document.getElementById('schedFilterTipologia').value;
            schedFilterPosizione = document.getElementById('schedFilterPosizione').value;
            updateSchedFiltersCount();
            preserveMapView = true;
            if (currentView === 'detail' && currentZona) {{
                if (showAllDatesInDetail) {{
                    toggleAllDates();
                }} else if (currentData) {{
                    const zona = zone.find(z => z.nome === currentZona);
                    if (zona) {{
                        apriDettaglio(currentZona, currentData);
                    }} else {{
                        apriDettaglioComune(currentZona, currentData);
                    }}
                }}
            }} else if (currentView === 'tuttiOperatori' && currentData) {{
                mostraPercorsoTuttiOperatori(currentData);
            }} else if (currentView === 'multiOperatori' && currentData) {{
                const selOps = getSelectedOperatori();
                if (selOps.length > 0) mostraPercorsoMultiOperatori(selOps, currentData);
                else mostraPercorsoTuttiOperatori(currentData);
            }} else if (currentView === 'operatore' && currentOperatore && currentData) {{
                mostraPercorsoCompletoOperatore(currentOperatore, currentData);
            }} else if (viewMode === 'zone') {{
                mostraVistaZone(document.getElementById('searchInput').value);
            }} else {{
                mostraVistaComuni(document.getElementById('searchInput').value);
            }}
        }}

        function updateSchedFiltersCount() {{
            const schedulabili = getContatoriSchedulabili();
            const countEl = document.getElementById('schedFiltersCount');
            if (countEl) {{
                const hasFilters = schedFilterTipologia || schedFilterStato !== 'tutti' || schedFilterPosizione;
                if (hasFilters) {{
                    countEl.textContent = '';
                    countEl.innerHTML = `Filtrati: <strong>${{schedulabili.length}}</strong> contatori`;
                }} else {{
                    countEl.textContent = '';
                }}
            }}
            // Aggiorna anche il badge principale
            updateSchedulabiliBadge();
        }}

        function updateSchedulabiliBadge() {{
            const schedulabili = getContatoriSchedulabili();
            const badge = document.getElementById('schedulabiliBadge');
            if (badge) {{
                badge.textContent = schedulabili.length;
                badge.style.display = schedulabili.length > 0 ? 'inline-block' : 'none';
            }}
        }}

        // Costruisce la lista inline degli schedulabili per una zona
        function buildSchedInlineZona(z, schedZoneId) {{
            const schedContatori = getSchedulabiliPerZona(z);
            if (schedContatori.length === 0) return '';
            return `<div class="zone-sched-inline-list" id="schedInline_${{schedZoneId}}">
                <div class="date-list">
                    ${{schedContatori.map(c => `
                        <div class="date-item schedulabile-item" onclick="event.stopPropagation(); mostraContatorePopup(contatori.find(cc => cc.id === '${{c.id}}'))">
                            <div style="display: flex; flex-direction: column; flex: 1; gap: 2px;">
                                <span style="font-weight: 600; font-size: 0.85rem;">📍 ${{c.comune}}</span>
                                <span style="font-size: 0.78rem; color: var(--text-muted);">${{c.via || 'N/A'}} ${{c.civico || ''}}</span>
                                <span style="font-size: 0.78rem; color: var(--text-muted);">🔢 ${{c.eneltel}}${{c.tipo_ce ? ` · ⚡ ${{c.tipo_ce}}` : ''}}</span>
                                ${{c.data ? `<span style="font-size: 0.75rem; color: var(--danger, #ef4444);">⏰ era: ${{formatDate(c.data)}}</span>` : `<span style="font-size: 0.75rem; color: var(--accent-secondary, #06b6d4);">📋 senza data</span>`}}
                                ${{c.is_appuntamento ? `<span style="font-size: 0.75rem; color: #f59e0b;">📞 Appuntamento${{c.ora_appuntamento ? ' ' + c.ora_appuntamento : ''}}</span>` : ''}}
                            </div>
                            <span class="arrow">→</span>
                        </div>
                    `).join('')}}
                </div>
            </div>`;
        }}

        // Costruisce la lista inline degli schedulabili per un comune
        function buildSchedInlineComune(comuneNome, schedComuneId) {{
            const schedContatori = getSchedulabiliPerComune(comuneNome);
            if (schedContatori.length === 0) return '';
            return `<div class="zone-sched-inline-list" id="schedInline_${{schedComuneId}}">
                <div class="date-list">
                    ${{schedContatori.map(sc => `
                        <div class="date-item schedulabile-item" onclick="event.stopPropagation(); mostraContatorePopup(contatori.find(cc => cc.id === '${{sc.id}}'))">
                            <div style="display: flex; flex-direction: column; flex: 1; gap: 2px;">
                                <span style="font-size: 0.78rem; color: var(--text-muted);">${{sc.via || 'N/A'}} ${{sc.civico || ''}}</span>
                                <span style="font-size: 0.78rem; color: var(--text-muted);">🔢 ${{sc.eneltel}}${{sc.tipo_ce ? ` · ⚡ ${{sc.tipo_ce}}` : ''}}</span>
                                ${{sc.data ? `<span style="font-size: 0.75rem; color: var(--danger, #ef4444);">⏰ era: ${{formatDate(sc.data)}}</span>` : `<span style="font-size: 0.75rem; color: var(--accent-secondary, #06b6d4);">📋 senza data</span>`}}
                                ${{sc.is_appuntamento ? `<span style="font-size: 0.75rem; color: #f59e0b;">📞 Appuntamento${{sc.ora_appuntamento ? ' ' + sc.ora_appuntamento : ''}}</span>` : ''}}
                            </div>
                            <span class="arrow">→</span>
                        </div>
                    `).join('')}}
                </div>
            </div>`;
        }}

        // Toggle visibilità lista schedulabili inline nella card
        function toggleSchedInline(tipo, id, nome) {{
            const listEl = document.getElementById('schedInline_' + id);
            const badgeEl = document.getElementById('schedBadge_' + id);
            if (!listEl) return;
            
            const isVisible = listEl.classList.contains('visible');
            listEl.classList.toggle('visible');
            
            if (badgeEl) {{
                // Aggiorna freccia indicatore
                const count = tipo === 'zona' 
                    ? countSchedulabiliPerZona(zone.find(z => z.nome === nome) || {{comuni: []}})
                    : countSchedulabiliPerComune(nome);
                const suffix = count === 1 ? 'e' : 'i';
                badgeEl.innerHTML = isVisible 
                    ? `📋 ${{count}} schedulabil${{suffix}} ▸`
                    : `📋 ${{count}} schedulabil${{suffix}} ▾`;
                badgeEl.classList.toggle('active', !isVisible);
            }}
        }}

        function toggleEneltelLabels() {{
            showEneltelLabels = !showEneltelLabels;
            const btn = document.getElementById('eneltelToggle');
            btn.classList.toggle('active', showEneltelLabels);

            if (currentView === 'detail' && currentData) {{
                // Preserva zoom prima di ricaricare la mappa
                preserveMapView = true;
                // Verifica se currentZona è una zona o un comune
                const zona = zone.find(z => z.nome === currentZona);
                if (zona) {{
                    apriDettaglio(currentZona, currentData);
                }} else {{
                    apriDettaglioComune(currentZona, currentData);
                }}
            }}
        }}

        function toggleView() {{
            viewMode = viewMode === 'zone' ? 'town' : 'zone';
            const btn = document.getElementById('viewToggle');

            if (viewMode === 'zone') {{
                btn.innerHTML = '🗺️ Zone';
                btn.classList.add('active');
                document.getElementById('searchInput').placeholder = '🔍 Cerca...';
                mostraVistaZone(document.getElementById('searchInput').value);
            }} else {{
                btn.innerHTML = '🏘️ Comuni';
                btn.classList.remove('active');
                document.getElementById('searchInput').placeholder = '🔍 Cerca...';
                mostraVistaComuni(document.getElementById('searchInput').value);
            }}
        }}
        
        function toggleSidebar() {{
            const sidebar = document.getElementById('sidebar');
            const toggle = document.getElementById('sidebarToggle');
            sidebar.classList.toggle('collapsed');
            toggle.classList.toggle('moved');
            toggle.textContent = sidebar.classList.contains('collapsed') ? '▶' : '◀';
            setTimeout(() => map.invalidateSize(), 350);
        }}

        function toggleSearchMode() {{
            advancedSearchMode = !advancedSearchMode;
            const btn = document.getElementById('searchModeToggle');
            const panel = document.getElementById('advancedSearchPanel');

            btn.classList.toggle('active', advancedSearchMode);
            panel.classList.toggle('visible', advancedSearchMode);

            // Nascondi la ricerca semplice quando la ricerca avanzata è attiva
            const searchInput = document.getElementById('searchInput');
            if (advancedSearchMode) {{
                searchInput.value = '';
                searchInput.disabled = true;
                searchInput.placeholder = '🔍 Usa i filtri avanzati sotto';
            }} else {{
                searchInput.disabled = false;
                searchInput.placeholder = '🔍 Cerca...';
                clearAdvancedSearch();
            }}
        }}

        function clearSearch() {{
            const input = document.getElementById('searchInput');
            input.value = '';
            input.focus();
            updateSearchClearButton();

            // Riapplica vista corrente
            if (currentView === 'operatore' && currentOperatore && currentData) {{
                mostraPercorsoCompletoOperatore(currentOperatore, currentData);
            }} else if (currentView === 'multiOperatori' && currentData) {{
                const selOps = getSelectedOperatori();
                if (selOps.length > 0) mostraPercorsoMultiOperatori(selOps, currentData);
                else mostraPercorsoTuttiOperatori(currentData);
            }} else if (currentView === 'detail' && currentZona && currentData) {{
                apriDettaglioConRicerca(currentZona, currentData, '');
            }} else if (viewMode === 'zone') {{
                mostraVistaZone();
            }} else {{
                mostraVistaComuni();
            }}
        }}

        function updateSearchClearButton() {{
            const input = document.getElementById('searchInput');
            const clearBtn = document.getElementById('searchClear');
            if (input.value.trim()) {{
                clearBtn.classList.add('visible');
            }} else {{
                clearBtn.classList.remove('visible');
            }}
        }}

        function clearAdvancedSearch() {{
            document.getElementById('searchNominativo').value = '';
            document.getElementById('searchPod').value = '';
            document.getElementById('searchEneltel').value = '';
            document.getElementById('searchComune').value = '';
            document.getElementById('searchVia').value = '';
            document.getElementById('searchTelefono').value = '';

            // Riapplica vista corrente senza filtri avanzati
            if (currentView === 'operatore' && currentOperatore && currentData) {{
                mostraPercorsoCompletoOperatore(currentOperatore, currentData);
            }} else if (currentView === 'multiOperatori' && currentData) {{
                const selOps = getSelectedOperatori();
                if (selOps.length > 0) mostraPercorsoMultiOperatori(selOps, currentData);
                else mostraPercorsoTuttiOperatori(currentData);
            }} else if (currentView === 'detail' && currentZona && currentData) {{
                apriDettaglioConRicerca(currentZona, currentData, '');
            }} else if (viewMode === 'zone') {{
                mostraVistaZone();
            }} else {{
                mostraVistaComuni();
            }}
        }}

        function applicaRicercaAvanzata() {{
            // Ottieni valori dai campi di ricerca avanzata
            const nominativo = document.getElementById('searchNominativo').value.trim().toLowerCase();
            const pod = document.getElementById('searchPod').value.trim().toLowerCase();
            const eneltel = document.getElementById('searchEneltel').value.trim().toLowerCase();
            const comune = document.getElementById('searchComune').value.trim().toLowerCase();
            const via = document.getElementById('searchVia').value.trim().toLowerCase();
            const telefono = document.getElementById('searchTelefono').value.trim().replace(/\\s+/g, '').toLowerCase();

            // Se nessun campo compilato, ripristina la vista corrente
            if (!nominativo && !pod && !eneltel && !comune && !via && !telefono) {{
                if (currentView === 'operatore' && currentOperatore && currentData) {{
                    mostraPercorsoCompletoOperatore(currentOperatore, currentData);
                }} else if (currentView === 'multiOperatori' && currentData) {{
                    const selOps = getSelectedOperatori();
                    if (selOps.length > 0) mostraPercorsoMultiOperatori(selOps, currentData);
                    else mostraPercorsoTuttiOperatori(currentData);
                }} else if (currentView === 'detail' && currentZona && currentData) {{
                    apriDettaglioConRicerca(currentZona, currentData, '');
                }} else if (viewMode === 'zone') {{
                    mostraVistaZone();
                }} else {{
                    mostraVistaComuni();
                }}
                return;
            }}

            // Filtra contatori in base a TUTTI i criteri specificati (AND logico)
            let contatoriTrovati = contatori.filter(c => {{
                let match = true;

                // Controlla ogni campo solo se compilato
                if (nominativo && !(c.cliente || '').toLowerCase().includes(nominativo)) match = false;
                if (pod && !(c.pod || '').toLowerCase().includes(pod)) match = false;
                if (eneltel && !(c.eneltel || '').toLowerCase().includes(eneltel)) match = false;
                if (comune && !(c.comune || '').toLowerCase().includes(comune)) match = false;
                if (via && !(c.via || '').toLowerCase().includes(via)) match = false;
                if (telefono && !(c.telefono || '').replace(/\\s+/g, '').toLowerCase().includes(telefono)) match = false;

                return match;
            }});

            // Applica anche filtri LCL, Data e Appuntamenti
            if (currentLCL) {{
                contatoriTrovati = contatoriTrovati.filter(c => c.lcl === currentLCL);
            }}
            if (currentDataFiltro) {{
                contatoriTrovati = contatoriTrovati.filter(c => c.data === currentDataFiltro);
            }}
            if (filterAppuntamentiOnly) {{
                contatoriTrovati = contatoriTrovati.filter(c => c.is_appuntamento);
            }}

            // Mostra risultati
            if (contatoriTrovati.length > 0) {{
                const criteri = [];
                if (nominativo) criteri.push(`Nominativo: "${{nominativo}}"`);
                if (pod) criteri.push(`POD: "${{pod}}"`);
                if (eneltel) criteri.push(`Eneltel: "${{eneltel}}"`);
                if (comune) criteri.push(`Comune: "${{comune}}"`);
                if (via) criteri.push(`Via: "${{via}}"`);
                if (telefono) criteri.push(`Telefono: "${{telefono}}"`);

                mostraRisultatiRicercaAvanzata(contatoriTrovati, criteri.join(' + '));
            }} else {{
                // Nessun risultato trovato
                mostraRisultatiVuoti();
            }}
        }}

        function mostraRisultatiVuoti() {{
            currentView = 'search-results';

            document.getElementById('breadcrumb').classList.add('visible');
            document.getElementById('breadcrumbText').innerHTML = '<strong>Ricerca utente:</strong> Nessun risultato';

            const resultsDiv = document.getElementById('results');
            resultsDiv.innerHTML = `
                <div class="no-results">
                    <div class="no-results-icon">🔍</div>
                    <div>Nessun contatore trovato con i criteri specificati</div>
                </div>
            `;

            clearMap();

            document.getElementById('statZone').textContent = 0;
            document.getElementById('statContatori').textContent = 0;
            document.getElementById('statAppuntamenti').textContent = 0;
        }}

        // ============================================================
        // PIN & DATE NAVIGATION
        // ============================================================
        function toggleAdvancedSearchModal() {{
            const modal = document.getElementById('advancedSearchModal');
            const isOpening = !modal.classList.contains('visible');
            modal.classList.toggle('visible');

            if (isOpening) {{
                restoreAdvancedSearchData();
            }}
        }}

        function clearModalAdvancedSearch() {{
            document.getElementById('modalSearchNominativo').value = '';
            document.getElementById('modalSearchPod').value = '';
            document.getElementById('modalSearchEneltel').value = '';
            document.getElementById('modalSearchComune').value = '';
            document.getElementById('modalSearchVia').value = '';
            document.getElementById('modalSearchTelefono').value = '';
            document.getElementById('modalSearchResults').innerHTML = '';
            localStorage.removeItem('advancedSearchData');
        }}

        // ============================================================
        // ADDRESS SEARCH (Geocoding con Nominatim)
        // ============================================================
        let addressMarker = null;
        let addressSearchResults = [];  // Array per memorizzare i risultati della ricerca

        function toggleAddressSearchModal() {{
            const modal = document.getElementById('addressSearchModal');
            modal.classList.toggle('visible');

            if (modal.classList.contains('visible')) {{
                document.getElementById('addressSearchInput').focus();
            }}
        }}

        function clearAddressSearch() {{
            document.getElementById('addressSearchInput').value = '';
            document.getElementById('addressSearchResults').innerHTML = '';
            if (addressMarker) {{
                map.removeLayer(addressMarker);
                addressMarker = null;
            }}
        }}

        async function searchAddress() {{
            const query = document.getElementById('addressSearchInput').value.trim();
            if (!query) return;

            const resultsDiv = document.getElementById('addressSearchResults');
            resultsDiv.innerHTML = '<div class="address-search-loading">🔄 Ricerca in corso...</div>';

            try {{
                // Usa Nominatim (OpenStreetMap) per il geocoding
                // Limita la ricerca all'Italia per risultati migliori
                const url = `https://nominatim.openstreetmap.org/search?format=json&q=${{encodeURIComponent(query)}}&countrycodes=it&limit=5&addressdetails=1`;

                const response = await fetch(url, {{
                    headers: {{
                        'User-Agent': 'MappaContatori/1.0'
                    }}
                }});

                if (!response.ok) throw new Error('Errore nella richiesta');

                const results = await response.json();

                if (results.length === 0) {{
                    resultsDiv.innerHTML = '<div class="address-search-error">❌ Nessun risultato trovato. Prova con un indirizzo più completo.</div>';
                    return;
                }}

                // Salva i risultati per accesso successivo
                addressSearchResults = results;

                resultsDiv.innerHTML = results.map((r, index) => `
                    <div class="address-result-item" onclick="selectAddressResultByIndex(${{index}})">
                        <div class="address-result-name">${{r.display_name}}</div>
                        <div class="address-result-detail">📍 Coordinate: ${{parseFloat(r.lat).toFixed(5)}}, ${{parseFloat(r.lon).toFixed(5)}}</div>
                    </div>
                `).join('');

            }} catch (error) {{
                console.error('Errore geocoding:', error);
                resultsDiv.innerHTML = '<div class="address-search-error">❌ Errore durante la ricerca. Riprova più tardi.</div>';
            }}
        }}

        function selectAddressResultByIndex(index) {{
            // Recupera il risultato dall'array salvato
            const r = addressSearchResults[index];
            if (r) {{
                selectAddressResult(parseFloat(r.lat), parseFloat(r.lon), r.display_name || '');
            }}
        }}

        function selectAddressResult(lat, lon, displayName) {{
            // Chiudi il modal
            toggleAddressSearchModal();

            // Rimuovi marker precedente se esiste
            if (addressMarker) {{
                map.removeLayer(addressMarker);
            }}

            // Crea un nuovo marker per l'indirizzo trovato
            addressMarker = L.marker([lat, lon], {{
                icon: L.divIcon({{
                    className: 'address-marker',
                    html: '<div style="background: #9b59b6; width: 20px; height: 20px; border-radius: 50%; box-shadow: 0 2px 8px rgba(0,0,0,0.4); border: 3px solid white;"></div>',
                    iconSize: [20, 20],
                    iconAnchor: [10, 10]
                }})
            }}).addTo(map);

            // Popup con dettagli
            addressMarker.bindPopup(`
                <div class="popup-content">
                    <div class="popup-header">
                        <div class="popup-title">📍 Indirizzo Cercato</div>
                    </div>
                    <div class="popup-row"><span class="popup-value" style="font-size: 0.9em;">${{displayName}}</span></div>
                    <div class="popup-row" style="margin-top: 10px;">
                        <button onclick="if(addressMarker) {{ map.removeLayer(addressMarker); addressMarker = null; }}"
                                style="background: #e74c3c; color: white; border: none; padding: 8px 15px; border-radius: 5px; cursor: pointer; width: 100%;">
                            🗑️ Rimuovi marker
                        </button>
                    </div>
                </div>
            `).openPopup();

            // Zoom sulla posizione
            map.flyTo([lat, lon], 16);
        }}

        // ============================================================
        // GESTIONALE PANEL
        // ============================================================
        function toggleGestionalePanel() {{
            const panel = document.getElementById('gestionalePanel');
            panel.classList.toggle('visible');
        }}

        // Chiudi panel se si clicca fuori
        document.addEventListener('click', function(e) {{
            const panel = document.getElementById('gestionalePanel');
            const fab = document.getElementById('gestionaleFab');
            if (panel && panel.classList.contains('visible') && 
                !panel.contains(e.target) && !fab.contains(e.target)) {{
                panel.classList.remove('visible');
            }}
        }});

        // ============================================================
        // GENERAZIONE ORARI LETTERE
        // ============================================================
        function toggleOrariLettereModal() {{
            const modal = document.getElementById('orariLettereModal');
            modal.classList.toggle('visible');
            
            // Reset risultato quando si apre
            if (modal.classList.contains('visible')) {{
                document.getElementById('orariLettereResult').style.display = 'none';
                document.getElementById('orariLettereGenera').disabled = false;
                document.getElementById('orariLettereGenera').textContent = '🚀 Genera e scrivi nel FILONE';
            }}
        }}

        async function generaOrariLettere() {{
            const dataDa = document.getElementById('orariLettereDa').value;
            const dataA = document.getElementById('orariLettereA').value;
            const modeAuto = document.querySelector('input[name="tappeFasciaMode"]:checked').value === 'auto';
            const tappeFascia = modeAuto ? 'auto' : (parseInt(document.getElementById('orariLettereTappeFascia').value) || 4);
            
            if (!dataDa || !dataA) {{
                alert('Seleziona entrambe le date');
                return;
            }}
            
            if (dataA < dataDa) {{
                alert('La data fine deve essere uguale o successiva alla data inizio');
                return;
            }}
            
            if (!modeAuto && (tappeFascia < 1 || tappeFascia > 20)) {{
                alert('Tappe per fascia deve essere tra 1 e 20');
                return;
            }}
            
            const btn = document.getElementById('orariLettereGenera');
            const resultDiv = document.getElementById('orariLettereResult');
            
            btn.disabled = true;
            btn.textContent = '⏳ Generazione in corso...';
            resultDiv.style.display = 'none';
            
            try {{
                const response = await fetch('/api/genera-orari-lettere', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    credentials: 'same-origin',
                    body: JSON.stringify({{ data_da: dataDa, data_a: dataA, tappe_per_fascia: tappeFascia }})
                }});
                
                const data = await response.json();
                
                if (data.success) {{
                    const d = data.dettagli;
                    resultDiv.className = 'orari-lettere-result success';
                    
                    // Auto-download del file Excel se generato
                    let excelLink = '';
                    if (d.file_excel) {{
                        const downloadUrl = '/api/download-excel-lettere/' + encodeURIComponent(d.file_excel);
                        excelLink = `📄 Excel: <a href="${{downloadUrl}}" style="color:#155724;font-weight:bold;text-decoration:underline;">${{d.file_excel}} ⬇️</a><br>`;
                        // Avvia download automatico tramite iframe nascosto
                        var dlFrame = document.createElement('iframe');
                        dlFrame.style.display = 'none';
                        dlFrame.src = downloadUrl;
                        document.body.appendChild(dlFrame);
                        setTimeout(function() {{ document.body.removeChild(dlFrame); }}, 10000);
                    }}
                    
                    resultDiv.innerHTML = `
                        <strong>✅ Completato!</strong><br>
                        📅 Periodo: ${{d.periodo}}<br>
                        🕐 Fasce calcolate: ${{d.fasce_calcolate}}<br>
                        ✍️ Scritti nel FILONE: <strong>${{d.scritti}}</strong><br>
                        ${{d.saltati_occupati > 0 ? `⏭️ Saltati (cella già compilata): ${{d.saltati_occupati}}<br>` : ''}}
                        📋 Colonna: ${{d.colonna}}<br>
                        ${{excelLink}}
                        <small style="opacity:0.7">Al prossimo aggiornamento le fasce saranno visibili sulla mappa.</small>
                    `;
                    resultDiv.style.display = 'block';
                    btn.textContent = '✅ Completato!';
                }} else {{
                    resultDiv.className = 'orari-lettere-result error';
                    resultDiv.innerHTML = `<strong>❌ Errore:</strong> ${{data.error || 'Errore sconosciuto'}}`;
                    resultDiv.style.display = 'block';
                    btn.disabled = false;
                    btn.textContent = '🚀 Genera e scrivi nel FILONE';
                }}
            }} catch (err) {{
                resultDiv.className = 'orari-lettere-result error';
                resultDiv.innerHTML = `<strong>❌ Errore di rete:</strong> ${{err.message}}`;
                resultDiv.style.display = 'block';
                btn.disabled = false;
                btn.textContent = '🚀 Genera e scrivi nel FILONE';
            }}
        }}

        function applyModalAdvancedSearch() {{
            const nominativo = document.getElementById('modalSearchNominativo').value.trim().toLowerCase();
            const pod = document.getElementById('modalSearchPod').value.trim().toLowerCase();
            const eneltel = document.getElementById('modalSearchEneltel').value.trim().toLowerCase();
            const comune = document.getElementById('modalSearchComune').value.trim().toLowerCase();
            const via = document.getElementById('modalSearchVia').value.trim().toLowerCase();
            const telefono = document.getElementById('modalSearchTelefono').value.trim().replace(/\\s+/g, '').toLowerCase();

            if (!nominativo && !pod && !eneltel && !comune && !via && !telefono) return;

            // Salva i dati nel localStorage
            saveAdvancedSearchData();

            // Cerca in TUTTI i contatori (non solo nella zona/data corrente)
            let risultati = contatori.filter(c => {{
                let match = true;
                if (nominativo && !(c.cliente || '').toLowerCase().includes(nominativo)) match = false;
                if (pod && !(c.pod || '').toLowerCase().includes(pod)) match = false;
                if (eneltel && !(c.eneltel || '').toLowerCase().includes(eneltel)) match = false;
                if (comune && !(c.comune || '').toLowerCase().includes(comune)) match = false;
                if (via && !(c.via || '').toLowerCase().includes(via)) match = false;
                if (telefono && !(c.telefono || '').replace(/\\s+/g, '').toLowerCase().includes(telefono)) match = false;
                return match;
            }});

            const resultsDiv = document.getElementById('modalSearchResults');

            if (risultati.length === 0) {{
                resultsDiv.innerHTML = '<div class="no-results">Nessun contatore trovato</div>';
                return;
            }}

            resultsDiv.innerHTML = risultati.slice(0, 10).map(c => `
                <div class="pin-result-item ${{pinnedContatore && pinnedContatore.eneltel === c.eneltel ? 'pinned' : ''}}"
                     onclick="pinContatore('${{c.eneltel}}')">
                    <div style="display: flex; align-items: center; justify-content: space-between;">
                        <div class="pin-result-name">${{c.cliente}}</div>
                        <button class="tappa-edit-btn" onclick="event.stopPropagation(); toggleAdvancedSearchModal(); apriVistaModifica('${{c.eneltel}}')" title="Modifica" style="opacity: 0.7; font-size: 1.2em;">✏️</button>
                    </div>
                    <div class="pin-result-info">
                        📍 ${{c.comune}} - ${{c.via || 'N/A'}}<br>
                        🔢 ${{c.eneltel || 'N/A'}} | ⚡ ${{c.pod || 'N/A'}}<br>
                        📅 ${{formatDate(c.data)}}
                    </div>
                </div>
            `).join('');
        }}

        function pinContatore(eneltel) {{
            pinnedContatore = contatori.find(c => c.eneltel === eneltel);
            if (!pinnedContatore) return;

            toggleAdvancedSearchModal();

            // Trova la zona che contiene questo contatore
            const zonaDelContatore = zone.find(z => {{
                return z.comuni.some(comune => comune.toLowerCase() === pinnedContatore.comune.toLowerCase());
            }});

            if (!zonaDelContatore) {{
                console.error('Zona non trovata per il contatore', pinnedContatore);
                return;
            }}

            // Naviga alla vista dettaglio della zona e data del contatore
            apriDettaglio(zonaDelContatore.nome, pinnedContatore.data);

            // Zoom automatico sul contatore pinnato dopo il rendering
            setTimeout(() => {{
                if (pinnedContatore) {{
                    map.flyTo([pinnedContatore.lat, pinnedContatore.lon], 18);
                    // Apri il popup del marker pinnato (priorità al pinnedMarker rosa)
                    setTimeout(() => {{
                        if (pinnedMarker) {{
                            pinnedMarker.openPopup();
                        }} else {{
                            const marker = markers.find(m => m.contatore && m.contatore.eneltel === pinnedContatore.eneltel);
                            if (marker) marker.openPopup();
                        }}
                    }}, 500);
                }}
            }}, 300);
        }}

        function unpinContatore() {{
            const wasContatore = pinnedContatore ? {{ ...pinnedContatore }} : null;
            const wasEneltel = wasContatore ? wasContatore.eneltel : null;
            pinnedContatore = null;
            if (pinnedMarker) {{
                map.removeLayer(pinnedMarker);
                pinnedMarker = null;
            }}

            // Controlla se il contatore appena unpinnato è uno schedulabile
            // (non ha data, o data nel passato, e non è annullato)
            let isSchedulabile = false;
            if (wasContatore) {{
                const oggiStr = getOggiStr();
                isSchedulabile = !wasContatore.annullato && 
                    (!wasContatore.data || wasContatore.data === '' || wasContatore.data < oggiStr);
            }}

            if (isSchedulabile && wasContatore && isValidCoordinate(wasContatore.lat, wasContatore.lon)) {{
                if (currentView === 'detail') {{
                    // In vista dettaglio, fai il re-render completo per aggiornare tutti i marker
                    // (il contatore schedulabile verrà renderizzato normalmente nel loop)
                }} else if (currentView === 'tuttiOperatori' || currentView === 'operatore' || currentView === 'multiOperatori') {{
                    // Le viste percorso hanno già i circleMarker schedulabili se filtro attivo
                    // Basta ri-renderizzare la mappa, il circleMarker ricompare da solo
                }} else {{
                    // In viste zone/comuni non serve fare nulla di speciale
                    // Il marker schedulabile non è visibile in queste viste
                }}
            }}

            if (currentView === 'detail') {{
                // Ricrea solo la mappa senza pulire la sidebar
                const zonaInfo = zone.find(z => z.nome === currentZona);
                let contatoriData;
                const dataLabel = showAllDatesInDetail ? 'Tutte' : currentData;

                if (!zonaInfo) {{
                    // È un comune singolo, non una zona
                    if (showAllDatesInDetail) {{
                        contatoriData = contatori.filter(c =>
                            c.comune.toUpperCase() === currentZona.toUpperCase() &&
                            !c.annullato &&
                            isValidCoordinate(c.lat, c.lon)
                        );
                    }} else {{
                        contatoriData = contatori.filter(c =>
                            c.data === currentData &&
                            c.comune.toUpperCase() === currentZona.toUpperCase()
                        );
                    }}
                }} else {{
                    // È una zona
                    const comuniZona = zonaInfo.comuni.map(c => c.toLowerCase());
                    if (showAllDatesInDetail) {{
                        contatoriData = contatori.filter(c =>
                            comuniZona.includes(c.comune.toLowerCase()) &&
                            !c.annullato &&
                            isValidCoordinate(c.lat, c.lon)
                        );
                    }} else {{
                        contatoriData = contatori.filter(c =>
                            c.data === currentData &&
                            comuniZona.includes(c.comune.toLowerCase())
                        );
                    }}
                }}

                if (currentLCL) {{
                    contatoriData = contatoriData.filter(c => c.lcl === currentLCL);
                }}
                if (filterAppuntamentiOnly) {{
                    contatoriData = contatoriData.filter(c => c.is_appuntamento);
                }}

                const perTecnico = {{}};
                contatoriData.forEach(c => {{
                    const tec = c.tecnico || 'TUTTI';
                    if (!perTecnico[tec]) perTecnico[tec] = [];
                    perTecnico[tec].push(c);
                }});

                Object.keys(perTecnico).forEach(tec => {{
                    perTecnico[tec] = ordinaPercorsoJS(perTecnico[tec]);
                }});

                // Ricrea solo la mappa, non la sidebar - preserva zoom
                preserveMapView = true;
                renderDettaglioMappa(perTecnico, dataLabel);

                // Dopo il render, cerca il marker del contatore appena unpinnato e apri il suo popup
                if (wasEneltel) {{
                    setTimeout(() => {{
                        const m = markers.find(mk => mk.contatore && mk.contatore.eneltel === wasEneltel);
                        if (m) m.openPopup();
                    }}, 100);
                }}
            }} else if (currentView === 'tuttiOperatori') {{
                // Ricostruisci la mappa con tutti gli operatori
                let contatoriData = contatori.filter(c => c.data === currentData);
                if (currentLCL) contatoriData = contatoriData.filter(c => c.lcl === currentLCL);
                const operatoriMap = new Map();
                contatoriData.forEach(c => {{
                    const tec = c.tecnico || 'TUTTI';
                    if (!operatoriMap.has(tec)) operatoriMap.set(tec, []);
                    operatoriMap.get(tec).push(c);
                }});
                const percorsiPerOperatore = [];
                operatoriMap.forEach((contatoriOp, op) => {{
                    percorsiPerOperatore.push({{
                        operatore: op,
                        percorso: ordinaPercorsoJS(contatoriOp),
                        colore: getColore(op)
                    }});
                }});
                preserveMapView = true;
                renderPercorsoTuttiOperatoriMappa(percorsiPerOperatore, currentData);
                if (wasEneltel) {{
                    setTimeout(() => {{
                        const m = markers.find(mk => mk.contatore && mk.contatore.eneltel === wasEneltel);
                        if (m) m.openPopup();
                    }}, 100);
                }}
            }} else if (currentView === 'multiOperatori') {{
                // Ricostruisci la mappa per gli operatori selezionati
                const selOps = getSelectedOperatori();
                let contatoriData = contatori.filter(c => c.data === currentData && selOps.includes(c.tecnico || 'TUTTI'));
                if (currentLCL) contatoriData = contatoriData.filter(c => c.lcl === currentLCL);
                const operatoriMap = new Map();
                contatoriData.forEach(c => {{
                    const tec = c.tecnico || 'TUTTI';
                    if (!operatoriMap.has(tec)) operatoriMap.set(tec, []);
                    operatoriMap.get(tec).push(c);
                }});
                const percorsiPerOperatore2 = [];
                operatoriMap.forEach((contatoriOp, op) => {{
                    percorsiPerOperatore2.push({{
                        operatore: op,
                        percorso: ordinaPercorsoJS(contatoriOp),
                        colore: getColore(op)
                    }});
                }});
                preserveMapView = true;
                renderPercorsoTuttiOperatoriMappa(percorsiPerOperatore2, currentData);
                if (wasEneltel) {{
                    setTimeout(() => {{
                        const m = markers.find(mk => mk.contatore && mk.contatore.eneltel === wasEneltel);
                        if (m) m.openPopup();
                    }}, 100);
                }}
            }} else if (currentView === 'operatore') {{
                // Ricostruisci la mappa per singolo operatore
                let contatoriOp = contatori.filter(c =>
                    c.data === currentData &&
                    (c.tecnico || 'TUTTI') === currentOperatore
                );
                if (currentLCL) contatoriOp = contatoriOp.filter(c => c.lcl === currentLCL);
                const percorsoOrdinato = ordinaPercorsoJS(contatoriOp);
                preserveMapView = true;
                renderPercorsoOperatoreMappa(percorsoOrdinato, currentData, currentOperatore);
                if (wasEneltel) {{
                    setTimeout(() => {{
                        const m = markers.find(mk => mk.contatore && mk.contatore.eneltel === wasEneltel);
                        if (m) m.openPopup();
                    }}, 100);
                }}
            }} else if (currentView === 'zone') {{
                // In vista zone, basta che il pin marker sia stato rimosso
                // (già fatto sopra)
            }} else if (currentView === 'comuni') {{
                // In vista comuni, idem
            }}
        }}

        function pinContatoreFromPopup(eneltel) {{
            // Trova il contatore nell'array usando l'eneltel
            const contatore = contatori.find(c => c.eneltel === eneltel);
            if (!contatore) return;

            // Se è già pinnato, rimuovi il pin
            if (pinnedContatore && pinnedContatore.eneltel === eneltel) {{
                unpinContatore();
                return;
            }}

            // Altrimenti salva in pinnedContatore
            pinnedContatore = contatore;

            // Chiudi tutti i popup aperti
            map.closePopup();

            // Se c'era un marker temporaneo schedulabile, trasformalo nel marker pinned
            if (tempSchedulabileMarker) {{
                map.removeLayer(tempSchedulabileMarker);
                tempSchedulabileMarker = null;
            }}
            
            // Crea subito il marker pinned sulla mappa
            const pinIcon = L.divIcon({{
                className: 'marker-pinned',
                html: `<div style="background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); width: 32px; height: 32px; border-radius: 50%; display: flex; align-items: center; justify-content: center; border: 3px solid white; box-shadow: 0 4px 12px rgba(0,0,0,0.4); font-size: 16px;">📌</div>`,
                iconSize: [32, 32],
                iconAnchor: [16, 16]
            }});
            if (pinnedMarker) map.removeLayer(pinnedMarker);
            pinnedMarker = L.marker([contatore.lat, contatore.lon], {{ icon: pinIcon, zIndexOffset: 10000 }})
                .bindPopup(() => createContatorePopup(contatore, '📌', '#f5576c'))
                .addTo(map);
            pinnedMarker.openPopup();

            // Ricarica solo la mappa mantenendo la sidebar invariata
            if (currentView === 'detail') {{
                // Ottieni i contatori attuali dalla sidebar
                const zonaInfo = zone.find(z => z.nome === currentZona);
                let contatoriData;
                const dataLabel = showAllDatesInDetail ? 'Tutte' : currentData;

                if (!zonaInfo) {{
                    // È un comune singolo, non una zona
                    if (showAllDatesInDetail) {{
                        contatoriData = contatori.filter(c =>
                            c.comune.toUpperCase() === currentZona.toUpperCase() &&
                            !c.annullato &&
                            isValidCoordinate(c.lat, c.lon)
                        );
                    }} else {{
                        contatoriData = contatori.filter(c =>
                            c.data === currentData &&
                            c.comune.toUpperCase() === currentZona.toUpperCase()
                        );
                    }}
                }} else {{
                    // È una zona
                    const comuniZona = zonaInfo.comuni.map(c => c.toLowerCase());
                    if (showAllDatesInDetail) {{
                        contatoriData = contatori.filter(c =>
                            comuniZona.includes(c.comune.toLowerCase()) &&
                            !c.annullato &&
                            isValidCoordinate(c.lat, c.lon)
                        );
                    }} else {{
                        contatoriData = contatori.filter(c =>
                            c.data === currentData &&
                            comuniZona.includes(c.comune.toLowerCase())
                        );
                    }}
                }}

                if (currentLCL) {{
                    contatoriData = contatoriData.filter(c => c.lcl === currentLCL);
                }}
                if (filterAppuntamentiOnly) {{
                    contatoriData = contatoriData.filter(c => c.is_appuntamento);
                }}

                const perTecnico = {{}};
                contatoriData.forEach(c => {{
                    const tec = c.tecnico || 'TUTTI';
                    if (!perTecnico[tec]) perTecnico[tec] = [];
                    perTecnico[tec].push(c);
                }});

                Object.keys(perTecnico).forEach(tec => {{
                    perTecnico[tec] = ordinaPercorsoJS(perTecnico[tec]);
                }});

                // Ricarica solo la mappa, non la sidebar - preserva zoom
                preserveMapView = true;
                renderDettaglioMappa(perTecnico, dataLabel);
            }} else if (currentView === 'tuttiOperatori') {{
                // Ricostruisci la mappa con tutti gli operatori
                let contatoriData = contatori.filter(c => c.data === currentData);
                if (currentLCL) contatoriData = contatoriData.filter(c => c.lcl === currentLCL);
                const operatoriMap = new Map();
                contatoriData.forEach(c => {{
                    const tec = c.tecnico || 'TUTTI';
                    if (!operatoriMap.has(tec)) operatoriMap.set(tec, []);
                    operatoriMap.get(tec).push(c);
                }});
                const percorsiPerOperatore = [];
                operatoriMap.forEach((contatoriOp, op) => {{
                    percorsiPerOperatore.push({{
                        operatore: op,
                        percorso: ordinaPercorsoJS(contatoriOp),
                        colore: getColore(op)
                    }});
                }});
                preserveMapView = true;
                renderPercorsoTuttiOperatoriMappa(percorsiPerOperatore, currentData);
            }} else if (currentView === 'multiOperatori') {{
                // Ricostruisci la mappa per gli operatori selezionati
                const selOps = getSelectedOperatori();
                let contatoriData = contatori.filter(c => c.data === currentData && selOps.includes(c.tecnico || 'TUTTI'));
                if (currentLCL) contatoriData = contatoriData.filter(c => c.lcl === currentLCL);
                const operatoriMap = new Map();
                contatoriData.forEach(c => {{
                    const tec = c.tecnico || 'TUTTI';
                    if (!operatoriMap.has(tec)) operatoriMap.set(tec, []);
                    operatoriMap.get(tec).push(c);
                }});
                const percorsiPerOperatore2 = [];
                operatoriMap.forEach((contatoriOp, op) => {{
                    percorsiPerOperatore2.push({{
                        operatore: op,
                        percorso: ordinaPercorsoJS(contatoriOp),
                        colore: getColore(op)
                    }});
                }});
                preserveMapView = true;
                renderPercorsoTuttiOperatoriMappa(percorsiPerOperatore2, currentData);
            }} else if (currentView === 'operatore') {{
                // Ricostruisci la mappa per singolo operatore
                let contatoriOp = contatori.filter(c =>
                    c.data === currentData &&
                    (c.tecnico || 'TUTTI') === currentOperatore
                );
                if (currentLCL) contatoriOp = contatoriOp.filter(c => c.lcl === currentLCL);
                const percorsoOrdinato = ordinaPercorsoJS(contatoriOp);
                preserveMapView = true;
                renderPercorsoOperatoreMappa(percorsoOrdinato, currentData, currentOperatore);
            }}
        }}

        function saveAdvancedSearchData() {{
            const data = {{
                nominativo: document.getElementById('modalSearchNominativo').value,
                pod: document.getElementById('modalSearchPod').value,
                eneltel: document.getElementById('modalSearchEneltel').value,
                comune: document.getElementById('modalSearchComune').value,
                via: document.getElementById('modalSearchVia').value,
                telefono: document.getElementById('modalSearchTelefono').value
            }};
            localStorage.setItem('advancedSearchData', JSON.stringify(data));
        }}

        function restoreAdvancedSearchData() {{
            const saved = localStorage.getItem('advancedSearchData');
            if (!saved) return;

            try {{
                const data = JSON.parse(saved);
                document.getElementById('modalSearchNominativo').value = data.nominativo || '';
                document.getElementById('modalSearchPod').value = data.pod || '';
                document.getElementById('modalSearchEneltel').value = data.eneltel || '';
                document.getElementById('modalSearchComune').value = data.comune || '';
                document.getElementById('modalSearchVia').value = data.via || '';
                document.getElementById('modalSearchTelefono').value = data.telefono || '';
            }} catch (e) {{
                console.error('Errore ripristino dati ricerca:', e);
            }}
        }}

        function updateDateNavigation() {{
            // Cerca prima nelle zone
            let zona = zone.find(z => z.nome === currentZona);
            const oggiStr = getOggiStr();

            // Se non trovata, potrebbe essere un comune singolo
            if (!zona) {{
                // Ottieni le date disponibili per questo comune
                const dateSet = new Set();
                contatori.forEach(c => {{
                    if (c.comune.toUpperCase() === currentZona.toUpperCase() && c.data) {{
                        dateSet.add(c.data);
                    }}
                }});
                availableDates = Array.from(dateSet).sort().filter(d => d >= oggiStr);
                currentDateIndex = availableDates.indexOf(currentData);
            }} else {{
                availableDates = zona.date.map(d => d.data).sort().filter(d => d >= oggiStr);
                currentDateIndex = availableDates.indexOf(currentData);
            }}

            const nav = document.getElementById('dateNavigation');
            const btnPrev = document.getElementById('btnPrevDate');
            const btnNext = document.getElementById('btnNextDate');
            const dateText = document.getElementById('currentDateText');

            const hasMultipleDates = availableDates.length > 1;
            const isNoDate = currentDateIndex === -1;
            if (currentView === 'detail' && (hasMultipleDates || (isNoDate && availableDates.length > 0))) {{
                nav.classList.add('visible');
                if (isNoDate) {{
                    // Utente schedulabile senza data: solo avanti
                    btnPrev.disabled = true;
                    btnNext.disabled = false;
                }} else {{
                    btnPrev.disabled = currentDateIndex <= 0;
                    btnNext.disabled = currentDateIndex >= availableDates.length - 1;
                }}
                dateText.textContent = isNoDate ? 'Senza data' : formatDate(currentData);
            }} else {{
                nav.classList.remove('visible');
            }}
        }}

        function openDatePicker() {{
            const modal = document.getElementById('datePickerModal');
            const dateList = document.getElementById('dateListPicker');

            // Cerca prima nelle zone
            const zona = zone.find(z => z.nome === currentZona);

            let html = '';

            const oggiStrPicker = getOggiStr();

            if (zona) {{
                // È una zona
                zona.date.filter(d => d.data >= oggiStrPicker).forEach(d => {{
                    const isCurrent = d.data === currentData;
                    html += `
                        <div class="date-item-picker ${{isCurrent ? 'current' : ''}}" onclick="selectDateFromPicker('${{d.data}}')">
                            <div class="date-text">${{formatDate(d.data)}}</div>
                            <div class="date-info">${{d.contatori}} cont.${{d.appuntamenti > 0 ? ' | 📞 ' + d.appuntamenti : ''}}</div>
                        </div>
                    `;
                }});
            }} else {{
                // È un comune singolo - raggruppa per data
                const dateMap = new Map();
                contatori.forEach(c => {{
                    if (c.comune.toUpperCase() === currentZona.toUpperCase()) {{
                        if (!dateMap.has(c.data)) {{
                            dateMap.set(c.data, {{ contatori: 0, appuntamenti: 0 }});
                        }}
                        const info = dateMap.get(c.data);
                        info.contatori++;
                        if (c.is_appuntamento) info.appuntamenti++;
                    }}
                }});

                Array.from(dateMap.keys()).sort().filter(d => d >= oggiStrPicker).forEach(data => {{
                    const info = dateMap.get(data);
                    const isCurrent = data === currentData;
                    html += `
                        <div class="date-item-picker ${{isCurrent ? 'current' : ''}}" onclick="selectDateFromPicker('${{data}}')">
                            <div class="date-text">${{formatDate(data)}}</div>
                            <div class="date-info">${{info.contatori}} cont.${{info.appuntamenti > 0 ? ' | 📞 ' + info.appuntamenti : ''}}</div>
                        </div>
                    `;
                }});
            }}

            dateList.innerHTML = html;
            modal.classList.add('visible');
        }}

        function closeDatePicker() {{
            const modal = document.getElementById('datePickerModal');
            modal.classList.remove('visible');
        }}

        function selectDateFromPicker(data) {{
            closeDatePicker();
            preserveMapView = true; // Preserva la vista quando selezioni dal picker

            // Verifica se currentZona è una zona o un comune
            const zona = zone.find(z => z.nome === currentZona);
            if (zona) {{
                apriDettaglio(currentZona, data);
            }} else {{
                // È un comune singolo
                apriDettaglioComune(currentZona, data);
            }}
        }}

        function toggleAllDates() {{
            // Stub - filtro rimosso
        }}

        function navigateDate(direction) {{
            // Se currentDateIndex è -1 (utente schedulabile senza data),
            // salta alla data più vicina a oggi nella direzione richiesta
            if (currentDateIndex === -1) {{
                const oggiStr = getOggiStr();
                let targetIndex = -1;
                if (direction > 0) {{
                    // Cerca la prima data >= oggi
                    targetIndex = availableDates.findIndex(d => d >= oggiStr);
                    // Se non c'è nessuna data futura, vai all'ultima disponibile
                    if (targetIndex === -1) targetIndex = availableDates.length - 1;
                }} else {{
                    // Cerca l'ultima data <= oggi
                    for (let i = availableDates.length - 1; i >= 0; i--) {{
                        if (availableDates[i] <= oggiStr) {{ targetIndex = i; break; }}
                    }}
                    // Se non c'è nessuna data passata, vai alla prima disponibile
                    if (targetIndex === -1) targetIndex = 0;
                }}
                if (targetIndex < 0 || targetIndex >= availableDates.length) return;
                currentDateIndex = targetIndex;
            }} else {{
                if (currentDateIndex + direction < 0 || currentDateIndex + direction >= availableDates.length) return;
                currentDateIndex += direction;
            }}
            const newData = availableDates[currentDateIndex];

            preserveMapView = true; // Preserva la vista quando navigo tra le date

            // Verifica se currentZona è una zona o un comune
            const zona = zone.find(z => z.nome === currentZona);
            if (zona) {{
                apriDettaglio(currentZona, newData);
            }} else {{
                // È un comune singolo
                apriDettaglioComune(currentZona, newData);
            }}
        }}

        // ============================================================
        // FILTRI
        // ============================================================
        function popolaFiltroDate() {{
            // Estrai date uniche solo dai contatori visibili (da oggi a fine mese prossimo)
            const dateSet = new Set();
            contatoriVisibili.forEach(c => {{
                if (c.data) dateSet.add(c.data);
            }});

            const dateOrdinateArray = Array.from(dateSet).sort();

            const dataFilter = document.getElementById('dataFilter');
            const prevValue = dataFilter.value;
            dataFilter.innerHTML = '<option value="">📅 Tutte le date</option>';

            dateOrdinateArray.forEach(data => {{
                const option = document.createElement('option');
                option.value = data;
                option.textContent = formatDate(data);
                dataFilter.appendChild(option);
            }});

            // Ripristina selezione precedente se ancora disponibile
            if (prevValue && dateOrdinateArray.includes(prevValue)) {{
                dataFilter.value = prevValue;
            }}
        }}

        function applicaFiltri() {{
            currentLCL = document.getElementById('lclFilter').value;
            currentDataFiltro = document.getElementById('dataFilter').value;

            // Preserva la vista quando applichi filtri
            preserveMapView = true;

            if (currentView === 'zone') {{
                mostraVistaZone(document.getElementById('searchInput').value);
            }} else if (currentView === 'towns') {{
                mostraVistaComuni(document.getElementById('searchInput').value);
            }} else if (currentView === 'detail') {{
                // Se currentData è null, trova la prima data disponibile
                if (!currentData) {{
                    const zona = zone.find(z => z.nome === currentZona);
                    if (zona && zona.date.length > 0) {{
                        currentData = zona.date.map(d => d.data).sort()[0];
                    }} else if (!zona) {{
                        // Comune singolo
                        const dateSet = new Set();
                        contatori.forEach(c => {{
                            if (c.comune.toUpperCase() === currentZona.toUpperCase() && c.data) {{
                                dateSet.add(c.data);
                            }}
                        }});
                        const dateComune = Array.from(dateSet).sort();
                        if (dateComune.length > 0) {{
                            currentData = dateComune[0];
                        }}
                    }}
                }}
                if (currentData) {{
                    const zona = zone.find(z => z.nome === currentZona);
                    if (zona) {{
                        apriDettaglio(currentZona, currentData);
                    }} else {{
                        apriDettaglioComune(currentZona, currentData);
                    }}
                }} else {{
                    tornaAlleZone();
                }}
            }} else if (currentView === 'operatore' && currentOperatore && currentData) {{
                // Se siamo nella vista percorso operatore, aggiorna con il filtro LCL
                // (il cambio data è gestito da onDataFilterChange)
                mostraPercorsoCompletoOperatore(currentOperatore, currentData);
            }} else if (currentView === 'multiOperatori' && currentData) {{
                const selOps = getSelectedOperatori();
                if (selOps.length > 0) mostraPercorsoMultiOperatori(selOps, currentData);
                else mostraPercorsoTuttiOperatori(currentData);
            }} else if (currentView === 'tuttiOperatori' && currentData) {{
                mostraPercorsoTuttiOperatori(currentData);
            }}
        }}
        
        // ============================================================
        // FILTRO DATA + OPERATORE per percorso giornata completo
        // ============================================================
        function onDataFilterChange() {{
            const dataSelezionata = document.getElementById('dataFilter').value;
            const operatoreContainer = document.getElementById('operatoreFilterContainer');
            
            // Salva gli operatori precedentemente selezionati
            const operatoriPrecedenti = (currentView === 'operatore' && currentOperatore) ? [currentOperatore] : 
                                        (currentView === 'multiOperatori' ? getSelectedOperatori() : []);
            
            if (dataSelezionata) {{
                // Popola il dropdown operatori per questa data
                populateOperatoreFilter(dataSelezionata);
                operatoreContainer.classList.add('visible');
                
                // Se eravamo in vista operatore, prova a mantenere lo stesso operatore
                if (currentView === 'operatore' && operatoriPrecedenti.length === 1) {{
                    const opPrec = operatoriPrecedenti[0];
                    const operatoriData = new Set();
                    contatori.filter(c => c.data === dataSelezionata).forEach(c => {{
                        operatoriData.add(c.tecnico || 'TUTTI');
                    }});
                    
                    if (operatoriData.has(opPrec)) {{
                        // Operatore esiste, selezionalo e aggiorna la vista
                        setSelectedOperatori([opPrec]);
                        currentData = dataSelezionata;
                        mostraPercorsoCompletoOperatore(opPrec, dataSelezionata);
                        return;
                    }} else {{
                        resetOperatoreFilter();
                        currentOperatore = null;
                    }}
                }}
                
                // Mostra vista tutti gli operatori per questa data
                mostraPercorsoTuttiOperatori(dataSelezionata);
                return;
            }} else {{
                // Nascondi dropdown operatore e resetta
                operatoreContainer.classList.remove('visible');
                resetOperatoreFilter();
                
                // Se eravamo in vista operatore/tuttiOperatori/multiOperatori, torna alla vista zone
                if (currentView === 'operatore' || currentView === 'tuttiOperatori' || currentView === 'multiOperatori') {{
                    currentOperatore = null;
                    currentView = 'zone';
                }}
            }}
            
            // Applica i filtri normali (solo quando non c'è data selezionata)
            applicaFiltri();
        }}
        
        function populateOperatoreFilter(data) {{
            const dropdown = document.getElementById('operatoreMultiDropdown');
            
            // Trova tutti gli operatori attivi per questa data
            const operatoriData = new Set();
            contatori.filter(c => c.data === data).forEach(c => {{
                const tec = c.tecnico || 'TUTTI';
                operatoriData.add(tec);
            }});
            
            const operatoriOrdinati = Array.from(operatoriData).sort((a, b) => {{
                if (a === 'TUTTI') return 1;
                if (b === 'TUTTI') return -1;
                return a.localeCompare(b);
            }});
            
            let html = '';
            operatoriOrdinati.forEach(op => {{
                const count = contatori.filter(c => c.data === data && (c.tecnico || 'TUTTI') === op).length;
                const label = op === 'TUTTI' ? `📦 Senza Operatore (${{count}})` : `👤 ${{getDisplayName(op)}} (${{count}})`;
                const colore = getColore(op);
                html += `<label class="operatore-multi-item" data-operatore="${{op}}">
                    <input type="checkbox" value="${{op}}" onchange="onOperatoreFilterChange()">
                    <span class="op-color-dot" style="background:${{colore}}"></span>
                    <span>${{label}}</span>
                </label>`;
            }});
            
            dropdown.innerHTML = html;
            updateOperatoreTriggerText();
        }}
        
        function toggleOperatoreDropdown() {{
            const dropdown = document.getElementById('operatoreMultiDropdown');
            const trigger = document.getElementById('operatoreMultiTrigger');
            const isOpen = dropdown.classList.contains('open');
            dropdown.classList.toggle('open');
            trigger.classList.toggle('active');
            
            if (!isOpen) {{
                // Chiudi il dropdown se si clicca altrove
                setTimeout(() => {{
                    const closeHandler = function(e) {{
                        const container = document.getElementById('operatoreFilterContainer');
                        if (container && !container.contains(e.target)) {{
                            dropdown.classList.remove('open');
                            trigger.classList.remove('active');
                            document.removeEventListener('click', closeHandler);
                        }}
                    }};
                    document.addEventListener('click', closeHandler);
                }}, 0);
            }}
        }}
        
        function getSelectedOperatori() {{
            const checkboxes = document.querySelectorAll('#operatoreMultiDropdown input[type="checkbox"]:checked');
            return Array.from(checkboxes).map(cb => cb.value);
        }}
        
        function setSelectedOperatori(operatori) {{
            const checkboxes = document.querySelectorAll('#operatoreMultiDropdown input[type="checkbox"]');
            checkboxes.forEach(cb => {{
                cb.checked = operatori.includes(cb.value);
            }});
            updateOperatoreTriggerText();
        }}
        
        function resetOperatoreFilter() {{
            const checkboxes = document.querySelectorAll('#operatoreMultiDropdown input[type="checkbox"]');
            checkboxes.forEach(cb => {{ cb.checked = false; }});
            updateOperatoreTriggerText();
        }}
        
        function updateOperatoreTriggerText() {{
            const trigger = document.getElementById('operatoreMultiTrigger');
            const selected = getSelectedOperatori();
            if (selected.length === 0) {{
                trigger.textContent = '👥 Tutti gli operatori';
            }} else if (selected.length === 1) {{
                const op = selected[0];
                trigger.textContent = op === 'TUTTI' ? '📦 Senza Operatore' : '👤 ' + getDisplayName(op);
            }} else {{
                trigger.textContent = '👤 ' + selected.length + ' operatori selezionati';
            }}
        }}
        
        function onOperatoreFilterChange() {{
            const data = document.getElementById('dataFilter').value;
            const selected = getSelectedOperatori();
            
            if (!data) return;
            
            // Forza zoom sulla zona dell'operatore
            preserveMapView = false;
            
            updateOperatoreTriggerText();
            
            if (selected.length === 0) {{
                // Mostra il percorso di TUTTI gli operatori insieme
                mostraPercorsoTuttiOperatori(data);
                return;
            }}
            
            if (selected.length === 1) {{
                // Mostra il percorso completo di un singolo operatore
                mostraPercorsoCompletoOperatore(selected[0], data);
                return;
            }}
            
            // Multi-operatore: mostra i percorsi degli operatori selezionati
            mostraPercorsoMultiOperatori(selected, data);
        }}
        
        function mostraPercorsoTuttiOperatori(data) {{
            currentView = 'tuttiOperatori';
            currentData = data;
            currentOperatore = null;
            
            // Mostra breadcrumb
            document.getElementById('breadcrumb').classList.add('visible');
            document.getElementById('breadcrumbText').innerHTML = `<strong>📅 ${{formatDate(data)}}</strong> → <span style="color: var(--accent)">👥 Tutti gli operatori</span> (percorsi unificati)`;
            
            // Nascondi i filtri dettaglio
            document.getElementById('detailFilters').style.display = 'none';
            
            // Filtra tutti i contatori per questa data
            let contatoriData = contatori.filter(c => c.data === data);
            
            // Applica filtro LCL se attivo
            if (currentLCL) {{
                contatoriData = contatoriData.filter(c => c.lcl === currentLCL);
            }}
            
            // Raggruppa per operatore
            const operatoriMap = new Map();
            contatoriData.forEach(c => {{
                const tec = c.tecnico || 'TUTTI';
                if (!operatoriMap.has(tec)) {{
                    operatoriMap.set(tec, []);
                }}
                operatoriMap.get(tec).push(c);
            }});
            
            // Ordina ogni gruppo con l'algoritmo di ottimizzazione
            const percorsiPerOperatore = [];
            operatoriMap.forEach((contatoriOp, operatore) => {{
                const percorsoOrdinato = ordinaPercorsoJS(contatoriOp);
                percorsiPerOperatore.push({{
                    operatore: operatore,
                    percorso: percorsoOrdinato,
                    colore: getColore(operatore)
                }});
            }});
            
            // Ordina operatori alfabeticamente (TUTTI per ultimo)
            percorsiPerOperatore.sort((a, b) => {{
                if (a.operatore === 'TUTTI') return 1;
                if (b.operatore === 'TUTTI') return -1;
                return a.operatore.localeCompare(b.operatore);
            }});
            
            // Render sidebar con tutti i percorsi
            renderPercorsoTuttiOperatoriSidebar(percorsiPerOperatore, data);
            
            // Render mappa con tutti i percorsi
            renderPercorsoTuttiOperatoriMappa(percorsiPerOperatore, data);
            
            // Aggiorna statistiche
            const tuttiContatori = percorsiPerOperatore.flatMap(p => p.percorso);
            document.getElementById('statZone').textContent = new Set(tuttiContatori.map(c => c.comune)).size;
            document.getElementById('statContatori').textContent = tuttiContatori.length;
            document.getElementById('statAppuntamenti').textContent = tuttiContatori.filter(c => c.is_appuntamento).length;
        }}
        
        function mostraPercorsoMultiOperatori(operatoriSelezionati, data) {{
            currentView = 'multiOperatori';
            currentData = data;
            currentOperatore = null;
            
            // Mostra breadcrumb
            const nomi = operatoriSelezionati.map(op => op === 'TUTTI' ? 'Senza Operatore' : getDisplayName(op));
            document.getElementById('breadcrumb').classList.add('visible');
            document.getElementById('breadcrumbText').innerHTML = `<strong>📅 ${{formatDate(data)}}</strong> → <span style="color: var(--accent)">👤 ${{nomi.join(', ')}}</span> (percorsi)`;
            
            // Nascondi i filtri dettaglio
            document.getElementById('detailFilters').style.display = 'none';
            
            // Filtra i contatori per questa data e per gli operatori selezionati
            let contatoriData = contatori.filter(c => 
                c.data === data && operatoriSelezionati.includes(c.tecnico || 'TUTTI')
            );
            
            // Applica filtro LCL se attivo
            if (currentLCL) {{
                contatoriData = contatoriData.filter(c => c.lcl === currentLCL);
            }}
            
            // Raggruppa per operatore
            const operatoriMap = new Map();
            contatoriData.forEach(c => {{
                const tec = c.tecnico || 'TUTTI';
                if (!operatoriMap.has(tec)) {{
                    operatoriMap.set(tec, []);
                }}
                operatoriMap.get(tec).push(c);
            }});
            
            // Ordina ogni gruppo con l'algoritmo di ottimizzazione
            const percorsiPerOperatore = [];
            operatoriMap.forEach((contatoriOp, operatore) => {{
                const percorsoOrdinato = ordinaPercorsoJS(contatoriOp);
                percorsiPerOperatore.push({{
                    operatore: operatore,
                    percorso: percorsoOrdinato,
                    colore: getColore(operatore)
                }});
            }});
            
            // Ordina operatori alfabeticamente (TUTTI per ultimo)
            percorsiPerOperatore.sort((a, b) => {{
                if (a.operatore === 'TUTTI') return 1;
                if (b.operatore === 'TUTTI') return -1;
                return a.operatore.localeCompare(b.operatore);
            }});
            
            // Render sidebar con i percorsi selezionati
            renderPercorsoTuttiOperatoriSidebar(percorsiPerOperatore, data);
            
            // Render mappa con i percorsi selezionati
            renderPercorsoTuttiOperatoriMappa(percorsiPerOperatore, data);
            
            // Aggiorna statistiche
            const tuttiContatori = percorsiPerOperatore.flatMap(p => p.percorso);
            document.getElementById('statZone').textContent = new Set(tuttiContatori.map(c => c.comune)).size;
            document.getElementById('statContatori').textContent = tuttiContatori.length;
            document.getElementById('statAppuntamenti').textContent = tuttiContatori.filter(c => c.is_appuntamento).length;
        }}
        
        function renderPercorsoTuttiOperatoriSidebar(percorsiPerOperatore, data) {{
            const resultsDiv = document.getElementById('results');
            let html = '';
            
            // Header generale
            const totContatori = percorsiPerOperatore.reduce((sum, p) => sum + p.percorso.length, 0);
            const totComuni = new Set(percorsiPerOperatore.flatMap(p => p.percorso).map(c => c.comune)).size;
            
            html += `
                <div style="background: linear-gradient(135deg, var(--bg-tertiary) 0%, var(--bg-secondary) 100%); border-radius: 12px; padding: 16px; margin-bottom: 15px; border: 2px solid var(--accent);">
                    <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 10px;">
                        <div style="font-weight: 700; font-size: 1.1em; color: var(--text-primary);">👥 Tutti gli operatori</div>
                    </div>
                    <div style="font-size: 0.85em; color: var(--text-secondary);">
                        📅 ${{formatDate(data)}} | 👤 ${{percorsiPerOperatore.length}} operatori | 📍 ${{totContatori}} tappe | 🏘️ ${{totComuni}} comuni
                    </div>
                </div>
            `;
            
            // Per ogni operatore, mostra il suo percorso
            percorsiPerOperatore.forEach(({{operatore, percorso, colore}}) => {{
                const nomeOp = operatore === 'TUTTI' ? 'Senza Operatore' : getDisplayName(operatore);
                const comuniOp = new Set(percorso.map(c => c.comune)).size;
                
                html += `
                    <div style="background: linear-gradient(135deg, var(--bg-tertiary) 0%, var(--bg-secondary) 100%); border-radius: 12px; padding: 12px; margin-bottom: 12px; border-left: 4px solid ${{colore}}; cursor: pointer;" onclick="setSelectedOperatori(['${{operatore}}']); onOperatoreFilterChange();">
                        <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 8px;">
                            <div style="width: 14px; height: 14px; background: ${{colore}}; border-radius: 3px;"></div>
                            <div style="font-weight: 600; font-size: 1em; color: var(--text-primary);">👤 ${{nomeOp}}</div>
                            <div style="margin-left: auto; font-size: 0.85em; color: var(--text-secondary);">📍 ${{percorso.length}} | 🏘️ ${{comuniOp}}</div>
                        </div>
                        <div style="font-size: 0.8em; color: var(--text-secondary); display: flex; flex-wrap: wrap; gap: 4px;">
                `;
                
                // Mostra i primi 3 comuni come preview
                const comuniPreview = [...new Set(percorso.map(c => c.comune))].slice(0, 3);
                comuniPreview.forEach(comune => {{
                    html += `<span style="background: var(--bg-secondary); padding: 2px 6px; border-radius: 4px;">${{comune}}</span>`;
                }});
                if ([...new Set(percorso.map(c => c.comune))].length > 3) {{
                    html += `<span style="color: var(--accent);">+${{[...new Set(percorso.map(c => c.comune))].length - 3}} altri</span>`;
                }}
                
                html += `
                        </div>
                    </div>
                `;
            }});

            // Sezione schedulabili (se filtro attivo)
            if (filterSchedulabili) {{
                const schedulabili = getContatoriSchedulabili();
                if (schedulabili.length > 0) {{
                    html += `
                        <div style="background: linear-gradient(135deg, rgba(6,182,212,0.15) 0%, var(--bg-secondary) 100%); border-radius: 12px; padding: 14px; margin-top: 15px; border: 2px solid var(--accent-secondary, #06b6d4);">
                            <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 10px;">
                                <span style="font-size: 1.1em;">📋</span>
                                <div style="font-weight: 700; font-size: 1em; color: var(--accent-secondary, #06b6d4);">Schedulabili</div>
                                <span style="margin-left: auto; background: var(--accent-secondary, #06b6d4); color: #fff; border-radius: 10px; padding: 1px 8px; font-size: 0.75rem; font-weight: 700;">${{schedulabili.length}}</span>
                            </div>
                            <div class="date-list">
                                ${{schedulabili.map(c => `
                                    <div class="date-item schedulabile-item" onclick="event.stopPropagation(); mostraContatorePopup(contatori.find(cc => cc.id === '${{c.id}}'))">
                                        <div style="display: flex; flex-direction: column; flex: 1; gap: 2px;">
                                            <span style="font-weight: 600; font-size: 0.85rem;">📍 ${{c.comune}}</span>
                                            <span style="font-size: 0.78rem; color: var(--text-muted);">${{c.via || 'N/A'}} ${{c.civico || ''}}</span>
                                            <span style="font-size: 0.78rem; color: var(--text-muted);">🔢 ${{c.eneltel}}${{c.tipo_ce ? ` · ⚡ ${{c.tipo_ce}}` : ''}}</span>
                                            ${{c.data ? `<span style="font-size: 0.75rem; color: var(--danger, #ef4444);">⏰ era: ${{formatDate(c.data)}}</span>` : `<span style="font-size: 0.75rem; color: var(--accent-secondary, #06b6d4);">📋 senza data</span>`}}
                                            ${{c.is_appuntamento ? `<span style="font-size: 0.75rem; color: #f59e0b;">📞 Appuntamento${{c.ora_appuntamento ? ' ' + c.ora_appuntamento : ''}}</span>` : ''}}
                                        </div>
                                        <span class="arrow">→</span>
                                    </div>
                                `).join('')}}
                            </div>
                        </div>
                    `;
                }}
            }}
            
            resultsDiv.innerHTML = html;
        }}
        
        function renderPercorsoTuttiOperatoriMappa(percorsiPerOperatore, data) {{
            // Salva la vista corrente se richiesto
            let savedCenter = null;
            let savedZoom = null;
            if (preserveMapView) {{
                savedCenter = map.getCenter();
                savedZoom = map.getZoom();
            }}

            clearMap();
            
            const allPoints = [];
            
            // Marker deposito
            const depositoMarker = L.marker([deposito.lat, deposito.lon], {{
                icon: L.divIcon({{
                    className: 'deposito-marker',
                    html: '<div style="background: #2ecc71; color: white; padding: 8px 12px; border-radius: 20px; font-weight: 600; box-shadow: 0 2px 8px rgba(0,0,0,0.3); white-space: nowrap;">🏠 Deposito</div>',
                    iconSize: [100, 40],
                    iconAnchor: [50, 40]
                }})
            }}).addTo(map);
            depositoMarker.bindPopup('<b>🏠 Deposito</b><br>' + deposito.nome);
            allPoints.push([deposito.lat, deposito.lon]);
            
            // Per ogni operatore, disegna il percorso
            percorsiPerOperatore.forEach(({{operatore, percorso, colore}}) => {{
                const lista = percorso;
                const listaConIndice = lista.map((c, idx) => ({{
                    ...c,
                    indiceTecnico: idx,
                    colore: colore
                }}));
                
                // Filtra il contatore pinnato PRIMA del raggruppamento
                const contPerMappa = pinnedContatore
                    ? listaConIndice.filter(c => c.eneltel !== pinnedContatore.eneltel)
                    : listaConIndice;
                
                // Raggruppa contatori sovrapposti
                const gruppi = raggruppaContatoriSovrapposti(contPerMappa);
                
                gruppi.forEach(gruppo => {{
                    const isGruppo = gruppo.contatori.length > 1;
                    const primoContatore = gruppo.contatori[0];
                    
                    if (isGruppo) {{
                        const marker = creaMarkerGruppo(gruppo, colore);
                        marker.addTo(map);
                        markers.push(marker);
                    }} else {{
                        const c = primoContatore;
                        const isApp = c.is_appuntamento;
                        const eneltelLast3 = c.eneltel.slice(-3);
                        const idx = c.indiceTecnico;
                        
                        const prevPoint = idx === 0 
                            ? {{ lat: deposito.lat, lon: deposito.lon, isDeposito: true, num: 0 }}
                            : {{ lat: lista[idx-1].lat, lon: lista[idx-1].lon, isDeposito: false, num: idx }};
                        
                        const icon = L.divIcon({{
                            className: 'marker-contatore' + (isApp ? ' marker-appuntamento' : ''),
                            html: `<div style="background: ${{colore}}; width: 100%; height: 100%; border-radius: 50%; display: flex; align-items: center; justify-content: center; position: relative;">
                                ${{c.indiceTecnico + 1}}
                                ${{showEneltelLabels ? `<span class="marker-label">${{eneltelLast3}}</span>` : ''}}
                            </div>`,
                            iconSize: [24, 24],
                            iconAnchor: [12, 12]
                        }});
                        
                        const marker = L.marker([c.lat, c.lon], {{ icon }})
                            .bindPopup(() => createContatorePopup(c, c.indiceTecnico + 1, colore, prevPoint))
                            .addTo(map);
                        
                        marker.contatore = c;
                        marker.numeroTappa = c.indiceTecnico + 1;
                        marker.coloreTecnico = colore;
                        marker.prevPoint = prevPoint;
                        markers.push(marker);
                    }}
                    
                    allPoints.push([gruppo.lat, gruppo.lon]);
                }});
                
                // Disegna linea del percorso
                if (lista.length > 0) {{
                    const rottaOSRM = rotte[data] && rotte[data][operatore];
                    
                    if (rottaOSRM && rottaOSRM.length > 0) {{
                        const latLngs = rottaOSRM.map(([lon, lat]) => [lat, lon]);
                        const polyline = L.polyline(latLngs, {{
                            color: colore,
                            weight: 4,
                            opacity: 0.8
                        }}).addTo(map);
                        routePolylines.push(polyline);
                    }} else {{
                        const percorsoCoords = [[deposito.lat, deposito.lon]];
                        lista.forEach(c => percorsoCoords.push([c.lat, c.lon]));
                        percorsoCoords.push([deposito.lat, deposito.lon]);
                        
                        const polyline = L.polyline(percorsoCoords, {{
                            color: colore,
                            weight: 3,
                            opacity: 0.7,
                            dashArray: '10, 10'
                        }}).addTo(map);
                        routePolylines.push(polyline);
                    }}
                }}
            }});

            // Aggiungi marker schedulabili se filtro attivo
            if (filterSchedulabili) {{
                aggiungiMarkerSchedulabili(allPoints);
            }}
            
            // Aggiungi marker pinned se presente
            if (pinnedContatore) {{
                const pinIcon = L.divIcon({{
                    className: 'marker-pinned',
                    html: `<div style="background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); width: 32px; height: 32px; border-radius: 50%; display: flex; align-items: center; justify-content: center; border: 3px solid white; box-shadow: 0 4px 12px rgba(0,0,0,0.4); font-size: 16px;">📌</div>`,
                    iconSize: [32, 32],
                    iconAnchor: [16, 16]
                }});

                if (pinnedMarker) map.removeLayer(pinnedMarker);

                pinnedMarker = L.marker([pinnedContatore.lat, pinnedContatore.lon], {{ icon: pinIcon, zIndexOffset: 10000 }})
                    .bindPopup(() => createContatorePopup(pinnedContatore, '📌', '#f5576c'))
                    .addTo(map);

                allPoints.push([pinnedContatore.lat, pinnedContatore.lon]);
            }}
            
            // Ripristina la vista salvata o centra sui punti
            if (savedCenter && savedZoom) {{
                map.setView(savedCenter, savedZoom);
                preserveMapView = false;
            }} else if (allPoints.length > 1) {{
                const bounds = L.latLngBounds(allPoints);
                map.fitBounds(bounds, {{ padding: [50, 50] }});
            }}
        }}
        
        function mostraPercorsoCompletoOperatore(operatore, data) {{
            currentView = 'operatore';
            currentData = data;
            currentOperatore = operatore;  // Salva operatore per refresh
            
            // Mostra breadcrumb
            const nomeOp = operatore === 'TUTTI' ? 'Senza Operatore' : getDisplayName(operatore);
            document.getElementById('breadcrumb').classList.add('visible');
            document.getElementById('breadcrumbText').innerHTML = `<strong>📅 ${{formatDate(data)}}</strong> → <span style="color: var(--accent)">👤 ${{nomeOp}}</span> (percorso completo)`;
            
            // Nascondi i filtri dettaglio
            document.getElementById('detailFilters').style.display = 'none';
            
            // Filtra TUTTI i contatori dell'operatore per questa data (tutti i comuni)
            let contatoriOperatore = contatori.filter(c => 
                c.data === data && 
                (c.tecnico || 'TUTTI') === operatore
            );
            
            // Applica filtro LCL se attivo
            if (currentLCL) {{
                contatoriOperatore = contatoriOperatore.filter(c => c.lcl === currentLCL);
            }}
            
            // Ordina con l'algoritmo di ottimizzazione
            const percorsoOrdinato = ordinaPercorsoJS(contatoriOperatore);
            
            // Render sidebar con info percorso completo
            renderPercorsoOperatoreSidebar(percorsoOrdinato, data, operatore);
            
            // Render mappa con percorso completo
            renderPercorsoOperatoreMappa(percorsoOrdinato, data, operatore);
            
            // Aggiorna statistiche
            document.getElementById('statZone').textContent = new Set(percorsoOrdinato.map(c => c.comune)).size;
            document.getElementById('statContatori').textContent = percorsoOrdinato.length;
            document.getElementById('statAppuntamenti').textContent = percorsoOrdinato.filter(c => c.is_appuntamento).length;
        }}
        
        function mostraPercorsoOperatoreConRicerca(operatore, data, searchQuery) {{
            // Mantieni la vista operatore senza resettare
            currentView = 'operatore';
            currentData = data;
            currentOperatore = operatore;
            
            // Mostra breadcrumb
            const nomeOp = operatore === 'TUTTI' ? 'Senza Operatore' : getDisplayName(operatore);
            document.getElementById('breadcrumb').classList.add('visible');
            let breadcrumbText = `<strong>📅 ${{formatDate(data)}}</strong> → <span style="color: var(--accent)">👤 ${{nomeOp}}</span> (percorso completo)`;
            if (searchQuery) {{
                breadcrumbText += ` → <span style="color: #ffc107;">🔍 "${{searchQuery}}"</span>`;
            }}
            document.getElementById('breadcrumbText').innerHTML = breadcrumbText;
            
            // Nascondi i filtri dettaglio
            document.getElementById('detailFilters').style.display = 'none';
            
            // Filtra TUTTI i contatori dell'operatore per questa data
            let contatoriOperatore = contatori.filter(c => 
                c.data === data && 
                (c.tecnico || 'TUTTI') === operatore
            );
            
            // Applica filtro LCL se attivo
            if (currentLCL) {{
                contatoriOperatore = contatoriOperatore.filter(c => c.lcl === currentLCL);
            }}
            
            // Applica filtro di ricerca se presente
            if (searchQuery) {{
                const q = searchQuery.toLowerCase();
                contatoriOperatore = contatoriOperatore.filter(c =>
                    c.comune.toLowerCase().includes(q) ||
                    c.cliente.toLowerCase().includes(q) ||
                    c.pod.toLowerCase().includes(q) ||
                    c.eneltel.toLowerCase().includes(q) ||
                    c.via.toLowerCase().includes(q) ||
                    (c.telefono && c.telefono.toLowerCase().includes(q)) ||
                    (c.note && c.note.toLowerCase().includes(q))
                );
            }}
            
            // Ordina con l'algoritmo di ottimizzazione
            const percorsoOrdinato = ordinaPercorsoJS(contatoriOperatore);
            
            // Render sidebar con info percorso
            renderPercorsoOperatoreSidebar(percorsoOrdinato, data, operatore);
            
            // Render mappa con percorso
            renderPercorsoOperatoreMappa(percorsoOrdinato, data, operatore);
            
            // Aggiorna statistiche
            document.getElementById('statZone').textContent = new Set(percorsoOrdinato.map(c => c.comune)).size;
            document.getElementById('statContatori').textContent = percorsoOrdinato.length;
            document.getElementById('statAppuntamenti').textContent = percorsoOrdinato.filter(c => c.is_appuntamento).length;
        }}
        
        function renderPercorsoOperatoreSidebar(lista, data, operatore) {{
            const resultsDiv = document.getElementById('results');
            const colore = getColore(operatore);
            const nomeOp = operatore === 'TUTTI' ? 'Senza Operatore' : getDisplayName(operatore);
            
            let html = '';
            
            // Header con info percorso
            html += `
                <div style="background: linear-gradient(135deg, var(--bg-tertiary) 0%, var(--bg-secondary) 100%); border-radius: 12px; padding: 16px; margin-bottom: 15px; border: 2px solid ${{colore}};">
                    <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 10px;">
                        <div style="width: 16px; height: 16px; background: ${{colore}}; border-radius: 4px;"></div>
                        <div style="font-weight: 700; font-size: 1.1em; color: var(--text-primary);">👤 ${{nomeOp}}</div>
                    </div>
                    <div style="font-size: 0.85em; color: var(--text-secondary);">
                        📅 ${{formatDate(data)}} | 📍 ${{lista.length}} tappe | 🏘️ ${{new Set(lista.map(c => c.comune)).size}} comuni
                    </div>
                </div>
            `;
            
            // Deposito partenza
            html += `
                <div class="tappa-card deposito">
                    <div class="tappa-header">
                        <div class="tappa-numero">🏠</div>
                        <div class="tappa-cliente">${{deposito.nome}}</div>
                    </div>
                    <div class="tappa-dettagli">
                        <div>📍 Punto di partenza</div>
                    </div>
                </div>
            `;
            
            // Tappe ordinate
            lista.forEach((c, idx) => {{
                // Calcola tempo dal punto precedente
                let tempoInfo = '';
                if (idx === 0) {{
                    // Prima tappa: distanza dal deposito
                    const dist = calcolaDistanzaKm(deposito.lat, deposito.lon, c.lat, c.lon);
                    const tempo = calcolaTempoMinuti(dist);
                    tempoInfo = `<div class="tappa-tempo">🚗 ${{formattaTempo(tempo)}} dal deposito (~${{dist.toFixed(1)}} km)</div>`;
                }} else {{
                    // Tappa successiva: distanza dalla precedente
                    const prev = lista[idx - 1];
                    const dist = calcolaDistanzaKm(prev.lat, prev.lon, c.lat, c.lon);
                    const tempo = calcolaTempoMinuti(dist);
                    tempoInfo = `<div class="tappa-tempo">🚗 ${{formattaTempo(tempo)}} dalla tappa ${{idx}} (~${{dist.toFixed(1)}} km)</div>`;
                }}
                
                html += `
                    <div class="tappa-card ${{c.is_appuntamento ? 'appuntamento' : ''}}" 
                         onclick="evidenziaContatore('${{c.id}}')"
                         data-id="${{c.id}}"
                         data-eneltel="${{c.eneltel}}">
                        <div class="tappa-header">
                            <div class="tappa-numero" style="background: ${{colore}}">${{idx + 1}}</div>
                            <div class="tappa-cliente">${{c.cliente || 'N/D'}}</div>
                            <button class="tappa-edit-btn" onclick="event.stopPropagation(); apriVistaModifica('${{c.eneltel}}')" title="Modifica">✏️</button>
                            ${{c.is_appuntamento ? `<div class="tappa-app-badge">☎️ ${{c.ora_appuntamento || 'APP'}}</div>` : ''}}
                        </div>
                        ${{tempoInfo}}
                        <div class="tappa-dettagli">
                            <div>📍 <strong>${{c.comune}}</strong> - ${{c.via || 'N/D'}}</div>
                            <div>🔌 ${{c.eneltel}}${{c.lcl ? ` | 🏷️ ${{c.lcl}}` : ''}}</div>
                            ${{c.telefono ? `<div>📱 ${{c.telefono}}</div>` : ''}}
                            ${{c.note ? `<div>📝 ${{c.note}}</div>` : ''}}
                        </div>
                        ${{c.orari_lettere && !c.is_appuntamento ? `<div class="tappa-orari-lettere">✉️ ${{c.orari_lettere}}</div>` : ''}}
                    </div>
                `;
            }});
            
            // Deposito ritorno
            html += `
                <div class="tappa-card deposito">
                    <div class="tappa-header">
                        <div class="tappa-numero">🏠</div>
                        <div class="tappa-cliente">${{deposito.nome}}</div>
                    </div>
                    <div class="tappa-dettagli">
                        <div>📍 Rientro al deposito</div>
                    </div>
                </div>
            `;

            // Sezione schedulabili (se filtro attivo)
            if (filterSchedulabili) {{
                const schedulabili = getContatoriSchedulabili();
                if (schedulabili.length > 0) {{
                    html += `
                        <div style="background: linear-gradient(135deg, rgba(6,182,212,0.15) 0%, var(--bg-secondary) 100%); border-radius: 12px; padding: 14px; margin-top: 15px; border: 2px solid var(--accent-secondary, #06b6d4);">
                            <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 10px;">
                                <span style="font-size: 1.1em;">📋</span>
                                <div style="font-weight: 700; font-size: 1em; color: var(--accent-secondary, #06b6d4);">Schedulabili</div>
                                <span style="margin-left: auto; background: var(--accent-secondary, #06b6d4); color: #fff; border-radius: 10px; padding: 1px 8px; font-size: 0.75rem; font-weight: 700;">${{schedulabili.length}}</span>
                            </div>
                            <div class="date-list">
                                ${{schedulabili.map(c => `
                                    <div class="date-item schedulabile-item" onclick="event.stopPropagation(); mostraContatorePopup(contatori.find(cc => cc.id === '${{c.id}}'))">
                                        <div style="display: flex; flex-direction: column; flex: 1; gap: 2px;">
                                            <span style="font-weight: 600; font-size: 0.85rem;">📍 ${{c.comune}}</span>
                                            <span style="font-size: 0.78rem; color: var(--text-muted);">${{c.via || 'N/A'}} ${{c.civico || ''}}</span>
                                            <span style="font-size: 0.78rem; color: var(--text-muted);">🔢 ${{c.eneltel}}${{c.tipo_ce ? ` · ⚡ ${{c.tipo_ce}}` : ''}}</span>
                                            ${{c.data ? `<span style="font-size: 0.75rem; color: var(--danger, #ef4444);">⏰ era: ${{formatDate(c.data)}}</span>` : `<span style="font-size: 0.75rem; color: var(--accent-secondary, #06b6d4);">📋 senza data</span>`}}
                                            ${{c.is_appuntamento ? `<span style="font-size: 0.75rem; color: #f59e0b;">📞 Appuntamento${{c.ora_appuntamento ? ' ' + c.ora_appuntamento : ''}}</span>` : ''}}
                                        </div>
                                        <span class="arrow">→</span>
                                    </div>
                                `).join('')}}
                            </div>
                        </div>
                    `;
                }}
            }}
            
            resultsDiv.innerHTML = html;
        }}
        
        function renderPercorsoOperatoreMappa(lista, data, operatore) {{
            // Salva la vista corrente se richiesto
            let savedCenter = null;
            let savedZoom = null;
            if (preserveMapView) {{
                savedCenter = map.getCenter();
                savedZoom = map.getZoom();
            }}

            clearMap();
            
            const colore = getColore(operatore);
            const allPoints = [];
            
            // Marker deposito
            const depositoMarker = L.marker([deposito.lat, deposito.lon], {{
                icon: L.divIcon({{
                    className: 'deposito-marker',
                    html: '<div style="background: #2ecc71; color: white; padding: 8px 12px; border-radius: 20px; font-weight: 600; box-shadow: 0 2px 8px rgba(0,0,0,0.3); white-space: nowrap;">🏠 Deposito</div>',
                    iconSize: [100, 40],
                    iconAnchor: [50, 40]
                }})
            }}).addTo(map);
            depositoMarker.bindPopup('<b>🏠 Deposito</b><br>' + deposito.nome);
            allPoints.push([deposito.lat, deposito.lon]);
            
            // Prepara lista con indice
            const listaConIndice = lista.map((c, idx) => ({{
                ...c,
                indiceTecnico: idx,
                colore: colore
            }}));
            
            // Filtra il contatore pinnato PRIMA del raggruppamento
            const contPerMappa = pinnedContatore
                ? listaConIndice.filter(c => c.eneltel !== pinnedContatore.eneltel)
                : listaConIndice;
            
            // Raggruppa contatori sovrapposti
            const gruppi = raggruppaContatoriSovrapposti(contPerMappa);
            
            gruppi.forEach(gruppo => {{
                const isGruppo = gruppo.contatori.length > 1;
                const primoContatore = gruppo.contatori[0];
                
                if (isGruppo) {{
                    // Usa la funzione dedicata per i marker di gruppo
                    const marker = creaMarkerGruppo(gruppo, colore);
                    marker.addTo(map);
                    markers.push(marker);
                }} else {{
                    // Marker singolo (comportamento normale)
                    const c = primoContatore;
                    const isApp = c.is_appuntamento;
                    const eneltelLast3 = c.eneltel.slice(-3);
                    const idx = c.indiceTecnico;
                    
                    // Calcola punto precedente per il tempo di percorrenza
                    const prevPoint = idx === 0 
                        ? {{ lat: deposito.lat, lon: deposito.lon, isDeposito: true, num: 0 }}
                        : {{ lat: lista[idx-1].lat, lon: lista[idx-1].lon, isDeposito: false, num: idx }};
                    
                    const icon = L.divIcon({{
                        className: 'marker-contatore' + (isApp ? ' marker-appuntamento' : ''),
                        html: `<div style="background: ${{colore}}; width: 100%; height: 100%; border-radius: 50%; display: flex; align-items: center; justify-content: center; position: relative;">
                            ${{c.indiceTecnico + 1}}
                            ${{showEneltelLabels ? `<span class="marker-label">${{eneltelLast3}}</span>` : ''}}
                        </div>`,
                        iconSize: [24, 24],
                        iconAnchor: [12, 12]
                    }});
                    
                    const marker = L.marker([c.lat, c.lon], {{ icon }})
                        .bindPopup(() => createContatorePopup(c, c.indiceTecnico + 1, colore, prevPoint))
                        .addTo(map);
                    
                    marker.contatore = c;
                    marker.numeroTappa = c.indiceTecnico + 1;
                    marker.coloreTecnico = colore;
                    marker.prevPoint = prevPoint;
                    markers.push(marker);
                }}
                
                allPoints.push([gruppo.lat, gruppo.lon]);
            }});
            
            // Disegna linea del percorso - usa geometria OSRM se disponibile
            if (lista.length > 0) {{
                // Verifica se esiste la rotta OSRM per questo operatore/data
                const rottaOSRM = rotte[data] && rotte[data][operatore];
                
                if (rottaOSRM && rottaOSRM.length > 0) {{
                    // Converti da [lon, lat] (OSRM) a [lat, lon] (Leaflet)
                    const latLngs = rottaOSRM.map(([lon, lat]) => [lat, lon]);
                    
                    // Usa la geometria OSRM (strada reale)
                    const polyline = L.polyline(latLngs, {{
                        color: colore,
                        weight: 4,
                        opacity: 0.8
                    }}).addTo(map);
                    routePolylines.push(polyline);
                }} else {{
                    // Fallback: linea retta tratteggiata
                    const percorsoCoords = [[deposito.lat, deposito.lon]];
                    lista.forEach(c => percorsoCoords.push([c.lat, c.lon]));
                    percorsoCoords.push([deposito.lat, deposito.lon]); // Ritorno
                    
                    const polyline = L.polyline(percorsoCoords, {{
                        color: colore,
                        weight: 3,
                        opacity: 0.7,
                        dashArray: '10, 10'
                    }}).addTo(map);
                    routePolylines.push(polyline);
                }}
            }}

            // Aggiungi marker schedulabili se filtro attivo
            if (filterSchedulabili) {{
                aggiungiMarkerSchedulabili(allPoints);
            }}
            
            // Aggiungi marker pinned se presente
            if (pinnedContatore) {{
                const pinIcon = L.divIcon({{
                    className: 'marker-pinned',
                    html: `<div style="background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); width: 32px; height: 32px; border-radius: 50%; display: flex; align-items: center; justify-content: center; border: 3px solid white; box-shadow: 0 4px 12px rgba(0,0,0,0.4); font-size: 16px;">📌</div>`,
                    iconSize: [32, 32],
                    iconAnchor: [16, 16]
                }});

                if (pinnedMarker) map.removeLayer(pinnedMarker);

                pinnedMarker = L.marker([pinnedContatore.lat, pinnedContatore.lon], {{ icon: pinIcon, zIndexOffset: 10000 }})
                    .bindPopup(() => createContatorePopup(pinnedContatore, '📌', '#f5576c'))
                    .addTo(map);

                allPoints.push([pinnedContatore.lat, pinnedContatore.lon]);
            }}
            
            // Ripristina la vista salvata o centra sui punti
            if (savedCenter && savedZoom) {{
                map.setView(savedCenter, savedZoom);
                preserveMapView = false; // Reset del flag
            }} else if (allPoints.length > 1) {{
                const bounds = L.latLngBounds(allPoints);
                map.fitBounds(bounds, {{ padding: [50, 50] }});
            }}
        }}
        
        // ============================================================
        // VISTA ZONE
        // ============================================================
        function mostraVistaZone(searchQuery = '') {{
            currentView = 'zone';
            currentZona = null;
            currentData = null;
            detailSelectedOperatori.clear();

            // Ripristina select filtro date (ma non durante auto-refresh)
            if (!isAutoRefresh) {{
                document.getElementById('dataFilterRow').style.display = 'flex';
                document.getElementById('detailFilters').style.display = 'none';
                filterAppuntamentiOnly = false;
                mostraRotteAttivo = false;
                
                // Gestisci dropdown operatore in base alla data selezionata
                const dataSelezionata = document.getElementById('dataFilter').value;
                if (!dataSelezionata) {{
                    document.getElementById('operatoreFilterContainer').classList.remove('visible');
                    resetOperatoreFilter();
                }}

                document.getElementById('breadcrumb').classList.remove('visible');
            }}

            // Usa zoneVisibili per mostrare solo date da oggi a fine mese prossimo
            let zoneFiltrate = zoneVisibili;
            const q = searchQuery.toLowerCase();

            // Filtra per LCL (usa contatoriVisibili per vista normale)
            if (currentLCL) {{
                const contatoriLCL = contatoriVisibili.filter(c => c.lcl === currentLCL);
                const comuniConLCL = new Set(contatoriLCL.map(c => c.comune.toLowerCase()));
                zoneFiltrate = zoneFiltrate.filter(z =>
                    z.comuni.some(c => comuniConLCL.has(c.toLowerCase()))
                );
            }}

            // Filtra per data
            if (currentDataFiltro) {{
                zoneFiltrate = zoneFiltrate.filter(z =>
                    z.date.some(d => d.data === currentDataFiltro)
                ).map(z => {{
                    // Clona la zona ma mostra solo la data filtrata
                    return {{
                        ...z,
                        date: z.date.filter(d => d.data === currentDataFiltro),
                        totale: z.date.filter(d => d.data === currentDataFiltro)
                                     .reduce((sum, d) => sum + d.contatori, 0)
                    }};
                }});
            }}

            // Filtra per solo appuntamenti
            if (filterAppuntamentiOnly) {{
                const comuniConApp = new Set(
                    contatoriVisibili.filter(c => c.is_appuntamento).map(c => c.comune.toLowerCase())
                );
                zoneFiltrate = zoneFiltrate.filter(z =>
                    z.comuni.some(c => comuniConApp.has(c.toLowerCase()))
                ).map(z => {{
                    // Ricalcola i contatori mostrando solo appuntamenti
                    const comuniZona = z.comuni.map(c => c.toLowerCase());
                    const appPerData = {{}};
                    contatoriVisibili.filter(c =>
                        c.is_appuntamento && comuniZona.includes(c.comune.toLowerCase())
                    ).forEach(c => {{
                        if (!appPerData[c.data]) appPerData[c.data] = 0;
                        appPerData[c.data]++;
                    }});
                    return {{
                        ...z,
                        date: z.date.map(d => ({{
                            ...d,
                            contatori: appPerData[d.data] || 0,
                            appuntamenti: appPerData[d.data] || 0
                        }})).filter(d => d.contatori > 0),
                        totale: Object.values(appPerData).reduce((a, b) => a + b, 0)
                    }};
                }}).filter(z => z.totale > 0);
            }}

            // Filtra per schedulabili
            if (filterSchedulabili) {{
                // Mostra tutte le zone (anche quelle senza date future) che hanno contatori schedulabili
                const allZoneConSchedulabili = zone.map(z => {{
                    const numSched = countSchedulabiliPerZona(z);
                    return {{ ...z, _schedulabili: numSched, date: [], totale: numSched }};
                }}).filter(z => z._schedulabili > 0);
                
                // Applica filtri di ricerca
                if (q && !advancedSearchMode) {{
                    zoneFiltrate = allZoneConSchedulabili.filter(z =>
                        z.nome.toLowerCase().includes(q) ||
                        z.comuni.some(c => c.toLowerCase().includes(q))
                    );
                }} else {{
                    zoneFiltrate = allZoneConSchedulabili;
                }}
                
                // Applica filtro LCL
                if (currentLCL) {{
                    const schedLCL = getContatoriSchedulabili().filter(c => c.lcl === currentLCL);
                    const comuniConLCL = new Set(schedLCL.map(c => c.comune.toLowerCase()));
                    zoneFiltrate = zoneFiltrate.filter(z =>
                        z.comuni.some(c => comuniConLCL.has(c.toLowerCase()))
                    );
                }}
                
                zoneFiltrate.sort((a, b) => b.totale - a.totale);
                
                renderZoneSidebar(zoneFiltrate);
                renderZoneMappa(zoneFiltrate);
                
                const totSched = zoneFiltrate.reduce((s, z) => s + z.totale, 0);
                document.getElementById('statZone').textContent = zoneFiltrate.length;
                document.getElementById('statContatori').textContent = totSched;
                document.getElementById('statAppuntamenti').textContent = 0;
                return;
            }}

            // Ricerca semplice (solo se ricerca avanzata non attiva)
            if (q && !advancedSearchMode) {{
                // Ricerca normale: cerca solo per zone e comuni
                zoneFiltrate = zoneFiltrate.filter(z =>
                    z.nome.toLowerCase().includes(q) ||
                    z.comuni.some(c => c.toLowerCase().includes(q))
                );
            }}

            zoneFiltrate.sort((a, b) => b.totale - a.totale);

            renderZoneSidebar(zoneFiltrate);
            renderZoneMappa(zoneFiltrate);

            document.getElementById('statZone').textContent = zoneFiltrate.length;
            document.getElementById('statContatori').textContent = zoneFiltrate.reduce((s, z) => s + z.totale, 0);
            document.getElementById('statAppuntamenti').textContent = zoneFiltrate.reduce((s, z) =>
                s + z.date.reduce((s2, d) => s2 + d.appuntamenti, 0), 0);
        }}
        
        function renderZoneSidebar(zoneFiltrate) {{
            const resultsDiv = document.getElementById('results');
            
            if (zoneFiltrate.length === 0) {{
                resultsDiv.innerHTML = `
                    <div class="no-results">
                        <div class="no-results-icon">🔍</div>
                        <div>${{filterSchedulabili ? 'Nessun contatore schedulabile trovato' : 'Nessuna zona trovata'}}</div>
                    </div>
                `;
                return;
            }}
            
            resultsDiv.innerHTML = zoneFiltrate.map(z => {{
                const lcls = getLCLsPerZona(z);
                const lclStr = lcls.length > 0 ? `🏷️ LCL: ${{lcls.join(', ')}}` : '🏷️ Nessun LCL';

                // Conta schedulabili per questa zona (mostrato sempre come info)
                const numSchedulabili = filterSchedulabili ? (z._schedulabili || 0) : countSchedulabiliPerZona(z);

                if (filterSchedulabili) {{
                    // Vista schedulabili: mostra lista contatori raggruppati per comune
                    const schedContatori = getSchedulabiliPerZona(z);
                    const perComune = {{}};
                    schedContatori.forEach(c => {{
                        const com = c.comune.toUpperCase();
                        if (!perComune[com]) perComune[com] = [];
                        perComune[com].push(c);
                    }});
                    
                    const comuniOrdinati = Object.keys(perComune).sort();
                    
                    return `
                    <div class="zone-card" data-zona="${{z.nome}}">
                        <div class="zone-header">
                            <div class="zone-color" style="background: ${{z.colore}}"></div>
                            <div class="zone-nome">${{z.nome}}</div>
                            <div class="zone-totale" style="background: var(--accent-secondary, #06b6d4); color: #fff;">${{numSchedulabili}}</div>
                        </div>
                        <div class="zone-comuni">${{z.comuni.join(', ')}}</div>
                        <div class="zone-stats">
                            <span>📍 ${{z.distanza}} km</span>
                            <span>🧭 ${{z.direzione}}</span>
                        </div>
                        <div class="date-list" id="dateList_${{z.nome.replace(/[^a-zA-Z0-9]/g, '_')}}">
                            ${{comuniOrdinati.map(com => {{
                                const lista = perComune[com];
                                return lista.map(c => `
                                    <div class="date-item schedulabile-item" onclick="event.stopPropagation(); mostraContatorePopup(contatori.find(cc => cc.id === '${{c.id}}'))">
                                        <div style="display: flex; flex-direction: column; flex: 1; gap: 2px;">
                                            <span style="font-weight: 600; font-size: 0.85rem;">📍 ${{com}}</span>
                                            <span style="font-size: 0.78rem; color: var(--text-muted);">${{c.via || 'N/A'}} ${{c.civico || ''}}</span>
                                            <span style="font-size: 0.78rem; color: var(--text-muted);">🔢 ${{c.eneltel}}${{c.tipo_ce ? ` · ⚡ ${{c.tipo_ce}}` : ''}}</span>
                                            ${{c.data ? `<span style="font-size: 0.75rem; color: var(--danger, #ef4444);">⏰ era: ${{formatDate(c.data)}}</span>` : `<span style="font-size: 0.75rem; color: var(--accent-secondary, #06b6d4);">📋 senza data</span>`}}
                                            ${{c.is_appuntamento ? `<span style="font-size: 0.75rem; color: #f59e0b;">📞 Appuntamento${{c.ora_appuntamento ? ' ' + c.ora_appuntamento : ''}}</span>` : ''}}
                                        </div>
                                        <span class="arrow">→</span>
                                    </div>
                                `).join('');
                            }}).join('')}}
                        </div>
                    </div>
                    `;
                }}

                const visibleDates = z.date.slice(0, 5);
                const hiddenDates = z.date.slice(5);
                const hasMore = hiddenDates.length > 0;
                
                // Badge schedulabili - cliccabile per aprire pianificazione pre-filtrata per zona
                const schedZoneId = z.nome.replace(/[^a-zA-Z0-9]/g, '_');
                const schedBadge = numSchedulabili > 0 ? `<div class="zone-schedulabili-badge" onclick="event.stopPropagation(); enterPlanningMode({{zona: '${{z.nome.replace(/'/g, "\\\\'")}}'}})" style="margin-top: 4px; font-size: 0.75rem; color: var(--accent-secondary, #06b6d4); cursor: pointer;" id="schedBadge_${{schedZoneId}}">📋 ${{numSchedulabili}} schedulabil${{numSchedulabili === 1 ? 'e' : 'i'}} →</div>` : '';
                
                return `
                <div class="zone-card" data-zona="${{z.nome}}" onclick="selezionaZona('${{z.nome.replace(/'/g, "\\\\'")}}')">
                    <div class="zone-header">
                        <div class="zone-color" style="background: ${{z.colore}}"></div>
                        <div class="zone-nome">${{z.nome}}</div>
                        <div class="zone-totale">${{z.totale}}</div>
                    </div>
                    <div class="zone-comuni">${{z.comuni.join(', ')}}</div>
                    ${{lclStr ? `<div class="zone-lcl">${{lclStr}}</div>` : ''}}
                    <div class="zone-stats">
                        <span>📍 ${{z.distanza}} km</span>
                        <span>🧭 ${{z.direzione}}</span>
                    </div>
                    ${{schedBadge}}
                    ${{z.date.length > 0 ? `
                        <div class="date-list" id="dateList_${{z.nome.replace(/[^a-zA-Z0-9]/g, '_')}}">
                            ${{visibleDates.map(d => `
                                <div class="date-item" onclick="event.stopPropagation(); apriDettaglio('${{z.nome.replace(/'/g, "\\\\'")}}', '${{d.data}}')">
                                    <span class="data">📅 ${{formatDate(d.data)}}</span>
                                    <span class="info">${{d.contatori}} cont.</span>
                                    <span class="app">${{d.appuntamenti > 0 ? `📞 ${{d.appuntamenti}}` : ''}}</span>
                                    <span class="arrow">→</span>
                                </div>
                            `).join('')}}
                            ${{hiddenDates.map(d => `
                                <div class="date-item date-hidden" onclick="event.stopPropagation(); apriDettaglio('${{z.nome.replace(/'/g, "\\\\'")}}', '${{d.data}}')">
                                    <span class="data">📅 ${{formatDate(d.data)}}</span>
                                    <span class="info">${{d.contatori}} cont.</span>
                                    <span class="app">${{d.appuntamenti > 0 ? `📞 ${{d.appuntamenti}}` : ''}}</span>
                                    <span class="arrow">→</span>
                                </div>
                            `).join('')}}
                            ${{hasMore ? `
                                <div class="date-expand" onclick="event.stopPropagation(); toggleDateList('${{z.nome.replace(/[^a-zA-Z0-9]/g, '_')}}', this)">
                                    📋 Mostra altre ${{hiddenDates.length}} date
                                </div>
                            ` : ''}}
                        </div>
                    ` : '<div class="date-list" style="padding: 12px; text-align: center; color: var(--text-muted);"><i>Nessuna data pianificata</i></div>'}}
                </div>
            `}}).join('');
        }}
        
        function toggleDateList(zoneId, btn) {{
            const list = document.getElementById('dateList_' + zoneId);
            list.classList.toggle('expanded');
            if (list.classList.contains('expanded')) {{
                btn.innerHTML = '📋 Riduci lista';
            }} else {{
                const hiddenCount = list.querySelectorAll('.date-hidden').length;
                btn.innerHTML = `📋 Mostra altre ${{hiddenCount}} date`;
            }}
        }}
        
        function renderZoneMappa(zoneFiltrate) {{
            // Salva vista se richiesto
            let savedCenter = null;
            let savedZoom = null;
            if (preserveMapView) {{
                savedCenter = map.getCenter();
                savedZoom = map.getZoom();
            }}

            clearMap();
            
            // Filtra zone con coordinate invalide
            const zoneValide = zoneFiltrate.filter(z => isValidCoordinate(z.lat, z.lon));
            if (zoneValide.length < zoneFiltrate.length) {{
                console.warn(`⚠️ Filtrate ${{zoneFiltrate.length - zoneValide.length}} zone con coordinate invalide`);
            }}

            zoneValide.forEach(z => {{
                // Usa raggio dinamico per ogni zona
                const raggio = z.raggio || 4000;

                const circle = L.circle([z.lat, z.lon], {{
                    color: z.colore,
                    fillColor: z.colore,
                    fillOpacity: 0.15,
                    radius: raggio,
                    weight: 2,
                    bubblingMouseEvents: false,
                    interactive: false
                }}).addTo(map);

                zoneCircles.push(circle);

                const marker = L.circleMarker([z.lat, z.lon], {{
                    radius: 8,
                    fillColor: z.colore,
                    color: 'white',
                    weight: 2,
                    fillOpacity: 1
                }}).addTo(map);

                marker.bindPopup(createZonaPopup(z));
                marker.on('click', (e) => {{
                    L.DomEvent.stopPropagation(e);
                    selezionaZona(z.nome);
                }});
                marker.bringToFront();
                zoneCircles.push(marker);
            }});

            // Aggiungi marker pinned se presente (persiste in tutte le viste)
            if (pinnedContatore) {{
                const pinIcon = L.divIcon({{
                    className: 'marker-pinned',
                    html: `<div style="background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); width: 32px; height: 32px; border-radius: 50%; display: flex; align-items: center; justify-content: center; border: 3px solid white; box-shadow: 0 4px 12px rgba(0,0,0,0.4); font-size: 16px;">📌</div>`,
                    iconSize: [32, 32],
                    iconAnchor: [16, 16]
                }});
                if (pinnedMarker) map.removeLayer(pinnedMarker);
                pinnedMarker = L.marker([pinnedContatore.lat, pinnedContatore.lon], {{ icon: pinIcon, zIndexOffset: 10000 }})
                    .bindPopup(() => createContatorePopup(pinnedContatore, '📌', '#f5576c'))
                    .addTo(map);
            }}

            // Ripristina vista salvata o centra sui punti
            if (savedCenter && savedZoom) {{
                map.setView(savedCenter, savedZoom);
                preserveMapView = false;
            }} else if (zoneValide.length > 0) {{
                const bounds = L.latLngBounds(zoneValide.map(z => [z.lat, z.lon]));
                if (isValidCoordinate(deposito.lat, deposito.lon)) {{
                    bounds.extend([deposito.lat, deposito.lon]);
                }}
                map.fitBounds(bounds, {{ padding: [50, 50] }});
            }}
        }}
        
        function createZonaPopup(z) {{
            const lcls = getLCLsPerZona(z);
            let html = `<div class="popup-content">`;
            html += `<div class="popup-header">`;
            html += `<div class="popup-title" style="color: ${{z.colore}}">${{z.nome}}</div>`;
            html += `<div class="popup-subtitle">${{z.comuni.join(', ')}}</div>`;
            html += `</div>`;
            html += `<div class="popup-row"><span class="popup-label">Distanza</span><span class="popup-value">${{z.distanza}} km (${{z.direzione}})</span></div>`;
            html += `<div class="popup-row"><span class="popup-label">Contatori</span><span class="popup-value">${{z.totale}}</span></div>`;
            if (lcls.length > 0) {{
                html += `<div class="popup-row"><span class="popup-label">LCL</span><span class="popup-value">${{lcls.join(', ')}}</span></div>`;
            }}
            if (z.date.length > 0) {{
                html += `<div style="margin-top: 10px; padding-top: 10px;">`;
                html += `<div style="color: var(--text-secondary); margin-bottom: 8px; font-size: 0.85em;">📅 Date pianificate:</div>`;
                z.date.slice(0, 3).forEach(d => {{
                    html += `<div style="padding: 4px 0; font-size: 0.85em;"><a href="#" onclick="event.preventDefault(); map.closePopup(); apriDettaglio('${{z.nome.replace(/'/g, "\\'")}}', '${{d.data}}');" style="color: var(--text-primary); text-decoration: underline; text-decoration-style: dotted; cursor: pointer;">${{formatDate(d.data)}}: ${{d.contatori}} cont.${{d.appuntamenti > 0 ? ' (📞' + d.appuntamenti + ')' : ''}}</a></div>`;
                }});
                if (z.date.length > 3) html += `<div style="color: var(--text-muted); font-size: 0.8em;">...e altre ${{z.date.length - 3}} date</div>`;
                html += `</div>`;
            }}
            html += `</div>`;
            return html;
        }}
        
        function selezionaZona(zonaNome) {{
            const z = zone.find(x => x.nome === zonaNome);
            if (z) {{
                map.flyTo([z.lat, z.lon], 12);
                document.querySelectorAll('.zone-card').forEach(c => {{
                    c.classList.toggle('selected', c.dataset.zona === zonaNome);
                }});
            }}
        }}

        function mostraRisultatiRicercaAvanzata(contatoriTrovati, query) {{
            currentView = 'search-results';

            document.getElementById('breadcrumb').classList.add('visible');
            document.getElementById('breadcrumbText').innerHTML = `<strong>Ricerca:</strong> "${{query}}" → ${{contatoriTrovati.length}} risultat${{contatoriTrovati.length === 1 ? 'o' : 'i'}}`;

            // Raggruppa per tecnico
            const perTecnico = {{}};
            contatoriTrovati.forEach(c => {{
                const tec = c.tecnico || 'RISULTATI';
                if (!perTecnico[tec]) perTecnico[tec] = [];
                perTecnico[tec].push(c);
            }});

            // Ordina ogni gruppo per comune e poi per via
            Object.keys(perTecnico).forEach(tec => {{
                perTecnico[tec].sort((a, b) => {{
                    if (a.comune !== b.comune) return a.comune.localeCompare(b.comune);
                    return (a.via || '').localeCompare(b.via || '');
                }});
            }});

            renderDettaglioSidebar(perTecnico, null, true);
            renderRisultatiRicercaMappa(contatoriTrovati);

            const totContatori = contatoriTrovati.length;
            const totApp = contatoriTrovati.filter(c => c.is_appuntamento).length;
            document.getElementById('statZone').textContent = Object.keys(perTecnico).length;
            document.getElementById('statContatori').textContent = totContatori;
            document.getElementById('statAppuntamenti').textContent = totApp;
        }}

        function renderRisultatiRicercaMappa(contatoriTrovati) {{
            clearMap();

            // Aggiungi deposito
            L.marker([deposito.lat, deposito.lon], {{
                icon: L.divIcon({{
                    className: 'deposito-marker',
                    html: '<div style="background: #2ecc71; color: white; padding: 8px 12px; border-radius: 20px; font-weight: 600; box-shadow: 0 2px 8px rgba(0,0,0,0.3); white-space: nowrap;">📦 Deposito</div>',
                    iconSize: [80, 40],
                    iconAnchor: [40, 40]
                }})
            }}).addTo(map).bindPopup('<b>📦 Deposito</b>');

            // Aggiungi marker per ogni contatore trovato
            contatoriTrovati.forEach((c, idx) => {{
                const colore = coloriTecnici[c.tecnico] || '#666';

                const marker = L.circleMarker([c.lat, c.lon], {{
                    radius: 8,
                    fillColor: colore,
                    color: 'white',
                    weight: 2,
                    fillOpacity: 0.9
                }}).addTo(map);

                const popupContent = `
                    <div class="popup-content">
                        <div class="popup-header">
                            <div class="popup-title">${{c.nominativo}}</div>
                        </div>
                        <div class="popup-row"><span class="popup-label">Comune</span><span class="popup-value">${{c.comune}}</span></div>
                        <div class="popup-row"><span class="popup-label">Via</span><span class="popup-value">${{c.via || 'N/A'}}</span></div>
                        <div class="popup-row"><span class="popup-label">POD</span><span class="popup-value">${{c.pod || 'N/A'}}</span></div>
                        <div class="popup-row"><span class="popup-label">Eneltel</span><span class="popup-value">${{c.eneltel || 'N/A'}}</span></div>
                        ${{c.lcl ? `<div class="popup-row"><span class="popup-label">LCL</span><span class="popup-value">${{c.lcl}}</span></div>` : ''}}
                        <div class="popup-row"><span class="popup-label">Data</span><span class="popup-value">${{formatDate(c.data)}}</span></div>
                        ${{c.is_appuntamento ? `<div class="popup-row"><span class="popup-label">Appuntamento</span><span class="popup-value">📞 ${{c.orario_appuntamento}}</span></div>` : ''}}
                    </div>
                `;

                marker.bindPopup(popupContent);
                marker.on('click', () => evidenziaContatore(c.eneltel));
                markers.push(marker);
            }});

            // Centra mappa sui risultati
            if (contatoriTrovati.length > 0) {{
                const bounds = L.latLngBounds(contatoriTrovati.map(c => [c.lat, c.lon]));
                bounds.extend([deposito.lat, deposito.lon]);
                map.fitBounds(bounds, {{ padding: [50, 50] }});
            }}

            // Aggiungi marker pinned se presente
            if (pinnedContatore) {{
                const pinIcon = L.divIcon({{
                    className: 'marker-pinned',
                    html: `<div style="background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); width: 32px; height: 32px; border-radius: 50%; display: flex; align-items: center; justify-content: center; border: 3px solid white; box-shadow: 0 4px 12px rgba(0,0,0,0.4); font-size: 16px;">📌</div>`,
                    iconSize: [32, 32],
                    iconAnchor: [16, 16]
                }});
                if (pinnedMarker) map.removeLayer(pinnedMarker);
                pinnedMarker = L.marker([pinnedContatore.lat, pinnedContatore.lon], {{ icon: pinIcon, zIndexOffset: 10000 }})
                    .bindPopup(() => createContatorePopup(pinnedContatore, '📌', '#f5576c'))
                    .addTo(map);
            }}
        }}

        // ============================================================
        // VISTA COMUNI (senza clustering)
        // ============================================================
        function mostraVistaComuni(searchQuery = '') {{
            currentView = 'towns';
            currentZona = null;
            currentData = null;
            detailSelectedOperatori.clear();

            // Ripristina select filtro date (ma non durante auto-refresh)
            if (!isAutoRefresh) {{
                document.getElementById('dataFilterRow').style.display = 'flex';
                document.getElementById('detailFilters').style.display = 'none';
                filterAppuntamentiOnly = false;
                mostraRotteAttivo = false;
                
                // Gestisci dropdown operatore in base alla data selezionata
                const dataSelezionata = document.getElementById('dataFilter').value;
                if (!dataSelezionata) {{
                    document.getElementById('operatoreFilterContainer').classList.remove('visible');
                    resetOperatoreFilter();
                }}

                document.getElementById('breadcrumb').classList.remove('visible');
            }}

            // Filtra contatori per data se necessario (usa contatoriVisibili per vista normale)
            let contatoriDaUsare = contatoriVisibili;
            if (currentDataFiltro) {{
                contatoriDaUsare = contatoriVisibili.filter(c => c.data === currentDataFiltro);
            }}

            // Raggruppa contatori per comune
            const comuniMap = new Map();
            contatoriDaUsare.forEach(c => {{
                const comune = c.comune.toUpperCase();
                if (!comuniMap.has(comune)) {{
                    comuniMap.set(comune, {{
                        nome: comune,
                        contatori: [],
                        lat: c.lat,
                        lon: c.lon,
                        lcls: new Set()
                    }});
                }}
                const comuneData = comuniMap.get(comune);
                comuneData.contatori.push(c);
                if (c.lcl && c.lcl.trim() !== '') comuneData.lcls.add(c.lcl);
            }});

            // Converti in array e calcola centroidi pesati (usa solo coordinate valide)
            let comuniList = Array.from(comuniMap.values()).map(c => {{
                // Filtra solo contatori con coordinate valide per il calcolo del centroide
                const contatoriValidi = c.contatori.filter(cont => isValidCoordinate(cont.lat, cont.lon));
                
                if (contatoriValidi.length > 0) {{
                    let latSum = 0, lonSum = 0;
                    contatoriValidi.forEach(cont => {{
                        latSum += cont.lat;
                        lonSum += cont.lon;
                    }});
                    c.lat = latSum / contatoriValidi.length;
                    c.lon = lonSum / contatoriValidi.length;
                }} else {{
                    // Se nessun contatore ha coordinate valide, usa coordinate di fallback (centro Italia)
                    c.lat = 45.0;
                    c.lon = 7.5;
                }}

                // Raggruppa per data
                const dateMap = new Map();
                c.contatori.forEach(cont => {{
                    if (!dateMap.has(cont.data)) {{
                        dateMap.set(cont.data, {{ contatori: 0, appuntamenti: 0 }});
                    }}
                    const dInfo = dateMap.get(cont.data);
                    dInfo.contatori++;
                    if (cont.is_appuntamento) dInfo.appuntamenti++;
                }});

                c.date = Array.from(dateMap.entries()).map(([data, info]) => ({{
                    data,
                    contatori: info.contatori,
                    appuntamenti: info.appuntamenti
                }})).sort((a, b) => a.data.localeCompare(b.data));

                c.totale = c.contatori.length;
                c.lcls = Array.from(c.lcls);
                return c;
            }});

            // Filtra per LCL
            if (currentLCL) {{
                comuniList = comuniList.filter(c => c.lcls.includes(currentLCL));
            }}

            // Filtra per solo appuntamenti
            if (filterAppuntamentiOnly) {{
                const comuniConApp = new Set(
                    contatoriVisibili.filter(c => c.is_appuntamento).map(c => c.comune.toUpperCase())
                );
                comuniList = comuniList.filter(c => comuniConApp.has(c.nome.toUpperCase())).map(c => {{
                    // Ricalcola mostrando solo appuntamenti
                    const appPerData = {{}};
                    contatoriVisibili.filter(cont =>
                        cont.is_appuntamento && cont.comune.toUpperCase() === c.nome.toUpperCase()
                    ).forEach(cont => {{
                        if (!appPerData[cont.data]) appPerData[cont.data] = 0;
                        appPerData[cont.data]++;
                    }});
                    return {{
                        ...c,
                        date: c.date.map(d => ({{
                            ...d,
                            contatori: appPerData[d.data] || 0,
                            appuntamenti: appPerData[d.data] || 0
                        }})).filter(d => d.contatori > 0),
                        totale: Object.values(appPerData).reduce((a, b) => a + b, 0)
                    }};
                }}).filter(c => c.totale > 0);
            }}

            // Filtra per schedulabili
            if (filterSchedulabili) {{
                const schedulabili = getContatoriSchedulabili();
                // Ricostruisci comuniList basandola sui contatori schedulabili
                const schedComuniMap = new Map();
                schedulabili.forEach(c => {{
                    const comune = c.comune.toUpperCase();
                    if (!schedComuniMap.has(comune)) {{
                        schedComuniMap.set(comune, {{
                            nome: comune,
                            contatori: [],
                            lat: c.lat,
                            lon: c.lon,
                            lcls: new Set()
                        }});
                    }}
                    const comuneData = schedComuniMap.get(comune);
                    comuneData.contatori.push(c);
                    if (c.lcl && c.lcl.trim() !== '') comuneData.lcls.add(c.lcl);
                }});
                
                comuniList = Array.from(schedComuniMap.values()).map(c => {{
                    const contatoriValidi = c.contatori.filter(cont => isValidCoordinate(cont.lat, cont.lon));
                    if (contatoriValidi.length > 0) {{
                        let latSum = 0, lonSum = 0;
                        contatoriValidi.forEach(cont => {{ latSum += cont.lat; lonSum += cont.lon; }});
                        c.lat = latSum / contatoriValidi.length;
                        c.lon = lonSum / contatoriValidi.length;
                    }}
                    c.totale = c.contatori.length;
                    c.date = [];
                    c.lcls = Array.from(c.lcls);
                    c._schedulabili = c.totale;
                    return c;
                }});
                
                // Applica filtro LCL
                if (currentLCL) {{
                    comuniList = comuniList.filter(c => c.lcls.includes(currentLCL));
                }}
            }}

            // Filtra per ricerca semplice (solo se ricerca avanzata non attiva)
            const q = searchQuery.toLowerCase();
            if (q && !advancedSearchMode) {{
                comuniList = comuniList.filter(c => c.nome.toLowerCase().includes(q));
            }}

            // Ordina per numero contatori
            comuniList.sort((a, b) => b.totale - a.totale);

            renderComuniSidebar(comuniList);
            renderComuniMappa(comuniList);

            document.getElementById('statZone').textContent = comuniList.length;
            document.getElementById('statContatori').textContent = comuniList.reduce((s, c) => s + c.totale, 0);
            document.getElementById('statAppuntamenti').textContent = comuniList.reduce((s, c) =>
                s + c.date.reduce((s2, d) => s2 + d.appuntamenti, 0), 0);
        }}

        function renderComuniSidebar(comuniList) {{
            const resultsDiv = document.getElementById('results');

            if (comuniList.length === 0) {{
                resultsDiv.innerHTML = `
                    <div class="no-results">
                        <div class="no-results-icon">🔍</div>
                        <div>${{filterSchedulabili ? 'Nessun contatore schedulabile trovato' : 'Nessun comune trovato'}}</div>
                    </div>
                `;
                return;
            }}

            const colori = [
                '#e6194b', '#3cb44b', '#ffe119', '#4363d8', '#f58231',
                '#911eb4', '#46f0f0', '#f032e6', '#bcf60c', '#fabebe',
                '#008080', '#e6beff', '#9a6324', '#fffac8', '#800000',
                '#aaffc3', '#808000', '#ffd8b1', '#000075', '#808080'
            ];

            resultsDiv.innerHTML = comuniList.map((c, idx) => {{
                const colore = colori[idx % colori.length];
                const lclStr = c.lcls.length > 0 ? `🏷️ LCL: ${{c.lcls.join(', ')}}` : '🏷️ Nessun LCL';

                // Conta schedulabili per questo comune
                const numSchedulabili = filterSchedulabili ? (c._schedulabili || 0) : countSchedulabiliPerComune(c.nome);

                if (filterSchedulabili) {{
                    // Vista schedulabili: mostra lista contatori schedulabili
                    const schedContatori = getSchedulabiliPerComune(c.nome);
                    
                    return `
                    <div class="zone-card" data-comune="${{c.nome}}">
                        <div class="zone-header">
                            <div class="zone-color" style="background: ${{colore}}"></div>
                            <div class="zone-nome">${{c.nome}}</div>
                            <div class="zone-totale" style="background: var(--accent-secondary, #06b6d4); color: #fff;">${{numSchedulabili}}</div>
                        </div>
                        ${{lclStr ? `<div class="zone-lcl">${{lclStr}}</div>` : ''}}
                        <div class="date-list">
                            ${{schedContatori.map(sc => `
                                <div class="date-item schedulabile-item" onclick="event.stopPropagation(); mostraContatorePopup(contatori.find(cc => cc.id === '${{sc.id}}'))">
                                    <div style="display: flex; flex-direction: column; flex: 1; gap: 2px;">
                                        <span style="font-size: 0.78rem; color: var(--text-muted);">${{sc.via || 'N/A'}} ${{sc.civico || ''}}</span>
                                        <span style="font-size: 0.78rem; color: var(--text-muted);">🔢 ${{sc.eneltel}}${{sc.tipo_ce ? ` · ⚡ ${{sc.tipo_ce}}` : ''}}</span>
                                        ${{sc.data ? `<span style="font-size: 0.75rem; color: var(--danger, #ef4444);">⏰ era: ${{formatDate(sc.data)}}</span>` : `<span style="font-size: 0.75rem; color: var(--accent-secondary, #06b6d4);">📋 senza data</span>`}}
                                        ${{sc.is_appuntamento ? `<span style="font-size: 0.75rem; color: #f59e0b;">📞 Appuntamento${{sc.ora_appuntamento ? ' ' + sc.ora_appuntamento : ''}}</span>` : ''}}
                                    </div>
                                    <span class="arrow">→</span>
                                </div>
                            `).join('')}}
                        </div>
                    </div>
                    `;
                }}

                const visibleDates = c.date.slice(0, 5);
                const hiddenDates = c.date.slice(5);
                const hasMore = hiddenDates.length > 0;

                // Badge schedulabili - cliccabile per aprire pianificazione pre-filtrata per comune
                const schedComuneId = c.nome.replace(/[^a-zA-Z0-9]/g, '_');
                const schedBadge = numSchedulabili > 0 ? `<div class="zone-schedulabili-badge" onclick="event.stopPropagation(); enterPlanningMode({{comune: '${{c.nome.replace(/'/g, "\\\\'")}}'}})" style="margin-top: 4px; font-size: 0.75rem; color: var(--accent-secondary, #06b6d4); cursor: pointer;" id="schedBadge_${{schedComuneId}}">📋 ${{numSchedulabili}} schedulabil${{numSchedulabili === 1 ? 'e' : 'i'}} →</div>` : '';

                return `
                <div class="zone-card" data-comune="${{c.nome}}" onclick="selezionaComune('${{c.nome.replace(/'/g, "\\\\'")}}')">
                    <div class="zone-header">
                        <div class="zone-color" style="background: ${{colore}}"></div>
                        <div class="zone-nome">${{c.nome}}</div>
                        <div class="zone-totale">${{c.totale}}</div>
                    </div>
                    ${{lclStr ? `<div class="zone-lcl">${{lclStr}}</div>` : ''}}
                    ${{schedBadge}}
                    ${{c.date.length > 0 ? `
                        <div class="date-list" id="dateList_${{c.nome.replace(/[^a-zA-Z0-9]/g, '_')}}">
                            ${{visibleDates.map(d => `
                                <div class="date-item" onclick="event.stopPropagation(); apriDettaglioComune('${{c.nome.replace(/'/g, "\\\\'")}}', '${{d.data}}')">
                                    <span class="data">📅 ${{formatDate(d.data)}}</span>
                                    <span class="info">${{d.contatori}} cont.</span>
                                    <span class="app">${{d.appuntamenti > 0 ? `📞 ${{d.appuntamenti}}` : ''}}</span>
                                    <span class="arrow">→</span>
                                </div>
                            `).join('')}}
                            ${{hiddenDates.map(d => `
                                <div class="date-item date-hidden" onclick="event.stopPropagation(); apriDettaglioComune('${{c.nome.replace(/'/g, "\\\\'")}}', '${{d.data}}')">
                                    <span class="data">📅 ${{formatDate(d.data)}}</span>
                                    <span class="info">${{d.contatori}} cont.</span>
                                    <span class="app">${{d.appuntamenti > 0 ? `📞 ${{d.appuntamenti}}` : ''}}</span>
                                    <span class="arrow">→</span>
                                </div>
                            `).join('')}}
                            ${{hasMore ? `
                                <div class="date-expand" onclick="event.stopPropagation(); toggleDateList('${{c.nome.replace(/[^a-zA-Z0-9]/g, '_')}}', this)">
                                    📋 Mostra altre ${{hiddenDates.length}} date
                                </div>
                            ` : ''}}
                        </div>
                    ` : '<div class="date-list" style="padding: 12px; text-align: center; color: var(--text-muted);"><i>Nessuna data pianificata</i></div>'}}
                </div>
            `}}).join('');
        }}

        function renderComuniMappa(comuniList) {{
            // Salva vista se richiesto
            let savedCenter = null;
            let savedZoom = null;
            if (preserveMapView) {{
                savedCenter = map.getCenter();
                savedZoom = map.getZoom();
            }}

            clearMap();

            const colori = [
                '#e6194b', '#3cb44b', '#ffe119', '#4363d8', '#f58231',
                '#911eb4', '#46f0f0', '#f032e6', '#bcf60c', '#fabebe',
                '#008080', '#e6beff', '#9a6324', '#fffac8', '#800000',
                '#aaffc3', '#808000', '#ffd8b1', '#000075', '#808080'
            ];

            // Filtra comuni con coordinate valide per evitare problemi di zoom
            const comuniValidi = comuniList.filter(c => isValidCoordinate(c.lat, c.lon));
            
            comuniValidi.forEach((c, idx) => {{
                const colore = colori[idx % colori.length];

                const marker = L.circleMarker([c.lat, c.lon], {{
                    radius: 10,
                    fillColor: colore,
                    color: 'white',
                    weight: 2,
                    fillOpacity: 0.9
                }}).addTo(map);

                marker.bindPopup(createComunePopup(c, colore));
                marker.on('click', () => selezionaComune(c.nome));
                zoneCircles.push(marker);
            }});

            // Aggiungi marker pinned se presente (persiste in tutte le viste)
            if (pinnedContatore) {{
                const pinIcon = L.divIcon({{
                    className: 'marker-pinned',
                    html: `<div style="background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); width: 32px; height: 32px; border-radius: 50%; display: flex; align-items: center; justify-content: center; border: 3px solid white; box-shadow: 0 4px 12px rgba(0,0,0,0.4); font-size: 16px;">📌</div>`,
                    iconSize: [32, 32],
                    iconAnchor: [16, 16]
                }});
                if (pinnedMarker) map.removeLayer(pinnedMarker);
                pinnedMarker = L.marker([pinnedContatore.lat, pinnedContatore.lon], {{ icon: pinIcon, zIndexOffset: 10000 }})
                    .bindPopup(() => createContatorePopup(pinnedContatore, '📌', '#f5576c'))
                    .addTo(map);
            }}

            // Ripristina vista salvata o centra sui punti (usa solo coordinate valide)
            if (savedCenter && savedZoom) {{
                map.setView(savedCenter, savedZoom);
                preserveMapView = false;
            }} else if (comuniValidi.length > 0) {{
                const bounds = L.latLngBounds(comuniValidi.map(c => [c.lat, c.lon]));
                bounds.extend([deposito.lat, deposito.lon]);
                map.fitBounds(bounds, {{ padding: [50, 50] }});
            }}
        }}

        function createComunePopup(c, colore) {{
            let html = `<div class="popup-content">`;
            html += `<div class="popup-header">`;
            html += `<div class="popup-title" style="color: ${{colore}}">${{c.nome}}</div>`;
            html += `</div>`;
            html += `<div class="popup-row"><span class="popup-label">Contatori</span><span class="popup-value">${{c.totale}}</span></div>`;
            if (c.lcls.length > 0) {{
                html += `<div class="popup-row"><span class="popup-label">LCL</span><span class="popup-value">${{c.lcls.join(', ')}}</span></div>`;
            }}
            if (c.date.length > 0) {{
                html += `<div style="margin-top: 10px; padding-top: 10px;">`;
                html += `<div style="color: var(--text-secondary); margin-bottom: 8px; font-size: 0.85em;">📅 Date pianificate:</div>`;
                c.date.slice(0, 3).forEach(d => {{
                    html += `<div style="padding: 4px 0; font-size: 0.85em;"><a href="#" onclick="event.preventDefault(); map.closePopup(); apriDettaglioComune('${{c.nome.replace(/'/g, "\\'")}}', '${{d.data}}');" style="color: var(--text-primary); text-decoration: underline; text-decoration-style: dotted; cursor: pointer;">${{formatDate(d.data)}}: ${{d.contatori}} cont.${{d.appuntamenti > 0 ? ' (📞' + d.appuntamenti + ')' : ''}}</a></div>`;
                }});
                if (c.date.length > 3) html += `<div style="color: var(--text-muted); font-size: 0.8em;">...e altre ${{c.date.length - 3}} date</div>`;
                html += `</div>`;
            }}
            html += `</div>`;
            return html;
        }}

        function selezionaComune(comuneNome) {{
            const c = contatori.find(x => x.comune.toUpperCase() === comuneNome.toUpperCase());
            if (c) {{
                map.flyTo([c.lat, c.lon], 13);
                document.querySelectorAll('.zone-card').forEach(card => {{
                    card.classList.toggle('selected', card.dataset.comune === comuneNome);
                }});
            }}
        }}

        function apriDettaglioComune(comuneNome, data) {{
            currentView = 'detail';
            currentZona = comuneNome;
            currentData = data;

            // Durante auto-refresh, non resettare lo stato UI
            if (!isAutoRefresh) {{
                // Pulisci la ricerca
                document.getElementById('searchInput').value = '';
                updateSearchClearButton();
            }}

            // Mostra toggle al posto del select in vista dettaglio
            document.getElementById('dataFilterRow').style.display = 'none';
            document.getElementById('detailFilters').style.display = 'flex';

            document.getElementById('breadcrumb').classList.add('visible');
            document.getElementById('breadcrumbText').innerHTML = `<strong>${{comuneNome}}</strong> → ${{data}}`;

            let contatoriData = contatori.filter(c =>
                c.data === data &&
                c.comune.toUpperCase() === comuneNome.toUpperCase()
            );

            // Aggiungi schedulabili se filtro attivo
            if (filterSchedulabili) {{
                const schedComune = getContatoriSchedulabili().filter(c =>
                    c.comune.toUpperCase() === comuneNome.toUpperCase() &&
                    c.data !== data  // evita duplicati
                );
                contatoriData = contatoriData.concat(schedComune);
            }}

            // Filtra per LCL se attivo
            if (currentLCL) {{
                contatoriData = contatoriData.filter(c => c.lcl === currentLCL);
            }}

            // Filtra per operatori selezionati
            if (detailSelectedOperatori.size > 0) {{
                contatoriData = contatoriData.filter(c => detailSelectedOperatori.has(c.tecnico || 'TUTTI'));
            }}

            // Raggruppa per tecnico
            const perTecnico = {{}};
            contatoriData.forEach(c => {{
                const tec = c.tecnico || 'TUTTI';
                if (!perTecnico[tec]) perTecnico[tec] = [];
                perTecnico[tec].push(c);
            }});

            // Ordina ogni gruppo
            Object.keys(perTecnico).forEach(tec => {{
                perTecnico[tec] = ordinaPercorsoJS(perTecnico[tec]);
            }});

            renderDettaglioSidebar(perTecnico, data);
            renderDettaglioMappa(perTecnico, data);

            // Popola i chip operatori
            populateDetailOperatoreChips();

            const totContatori = Object.values(perTecnico).flat().length;
            const totApp = Object.values(perTecnico).flat().filter(c => c.is_appuntamento).length;
            document.getElementById('statZone').textContent = Object.keys(perTecnico).length;
            document.getElementById('statContatori').textContent = totContatori;
            document.getElementById('statAppuntamenti').textContent = totApp;

            // Abilita navigazione date
            updateDateNavigation();
        }}

        // ============================================================
        // VISTA DETTAGLIO
        // ============================================================
        function apriDettaglio(zonaNome, data) {{
            currentView = 'detail';
            currentZona = zonaNome;
            currentData = data;

            // Durante auto-refresh, non resettare lo stato UI
            if (!isAutoRefresh) {{
                // Pulisci la ricerca
                document.getElementById('searchInput').value = '';
                updateSearchClearButton();
            }}

            // Mostra toggle al posto del select in vista dettaglio
            document.getElementById('dataFilterRow').style.display = 'none';
            document.getElementById('detailFilters').style.display = 'flex';

            document.getElementById('breadcrumb').classList.add('visible');
            document.getElementById('breadcrumbText').innerHTML = `<strong>${{zonaNome}}</strong> → ${{data}}`;
            
            const zonaInfo = zone.find(z => z.nome === zonaNome);
            const comuniZona = zonaInfo ? zonaInfo.comuni.map(c => c.toLowerCase()) : [];
            
            let contatoriData = contatori.filter(c =>
                c.data === data &&
                comuniZona.includes(c.comune.toLowerCase())
            );

            // Aggiungi schedulabili se filtro attivo
            if (filterSchedulabili) {{
                const schedZona = getContatoriSchedulabili().filter(c =>
                    comuniZona.includes(c.comune.toLowerCase()) &&
                    c.data !== data  // evita duplicati
                );
                contatoriData = contatoriData.concat(schedZona);
            }}

            // Filtra per LCL se attivo
            if (currentLCL) {{
                contatoriData = contatoriData.filter(c => c.lcl === currentLCL);
            }}

            // Filtra per operatori selezionati
            if (detailSelectedOperatori.size > 0) {{
                contatoriData = contatoriData.filter(c => detailSelectedOperatori.has(c.tecnico || 'TUTTI'));
            }}

            // Raggruppa per tecnico
            const perTecnico = {{}};
            contatoriData.forEach(c => {{
                const tec = c.tecnico || 'TUTTI';
                if (!perTecnico[tec]) perTecnico[tec] = [];
                perTecnico[tec].push(c);
            }});
            
            // Ordina ogni gruppo
            Object.keys(perTecnico).forEach(tec => {{
                perTecnico[tec] = ordinaPercorsoJS(perTecnico[tec]);
            }});
            
            renderDettaglioSidebar(perTecnico, data);
            renderDettaglioMappa(perTecnico, data);

            // Popola i chip operatori
            populateDetailOperatoreChips();

            const totContatori = Object.values(perTecnico).flat().length;
            const totApp = Object.values(perTecnico).flat().filter(c => c.is_appuntamento).length;
            document.getElementById('statZone').textContent = Object.keys(perTecnico).length;
            document.getElementById('statContatori').textContent = totContatori;
            document.getElementById('statAppuntamenti').textContent = totApp;

            // Abilita navigazione date
            updateDateNavigation();
        }}

        function apriDettaglioConRicerca(zonaNome, data, searchQuery) {{
            currentView = 'detail';
            currentZona = zonaNome;
            currentData = data;

            document.getElementById('breadcrumb').classList.add('visible');
            document.getElementById('breadcrumbText').innerHTML = `<strong>${{zonaNome}}</strong> → ${{data}}`;

            const zonaInfo = zone.find(z => z.nome === zonaNome);
            const comuniZona = zonaInfo ? zonaInfo.comuni.map(c => c.toLowerCase()) : [];

            let contatoriData = contatori.filter(c =>
                c.data === data &&
                comuniZona.includes(c.comune.toLowerCase())
            );

            // Filtra per LCL se attivo
            if (currentLCL) {{
                contatoriData = contatoriData.filter(c => c.lcl === currentLCL);
            }}

            // Filtra per operatori selezionati
            if (detailSelectedOperatori.size > 0) {{
                contatoriData = contatoriData.filter(c => detailSelectedOperatori.has(c.tecnico || 'TUTTI'));
            }}

            // Filtra per ricerca se presente
            if (searchQuery) {{
                const q = searchQuery.toLowerCase();
                contatoriData = contatoriData.filter(c =>
                    c.comune.toLowerCase().includes(q) ||
                    c.cliente.toLowerCase().includes(q) ||
                    c.pod.toLowerCase().includes(q) ||
                    c.eneltel.toLowerCase().includes(q) ||
                    c.via.toLowerCase().includes(q) ||
                    c.tecnico?.toLowerCase().includes(q) ||
                    getDisplayName(c.tecnico).toLowerCase().includes(q)
                );
            }}

            // Raggruppa per tecnico
            const perTecnico = {{}};
            contatoriData.forEach(c => {{
                const tec = c.tecnico || 'TUTTI';
                if (!perTecnico[tec]) perTecnico[tec] = [];
                perTecnico[tec].push(c);
            }});

            // Ordina ogni gruppo
            Object.keys(perTecnico).forEach(tec => {{
                perTecnico[tec] = ordinaPercorsoJS(perTecnico[tec]);
            }});

            renderDettaglioSidebar(perTecnico, data);
            renderDettaglioMappa(perTecnico, data);

            const totContatori = Object.values(perTecnico).flat().length;
            const totApp = Object.values(perTecnico).flat().filter(c => c.is_appuntamento).length;
            document.getElementById('statZone').textContent = Object.keys(perTecnico).length;
            document.getElementById('statContatori').textContent = totContatori;
            document.getElementById('statAppuntamenti').textContent = totApp;
        }}

        // ================================================================
        // FUNZIONE PRINCIPALE: ordina usando l'ordine PRE-CALCOLATO da Python/OSRM
        // L'ordine è già stato calcolato con distanze stradali reali
        // NOTA: OSRM è obbligatorio - nessun fallback JavaScript
        // ================================================================
        function ordinaPercorsoJS(lista) {{
            if (lista.length <= 1) return lista;

            // Controlla se abbiamo ordini pre-calcolati
            const conOrdine = lista.filter(c => c.ordine_tappa && c.ordine_tappa < 999).length;

            // Se ALMENO UN contatore ha ordine valido, usa l'ordine Python
            // I contatori senza ordine (999) finiranno alla fine, poi ottimizziamo quelli
            if (conOrdine > 0) {{
                // Separa contatori con ordine da quelli senza
                const conOrdineList = lista.filter(c => c.ordine_tappa && c.ordine_tappa < 999);
                const senzaOrdineList = lista.filter(c => !c.ordine_tappa || c.ordine_tappa >= 999);

                // Ordina quelli con ordine pre-calcolato
                conOrdineList.sort((a, b) => a.ordine_tappa - b.ordine_tappa);

                // Se ci sono contatori senza ordine, ottimizzali con nearest neighbor
                // partendo dall'ultimo contatore ordinato
                if (senzaOrdineList.length > 0 && conOrdineList.length > 0) {{
                    const ultimoOrdinato = conOrdineList[conOrdineList.length - 1];
                    let pos = {{ lat: ultimoOrdinato.lat, lon: ultimoOrdinato.lon }};
                    const rimanenti = [...senzaOrdineList];
                    const ordinati = [];

                    while (rimanenti.length > 0) {{
                        let minDist = Infinity;
                        let minIdx = 0;
                        rimanenti.forEach((c, i) => {{
                            const dist = Math.sqrt(Math.pow(c.lat - pos.lat, 2) + Math.pow(c.lon - pos.lon, 2));
                            if (dist < minDist) {{ minDist = dist; minIdx = i; }}
                        }});
                        const next = rimanenti.splice(minIdx, 1)[0];
                        ordinati.push(next);
                        pos = {{ lat: next.lat, lon: next.lon }};
                    }}
                    return [...conOrdineList, ...ordinati];
                }}

                return conOrdineList;
            }} else {{
                // NESSUN ordine pre-calcolato - OSRM probabilmente non era attivo
                // Mostra avviso solo se ci sono contatori nel range date visibile (oggi -> DATA_FINE)
                // Non mostrare per date passate o future lontane
                const oggi = new Date();
                oggi.setHours(0, 0, 0, 0);
                
                // Controlla se almeno un contatore è nel range che avrebbe dovuto essere calcolato
                const contatoriNelRange = lista.filter(c => {{
                    if (!c.data) return false;
                    try {{
                        const dataContatore = new Date(c.data);
                        dataContatore.setHours(0, 0, 0, 0);
                        return dataContatore >= oggi;
                    }} catch(e) {{ return false; }}
                }});
                
                console.warn('⚠️ Nessun ordine pre-calcolato trovato per questa selezione');
                console.warn('   Contatori nel range futuro:', contatoriNelRange.length);
                
                // Mostra alert solo se ci sono contatori futuri che avrebbero dovuto avere ordine
                // e solo una volta per sessione
                if (contatoriNelRange.length > 0 && !window._osrmAlertShown) {{
                    window._osrmAlertShown = true;
                    alert('⚠️ Ordine percorso non disponibile!\\n\\nOSRM non era attivo durante la generazione.\\nRigenera la mappa con Docker attivo.');
                }}

                // Restituisce lista in ordine originale (non ottimizzata)
                return lista;
            }}
        }}

        function renderDettaglioSidebar(perTecnico, data) {{
            const resultsDiv = document.getElementById('results');
            let html = '';
            
            html += `
                <div class="tappa-card deposito">
                    <div class="tappa-header">
                        <div class="tappa-numero">🏠</div>
                        <div class="tappa-cliente">${{deposito.nome}}</div>
                    </div>
                    <div class="tappa-dettagli">
                        <div>📍 Punto di partenza</div>
                    </div>
                </div>
            `;
            
            const tecnici = Object.keys(perTecnico).sort();
            
            tecnici.forEach(tec => {{
                const lista = perTecnico[tec];
                const colore = getColore(tec);
                
                if (tecnici.length > 1 || tec !== 'TUTTI') {{
                    html += `
                        <div class="tecnico-header">
                            <div class="tecnico-color" style="background: ${{colore}}"></div>
                            <div class="tecnico-nome">${{tec === 'TUTTI' ? '📦 Senza Operatore' : '👤 ' + getDisplayName(tec)}}</div>
                            <div class="tecnico-count">${{lista.length}} tappe</div>
                        </div>
                    `;
                }}
                
                lista.forEach((c, idx) => {{
                    // Determina se è schedulabile (senza data o data passata)
                    const oggiStr = getOggiStr();
                    const isSchedulabile = !c.data || c.data === '' || c.data < oggiStr;
                    const schedStyle = isSchedulabile ? 'border-left: 3px solid var(--accent-secondary, #06b6d4);' : '';
                    let dateInfo = '';
                    if (showAllDatesInDetail) {{
                        dateInfo = isSchedulabile 
                            ? (c.data ? `<div style="color: var(--danger, #ef4444); font-size: 0.78rem;">⏰ era: ${{formatDate(c.data)}}</div>` : `<div style="color: var(--accent-secondary, #06b6d4); font-size: 0.78rem;">📋 senza data</div>`)
                            : `<div style="color: var(--text-muted); font-size: 0.78rem;">📅 ${{formatDate(c.data)}}</div>`;
                    }} else if (isSchedulabile) {{
                        dateInfo = `<div style="color: var(--accent-secondary, #06b6d4); font-size: 0.78rem;">📋 schedulabile</div>`;
                    }}
                    
                    // Per schedulabili in showAllDatesInDetail usa mostraContatorePopup (marker temp), altrimenti evidenzia il marker esistente
                    const onclickAction = isSchedulabile && showAllDatesInDetail 
                        ? `mostraContatorePopup(contatori.find(cc => cc.id === '${{c.id}}'))` 
                        : `evidenziaContatore('${{c.id}}')`;
                    
                    html += `
                        <div class="tappa-card ${{c.is_appuntamento ? 'appuntamento' : ''}}${{isSchedulabile ? ' schedulabile-tappa' : ''}}" 
                             onclick="${{onclickAction}}"
                             data-id="${{c.id}}"
                             data-eneltel="${{c.eneltel}}"
                             style="${{schedStyle}}">
                            <div class="tappa-header">
                                <div class="tappa-numero" style="background: ${{isSchedulabile ? 'var(--accent-secondary, #06b6d4)' : colore}}">${{idx + 1}}</div>
                                <div class="tappa-cliente">${{c.cliente || 'N/D'}}</div>
                                <button class="tappa-edit-btn" onclick="event.stopPropagation(); apriVistaModifica('${{c.eneltel}}')" title="Modifica">✏️</button>
                                ${{c.is_appuntamento ? `<div class="tappa-app-badge">☎️ ${{c.ora_appuntamento || 'APP'}}</div>` : ''}}
                            </div>
                            <div class="tappa-dettagli">
                                <div>📍 ${{c.comune}} - ${{c.via || 'N/D'}}</div>
                                <div>🔌 ${{c.eneltel}}${{c.lcl ? ` | 🏷️ ${{c.lcl}}` : ''}}</div>
                                ${{dateInfo}}
                                ${{c.telefono ? `<div>📱 ${{c.telefono}}</div>` : ''}}
                                ${{c.note ? `<div>📝 ${{c.note}}</div>` : ''}}
                            </div>
                            ${{c.orari_lettere && !c.is_appuntamento ? `<div class="tappa-orari-lettere">✉️ ${{c.orari_lettere}}</div>` : ''}}
                        </div>
                    `;
                }});
            }});
            
            html += `
                <div class="tappa-card deposito">
                    <div class="tappa-header">
                        <div class="tappa-numero">🏠</div>
                        <div class="tappa-cliente">${{deposito.nome}}</div>
                    </div>
                    <div class="tappa-dettagli">
                        <div>📍 Rientro al deposito</div>
                    </div>
                </div>
            `;
            
            resultsDiv.innerHTML = html;
        }}
        
        function renderDettaglioMappa(perTecnico, data) {{
            // Salva la vista corrente se richiesto
            let savedCenter = null;
            let savedZoom = null;
            if (preserveMapView) {{
                savedCenter = map.getCenter();
                savedZoom = map.getZoom();
            }}

            clearMap();

            const allPoints = [];
            const tecnici = Object.keys(perTecnico).sort();
            
            // Prepara lista completa con indice e tecnico
            const oggiStr = getOggiStr();
            const tuttiContatori = [];
            tecnici.forEach(tec => {{
                const lista = perTecnico[tec];
                lista.forEach((c, idx) => {{
                    const isSchedulabile = !c.data || c.data === '' || c.data < oggiStr;
                    tuttiContatori.push({{
                        ...c,
                        tecnico: tec,
                        indiceTecnico: idx,
                        colore: isSchedulabile ? '#06b6d4' : getColore(tec),
                        _isSchedulabile: isSchedulabile
                    }});
                }});
            }});
            
            // Filtra il contatore pinnato PRIMA del raggruppamento
            // (evita che finisca in un gruppo sovrapposto creando duplicati)
            const contPerMappa = pinnedContatore
                ? tuttiContatori.filter(c => c.eneltel !== pinnedContatore.eneltel)
                : tuttiContatori;

            // Raggruppa contatori sovrapposti
            const gruppi = raggruppaContatoriSovrapposti(contPerMappa);
            
            gruppi.forEach(gruppo => {{
                const isGruppo = gruppo.contatori.length > 1;
                const primoContatore = gruppo.contatori[0];
                const colore = primoContatore.colore;
                
                if (isGruppo) {{
                    // Usa la funzione dedicata per i marker di gruppo
                    const marker = creaMarkerGruppo(gruppo, colore);
                    marker.addTo(map);
                    markers.push(marker);
                }} else {{
                    // Marker singolo (comportamento normale)
                    const c = primoContatore;

                    const isApp = c.is_appuntamento;
                    const eneltelLast3 = c.eneltel.slice(-3);
                    
                    let icon;
                    if (c._isSchedulabile) {{
                        // Marker schedulabile: circleMarker arancione (canvas, molto performante)
                        const cm = L.circleMarker([c.lat, c.lon], {{
                            radius: 8,
                            fillColor: '#06b6d4',
                            color: '#ffffff',
                            weight: 2,
                            opacity: 1,
                            fillOpacity: 0.9
                        }})
                            .bindPopup(() => createContatorePopup(c, '📋', '#06b6d4'))
                            .addTo(map);
                        cm.contatore = c;
                        cm.numeroTappa = c.indiceTecnico + 1;
                        cm.coloreTecnico = '#06b6d4';
                        cm.prevPoint = null;
                        markers.push(cm);
                        allPoints.push([gruppo.lat, gruppo.lon]);
                        return; // skip to next gruppo
                    }}
                    // Marker non-schedulabile
                    icon = L.divIcon({{
                        className: 'marker-contatore' + (isApp ? ' marker-appuntamento' : ''),
                        html: `<div style="background: ${{colore}}; width: 100%; height: 100%; border-radius: 50%; display: flex; align-items: center; justify-content: center; position: relative;">
                            ${{c.indiceTecnico + 1}}
                            ${{showEneltelLabels ? `<span class="marker-label">${{eneltelLast3}}</span>` : ''}}
                        </div>`,
                        iconSize: [24, 24],
                        iconAnchor: [12, 12]
                    }});
                    
                    const marker = L.marker([c.lat, c.lon], {{ icon }})
                        .bindPopup(() => createContatorePopup(c, c.indiceTecnico + 1, colore))
                        .addTo(map);

                    marker.contatore = c;
                    marker.numeroTappa = c.indiceTecnico + 1;
                    marker.coloreTecnico = colore;
                    marker.prevPoint = null;
                    markers.push(marker);
                }}
                
                allPoints.push([gruppo.lat, gruppo.lon]);
            }});
            
            // Aggiungi marker pinned se presente
            if (pinnedContatore) {{
                const pinIcon = L.divIcon({{
                    className: 'marker-pinned',
                    html: `<div style="background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); width: 32px; height: 32px; border-radius: 50%; display: flex; align-items: center; justify-content: center; border: 3px solid white; box-shadow: 0 4px 12px rgba(0,0,0,0.4); font-size: 16px;">📌</div>`,
                    iconSize: [32, 32],
                    iconAnchor: [16, 16]
                }});

                if (pinnedMarker) map.removeLayer(pinnedMarker);

                pinnedMarker = L.marker([pinnedContatore.lat, pinnedContatore.lon], {{ icon: pinIcon, zIndexOffset: 10000 }})
                    .bindPopup(() => createContatorePopup(pinnedContatore, '📌', '#f5576c'))
                    .addTo(map);

                allPoints.push([pinnedContatore.lat, pinnedContatore.lon]);
            }}

            // Ripristina la vista salvata o centra sui punti
            if (savedCenter && savedZoom) {{
                map.setView(savedCenter, savedZoom);
                preserveMapView = false; // Reset del flag
            }} else if (allPoints.length > 0) {{
                const bounds = L.latLngBounds(allPoints);
                bounds.extend([deposito.lat, deposito.lon]);
                map.fitBounds(bounds, {{ padding: [50, 50] }});
            }}

            // Disegna rotte se toggle attivo
            if (mostraRotteAttivo) {{
                disegnaRotte();
            }}
        }}

        // Calcola distanza in km tra due punti (Haversine)
        function calcolaDistanzaKm(lat1, lon1, lat2, lon2) {{
            const R = 6371; // Raggio terra in km
            const dLat = (lat2 - lat1) * Math.PI / 180;
            const dLon = (lon2 - lon1) * Math.PI / 180;
            const a = Math.sin(dLat/2) * Math.sin(dLat/2) +
                      Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
                      Math.sin(dLon/2) * Math.sin(dLon/2);
            const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
            return R * c * 1.3; // Moltiplica per 1.3 per approssimare strade reali
        }}
        
        // Calcola tempo in minuti data distanza e velocità media
        function calcolaTempoMinuti(distanzaKm, velocitaKmh = 35) {{
            return Math.round((distanzaKm / velocitaKmh) * 60);
        }}
        
        // Formatta tempo in formato leggibile
        function formattaTempo(minuti) {{
            if (minuti < 1) return '< 1 min';
            if (minuti < 60) return `${{minuti}} min`;
            const ore = Math.floor(minuti / 60);
            const min = minuti % 60;
            return min > 0 ? `${{ore}}h ${{min}}min` : `${{ore}}h`;
        }}

        function createContatorePopup(c, num, colore, prevPoint) {{
            let html = `<div class="popup-content" data-eneltel="${{c.eneltel}}" data-popup-num="${{num}}" data-popup-colore="${{colore}}">`;

            if (c.is_appuntamento) {{
                html += `<div class="popup-app">☎️ APPUNTAMENTO ${{c.ora_appuntamento || ''}}
                    <button onclick="event.stopPropagation(); rimuoviAppuntamento('${{c.eneltel}}')" style="margin-left: 8px; background: rgba(255,255,255,0.2); border: 1px solid rgba(255,255,255,0.4); color: white; border-radius: 4px; padding: 2px 8px; cursor: pointer; font-size: 11px;" title="Rimuovi appuntamento">✕</button>
                </div>`;
            }}
            
            // Mostra tempo di arrivo dal punto precedente
            if (prevPoint) {{
                const distanza = calcolaDistanzaKm(prevPoint.lat, prevPoint.lon, c.lat, c.lon);
                const tempo = calcolaTempoMinuti(distanza);
                const origine = prevPoint.isDeposito ? 'dal deposito' : `dalla tappa ${{prevPoint.num}}`;
                html += `<div class="popup-travel-time">🚗 ${{formattaTempo(tempo)}} ${{origine}} (~${{distanza.toFixed(1)}} km)</div>`;
            }}

            html += `<div class="popup-header">`;
            html += `<div class="popup-title">${{num}}. ${{c.cliente || 'N/D'}}</div>`;
            if (c.data && c.data !== '') {{
                html += `<div class="popup-subtitle">📅 ${{formatDate(c.data)}}</div>`;
            }}
            html += `</div>`;

            html += `<div class="popup-row"><span class="popup-label">Comune</span><span class="popup-value">${{c.comune}}</span></div>`;
            
            // Località (se presente e diversa dal comune)
            if (c.localita && c.localita !== c.comune) {{
                html += `<div class="popup-row"><span class="popup-label">Località</span><span class="popup-value">${{c.localita}}</span></div>`;
            }}
            
            // Via con civico
            let indirizzo = c.via || 'N/D';
            if (c.civico) indirizzo += ', ' + c.civico;
            html += `<div class="popup-row"><span class="popup-label">Via</span><span class="popup-value">${{indirizzo}}</span></div>`;
            
            html += `<div class="popup-row"><span class="popup-label">Eneltel</span><span class="popup-value">${{c.eneltel}}</span></div>`;
            html += `<div class="popup-row"><span class="popup-label">POD</span><span class="popup-value">${{c.pod}}</span></div>`;

            if (c.tipo_ce) {{
                html += `<div class="popup-row"><span class="popup-label">Tipologia</span><span class="popup-value">${{c.tipo_ce}}</span></div>`;
            }}
            if (c.lcl) {{
                html += `<div class="popup-row"><span class="popup-label">LCL</span><span class="popup-value">${{c.lcl}}</span></div>`;
            }}
            if (c.telefono) {{
                html += `<div class="popup-row"><span class="popup-label">Telefono</span><span class="popup-value">${{c.telefono}}</span></div>`;
            }}
            if (c.orari_lettere) {{
                html += `<div class="popup-row"><span class="popup-label">Orari lettera</span><span class="popup-value" style="color: #7ec8e3;">✉️ ${{c.orari_lettere}}</span></div>`;
            }}
            if (c.note) {{
                html += `<div class="popup-row"><span class="popup-label">Posizione</span><span class="popup-value">${{c.note}}</span></div>`;
            }}
            if (c.note_app) {{
                html += `<div class="popup-row"><span class="popup-label">Note</span><span class="popup-value">${{c.note_app}}</span></div>`;
            }}

            // Aggiungi pulsante pin
            const isPinned = pinnedContatore && pinnedContatore.eneltel === c.eneltel;
            if (isPinned) {{
                html += `<button class="btn-unpin-popup" onclick="unpinContatore()" style="margin-top: 10px; width: 100%; padding: 8px; background: linear-gradient(135deg, #ff6b6b 0%, #ee5a6f 100%); color: white; border: none; border-radius: 8px; cursor: pointer; font-weight: 600; font-size: 13px;">❌ Rimuovi pin</button>`;
            }} else {{
                html += `<button class="btn-pin-popup" onclick="pinContatoreFromPopup('${{c.eneltel}}')" style="margin-top: 10px; width: 100%; padding: 8px; background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); color: white; border: none; border-radius: 8px; cursor: pointer; font-weight: 600; font-size: 13px;">📌 Fissa questo</button>`;
            }}
            
            // Pulsante rimuovi appuntamento (se c'è un appuntamento)
            if (c.is_appuntamento) {{
                html += `<button onclick="rimuoviAppuntamento('${{c.eneltel}}')" style="margin-top: 6px; width: 100%; padding: 8px; background: linear-gradient(135deg, #e74c3c 0%, #c0392b 100%); color: white; border: none; border-radius: 8px; cursor: pointer; font-weight: 600; font-size: 13px;">🗑️ Rimuovi appuntamento</button>`;
            }}
            
            // Pulsante per aprire nella vista modifica
            html += `<button onclick="apriVistaModifica('${{c.eneltel}}')" style="margin-top: 6px; width: 100%; padding: 8px; background: linear-gradient(135deg, #3498db 0%, #2980b9 100%); color: white; border: none; border-radius: 8px; cursor: pointer; font-weight: 600; font-size: 13px;">✏️ Modifica</button>`;

            html += `</div>`;
            return html;
        }}
        
        async function rimuoviAppuntamento(eneltel) {{
            if (!confirm('Vuoi rimuovere l\\'appuntamento da questo contatore?')) return;
            
            try {{
                const response = await fetch(`${{window.location.origin}}/api/contatore/${{eneltel}}/rimuovi-appuntamento`, {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }}
                }});
                
                const result = await response.json();
                
                if (response.ok && result.success) {{
                    // Aggiorna il contatore locale
                    const idx = contatori.findIndex(c => c.eneltel === eneltel);
                    if (idx >= 0) {{
                        contatori[idx].is_appuntamento = false;
                        contatori[idx].ora_appuntamento = null;
                        contatori[idx].note_app = result.nota || '';
                    }}
                    
                    map.closePopup();
                    alert('✅ Appuntamento rimosso');
                    
                    // Ricarica la vista corrente per aggiornare i badge
                    if (typeof refreshCurrentView === 'function') {{
                        refreshCurrentView();
                    }}
                }} else {{
                    alert('❌ Errore: ' + (result.error || 'Errore sconosciuto'));
                }}
            }} catch (err) {{
                alert('❌ Errore di connessione: ' + err.message);
            }}
        }}
        
        function evidenziaContatore(id) {{
            // Prima cerca nei marker singoli
            let marker = markers.find(m => m.contatore && m.contatore.id === id);
            
            // Se non trovato, cerca nei marker di gruppo
            if (!marker) {{
                marker = markers.find(m => {{
                    if (m.gruppo) {{
                        return m.gruppo.contatori.some(c => c.id === id);
                    }}
                    return false;
                }});
            }}
            
            if (marker) {{
                map.flyTo(marker.getLatLng(), 16);
                marker.openPopup();
            }}
        }}

        // Mostra popup contatore sulla mappa (per schedulabili)
        function mostraContatorePopup(c) {{
            if (!c) return;
            
            // Verifica coordinate valide
            if (!isValidCoordinate(c.lat, c.lon)) {{
                console.warn('Contatore senza coordinate valide:', c.eneltel);
                return;
            }}
            
            // Rimuovi marker temporaneo precedente
            if (tempSchedulabileMarker) {{
                map.removeLayer(tempSchedulabileMarker);
                tempSchedulabileMarker = null;
            }}
            
            // Centra mappa sul contatore
            map.flyTo([c.lat, c.lon], 16);
            
            // Crea marker temporaneo sul contatore
            const schedIcon = L.divIcon({{
                className: 'marker-schedulabile-temp',
                html: `<div style="background: linear-gradient(135deg, #06b6d4 0%, #0891b2 100%); width: 30px; height: 30px; border-radius: 50%; display: flex; align-items: center; justify-content: center; border: 3px solid white; box-shadow: 0 4px 12px rgba(0,0,0,0.4); font-size: 14px; animation: pulse-marker 1.5s ease-in-out infinite;">📋</div>`,
                iconSize: [30, 30],
                iconAnchor: [15, 15]
            }});
            tempSchedulabileMarker = L.marker([c.lat, c.lon], {{ icon: schedIcon }}).addTo(map);
            
            // Salva riferimento al contatore nel marker
            tempSchedulabileMarker.contatore = c;
            
            // Usa il popup completo con modifica, pin, ecc.
            // Bind popup al marker e aprilo (funzione per contenuto dinamico)
            tempSchedulabileMarker.bindPopup(() => createContatorePopup(c, '📋', '#06b6d4'), {{ maxWidth: 350 }}).openPopup();
        }}
        
        // ============================================================
        // RICERCA
        // ============================================================
        function mostraRisultatiRicerca(contatoriMatch) {{
            const resultsDiv = document.getElementById('results');
            
            document.getElementById('breadcrumb').classList.remove('visible');
            
            resultsDiv.innerHTML = contatoriMatch.map((c, idx) => `
                <div class="tappa-card ${{c.is_appuntamento ? 'appuntamento' : ''}}" 
                     onclick="vaiAContatore(${{c.lat}}, ${{c.lon}}, '${{c.id}}')"
                     data-id="${{c.id}}"
                     data-eneltel="${{c.eneltel}}">
                    <div class="tappa-header">
                        <div class="tappa-numero">${{idx + 1}}</div>
                        <div class="tappa-cliente">${{c.cliente || 'N/D'}}</div>
                        <button class="tappa-edit-btn" onclick="event.stopPropagation(); apriVistaModifica('${{c.eneltel}}')" title="Modifica">✏️</button>
                        ${{c.is_appuntamento ? `<div class="tappa-app-badge">☎️</div>` : ''}}
                    </div>
                    <div class="tappa-dettagli">
                        <div>📅 ${{formatDate(c.data)}} | 📍 ${{c.comune}}</div>
                        <div>🔌 ${{c.eneltel}} | POD: ${{c.pod}}</div>
                        ${{c.lcl ? `<div>🏷️ LCL: ${{c.lcl}}</div>` : ''}}
                    </div>
                    ${{c.orari_lettere && !c.is_appuntamento ? `<div class="tappa-orari-lettere">✉️ ${{c.orari_lettere}}</div>` : ''}}
                </div>
            `).join('');
            
            clearMap();
            
            // Prepara lista con indice
            const listaConIndice = contatoriMatch.map((c, idx) => ({{
                ...c,
                indiceTecnico: idx,
                colore: '#e94560'
            }}));
            
            // Raggruppa contatori sovrapposti
            const gruppi = raggruppaContatoriSovrapposti(listaConIndice);
            
            gruppi.forEach(gruppo => {{
                const isGruppo = gruppo.contatori.length > 1;
                const primoContatore = gruppo.contatori[0];
                
                if (isGruppo) {{
                    // Usa la funzione dedicata per i marker di gruppo
                    const marker = creaMarkerGruppo(gruppo, '#e94560');
                    marker.addTo(map);
                    markers.push(marker);
                }} else {{
                    // Marker singolo
                    const c = primoContatore;
                    const icon = L.divIcon({{
                        className: 'marker-contatore' + (c.is_appuntamento ? ' marker-appuntamento' : ''),
                        html: `<div style="background: #e94560; width: 100%; height: 100%; border-radius: 50%; display: flex; align-items: center; justify-content: center;">${{c.indiceTecnico + 1}}</div>`,
                        iconSize: [24, 24],
                        iconAnchor: [12, 12]
                    }});
                    
                    const marker = L.marker([c.lat, c.lon], {{ icon }})
                        .bindPopup(() => createContatorePopup(c, c.indiceTecnico + 1, '#e94560'))
                        .addTo(map);
                    
                    marker.contatore = c;
                    marker.numeroTappa = c.indiceTecnico + 1;
                    marker.coloreTecnico = '#e94560';
                    marker.prevPoint = null;
                    markers.push(marker);
                }}
            }});
            
            if (contatoriMatch.length > 0) {{
                const bounds = L.latLngBounds(contatoriMatch.map(c => [c.lat, c.lon]));
                map.fitBounds(bounds, {{ padding: [50, 50] }});
            }}
            
            document.getElementById('statZone').textContent = '-';
            document.getElementById('statContatori').textContent = contatoriMatch.length;
            document.getElementById('statAppuntamenti').textContent = contatoriMatch.filter(c => c.is_appuntamento).length;
        }}
        
        function vaiAContatore(lat, lon, id) {{
            map.flyTo([lat, lon], 16);
            const marker = markers.find(m => m.contatore && m.contatore.id === id);
            if (marker) marker.openPopup();
        }}
        
        // ============================================================
        // NAVIGAZIONE
        // ============================================================
        function tornaAlleZone() {{
            document.getElementById('searchInput').value = '';

            // Resetta anche ricerca avanzata se attiva
            if (advancedSearchMode) {{
                clearAdvancedSearch();
            }}

            // Nascondi SEMPRE breadcrumb e navigazione date quando si torna indietro
            // (evita bug grafico se isAutoRefresh rimane bloccato)
            document.getElementById('breadcrumb').classList.remove('visible');
            document.getElementById('dateNavigation').classList.remove('visible');
            
            // Se siamo nella vista operatore singolo o multi, torna alla vista tutti operatori (mantieni data)
            if (currentView === 'operatore' || currentView === 'multiOperatori') {{
                const dataSelezionata = document.getElementById('dataFilter').value;
                if (dataSelezionata) {{
                    // Resetta solo l'operatore, mantieni la data
                    resetOperatoreFilter();
                    currentOperatore = null;
                    // Torna alla vista tutti operatori
                    mostraPercorsoTuttiOperatori(dataSelezionata);
                    return;
                }}
            }}
            
            // Comportamento standard: resetta tutto (sia da tuttiOperatori che da altre viste)
            document.getElementById('dataFilter').value = '';
            resetOperatoreFilter();
            document.getElementById('operatoreFilterContainer').classList.remove('visible');
            currentDataFiltro = '';
            currentOperatore = null;

            if (viewMode === 'zone') {{
                mostraVistaZone();
            }} else {{
                mostraVistaComuni();
            }}
        }}
        
        // ============================================================
        // AUTO-REFRESH (ottimizzato: fetch JSON invece di reload pagina)
        // ============================================================
        let lastTimestamp = dataTimestamp;

        // Refresh SMART: ri-renderizza la vista corrente con animazioni per nuovi marker
        function refreshCurrentView() {{
            // Salva gli eneltel attuali per rilevare nuovi marker
            const oldEneltels = new Set(markers.filter(m => m.contatore).map(m => m.contatore.eneltel));

            // Salva scroll sidebar
            const resultsDiv = document.getElementById('results');
            const savedScroll = resultsDiv ? resultsDiv.scrollTop : 0;

            // Salva popup aperto
            let openPopupContatoreId = null;
            markers.forEach(m => {{
                if (m.isPopupOpen && m.isPopupOpen() && m.contatore) {{
                    openPopupContatoreId = m.contatore.id;
                }}
            }});

            preserveMapView = true;
            isAutoRefresh = true;

            try {{
                // Ri-renderizza completamente la vista corrente
                switch (currentView) {{
                    case 'zone':
                        mostraVistaZone(document.getElementById('searchInput')?.value || '');
                        break;
                    case 'towns':
                        mostraVistaComuni(document.getElementById('searchInput')?.value || '');
                        break;
                    case 'detail':
                        if (currentZona && currentData) {{
                            const isZona = zone.some(z => z.nome === currentZona);
                            if (isZona) {{
                                apriDettaglio(currentZona, currentData);
                            }} else {{
                                apriDettaglioComune(currentZona, currentData);
                            }}
                        }}
                        break;
                    case 'operatore':
                        if (currentOperatore && currentData) {{
                            mostraPercorsoCompletoOperatore(currentOperatore, currentData);
                        }}
                        break;
                    case 'tuttiOperatori':
                        if (currentData) {{
                            mostraPercorsoTuttiOperatori(currentData);
                        }}
                        break;
                    case 'multiOperatori':
                        if (currentData) {{
                            const selOps = getSelectedOperatori();
                            if (selOps.length > 0) {{
                                mostraPercorsoMultiOperatori(selOps, currentData);
                            }} else {{
                                mostraPercorsoTuttiOperatori(currentData);
                            }}
                        }}
                        break;
                    case 'search-results':
                        // Per i risultati di ricerca, aggiorna solo i dati nei marker esistenti
                        // (non possiamo ri-eseguire la query di ricerca)
                        markers.forEach(marker => {{
                            if (marker.contatore) {{
                                const updated = contatori.find(c => c.id === marker.contatore.id || c.eneltel === marker.contatore.eneltel);
                                if (updated) {{
                                    marker.contatore = updated;
                                    if (marker.isPopupOpen && marker.isPopupOpen()) {{
                                        const popup = marker.getPopup();
                                        if (popup) popup.update();
                                    }}
                                }}
                            }}
                        }});
                        break;
                    default:
                        mostraVistaZone();
                }}
            }} finally {{
                isAutoRefresh = false;
            }}

            // Ripristina scroll sidebar
            if (resultsDiv && savedScroll > 0) {{
                resultsDiv.scrollTop = savedScroll;
            }}

            // Rileva nuovi marker e anima la loro comparsa
            const newEneltels = new Set(markers.filter(m => m.contatore).map(m => m.contatore.eneltel));
            const addedEneltels = new Set([...newEneltels].filter(e => !oldEneltels.has(e)));
            const removedCount = [...oldEneltels].filter(e => !newEneltels.has(e)).length;

            // Mostra toast se ci sono cambiamenti
            if (addedEneltels.size > 0 || removedCount > 0) {{
                const parts = [];
                if (addedEneltels.size > 0) parts.push(`+${{addedEneltels.size}} nuov${{addedEneltels.size === 1 ? 'o' : 'i'}}`);
                if (removedCount > 0) parts.push(`-${{removedCount}} rimoss${{removedCount === 1 ? 'o' : 'i'}}`);
                showMappaToast(`✨ Mappa aggiornata: ${{parts.join(', ')}}`);
            }}

            // Ripristina popup se era aperto
            if (openPopupContatoreId) {{
                setTimeout(() => {{
                    const marker = markers.find(m => m.contatore && m.contatore.id === openPopupContatoreId);
                    if (marker) marker.openPopup();
                }}, 150);
            }}

            console.log(`🔄 Smart refresh: vista=${{currentView}}, nuovi=${{addedEneltels.size}}, rimossi=${{removedCount}}`);
        }}

        // Toast discreto in basso al centro per notificare aggiornamenti
        let _mappaToastTimeout = null;
        function showMappaToast(msg) {{
            const toast = document.getElementById('mappaToast');
            if (!toast) return;
            toast.textContent = msg;
            toast.classList.add('visible');
            clearTimeout(_mappaToastTimeout);
            _mappaToastTimeout = setTimeout(() => {{
                toast.classList.remove('visible');
            }}, 3500);
        }}

        // Flash discreto sull'orario quando i dati vengono aggiornati
        function flashUpdateTime() {{
            const el = document.getElementById('updateTime');
            if (!el) return;
            el.style.transition = 'color 0.3s ease, text-shadow 0.3s ease';
            el.style.color = '#2ecc71';
            el.style.textShadow = '0 0 8px #2ecc71';
            setTimeout(() => {{
                el.style.color = '';
                el.style.textShadow = '';
            }}, 1500);
        }}

        // ============================================================
        // SSE (Server-Sent Events) - Aggiornamenti in tempo reale
        // ============================================================
        let eventSource = null;
        let sseReconnectAttempts = 0;
        const SSE_MAX_RECONNECT = 5;
        
        function initSSE() {{
            // Solo in modalità fetch (http://), non file://
            if (window.location.protocol === 'file:') {{
                console.log('[SSE] Modalità file://, SSE disabilitato');
                // Fallback: polling tradizionale per file://
                setInterval(checkForUpdates, REFRESH_INTERVAL);
                return;
            }}
            
            console.log('[SSE] Inizializzazione connessione...');
            
            eventSource = new EventSource('/api/events');
            
            eventSource.onopen = () => {{
                console.log('[SSE] ✅ Connesso al server');
                sseReconnectAttempts = 0;
            }};
            
            eventSource.addEventListener('connected', (e) => {{
                console.log('[SSE] Handshake completato');
            }});
            
            eventSource.addEventListener('data_changed', async (e) => {{
                const data = JSON.parse(e.data);
                console.log('[SSE] 📢 Dati modificati:', data);
                await reloadData();
            }});
            
            eventSource.addEventListener('data_changing', (e) => {{
                console.log('[SSE] ⏳ Sync esterno in corso...');
                showMappaToast('⏳ Aggiornamento dati in corso...');
                // Mostra clessidra sul pulsante refresh
                const btn = document.getElementById('refreshBtn');
                if (btn && !_refreshWaitingForSSE) {{
                    _refreshWaitingForSSE = true;
                    btn.textContent = '⏳';
                    btn.disabled = true;
                    btn.style.opacity = '0.7';
                }}
            }});
            
            eventSource.addEventListener('sync_complete', async (e) => {{
                const data = JSON.parse(e.data);
                console.log('[SSE] 📢 Sync completato:', data);
                _onSyncComplete();
                await reloadData();
            }});
            
            eventSource.onerror = (e) => {{
                console.log('[SSE] ❌ Errore connessione');
                eventSource.close();
                
                sseReconnectAttempts++;
                if (sseReconnectAttempts <= SSE_MAX_RECONNECT) {{
                    const delay = Math.min(5000 * sseReconnectAttempts, 30000);
                    console.log(`[SSE] Riconnessione in ${{delay/1000}}s (tentativo ${{sseReconnectAttempts}}/${{SSE_MAX_RECONNECT}})`);
                    setTimeout(initSSE, delay);
                }} else {{
                    console.log('[SSE] Max tentativi raggiunti, fallback a polling');
                    setInterval(checkForUpdates, REFRESH_INTERVAL);
                }}
            }};
        }}
        
        async function reloadData() {{
            try {{
                const response = await fetch(DATA_PATH + '?t=' + Date.now(), {{ cache: 'no-store' }});
                if (!response.ok) {{
                    console.log('[SSE] ❌ Fetch dati fallito');
                    return;
                }}
                
                const newData = await response.json();
                console.log('[SSE] 🔄 Aggiornamento dati, timestamp:', newData.timestamp);
                
                // Aggiorna dati in memoria
                zone = newData.zone;
                contatori = newData.contatori;
                rotte = newData.rotte;
                dataTimestamp = newData.timestamp;
                lastTimestamp = newData.timestamp;
                
                // Ricalcola e refresh
                updateContatoriVisibili();
                updateSchedulabiliBadge();
                if (planningMode) {{
                    populatePlanningDropdowns();
                    applyPlanningFilters(true);
                }} else {{
                    refreshCurrentView();
                }}
                
                // Aggiorna UI
                document.getElementById('updateTime').textContent = formatTimestamp(dataTimestamp);
                flashUpdateTime();
            }} catch (e) {{
                console.log('[SSE] Errore reload:', e);
            }}
        }}

        // Funzione legacy per fallback polling (file://)
        async function checkForUpdates() {{
            if (dataLoadMode === 'embedded') {{
                // Modalità embedded (file://): reload completo pagina
                try {{
                    const response = await fetch(window.location.href + '?t=' + Date.now(), {{ cache: 'no-store' }});
                    const text = await response.text();

                    const match = text.match(/timestamp: "([0-9]+)"/);
                    if (match && match[1] !== lastTimestamp) {{
                        document.getElementById('updateIndicator').style.display = 'block';
                        setTimeout(() => {{
                            window.location.reload();
                        }}, 1500);
                    }}
                }} catch (e) {{
                    console.log('Check update error (embedded):', e);
                }}
                return;
            }}

            // Questo codice non dovrebbe mai essere raggiunto con SSE attivo
            // Tenuto come fallback extra
            try {{
                const response = await fetch(DATA_PATH + '?t=' + Date.now(), {{ cache: 'no-store' }});
                if (!response.ok) return;

                const newData = await response.json();
                if (newData.timestamp !== lastTimestamp) {{
                    await reloadData();
                }}
            }} catch (e) {{
                console.log('Check update error (fetch):', e);
            }}
        }}

        // Inizializza SSE per aggiornamenti in tempo reale
        initSSE();
        
        // ============================================================
        // EVENTI
        // ============================================================
        document.getElementById('searchInput').addEventListener('input', (e) => {{
            const query = e.target.value.trim();
            updateSearchClearButton();

            if (currentView === 'operatore' && currentOperatore && currentData) {{
                // Se siamo nella vista percorso operatore, filtra i contatori dell'operatore
                mostraPercorsoOperatoreConRicerca(currentOperatore, currentData, query);
            }} else if (currentView === 'multiOperatori' && currentData) {{
                // Se siamo nella vista multi-operatore, aggiorna senza ricerca inline
                const selOps = getSelectedOperatori();
                if (selOps.length > 0) mostraPercorsoMultiOperatori(selOps, currentData);
                else mostraPercorsoTuttiOperatori(currentData);
            }} else if (currentView === 'detail') {{
                // Se siamo nella vista dettaglio, filtra i contatori della zona/data corrente
                apriDettaglioConRicerca(currentZona, currentData, query);
            }} else if (viewMode === 'zone') {{
                mostraVistaZone(query);
            }} else {{
                mostraVistaComuni(query);
            }}
        }});

        // Event listener per campi ricerca avanzata (Enter = avvia ricerca)
        ['searchNominativo', 'searchPod', 'searchEneltel', 'searchComune', 'searchVia', 'searchTelefono'].forEach(id => {{
            document.getElementById(id).addEventListener('keypress', (e) => {{
                if (e.key === 'Enter') {{
                    applicaRicercaAvanzata();
                }}
            }});
        }});

        // Event listener per campi modal search (Enter = avvia ricerca)
        ['modalSearchNominativo', 'modalSearchPod', 'modalSearchEneltel', 'modalSearchComune', 'modalSearchVia', 'modalSearchTelefono'].forEach(id => {{
            document.getElementById(id).addEventListener('keypress', (e) => {{
                if (e.key === 'Enter') {{
                    applyModalAdvancedSearch();
                }}
            }});
        }});

        // Event listener per frecce tastiera (navigazione date)
        document.addEventListener('keydown', (e) => {{
            // Solo se siamo in vista dettaglio e le date navigation sono visibili
            if (currentView === 'detail' && availableDates.length > 1) {{
                const dateNav = document.getElementById('dateNavigation');
                if (dateNav && dateNav.classList.contains('visible')) {{
                    // Controlla che non siamo in un input, textarea o sulla mappa
                    const activeElement = document.activeElement;
                    const isInputActive = activeElement && (
                        activeElement.tagName === 'INPUT' ||
                        activeElement.tagName === 'TEXTAREA' ||
                        activeElement.isContentEditable
                    );

                    // Controlla se il mouse è sulla mappa
                    const mapContainer = document.getElementById('map');
                    const isMouseOverMap = mapContainer && mapContainer.matches(':hover');

                    if (!isInputActive && !isMouseOverMap) {{
                        if (e.key === 'ArrowLeft') {{
                            e.preventDefault();
                            const btnPrev = document.getElementById('btnPrevDate');
                            if (btnPrev && !btnPrev.disabled) {{
                                navigateDate(-1);
                            }}
                        }} else if (e.key === 'ArrowRight') {{
                            e.preventDefault();
                            const btnNext = document.getElementById('btnNextDate');
                            if (btnNext && !btnNext.disabled) {{
                                navigateDate(1);
                            }}
                        }}
                    }}
                }}
            }}
        }});

        // ============================================================
        // STAMPA TAPPE (foglio tecnici)
        // ============================================================

        function stampaTappe() {{
            if (currentView !== 'operatore') {{
                alert('La stampa tappe è disponibile solo nella vista percorso operatore.');
                return;
            }}

            // Recupero contatori dell'operatore per la data corrente
            let contatoriOp = contatori.filter(c =>
                c.data === currentData &&
                (c.tecnico || 'TUTTI') === currentOperatore
            );

            if (currentLCL) {{
                contatoriOp = contatoriOp.filter(c => c.lcl === currentLCL);
            }}

            if (contatoriOp.length === 0) {{
                alert('Nessuna tappa da stampare.');
                return;
            }}

            // Per gli appuntamenti: sposta l'orario appuntamento nella colonna Orari
            // e crea una copia di lavoro per non modificare i dati originali
            const listaTappe = contatoriOp.map(c => {{
                const copia = {{ ...c }};
                if (copia.is_appuntamento && copia.ora_appuntamento) {{
                    copia._orari_display = copia.ora_appuntamento;
                }} else {{
                    copia._orari_display = copia.orari_lettere || '';
                }}
                return copia;
            }});

            // Usa lo stesso ordinamento del percorso (algoritmo di ottimizzazione)
            const tappeOrdinate = ordinaPercorsoJS(listaTappe);
            listaTappe.length = 0;
            tappeOrdinate.forEach(t => listaTappe.push(t));

            // Info operatore e data
            const nomeOp = currentOperatore === 'TUTTI' ? 'Senza Operatore' : getDisplayName(currentOperatore);
            const dataFormattata = formatDate(currentData);
            const isPavia = PROVINCIA === 'pavia';

            // Costruisci tabella HTML stampabile
            let righeHtml = '';
            listaTappe.forEach((c, idx) => {{
                const isApp = c.is_appuntamento;
                const bgColor = isApp ? '#fff3cd' : (idx % 2 === 0 ? '#ffffff' : '#f8f9fa');
                const appLabel = isApp ? ' ☎️' : '';

                righeHtml += `
                    <tr draggable="true" style="background: ${{bgColor}};">
                        <td class="drag-handle" style="text-align:center; font-weight:600; cursor:grab;">☰ ${{idx + 1}}</td>
                        <td contenteditable="true">${{c.tipo_ce || ''}}</td>
                        <td contenteditable="true" style="font-weight:${{isApp ? '700' : '400'}}; color:${{isApp ? '#d63384' : '#333'}};">${{c._orari_display}}${{appLabel}}</td>
                        <td contenteditable="true">${{c.note_app || ''}}</td>
                        <td contenteditable="true" style="font-family:monospace;">${{c.eneltel || ''}}</td>
                        <td contenteditable="true">${{c.cliente || ''}}</td>
                        <td contenteditable="true">${{c.via || ''}}</td>
                        <td contenteditable="true" style="text-align:center;">${{c.civico || ''}}</td>
                        <td contenteditable="true">${{c.comune || ''}}</td>
                        <td contenteditable="true">${{c.note || ''}}</td>
                    </tr>
                `;
            }});

            const totApp = listaTappe.filter(c => c.is_appuntamento).length;

            // Conteggio tipologie CE
            let totMono = 0, totTriSemi = 0, totTriDir = 0;
            listaTappe.forEach(c => {{
                const t = (c.tipo_ce || '').toLowerCase();
                if (t.includes('monofase')) totMono++;
                else if (t.includes('semidirett')) totTriSemi++;
                else if (t.includes('trifase') || t.includes('dirett')) totTriDir++;
            }});
            const ceInfoParts = [];
            if (totMono > 0) ceInfoParts.push(totMono + ' mono');
            if (totTriDir > 0) ceInfoParts.push(totTriDir + ' tri.dir');
            if (totTriSemi > 0) ceInfoParts.push(totTriSemi + ' tri.semi');
            const ceInfoHtml = ceInfoParts.length > 0 ? '<span>⚡ ' + ceInfoParts.join(' · ') + '</span>' : '';

            const htmlStampa = `<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Tappe ${{nomeOp}} - ${{dataFormattata}}</title>
    <style>
        @page {{
            size: A4 landscape;
            margin: 6mm;
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: Arial, Helvetica, sans-serif;
            font-size: 11px;
            color: #333;
            -webkit-print-color-adjust: exact;
            print-color-adjust: exact;
        }}
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 6px 10px;
            background: #2c3e50;
            color: white;
            border-radius: 6px;
            margin-bottom: 6px;
        }}
        .header h1 {{
            font-size: 14px;
            font-weight: 700;
        }}
        .header .info {{
            font-size: 11px;
            text-align: right;
        }}
        .header .info span {{
            display: inline-block;
            margin-left: 12px;
            background: rgba(255,255,255,0.15);
            padding: 2px 8px;
            border-radius: 10px;
        }}
        .table-wrapper {{
            overflow-x: auto;
            width: 100%;
        }}
        table {{
            border-collapse: collapse;
            font-size: 10.5px;
            width: 100%;
        }}
        thead {{ display: table-header-group; }}
        tbody {{ display: table-row-group; }}
        tr {{ page-break-inside: avoid; }}
        th {{
            background: #34495e;
            color: white;
            padding: 5px 6px;
            text-align: center;
            font-weight: 600;
            font-size: 9px;
            text-transform: uppercase;
            letter-spacing: 0.3px;
            white-space: nowrap;
            position: relative;
            border-right: 2px solid rgba(255,255,255,0.15);
        }}
        th:last-child {{ border-right: none; }}
        /* Resize handle */
        th .resize-handle {{
            position: absolute;
            right: -4px;
            top: 0;
            bottom: 0;
            width: 8px;
            cursor: col-resize;
            background: transparent;
            z-index: 10;
        }}
        th .resize-handle:hover,
        th .resize-handle.active {{
            background: rgba(58,123,213,0.5);
        }}
        td {{
            padding: 4px 3px;
            border-bottom: 1px solid #dee2e6;
            vertical-align: middle;
            word-wrap: break-word;
            overflow-wrap: break-word;
            white-space: normal;
            text-align: center;
        }}
        td[contenteditable] {{
            cursor: text;
            transition: background 0.15s;
        }}
        td[contenteditable]:focus {{
            outline: 2px solid #3a7bd5;
            outline-offset: -1px;
            background: #eaf3ff !important;
        }}
        tr:hover {{ background: #e8f4fd !important; }}
        /* Drag & drop styles */
        tr[draggable] {{ transition: opacity 0.2s; }}
        tr.dragging {{ opacity: 0.4; background: #cfe2ff !important; }}
        tr.drag-over {{ border-top: 3px solid #3a7bd5; }}
        .drag-handle {{ user-select: none; font-size: 10px; color: #888; }}
        .drag-handle:active {{ cursor: grabbing; }}
        .footer {{
            margin-top: 6px;
            text-align: center;
            font-size: 8px;
            color: #999;
        }}
        @media print {{
            .no-print {{ display: none !important; }}
            body {{ font-size: 10.5px; }}
            .header {{ margin-bottom: 4px; }}
            th .resize-handle {{ display: none; }}
            th {{ border-right: 1px solid rgba(255,255,255,0.1); }}
            .table-wrapper {{ overflow: visible; }}
            .drag-handle {{ cursor: default; }}
            tr[draggable] {{ -webkit-user-drag: none; }}
        }}
        .print-bar {{
            text-align: center;
            padding: 10px;
            margin-bottom: 10px;
            background: #f0f0f0;
            border-radius: 6px;
        }}
        .print-bar button {{
            padding: 10px 30px;
            font-size: 14px;
            font-weight: 600;
            background: #2c3e50;
            color: white;
            border: none;
            border-radius: 20px;
            cursor: pointer;
        }}
        .print-bar button:hover {{ background: #1a252f; }}
    </style>
</head>
<body>
    <div class="print-bar no-print">
        <button onclick="window.print()">🖨️ Stampa</button>
        <button onclick="riordinaPerFasciaOraria()" style="margin-left:10px; background:#e67e22;">🕐 Riordina per Fascia Oraria</button>
    </div>
    <div class="header">
        <h1>📋 Tappe - ${{nomeOp}}</h1>
        <div class="info">
            <span>📅 ${{dataFormattata}}</span>
            <span>📍 ${{listaTappe.length}} tappe</span>
            ${{totApp > 0 ? '<span>☎️ ' + totApp + ' app.</span>' : ''}}
            ${{ceInfoHtml}}
        </div>
    </div>
    <div class="table-wrapper">
    <table>
        <thead>
            <tr>
                <th style="text-align:center;">#</th>
                <th>Tipo Mis.</th>
                <th>Orario</th>
                <th>Note</th>
                <th>Eneltel</th>
                <th>Nominativo</th>
                <th>Indirizzo</th>
                <th style="text-align:center;">Civ.</th>
                <th>Comune</th>
                <th>Pos. Mis.</th>
            </tr>
        </thead>
        <tbody>
            ${{righeHtml}}
        </tbody>
    </table>
    </div>
    <div class="footer">
        Generato il ${{new Date().toLocaleDateString('it-IT')}} alle ${{new Date().toLocaleTimeString('it-IT', {{hour:'2-digit', minute:'2-digit'}})}}
    </div>
</body>
</html>
<scr` + `ipt>
/* ── Riordina per Fascia Oraria (su richiesta) ── */
function riordinaPerFasciaOraria() {{
    function estraiMinutiOrario(orarioStr) {{
        if (!orarioStr || orarioStr.trim() === '') return 99999;
        const match = orarioStr.match(/([0-9]{{1,2}})[:.h]([0-9]{{2}})/);
        if (match) return parseInt(match[1]) * 60 + parseInt(match[2]);
        const matchOra = orarioStr.match(/(?:^|[^0-9])([0-9]{{1,2}})(?:$|[^0-9])/);
        if (matchOra) {{
            const ore = parseInt(matchOra[1]);
            if (ore >= 0 && ore <= 23) return ore * 60;
        }}
        return 99999;
    }}
    function estraiMinutiFineOrario(orarioStr) {{
        if (!orarioStr || orarioStr.trim() === '') return 99999;
        const matches = [...orarioStr.matchAll(/([0-9]{{1,2}})[:.h]([0-9]{{2}})/g)];
        if (matches.length >= 2) return parseInt(matches[1][1]) * 60 + parseInt(matches[1][2]);
        const matchOre = [...orarioStr.matchAll(/(?:^|[^0-9])([0-9]{{1,2}})(?:$|[^0-9])/g)];
        if (matchOre.length >= 2) {{
            const ore = parseInt(matchOre[matchOre.length - 1][1]);
            if (ore >= 0 && ore <= 23) return ore * 60;
        }}
        return 99999;
    }}

    const tbody = document.querySelector('tbody');
    const rows = Array.from(tbody.querySelectorAll('tr'));

    // Estrai orario dalla colonna 2 (indice 2 = Orario)
    rows.sort((a, b) => {{
        const orarioA = a.cells[2] ? a.cells[2].textContent.trim() : '';
        const orarioB = b.cells[2] ? b.cells[2].textContent.trim() : '';
        const minA = estraiMinutiOrario(orarioA);
        const minB = estraiMinutiOrario(orarioB);
        if (minA !== minB) return minA - minB;
        const fineA = estraiMinutiFineOrario(orarioA);
        const fineB = estraiMinutiFineOrario(orarioB);
        return fineA - fineB;
    }});

    // Rimuovi e reinserisci righe ordinate
    rows.forEach(row => tbody.appendChild(row));

    // Rinumera e ricalcola colori
    rows.forEach((row, i) => {{
        const cell = row.querySelector('.drag-handle');
        if (cell) cell.textContent = '☰ ' + (i + 1);
        const isApp = row.style.background === 'rgb(255, 243, 205)' || row.style.background === '#fff3cd';
        if (!isApp) row.style.background = (i % 2 === 0) ? '#ffffff' : '#f8f9fa';
    }});
}}

(function() {{
    /* ── Drag & Drop riordinamento righe ── */
    const tbody = document.querySelector('tbody');
    let dragRow = null;

    tbody.addEventListener('dragstart', function(e) {{
        const tr = e.target.closest('tr');
        if (!tr || !tbody.contains(tr)) return;
        dragRow = tr;
        tr.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', '');
    }});

    tbody.addEventListener('dragover', function(e) {{
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        const tr = e.target.closest('tr');
        if (!tr || tr === dragRow || !tbody.contains(tr)) return;
        tbody.querySelectorAll('tr.drag-over').forEach(r => r.classList.remove('drag-over'));
        tr.classList.add('drag-over');
    }});

    tbody.addEventListener('dragleave', function(e) {{
        const tr = e.target.closest('tr');
        if (tr) tr.classList.remove('drag-over');
    }});

    tbody.addEventListener('drop', function(e) {{
        e.preventDefault();
        const tr = e.target.closest('tr');
        tbody.querySelectorAll('tr.drag-over').forEach(r => r.classList.remove('drag-over'));
        if (!tr || tr === dragRow || !tbody.contains(tr)) return;
        // Inserisci prima o dopo in base alla posizione relativa
        const rows = Array.from(tbody.querySelectorAll('tr'));
        const fromIdx = rows.indexOf(dragRow);
        const toIdx = rows.indexOf(tr);
        if (fromIdx < toIdx) {{
            tr.after(dragRow);
        }} else {{
            tr.before(dragRow);
        }}
        rinumeraRighe();
    }});

    tbody.addEventListener('dragend', function() {{
        if (dragRow) dragRow.classList.remove('dragging');
        tbody.querySelectorAll('tr.drag-over').forEach(r => r.classList.remove('drag-over'));
        dragRow = null;
    }});

    function rinumeraRighe() {{
        const rows = tbody.querySelectorAll('tr');
        rows.forEach((row, i) => {{
            const cell = row.querySelector('.drag-handle');
            if (cell) cell.textContent = '☰ ' + (i + 1);
            // Ricalcola colori zebrati (mantieni giallo per appuntamenti)
            const isApp = row.style.background === 'rgb(255, 243, 205)' || row.style.background === '#fff3cd';
            if (!isApp) {{
                row.style.background = (i % 2 === 0) ? '#ffffff' : '#f8f9fa';
            }}
        }});
    }}

    /* ── Ridimensionamento colonne ── */
    const table = document.querySelector('table');
    const ths = Array.from(document.querySelectorAll('thead th'));
    let frozen = false;

    function freezeWidths() {{
        if (frozen) return;
        // Read all widths first (avoid layout thrashing)
        const colWidths = ths.map(th => th.offsetWidth);
        const tableW = colWidths.reduce((s, w) => s + w, 0);
        // Lock table to fixed layout with explicit pixel widths
        table.style.tableLayout = 'fixed';
        table.style.width = tableW + 'px';
        ths.forEach((th, i) => {{ th.style.width = colWidths[i] + 'px'; }});
        frozen = true;
    }}

    ths.forEach((th) => {{
        const handle = document.createElement('div');
        handle.className = 'resize-handle';
        th.appendChild(handle);

        handle.addEventListener('mousedown', function(e) {{
            e.preventDefault();
            e.stopPropagation();
            freezeWidths();

            const startX = e.pageX;
            const startColW = th.offsetWidth;
            const startTableW = table.offsetWidth;
            handle.classList.add('active');
            document.body.style.cursor = 'col-resize';
            document.body.style.userSelect = 'none';

            function onMove(ev) {{
                const diff = ev.pageX - startX;
                const newColW = Math.max(24, startColW + diff);
                th.style.width = newColW + 'px';
                table.style.width = (startTableW - startColW + newColW) + 'px';
            }}
            function onUp() {{
                handle.classList.remove('active');
                document.body.style.cursor = '';
                document.body.style.userSelect = '';
                document.removeEventListener('mousemove', onMove);
                document.removeEventListener('mouseup', onUp);
            }}
            document.addEventListener('mousemove', onMove);
            document.addEventListener('mouseup', onUp);
        }});
    }});
}})()
</scr` + `ipt>`;

            // Apri nuova finestra e stampa
            const printWindow = window.open('', '_blank');
            if (printWindow) {{
                printWindow.document.write(htmlStampa);
                printWindow.document.close();
            }} else {{
                alert('Impossibile aprire la finestra di stampa. Controlla il blocco popup.');
            }}
        }}

        // ============================================================
        // PRINT FUNCTIONALITY
        // ============================================================
        let printMap = null;
        let printMarkers = [];
        let printLabels = [];
        let printTecnicoFilter = '';
        let printContatoriData = [];

        function openPrintPreview() {{
            // Solo in vista percorso operatore
            if (currentView !== 'operatore') {{
                alert('La stampa è disponibile solo nella vista percorso completo operatore.');
                return;
            }}

            const modal = document.getElementById('printPreviewModal');
            modal.classList.add('visible');

            // Info stampa - mostra operatore e data
            const nomeOp = currentOperatore === 'TUTTI' ? 'Senza Operatore' : getDisplayName(currentOperatore);
            document.getElementById('printPreviewInfo').textContent = `${{nomeOp}} - ${{formatDate(currentData)}}`;

            // Carica i contatori per la vista corrente
            loadPrintContatori();

            // Nascondi selettore tecnici (siamo già filtrati per operatore)
            document.getElementById('printTecnicoFilter').style.display = 'none';

            // Inizializza mappa di stampa dopo un breve delay per permettere al modal di renderizzare
            setTimeout(() => {{
                initPrintMap();
            }}, 100);
        }}

        function loadPrintContatori() {{
            // In vista operatore, carica tutti i contatori dell'operatore per la data
            printContatoriData = contatori.filter(c =>
                c.data === currentData &&
                (c.tecnico || 'TUTTI') === currentOperatore
            );

            if (currentLCL) {{
                printContatoriData = printContatoriData.filter(c => c.lcl === currentLCL);
            }}
        }}

        function populateTecnicoFilter() {{
            const select = document.getElementById('printTecnicoFilter');
            select.innerHTML = '<option value="">👥 Tutti i tecnici</option>';

            // Trova tutti i tecnici unici
            const tecniciSet = new Set();
            printContatoriData.forEach(c => {{
                const tec = c.tecnico || 'TUTTI';
                if (tec !== 'TUTTI') tecniciSet.add(tec);
            }});

            const tecnici = Array.from(tecniciSet).sort();

            // Mostra/nascondi selettore in base al numero di tecnici
            if (tecnici.length <= 1) {{
                select.style.display = 'none';
            }} else {{
                select.style.display = 'block';
                tecnici.forEach(tec => {{
                    const option = document.createElement('option');
                    option.value = tec;
                    option.textContent = `👤 ${{getDisplayName(tec)}}`;
                    select.appendChild(option);
                }});
            }}

            // Reset filtro
            printTecnicoFilter = '';
            select.value = '';
        }}

        function onPrintTecnicoChange() {{
            printTecnicoFilter = document.getElementById('printTecnicoFilter').value;
            initPrintMap();
        }}

        function closePrintPreview() {{
            const modal = document.getElementById('printPreviewModal');
            modal.classList.remove('visible');

            // Pulizia aggressiva
            cleanupPrintLabels();

            // Cleanup mappa
            if (printMap) {{
                printMap.remove();
                printMap = null;
            }}
            printMarkers = [];
            printContatoriData = [];
        }}

        function cleanupPrintLabels() {{
            // Pulizia aggressiva: rimuovi TUTTE le etichette e liste dal DOM
            document.querySelectorAll('.print-eneltel-label').forEach(el => el.remove());
            document.querySelectorAll('.print-cluster-list').forEach(el => el.remove());
            printLabels = [];
            const svgLines = document.getElementById('printLabelLines');
            if (svgLines) svgLines.innerHTML = '';
        }}

        function initPrintMap() {{
            const printMapDiv = document.getElementById('printMap');

            // Pulizia aggressiva prima di ricreare
            cleanupPrintLabels();

            // Rimuovi mappa esistente se presente
            if (printMap) {{
                printMap.remove();
            }}

            // Crea nuova mappa con tema chiaro per stampa
            printMap = L.map('printMap', {{
                zoomControl: false,
                attributionControl: false,
                zoomSnap: 0.25,           // Zoom graduale
                zoomDelta: 0.5,
                wheelPxPerZoomLevel: 120  // Rotellina più precisa
            }});

            // Usa tile layer chiaro per stampa
            L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
                maxZoom: 19
            }}).addTo(printMap);

            // Filtra contatori per tecnico se selezionato
            let contatoriDaStampare = printContatoriData;
            if (printTecnicoFilter) {{
                contatoriDaStampare = printContatoriData.filter(c =>
                    (c.tecnico || 'TUTTI') === printTecnicoFilter
                );
            }}

            // Raggruppa per tecnico
            const perTecnico = {{}};
            contatoriDaStampare.forEach(c => {{
                const tec = c.tecnico || 'TUTTI';
                if (!perTecnico[tec]) perTecnico[tec] = [];
                perTecnico[tec].push(c);
            }});

            // Ordina ogni gruppo
            Object.keys(perTecnico).forEach(tec => {{
                perTecnico[tec] = ordinaPercorsoJS(perTecnico[tec]);
            }});

            // Cluster e render
            renderPrintMap(perTecnico);
        }}

        function renderPrintMap(perTecnico) {{
            printMarkers = [];
            printLabels = [];

            const allPoints = [];
            const allContatori = [];

            // Raccogli tutti i contatori con i loro numeri progressivi
            const tecnici = Object.keys(perTecnico).sort();
            tecnici.forEach(tec => {{
                const lista = perTecnico[tec];
                const colore = getColore(tec);

                lista.forEach((c, idx) => {{
                    allContatori.push({{
                        ...c,
                        numero: idx + 1,
                        colore: colore,
                        tecnico: tec
                    }});
                    allPoints.push([c.lat, c.lon]);
                }});
            }});

            // Cluster i contatori vicini
            const clusters = clusterMarkersForPrint(allContatori);

            // Render marker clusterizzati
            clusters.forEach(cluster => {{
                const isCluster = cluster.contatori.length > 1;
                const colore = cluster.contatori[0].colore;

                let markerHtml;
                if (isCluster) {{
                    // Marker cluster con badge
                    const numeri = cluster.contatori.map(c => c.numero).join(',');
                    markerHtml = `<div class="print-cluster-marker" style="background: ${{colore}}; width: 28px; height: 28px; position: relative;">
                        <span class="print-cluster-badge">${{cluster.contatori.length}}</span>
                    </div>`;
                }} else {{
                    // Marker singolo
                    markerHtml = `<div class="print-cluster-marker" style="background: ${{colore}}; width: 24px; height: 24px;">
                        ${{cluster.contatori[0].numero}}
                    </div>`;
                }}

                const icon = L.divIcon({{
                    className: '',
                    html: markerHtml,
                    iconSize: isCluster ? [28, 28] : [24, 24],
                    iconAnchor: isCluster ? [14, 14] : [12, 12]
                }});

                const marker = L.marker([cluster.lat, cluster.lon], {{ icon }}).addTo(printMap);

                printMarkers.push({{
                    marker: marker,
                    cluster: cluster
                }});
            }});

            // Centra mappa sui punti
            if (allPoints.length > 0) {{
                const bounds = L.latLngBounds(allPoints);
                bounds.extend([deposito.lat, deposito.lon]);
                printMap.fitBounds(bounds, {{ padding: [80, 80] }});
            }}

            // Attendi che la mappa sia stabile prima di posizionare le etichette
            printMap.whenReady(() => {{
                setTimeout(() => {{
                    positionLabelsForPrint(clusters);
                }}, 300);
            }});

            // Riposiziona etichette durante e dopo il movimento/zoom
            printMap.on('move zoom', () => {{
                // Aggiornamento continuo durante il movimento
                updateLabelPositions(clusters);
            }});

            printMap.on('moveend zoomend', () => {{
                // Ricalcolo completo alla fine del movimento
                setTimeout(() => {{
                    positionLabelsForPrint(clusters);
                }}, 50);
            }});

            // ============================================================
            // PRINT ZOOM SLIDER
            // ============================================================
            const printZoomSlider = document.getElementById('printZoomSlider');
            const printZoomValue = document.getElementById('printZoomValue');

            // Aggiorna slider quando lo zoom cambia
            printMap.on('zoomend', function() {{
                const zoom = printMap.getZoom();
                printZoomSlider.value = zoom;
                printZoomValue.textContent = zoom.toFixed(1) + 'x';
            }});

            // Aggiorna mappa quando lo slider viene trascinato
            printZoomSlider.addEventListener('input', function() {{
                const zoom = parseFloat(this.value);
                printMap.setZoom(zoom);
                printZoomValue.textContent = zoom.toFixed(1) + 'x';
            }});

            // Pulsanti zoom +/-
            document.getElementById('printZoomOut').addEventListener('click', function() {{
                printMap.zoomOut(0.5);
            }});
            document.getElementById('printZoomIn').addEventListener('click', function() {{
                printMap.zoomIn(0.5);
            }});

            // Inizializza il valore dello slider dopo fitBounds
            setTimeout(() => {{
                const zoom = printMap.getZoom();
                printZoomSlider.value = zoom;
                printZoomValue.textContent = zoom.toFixed(1) + 'x';
            }}, 350);
        }}

        function updateLabelPositions(clusters) {{
            // Aggiorna posizioni etichette/liste durante pan/zoom
            const svgLines = document.getElementById('printLabelLines');
            if (!svgLines) return;
            svgLines.innerHTML = '';

            printLabels.forEach((label, idx) => {{
                if (!label.element || !label.cluster) return;

                const cluster = label.cluster;
                const point = printMap.latLngToContainerPoint([cluster.lat, cluster.lon]);

                // Salva offset originale se non già salvato
                if (!label.origMarkerPoint) {{
                    label.origMarkerPoint = {{ x: label.markerPoint.x, y: label.markerPoint.y }};
                    label.origPosition = {{ x: label.position.x, y: label.position.y }};
                }}

                const offsetX = label.origPosition.x - label.origMarkerPoint.x;
                const offsetY = label.origPosition.y - label.origMarkerPoint.y;

                const newX = point.x + offsetX;
                const newY = point.y + offsetY;

                label.element.style.left = newX + 'px';
                label.element.style.top = newY + 'px';

                // Ridisegna freccia per tutte le etichette
                const elWidth = label.element.offsetWidth || (label.type === 'list' ? 70 : 55);
                const elHeight = label.element.offsetHeight || (label.type === 'list' ? 50 : 26);
                drawArrowToMarker(svgLines, {{ x: newX, y: newY }}, elWidth, elHeight, point);
            }});
        }}

        function clusterMarkersForPrint(contatori) {{
            const CLUSTER_DISTANCE_PX = 40; // Distanza in pixel sotto cui raggruppare
            const clusters = [];
            const used = new Set();

            contatori.forEach((c, i) => {{
                if (used.has(i)) return;

                const cluster = {{
                    lat: c.lat,
                    lon: c.lon,
                    contatori: [c]
                }};
                used.add(i);

                // Trova contatori vicini
                contatori.forEach((other, j) => {{
                    if (i === j || used.has(j)) return;

                    const dist = Math.sqrt(
                        Math.pow(c.lat - other.lat, 2) +
                        Math.pow(c.lon - other.lon, 2)
                    ) * 111000; // Conversione approssimativa in metri

                    // Distanza massima per raggruppare contatori vicini
                    const maxDist = 5; // metri

                    if (dist < maxDist) {{
                        cluster.contatori.push(other);
                        used.add(j);
                    }}
                }});

                // Ricalcola centroide del cluster
                if (cluster.contatori.length > 1) {{
                    let latSum = 0, lonSum = 0;
                    cluster.contatori.forEach(cont => {{
                        latSum += cont.lat;
                        lonSum += cont.lon;
                    }});
                    cluster.lat = latSum / cluster.contatori.length;
                    cluster.lon = lonSum / cluster.contatori.length;
                }}

                clusters.push(cluster);
            }});

            return clusters;
        }}

        function positionLabelsForPrint(clusters) {{
            // Pulizia aggressiva
            cleanupPrintLabels();

            const mapContainer = document.getElementById('printMap');
            const mapRect = mapContainer.getBoundingClientRect();
            const svgLines = document.getElementById('printLabelLines');

            const placedBoxes = []; // Traccia tutte le etichette/liste posizionate
            const SINGLE_LABEL_HEIGHT = 26;
            const SINGLE_LABEL_WIDTH = 55;
            const LIST_ITEM_HEIGHT = 22;
            const LIST_WIDTH = 70;
            const PADDING = 10;

            // Raccoglie posizioni marker
            const markerPositions = clusters.map(cluster => {{
                const point = printMap.latLngToContainerPoint([cluster.lat, cluster.lon]);
                return {{ x: point.x - 15, y: point.y - 15, width: 30, height: 30 }};
            }});

            clusters.forEach((cluster, clusterIdx) => {{
                const point = printMap.latLngToContainerPoint([cluster.lat, cluster.lon]);
                const isCluster = cluster.contatori.length > 1;

                if (!isCluster) {{
                    // ========== CONTATORE SINGOLO ==========
                    const c = cluster.contatori[0];
                    const eneltelLast3 = c.eneltel.slice(-3);

                    // Trova posizione adiacente al marker
                    const pos = findAdjacentPosition(
                        point.x, point.y,
                        SINGLE_LABEL_WIDTH, SINGLE_LABEL_HEIGHT,
                        placedBoxes, markerPositions, mapRect
                    );

                    // Crea etichetta singola
                    const labelEl = document.createElement('div');
                    labelEl.className = 'print-eneltel-label';
                    labelEl.textContent = eneltelLast3;
                    labelEl.style.left = pos.x + 'px';
                    labelEl.style.top = pos.y + 'px';
                    labelEl.style.borderLeftColor = c.colore;
                    labelEl.style.borderLeftWidth = '4px';
                    labelEl.style.borderLeftStyle = 'solid';

                    mapContainer.appendChild(labelEl);

                    placedBoxes.push({{ x: pos.x, y: pos.y, width: SINGLE_LABEL_WIDTH, height: SINGLE_LABEL_HEIGHT }});
                    printLabels.push({{
                        element: labelEl,
                        type: 'single',
                        cluster: cluster,
                        position: pos,
                        markerPoint: {{ x: point.x, y: point.y }}
                    }});

                    // Disegna freccia dall'etichetta al marker
                    drawArrowToMarker(svgLines, pos, SINGLE_LABEL_WIDTH, SINGLE_LABEL_HEIGHT, point);

                }} else {{
                    // ========== CLUSTER MULTIPLO - LISTA ==========
                    const listHeight = cluster.contatori.length * LIST_ITEM_HEIGHT + 4;

                    // Trova posizione per la lista
                    const pos = findListPosition(
                        point.x, point.y,
                        LIST_WIDTH, listHeight,
                        placedBoxes, markerPositions, mapRect
                    );

                    // Crea contenitore lista
                    const listEl = document.createElement('div');
                    listEl.className = 'print-cluster-list';
                    listEl.style.left = pos.x + 'px';
                    listEl.style.top = pos.y + 'px';

                    // Aggiungi ogni contatore come riga
                    cluster.contatori.forEach(c => {{
                        const itemEl = document.createElement('div');
                        itemEl.className = 'print-cluster-list-item';
                        itemEl.innerHTML = `<span class="color-dot" style="background: ${{c.colore}};"></span>${{c.eneltel.slice(-3)}}`;
                        listEl.appendChild(itemEl);
                    }});

                    mapContainer.appendChild(listEl);

                    placedBoxes.push({{ x: pos.x, y: pos.y, width: LIST_WIDTH, height: listHeight }});
                    printLabels.push({{
                        element: listEl,
                        type: 'list',
                        cluster: cluster,
                        position: pos,
                        markerPoint: {{ x: point.x, y: point.y }}
                    }});

                    // Disegna freccia dalla lista al marker
                    drawArrowToMarker(svgLines, pos, LIST_WIDTH, listHeight, point);
                }}
            }});
        }}

        // Trova posizione adiacente per etichetta singola (senza linea)
        function findAdjacentPosition(markerX, markerY, width, height, placedBoxes, markerPositions, mapRect) {{
            const CLOSE_OFFSET = 8;
            const directions = [
                {{ x: 0, y: -1 }},   // sopra
                {{ x: 1, y: 0 }},    // destra
                {{ x: 0, y: 1 }},    // sotto
                {{ x: -1, y: 0 }},   // sinistra
                {{ x: 0.7, y: -0.7 }}, {{ x: 0.7, y: 0.7 }}, {{ x: -0.7, y: -0.7 }}, {{ x: -0.7, y: 0.7 }}
            ];

            for (let offset = CLOSE_OFFSET; offset <= 80; offset += 20) {{
                for (const dir of directions) {{
                    let x = markerX + dir.x * offset - width / 2;
                    let y = markerY + dir.y * offset - height / 2;

                    if (dir.x > 0.5) x = markerX + offset;
                    else if (dir.x < -0.5) x = markerX - offset - width;
                    else x = markerX - width / 2;

                    if (dir.y > 0.5) y = markerY + offset;
                    else if (dir.y < -0.5) y = markerY - offset - height;
                    else y = markerY - height / 2;

                    // Verifica limiti mappa
                    if (x < 10 || y < 10 || x + width > mapRect.width - 10 || y + height > mapRect.height - 10) continue;

                    // Verifica sovrapposizioni
                    if (!hasOverlap(x, y, width, height, placedBoxes, markerPositions)) {{
                        return {{ x, y }};
                    }}
                }}
            }}
            // Fallback
            return {{ x: markerX + 25, y: markerY - height / 2 }};
        }}

        // Trova posizione per lista cluster (con freccia)
        function findListPosition(markerX, markerY, width, height, placedBoxes, markerPositions, mapRect) {{
            const OFFSET_START = 45;
            const directions = [
                {{ x: 1, y: -0.3 }},   // destra-alto
                {{ x: 1, y: 0.3 }},    // destra-basso
                {{ x: -1, y: -0.3 }},  // sinistra-alto
                {{ x: -1, y: 0.3 }},   // sinistra-basso
                {{ x: 0, y: -1 }},     // sopra
                {{ x: 0, y: 1 }},      // sotto
            ];

            for (let offset = OFFSET_START; offset <= 150; offset += 30) {{
                for (const dir of directions) {{
                    let x, y;

                    if (dir.x > 0) x = markerX + offset;
                    else if (dir.x < 0) x = markerX - offset - width;
                    else x = markerX - width / 2;

                    if (dir.y > 0) y = markerY + Math.abs(dir.y) * offset;
                    else if (dir.y < 0) y = markerY - Math.abs(dir.y) * offset - height;
                    else y = markerY - height / 2;

                    // Verifica limiti
                    if (x < 10 || y < 10 || x + width > mapRect.width - 10 || y + height > mapRect.height - 10) continue;

                    // Verifica sovrapposizioni
                    if (!hasOverlap(x, y, width, height, placedBoxes, markerPositions)) {{
                        return {{ x, y }};
                    }}
                }}
            }}
            return {{ x: markerX + 50, y: markerY - height / 2 }};
        }}

        // Verifica sovrapposizioni
        function hasOverlap(x, y, width, height, placedBoxes, markerPositions) {{
            const SPACING = 6;
            const allBoxes = [...placedBoxes, ...markerPositions];
            return allBoxes.some(box => {{
                return x < box.x + box.width + SPACING &&
                       x + width + SPACING > box.x &&
                       y < box.y + box.height + SPACING &&
                       y + height + SPACING > box.y;
            }});
        }}

        // Disegna freccia dalla lista al marker
        function drawArrowToMarker(svgLines, listPos, listWidth, listHeight, markerPoint) {{
            // Trova il punto della lista più vicino al marker
            const listCenterX = listPos.x + listWidth / 2;
            const listCenterY = listPos.y + listHeight / 2;

            let startX, startY;

            // Determina da quale lato della lista parte la freccia
            if (markerPoint.x > listPos.x + listWidth) {{
                // Marker a destra
                startX = listPos.x + listWidth;
                startY = Math.max(listPos.y + 5, Math.min(listPos.y + listHeight - 5, markerPoint.y));
            }} else if (markerPoint.x < listPos.x) {{
                // Marker a sinistra
                startX = listPos.x;
                startY = Math.max(listPos.y + 5, Math.min(listPos.y + listHeight - 5, markerPoint.y));
            }} else if (markerPoint.y < listPos.y) {{
                // Marker sopra
                startX = Math.max(listPos.x + 5, Math.min(listPos.x + listWidth - 5, markerPoint.x));
                startY = listPos.y;
            }} else {{
                // Marker sotto
                startX = Math.max(listPos.x + 5, Math.min(listPos.x + listWidth - 5, markerPoint.x));
                startY = listPos.y + listHeight;
            }}

            // Linea principale
            const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
            line.setAttribute('x1', startX);
            line.setAttribute('y1', startY);
            line.setAttribute('x2', markerPoint.x);
            line.setAttribute('y2', markerPoint.y);
            line.setAttribute('class', 'print-label-arrow');
            line.setAttribute('stroke-dasharray', '4,3');
            svgLines.appendChild(line);
        }}

        // Funzione per verificare se due segmenti si intersecano
        function segmentsIntersect(x1, y1, x2, y2, x3, y3, x4, y4) {{
            // Calcola le direzioni dei segmenti
            const d1 = (x4 - x3) * (y1 - y3) - (y4 - y3) * (x1 - x3);
            const d2 = (x4 - x3) * (y2 - y3) - (y4 - y3) * (x2 - x3);
            const d3 = (x2 - x1) * (y3 - y1) - (y2 - y1) * (x3 - x1);
            const d4 = (x2 - x1) * (y4 - y1) - (y2 - y1) * (x4 - x1);

            if (((d1 > 0 && d2 < 0) || (d1 < 0 && d2 > 0)) &&
                ((d3 > 0 && d4 < 0) || (d3 < 0 && d4 > 0))) {{
                return true;
            }}
            return false;
        }}

        // Calcola la distanza punto-segmento per evitare linee troppo vicine
        function pointToSegmentDistance(px, py, x1, y1, x2, y2) {{
            const A = px - x1;
            const B = py - y1;
            const C = x2 - x1;
            const D = y2 - y1;
            const dot = A * C + B * D;
            const lenSq = C * C + D * D;
            let param = lenSq !== 0 ? dot / lenSq : -1;

            let xx, yy;
            if (param < 0) {{
                xx = x1; yy = y1;
            }} else if (param > 1) {{
                xx = x2; yy = y2;
            }} else {{
                xx = x1 + param * C;
                yy = y1 + param * D;
            }}

            const dx = px - xx;
            const dy = py - yy;
            return Math.sqrt(dx * dx + dy * dy);
        }}

        function findBestLabelPosition(markerX, markerY, labelWidth, labelHeight, existingLabels, markerPositions, indexInCluster, totalInCluster, mapRect, existingLines) {{
            const PADDING = 10;
            const LABEL_SPACING = 8;
            const LINE_MIN_DISTANCE = 15; // Distanza minima tra linee per evitare intreccio visivo

            existingLines = existingLines || [];

            // ========== CASO 1: CONTATORE SINGOLO ==========
            // Per contatori singoli, prova PRIMA posizioni ADIACENTI senza linea
            if (totalInCluster === 1) {{
                const CLOSE_OFFSET = 22; // Molto vicino al marker

                // Direzioni cardinali + diagonali per contatori singoli (ordine ottimizzato)
                const singleDirections = [
                    {{ x: 0, y: -1, name: 'sopra' }},      // sopra (priorità alta)
                    {{ x: 1, y: 0, name: 'destra' }},      // destra
                    {{ x: 0, y: 1, name: 'sotto' }},       // sotto
                    {{ x: -1, y: 0, name: 'sinistra' }},   // sinistra
                    {{ x: 0.7, y: -0.7, name: 'diag-alto-dx' }},  // diagonale alto-destra
                    {{ x: 0.7, y: 0.7, name: 'diag-basso-dx' }},  // diagonale basso-destra
                    {{ x: -0.7, y: -0.7, name: 'diag-alto-sx' }}, // diagonale alto-sinistra
                    {{ x: -0.7, y: 0.7, name: 'diag-basso-sx' }}  // diagonale basso-sinistra
                ];

                // Prova posizioni vicine
                for (const dir of singleDirections) {{
                    let x = markerX + dir.x * CLOSE_OFFSET;
                    let y = markerY + dir.y * CLOSE_OFFSET;

                    // Centra l'etichetta rispetto alla direzione
                    if (dir.x < 0) x -= labelWidth;
                    else if (dir.x === 0) x -= labelWidth / 2;
                    if (dir.y < 0) y -= labelHeight;
                    else if (dir.y === 0) y -= labelHeight / 2;

                    // Verifica limiti mappa
                    if (x < PADDING || y < PADDING ||
                        x + labelWidth > mapRect.width - PADDING ||
                        y + labelHeight > mapRect.height - PADDING) {{
                        continue;
                    }}

                    // Verifica sovrapposizioni con etichette
                    const overlapsLabels = existingLabels.some(pos => {{
                        const hOverlap = x < pos.x + pos.width + LABEL_SPACING && x + labelWidth + LABEL_SPACING > pos.x;
                        const vOverlap = y < pos.y + pos.height + LABEL_SPACING && y + labelHeight + LABEL_SPACING > pos.y;
                        return hOverlap && vOverlap;
                    }});

                    // Verifica sovrapposizioni con marker
                    const overlapsMarkers = markerPositions.some(m => {{
                        const hOverlap = x < m.x + m.width + 3 && x + labelWidth + 3 > m.x;
                        const vOverlap = y < m.y + m.height + 3 && y + labelHeight + 3 > m.y;
                        return hOverlap && vOverlap;
                    }});

                    if (!overlapsLabels && !overlapsMarkers) {{
                        return {{
                            x: x,
                            y: y,
                            width: labelWidth,
                            height: labelHeight,
                            noLine: true, // Flag: non disegnare linea
                            direction: dir.name
                        }};
                    }}
                }}

                // Se le posizioni vicine sono tutte occupate, usa offset progressivo
                for (let offsetMult = 1; offsetMult <= 3; offsetMult++) {{
                    const offset = CLOSE_OFFSET + offsetMult * 25;
                    for (const dir of singleDirections) {{
                        let x = markerX + dir.x * offset;
                        let y = markerY + dir.y * offset;

                        if (dir.x < 0) x -= labelWidth;
                        else if (dir.x === 0) x -= labelWidth / 2;
                        if (dir.y < 0) y -= labelHeight;
                        else if (dir.y === 0) y -= labelHeight / 2;

                        if (x < PADDING || y < PADDING ||
                            x + labelWidth > mapRect.width - PADDING ||
                            y + labelHeight > mapRect.height - PADDING) {{
                            continue;
                        }}

                        const overlapsLabels = existingLabels.some(pos => {{
                            const hOverlap = x < pos.x + pos.width + LABEL_SPACING && x + labelWidth + LABEL_SPACING > pos.x;
                            const vOverlap = y < pos.y + pos.height + LABEL_SPACING && y + labelHeight + LABEL_SPACING > pos.y;
                            return hOverlap && vOverlap;
                        }});

                        const overlapsMarkers = markerPositions.some(m => {{
                            const hOverlap = x < m.x + m.width + 3 && x + labelWidth + 3 > m.x;
                            const vOverlap = y < m.y + m.height + 3 && y + labelHeight + 3 > m.y;
                            return hOverlap && vOverlap;
                        }});

                        // Verifica che la linea non incroci altre linee
                        const labelCenterX = x + labelWidth / 2;
                        const labelCenterY = y + labelHeight / 2;

                        const crossesLine = existingLines.some(line => {{
                            return segmentsIntersect(markerX, markerY, labelCenterX, labelCenterY,
                                                     line.x1, line.y1, line.x2, line.y2);
                        }});

                        if (!overlapsLabels && !overlapsMarkers && !crossesLine) {{
                            return {{
                                x: x,
                                y: y,
                                width: labelWidth,
                                height: labelHeight,
                                noLine: offset <= 35, // Linea corta non necessaria
                                direction: dir.name
                            }};
                        }}
                    }}
                }}
            }}

            // ========== CASO 2: CLUSTER CON PIU' CONTATORI ==========
            const OFFSET_BASE = 50;
            const OFFSET_INCREMENT = 35;

            // Direzioni distribuite uniformemente per cluster
            const clusterDirections = [
                {{ x: 0, y: -1 }},       // alto
                {{ x: 0.71, y: -0.71 }}, // alto-destra 45°
                {{ x: 1, y: 0 }},        // destra
                {{ x: 0.71, y: 0.71 }},  // basso-destra 45°
                {{ x: 0, y: 1 }},        // basso
                {{ x: -0.71, y: 0.71 }}, // basso-sinistra 45°
                {{ x: -1, y: 0 }},       // sinistra
                {{ x: -0.71, y: -0.71 }} // alto-sinistra 45°
            ];

            // Calcola la direzione di partenza basata sull'indice nel cluster
            // Distribuisce le etichette uniformemente attorno al marker
            const angleStep = (2 * Math.PI) / totalInCluster;
            const startAngle = indexInCluster * angleStep - Math.PI / 2; // Parte da sopra

            // Ordina le direzioni in base alla vicinanza all'angolo ideale
            const sortedDirs = [...clusterDirections].sort((a, b) => {{
                const angleA = Math.atan2(a.y, a.x);
                const angleB = Math.atan2(b.y, b.x);
                const diffA = Math.abs(angleA - startAngle);
                const diffB = Math.abs(angleB - startAngle);
                return diffA - diffB;
            }});

            for (let offsetMultiplier = 1; offsetMultiplier <= 6; offsetMultiplier++) {{
                const offset = OFFSET_BASE + (offsetMultiplier - 1) * OFFSET_INCREMENT;

                for (const dir of sortedDirs) {{
                    let x = markerX + dir.x * offset;
                    let y = markerY + dir.y * offset;

                    // Centra l'etichetta rispetto alla direzione
                    if (dir.x < 0) x -= labelWidth;
                    else if (dir.x === 0) x -= labelWidth / 2;
                    if (dir.y < 0) y -= labelHeight;
                    else if (dir.y === 0) y -= labelHeight / 2;

                    // Verifica limiti mappa
                    if (x < PADDING) x = PADDING;
                    if (y < PADDING) y = PADDING;
                    if (x + labelWidth > mapRect.width - PADDING) x = mapRect.width - labelWidth - PADDING;
                    if (y + labelHeight > mapRect.height - PADDING) y = mapRect.height - labelHeight - PADDING;

                    // Verifica sovrapposizioni con etichette esistenti
                    const overlapsLabels = existingLabels.some(pos => {{
                        const hOverlap = x < pos.x + pos.width + LABEL_SPACING && x + labelWidth + LABEL_SPACING > pos.x;
                        const vOverlap = y < pos.y + pos.height + LABEL_SPACING && y + labelHeight + LABEL_SPACING > pos.y;
                        return hOverlap && vOverlap;
                    }});

                    // Verifica sovrapposizioni con marker
                    const overlapsMarkers = markerPositions.some(m => {{
                        const hOverlap = x < m.x + m.width + 5 && x + labelWidth + 5 > m.x;
                        const vOverlap = y < m.y + m.height + 5 && y + labelHeight + 5 > m.y;
                        return hOverlap && vOverlap;
                    }});

                    // IMPORTANTE: Verifica che la nuova linea non incroci linee esistenti
                    const lineEndX = x + (x > markerX ? 0 : labelWidth);
                    const lineEndY = y + labelHeight / 2;

                    let crossesExistingLine = false;
                    let tooCloseToLine = false;

                    for (const line of existingLines) {{
                        // Verifica intersezione
                        if (segmentsIntersect(markerX, markerY, lineEndX, lineEndY,
                                             line.x1, line.y1, line.x2, line.y2)) {{
                            crossesExistingLine = true;
                            break;
                        }}

                        // Verifica che le linee non siano troppo vicine (evita quasi-intersezioni)
                        const midX = (markerX + lineEndX) / 2;
                        const midY = (markerY + lineEndY) / 2;
                        const dist = pointToSegmentDistance(midX, midY, line.x1, line.y1, line.x2, line.y2);
                        if (dist < LINE_MIN_DISTANCE) {{
                            tooCloseToLine = true;
                            break;
                        }}
                    }}

                    if (!overlapsLabels && !overlapsMarkers && !crossesExistingLine && !tooCloseToLine) {{
                        return {{
                            x: x,
                            y: y,
                            width: labelWidth,
                            height: labelHeight,
                            noLine: false,
                            lineEnd: {{ x: lineEndX, y: lineEndY }}
                        }};
                    }}
                }}
            }}

            // Fallback: cerca qualsiasi posizione libera con linea non incrociante
            for (let angle = 0; angle < 360; angle += 15) {{
                for (let dist = 60; dist <= 200; dist += 30) {{
                    const rad = angle * Math.PI / 180;
                    let x = markerX + Math.cos(rad) * dist - labelWidth / 2;
                    let y = markerY + Math.sin(rad) * dist - labelHeight / 2;

                    if (x < PADDING) x = PADDING;
                    if (y < PADDING) y = PADDING;
                    if (x + labelWidth > mapRect.width - PADDING) continue;
                    if (y + labelHeight > mapRect.height - PADDING) continue;

                    const overlapsLabels = existingLabels.some(pos => {{
                        const hOverlap = x < pos.x + pos.width + LABEL_SPACING && x + labelWidth + LABEL_SPACING > pos.x;
                        const vOverlap = y < pos.y + pos.height + LABEL_SPACING && y + labelHeight + LABEL_SPACING > pos.y;
                        return hOverlap && vOverlap;
                    }});

                    const overlapsMarkers = markerPositions.some(m => {{
                        const hOverlap = x < m.x + m.width + 5 && x + labelWidth + 5 > m.x;
                        const vOverlap = y < m.y + m.height + 5 && y + labelHeight + 5 > m.y;
                        return hOverlap && vOverlap;
                    }});

                    const lineEndX = x + (x > markerX ? 0 : labelWidth);
                    const lineEndY = y + labelHeight / 2;

                    const crossesLine = existingLines.some(line =>
                        segmentsIntersect(markerX, markerY, lineEndX, lineEndY,
                                         line.x1, line.y1, line.x2, line.y2)
                    );

                    if (!overlapsLabels && !overlapsMarkers && !crossesLine) {{
                        return {{
                            x: x,
                            y: y,
                            width: labelWidth,
                            height: labelHeight,
                            noLine: false,
                            lineEnd: {{ x: lineEndX, y: lineEndY }}
                        }};
                    }}
                }}
            }}

            // Fallback finale: posiziona dove possibile
            return {{
                x: markerX + 60 + (indexInCluster * 40),
                y: markerY + 25 + (indexInCluster * (labelHeight + 8)),
                width: labelWidth,
                height: labelHeight,
                noLine: false
            }};
        }}

        async function executePrint() {{
            const printBtn = document.querySelector('.btn-print-confirm');
            const originalBtnText = printBtn.textContent;
            printBtn.textContent = '⏳ Generando...';
            printBtn.disabled = true;

            try {{
                // Nascondi lo slider prima dello screenshot
                const zoomSlider = document.getElementById('printZoomSliderContainer');
                if (zoomSlider) zoomSlider.style.display = 'none';

                // Cattura la mappa come immagine
                const mapContainer = document.querySelector('.print-preview-map');

                const canvas = await html2canvas(mapContainer, {{
                    useCORS: true,
                    allowTaint: true,
                    scale: 2, // Alta risoluzione per stampa
                    logging: false,
                    backgroundColor: '#ffffff'
                }});

                // Rimostra lo slider dopo lo screenshot
                if (zoomSlider) zoomSlider.style.display = 'flex';

                // Crea una nuova finestra per la stampa
                const printWindow = window.open('', '_blank');
                printWindow.document.write(`
                    <!DOCTYPE html>
                    <html>
                    <head>
                        <title>Stampa Mappa - ${{currentZona}} - ${{currentData}}</title>
                        <style>
                            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
                            body {{
                                display: flex;
                                justify-content: center;
                                align-items: center;
                                min-height: 100vh;
                                background: white;
                            }}
                            img {{
                                max-width: 100%;
                                max-height: 100vh;
                                object-fit: contain;
                            }}
                            @media print {{
                                body {{
                                    margin: 0;
                                    padding: 0;
                                }}
                                img {{
                                    width: 100%;
                                    height: auto;
                                    max-height: none;
                                }}
                            }}
                        </style>
                    </head>
                    <body>
                        <img src="${{canvas.toDataURL('image/png')}}" />
                    </body>
                    </html>
                `);
                printWindow.document.close();

                // Attendi che l'immagine sia caricata, poi stampa
                printWindow.onload = () => {{
                    setTimeout(() => {{
                        printWindow.print();
                    }}, 250);
                }};

            }} catch (error) {{
                console.error('Errore durante la generazione dello screenshot:', error);
                alert('Errore durante la generazione. Riprova.');
                // Rimostra lo slider in caso di errore
                const zoomSlider = document.getElementById('printZoomSliderContainer');
                if (zoomSlider) zoomSlider.style.display = 'flex';
            }} finally {{
                printBtn.textContent = originalBtnText;
                printBtn.disabled = false;
            }}
        }}

        // Mostra/nascondi pulsante stampa in base alla vista
        function updatePrintButtonVisibility() {{
            const btn = document.getElementById('printButton');
            const btnTappe = document.getElementById('printTappeButton');
            if (currentView === 'operatore' || currentView === 'multiOperatori') {{
                btn.classList.remove('hidden');
                btnTappe.classList.remove('hidden');
            }} else {{
                btn.classList.add('hidden');
                btnTappe.classList.add('hidden');
            }}
        }}

        // Override funzioni che cambiano vista per aggiornare visibilità pulsante
        const originalApriDettaglio = apriDettaglio;
        apriDettaglio = function(...args) {{
            originalApriDettaglio.apply(this, args);
            updatePrintButtonVisibility();
        }};

        const originalApriDettaglioComune = apriDettaglioComune;
        apriDettaglioComune = function(...args) {{
            originalApriDettaglioComune.apply(this, args);
            updatePrintButtonVisibility();
        }};

        const originalMostraVistaZone = mostraVistaZone;
        mostraVistaZone = function(...args) {{
            originalMostraVistaZone.apply(this, args);
            updatePrintButtonVisibility();
        }};

        const originalMostraVistaComuni = mostraVistaComuni;
        mostraVistaComuni = function(...args) {{
            originalMostraVistaComuni.apply(this, args);
            updatePrintButtonVisibility();
        }};

        const originalTornaAlleZone = tornaAlleZone;
        tornaAlleZone = function(...args) {{
            originalTornaAlleZone.apply(this, args);
            updatePrintButtonVisibility();
        }};

        const originalMostraPercorsoCompletoOperatore = mostraPercorsoCompletoOperatore;
        mostraPercorsoCompletoOperatore = function(...args) {{
            originalMostraPercorsoCompletoOperatore.apply(this, args);
            updatePrintButtonVisibility();
        }};

        const originalMostraPercorsoMultiOperatori = mostraPercorsoMultiOperatori;
        mostraPercorsoMultiOperatori = function(...args) {{
            originalMostraPercorsoMultiOperatori.apply(this, args);
            updatePrintButtonVisibility();
        }};

        // ============================================================
        // IMPOSTAZIONI - Pannello configurazione interattiva
        // ============================================================
        
        let settingsData = null;  // Cache dei dati settings caricati
        
        function apriImpostazioni() {{
            document.getElementById('settingsModal').classList.add('visible');
            // Mostra spinner di caricamento
            const overlay = document.getElementById('settingsLoadingOverlay');
            if (overlay) overlay.classList.remove('hidden');
            caricaImpostazioni();
        }}
        
        function chiudiImpostazioni() {{
            document.getElementById('settingsModal').classList.remove('visible');
            document.getElementById('settingsStatus').className = 'settings-status';
            document.getElementById('settingsStatus').textContent = '';
        }}

        async function effettuaLogout() {{
            if (!confirm('Vuoi disconnetterti?')) return;
            try {{
                await fetch('/api/logout', {{ method: 'POST' }});
            }} catch(e) {{}}
            window.location.href = '/login';
        }}
        
        function switchSettingsTab(tabName) {{
            // Aggiorna tabs
            document.querySelectorAll('.settings-tab').forEach(t => t.classList.remove('active'));
            document.querySelector(`.settings-tab[data-tab="${{tabName}}"]`).classList.add('active');
            // Aggiorna sezioni
            document.querySelectorAll('.settings-section').forEach(s => s.classList.remove('active'));
            document.getElementById(`settingsTab_${{tabName}}`).classList.add('active');
            // Carica colonne on-demand quando si apre il tab
            if (tabName === 'colonne') {{
                caricaColonneFilone();
            }}
            // Carica utenti on-demand quando si apre il tab
            if (tabName === 'utenti') {{
                caricaUtentiAdmin();
            }}
        }}
        
        async function caricaImpostazioni() {{
            try {{
                const resp = await fetch('/api/settings');
                if (!resp.ok) throw new Error('Errore caricamento');
                settingsData = await resp.json();
                popolaFormImpostazioni(settingsData);
                // Nascondi spinner
                const overlay = document.getElementById('settingsLoadingOverlay');
                if (overlay) overlay.classList.add('hidden');
            }} catch (e) {{
                console.error('Errore caricamento impostazioni:', e);
                // Nascondi spinner anche in caso di errore
                const overlay = document.getElementById('settingsLoadingOverlay');
                if (overlay) overlay.classList.add('hidden');
                mostraSettingsStatus('Errore nel caricamento delle impostazioni', 'error');
            }}
        }}
        
        function popolaFormImpostazioni(s) {{
            // Operatori
            const tbody = document.getElementById('settingsOperatorsBody');
            tbody.innerHTML = '';
            
            // Costruisci reverse map: matricola -> nome display
            const reverseDict = {{}};
            if (s.operatori_dict) {{
                Object.entries(s.operatori_dict).forEach(([nome, matricola]) => {{
                    reverseDict[matricola] = nome;
                }});
            }}
            
            (s.operatori_vip || []).forEach(matricola => {{
                const colore = (s.colori_operatori || {{}})[matricola] || '#3498db';
                const nome = reverseDict[matricola] || '';
                aggiungiRigaOperatore(matricola, nome, colore);
            }});
            
            // Deposito
            if (s.deposito) {{
                document.getElementById('settings_deposito_nome').value = s.deposito.Nome || '';
                document.getElementById('settings_deposito_lat').value = s.deposito.Lat || '';
                document.getElementById('settings_deposito_lon').value = s.deposito.Lon || '';
            }}
            
            // Percorso
            document.getElementById('settings_velocita').value = s.velocita_media_kmh || 35;
            document.getElementById('settings_tempo_intervento').value = s.tempo_intervento_min || 20;
            document.getElementById('settings_raggio_microzona').value = s.raggio_microzona_km || 0.5;
            document.getElementById('settings_ottimizza_tempo').checked = s.ottimizza_per_tempo !== false;
            
            // Ora inizio: converti da minuti a formato HH:MM
            const oraMin = s.ora_inizio_giornata || 480;
            const ore = Math.floor(oraMin / 60);
            const minuti = oraMin % 60;
            document.getElementById('settings_ora_inizio').value = `${{ore}}:${{String(minuti).padStart(2, '0')}}`;
            
            // Ora fine: converti da minuti a formato HH:MM
            const oraFineMin = s.ora_fine_giornata || 1020;
            const oreFine = Math.floor(oraFineMin / 60);
            const minutiFine = oraFineMin % 60;
            document.getElementById('settings_ora_fine').value = `${{oreFine}}:${{String(minutiFine).padStart(2, '0')}}`;
            
            // Date
            document.getElementById('settings_data_inizio').value = s.data_inizio || '';
            const isIllimitata = !s.data_fine;
            document.getElementById('settings_data_fine_illimitata').checked = isIllimitata;
            document.getElementById('settings_data_fine').value = s.data_fine || '';
            document.getElementById('settings_data_fine').disabled = isIllimitata;
            document.getElementById('settings_data_fine').style.opacity = isIllimitata ? '0.5' : '1';
            
            // Sync
            document.getElementById('settings_intervallo_sync').value = s.intervallo_sync_secondi || 180;
            
            // Mappatura colonne
            mappaturaColonneDefault = s.mappatura_colonne_default || {{}};
            mappaturaColonneCustom = s.mappatura_colonne || {{}};
            colonneFilone = s.colonne_filone || [];
        }}
        
        function toggleDataFineIllimitata(checked) {{
            const input = document.getElementById('settings_data_fine');
            input.disabled = checked;
            input.style.opacity = checked ? '0.5' : '1';
            if (checked) input.value = '';
        }}
        
        function aggiungiRigaOperatore(matricola, nome, colore) {{
            const tbody = document.getElementById('settingsOperatorsBody');
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td><input type="text" value="${{matricola || ''}}" placeholder="Es: AE101392" class="op-matricola"></td>
                <td><input type="text" value="${{nome || ''}}" placeholder="Es: Nome Operatore" class="op-nome"></td>
                <td><input type="color" value="${{colore || '#3498db'}}" class="op-colore"></td>
                <td><button class="settings-btn-remove" onclick="this.closest('tr').remove()" title="Rimuovi">\u2716</button></td>
            `;
            tbody.appendChild(tr);
        }}
        
        function aggiungiOperatore() {{
            // Genera un colore casuale piacevole
            const colori = ['#e74c3c', '#3498db', '#2ecc71', '#f1c40f', '#9b59b6', '#e67e22', '#1abc9c', '#34495e'];
            const usati = new Set();
            document.querySelectorAll('#settingsOperatorsBody .op-colore').forEach(el => usati.add(el.value));
            const disponibile = colori.find(c => !usati.has(c)) || colori[Math.floor(Math.random() * colori.length)];
            aggiungiRigaOperatore('', '', disponibile);
        }}
        
        function raccogliDatiImpostazioni() {{
            const operatori_vip = [];
            const colori_operatori = {{}};
            const operatori_dict = {{}};
            
            document.querySelectorAll('#settingsOperatorsBody tr').forEach(tr => {{
                const matricola = tr.querySelector('.op-matricola').value.trim().toUpperCase();
                const nome = tr.querySelector('.op-nome').value.trim();
                const colore = tr.querySelector('.op-colore').value;
                
                if (matricola) {{
                    operatori_vip.push(matricola);
                    colori_operatori[matricola] = colore;
                    if (nome) {{
                        operatori_dict[nome] = matricola;
                    }}
                }}
            }});
            
            // Parsing ora inizio
            let oraInizio = 480;  // default 8:00
            const oraStr = document.getElementById('settings_ora_inizio').value.trim();
            const oraMatch = oraStr.match(/^([0-9]{{1,2}})[:.\\.]([0-9]{{2}})$/);
            if (oraMatch) {{
                oraInizio = parseInt(oraMatch[1]) * 60 + parseInt(oraMatch[2]);
            }} else {{
                const oraMatch2 = oraStr.match(/^([0-9]{{1,2}})$/);
                if (oraMatch2) oraInizio = parseInt(oraMatch2[1]) * 60;
            }}
            
            // Parsing ora fine
            let oraFine = 1020;  // default 17:00
            const oraFineStr = document.getElementById('settings_ora_fine').value.trim();
            const oraFineMatch = oraFineStr.match(/^([0-9]{{1,2}})[:.\\.]([0-9]{{2}})$/);
            if (oraFineMatch) {{
                oraFine = parseInt(oraFineMatch[1]) * 60 + parseInt(oraFineMatch[2]);
            }} else {{
                const oraFineMatch2 = oraFineStr.match(/^([0-9]{{1,2}})$/);
                if (oraFineMatch2) oraFine = parseInt(oraFineMatch2[1]) * 60;
            }}
            
            return {{
                operatori_vip,
                colori_operatori,
                operatori_dict,
                deposito: {{
                    Nome: document.getElementById('settings_deposito_nome').value.trim(),
                    Lat: parseFloat(document.getElementById('settings_deposito_lat').value) || 0,
                    Lon: parseFloat(document.getElementById('settings_deposito_lon').value) || 0,
                }},
                ora_inizio_giornata: oraInizio,
                ora_fine_giornata: oraFine,
                velocita_media_kmh: parseInt(document.getElementById('settings_velocita').value) || 35,
                tempo_intervento_min: parseInt(document.getElementById('settings_tempo_intervento').value) || 20,
                raggio_microzona_km: parseFloat(document.getElementById('settings_raggio_microzona').value) || 0.5,
                ottimizza_per_tempo: document.getElementById('settings_ottimizza_tempo').checked,
                data_inizio: document.getElementById('settings_data_inizio').value,
                data_fine: document.getElementById('settings_data_fine_illimitata').checked ? '' : document.getElementById('settings_data_fine').value,
                intervallo_sync_secondi: parseInt(document.getElementById('settings_intervallo_sync').value) || 180,
                mappatura_colonne: raccogliMappaturaColonne(),
            }};
        }}
        
        async function salvaImpostazioni() {{
            const btn = document.getElementById('settingsBtnSave');
            btn.disabled = true;
            btn.textContent = '⏳ Salvataggio...';
            
            try {{
                const dati = raccogliDatiImpostazioni();
                
                // Validazione base
                if (dati.operatori_vip.length === 0) {{
                    mostraSettingsStatus('⚠️ Inserisci almeno un operatore', 'error');
                    btn.disabled = false;
                    btn.textContent = '💾 Salva e Sincronizza';
                    return;
                }}
                
                const dupl = dati.operatori_vip.filter((v, i, a) => a.indexOf(v) !== i);
                if (dupl.length > 0) {{
                    mostraSettingsStatus(`⚠️ Matricole duplicate: ${{dupl.join(', ')}}`, 'error');
                    btn.disabled = false;
                    btn.textContent = '💾 Salva e Sincronizza';
                    return;
                }}
                
                if (!dati.deposito.Lat || !dati.deposito.Lon) {{
                    mostraSettingsStatus('⚠️ Inserisci le coordinate del deposito', 'error');
                    btn.disabled = false;
                    btn.textContent = '💾 Salva e Sincronizza';
                    return;
                }}
                
                const resp = await fetch('/api/settings', {{
                    method: 'PUT',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify(dati)
                }});
                
                const result = await resp.json();
                
                if (resp.ok && result.success) {{
                    mostraSettingsStatus('✅ ' + result.message, 'success');
                    
                    // Aggiorna anche i dati locali per effetto immediato
                    coloriTecnici = dati.colori_operatori;
                    matricoleVip = dati.operatori_vip;
                    operatoriDict = dati.operatori_dict || {{}};
                    buildReverseOperatoriDict();
                    deposito = {{
                        nome: dati.deposito.Nome,
                        lat: dati.deposito.Lat,
                        lon: dati.deposito.Lon
                    }};
                    
                    setTimeout(() => chiudiImpostazioni(), 2500);
                }} else {{
                    mostraSettingsStatus('❌ ' + (result.error || 'Errore sconosciuto'), 'error');
                }}
            }} catch (e) {{
                mostraSettingsStatus('❌ Errore di rete: ' + e.message, 'error');
            }} finally {{
                btn.disabled = false;
                btn.textContent = '💾 Salva e Sincronizza';
            }}
        }}
        
        function mostraSettingsStatus(msg, tipo) {{
            const el = document.getElementById('settingsStatus');
            el.textContent = msg;
            el.className = `settings-status ${{tipo}}`;
        }}

        // ============================================================
        // MAPPATURA COLONNE FILONE
        // ============================================================
        let colonneFilone = null;       // Colonne attuali dal Google Sheet
        let mappaturaColonneDefault = null;  // Definizioni default da config
        let mappaturaColonneCustom = {{}};    // Mappatura personalizzata corrente

        function caricaColonneFilone() {{
            // I dati vengono già caricati con /api/settings (dentro settingsData)
            if (!settingsData) return;
            
            colonneFilone = settingsData.colonne_filone || [];
            
            if (colonneFilone.length === 0) {{
                const container = document.getElementById('colonneTableContainer');
                container.innerHTML = `<p style="color: var(--text-secondary); text-align: center; padding: 20px;">
                    ⚠️ Nessuna colonna disponibile. Il server potrebbe non aver ancora sincronizzato il filone.<br>
                    <button onclick="caricaImpostazioni().then(() => caricaColonneFilone())" style="margin-top: 10px; padding: 6px 14px; border-radius: 6px; border: 1px solid var(--border); background: var(--bg-secondary); cursor: pointer; color: var(--text-primary);">🔄 Riprova</button>
                </p>`;
                return;
            }}
            
            renderTabellaMappatura();
        }}

        function renderTabellaMappatura() {{
            if (!colonneFilone || !mappaturaColonneDefault) return;
            
            const container = document.getElementById('colonneTableContainer');
            const colonneFiloneLower = {{}};
            colonneFilone.forEach(c => {{ colonneFiloneLower[c.toLowerCase().trim()] = c; }});
            
            let html = `<table class="settings-colonne-table">
                <thead>
                    <tr>
                        <th style="width:30px;"></th>
                        <th>Campo Programma</th>
                        <th>Colonna Filone</th>
                    </tr>
                </thead>
                <tbody>`;
            
            const chiavi = Object.keys(mappaturaColonneDefault);
            chiavi.forEach(chiave => {{
                const def = mappaturaColonneDefault[chiave];
                const label = def.label || chiave;
                const desc = def.descrizione || '';
                const nomiDefault = def.nomi_default || [];
                
                // Determina il valore corrente: custom mapping > auto-detect > vuoto
                let colonnaAttuale = '';
                let stato = 'unmapped';
                
                if (mappaturaColonneCustom[chiave]) {{
                    // Mappatura personalizzata esplicita
                    const customCol = mappaturaColonneCustom[chiave];
                    if (colonneFiloneLower[customCol.toLowerCase().trim()]) {{
                        colonnaAttuale = colonneFiloneLower[customCol.toLowerCase().trim()];
                        stato = 'custom';
                    }} else {{
                        colonnaAttuale = customCol; // Colonna mappata ma non trovata
                        stato = 'unmapped';
                    }}
                }} else {{
                    // Auto-detect dai nomi default
                    for (const nome of nomiDefault) {{
                        if (colonneFiloneLower[nome.toLowerCase().trim()]) {{
                            colonnaAttuale = colonneFiloneLower[nome.toLowerCase().trim()];
                            stato = 'mapped';
                            break;
                        }}
                    }}
                }}
                
                const statusIcon = stato === 'mapped' ? '✅' : (stato === 'custom' ? '🔵' : '⚠️');
                const selectClass = stato === 'mapped' ? 'col-mapped' : (stato === 'custom' ? 'col-custom' : 'col-unmapped');
                
                html += `<tr>
                    <td class="col-status" id="colStatus_${{chiave}}">${{statusIcon}}</td>
                    <td>
                        <div class="col-label">${{label}}</div>
                        <div class="col-desc">${{desc}}</div>
                    </td>
                    <td>
                        <select class="${{selectClass}}" id="colMap_${{chiave}}" 
                                data-chiave="${{chiave}}" 
                                data-default="${{nomiDefault.join(',')}}"
                                onchange="onColonnaChanged(this)">
                            <option value="">-- Non mappata --</option>`;
                
                colonneFilone.forEach(col => {{
                    const selected = (col === colonnaAttuale) ? 'selected' : '';
                    html += `<option value="${{col}}" ${{selected}}>${{col}}</option>`;
                }});
                
                html += `</select>
                    </td>
                </tr>`;
            }});
            
            html += `</tbody></table>`;
            container.innerHTML = html;
            
            // Mostra pulsante reset se ci sono mappature custom
            const btnReset = document.getElementById('btnResetColonne');
            btnReset.style.display = Object.keys(mappaturaColonneCustom).length > 0 ? 'inline-flex' : 'none';
        }}

        function onColonnaChanged(selectEl) {{
            const chiave = selectEl.dataset.chiave;
            const nomiDefault = (selectEl.dataset.default || '').split(',').filter(n => n);
            const valore = selectEl.value;
            
            // Determina se è un valore default o custom
            const colonneFiloneLower = {{}};
            colonneFilone.forEach(c => {{ colonneFiloneLower[c.toLowerCase().trim()] = c; }});
            
            let isDefault = false;
            if (valore) {{
                for (const nome of nomiDefault) {{
                    if (colonneFiloneLower[nome.toLowerCase().trim()] === valore) {{
                        isDefault = true;
                        break;
                    }}
                }}
            }}
            
            // Aggiorna stato visivo
            const statusEl = document.getElementById(`colStatus_${{chiave}}`);
            selectEl.classList.remove('col-mapped', 'col-custom', 'col-unmapped');
            
            if (!valore) {{
                selectEl.classList.add('col-unmapped');
                statusEl.textContent = '⚠️';
            }} else if (isDefault) {{
                selectEl.classList.add('col-mapped');
                statusEl.textContent = '✅';
            }} else {{
                selectEl.classList.add('col-custom');
                statusEl.textContent = '🔵';
            }}

            // Mostra pulsante reset
            const btnReset = document.getElementById('btnResetColonne');
            const hasCustom = document.querySelectorAll('.settings-colonne-table select.col-custom').length > 0;
            btnReset.style.display = hasCustom || Object.keys(mappaturaColonneCustom).length > 0 ? 'inline-flex' : 'none';
        }}

        function raccogliMappaturaColonne() {{
            const mapping = {{}};
            document.querySelectorAll('.settings-colonne-table select').forEach(sel => {{
                const chiave = sel.dataset.chiave;
                const valore = sel.value;
                const nomiDefault = (sel.dataset.default || '').split(',').filter(n => n);
                
                if (!chiave || !valore) return;
                
                // Salva solo se è diverso dal default auto-detected
                const colonneFiloneLower = {{}};
                (colonneFilone || []).forEach(c => {{ colonneFiloneLower[c.toLowerCase().trim()] = c; }});
                
                let isDefault = false;
                for (const nome of nomiDefault) {{
                    if (colonneFiloneLower[nome.toLowerCase().trim()] === valore) {{
                        isDefault = true;
                        break;
                    }}
                }}
                
                if (!isDefault) {{
                    mapping[chiave] = valore;
                }}
            }});
            return mapping;
        }}

        function resetMappaturaColonne() {{
            mappaturaColonneCustom = {{}};
            renderTabellaMappatura();
        }}

        // ============================================================
        // GESTIONE UTENTI (admin panel)
        // ============================================================
        let adminUsersData = null;
        let adminProvinceDisponibili = [];
        let adminRuoliDisponibili = [];

        // Mostra tab Utenti solo se l'utente corrente è admin
        (async function checkAdminTab() {{
            try {{
                const r = await fetch('/api/user');
                const d = await r.json();
                if (d.authenticated && d.user && d.user.ruolo === 'admin') {{
                    const tabBtn = document.getElementById('tabUtentiBtn');
                    if (tabBtn) tabBtn.style.display = '';
                }}
            }} catch(e) {{}}
        }})();

        async function caricaUtentiAdmin() {{
            try {{
                const r = await fetch('/api/admin/users');
                if (r.status === 403) {{
                    document.getElementById('adminUsersTableContainer').innerHTML =
                        '<p style="text-align:center; color:var(--text-secondary); padding:20px;">🔒 Accesso riservato agli amministratori</p>';
                    return;
                }}
                const data = await r.json();
                if (data.success) {{
                    adminUsersData = data.users;
                    adminProvinceDisponibili = data.province_disponibili || [];
                    adminRuoliDisponibili = data.ruoli_disponibili || [];
                    renderTabellaUtenti();
                }}
            }} catch(e) {{
                mostraAdminStatus('❌ Errore caricamento utenti: ' + e.message, 'error');
            }}
        }}

        function renderTabellaUtenti() {{
            if (!adminUsersData) return;
            const container = document.getElementById('adminUsersTableContainer');

            if (adminUsersData.length === 0) {{
                container.innerHTML = '<p style="text-align:center; color:var(--text-secondary); padding:20px;">Nessun utente registrato</p>';
                return;
            }}

            let html = `<table class="admin-users-table">
                <thead><tr>
                    <th>Utente</th>
                    <th>Ruolo</th>
                    <th>Province</th>
                    <th>Stato</th>
                    <th>Ultimo login</th>
                    <th></th>
                </tr></thead><tbody>`;

            for (const u of adminUsersData) {{
                const ruoloBadge = u.ruolo === 'admin'
                    ? '<span class="admin-badge admin-badge-admin">Admin</span>'
                    : '<span class="admin-badge admin-badge-user">User</span>';

                const provinceTags = (u.province || []).map(p =>
                    `<span class="admin-province-tag">${{p}}</span>`
                ).join('');

                const statoIcon = u.active ? '🟢' : '🔴';
                const statoText = u.active ? 'Attivo' : 'Disabilitato';

                let ultimoLogin = 'Mai';
                if (u.last_login) {{
                    try {{
                        const d = new Date(u.last_login);
                        ultimoLogin = d.toLocaleDateString('it-IT') + ' ' + d.toLocaleTimeString('it-IT', {{hour:'2-digit', minute:'2-digit'}});
                    }} catch(e) {{}}
                }}

                html += `<tr>
                    <td class="user-username">${{u.username}}</td>
                    <td>${{ruoloBadge}}</td>
                    <td>${{provinceTags}}</td>
                    <td style="white-space:nowrap">${{statoIcon}} ${{statoText}}</td>
                    <td style="font-size:12px; color:var(--text-secondary)">${{ultimoLogin}}</td>
                    <td style="text-align:right;">
                        <button class="admin-actions-btn" onclick="toggleUserMenu(this, '${{u.username}}')">⋯</button>
                        <div class="admin-action-menu" id="menu_${{u.username}}">
                            <button class="admin-action-item" onclick="adminResetPassword('${{u.username}}')">🔄 Reset password</button>
                            <button class="admin-action-item" onclick="adminCambiaRuolo('${{u.username}}', '${{u.ruolo}}')">🔑 Cambia ruolo</button>
                            <button class="admin-action-item" onclick="adminCambiaProvince('${{u.username}}')">🗺️ Modifica province</button>
                            <button class="admin-action-item" onclick="adminToggleUtente('${{u.username}}', ${{u.active}})">${{u.active ? '🔒 Disattiva' : '🔓 Attiva'}}</button>
                            <button class="admin-action-item danger" onclick="adminEliminaUtente('${{u.username}}')">🗑️ Elimina</button>
                        </div>
                    </td>
                </tr>`;
            }}

            html += '</tbody></table>';
            container.innerHTML = html;
        }}

        function toggleUserMenu(btn, username) {{
            // Chiudi tutti i menu aperti
            document.querySelectorAll('.admin-action-menu.visible').forEach(m => m.classList.remove('visible'));
            const menu = document.getElementById('menu_' + username);
            // Posiziona il menu vicino al bottone
            const rect = btn.getBoundingClientRect();
            menu.style.top = (rect.bottom + 4) + 'px';
            menu.style.right = (window.innerWidth - rect.right) + 'px';
            menu.classList.toggle('visible');
            // Chiudi cliccando fuori
            setTimeout(() => {{
                const handler = (e) => {{
                    if (!menu.contains(e.target) && e.target !== btn) {{
                        menu.classList.remove('visible');
                        document.removeEventListener('click', handler);
                    }}
                }};
                document.addEventListener('click', handler);
            }}, 10);
        }}

        function mostraFormNuovoUtente() {{
            const form = document.getElementById('formNuovoUtente');
            form.style.display = 'block';
            document.getElementById('nuovoUtente_username').value = '';
            document.getElementById('nuovoUtente_password').value = '';
            document.getElementById('nuovoUtente_ruolo').value = 'user';
            // Popola checkbox province
            const cont = document.getElementById('nuovoUtente_province');
            cont.innerHTML = adminProvinceDisponibili.map(p =>
                `<label style="display:flex; align-items:center; gap:4px; font-size:13px; cursor:pointer;">
                    <input type="checkbox" value="${{p}}" checked> ${{p}}
                </label>`
            ).join('');
            document.getElementById('nuovoUtente_username').focus();
        }}

        function nascondiFormNuovoUtente() {{
            document.getElementById('formNuovoUtente').style.display = 'none';
        }}

        async function creaNuovoUtente() {{
            const username = document.getElementById('nuovoUtente_username').value.trim();
            const password = document.getElementById('nuovoUtente_password').value.trim();
            const ruolo = document.getElementById('nuovoUtente_ruolo').value;
            const provinceChecks = document.querySelectorAll('#nuovoUtente_province input[type=checkbox]:checked');
            const province = Array.from(provinceChecks).map(cb => cb.value);

            if (!username) {{
                mostraAdminStatus('❌ Username richiesto', 'error');
                return;
            }}
            if (password && password.length < 8) {{
                mostraAdminStatus('❌ Password troppo corta (min 8 caratteri)', 'error');
                return;
            }}
            if (province.length === 0) {{
                mostraAdminStatus('❌ Seleziona almeno una provincia', 'error');
                return;
            }}

            try {{
                const r = await fetch('/api/admin/users', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{ username, password: password || null, province, ruolo }})
                }});
                const data = await r.json();
                if (data.success) {{
                    mostraAdminStatus('✅ ' + data.message, 'success');
                    nascondiFormNuovoUtente();
                    if (data.password_generata) {{
                        mostraPasswordGenerata(username, data.password_generata);
                    }}
                    caricaUtentiAdmin();
                }} else {{
                    mostraAdminStatus('❌ ' + data.message, 'error');
                }}
            }} catch(e) {{
                mostraAdminStatus('❌ Errore: ' + e.message, 'error');
            }}
        }}

        function mostraPasswordGenerata(username, password) {{
            document.getElementById('passwordGenerataUser').textContent = username;
            document.getElementById('passwordGenerataValue').textContent = password;
            document.getElementById('passwordGenerataBox').style.display = 'block';
        }}

        function copiaPasswordGenerata() {{
            const pass = document.getElementById('passwordGenerataValue').textContent;
            navigator.clipboard.writeText(pass).then(() => {{
                mostraAdminStatus('📋 Password copiata negli appunti', 'success');
            }}).catch(() => {{
                // Fallback
                const range = document.createRange();
                range.selectNode(document.getElementById('passwordGenerataValue'));
                window.getSelection().removeAllRanges();
                window.getSelection().addRange(range);
            }});
        }}

        async function adminResetPassword(username) {{
            if (!confirm(`Vuoi resettare la password di "${{username}}"?\\nVerrà generata una nuova password casuale.`)) return;
            try {{
                const r = await fetch(`/api/admin/users/${{username}}/reset-password`, {{ method: 'POST' }});
                const data = await r.json();
                if (data.success) {{
                    mostraAdminStatus('✅ ' + data.message, 'success');
                    if (data.new_password) {{
                        mostraPasswordGenerata(username, data.new_password);
                    }}
                }} else {{
                    mostraAdminStatus('❌ ' + data.message, 'error');
                }}
            }} catch(e) {{
                mostraAdminStatus('❌ Errore: ' + e.message, 'error');
            }}
        }}

        async function adminCambiaRuolo(username, ruoloAttuale) {{
            const nuovoRuolo = ruoloAttuale === 'admin' ? 'user' : 'admin';
            if (!confirm(`Cambiare il ruolo di "${{username}}" da "${{ruoloAttuale}}" a "${{nuovoRuolo}}"?`)) return;
            try {{
                const r = await fetch(`/api/admin/users/${{username}}/ruolo`, {{
                    method: 'PUT',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{ ruolo: nuovoRuolo }})
                }});
                const data = await r.json();
                if (data.success) {{
                    mostraAdminStatus('✅ ' + data.message, 'success');
                    caricaUtentiAdmin();
                }} else {{
                    mostraAdminStatus('❌ ' + data.message, 'error');
                }}
            }} catch(e) {{
                mostraAdminStatus('❌ Errore: ' + e.message, 'error');
            }}
        }}

        async function adminCambiaProvince(username) {{
            // Trova utente
            const utente = adminUsersData.find(u => u.username === username);
            if (!utente) return;

            const provinceAttuali = utente.province || [];

            // Crea prompt con checkbox
            let msg = `Province per "${{username}}":\\n\\n`;
            for (const p of adminProvinceDisponibili) {{
                msg += `${{provinceAttuali.includes(p) ? '☑' : '☐'}} ${{p}}\\n`;
            }}
            msg += `\\nInserisci le province separate da virgola (es: TORINO,PAVIA):`;

            const input = prompt(msg, provinceAttuali.join(','));
            if (input === null) return;

            const nuoveProvince = input.split(',').map(s => s.trim().toUpperCase()).filter(s => s);
            if (nuoveProvince.length === 0) {{
                mostraAdminStatus('❌ Seleziona almeno una provincia', 'error');
                return;
            }}

            try {{
                const r = await fetch(`/api/admin/users/${{username}}/province`, {{
                    method: 'PUT',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{ province: nuoveProvince }})
                }});
                const data = await r.json();
                if (data.success) {{
                    mostraAdminStatus('✅ ' + data.message, 'success');
                    caricaUtentiAdmin();
                }} else {{
                    mostraAdminStatus('❌ ' + data.message, 'error');
                }}
            }} catch(e) {{
                mostraAdminStatus('❌ Errore: ' + e.message, 'error');
            }}
        }}

        async function adminToggleUtente(username, attivoAttuale) {{
            const azione = attivoAttuale ? 'disattivare' : 'attivare';
            if (!confirm(`Vuoi ${{azione}} l'utente "${{username}}"?`)) return;
            try {{
                const r = await fetch(`/api/admin/users/${{username}}/toggle`, {{ method: 'POST' }});
                const data = await r.json();
                if (data.success) {{
                    mostraAdminStatus('✅ ' + data.message, 'success');
                    caricaUtentiAdmin();
                }} else {{
                    mostraAdminStatus('❌ ' + data.message, 'error');
                }}
            }} catch(e) {{
                mostraAdminStatus('❌ Errore: ' + e.message, 'error');
            }}
        }}

        async function adminEliminaUtente(username) {{
            if (!confirm(`⚠️ ATTENZIONE\\n\\nVuoi eliminare definitivamente l'utente "${{username}}"?\\nQuesta azione è irreversibile!`)) return;
            try {{
                const r = await fetch(`/api/admin/users/${{username}}`, {{ method: 'DELETE' }});
                const data = await r.json();
                if (data.success) {{
                    mostraAdminStatus('✅ ' + data.message, 'success');
                    document.getElementById('passwordGenerataBox').style.display = 'none';
                    caricaUtentiAdmin();
                }} else {{
                    mostraAdminStatus('❌ ' + data.message, 'error');
                }}
            }} catch(e) {{
                mostraAdminStatus('❌ Errore: ' + e.message, 'error');
            }}
        }}

        function mostraAdminStatus(msg, tipo) {{
            const el = document.getElementById('adminUsersStatus');
            el.textContent = msg;
            el.className = `settings-status ${{tipo}}`;
            if (tipo === 'success') {{
                setTimeout(() => {{ el.className = 'settings-status'; }}, 5000);
            }}
        }}

        // ============================================================
        // MODALITÀ PIANIFICAZIONE
        // ============================================================
        let planningMode = false;
        let planFilterTel = 'tutti';
        // Rimosso planFilterStato: ora usiamo toggle schedulabili/pianificati
        let planSelectedEnteltels = new Set();
        let selectionMode = null; // null, 'rect', 'lasso'
        let selectionStartPoint = null;
        let selectionOverlay = null;
        let lassoPoints = [];
        let lassoSvg = null;
        let planningContatoriFiltrati = [];
        let planningLayerGroup = null;
        let _planFilterTimer = null;
        let _allPlanMarkersMap = null;  // Map<eneltel, L.circleMarker> - cache persistente
        let _allPlanContatori = [];     // Cache contatori pianificabili per filtri dipendenti
        let _planCardsRenderedSet = null;  // Set di eneltel attualmente visibili nelle card
        let _planCardsBatchTimer = null;

        function enterPlanningMode(opts) {{
            planningMode = true;
            document.getElementById('sidebar').classList.add('planning-mode');
            // Reset filters
            planFilterTel = 'tutti';
            planSelectedEnteltels.clear();
            document.getElementById('planFilterNome').value = '';
            document.getElementById('planFilterData').value = '';
            document.getElementById('planFilterOperatore').value = '';
            document.getElementById('planFilterZona').value = '';
            document.getElementById('planFilterComune').value = '';
            document.getElementById('planFilterTipologia').value = '';
            document.getElementById('planFilterPosizione').value = '';
            document.getElementById('planFilterLCL').value = '';
            // Reset toggles
            document.getElementById('planShowSchedulabili').checked = true;
            document.getElementById('planShowPianificati').checked = true;
            // Reset chip UI
            document.querySelectorAll('[data-plan-tel]').forEach(el => el.classList.toggle('active', el.dataset.planTel === 'tutti'));
            // Pre-build all planning markers (cache)
            _buildAllPlanMarkers();
            // Populate dropdowns and apply
            populatePlanningDropdowns();
            // Apply pre-filter if provided (zona or comune)
            if (opts) {{
                if (opts.zona) {{
                    document.getElementById('planFilterZona').value = opts.zona;
                    onPlanZonaChange(true);
                }}
                if (opts.comune) {{
                    document.getElementById('planFilterComune').value = opts.comune;
                }}
            }}
            applyPlanningFilters();
        }}

        function exitPlanningMode() {{
            planningMode = false;
            stopSelectionMode();
            planSelectedEnteltels.clear();
            // Rimuovi layer group e cache marker
            if (planningLayerGroup) {{
                map.removeLayer(planningLayerGroup);
                planningLayerGroup = null;
            }}
            if (_allPlanMarkersMap) {{
                _allPlanMarkersMap = null;
            }}
            _planCardsRenderedSet = null;
            // Rimuovi scroll handler lazy-load delle planning cards
            if (_planCardsScrollHandler) {{
                document.getElementById('results').removeEventListener('scroll', _planCardsScrollHandler);
                _planCardsScrollHandler = null;
            }}
            if (_planCardsBatchTimer) {{
                clearTimeout(_planCardsBatchTimer);
                _planCardsBatchTimer = null;
            }}
            _planCardsCurrentList = [];
            _planCardsRendered = 0;
            markers = [];
            document.getElementById('sidebar').classList.remove('planning-mode');
            // Restore normal view
            preserveMapView = true;
            mostraVistaZone();
        }}

        function populatePlanningDropdowns() {{
            // Get ALL non-annullati with valid coordinates (schedulabili + pianificati)
            const oggiStr = getOggiStr();
            _allPlanContatori = contatori.filter(c => {{
                if (c.annullato) return false;
                if (!isValidCoordinate(c.lat, c.lon)) return false;
                const sd = !c.data || c.data === '';
                const dp = c.data && c.data < oggiStr;
                const pian = c.data && c.data >= oggiStr;
                return sd || dp || pian;
            }});
            const allPlan = _allPlanContatori;

            // Comuni
            const comuni = new Set();
            allPlan.forEach(c => {{ if (c.comune) comuni.add(c.comune); }});
            const selComune = document.getElementById('planFilterComune');
            selComune.innerHTML = '<option value="">Tutti</option>';
            Array.from(comuni).sort().forEach(com => {{
                const o = document.createElement('option');
                o.value = com; o.textContent = com;
                selComune.appendChild(o);
            }});

            // Zone
            const selZona = document.getElementById('planFilterZona');
            selZona.innerHTML = '<option value="">Tutte le zone</option>';
            zone.forEach(z => {{
                const o = document.createElement('option');
                o.value = z.nome; o.textContent = z.nome;
                selZona.appendChild(o);
            }});

            // Tipologia (merge case-insensitive)
            const tipologieMap = new Map();
            allPlan.forEach(c => {{
                if (c.tipo_ce && c.tipo_ce.trim()) {{
                    const key = c.tipo_ce.trim().toLowerCase();
                    if (!tipologieMap.has(key)) tipologieMap.set(key, c.tipo_ce.trim());
                }}
            }});
            const selTipo = document.getElementById('planFilterTipologia');
            selTipo.innerHTML = '<option value="">Tutte</option>';
            Array.from(tipologieMap.values()).sort((a,b) => a.toLowerCase().localeCompare(b.toLowerCase())).forEach(t => {{
                const o = document.createElement('option');
                o.value = t; o.textContent = t;
                selTipo.appendChild(o);
            }});

            // Posizione
            const posizioni = new Map();
            allPlan.forEach(c => {{
                const raw = (c.note || '').trim();
                if (!raw) return;
                const norm = normalizePosizione(raw);
                if (norm && !posizioni.has(norm)) posizioni.set(norm, norm.charAt(0).toUpperCase() + norm.slice(1));
            }});
            const selPos = document.getElementById('planFilterPosizione');
            selPos.innerHTML = '<option value="">Tutte</option>';
            Array.from(posizioni.entries()).sort((a,b) => a[1].localeCompare(b[1])).forEach(([n, l]) => {{
                const o = document.createElement('option');
                o.value = n; o.textContent = l;
                selPos.appendChild(o);
            }});

            // LCL
            const lcls = new Set();
            allPlan.forEach(c => {{ if (c.lcl) lcls.add(c.lcl); }});
            const selLCL = document.getElementById('planFilterLCL');
            selLCL.innerHTML = '<option value="">Tutti</option>';
            Array.from(lcls).sort().forEach(l => {{
                const o = document.createElement('option');
                o.value = l; o.textContent = l;
                selLCL.appendChild(o);
            }});

            // Date (solo future)
            const dateSet = new Set();
            allPlan.forEach(c => {{ if (c.data && c.data !== '' && c.data >= oggiStr) dateSet.add(c.data); }});
            const selData = document.getElementById('planFilterData');
            selData.innerHTML = '<option value="">Tutte le date</option>';
            Array.from(dateSet).sort().forEach(d => {{
                const o = document.createElement('option');
                o.value = d; o.textContent = '📅 ' + formatDate(d);
                selData.appendChild(o);
            }});

            // Operatori (from pianificati)
            const opSet = new Set();
            allPlan.forEach(c => {{ if (c.tecnico && c.tecnico !== 'TUTTI') opSet.add(c.tecnico); }});
            const selOp = document.getElementById('planFilterOperatore');
            selOp.innerHTML = '<option value="">Tutti</option>';
            const oNone = document.createElement('option');
            oNone.value = '__NESSUNO__'; oNone.textContent = '📦 Senza operatore';
            selOp.appendChild(oNone);
            Array.from(opSet).sort().forEach(op => {{
                const o = document.createElement('option');
                o.value = op;
                o.textContent = (reverseOperatoriDict[op] || op) + (reverseOperatoriDict[op] ? ' (' + op + ')' : '');
                selOp.appendChild(o);
            }});

            // Popola dropdown operatori nel modal di assegnazione
            const selOpModal = document.getElementById('bulkAssignOperatore');
            selOpModal.innerHTML = '<option value="">— Nessuno (non modificare) —</option>';
            if (operatoriDict) {{
                Object.entries(operatoriDict).forEach(([nome, matricola]) => {{
                    const o = document.createElement('option');
                    o.value = matricola;
                    o.textContent = nome + ' (' + matricola + ')';
                    selOpModal.appendChild(o);
                }});
            }}
        }}

        // setPlanFilterStato rimosso: sostituito da toggle schedulabili/pianificati

        function onPlanTogglePianificati() {{
            const checked = document.getElementById('planShowPianificati').checked;
            const section = document.getElementById('planFiltersPianificatiSection');
            section.style.display = checked ? '' : 'none';
            if (!checked) {{
                // Reset filtri pianificati quando disattivati
                document.getElementById('planFilterData').value = '';
                document.getElementById('planFilterOperatore').value = '';
            }}
            applyPlanningFilters();
        }}

        function setPlanFilterTel(val) {{
            planFilterTel = val;
            document.querySelectorAll('[data-plan-tel]').forEach(el => {{
                el.classList.toggle('active', el.dataset.planTel === val);
            }});
            applyPlanningFilters();
        }}

        function onPlanLCLChange() {{
            // Aggiorna dropdown comuni in base alla LCL selezionata e alla zona
            const lclVal = document.getElementById('planFilterLCL').value;
            const zonaVal = document.getElementById('planFilterZona').value;
            const selComune = document.getElementById('planFilterComune');
            const prevComune = selComune.value;
            let source = _allPlanContatori;
            if (lclVal) source = source.filter(c => c.lcl === lclVal);
            const comuni = new Set();
            source.forEach(c => {{ if (c.comune) comuni.add(c.comune); }});
            // Interseca con i comuni della zona se selezionata
            let comuniFinali = comuni;
            if (zonaVal) {{
                const z = zone.find(zz => zz.nome === zonaVal);
                if (z) {{
                    const zonaComuniSet = new Set(z.comuni);
                    comuniFinali = new Set([...comuni].filter(c => zonaComuniSet.has(c)));
                }}
            }}
            selComune.innerHTML = '<option value="">Tutti</option>';
            Array.from(comuniFinali).sort().forEach(com => {{
                const o = document.createElement('option');
                o.value = com; o.textContent = com;
                selComune.appendChild(o);
            }});
            if (comuniFinali.has(prevComune)) {{
                selComune.value = prevComune;
            }} else {{
                selComune.value = '';
            }}
            applyPlanningFilters();
        }}

        function onPlanZonaChange(skipApply) {{
            const zonaVal = document.getElementById('planFilterZona').value;
            const selComune = document.getElementById('planFilterComune');
            const prevComune = selComune.value;
            // Determina comuni disponibili dalla zona selezionata
            let comuniDisponibili;
            if (zonaVal) {{
                const z = zone.find(zz => zz.nome === zonaVal);
                comuniDisponibili = z ? new Set(z.comuni) : new Set();
            }} else {{
                // Nessuna zona selezionata: mostra tutti i comuni (rispettando LCL se attivo)
                const lclVal = document.getElementById('planFilterLCL').value;
                const source = lclVal ? _allPlanContatori.filter(c => c.lcl === lclVal) : _allPlanContatori;
                comuniDisponibili = new Set();
                source.forEach(c => {{ if (c.comune) comuniDisponibili.add(c.comune); }});
            }}
            selComune.innerHTML = '<option value="">Tutti</option>';
            Array.from(comuniDisponibili).sort().forEach(com => {{
                const o = document.createElement('option');
                o.value = com; o.textContent = com;
                selComune.appendChild(o);
            }});
            if (comuniDisponibili.has(prevComune)) {{
                selComune.value = prevComune;
            }} else {{
                selComune.value = '';
            }}
            if (!skipApply) applyPlanningFilters();
        }}

        function getPlanningContatori() {{
            const oggiStr = getOggiStr();
            const nomeFilter = (document.getElementById('planFilterNome').value || '').toLowerCase().trim();
            const dataFilter = document.getElementById('planFilterData').value;
            const opFilter = document.getElementById('planFilterOperatore').value;
            const zonaFilter = document.getElementById('planFilterZona').value;
            const comuneFilter = document.getElementById('planFilterComune').value;
            const tipologiaFilter = document.getElementById('planFilterTipologia').value;
            const posizioneFilter = document.getElementById('planFilterPosizione').value;
            const lclFilter = document.getElementById('planFilterLCL').value;

            // Pre-calcola comuni della zona selezionata
            let zonaComuniSet = null;
            if (zonaFilter) {{
                const z = zone.find(zz => zz.nome === zonaFilter);
                if (z) {{
                    zonaComuniSet = new Set(z.comuni.map(c => c.toLowerCase()));
                }}
            }}

            return contatori.filter(c => {{
                if (c.annullato) return false;
                if (!isValidCoordinate(c.lat, c.lon)) return false;

                const isSenzaData = !c.data || c.data === '';
                const isDataPassata = c.data && c.data < oggiStr;
                const isPianificato = c.data && c.data >= oggiStr;
                const isSchedulabile = isSenzaData || isDataPassata;

                // Must be schedulabile (senza data / data passata) OR pianificato
                if (!isSchedulabile && !isPianificato) return false;

                // Toggle filter: mostra/nascondi schedulabili e pianificati
                const showSched = document.getElementById('planShowSchedulabili').checked;
                const showPian = document.getElementById('planShowPianificati').checked;
                if (isSchedulabile && !showSched) return false;
                if (isPianificato && !showPian) return false;

                // Nome
                if (nomeFilter && !(c.cliente || '').toLowerCase().includes(nomeFilter)) return false;

                // Data singola (filtra solo i pianificati, gli schedulabili passano sempre)
                if (dataFilter && isPianificato && c.data !== dataFilter) return false;

                // Operatore (filtra solo i pianificati, gli schedulabili passano sempre)
                if (opFilter && isPianificato) {{
                    if (opFilter === '__NESSUNO__' && c.tecnico && c.tecnico !== 'TUTTI') return false;
                    if (opFilter !== '__NESSUNO__' && c.tecnico !== opFilter) return false;
                }}

                // Telefono
                if (planFilterTel === 'con' && (!c.telefono || c.telefono.trim() === '')) return false;
                if (planFilterTel === 'senza' && c.telefono && c.telefono.trim() !== '') return false;

                // Zona
                if (zonaComuniSet && !zonaComuniSet.has((c.comune || '').toLowerCase())) return false;

                // Comune
                if (comuneFilter && c.comune !== comuneFilter) return false;

                // Tipologia
                if (tipologiaFilter && (c.tipo_ce || '').toLowerCase() !== tipologiaFilter.toLowerCase()) return false;

                // Posizione
                if (posizioneFilter && normalizePosizione(c.note || '') !== posizioneFilter) return false;

                // LCL
                if (lclFilter && c.lcl !== lclFilter) return false;

                return true;
            }});
        }}

        function applyPlanningFilters(keepView) {{
            if (!planningMode) return;
            // Debounce: evita chiamate ripetute ravvicinate (es. toggle rapidi)
            if (_planFilterTimer) cancelAnimationFrame(_planFilterTimer);
            _planFilterTimer = requestAnimationFrame(() => {{
                _applyPlanningFiltersNow(keepView);
            }});
        }}

        function _applyPlanningFiltersNow(keepView) {{
            planningContatoriFiltrati = getPlanningContatori();

            // Count by type for badge
            const oggiStr = getOggiStr();
            let nSched = 0, nPian = 0;
            planningContatoriFiltrati.forEach(c => {{
                if (c.data && c.data >= oggiStr) nPian++;
                else nSched++;
            }});
            document.getElementById('planningBadge').textContent = nSched + ' sched. · ' + nPian + ' pian.';
            document.getElementById('planningFilteredCount').innerHTML = 'Filtrati: <strong>' + planningContatoriFiltrati.length + '</strong>';
            updatePlanSelCount();

            renderPlanningCards(planningContatoriFiltrati);
            _updatePlanningMapFromCache(planningContatoriFiltrati, keepView);
        }}

        // Rendering card lazy: prime 80 subito, poi carica scrollando
        const PLAN_CARDS_PAGE = 80;
        let _planCardsCurrentList = [];
        let _planCardsRendered = 0;
        let _planCardsScrollHandler = null;

        function renderPlanningCards(lista) {{
            const resultsDiv = document.getElementById('results');
            const oggiStr = getOggiStr();
            
            if (_planCardsBatchTimer) {{
                clearTimeout(_planCardsBatchTimer);
                _planCardsBatchTimer = null;
            }}

            // Rimuovi scroll handler precedente
            if (_planCardsScrollHandler) {{
                resultsDiv.removeEventListener('scroll', _planCardsScrollHandler);
                _planCardsScrollHandler = null;
            }}

            _planCardsCurrentList = lista;
            _planCardsRendered = 0;

            if (lista.length === 0) {{
                resultsDiv.innerHTML = '<div style="text-align:center; padding:40px 20px; color:var(--text-muted);"><p>Nessun contatore con i filtri correnti</p></div>';
                return;
            }}

            // Render primo batch subito
            const firstBatch = lista.slice(0, PLAN_CARDS_PAGE);
            resultsDiv.innerHTML = _buildPlanCardHtml(firstBatch, oggiStr);
            _planCardsRendered = firstBatch.length;

            // Lazy load on scroll
            if (lista.length > PLAN_CARDS_PAGE) {{
                _planCardsScrollHandler = function() {{
                    if (_planCardsRendered >= _planCardsCurrentList.length) return;
                    const el = resultsDiv;
                    if (el.scrollTop + el.clientHeight >= el.scrollHeight - 200) {{
                        const nextBatch = _planCardsCurrentList.slice(_planCardsRendered, _planCardsRendered + PLAN_CARDS_PAGE);
                        if (nextBatch.length > 0) {{
                            el.insertAdjacentHTML('beforeend', _buildPlanCardHtml(nextBatch, getOggiStr()));
                            _planCardsRendered += nextBatch.length;
                        }}
                    }}
                }};
                resultsDiv.addEventListener('scroll', _planCardsScrollHandler, {{ passive: true }});
            }}
        }}

        function _buildPlanCardHtml(items, oggiStr) {{
            return items.map(c => {{
                const selected = planSelectedEnteltels.has(c.eneltel);
                const hasTel = c.telefono && c.telefono.trim() !== '';
                const isSenzaData = !c.data || c.data === '';
                const isDataPassata = c.data && c.data < oggiStr;
                const isPianificato = c.data && c.data >= oggiStr;
                const opDisplay = c.tecnico && c.tecnico !== 'TUTTI' ? (reverseOperatoriDict[c.tecnico] || c.tecnico) : '';

                let badges = '';
                if (isPianificato) {{
                    badges += '<span class="pc-badge" style="background:rgba(37,211,102,0.15);color:#25d366;">📅 ' + formatDate(c.data) + '</span>';
                    if (opDisplay) badges += '<span class="pc-badge" style="background:rgba(99,102,241,0.15);color:#818cf8;">👷 ' + opDisplay + '</span>';
                }} else {{
                    if (hasTel) badges += '<span class="pc-badge phone">📞</span>';
                    else badges += '<span class="pc-badge no-phone">No ☎️</span>';
                    if (isSenzaData) badges += '<span class="pc-badge no-date">No data</span>';
                    if (isDataPassata) badges += '<span class="pc-badge old-date">⏰ ' + formatDate(c.data) + '</span>';
                }}

                return `<div class="planning-card ${{selected ? 'selected' : ''}} ${{isPianificato ? 'pianificato' : ''}}" data-eneltel="${{c.eneltel}}" onclick="togglePlanSelect('${{c.eneltel}}')">
                    <div class="pc-header">
                        <input type="checkbox" class="pc-checkbox" ${{selected ? 'checked' : ''}} onclick="event.stopPropagation(); togglePlanSelect('${{c.eneltel}}')">
                        <span class="pc-nome">${{c.cliente || 'N/D'}}</span>
                        <div class="pc-badges">${{badges}}</div>
                    </div>
                    <div class="pc-detail">📍 ${{c.comune}} · ${{c.via || 'N/A'}} ${{c.civico || ''}} ${{c.tipo_ce ? '· ⚡ ' + c.tipo_ce : ''}}</div>
                </div>`;
            }}).join('');
        }}

        function togglePlanSelect(eneltel) {{
            if (planSelectedEnteltels.has(eneltel)) {{
                planSelectedEnteltels.delete(eneltel);
            }} else {{
                planSelectedEnteltels.add(eneltel);
            }}
            // Update card appearance
            const card = document.querySelector(`.planning-card[data-eneltel="${{eneltel}}"]`);
            if (card) {{
                card.classList.toggle('selected', planSelectedEnteltels.has(eneltel));
                const cb = card.querySelector('.pc-checkbox');
                if (cb) cb.checked = planSelectedEnteltels.has(eneltel);
            }}
            // Update marker appearance
            updateMarkerSelection(eneltel);
            updatePlanSelCount();
        }}

        function selectAllVisible() {{
            planningContatoriFiltrati.forEach(c => planSelectedEnteltels.add(c.eneltel));
            // Update all cards
            document.querySelectorAll('.planning-card').forEach(card => {{
                card.classList.add('selected');
                const cb = card.querySelector('.pc-checkbox');
                if (cb) cb.checked = true;
            }});
            // Update markers
            markers.forEach(m => _applyMarkerSelectionStyle(m));
            updatePlanSelCount();
        }}

        function deselectAll() {{
            planSelectedEnteltels.clear();
            document.querySelectorAll('.planning-card').forEach(card => {{
                card.classList.remove('selected');
                const cb = card.querySelector('.pc-checkbox');
                if (cb) cb.checked = false;
            }});
            markers.forEach(m => _applyMarkerSelectionStyle(m));
            updatePlanSelCount();
        }}

        function updatePlanSelCount() {{
            const count = planSelectedEnteltels.size;
            document.getElementById('planSelCount').innerHTML = '<strong>' + count + '</strong> sel.';
            document.getElementById('planningSelectedCount').innerHTML = 'Selezionati: <strong>' + count + '</strong>';
            document.getElementById('btnAssignBulk').disabled = count === 0;
        }}

        function updateMarkerSelection(eneltel) {{
            const marker = markers.find(m => m.contatore && m.contatore.eneltel === eneltel);
            if (!marker) return;
            _applyMarkerSelectionStyle(marker);
        }}

        function _applyMarkerSelectionStyle(m) {{
            if (!m.contatore) return;
            const selected = planSelectedEnteltels.has(m.contatore.eneltel);
            const baseColor = m.coloreTecnico || '#06b6d4';
            m.setStyle({{
                radius: selected ? 10 : 7,
                fillColor: selected ? '#25d366' : baseColor,
                color: selected ? '#fff' : 'rgba(255,255,255,0.7)',
                weight: selected ? 3 : 2
            }});
        }}

        function renderPlanningMap(lista, keepView) {{
            if (!_allPlanMarkersMap) _buildAllPlanMarkers();
            _updatePlanningMapFromCache(lista, keepView);
        }}

        // Costruisce TUTTI i marker e li aggiunge alla mappa UNA VOLTA
        function _buildAllPlanMarkers() {{
            _allPlanMarkersMap = new Map();
            const oggiStr = getOggiStr();
            const layerArr = [];

            contatori.forEach(c => {{
                if (c.annullato) return;
                if (!isValidCoordinate(c.lat, c.lon)) return;
                const isSenzaData = !c.data || c.data === '';
                const isDataPassata = c.data && c.data < oggiStr;
                const isPianificato = c.data && c.data >= oggiStr;
                if (!isSenzaData && !isDataPassata && !isPianificato) return;

                let baseColor;
                if (isPianificato) {{
                    baseColor = (c.tecnico && c.tecnico !== 'TUTTI') ? getColore(c.tecnico) : '#888';
                }} else {{
                    baseColor = '#06b6d4';
                }}
                // Crea marker nascosto (radius 0)
                const cm = L.circleMarker([c.lat, c.lon], {{
                    radius: 0,
                    fillColor: baseColor,
                    color: 'rgba(255,255,255,0.7)',
                    weight: 0,
                    opacity: 0,
                    fillOpacity: 0,
                    interactive: false
                }})
                    .bindPopup(() => createContatorePopup(c, isPianificato ? '\U0001f4c5' : '\U0001f4cb', baseColor));
                cm.contatore = c;
                cm.coloreTecnico = baseColor;
                cm._planVisible = false;
                _allPlanMarkersMap.set(c.eneltel, cm);
                layerArr.push(cm);
            }});

            // Aggiungi TUTTI alla mappa in un colpo solo (rimangono nascosti)
            if (layerArr.length > 0) {{
                planningLayerGroup = L.featureGroup(layerArr).addTo(map);
            }}
        }}

        // Aggiorna visibilit\u00e0 marker dalla cache (NESSUN add/remove, solo setStyle)
        function _updatePlanningMapFromCache(lista, keepView) {{
            let savedCenter = null;
            let savedZoom = null;
            if (keepView) {{
                savedCenter = map.getCenter();
                savedZoom = map.getZoom();
            }}

            // Pulisci zone/routes non-planning
            zoneCircles.forEach(c => map.removeLayer(c));
            zoneCircles = [];
            eneltelLabels.forEach(l => map.removeLayer(l));
            eneltelLabels = [];
            clearRoutes();

            if (!_allPlanMarkersMap) return;

            // Set di eneltel visibili per lookup veloce
            const visibleSet = new Set(lista.map(c => c.eneltel));
            const oggiStr = getOggiStr();
            markers = [];

            // Nascondi tutti quelli non filtrati, mostra quelli filtrati
            _allPlanMarkersMap.forEach((cm, eneltel) => {{
                if (visibleSet.has(eneltel)) {{
                    const c = cm.contatore;
                    const selected = planSelectedEnteltels.has(eneltel);
                    const isPianificato = c.data && c.data >= oggiStr;
                    let baseColor = cm.coloreTecnico;
                    if (isPianificato) {{
                        baseColor = (c.tecnico && c.tecnico !== 'TUTTI') ? getColore(c.tecnico) : '#888';
                        cm.coloreTecnico = baseColor;
                    }}
                    cm.setStyle({{
                        radius: selected ? 10 : 7,
                        fillColor: selected ? '#25d366' : baseColor,
                        color: selected ? '#fff' : 'rgba(255,255,255,0.7)',
                        weight: selected ? 3 : 2,
                        opacity: 1,
                        fillOpacity: 0.85
                    }});
                    cm.options.interactive = true;
                    cm._planVisible = true;
                    // In single-select mode, bind click handler instead of popup
                    if (selectionMode === 'single') {{
                        cm.unbindPopup();
                        cm.off('click', _onSingleSelectMarkerClick);
                        cm.on('click', _onSingleSelectMarkerClick);
                    }}
                    markers.push(cm);
                }} else if (cm._planVisible) {{
                    // Nascondi solo se era visibile (evita setStyle inutili)
                    cm.setStyle({{
                        radius: 0, weight: 0, opacity: 0, fillOpacity: 0
                    }});
                    cm.options.interactive = false;
                    cm._planVisible = false;
                }}
            }});

            if (savedCenter && savedZoom) {{
                map.setView(savedCenter, savedZoom);
            }} else if (markers.length > 0) {{
                const allPoints = markers.map(m => [m.contatore.lat, m.contatore.lon]);
                map.fitBounds(L.latLngBounds(allPoints), {{ padding: [50, 50] }});
            }}
        }}

        // ============================================================
        // SELEZIONE RETTANGOLARE E LIBERA
        // ============================================================
        function startSelectionMode(mode) {{
            if (selectionMode === mode) {{
                stopSelectionMode();
                return;
            }}
            stopSelectionMode();
            selectionMode = mode;
            
            // Toggle button active state
            document.getElementById('btnSelSingle').classList.toggle('active', mode === 'single');
            document.getElementById('btnSelRect').classList.toggle('active', mode === 'rect');
            document.getElementById('btnSelLasso').classList.toggle('active', mode === 'lasso');
            
            if (mode === 'single') {{
                // Single selection: click markers to toggle
                map.getContainer().classList.add('map-selection-single');
                // Disable popups, enable click-to-select on markers
                markers.forEach(m => {{
                    m.unbindPopup();
                    m.on('click', _onSingleSelectMarkerClick);
                }});
                return;
            }}
            
            // Disable map dragging during selection
            map.dragging.disable();
            map.getContainer().classList.add('map-selection-active');
            
            // Bind mouse events on map container
            const mapEl = map.getContainer();
            mapEl.addEventListener('mousedown', onSelectionMouseDown);
            mapEl.addEventListener('mousemove', onSelectionMouseMove);
            mapEl.addEventListener('mouseup', onSelectionMouseUp);
        }}

        function stopSelectionMode() {{
            const wasSingle = selectionMode === 'single';
            selectionMode = null;
            document.getElementById('btnSelSingle').classList.remove('active');
            document.getElementById('btnSelRect').classList.remove('active');
            document.getElementById('btnSelLasso').classList.remove('active');
            
            map.dragging.enable();
            map.getContainer().classList.remove('map-selection-active');
            map.getContainer().classList.remove('map-selection-single');
            
            // Restore popups if exiting single mode
            if (wasSingle) {{
                _restorePlanMarkerPopups();
            }}
            
            const mapEl = map.getContainer();
            mapEl.removeEventListener('mousedown', onSelectionMouseDown);
            mapEl.removeEventListener('mousemove', onSelectionMouseMove);
            mapEl.removeEventListener('mouseup', onSelectionMouseUp);
            
            // Remove selection overlay
            if (selectionOverlay) {{
                selectionOverlay.remove();
                selectionOverlay = null;
            }}
            if (lassoSvg) {{
                lassoSvg.remove();
                lassoSvg = null;
            }}
            lassoPoints = [];
            selectionStartPoint = null;
        }}

        function _onSingleSelectMarkerClick(e) {{
            const m = e.target;
            if (!m.contatore) return;
            const eneltel = m.contatore.eneltel;
            if (planSelectedEnteltels.has(eneltel)) {{
                planSelectedEnteltels.delete(eneltel);
            }} else {{
                planSelectedEnteltels.add(eneltel);
            }}
            _applyMarkerSelectionStyle(m);
            syncSelectionToCards();
            updatePlanSelCount();
        }}

        function _restorePlanMarkerPopups() {{
            const oggiStr = getOggiStr();
            markers.forEach(m => {{
                m.off('click', _onSingleSelectMarkerClick);
                if (m.contatore) {{
                    const c = m.contatore;
                    const isPianificato = c.data && c.data >= oggiStr;
                    const baseColor = m.coloreTecnico || '#06b6d4';
                    m.bindPopup(() => createContatorePopup(c, isPianificato ? '\U0001f4c5' : '\U0001f4cb', baseColor));
                }}
            }});
        }}

        function onSelectionMouseDown(e) {{
            if (!selectionMode) return;
            if (e.button !== 0) return; // left click only
            
            const mapRect = map.getContainer().getBoundingClientRect();
            const x = e.clientX - mapRect.left;
            const y = e.clientY - mapRect.top;
            selectionStartPoint = {{ x, y }};
            
            if (selectionMode === 'rect') {{
                // Create rectangle overlay
                selectionOverlay = document.createElement('div');
                selectionOverlay.className = 'selection-rect';
                selectionOverlay.style.left = x + 'px';
                selectionOverlay.style.top = y + 'px';
                selectionOverlay.style.width = '0';
                selectionOverlay.style.height = '0';
                map.getContainer().appendChild(selectionOverlay);
            }} else if (selectionMode === 'lasso') {{
                lassoPoints = [{{ x, y }}];
                // Create SVG for lasso
                lassoSvg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
                lassoSvg.style.position = 'absolute';
                lassoSvg.style.top = '0';
                lassoSvg.style.left = '0';
                lassoSvg.style.width = '100%';
                lassoSvg.style.height = '100%';
                lassoSvg.style.pointerEvents = 'none';
                lassoSvg.style.zIndex = '1000';
                map.getContainer().appendChild(lassoSvg);
            }}
            
            e.preventDefault();
            e.stopPropagation();
        }}

        function onSelectionMouseMove(e) {{
            if (!selectionStartPoint) return;

            const mapRect = map.getContainer().getBoundingClientRect();
            const x = e.clientX - mapRect.left;
            const y = e.clientY - mapRect.top;

            if (selectionMode === 'rect' && selectionOverlay) {{
                const left = Math.min(selectionStartPoint.x, x);
                const top = Math.min(selectionStartPoint.y, y);
                const w = Math.abs(x - selectionStartPoint.x);
                const h = Math.abs(y - selectionStartPoint.y);
                selectionOverlay.style.left = left + 'px';
                selectionOverlay.style.top = top + 'px';
                selectionOverlay.style.width = w + 'px';
                selectionOverlay.style.height = h + 'px';
            }} else if (selectionMode === 'lasso' && lassoSvg) {{
                lassoPoints.push({{ x, y }});
                // Draw polyline
                const pointsStr = lassoPoints.map(p => p.x + ',' + p.y).join(' ');
                lassoSvg.innerHTML = `<polyline points="${{pointsStr}}" fill="rgba(6,182,212,0.1)" stroke="#06b6d4" stroke-width="2" stroke-dasharray="5,5"/>`;
            }}
            
            e.preventDefault();
        }}

        function onSelectionMouseUp(e) {{
            if (!selectionStartPoint) return;

            const mapRect = map.getContainer().getBoundingClientRect();

            if (selectionMode === 'rect') {{
                const x = e.clientX - mapRect.left;
                const y = e.clientY - mapRect.top;
                const left = Math.min(selectionStartPoint.x, x);
                const top = Math.min(selectionStartPoint.y, y);
                const right = Math.max(selectionStartPoint.x, x);
                const bottom = Math.max(selectionStartPoint.y, y);

                // Select markers inside rectangle
                selectMarkersInBounds(left, top, right, bottom);

                if (selectionOverlay) {{
                    selectionOverlay.remove();
                    selectionOverlay = null;
                }}
            }} else if (selectionMode === 'lasso') {{
                // Close the polygon
                if (lassoPoints.length > 2) {{
                    selectMarkersInPolygon(lassoPoints);
                }}
                if (lassoSvg) {{
                    lassoSvg.remove();
                    lassoSvg = null;
                }}
                lassoPoints = [];
            }}

            selectionStartPoint = null;
            e.preventDefault();
        }}

        function selectMarkersInBounds(left, top, right, bottom) {{
            const mapContainer = map.getContainer();
            markers.forEach(m => {{
                if (!m.contatore) return;
                const point = map.latLngToContainerPoint(m.getLatLng());
                if (point.x >= left && point.x <= right && point.y >= top && point.y <= bottom) {{
                    planSelectedEnteltels.add(m.contatore.eneltel);
                    _applyMarkerSelectionStyle(m);
                }}
            }});
            // Update sidebar cards and counts
            syncSelectionToCards();
            updatePlanSelCount();
        }}

        function selectMarkersInPolygon(polygon) {{
            markers.forEach(m => {{
                if (!m.contatore) return;
                const point = map.latLngToContainerPoint(m.getLatLng());
                if (isPointInPolygon(point.x, point.y, polygon)) {{
                    planSelectedEnteltels.add(m.contatore.eneltel);
                    _applyMarkerSelectionStyle(m);
                }}
            }});
            syncSelectionToCards();
            updatePlanSelCount();
        }}

        function isPointInPolygon(x, y, polygon) {{
            // Ray-casting algorithm
            let inside = false;
            for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {{
                const xi = polygon[i].x, yi = polygon[i].y;
                const xj = polygon[j].x, yj = polygon[j].y;
                const intersect = ((yi > y) !== (yj > y)) && (x < (xj - xi) * (y - yi) / (yj - yi) + xi);
                if (intersect) inside = !inside;
            }}
            return inside;
        }}

        function syncSelectionToCards() {{
            document.querySelectorAll('.planning-card').forEach(card => {{
                const eneltel = card.dataset.eneltel;
                const sel = planSelectedEnteltels.has(eneltel);
                card.classList.toggle('selected', sel);
                const cb = card.querySelector('.pc-checkbox');
                if (cb) cb.checked = sel;
            }});
        }}

        // ============================================================
        // BULK ASSIGN
        // ============================================================
        function openBulkAssignModal() {{
            if (planSelectedEnteltels.size === 0) return;
            document.getElementById('bulkAssignCount').textContent = planSelectedEnteltels.size + ' contatori selezionati';
            document.getElementById('bulkAssignData').value = '';
            document.getElementById('bulkAssignOperatore').value = '';
            document.getElementById('bulkAssignOverlay').classList.add('visible');
        }}

        function closeBulkAssignModal() {{
            document.getElementById('bulkAssignOverlay').classList.remove('visible');
        }}

        async function executeBulkAssign() {{
            const dataPian = document.getElementById('bulkAssignData').value;
            const operatore = document.getElementById('bulkAssignOperatore').value;

            if (!dataPian && !operatore) {{
                alert('Seleziona almeno una data o un operatore.');
                return;
            }}

            const enteltels = Array.from(planSelectedEnteltels);
            const btn = document.getElementById('btnBulkConfirm');
            btn.disabled = true;
            btn.textContent = '⏳ Salvataggio...';

            try {{
                const response = await fetch(window.location.origin + '/api/contatori/bulk-pianifica', {{
                    method: 'PUT',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{
                        enteltels: enteltels,
                        data_pian: dataPian,
                        operatore: operatore
                    }})
                }});

                const result = await response.json();
                if (response.ok && result.success) {{
                    // Aggiorna contatori locali
                    enteltels.forEach(en => {{
                        const c = contatori.find(cc => cc.eneltel === en);
                        if (c) {{
                            if (dataPian) c.data = dataPian;
                            if (operatore) c.tecnico = operatore;
                        }}
                    }});
                    
                    closeBulkAssignModal();
                    planSelectedEnteltels.clear();
                    
                    const msg = '✅ ' + result.aggiornati + ' contatori aggiornati' + 
                        (result.non_trovati_count ? ' (' + result.non_trovati_count + ' non trovati)' : '');
                    alert(msg);
                    
                    // Scarica Excel con contatori assegnati
                    try {{
                        const excelResp = await fetch(window.location.origin + '/api/contatori/excel-assegnati', {{
                            method: 'POST',
                            headers: {{ 'Content-Type': 'application/json' }},
                            body: JSON.stringify({{
                                enteltels: enteltels,
                                data_pian: dataPian,
                                operatore: operatore
                            }})
                        }});
                        if (excelResp.ok) {{
                            const blob = await excelResp.blob();
                            const url = URL.createObjectURL(blob);
                            const a = document.createElement('a');
                            a.href = url;
                            const disposition = excelResp.headers.get('Content-Disposition');
                            const match = disposition && disposition.match(/filename="?([^"]+)"?/);
                            a.download = match ? match[1] : 'assegnazione.xlsx';
                            document.body.appendChild(a);
                            a.click();
                            a.remove();
                            URL.revokeObjectURL(url);
                        }}
                    }} catch (excelErr) {{
                        console.warn('Errore download excel assegnati:', excelErr);
                    }}
                    
                    // Refresh planning view
                    populatePlanningDropdowns();
                    applyPlanningFilters(true);
                }} else {{
                    alert('❌ Errore: ' + (result.error || 'Errore sconosciuto'));
                }}
            }} catch (err) {{
                alert('❌ Errore di connessione: ' + err.message);
            }} finally {{
                btn.disabled = false;
                btn.textContent = '✅ Assegna';
            }}
        }}

        // ============================================================
        // GESTIONALE - Funzioni editing, Excel, BOXBEAT
        // ============================================================
        {genera_js_gestionale()}

        // ============================================================
        // INIT
        // ============================================================
        popolaFiltroDate();
        updateSchedulabiliBadge();
        mostraVistaZone();
        updatePrintButtonVisibility();

        // Prova a caricare dati via fetch in background (se non file://)
        // Se riesce e i dati sono diversi, aggiorna la vista
        (async function() {{
            const oldTimestamp = dataTimestamp;
            await initializeData();
            if (dataTimestamp !== oldTimestamp) {{
                // Dati diversi caricati via fetch, aggiorna vista
                popolaFiltroDate();
                updateSchedulabiliBadge();
                refreshCurrentView();
                console.log('🔄 Vista aggiornata con dati fetch');
            }}
            // Nascondi lo spinner di caricamento pagina
            const loadingOverlay = document.getElementById('pageLoadingOverlay');
            if (loadingOverlay) {{
                loadingOverlay.classList.add('fade-out');
                setTimeout(() => loadingOverlay.remove(), 500);
            }}
        }})();
    </script>
    
    <!-- Modali Gestionale -->
    {genera_html_gestionale()}
<script src="/static/bacheca.js"></script>
</body>
</html>'''
    
    return html
