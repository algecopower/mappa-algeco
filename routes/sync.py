# -*- coding: utf-8 -*-
"""
Blueprint: Sync, Backup e Upload BoxBeat.
"""
import os
import re
import io
import json
import logging
import traceback
from datetime import datetime

import pandas as pd
import gspread.utils
from flask import Blueprint, request, jsonify, session, send_file

from moduli.safe_json import json_read

from moduli.auth import login_required
from moduli.shared import (
    get_worksheet, get_header_map, get_headers,
    invalidate_values_cache,
    find_row_by_eneltel, find_row_by_pod,
    build_pod_row_map, verify_row_pod,
    _api_error, _get_username, _callbacks,
    _trova_indice_colonna, _trova_indice_multi,
)
from config import PROVINCIA_ATTIVA, CAMPI_BOXBEAT

logger = logging.getLogger('mappa_algeco')
bp = Blueprint('sync', __name__)


# ============================================================================
# SYNC ROUTES
# ============================================================================

@bp.route('/api/filone/sync', methods=['POST'])
@login_required
def filone_trigger_sync():
    """Triggera sync della mappa dopo modifiche dalla vista Excel."""
    try:
        is_running = _callbacks.get('is_sync_running', lambda: False)()
        _callbacks['trigger_sync_after_edit']()
        return jsonify({'success': True, 'already_running': is_running})
    except Exception as e:
        return _api_error(e, 'sync filone')


@bp.route('/api/sync', methods=['POST'])
@login_required
def trigger_sync():
    """Triggera una sincronizzazione manuale."""
    try:
        is_running = _callbacks.get('is_sync_running', lambda: False)()
        if is_running:
            # Sync già in corso, segnala che serve un altro sync dopo
            _callbacks['trigger_sync_after_edit']()
            return jsonify({'success': True, 'message': 'Sync già in corso, verrà rieseguito al termine'})
        result = _callbacks['sincronizza_dati']()
        return jsonify({'success': result, 'message': 'Sync completato' if result else 'Sync fallito'})
    except Exception as e:
        return _api_error(e, 'sync manuale')


@bp.route('/api/sync/status')
@login_required
def sync_status():
    """Restituisce lo stato del sync."""
    return jsonify({
        'running': _callbacks.get('is_sync_running', lambda: False)(),
        'interval': _callbacks.get('get_sync_interval', lambda: 300)()
    })


# ============================================================================
# BACKUP ROUTES
# ============================================================================
from moduli.backup import (
    esegui_backup,
    lista_backup,
    ripristina_backup,
    elimina_backup,
)


@bp.route('/api/backup/lista')
@login_required
def api_backup_lista():
    """Restituisce la lista dei backup disponibili."""
    try:
        backups = lista_backup()
        return jsonify({'success': True, 'backups': backups})
    except Exception as e:
        return _api_error(e, 'lista backup')


@bp.route('/api/backup/esegui', methods=['POST'])
@login_required
def api_backup_esegui():
    """Esegue un backup manuale del FILONE."""
    try:
        forza = request.json.get('forza', False) if request.json else False
        risultato = esegui_backup(forza=forza)
        return jsonify(risultato)
    except Exception as e:
        return _api_error(e, 'esecuzione backup')


@bp.route('/api/backup/ripristina', methods=['POST'])
@login_required
def api_backup_ripristina():
    """Ripristina un backup sul FILONE (Google Drive)."""
    try:
        data = request.json
        if not data or 'filename' not in data:
            return jsonify({'error': 'Filename richiesto'}), 400

        filename = data['filename']
        risultato = ripristina_backup(filename)

        if risultato.get('success'):
            # Triggera un sync per aggiornare la mappa
            _callbacks['trigger_sync_after_edit']()

        return jsonify(risultato)
    except Exception as e:
        return _api_error(e, 'ripristino backup')


@bp.route('/api/backup/elimina', methods=['POST'])
@login_required
def api_backup_elimina():
    """Elimina un file di backup."""
    try:
        data = request.json
        if not data or 'filename' not in data:
            return jsonify({'error': 'Filename richiesto'}), 400

        risultato = elimina_backup(data['filename'])
        return jsonify(risultato)
    except Exception as e:
        return _api_error(e, 'eliminazione backup')


@bp.route('/api/backup/scarica_excel')
@login_required
def api_backup_scarica_excel():
    """Scarica un backup come file Excel (.xlsx)."""
    try:
        filename = request.args.get('filename', '')
        if not filename:
            return jsonify({'error': 'Filename richiesto'}), 400

        # Validazione nome file
        if not filename.startswith('filone_') or not filename.endswith('.json'):
            return jsonify({'error': 'Nome file non valido'}), 400

        backup_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'backup', PROVINCIA_ATTIVA.lower()
        )
        percorso = os.path.realpath(os.path.join(backup_dir, filename))
        if not percorso.startswith(os.path.realpath(backup_dir) + os.sep):
            return jsonify({'error': 'Percorso non valido'}), 400

        if not os.path.exists(percorso):
            return jsonify({'error': 'File di backup non trovato'}), 404

        backup_data = json_read(percorso, default=None)
        if backup_data is None:
            return jsonify({'error': 'Backup vuoto o corrotto'}), 400

        headers = backup_data.get('headers', [])
        righe = backup_data.get('righe', [])

        if not headers:
            return jsonify({'error': 'Backup vuoto o corrotto'}), 400

        # Crea DataFrame e genera Excel in memoria
        df = pd.DataFrame(righe, columns=headers)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='FILONE')
        output.seek(0)

        # Nome file Excel: stessa base ma .xlsx
        nome_excel = filename.replace('.json', '.xlsx')

        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=nome_excel
        )
    except Exception as e:
        return _api_error(e, 'download Excel backup')


# ============================================================================
# UPLOAD BOXBEAT
# ============================================================================

@bp.route('/api/upload-BOXBEAT', methods=['POST'])
@login_required
def upload_BOXBEAT():
    """
    Carica file BOXBEAT (Enel) e aggiorna i contatori installati.
    Accetta uno o più file Excel (.xlsx, .xls) o CSV.
    """
    try:
        # Supporta sia 'file' (singolo) che 'files' (multipli) per retrocompatibilità
        if 'files' in request.files:
            files = request.files.getlist('files')
        elif 'file' in request.files:
            files = [request.files['file']]
        else:
            return jsonify({'error': 'Nessun file caricato'}), 400

        # Filtra file validi
        files = [f for f in files if f and f.filename]
        if not files:
            return jsonify({'error': 'Nessun file selezionato'}), 400

        # OTTIMIZZAZIONE: Carica TUTTI i dati del FILONE una sola volta (prima di elaborare i file)
        ws = get_worksheet()
        all_data = ws.get_all_values()
        headers = all_data[0] if all_data else []

        # Trova indice colonna POD nel FILONE
        col_pod_filone = None
        for i, h in enumerate(headers):
            if h.strip().upper() == 'POD':
                col_pod_filone = i
                break

        if col_pod_filone is None:
            return jsonify({'error': 'Colonna POD non trovata nel FILONE'}), 400

        # Costruisci mappa POD -> numero riga (per lookup veloce)
        pod_to_row = {}
        for row_idx, row_data in enumerate(all_data[1:], start=2):
            if col_pod_filone < len(row_data):
                pod_value = str(row_data[col_pod_filone]).strip().upper()
                if pod_value:
                    pod_to_row[pod_value] = row_idx

        logger.info(f"[BOXBEAT] POD nel FILONE: {len(pod_to_row)}")

        # Configurazione campi BOXBEAT
        cfg = CAMPI_BOXBEAT.get(PROVINCIA_ATTIVA, {})
        col_esito_cfg = cfg.get('esito', 'Esito')
        esito_ok = cfg.get('esito_ok_valore', 'Esito OK')
        col_dest_op = cfg.get('colonna_dest_operatore', 'Operatore')
        col_dest_data = cfg.get('colonna_dest_data_inst', 'Data Installazione')
        col_dest_esito = cfg.get('colonna_dest_esito', 'Esito')

        # Raccogli aggiornamenti per POD (rimappati prima della scrittura)
        pod_updates = {}  # {POD: [(col_idx, valore), ...]}
        all_updates = []
        totale_righe = 0
        totale_esito_ok = 0
        totale_esito_ko = 0
        esito_ko_dettagli = []  # Lista dettagli KO
        aggiornati = 0
        errori = []
        file_elaborati = []

        _header_map = get_header_map()

        for file in files:
            filename = file.filename.lower()
            df = None

            try:
                if filename.endswith('.csv'):
                    file_content = file.read()
                    for encoding in ['utf-8', 'latin-1', 'cp1252', 'iso-8859-1']:
                        try:
                            import io
                            df = pd.read_csv(io.BytesIO(file_content), encoding=encoding, header=2)
                            break
                        except UnicodeDecodeError:
                            continue
                    if df is None:
                        errori.append(f"{file.filename}: impossibile decodificare CSV")
                        continue
                elif filename.endswith('.xlsx'):
                    df = pd.read_excel(file, engine='openpyxl', header=2)
                elif filename.endswith('.xls'):
                    df = pd.read_excel(file, engine='xlrd', header=2)
                else:
                    errori.append(f"{file.filename}: formato non supportato")
                    continue
            except Exception as e:
                errori.append(f"{file.filename}: {str(e)}")
                continue

            logger.info(f"[BOXBEAT] File {file.filename}: {len(df)} righe")
            totale_righe += len(df)

            # Trova colonna POD nel file Enel
            col_pod_bb = None
            for col in df.columns:
                if col.strip().upper() in ['POD', 'CODICE POD']:
                    col_pod_bb = col
                    break

            if col_pod_bb is None:
                errori.append(f"{file.filename}: colonna POD non trovata")
                continue

            # Trova colonna Esito
            col_esito_bb = None
            for col in df.columns:
                if col.strip().upper() == 'ESITO':
                    col_esito_bb = col
                    break

            if col_esito_bb is None:
                errori.append(f"{file.filename}: colonna Esito non trovata")
                continue

            # Trova colonna Matricola
            col_matricola_bb = None
            for col in df.columns:
                col_up = col.strip().upper()
                if 'MATRICOLA' in col_up and 'PREPOSTO' in col_up:
                    col_matricola_bb = col
                    break

            # Trova colonna Data esecuzione
            col_data_bb = None
            for col in df.columns:
                col_up = col.strip().upper()
                if 'DATA' in col_up and 'ESECUZIONE' in col_up:
                    col_data_bb = col
                    break

            # Filtra Esito OK
            df_ok = df[df[col_esito_bb].astype(str).str.strip().str.upper() == esito_ok.upper()].copy()
            logger.info(f"[BOXBEAT] {file.filename}: {len(df_ok)} con Esito OK")
            totale_esito_ok += len(df_ok)

            # Rileva Esito KO
            df_ko = df[df[col_esito_bb].astype(str).str.strip().str.upper().str.contains('KO', na=False)].copy()
            if len(df_ko) > 0:
                logger.info(f"[BOXBEAT] {file.filename}: {len(df_ko)} con Esito KO")
                totale_esito_ko += len(df_ko)
                for _, row_ko in df_ko.iterrows():
                    pod_ko = str(row_ko[col_pod_bb]).strip().upper() if col_pod_bb else ''
                    esito_val = str(row_ko[col_esito_bb]).strip() if col_esito_bb else ''
                    # Cerca nome cliente nel filone
                    cliente_ko = ''
                    row_filone = pod_to_row.get(pod_ko)
                    if row_filone is not None:
                        # Cerca colonna CLIENTE nel filone
                        for cn in ['CLIENTE', 'Cliente', 'cliente', 'NOMINATIVO', 'Nominativo']:
                            if cn in _header_map:
                                try:
                                    cliente_ko = ws.cell(row_filone, _header_map[cn]).value or ''
                                except Exception:
                                    pass
                                break
                    esito_ko_dettagli.append({
                        'pod': pod_ko,
                        'esito': esito_val,
                        'cliente': cliente_ko,
                        'file': file.filename
                    })

            file_aggiornati = 0
            for _, row_bb in df_ok.iterrows():
                pod = str(row_bb[col_pod_bb]).strip().upper()

                row_num = pod_to_row.get(pod)
                if row_num is None:
                    continue

                # Accumula aggiornamenti per POD (col_idx, valore)
                if pod not in pod_updates:
                    pod_updates[pod] = []

                # Operatore
                if col_matricola_bb and col_dest_op in _header_map:
                    operatore = str(row_bb[col_matricola_bb]).strip()
                    if operatore and operatore != 'nan':
                        pod_updates[pod].append((_header_map[col_dest_op], operatore))

                # Data installazione
                if col_data_bb and col_dest_data in _header_map:
                    data_exec = row_bb[col_data_bb]
                    if pd.notna(data_exec):
                        if isinstance(data_exec, datetime):
                            data_str = data_exec.strftime('%d/%m/%Y')
                        elif isinstance(data_exec, pd.Timestamp):
                            data_str = data_exec.strftime('%d/%m/%Y')
                        else:
                            data_str = str(data_exec).split()[0].rstrip('-').strip()
                            if '-' in data_str and len(data_str) == 10:
                                parts = data_str.split('-')
                                if len(parts) == 3 and len(parts[0]) == 4:
                                    data_str = f"{parts[2]}/{parts[1]}/{parts[0]}"
                        pod_updates[pod].append((_header_map[col_dest_data], data_str))

                # Esito
                if col_dest_esito in _header_map:
                    pod_updates[pod].append((_header_map[col_dest_esito], 'ESITO OK'))

                # Cancella eventuale testo nella colonna SIGILLO
                # MA preserva i codici sigillo reali (es. 20198337Z)
                if 'SIGILLO' in _header_map:
                    sigillo_attuale = ''
                    if row_num - 1 < len(all_data) and _header_map['SIGILLO'] - 1 < len(all_data[row_num - 1]):
                        sigillo_attuale = str(all_data[row_num - 1][_header_map['SIGILLO'] - 1]).strip()
                    # Preserva se contiene un codice sigillo reale (cifre + lettera)
                    if not re.search(r'\d+[A-Za-z]', sigillo_attuale):
                        pod_updates[pod].append((_header_map['SIGILLO'], ''))

                file_aggiornati += 1

            aggiornati += file_aggiornati
            file_elaborati.append({'nome': file.filename, 'righe': len(df), 'esito_ok': len(df_ok), 'esito_ko': len(df_ko), 'aggiornati': file_aggiornati})

        # RIMAPPA: ri-leggi POD dal foglio fresco per avere righe aggiornate
        if pod_updates:
            fresh_pod_map = build_pod_row_map()
            all_updates = []
            pods_spostati = 0

            for pod, updates_list in pod_updates.items():
                fresh_row = fresh_pod_map.get(pod)
                if fresh_row is None:
                    logger.warning(f"[BOXBEAT] POD {pod} non trovato nella rimappatura, saltato")
                    continue
                old_row = pod_to_row.get(pod)
                if old_row != fresh_row:
                    pods_spostati += 1
                for col_idx, value in updates_list:
                    all_updates.append({
                        'range': gspread.utils.rowcol_to_a1(fresh_row, col_idx),
                        'values': [[value]]
                    })

            if pods_spostati:
                logger.warning(f"[BOXBEAT] ⚠️ {pods_spostati} POD avevano righe spostate, rimappati correttamente")

        # ESEGUI UN SOLO BATCH UPDATE per tutti i file
        if all_updates:
            logger.info(f"[BOXBEAT] Eseguo batch update con {len(all_updates)} celle...")
            ws.batch_update(all_updates)
            invalidate_values_cache()

        return jsonify({
            'success': True,
            'message': f'Aggiornati {aggiornati} contatori da {len(file_elaborati)} file',
            'aggiornati': aggiornati,
            'totale_file': totale_righe,
            'con_esito_ok': totale_esito_ok,
            'con_esito_ko': totale_esito_ko,
            'esito_ko_dettagli': esito_ko_dettagli[:50],
            'file_elaborati': file_elaborati,
            'errori': errori[:10] if errori else []
        })

    except Exception as e:
        return _api_error(e, 'upload BoxBeat')
