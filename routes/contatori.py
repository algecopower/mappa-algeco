# -*- coding: utf-8 -*-
"""
Blueprint: Contatori — gestione contatori, filone, orari lettere.
"""
import logging
import os
import re
from datetime import datetime

from flask import Blueprint, request, jsonify, session, send_file

from moduli.auth import login_required
from moduli.shared import (
    get_worksheet,
    invalidate_values_cache,
    find_row_by_eneltel,
    update_row_fields,
    safe_batch_update_by_name,
    force_refresh_headers,
    _imposta_appuntamento_su_filone,
    _rimuovi_appuntamento_da_filone,
    _get_username,
    _api_error,
    _resolve_anchor_col_name,
    _callbacks,
    _sheets_retry,
    _gsheets_write_lock,
)
from config import (
    PROVINCIA_ATTIVA,
    CAMPI_MODIFICABILI,
    MATRICOLE_VIP,
    DEPOSITO,
    MAPPATURA_COLONNE,
    MAPPATURA_COLONNE_DEFAULT,
)

logger = logging.getLogger('mappa_algeco')

# Colonne protette nella vista Excel (non modificabili dall'utente)
# Centralizzate: stesse colonne per entrambe le province
_COLONNE_PROTETTE_BASE = [
    'LCL', 'TIPO_MISURATORE', 'MOT. RICH.', 'MID', 'SIGILLO', 'TIPOLOGIA ZONA',
    'POD', 'ENELTEL', 'CODICE_ANTE_SOSTITUZIONE', 'MATRICOLA_ANTE_SOSTITUZIONE',
    'COD. INSTALLATO', 'MATR. INSTALLATO', 'DATA INSTALLAZIONE', 'ESITO'
]
COLONNE_PROTETTE_FILONE = {
    'TORINO': _COLONNE_PROTETTE_BASE,
    'PAVIA': _COLONNE_PROTETTE_BASE + ['PR'],  # PR è in sola lettura per Pavia
}

bp = Blueprint('contatori', __name__)


def _find_value_in_row(data, chiave_mapping):
    """Trova il valore di un campo dal dict raw Sheets usando la mappatura colonne."""
    # Prima controlla mappatura personalizzata
    if MAPPATURA_COLONNE.get(chiave_mapping):
        nome = MAPPATURA_COLONNE[chiave_mapping]
        for k, v in data.items():
            if k.strip().lower() == nome.strip().lower():
                return str(v).strip() if v else ''
    # Poi controlla i nomi default
    defaults = MAPPATURA_COLONNE_DEFAULT.get(chiave_mapping, {})
    nomi = defaults.get('nomi_default', [])
    for nome in nomi:
        for k, v in data.items():
            if k.strip().lower() == nome.strip().lower():
                return str(v).strip() if v else ''
    return ''


def _format_row_to_contatore(data):
    """Converte il dict raw Sheets (header→valore) nel formato contatore frontend."""
    from moduli.percorso import normalizza_eneltel, estrai_orario, rileva_appuntamento

    eneltel_raw = _find_value_in_row(data, 'eneltel')
    note_app_raw = _find_value_in_row(data, 'note_appuntamento')

    is_appuntamento = rileva_appuntamento(note_app_raw)
    orari_raw = _find_value_in_row(data, 'orari_lettere')
    ora_appuntamento = estrai_orario(orari_raw) if (is_appuntamento and orari_raw) else None

    return {
        'eneltel': eneltel_raw,
        'id': normalizza_eneltel(eneltel_raw) or '',
        'pod': _find_value_in_row(data, 'pod'),
        'cliente': _find_value_in_row(data, 'nominativo'),
        'comune': _find_value_in_row(data, 'comune'),
        'localita': _find_value_in_row(data, 'localita'),
        'via': _find_value_in_row(data, 'indirizzo'),
        'civico': _find_value_in_row(data, 'civico'),
        'telefono': _find_value_in_row(data, 'telefono'),
        'email': _find_value_in_row(data, 'email'),
        'note': _find_value_in_row(data, 'posizione_misuratore'),
        'posizione_mis': _find_value_in_row(data, 'posizione_misuratore'),
        'note_app': note_app_raw,
        'tipo_ce': _find_value_in_row(data, 'tipo_misuratore'),
        'orari_lettere': orari_raw,
        'ora_pianificazione': orari_raw,
        'lcl': _find_value_in_row(data, 'lcl'),
        'is_appuntamento': is_appuntamento,
        'ora_appuntamento': ora_appuntamento,
        'smart_test': _find_value_in_row(data, 'smart_test'),
    }


@bp.route('/api/contatore/<eneltel>')
@login_required
def get_contatore(eneltel):
    """Ottiene i dati di un contatore dal DB locale + pending in volo."""
    from moduli import db as _db
    ek = _db.normalize_eneltel(eneltel)
    # LCL opzionale come query param per disambiguare righe duplicate
    # (Pavia multi-LCL). Default: prima riga (sheets_row più basso).
    lcl_arg = request.args.get('lcl')
    local = _db.get_filone_row(ek, lcl=lcl_arg)
    if not local:
        return jsonify({'error': f'ENELTEL {eneltel} non trovato'}), 404
    row_lcl = local.get('lcl') or ''

    conn = _db.get_conn()
    col_rows = conn.execute(
        'SELECT column_name, chiave_interna FROM column_registry '
        'ORDER BY last_index'
    ).fetchall()
    data = {}
    for cr in col_rows:
        col_sheet = cr['column_name']
        chiave = cr['chiave_interna']
        if chiave in _db.COLONNE_NOTE:
            val = local.get(chiave, '') or ''
        elif chiave == 'eneltel':
            val = local.get('eneltel_key', '') or ''
        else:
            ev = conn.execute(
                "SELECT value FROM filone_extra WHERE eneltel_key=? AND COALESCE(lcl,'')=? AND column_name=?",
                (ek, row_lcl, col_sheet),
            ).fetchone()
            val = (ev['value'] if ev else '') or ''
        data[col_sheet] = val

    # Pending in volo per la specifica (eneltel, lcl): mostrano subito le
    # modifiche utente prima che il drainer pushi su Sheets.
    pending_rows = conn.execute(
        "SELECT column_name, new_value FROM pending_writes "
        "WHERE eneltel_key=? AND COALESCE(lcl,'')=? AND state IN ('pending','in_flight')",
        (ek, row_lcl),
    ).fetchall()
    for pr in pending_rows:
        data[pr['column_name']] = pr['new_value'] or ''

    return jsonify({
        'eneltel': eneltel,
        'row': local.get('sheets_row'),
        'lcl': row_lcl,
        'data': data,
        'contatore': _format_row_to_contatore(data),
    })


@bp.route('/api/contatore/<eneltel>', methods=['PUT'])
@login_required
def update_contatore(eneltel):
    """Aggiorna i campi di un contatore."""
    try:
        fields = request.json
        if not fields:
            return jsonify({'error': 'Nessun campo da aggiornare'}), 400

        # Estrai LCL come ancora secondaria (per disambiguare duplicate_eneltel
        # su Pavia). Rimosso dai fields prima della scrittura.
        anchor_lcl = fields.pop('_anchor_lcl', None)

        # Valida che i campi siano modificabili
        campi_ammessi = CAMPI_MODIFICABILI.get(PROVINCIA_ATTIVA, {})
        col_names = list(campi_ammessi.values())

        # Header map letta dal column_registry del DB (no chiamate Sheets).
        from moduli import db as _db
        conn = _db.get_conn()
        registry_rows = conn.execute(
            'SELECT column_name FROM column_registry ORDER BY last_index'
        ).fetchall()
        _header_map = {r['column_name']: i + 1 for i, r in enumerate(registry_rows)}
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
            elif key in ('ora_pianificazione', 'orari_lettere', 'Ora pianificazione', 'ORARI', 'Orari'):
                target_col = campi_ammessi.get('ora_pianificazione', 'Orari')
                fields_validi[target_col] = value

        logger.info(f"✅ Campi validi da scrivere: {fields_validi}")

        if not fields_validi:
            return jsonify({'error': 'Nessun campo valido da aggiornare'}), 400

        success, msg = update_row_fields(eneltel, fields_validi, lcl=anchor_lcl)

        if success:
            # L'audit della modifica è gestito automaticamente da
            # db.record_local_change() dentro update_row_fields().
            _callbacks['trigger_sync_after_edit']()
            return jsonify({'success': True, 'message': msg, 'updated': list(fields_validi.keys())})
        else:
            return jsonify({'error': msg}), 400

    except Exception as e:
        return _api_error(e, 'aggiornamento contatore')


@bp.route('/api/contatori/bulk-pianifica', methods=['PUT'])
@login_required
def bulk_pianifica():
    """Assegna data e/o operatore a più contatori in bulk (per modalità pianificazione).

    Body JSON: {
        "enteltels": ["123456", "789012", ...],
        "data_pian": "2026-04-15",   // opzionale
        "operatore": "AE101392"      // opzionale (matricola o nome)
    }
    Almeno uno tra data_pian e operatore deve essere presente.
    """
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'Body JSON richiesto'}), 400

        enteltels = data.get('enteltels', [])
        data_pian = data.get('data_pian', '').strip()
        operatore = data.get('operatore', '').strip()
        # Mappa opzionale {eneltel: lcl} per disambiguare righe duplicate.
        lcl_by_eneltel = data.get('lcl_by_eneltel') or {}
        data_pian_filone = ''

        if not enteltels:
            return jsonify({'error': 'Nessun contatore selezionato'}), 400
        if not data_pian and not operatore:
            return jsonify({'error': 'Specificare almeno data o operatore'}), 400
        if len(enteltels) > 500:
            return jsonify({'error': 'Massimo 500 contatori per volta'}), 400

        # Valida data se presente
        if data_pian:
            try:
                dt_pian = datetime.strptime(data_pian, '%Y-%m-%d')
                # Sul FILONE la data di pianificazione va salvata in formato italiano.
                data_pian_filone = dt_pian.strftime('%d/%m/%Y')
            except ValueError:
                return jsonify({'error': 'Formato data non valido (atteso YYYY-MM-DD)'}), 400

        campi_ammessi = CAMPI_MODIFICABILI.get(PROVINCIA_ATTIVA, {})

        # Prepara la lista (col_name, value) — gli indici li risolve safe_batch_update
        # con header FRESCHI al momento della scrittura.
        fields_to_set = []  # list of (col_name, value)
        if data_pian and 'data_pian' in campi_ammessi:
            fields_to_set.append((campi_ammessi['data_pian'], data_pian_filone))
        if operatore and 'operatore' in campi_ammessi:
            fields_to_set.append((campi_ammessi['operatore'], operatore))

        if not fields_to_set:
            return jsonify({'error': 'Nessun campo valido da aggiornare'}), 400

        # Trova le righe via ENELTEL una sola volta (find_row_by_eneltel ora usa
        # header freschi internamente). Tieni anche l'ENELTEL come ancora.
        eneltel_to_row = {}
        non_trovati = []
        for eneltel in enteltels:
            eneltel_str = str(eneltel).strip()
            row = find_row_by_eneltel(eneltel_str)
            if row is None:
                non_trovati.append(eneltel_str)
                continue
            eneltel_to_row[eneltel_str] = row

        if not eneltel_to_row:
            return jsonify({'error': 'Nessun contatore trovato nel filone'}), 404

        # Costruisci la lista di item per safe_batch_update_by_name.
        # `anchor` = ENELTEL → se la riga è stata spostata medio tempore
        # (sort/inserimento/cancellazione) viene rimappata; se l'ENELTEL non
        # è più nel foglio, la cella viene saltata invece di sovrascrivere
        # un altro contatore.
        items = []
        for eneltel_str, row in eneltel_to_row.items():
            anchor_lcl = lcl_by_eneltel.get(eneltel_str)
            for col_name, value in fields_to_set:
                item = {
                    'row': row,
                    'col': col_name,
                    'value': value,
                    'anchor': eneltel_str,
                }
                if anchor_lcl is not None:
                    item['anchor_lcl'] = str(anchor_lcl)
                items.append(item)

        eneltel_anchor_col = _resolve_anchor_col_name('eneltel') or 'Eneltel'
        res = safe_batch_update_by_name(
            items, anchor_col=eneltel_anchor_col, anchor_normalize='lstrip0',
        )

        if res.get('error'):
            return jsonify({'error': res['error']}), 400
        if res.get('invalid_cols'):
            return jsonify({
                'error': f"Colonne non trovate sul foglio: {res['invalid_cols']}"
            }), 400

        invalidate_values_cache()
        _callbacks['trigger_sync_after_edit']()

        username = _get_username()
        logger.info(
            f"[Pianificazione] ✅ {username}: bulk update {len(eneltel_to_row)} contatori "
            f"(data={data_pian or '-'}, op={operatore or '-'}) "
            f"written={res['written']} remapped={res['remapped']} "
            f"lost_anchors={len(res['lost_anchors'])}"
        )

        result = {
            'success': True,
            'aggiornati': len(eneltel_to_row),
            'celle': res['written'],
            'remapped': res['remapped'],
        }
        if non_trovati:
            result['non_trovati'] = non_trovati[:20]
            result['non_trovati_count'] = len(non_trovati)
        if res['lost_anchors']:
            result['ancore_perse'] = len(res['lost_anchors'])
        return jsonify(result)

    except Exception as e:
        return _api_error(e, 'bulk pianificazione')


@bp.route('/api/contatori/excel-assegnati', methods=['POST'])
@login_required
def excel_contatori_assegnati():
    """Genera un file Excel con POD e operatore dei contatori appena assegnati.

    Body JSON: {
        "enteltels": ["123456", "789012", ...],
        "data_pian": "2026-04-15",   // opzionale, per includere nel nome file
        "operatore": "AE101392"      // opzionale, per includere nel nome file
    }
    Restituisce direttamente il file .xlsx.
    """
    try:
        from io import BytesIO
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

        data = request.json
        if not data:
            return jsonify({'error': 'Body JSON richiesto'}), 400

        enteltels = data.get('enteltels', [])
        data_pian = data.get('data_pian', '').strip()
        operatore = data.get('operatore', '').strip()

        if not enteltels:
            return jsonify({'error': 'Nessun contatore specificato'}), 400

        ws_sheet = get_worksheet()
        all_values = ws_sheet.get_all_values()
        if not all_values:
            return jsonify({'error': 'Filone vuoto'}), 500

        headers = all_values[0]
        rows = all_values[1:]

        # Trova indici colonne necessarie
        def find_col(names):
            for name in names:
                if name in headers:
                    return headers.index(name)
            return None

        col_eneltel = find_col(['Eneltel', 'ENELTEL'])
        col_pod = find_col(['POD', 'Pod', 'pod'])
        col_operatore = find_col(['Operatore', 'OPERATORE', 'operatore'])
        col_nominativo = find_col(['NOMINATIVO', 'Nominativo', 'Cliente', 'CLIENTE'])
        col_indirizzo = find_col(['Indirizzo', 'Via', 'VIA', 'Via Num'])
        col_civico = find_col(['Num', 'NUM', 'civico', 'CIVICO'])
        col_comune = find_col(['Comune', 'COMUNE'])
        col_data_pian = find_col(['DATA PIAN', 'DATA_PIAN', 'PROG', 'DATA'])

        if col_eneltel is None:
            return jsonify({'error': 'Colonna ENELTEL non trovata'}), 500

        # Filtra righe corrispondenti
        enteltels_set = set(str(e).strip() for e in enteltels)
        righe_trovate = []
        for row in rows:
            eneltel_val = str(row[col_eneltel]).strip() if col_eneltel < len(row) else ''
            if eneltel_val in enteltels_set:
                righe_trovate.append(row)

        if not righe_trovate:
            return jsonify({'error': 'Nessun contatore trovato'}), 404

        # Crea Excel
        wb = Workbook()
        ws_xl = wb.active
        ws_xl.title = 'Contatori Assegnati'

        # Definisci colonne da esportare
        colonne_export = []
        colonne_export.append(('POD', col_pod))
        colonne_export.append(('Operatore', col_operatore))
        colonne_export.append(('ENELTEL', col_eneltel))
        colonne_export.append(('Nominativo', col_nominativo))
        colonne_export.append(('Indirizzo', col_indirizzo))
        colonne_export.append(('Civico', col_civico))
        colonne_export.append(('Comune', col_comune))
        colonne_export.append(('Data Pianificazione', col_data_pian))

        # Filtra colonne che esistono
        colonne_export = [(name, idx) for name, idx in colonne_export if idx is not None]

        # Header
        header_font = Font(bold=True, color='FFFFFF', size=11)
        header_fill = PatternFill(start_color='2E86C1', end_color='2E86C1', fill_type='solid')
        header_alignment = Alignment(horizontal='center', vertical='center')
        thin_border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
        )

        for col_num, (col_name, _) in enumerate(colonne_export, 1):
            cell = ws_xl.cell(row=1, column=col_num, value=col_name)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_alignment
            cell.border = thin_border

        # Dati
        data_alignment = Alignment(horizontal='left', vertical='center')
        for row_num, row_data in enumerate(righe_trovate, 2):
            for col_num, (col_name, src_idx) in enumerate(colonne_export, 1):
                val = str(row_data[src_idx]).strip() if src_idx < len(row_data) else ''
                # Converti date YYYY-MM-DD in DD/MM/YYYY
                if col_name == 'Data Pianificazione' and val and re.match(r'^\d{4}-\d{2}-\d{2}$', val):
                    val = f"{val[8:10]}/{val[5:7]}/{val[0:4]}"
                cell = ws_xl.cell(row=row_num, column=col_num, value=val)
                cell.alignment = data_alignment
                cell.border = thin_border

        # Auto-dimensiona colonne
        from openpyxl.utils import get_column_letter
        for col_num, (col_name, _) in enumerate(colonne_export, 1):
            max_len = len(col_name)
            for row_num in range(2, len(righe_trovate) + 2):
                val = ws_xl.cell(row=row_num, column=col_num).value or ''
                max_len = max(max_len, len(str(val)))
            ws_xl.column_dimensions[get_column_letter(col_num)].width = min(max_len + 3, 40)

        # Freeze prima riga
        ws_xl.freeze_panes = 'A2'

        # Genera nome file
        parts = ['ASSEGNAZIONE', PROVINCIA_ATTIVA]
        if data_pian:
            parts.append(data_pian.replace('-', ''))
        if operatore:
            parts.append(operatore)
        filename = '_'.join(parts) + '.xlsx'

        # Salva in memoria e restituisci
        output = BytesIO()
        wb.save(output)
        output.seek(0)

        return send_file(
            output,
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

    except Exception as e:
        return _api_error(e, 'generazione excel assegnati')


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
        anchor_lcl = data.get('_anchor_lcl')

        if not orario:
            return jsonify({'error': 'Orario richiesto'}), 400

        row = find_row_by_eneltel(eneltel)
        if row is None:
            return jsonify({'error': f'ENELTEL {eneltel} non trovato'}), 404

        result = _imposta_appuntamento_su_filone(
            row, orario, data_app=data_app, note_extra=note_extra,
            origine='contatore', eneltel_override=eneltel,
            nota_corrente=nota_corrente, lcl=anchor_lcl,
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
        anchor_lcl = data.get('_anchor_lcl')

        row = find_row_by_eneltel(eneltel)
        if row is None:
            return jsonify({'error': f'ENELTEL {eneltel} non trovato'}), 404

        result = _rimuovi_appuntamento_da_filone(row, nota_corrente=nota_corrente, lcl=anchor_lcl)
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
    """Restituisce tutti i dati del FILONE per la vista Excel.

    Legge direttamente dal DB locale: zero finestre di staleness, niente race
    condition con il cache Sheets/drainer. Il DB è fonte di verità e il
    drainer asincrono si occupa di pushare verso Sheets.
    """
    try:
        from moduli import db as _db
        headers, data = _db.dump_filone_as_rows()
        return jsonify({
            'headers': headers,
            'data': data,
            'total': len(data),
            'protectedCols': COLONNE_PROTETTE_FILONE.get(PROVINCIA_ATTIVA, []),
        })
    except Exception as e:
        return _api_error(e, 'caricamento filone')


@bp.route('/api/filone/notes')
@login_required
def get_filone_notes():
    """Restituisce i commenti cella persistenti dal DB locale."""
    try:
        from moduli import db as _db
        return jsonify({
            'notes': _db.get_cell_comments(),
            'source': 'db',
        })
    except Exception as e:
        return _api_error(e, 'caricamento commenti filone')


@bp.route('/api/filone/notes', methods=['PUT'])
@login_required
def update_filone_notes():
    """Salva/elimina commenti cella nel DB locale."""
    try:
        from moduli import db as _db
        data = request.get_json(silent=True) or {}
        notes = data.get('notes') or {}
        cleared = data.get('cleared') or []
        if not isinstance(notes, dict):
            return jsonify({'error': 'Formato notes non valido'}), 400
        if not isinstance(cleared, list):
            return jsonify({'error': 'Formato cleared non valido'}), 400
        result = _db.set_cell_comments_from_ui(
            notes,
            cleared,
            user=_get_username(),
        )
        return jsonify({'success': True, **result})
    except Exception as e:
        return _api_error(e, 'salvataggio commenti filone')


@bp.route('/api/filone/row/<int:row_num>', methods=['PUT'])
@login_required
def update_filone_row(row_num):
    """Aggiorna una riga del FILONE (per vista Excel) usando safe_batch_update.

    Anti-spanamento:
    - rilegge gli header freschi (sotto write-lock) → indice colonna sempre attuale
    - usa POD come ancora di riga: se la riga è stata spostata viene rimappata,
      se il POD non è più presente la scrittura viene saltata invece che andare
      su un altro contatore.
    """
    try:
        if row_num < 2:
            return jsonify({'error': 'Riga non valida'}), 400

        fields = request.json
        if not fields:
            return jsonify({'error': 'Nessun campo da aggiornare'}), 400

        # Colonne sbloccate dall'utente (protette ma editabili per questa sessione)
        sbloccate = set(c.lower() for c in fields.pop('_unlocked_cols', []))

        # Estrae l'ancora POD (se il client l'ha inviata).
        anchor_pod = fields.pop('_anchor_pod', None)
        # LCL come ancora secondaria per disambiguare righe con stesso
        # Eneltel ma LCL diverso (caso multi-LCL su Pavia).
        anchor_lcl = fields.pop('_anchor_lcl', None)

        # Colonne protette lato server (escluse quelle sbloccate dall'utente)
        protette = set(c.lower() for c in COLONNE_PROTETTE_FILONE.get(PROVINCIA_ATTIVA, _COLONNE_PROTETTE_BASE))
        protette -= sbloccate

        # Costruisci items per safe_batch_update.
        items = []
        for col_name, value in fields.items():
            if col_name.startswith('_'):
                continue
            if col_name.lower() in protette:
                continue
            item = {'row': row_num, 'col': col_name, 'value': value}
            if anchor_pod:
                item['anchor'] = anchor_pod
            if anchor_lcl is not None:
                item['anchor_lcl'] = anchor_lcl
            items.append(item)

        if not items:
            return jsonify({'success': True, 'updated': 0})

        pod_anchor_col = _resolve_anchor_col_name('pod') or 'POD'
        res = safe_batch_update_by_name(
            items,
            anchor_col=pod_anchor_col if anchor_pod else None,
            anchor_normalize='upper',
        )

        if anchor_pod and res.get('lost_anchors'):
            return jsonify({
                'error': f'POD {anchor_pod} non trovato nel filone (rimosso o cambiato)'
            }), 409
        if res.get('error'):
            return jsonify({'error': res['error']}), 400
        if res.get('invalid_cols'):
            logger.warning(
                f"[Filone] update_filone_row: colonne non trovate sul foglio "
                f"({res['invalid_cols']}) — relative scritture saltate."
            )

        if res.get('remapped'):
            logger.warning(
                f"[Filone] ⚠️ Riga {row_num} rimappata via POD={anchor_pod}"
            )

        # === RILEVAMENTO APPUNTAMENTI MANUALI ===
        # "APP HH:MM" nella colonna NOTE APP (logica unificata)
        try:
            orario_trovato = None
            nota_salvata = ''

            for col_name, value in fields.items():
                val_str = str(value).strip()
                if not val_str:
                    continue

                col_upper = col_name.upper().strip()

                # Cerca "APP HH:MM" in NOTE APP / NOTE
                if col_upper in ('NOTE APP', 'NOTE', 'NOTE_APP'):
                    match_app = re.search(
                        r'APP\s+(\d{1,2}[:.]\d{2}(?:\s*[-/]\s*\d{1,2}[:.]\d{2})?)',
                        val_str, re.IGNORECASE
                    )
                    if match_app:
                        orario_trovato = match_app.group(1).replace('.', ':')
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

        # Trigger sync per propagare la modifica alla mappa via SSE.
        # trigger_sync_after_edit() prova prima una patch in-place (campi
        # cosmetici); se fallisce o se il cambio e' strutturale
        # (data_pianificata, operatore, lat/lon, esito, comune) sveglia
        # il sync loop che ricalcola zone/percorsi.
        try:
            _callbacks['trigger_sync_after_edit']()
        except Exception:
            pass

        return jsonify({
            'success': True,
            'updated': res['written'],
            'remapped': res['remapped'] > 0,
            'invalid_cols': res['invalid_cols'],
        })

    except Exception as e:
        return _api_error(e, 'aggiornamento riga filone')


@bp.route('/api/filone/bulk', methods=['PUT'])
@login_required
def update_filone_bulk():
    """Aggiorna più righe del FILONE in un'unica chiamata batch (vista Excel).

    Body JSON: { "rows": { "row_num": { "col_name": "value", ... }, ... } }

    Anti-spanamento: tutte le scritture passano da safe_batch_update_by_name
    che (1) rilegge gli header freschi sotto write-lock e (2) usa POD come
    ancora di riga per re-mappare le righe spostate o saltare i record
    spariti, invece di sovrascrivere i contatori sbagliati.
    """
    try:
        data = request.json
        if not data or 'rows' not in data:
            return jsonify({'error': 'Formato non valido. Atteso: {"rows": {...}}'}), 400

        rows_dict = data['rows']
        if not rows_dict:
            return jsonify({'error': 'Nessuna riga da aggiornare'}), 400

        # Colonne sbloccate dall'utente (protette ma editabili per questa sessione)
        sbloccate = set(c.lower() for c in data.get('_unlocked_cols', []))

        # Colonne protette lato server (escluse quelle sbloccate dall'utente)
        protette = set(c.lower() for c in COLONNE_PROTETTE_FILONE.get(PROVINCIA_ATTIVA, _COLONNE_PROTETTE_BASE))
        protette -= sbloccate

        from moduli import db as _db
        conn = _db.get_conn()
        valid_cols = {
            r['column_name'].strip().lower(): r['column_name']
            for r in conn.execute('SELECT column_name FROM column_registry').fetchall()
        }
        invalid_cols = set()

        # Costruisci la lista di item per safe_batch_update_by_name.
        items = []
        rows_ok = set()
        any_anchor = False
        for row_num_str, fields in rows_dict.items():
            try:
                row_num = int(row_num_str)
            except (ValueError, TypeError):
                continue
            if row_num < 2:
                continue
            anchor_pod = fields.get('_anchor_pod') if isinstance(fields, dict) else None
            anchor_lcl = fields.get('_anchor_lcl') if isinstance(fields, dict) else None
            if anchor_pod:
                any_anchor = True

            for col_name, value in fields.items():
                if col_name.startswith('_'):
                    continue
                if col_name.lower() in protette:
                    continue
                col_key = col_name.strip().lower()
                if col_key not in valid_cols:
                    invalid_cols.add(col_name)
                    continue
                item = {'row': row_num, 'col': valid_cols[col_key], 'value': value}
                if anchor_pod:
                    item['anchor'] = anchor_pod
                if anchor_lcl is not None:
                    item['anchor_lcl'] = anchor_lcl
                items.append(item)
            rows_ok.add(row_num_str)

        if not items:
            if invalid_cols:
                return jsonify({
                    'error': 'Colonne non trovate nel filone',
                    'invalid_cols': sorted(invalid_cols),
                }), 400
            return jsonify({'error': 'Nessun campo valido da aggiornare'}), 400

        pod_anchor_col = _resolve_anchor_col_name('pod') or 'POD'
        res = safe_batch_update_by_name(
            items,
            anchor_col=pod_anchor_col if any_anchor else None,
            anchor_normalize='upper',
        )

        if res.get('error'):
            return jsonify({'error': res['error']}), 400
        if invalid_cols:
            res['invalid_cols'] = sorted(set(res.get('invalid_cols') or []) | invalid_cols)
        if not res.get('success', True) and res.get('written', 0) == 0:
            return jsonify({
                'error': 'Righe o ancore non trovate nel filone',
                'skipped': res.get('skipped', 0),
            }), 409

        if res['lost_anchors']:
            logger.warning(
                f"[Filone Bulk] ⚠️ {len(res['lost_anchors'])} POD non trovati: "
                f"relative scritture saltate."
            )
        if res['invalid_cols']:
            logger.warning(
                f"[Filone Bulk] ⚠️ Colonne non trovate sul foglio: {res['invalid_cols']}"
            )

        # Rilevamento appuntamenti (per tutte le righe modificate)
        try:
            for row_num_str, fields in rows_dict.items():
                try:
                    row_num = int(row_num_str)
                except (ValueError, TypeError):
                    continue
                orario_trovato = None
                nota_salvata = ''

                for col_name, value in fields.items():
                    val_str = str(value).strip()
                    if not val_str:
                        continue
                    col_upper = col_name.upper().strip()

                    # Cerca "APP HH:MM" in NOTE APP / NOTE (logica unificata)
                    if col_upper in ('NOTE APP', 'NOTE', 'NOTE_APP'):
                        match_app = re.search(
                            r'APP\s+(\d{1,2}[:.]\d{2}(?:\s*[-/]\s*\d{1,2}[:.]\d{2})?)',
                            val_str, re.IGNORECASE
                        )
                        if match_app:
                            orario_trovato = match_app.group(1).replace('.', ':')
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

        logger.info(
            f"[Filone] ✅ Bulk update: {len(rows_ok)} righe, {res['written']} celle "
            f"(remapped={res['remapped']}, lost={len(res['lost_anchors'])}, "
            f"invalid_cols={res['invalid_cols']})"
        )

        # Trigger sync per propagare alla mappa (vedi update_filone_row)
        try:
            _callbacks['trigger_sync_after_edit']()
        except Exception:
            pass

        return jsonify({
            'success': True,
            'rows': len(rows_ok),
            'cells': res['written'],
            'skipped': res.get('skipped', 0),
            'remapped': res['remapped'],
            'invalid_cols': res['invalid_cols'],
            'lost_anchors': len(res['lost_anchors']),
        })

    except Exception as e:
        return _api_error(e, 'aggiornamento bulk filone')


@bp.route('/api/filone/append', methods=['POST'])
@login_required
def append_filone_rows():
    """Aggiunge nuove righe in fondo al FILONE (DB locale).

    Body JSON: { "rows": [ { "col_name": "value", ... }, ... ] }
    Ogni dict rappresenta una nuova riga; chiave = nome colonna esattamente
    come appare nel column_registry. Eneltel è obbligatorio (chiave primaria).

    Le righe vengono inserite direttamente in `filone` (campi tipati) e
    `filone_extra` (colonne libere). Il `last_local_change_ts` viene
    impostato così le righe entrano nell'outbox per l'export delta.
    """
    try:
        from moduli import db as _db

        data = request.json
        if not data or 'rows' not in data:
            return jsonify({'error': 'Formato non valido. Atteso: {"rows": [...]}'}), 400

        new_rows = data['rows']
        if not new_rows or not isinstance(new_rows, list):
            return jsonify({'error': 'Nessuna riga da aggiungere'}), 400
        if len(new_rows) > 5000:
            return jsonify({'error': 'Troppo righe (max 5000)'}), 400

        username = ((session.get('user') or {}).get('username')
                    or session.get('utente') or 'unknown')

        conn = _db.get_conn()
        added = 0
        skipped = []
        unknown_cols = set()
        max_sheets_row = conn.execute(
            "SELECT COALESCE(MAX(sheets_row), 1) AS m FROM filone"
        ).fetchone()['m']

        with conn:
            conn.execute('BEGIN IMMEDIATE')
            now = _db._now_ts()

            for idx, row_data in enumerate(new_rows):
                # Trova Eneltel o POD (case-insensitive sui nomi noti)
                eneltel_raw = None
                pod_raw = None
                for k, v in row_data.items():
                    if not k or k.startswith('_'):
                        continue
                    k_clean = k.strip().lower()
                    if k_clean in ('eneltel',):
                        eneltel_raw = v
                    elif k_clean in ('pod', 'codice_pod', 'punto_di_prelievo'):
                        pod_raw = v
                eneltel_key = _db.normalize_eneltel(eneltel_raw or pod_raw or '')
                if not eneltel_key:
                    skipped.append({'idx': idx, 'reason': 'POD o Eneltel mancante o non valido'})
                    continue

                # Lcl opzionale per disambiguare righe Pavia multi-LCL
                lcl = ''
                for k, v in row_data.items():
                    if k.strip().lower() == 'lcl':
                        lcl = str(v or '').strip()
                        break

                # Già esiste? salta
                exists = conn.execute(
                    "SELECT 1 FROM filone WHERE (eneltel_key=? OR (pod IS NOT NULL AND pod=?)) AND COALESCE(lcl,'')=?",
                    (eneltel_key, eneltel_key, lcl),
                ).fetchone()
                if exists:
                    skipped.append({
                        'idx': idx,
                        'reason': f'Contatore {eneltel_key} già presente'
                                  + (f' (LCL={lcl})' if lcl else ''),
                    })
                    continue

                # Smista i campi tipati vs extra
                filone_cols = {'lcl': lcl}
                extra_cols = {}
                for k, v in row_data.items():
                    if not k or k.startswith('_'):
                        continue
                    if k.strip().lower() == 'eneltel':
                        continue
                    chiave = _db.column_to_internal_key(conn, k)
                    val_str = '' if v is None else str(v).strip()
                    if chiave and chiave in _db.COLONNE_NOTE:
                        filone_cols[chiave] = val_str
                    elif chiave == 'eneltel':
                        continue
                    else:
                        # Colonna non riconosciuta: se è nel registry la mettiamo
                        # in filone_extra, altrimenti la segnaliamo
                        in_registry = conn.execute(
                            "SELECT 1 FROM column_registry WHERE LOWER(column_name)=?",
                            (k.strip().lower(),),
                        ).fetchone()
                        if in_registry:
                            extra_cols[_db.canonical_column_name(conn, k)] = val_str
                        else:
                            unknown_cols.add(k)

                max_sheets_row += 1
                # INSERT filone
                cols_sql = ['eneltel_key', 'sheets_row', 'row_hash',
                            'last_remote_pull_ts', 'last_local_change_ts', 'is_orphan']
                vals_sql = [eneltel_key, max_sheets_row,
                            _db.compute_row_hash(list(filone_cols.values())),
                            now, now, 0]
                for k, v in filone_cols.items():
                    cols_sql.append(k)
                    vals_sql.append(v)
                conn.execute(
                    f"INSERT INTO filone ({', '.join(cols_sql)}) "
                    f"VALUES ({', '.join('?' * len(cols_sql))})",
                    vals_sql,
                )
                # INSERT filone_extra
                for col_name, val in extra_cols.items():
                    conn.execute(
                        "INSERT OR REPLACE INTO filone_extra(eneltel_key, lcl, column_name, value) "
                        "VALUES (?, ?, ?, ?)",
                        (eneltel_key, lcl, col_name, val),
                    )
                # Audit
                _db.audit(conn, eneltel_key=eneltel_key, column_name=None,
                          old_value=None, new_value=None,
                          source='local', user=username,
                          detail={'action': 'append_row', 'lcl': lcl,
                                  'sheets_row': max_sheets_row,
                                  'n_extra': len(extra_cols)})
                # Pending writes per outbox (1 per ogni campo non vuoto)
                for col_name, val in {**{('eneltel'): eneltel_raw},
                                       **filone_cols,
                                       **extra_cols}.items():
                    if not val or not str(val).strip():
                        continue
                    # Risolvi il nome canonico per la pending
                    if col_name in ('eneltel',):
                        col_canonical = _db.canonical_column_name(conn, 'Eneltel')
                    elif col_name in extra_cols:
                        col_canonical = col_name
                    else:
                        # nome tipato → trova nome del registry
                        reg = conn.execute(
                            "SELECT column_name FROM column_registry WHERE chiave_interna=? LIMIT 1",
                            (col_name,),
                        ).fetchone()
                        col_canonical = reg['column_name'] if reg else col_name
                    conn.execute(
                        "INSERT INTO pending_writes(eneltel_key, lcl, column_name, new_value, "
                        "base_value, created_ts, user, origin, state) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (eneltel_key, lcl, col_canonical, str(val),
                         '', now, username, 'append_row', 'pending'),
                    )
                added += 1

        # Trigger sync per rigenerare mappa
        try:
            _callbacks['trigger_sync_after_edit']()
        except Exception:
            pass

        logger.info(
            f"[Filone] ✅ Append DB: {added} righe aggiunte, "
            f"{len(skipped)} saltate, {len(unknown_cols)} colonne ignorate"
        )
        return jsonify({
            'success': True,
            'rows_added': added,
            'rows_skipped': skipped,
            'colonne_ignorate': sorted(unknown_cols),
        })

    except Exception as e:
        return _api_error(e, 'aggiunta righe filone')


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
    from moduli.percorso import ordina_percorso
    from moduli.fasce_orarie import (
        assegna_fasce,
        consolida_fasce_per_indirizzo,
        formatta_fascia_oraria,
        FASCE_ORARIE,
    )
    from moduli.data_loader import carica_dati_db_locale
    from genera_excel_lettere import genera_excel_lettere as _genera_excel

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
    logger.info("📬 GENERAZIONE ORARI LETTERE")
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
        df = carica_dati_db_locale()
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

            import config
            disabled_ops = {str(x).strip().upper() for x in getattr(config, 'OPERATORI_DISABILITATI', []) if str(x).strip()}
            tecnici_data = [t for t in MATRICOLE_VIP if t in df_data['TEC_K'].unique() and str(t).strip().upper() not in disabled_ops]
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

                # Consolida fasce per indirizzo (allinea contatori con stesso
                # comune+localita+via+civico ma fascia diversa).
                tappe_con_fasce = consolida_fasce_per_indirizzo(tappe_con_fasce)

                for punto, fascia in tappe_con_fasce:
                    pod = str(punto.get('POD_K', '')).strip()
                    if pod:
                        fasce_per_pod[pod] = formatta_fascia_oraria(fascia)

        logger.info(f"   🕐 Fasce calcolate per {len(fasce_per_pod)} contatori (per POD)")

        if not fasce_per_pod:
            return jsonify({'error': 'Nessuna fascia oraria calcolata'}), 500

        # Ora scrivi nel FILONE — TUTTE le letture/scritture avvengono dentro
        # il write-lock con header rigenerati per evitare di scrivere in
        # colonne sbagliate se la collega modifica il foglio in parallelo.
        scritti = 0
        saltati_occupati = 0
        saltati_non_trovati = 0
        col_dest_name = None
        with _gsheets_write_lock:
            header_map = force_refresh_headers()

            for nome in ['Orari', 'ORARI']:
                if nome in header_map:
                    col_dest_name = nome
                    break
            if col_dest_name is None:
                return jsonify({'error': "Colonna 'Orari' non trovata nel FILONE. Crea prima la colonna."}), 400

            pod_anchor_col = _resolve_anchor_col_name('pod')
            if pod_anchor_col is None:
                return jsonify({'error': 'Colonna POD non trovata nel FILONE'}), 400

            col_pod_idx = header_map[pod_anchor_col]
            col_dest_idx = header_map[col_dest_name]
            # LCL serve come ancora secondaria per disambiguare i POD duplicati
            # (stesso POD su 2 righe diverse del foglio, frequente su Pavia).
            col_lcl_idx = header_map.get('LCL')

            ws = get_worksheet()
            all_values = _sheets_retry(ws.get_all_values)

            items = []
            for row_idx, row_values in enumerate(all_values[1:], start=2):
                pod_val = ''
                if col_pod_idx - 1 < len(row_values):
                    pod_val = str(row_values[col_pod_idx - 1]).strip().upper()
                if not pod_val:
                    continue

                fascia_trovata = None
                for pod_calc, fascia in fasce_per_pod.items():
                    if str(pod_calc).strip().upper() == pod_val:
                        fascia_trovata = fascia
                        break
                if fascia_trovata is None:
                    saltati_non_trovati += 1
                    continue

                current_val = ''
                if col_dest_idx - 1 < len(row_values):
                    current_val = str(row_values[col_dest_idx - 1]).strip()
                if current_val and current_val.upper() not in ('', 'NAN', 'NONE', 'NULL'):
                    saltati_occupati += 1
                    continue

                # LCL della riga del foglio: ancora secondaria per identificare
                # univocamente la riga in caso di POD duplicato (es. Pavia).
                lcl_val = ''
                if col_lcl_idx is not None and col_lcl_idx - 1 < len(row_values):
                    lcl_val = str(row_values[col_lcl_idx - 1] or '').strip()

                items.append({
                    'row': row_idx,
                    'col': col_dest_name,
                    'value': fascia_trovata,
                    'anchor': pod_val,
                    'anchor_lcl': lcl_val,
                })
                scritti += 1

        # safe_batch_update_by_name acquisirà di nuovo il write-lock
        # (è una RLock, quindi rientrante sicura) e ricontrollerà gli header.
        # In modalità sola lettura ritorna read_only=True e written=0: l'Excel
        # viene comunque generato sotto.
        read_only_attivo = False
        if items:
            res = safe_batch_update_by_name(
                items, anchor_col=pod_anchor_col, anchor_normalize='upper',
            )
            scritti = res['written']
            read_only_attivo = bool(res.get('read_only'))
            if res['lost_anchors']:
                logger.warning(
                    f"[Orari Lettere] {len(res['lost_anchors'])} righe POD non più presenti, saltate."
                )
            if res['remapped']:
                logger.warning(
                    f"[Orari Lettere] {res['remapped']} righe rimappate (foglio riordinato)."
                )

        # Genera SEMPRE il file Excel lettere — anche in modalità sola lettura,
        # perché il file su disco non è una scrittura verso Sheets.
        file_excel = None
        errore_excel = ''
        try:
            logger.info("📄 Generazione file Excel lettere...")
            file_excel = _genera_excel(
                df,
                date_da_elaborare,
                tappe_per_fascia='auto' if auto_mode else tappe_per_fascia_fisso,
                utente=_get_username(),
            )
            if file_excel:
                logger.info(f"   ✅ Excel generato: {file_excel}")
        except Exception as ex_excel:
            errore_excel = str(ex_excel)
            logger.warning(f"   ⚠️ Errore generazione Excel: {ex_excel}")

        generazione_lettere = None
        if file_excel:
            try:
                from moduli.lettere import get_generazione_by_file
                generazione_lettere = get_generazione_by_file(os.path.basename(file_excel))
            except Exception as ex_gen:
                logger.warning(f"[Orari Lettere] Archivio generazione non letto: {ex_gen}")

        if read_only_attivo:
            msg = ('Excel generato. Modalità sola lettura attiva: '
                   'gli orari NON sono stati scritti sul foglio.')
        elif errore_excel:
            msg = (
                'Orari lettere calcolati, ma il file Excel non è stato archiviato: '
                f'{errore_excel}'
            )
        else:
            msg = 'Orari lettere generati con successo'
        risultato = {
            'success': True,
            'read_only': read_only_attivo,
            'message': msg,
            'dettagli': {
                'periodo': f'{data_da} → {data_a}',
                'giorni': len(date_da_elaborare),
                'fasce_calcolate': len(fasce_per_pod),
                'scritti': scritti,
                'saltati_occupati': saltati_occupati,
                'saltati_non_trovati': saltati_non_trovati,
                'colonna': col_dest_name,
                'file_excel': os.path.basename(file_excel) if file_excel else '',
                'errore_excel': errore_excel,
                'generazione_lettere_id': (
                    generazione_lettere.get('id') if generazione_lettere else None
                ),
            }
        }

        logger.info("✅ Risultato:")
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

    # Cerca nella cartella dedicata output/lettere/
    project_root = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
    filepath = os.path.join(project_root, 'output', 'lettere', safe_name)
    filepath = os.path.normpath(filepath)
    # Fallback: cerca anche nella root del progetto (vecchi file)
    if not os.path.exists(filepath):
        filepath = os.path.join(project_root, safe_name)
        filepath = os.path.normpath(filepath)
    if not os.path.exists(filepath):
        return jsonify({'error': 'File non trovato'}), 404

    return send_file(
        filepath,
        as_attachment=True,
        download_name=safe_name,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


# ============================================================
# CELL CHANGE LOG — storico modifiche celle filone
# ============================================================

@bp.route('/api/filone/log', methods=['POST'])
@login_required
def save_cell_log():
    """No-op: l'audit ora è registrato automaticamente in `audit_log` del DB
    al momento del `db.record_local_change()` dentro le route di scrittura.

    L'endpoint resta per retro-compatibilità con il frontend filone.html che
    invia ancora i changes — la response è 200 e basta, niente viene scritto
    su file.
    """
    try:
        data = request.json or {}
        changes = data.get('changes', [])
        return jsonify({'ok': True, 'logged': len(changes), 'note': 'audit handled by DB'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@bp.route('/api/filone/log', methods=['GET'])
@login_required
def get_cell_log():
    """Restituisce lo storico modifiche per una cella specifica.

    Legge da `audit_log` del DB locale (fonte di verità unica dalla Fase 3).
    Risolve `row` (sheets_row del foglio) → `eneltel_key` tramite la tabella
    `filone`.

    Query params:
      - row (int, opzionale): filtra per riga del foglio
      - col (str, opzionale): filtra per nome colonna
      - limit (int, default 50, max 200): numero massimo di risultati
    """
    try:
        from moduli import db as _db
        row_filter = request.args.get('row', type=int)
        col_filter = request.args.get('col', '').strip()
        limit = min(request.args.get('limit', 50, type=int), 200)

        conn = _db.get_conn()

        # Risolvi row → eneltel_key (può ritornare multipli se schema v2 multi-LCL)
        eneltel_keys = []
        if row_filter is not None:
            cur = conn.execute(
                "SELECT eneltel_key FROM filone WHERE sheets_row=?",
                (row_filter,),
            )
            eneltel_keys = [r['eneltel_key'] for r in cur.fetchall()]
            if not eneltel_keys:
                return jsonify({'history': []})

        # Query audit_log con filtri
        sql = "SELECT ts, eneltel_key, column_name, old_value, new_value, user, source FROM audit_log"
        params = []
        conds = []
        if eneltel_keys:
            placeholders = ','.join('?' * len(eneltel_keys))
            conds.append(f"eneltel_key IN ({placeholders})")
            params.extend(eneltel_keys)
        if col_filter:
            conds.append("LOWER(column_name)=LOWER(?)")
            params.append(col_filter)
        # Solo le modifiche con valore reale cambiato (filtra rumore audit duplicati)
        conds.append("(old_value IS NOT new_value)")
        conds.append("source IN ('local','remote','conflict_local_wins')")
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)

        results = []
        for r in conn.execute(sql, params):
            ts_str = datetime.fromtimestamp(r['ts']).strftime('%Y-%m-%d %H:%M:%S')
            results.append({
                'ts': ts_str,
                'user': r['user'] or '',
                'row': row_filter,  # preservato per il frontend
                'col': r['column_name'] or '',
                'old': r['old_value'] or '',
                'new': r['new_value'] or '',
                'source': r['source'],
            })

        return jsonify({'history': results})
    except Exception as e:
        return _api_error(e, 'lettura log celle')


@bp.route('/api/contatori/check-inactive-lcl', methods=['POST'])
@login_required
def check_inactive_lcl():
    """Controlla se i parametri di ricerca (POD, Eneltel, Nominativo)
    corrispondono a contatori di LCL disabilitate senza alcun duplicato attivo.

    Payload JSON:
      - pod_tokens: list[str] (opzionale, per ricerca multipla POD)
      - eneltel_tokens: list[str] (opzionale, per ricerca multipla Eneltel)
      - pod: str (opzionale)
      - eneltel: str (opzionale)
      - nominativo: str (opzionale)
    """
    try:
        from moduli import db as _db
        import config

        data = request.get_json(silent=True) or {}
        disabled_lcls = getattr(config, 'LCL_DISABILITATI', [])
        if not disabled_lcls:
            return jsonify({'inactive_found': [], 'total_inactive': 0})

        pod_list = data.get('pod_tokens') or []
        if not isinstance(pod_list, list):
            pod_list = []
        if data.get('pod'):
            pod_list.append(str(data['pod']))

        eneltel_list = data.get('eneltel_tokens') or []
        if not isinstance(eneltel_list, list):
            eneltel_list = []
        if data.get('eneltel'):
            eneltel_list.append(str(data['eneltel']))

        nominativo = data.get('nominativo')

        found = _db.find_inactive_lcl_contatori(
            pod_list=pod_list,
            eneltel_list=eneltel_list,
            nominativo=nominativo,
            disabled_lcls=disabled_lcls,
            limit=50
        )

        return jsonify({
            'inactive_found': found,
            'total_inactive': len(found),
        })
    except Exception as e:
        return _api_error(e, 'controllo LCL disabilitate')

