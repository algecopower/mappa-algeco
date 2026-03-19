# -*- coding: utf-8 -*-
"""
Blueprint: Contatori — gestione contatori, filone, orari lettere.
"""
import logging
import os
import re
from datetime import datetime

import gspread.utils
from flask import Blueprint, request, jsonify, session, send_file

from moduli.auth import login_required
from moduli.shared import (
    get_worksheet,
    get_header_map,
    get_headers,
    find_row_by_eneltel,
    update_row_fields,
    _imposta_appuntamento_su_filone,
    _rimuovi_appuntamento_da_filone,
    _get_username,
    _api_error,
    _trova_indice_colonna,
    _trova_indice_multi,
    _callbacks,
)
from config import (
    PROVINCIA_ATTIVA,
    CAMPI_MODIFICABILI,
    CAMPI_BOXBEAT,
    COLONNE_SIGILLI,
    MODALITA_TEST,
    MATRICOLE_VIP,
    DEPOSITO,
)

logger = logging.getLogger('mappa_algeco')

# Colonne protette nella vista Excel (non modificabili dall'utente)
COLONNE_PROTETTE_FILONE = {
    'TORINO': [
        'LCL', 'TIPO_MISURATORE', 'MOT. RICH.', 'MID', 'TIPOLOGIA ZONA',
        'POD', 'ENELTEL', 'CODICE_ANTE_SOSTITUZIONE', 'MATRICOLA_ANTE_SOSTITUZIONE',
        'MATRICOLA CE INST', 'COD.', 'DATA INSTALLAZIONE', 'ESITO'
    ],
    'PAVIA': [
        'LCL', 'INIZIO LCL', 'FINE LCL', 'NDIS', 'TIPOLOGIA CE',
        'ENELTEL', 'POD', 'CODICE MISURATORE IN OPERA', 'MATRICOLA MISURATORE IN OPERA',
        'DATA INSTALLAZIONE', 'ESITO', 'CODICE POST', 'MATRICOLA POST'
    ]
}

bp = Blueprint('contatori', __name__)


@bp.route('/api/contatore/<eneltel>')
@login_required
def get_contatore(eneltel):
    """Ottiene i dati di un contatore."""
    row = find_row_by_eneltel(eneltel)
    if row is None:
        return jsonify({'error': f'ENELTEL {eneltel} non trovato'}), 404

    ws = get_worksheet()
    row_data = ws.row_values(row)

    # Costruisci dict con headers
    _headers = get_headers()
    data = {}
    for i, h in enumerate(_headers):
        if i < len(row_data):
            data[h] = row_data[i]
        else:
            data[h] = ''

    return jsonify({
        'eneltel': eneltel,
        'row': row,
        'data': data
    })


@bp.route('/api/contatore/<eneltel>', methods=['PUT'])
@login_required
def update_contatore(eneltel):
    """Aggiorna i campi di un contatore."""
    try:
        fields = request.json
        if not fields:
            return jsonify({'error': 'Nessun campo da aggiornare'}), 400

        # Valida che i campi siano modificabili
        campi_ammessi = CAMPI_MODIFICABILI.get(PROVINCIA_ATTIVA, {})
        col_names = list(campi_ammessi.values())

        # Debug: mostra headers disponibili
        get_worksheet()  # Assicura che _header_map sia popolato
        _header_map = get_header_map()
        logger.info(f"📋 Headers disponibili: {list(_header_map.keys())}")
        logger.info(f"📝 Campi ricevuti dal client: {fields}")
        logger.info(f"📝 Colonne ammesse dalla config: {col_names}")

        # Filtra solo campi ammessi
        fields_validi = {}
        for key, value in fields.items():
            # key può essere il nome interno o il nome colonna
            if key in campi_ammessi:
                fields_validi[campi_ammessi[key]] = value
            elif key in col_names:
                fields_validi[key] = value

        logger.info(f"✅ Campi validi da scrivere: {fields_validi}")

        if not fields_validi:
            return jsonify({'error': 'Nessun campo valido da aggiornare'}), 400

        success, msg = update_row_fields(eneltel, fields_validi)

        if success:
            # Triggera sync immediato per rigenerare i JSON
            _callbacks['trigger_sync_after_edit']()
            return jsonify({'success': True, 'message': msg, 'updated': list(fields_validi.keys())})
        else:
            return jsonify({'error': msg}), 400

    except Exception as e:
        return _api_error(e, 'aggiornamento contatore')


@bp.route('/api/contatore/<eneltel>/appuntamento', methods=['POST'])
@login_required
def set_appuntamento(eneltel):
    """
    Imposta un contatore come appuntamento.
    Body: {"orario": "10:00-12:00"} oppure {"orario": "10:30"}
    """
    try:
        data = request.json
        orario = data.get('orario', '')
        data_app = data.get('data_appuntamento', '')
        note_extra = data.get('note', '')
        nota_corrente = data.get('nota_corrente')

        if not orario:
            return jsonify({'error': 'Orario richiesto'}), 400

        row = find_row_by_eneltel(eneltel)
        if row is None:
            return jsonify({'error': f'ENELTEL {eneltel} non trovato'}), 404

        result = _imposta_appuntamento_su_filone(
            row, orario, data_app=data_app, note_extra=note_extra,
            origine='contatore', eneltel_override=eneltel,
            nota_corrente=nota_corrente
        )
        return jsonify({
            'success': True,
            'message': f"Appuntamento impostato: {result['nota_app']}",
            'nota': result['nuova_nota']
        })

    except ValueError as ve:
        return jsonify({'error': str(ve)}), 400
    except Exception as e:
        return _api_error(e, 'impostazione appuntamento')


@bp.route('/api/contatore/<eneltel>/rimuovi-appuntamento', methods=['POST'])
@login_required
def remove_appuntamento(eneltel):
    """Rimuove l'appuntamento da un contatore."""
    try:
        data = request.get_json(silent=True) or {}
        nota_corrente = data.get('nota_corrente')

        row = find_row_by_eneltel(eneltel)
        if row is None:
            return jsonify({'error': f'ENELTEL {eneltel} non trovato'}), 404

        result = _rimuovi_appuntamento_da_filone(row, nota_corrente=nota_corrente)
        return jsonify({
            'success': True,
            'message': 'Appuntamento rimosso',
            'nota': result['nuova_nota']
        })

    except ValueError as ve:
        return jsonify({'error': str(ve)}), 400
    except Exception as e:
        return _api_error(e, 'rimozione appuntamento')


@bp.route('/api/filone')
@login_required
def get_filone():
    """
    Restituisce tutti i dati del FILONE per la vista Excel.
    Carica tutti i dati in una sola volta (cache lato client).
    """
    try:
        ws = get_worksheet()

        # Ottieni tutti i dati
        all_data = ws.get_all_values()
        headers = all_data[0] if all_data else []
        rows = all_data[1:] if len(all_data) > 1 else []

        # Converti in lista di dict
        data = []
        for i, row in enumerate(rows, start=2):  # +2 perché riga 1 è header
            row_dict = {'_row': i}  # Numero riga per aggiornamenti
            for j, h in enumerate(headers):
                row_dict[h] = row[j] if j < len(row) else ''
            data.append(row_dict)

        return jsonify({
            'headers': headers,
            'data': data,
            'total': len(rows),
            'protectedCols': COLONNE_PROTETTE_FILONE.get(PROVINCIA_ATTIVA, [])
        })

    except Exception as e:
        return _api_error(e, 'caricamento filone')


@bp.route('/api/filone/row/<int:row_num>', methods=['PUT'])
@login_required
def update_filone_row(row_num):
    """Aggiorna una riga del FILONE (per vista Excel)."""
    try:
        fields = request.json
        if not fields:
            return jsonify({'error': 'Nessun campo da aggiornare'}), 400

        ws = get_worksheet()
        _header_map = get_header_map()

        # Prepara batch update
        updates = []
        for col_name, value in fields.items():
            if col_name.startswith('_'):  # Salta campi interni
                continue
            if col_name in _header_map:
                updates.append({
                    'range': gspread.utils.rowcol_to_a1(row_num, _header_map[col_name]),
                    'values': [[value]]
                })

        if updates:
            ws.batch_update(updates)

        # === RILEVAMENTO APPUNTAMENTI MANUALI ===
        # Torino: "APP HH:MM" nella colonna NOTE APP / NOTE
        # Pavia: orario diretto (es. "9:00", "8/10") nella colonna "Ora pian"
        try:
            orario_trovato = None
            nota_salvata = ''

            for col_name, value in fields.items():
                val_str = str(value).strip()
                if not val_str:
                    continue

                col_upper = col_name.upper().strip()

                # --- TORINO: cerca "APP HH:MM" in NOTE APP / NOTE ---
                if col_upper in ('NOTE APP', 'NOTE', 'NOTE_APP'):
                    match_app = re.search(
                        r'APP\s+(\d{1,2}[:.]\d{2}(?:\s*[-/]\s*\d{1,2}[:.]\d{2})?)',
                        val_str, re.IGNORECASE
                    )
                    if match_app:
                        orario_trovato = match_app.group(1).replace('.', ':')
                        nota_salvata = val_str

                # --- PAVIA: orario diretto in "Ora pian" ---
                if col_upper in ('ORA PIAN', 'ORA_PIAN'):
                    from moduli.ai_detector import rileva_appuntamento_pavia, estrai_orario_pavia
                    if rileva_appuntamento_pavia(val_str):
                        orario_estratto = estrai_orario_pavia(val_str)
                        if orario_estratto:
                            orario_trovato = orario_estratto
                            nota_salvata = val_str

            if orario_trovato:
                # Estrai dati contatore dalla riga
                contatore_data = _callbacks['extract_contatore_data'](row_num)
                data_oggi = datetime.now().strftime('%Y-%m-%d')

                # Controlla se non è già registrato
                from moduli.calendario import _load_excel_data, _excel_lock
                with _excel_lock:
                    excel_data = _load_excel_data()
                registrati = excel_data.get(data_oggi, [])

                pod = contatore_data.get('pod', '').strip().upper()
                enel = contatore_data.get('eneltel', '').strip()
                gia_presente = any(
                    (pod and r.get('pod', '').strip().upper() == pod) or
                    (enel and r.get('eneltel', '').strip() == enel)
                    for r in registrati
                )

                if not gia_presente:
                    # Username dall'utente in sessione
                    username_bb = session.get('user', {}).get('username', 'sconosciuto') if isinstance(session.get('user'), dict) else session.get('user', 'sconosciuto')
                    # Data appuntamento = data pianificata del contatore, fallback a oggi
                    data_app_bb = contatore_data.get('data_pianificata', '') or data_oggi
                    _callbacks['crea_promemoria_boxbeat'](
                        data_appuntamento=data_app_bb,
                        ora_appuntamento=orario_trovato,
                        pod=contatore_data.get('pod', ''),
                        eneltel=contatore_data.get('eneltel', ''),
                        nominativo=contatore_data.get('nominativo', ''),
                        via=contatore_data.get('via', ''),
                        civico=contatore_data.get('civico', ''),
                        comune=contatore_data.get('comune', ''),
                        note=nota_salvata,
                        provincia=PROVINCIA_ATTIVA,
                        creato_da=username_bb,
                        origine='filone_manuale',
                        data_pianificata=contatore_data.get('data_pianificata', '')
                    )
                    _callbacks['registra_appuntamento_excel'](
                        data_appuntamento=data_app_bb,
                        orario=orario_trovato,
                        pod=contatore_data.get('pod', ''),
                        eneltel=contatore_data.get('eneltel', ''),
                        nominativo=contatore_data.get('nominativo', ''),
                        via=contatore_data.get('via', ''),
                        civico=contatore_data.get('civico', ''),
                        comune=contatore_data.get('comune', ''),
                        note=nota_salvata,
                        origine='filone_manuale',
                        data_pianificata=contatore_data.get('data_pianificata', ''),
                        creato_da=username_bb,
                        provincia=PROVINCIA_ATTIVA
                    )
                    logger.info(f"[Appuntamento] ✅ Appuntamento rilevato da filone: {orario_trovato} - {contatore_data.get('nominativo', 'N/A')}")
                    _callbacks['sse_notify_all']('calendario_update', {'azione': 'boxbeat_creato'})
        except Exception as e_app:
            logger.warning(f"[Appuntamento] ⚠️ Errore rilevamento APP da filone: {e_app}")

        return jsonify({'success': True, 'updated': len(updates)})

    except Exception as e:
        return _api_error(e, 'aggiornamento riga filone')


@bp.route('/api/filone/bulk', methods=['PUT'])
@login_required
def update_filone_bulk():
    """Aggiorna più righe del FILONE in un'unica chiamata batch (per vista Excel).
    
    Body JSON: { "rows": { "row_num": { "col_name": "value", ... }, ... } }
    Tutte le celle vengono scritte in un unico batch_update su Google Sheets.
    """
    try:
        data = request.json
        if not data or 'rows' not in data:
            return jsonify({'error': 'Formato non valido. Atteso: {"rows": {...}}'}), 400

        rows_dict = data['rows']
        if not rows_dict:
            return jsonify({'error': 'Nessuna riga da aggiornare'}), 400

        ws = get_worksheet()
        _header_map = get_header_map()

        # Accumula tutti gli update in un unico batch
        all_updates = []
        rows_ok = 0

        for row_num_str, fields in rows_dict.items():
            try:
                row_num = int(row_num_str)
            except (ValueError, TypeError):
                continue

            for col_name, value in fields.items():
                if col_name.startswith('_'):
                    continue
                if col_name in _header_map:
                    all_updates.append({
                        'range': gspread.utils.rowcol_to_a1(row_num, _header_map[col_name]),
                        'values': [[value]]
                    })
            rows_ok += 1

        if not all_updates:
            return jsonify({'error': 'Nessun campo valido da aggiornare'}), 400

        # Esecuzione in chunk: Google Sheets ha un limite di ~60.000 celle per batch
        CHUNK_SIZE = 500  # celle per batch
        for i in range(0, len(all_updates), CHUNK_SIZE):
            chunk = all_updates[i:i + CHUNK_SIZE]
            ws.batch_update(chunk)

        # Rilevamento appuntamenti (per tutte le righe modificate)
        try:
            for row_num_str, fields in rows_dict.items():
                row_num = int(row_num_str)
                orario_trovato = None
                nota_salvata = ''

                for col_name, value in fields.items():
                    val_str = str(value).strip()
                    if not val_str:
                        continue
                    col_upper = col_name.upper().strip()

                    if col_upper in ('NOTE APP', 'NOTE', 'NOTE_APP'):
                        match_app = re.search(
                            r'APP\s+(\d{1,2}[:.]\d{2}(?:\s*[-/]\s*\d{1,2}[:.]\d{2})?)',
                            val_str, re.IGNORECASE
                        )
                        if match_app:
                            orario_trovato = match_app.group(1).replace('.', ':')
                            nota_salvata = val_str

                    if col_upper in ('ORA PIAN', 'ORA_PIAN'):
                        from moduli.ai_detector import rileva_appuntamento_pavia, estrai_orario_pavia
                        if rileva_appuntamento_pavia(val_str):
                            orario_estratto = estrai_orario_pavia(val_str)
                            if orario_estratto:
                                orario_trovato = orario_estratto
                                nota_salvata = val_str

                if orario_trovato:
                    contatore_data = _callbacks['extract_contatore_data'](row_num)
                    data_oggi = datetime.now().strftime('%Y-%m-%d')
                    from moduli.calendario import _load_excel_data, _excel_lock
                    with _excel_lock:
                        excel_data = _load_excel_data()
                    registrati = excel_data.get(data_oggi, [])
                    pod = contatore_data.get('pod', '').strip().upper()
                    enel = contatore_data.get('eneltel', '').strip()
                    gia_presente = any(
                        (pod and r.get('pod', '').strip().upper() == pod) or
                        (enel and r.get('eneltel', '').strip() == enel)
                        for r in registrati
                    )
                    if not gia_presente:
                        username_bb = session.get('user', {}).get('username', 'sconosciuto') if isinstance(session.get('user'), dict) else session.get('user', 'sconosciuto')
                        data_app_bb = contatore_data.get('data_pianificata', '') or data_oggi
                        _callbacks['crea_promemoria_boxbeat'](
                            data_appuntamento=data_app_bb,
                            ora_appuntamento=orario_trovato,
                            pod=contatore_data.get('pod', ''),
                            eneltel=contatore_data.get('eneltel', ''),
                            nominativo=contatore_data.get('nominativo', ''),
                            via=contatore_data.get('via', ''),
                            civico=contatore_data.get('civico', ''),
                            comune=contatore_data.get('comune', ''),
                            note=nota_salvata,
                            provincia=PROVINCIA_ATTIVA,
                            creato_da=username_bb,
                            origine='filone_manuale',
                            data_pianificata=contatore_data.get('data_pianificata', '')
                        )
                        _callbacks['registra_appuntamento_excel'](
                            data_appuntamento=data_app_bb,
                            orario=orario_trovato,
                            pod=contatore_data.get('pod', ''),
                            eneltel=contatore_data.get('eneltel', ''),
                            nominativo=contatore_data.get('nominativo', ''),
                            via=contatore_data.get('via', ''),
                            civico=contatore_data.get('civico', ''),
                            comune=contatore_data.get('comune', ''),
                            note=nota_salvata,
                            origine='filone_manuale',
                            data_pianificata=contatore_data.get('data_pianificata', ''),
                            creato_da=username_bb,
                            provincia=PROVINCIA_ATTIVA
                        )
                        _callbacks['sse_notify_all']('calendario_update', {'azione': 'boxbeat_creato'})
        except Exception as e_app:
            logger.warning(f"[Appuntamento] ⚠️ Errore rilevamento APP da filone bulk: {e_app}")

        logger.info(f"[Filone] ✅ Bulk update: {rows_ok} righe, {len(all_updates)} celle aggiornate")
        return jsonify({'success': True, 'rows': rows_ok, 'cells': len(all_updates)})

    except Exception as e:
        return _api_error(e, 'aggiornamento bulk filone')


@bp.route('/api/genera-orari-lettere', methods=['POST'])
@login_required
def genera_orari_lettere():
    """
    Genera le fasce orarie delle lettere e le scrive nel FILONE.

    Calcola il percorso ottimizzato per ogni tecnico/data, assegna le fasce orarie,
    e scrive il risultato nella colonna:
      - 'Orari' per Torino
      - 'NOTE' per Pavia (note)
      - 'Ora pian' per Pavia (orari lettere)
    Solo se la cella è attualmente vuota.
    Associa i risultati per POD.
    """
    import re as _re
    from moduli.percorso import ordina_percorso
    from moduli.fasce_orarie import assegna_fasce, formatta_fascia_oraria, FASCE_ORARIE
    from moduli.data_loader import carica_dati_google_sheets
    from genera_excel_lettere import genera_excel_lettere as _genera_excel
    import math as _math

    data = request.get_json()
    if not data:
        return jsonify({'error': 'Dati mancanti'}), 400

    data_da = data.get('data_da')
    data_a = data.get('data_a')
    tappe_per_fascia_raw = data.get('tappe_per_fascia', 'auto')

    # Determina modalità: 'auto' o numero fisso
    auto_mode = (str(tappe_per_fascia_raw).lower() == 'auto')
    tappe_per_fascia_fisso = None
    if not auto_mode:
        try:
            tappe_per_fascia_fisso = int(tappe_per_fascia_raw)
            if tappe_per_fascia_fisso < 1 or tappe_per_fascia_fisso > 20:
                tappe_per_fascia_fisso = 4
        except (ValueError, TypeError):
            auto_mode = True

    if not data_da or not data_a:
        return jsonify({'error': 'Specificare data_da e data_a'}), 400

    logger.info("=" * 60)
    logger.info(f"📬 GENERAZIONE ORARI LETTERE")
    logger.info(f"   Provincia: {PROVINCIA_ATTIVA}")
    logger.info(f"   Periodo: {data_da} → {data_a}")
    logger.info(f"   Tappe per fascia: {'AUTO (distribuzione uniforme)' if auto_mode else tappe_per_fascia_fisso}")
    logger.info("=" * 60)

    try:
        # Genera lista date nel range
        from datetime import datetime as _dt, timedelta as _td
        dt_da = _dt.strptime(data_da, '%Y-%m-%d')
        dt_a = _dt.strptime(data_a, '%Y-%m-%d')

        if dt_a < dt_da:
            return jsonify({'error': 'data_a deve essere >= data_da'}), 400

        date_da_elaborare = []
        current = dt_da
        while current <= dt_a:
            date_da_elaborare.append(current.strftime('%Y-%m-%d'))
            current += _td(days=1)

        logger.info(f"   📅 Date: {len(date_da_elaborare)} giorni")

        # Carica dati dal FILONE
        df = carica_dati_google_sheets()
        if df is None or df.empty:
            return jsonify({'error': 'Impossibile caricare i dati dal FILONE'}), 500

        # Filtra per le date richieste
        df_filtrato = df[df['DATA_K'].isin(date_da_elaborare)].copy()
        df_filtrato = df_filtrato.loc[:, ~df_filtrato.columns.duplicated()]

        if df_filtrato.empty:
            return jsonify({'error': f'Nessun contatore trovato per il periodo {data_da} - {data_a}'}), 404

        logger.info(f"   📍 Contatori nel periodo: {len(df_filtrato)}")

        # Calcola fasce orarie per ogni contatore (chiave: POD)
        fasce_per_pod = {}  # pod -> fascia formattata

        for data_target in date_da_elaborare:
            df_data = df_filtrato[df_filtrato['DATA_K'] == data_target]
            if df_data.empty:
                continue

            tecnici_data = [t for t in MATRICOLE_VIP if t in df_data['TEC_K'].unique()]
            if 'TUTTI' in df_data['TEC_K'].unique():
                tecnici_data.append('TUTTI')

            for tec in tecnici_data:
                df_tec = df_data[df_data['TEC_K'] == tec]
                if df_tec.empty:
                    continue

                punti_list = df_tec.to_dict('records')
                percorso_ordinato = ordina_percorso(punti_list, DEPOSITO, usa_osrm_ottimizzazione=True)

                # Calcola tappe_per_fascia: auto (bilanciato) o fisso
                if auto_mode:
                    n_tappe = len(percorso_ordinato)
                    n_fasce = len(FASCE_ORARIE)
                    base = n_tappe // n_fasce
                    resto = n_tappe % n_fasce
                    logger.info(f"      🧠 Auto bilanciato: {tec} {data_target} → {n_tappe} tappe → {base}+1 x{resto} fasce, {base} x{n_fasce - resto} fasce")
                    tappe_con_fasce = assegna_fasce(percorso_ordinato, bilancia=True)
                else:
                    tpf = tappe_per_fascia_fisso
                    tappe_con_fasce = assegna_fasce(percorso_ordinato, tappe_per_fascia=tpf)

                for punto, fascia in tappe_con_fasce:
                    pod = str(punto.get('POD_K', '')).strip()
                    if pod:
                        fasce_per_pod[pod] = formatta_fascia_oraria(fascia)

        logger.info(f"   🕐 Fasce calcolate per {len(fasce_per_pod)} contatori (per POD)")

        if not fasce_per_pod:
            return jsonify({'error': 'Nessuna fascia oraria calcolata'}), 500

        # Ora scrivi nel FILONE - prima leggi i valori correnti per non sovrascrivere
        ws = get_worksheet(force_refresh=True)
        _header_map = get_header_map()

        # Determina colonna destinazione
        if PROVINCIA_ATTIVA == 'PAVIA':
            col_dest_name = 'Ora pian'  # Orari lettere vanno in Ora pian
        else:
            col_dest_name = 'Orari'

        # Trova colonna destinazione
        if col_dest_name not in _header_map:
            return jsonify({'error': f"Colonna '{col_dest_name}' non trovata nel FILONE. Crea prima la colonna."}), 400

        col_dest_idx = _header_map[col_dest_name]

        # Trova colonna POD
        col_pod_idx = None
        for pod_name in ['POD', 'Pod', 'pod', 'Codice POD']:
            if pod_name in _header_map:
                col_pod_idx = _header_map[pod_name]
                break

        if col_pod_idx is None:
            return jsonify({'error': 'Colonna POD non trovata nel FILONE'}), 400

        # Leggi tutte le righe (POD + colonna destinazione)
        all_values = ws.get_all_values()

        # Prepara batch update (solo celle vuote)
        updates = []
        scritti = 0
        saltati_occupati = 0
        saltati_non_trovati = 0

        for row_idx, row_values in enumerate(all_values[1:], start=2):  # Salta header
            # Leggi POD dalla riga
            pod_val = ''
            if col_pod_idx - 1 < len(row_values):
                pod_val = str(row_values[col_pod_idx - 1]).strip().upper()

            if not pod_val:
                continue

            # Cerca il POD nelle fasce calcolate
            fascia_trovata = None
            for pod_calc, fascia in fasce_per_pod.items():
                if str(pod_calc).strip().upper() == pod_val:
                    fascia_trovata = fascia
                    break

            if fascia_trovata is None:
                saltati_non_trovati += 1
                continue

            # Leggi il valore attuale nella colonna destinazione
            current_val = ''
            if col_dest_idx - 1 < len(row_values):
                current_val = str(row_values[col_dest_idx - 1]).strip()

            # Scrivi SOLO se la cella è vuota
            if current_val and current_val.upper() not in ('', 'NAN', 'NONE', 'NULL'):
                saltati_occupati += 1
                continue

            # Aggiungi al batch
            cell_ref = gspread.utils.rowcol_to_a1(row_idx, col_dest_idx)
            updates.append({
                'range': cell_ref,
                'values': [[fascia_trovata]]
            })
            scritti += 1

        # Esegui batch update
        if updates:
            # gspread ha un limite di batch, facciamo a blocchi di 500
            batch_size = 500
            for i in range(0, len(updates), batch_size):
                batch = updates[i:i + batch_size]
                ws.batch_update(batch)
                logger.info(f"   ✍️ Batch {i//batch_size + 1}: {len(batch)} celle aggiornate")

        # Genera anche il file Excel lettere
        file_excel = None
        try:
            logger.info(f"📄 Generazione file Excel lettere...")
            file_excel = _genera_excel(df, date_da_elaborare, tappe_per_fascia='auto' if auto_mode else tappe_per_fascia_fisso)
            if file_excel:
                logger.info(f"   ✅ Excel generato: {file_excel}")
        except Exception as ex_excel:
            logger.warning(f"   ⚠️ Errore generazione Excel: {ex_excel}")

        risultato = {
            'success': True,
            'message': f'Orari lettere generati con successo',
            'dettagli': {
                'periodo': f'{data_da} → {data_a}',
                'giorni': len(date_da_elaborare),
                'fasce_calcolate': len(fasce_per_pod),
                'scritti': scritti,
                'saltati_occupati': saltati_occupati,
                'saltati_non_trovati': saltati_non_trovati,
                'colonna': col_dest_name,
                'file_excel': file_excel or ''
            }
        }

        logger.info(f"✅ Risultato:")
        logger.info(f"      Scritti: {scritti}")
        logger.info(f"      Saltati (cella occupata): {saltati_occupati}")
        logger.info(f"      Non trovati nel range: {saltati_non_trovati}")
        logger.info("=" * 60)

        # Triggera sync dopo aver scritto
        _callbacks['trigger_sync_after_edit']()

        return jsonify(risultato)

    except Exception as e:
        return _api_error(e, 'generazione orari lettere')


@bp.route('/api/download-excel-lettere/<path:filename>')
@login_required
def download_excel_lettere(filename):
    """
    Endpoint per il download del file Excel lettere generato.
    Serve il file con header Content-Disposition per forzare il download sul client.
    """
    # Sicurezza: solo file .xlsx nella directory corrente
    safe_name = os.path.basename(filename)
    if not safe_name.endswith('.xlsx'):
        return jsonify({'error': 'Tipo file non consentito'}), 400

    # Risali dalla cartella moduli/ alla root del progetto
    filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', safe_name)
    filepath = os.path.normpath(filepath)
    if not os.path.exists(filepath):
        return jsonify({'error': 'File non trovato'}), 404

    return send_file(
        filepath,
        as_attachment=True,
        download_name=safe_name,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
