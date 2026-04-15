# -*- coding: utf-8 -*-
"""
Route Report — statistiche e reportistica operatori.
Aggrega i dati dai backup giornalieri del filone.
"""
import io
import os
import json
import re
import logging
from datetime import datetime, date, timedelta
from collections import Counter, defaultdict

from flask import Blueprint, jsonify, redirect, request, send_file
from moduli.auth import login_required
from moduli.safe_json import json_read
from moduli.data_loader import parse_data_flessibile
from config import PROVINCIA_ATTIVA, MATRICOLE_VIP, COLORI_TECNICI, OPERATORI_DICT

logger = logging.getLogger('mappa_algeco')

bp = Blueprint('report', __name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP_DIR = os.path.join(BASE_DIR, 'backup', PROVINCIA_ATTIVA.lower())
CACHE_DIR = os.path.join(BASE_DIR, 'cache')

# ── helpers ────────────────────────────────────────────────

_NOME_DA_MATRICOLA = {m: n for n, m in OPERATORI_DICT.items()}


def _col_index(headers, nomi):
    lower_map = {h.lower().strip(): i for i, h in enumerate(headers)}
    for n in nomi:
        idx = lower_map.get(n.lower().strip())
        if idx is not None:
            return idx
    return None


def _cell(row, idx):
    if idx is None or idx >= len(row):
        return ''
    return str(row[idx]).strip()


def _parse_data(val):
    val = str(val).strip()
    if not val or len(val) < 6 or 'annullat' in val.lower():
        return None
    val_clean = re.split(r'\s+', val)[0]
    return parse_data_flessibile(val_clean)


def _carica_backup(data_str=None):
    """Carica un backup specifico o l'ultimo disponibile."""
    if not os.path.isdir(BACKUP_DIR):
        return None
    files = sorted(f for f in os.listdir(BACKUP_DIR) if f.endswith('.json'))
    if not files:
        return None
    if data_str:
        target = f'filone_{PROVINCIA_ATTIVA.lower()}_{data_str}.json'
        if target in files:
            path = os.path.join(BACKUP_DIR, target)
        else:
            return None
    else:
        path = os.path.join(BACKUP_DIR, files[-1])
    return json_read(path, default=None)


def _date_range(da, a):
    """Genera lista date YYYY-MM-DD tra da e a inclusi."""
    result = []
    d = da
    while d <= a:
        result.append(d.isoformat())
        d += timedelta(days=1)
    return result


def _carica_cache_distanze():
    """Carica la cache distanze percorsi per calcolo km/durata."""
    fname = f'cache_distanze_{PROVINCIA_ATTIVA.lower()}.json'
    path = os.path.join(CACHE_DIR, fname)
    return json_read(path, default={})


# ── calcolo report principale ──────────────────────────────

def _calcola_report(backup, data_da, data_a):
    """
    Calcola le statistiche per un range di date dal backup.
    Restituisce dati aggregati per operatore, per giorno, per esito, per LCL.
    """
    headers = backup.get('headers', [])
    righe = backup.get('righe', [])

    i_lcl = _col_index(headers, ['LCL'])
    i_data = _col_index(headers, ['DATA PIAN', 'DATA_PIAN'])
    i_operatore = _col_index(headers, ['Operatore', 'OPERATORE'])
    i_esito = _col_index(headers, ['Esito', 'ESITO'])
    i_note = _col_index(headers, ['NOTE APP', 'NOTE_APP'])
    i_comune = _col_index(headers, ['Comune', 'COMUNE'])
    i_data_inst = _col_index(headers, ['Data Installazione', 'DATA INSTALLAZIONE'])
    i_tipo_mis = _col_index(headers, ['Tipo_Misuratore', 'TIPO_MISURATORE'])

    app_re = re.compile(r'\bapp\b', re.IGNORECASE)

    # Strutture dati
    per_operatore = defaultdict(lambda: {
        'installati': 0,
        'pianificati': 0,
        'appuntamenti': 0,
        'esiti': Counter(),
        'per_giorno': Counter(),         # data -> installati
        'per_giorno_pian': Counter(),     # data -> pianificati
        'comuni': Counter(),
        'lcl': Counter(),
        'tipi_mis': Counter(),
    })
    per_giorno_totale = Counter()       # data -> installati totali
    per_giorno_pian = Counter()         # data -> pianificati totali
    esiti_globali = Counter()
    per_lcl = defaultdict(lambda: {'installati': 0, 'pianificati': 0})
    comuni_globali = Counter()

    da_str = data_da.isoformat()
    a_str = data_a.isoformat()

    for row in righe:
        data_raw = _cell(row, i_data)
        operatore = _cell(row, i_operatore)
        esito = _cell(row, i_esito)
        note = _cell(row, i_note)
        comune = _cell(row, i_comune)
        data_inst_raw = _cell(row, i_data_inst)
        lcl_val = _cell(row, i_lcl)
        tipo_mis = _cell(row, i_tipo_mis)

        if data_raw and 'annullat' in data_raw.lower():
            continue

        data_pian = _parse_data(data_raw)
        is_installato = bool(esito and 'ok' in esito.lower())
        is_appuntamento = bool(note and app_re.search(note))

        # Pianificati nel range
        if data_pian and da_str <= data_pian <= a_str:
            per_giorno_pian[data_pian] += 1
            if lcl_val:
                per_lcl[lcl_val]['pianificati'] += 1
            if operatore and operatore != 'TUTTI':
                per_operatore[operatore]['pianificati'] += 1
                per_operatore[operatore]['per_giorno_pian'][data_pian] += 1
                if is_appuntamento:
                    per_operatore[operatore]['appuntamenti'] += 1
                if comune:
                    per_operatore[operatore]['comuni'][comune.upper()] += 1
                    comuni_globali[comune.upper()] += 1

        # Installati nel range (usa data installazione se disponibile)
        if is_installato:
            d_inst = _parse_data(data_inst_raw) if data_inst_raw else data_pian
            if d_inst and da_str <= d_inst <= a_str:
                per_giorno_totale[d_inst] += 1
                if lcl_val:
                    per_lcl[lcl_val]['installati'] += 1
                # Conta esito reale
                esiti_globali[esito.strip()] += 1
                if operatore and operatore != 'TUTTI':
                    per_operatore[operatore]['installati'] += 1
                    per_operatore[operatore]['per_giorno'][d_inst] += 1
                    per_operatore[operatore]['esiti'][esito.strip()] += 1
                    if lcl_val:
                        per_operatore[operatore]['lcl'][lcl_val] += 1
                    if tipo_mis:
                        per_operatore[operatore]['tipi_mis'][tipo_mis] += 1

        # Esiti NON ok (KO, assente, ecc.) nel range
        if esito and 'ok' not in esito.lower() and esito.strip():
            d_esito = _parse_data(data_inst_raw) if data_inst_raw else data_pian
            if d_esito and da_str <= d_esito <= a_str:
                esiti_globali[esito.strip()] += 1
                if operatore and operatore != 'TUTTI':
                    per_operatore[operatore]['esiti'][esito.strip()] += 1

    # ── Distanze dalla cache ──
    cache_dist = _carica_cache_distanze()
    distanze_op = {}  # matricola -> {km_totale, durata_totale, giorni}
    for key, val in cache_dist.items():
        if not isinstance(val, dict):
            continue
        parts = key.rsplit('_', 1)
        if len(parts) != 2:
            continue
        d_cache, op_cache = parts[0], parts[1]
        if da_str <= d_cache <= a_str and op_cache in MATRICOLE_VIP:
            if op_cache not in distanze_op:
                distanze_op[op_cache] = {'km': 0, 'min': 0, 'giorni': 0, 'tappe': 0}
            distanze_op[op_cache]['km'] += val.get('distanza_totale_km', 0)
            distanze_op[op_cache]['min'] += val.get('durata_guida_min', 0)
            distanze_op[op_cache]['giorni'] += 1
            distanze_op[op_cache]['tappe'] += val.get('num_tappe', 0)

    # ── Costruisci operatori output ──
    date_range = _date_range(data_da, data_a)
    operatori_out = []
    for matricola in MATRICOLE_VIP:
        stats = per_operatore[matricola]
        dist = distanze_op.get(matricola, {})
        giorni_lavorati = len([d for d in date_range if stats['per_giorno'].get(d, 0) > 0])
        media_giorno = round(stats['installati'] / giorni_lavorati, 1) if giorni_lavorati else 0

        # Serie giornaliera (per grafico)
        serie_inst = [stats['per_giorno'].get(d, 0) for d in date_range]
        serie_pian = [stats['per_giorno_pian'].get(d, 0) for d in date_range]

        operatori_out.append({
            'matricola': matricola,
            'nome': _NOME_DA_MATRICOLA.get(matricola, matricola),
            'colore': COLORI_TECNICI.get(matricola, '#888'),
            'installati': stats['installati'],
            'pianificati': stats['pianificati'],
            'appuntamenti': stats['appuntamenti'],
            'giorni_lavorati': giorni_lavorati,
            'media_giorno': media_giorno,
            'esiti': dict(stats['esiti'].most_common(10)),
            'top_comuni': [{'comune': c, 'count': n} for c, n in stats['comuni'].most_common(8)],
            'top_lcl': dict(stats['lcl'].most_common()),
            'tipi_misuratore': dict(stats['tipi_mis'].most_common()),
            'km_totali': round(dist.get('km', 0), 1),
            'minuti_guida': round(dist.get('min', 0)),
            'tappe_percorso': dist.get('tappe', 0),
            'serie_installati': serie_inst,
            'serie_pianificati': serie_pian,
        })

    # Serie totali
    serie_totale_inst = [per_giorno_totale.get(d, 0) for d in date_range]
    serie_totale_pian = [per_giorno_pian.get(d, 0) for d in date_range]

    # LCL output
    lcl_out = [{'codice': k, **v} for k, v in sorted(per_lcl.items())]

    return {
        'data_da': data_da.isoformat(),
        'data_a': data_a.isoformat(),
        'date_labels': date_range,
        'provincia': PROVINCIA_ATTIVA,
        'operatori': operatori_out,
        'serie_installati': serie_totale_inst,
        'serie_pianificati': serie_totale_pian,
        'installati_totale': sum(serie_totale_inst),
        'pianificati_totale': sum(serie_totale_pian),
        'esiti': dict(esiti_globali.most_common(20)),
        'top_comuni': [{'comune': c, 'count': n} for c, n in comuni_globali.most_common(15)],
        'lcl': lcl_out,
    }


# ── Routes ─────────────────────────────────────────────────

@bp.route('/report')
@login_required
def report_page():
    return redirect('/dashboard')


@bp.route('/api/report', methods=['GET'])
@login_required
def api_report():
    """
    GET /api/report?periodo=settimana|mese|custom&da=YYYY-MM-DD&a=YYYY-MM-DD
    """
    try:
        periodo = request.args.get('periodo', 'settimana')
        oggi = date.today()

        if periodo == 'mese':
            data_da = oggi.replace(day=1)
            data_a = oggi
        elif periodo == 'custom':
            da_str = request.args.get('da', '')
            a_str = request.args.get('a', '')
            if not da_str or not a_str:
                return jsonify({'error': 'Specificare da e a per periodo custom'}), 400
            data_da = date.fromisoformat(da_str)
            data_a = date.fromisoformat(a_str)
            if (data_a - data_da).days > 366:
                return jsonify({'error': 'Range massimo 1 anno'}), 400
        else:  # settimana
            data_da = oggi - timedelta(days=oggi.weekday())
            data_a = oggi

        backup = _carica_backup()
        if not backup:
            return jsonify({'error': 'Nessun backup disponibile'}), 404

        report = _calcola_report(backup, data_da, data_a)
        report['success'] = True
        return jsonify(report)

    except ValueError as e:
        return jsonify({'error': f'Formato data non valido: {e}'}), 400
    except Exception as e:
        logger.error(f"[Report] Errore: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Errore calcolo report: {e}'}), 500


@bp.route('/api/report/export', methods=['GET'])
@login_required
def api_report_export():
    """
    GET /api/report/export?periodo=settimana|mese|custom&da=...&a=...&formato=excel
    Genera e scarica un file Excel con il report.
    """
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter

        periodo = request.args.get('periodo', 'settimana')
        oggi = date.today()

        if periodo == 'mese':
            data_da = oggi.replace(day=1)
            data_a = oggi
        elif periodo == 'custom':
            da_str = request.args.get('da', '')
            a_str = request.args.get('a', '')
            data_da = date.fromisoformat(da_str)
            data_a = date.fromisoformat(a_str)
            if (data_a - data_da).days > 366:
                return jsonify({'error': 'Range massimo 1 anno'}), 400
        else:
            data_da = oggi - timedelta(days=oggi.weekday())
            data_a = oggi

        backup = _carica_backup()
        if not backup:
            return jsonify({'error': 'Nessun backup disponibile'}), 404

        report = _calcola_report(backup, data_da, data_a)

        # ── Crea workbook Excel ──
        wb = openpyxl.Workbook()

        # Stili
        header_font = Font(bold=True, size=11, color='FFFFFF')
        header_fill = PatternFill(start_color='2E7D32', end_color='2E7D32', fill_type='solid')
        title_font = Font(bold=True, size=14)
        subtitle_font = Font(bold=True, size=11, color='555555')
        thin_border = Border(
            left=Side(style='thin'), right=Side(style='thin'),
            top=Side(style='thin'), bottom=Side(style='thin')
        )

        def style_header_row(ws, row_num, max_col):
            for col in range(1, max_col + 1):
                cell = ws.cell(row=row_num, column=col)
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = Alignment(horizontal='center')
                cell.border = thin_border

        # ─── FOGLIO 1: Riepilogo ───
        ws1 = wb.active
        ws1.title = 'Riepilogo'
        ws1.cell(1, 1, f'Report Operatori — {PROVINCIA_ATTIVA}').font = title_font
        ws1.cell(2, 1, f'Periodo: {data_da.strftime("%d/%m/%Y")} — {data_a.strftime("%d/%m/%Y")}').font = subtitle_font
        ws1.cell(3, 1, f'Generato il: {datetime.now().strftime("%d/%m/%Y %H:%M")}').font = subtitle_font

        # Tabella riepilogo operatori
        row = 5
        riepilogo_headers = [
            'Operatore', 'Matricola', 'Installati', 'Pianificati', 'Appuntamenti',
            'Giorni Lavorati', 'Media/Giorno', 'Km Totali', 'Min. Guida', 'Tappe Percorso',
        ]
        for c, h in enumerate(riepilogo_headers, 1):
            ws1.cell(row, c, h)
        style_header_row(ws1, row, len(riepilogo_headers))

        for op in report['operatori']:
            row += 1
            ws1.cell(row, 1, op['nome'])
            ws1.cell(row, 2, op['matricola'])
            ws1.cell(row, 3, op['installati'])
            ws1.cell(row, 4, op['pianificati'])
            ws1.cell(row, 5, op['appuntamenti'])
            ws1.cell(row, 6, op['giorni_lavorati'])
            ws1.cell(row, 7, op['media_giorno'])
            ws1.cell(row, 8, op['km_totali'])
            ws1.cell(row, 9, op['minuti_guida'])
            ws1.cell(row, 10, op['tappe_percorso'])
            for c in range(1, 11):
                ws1.cell(row, c).border = thin_border

        # Totali
        row += 1
        ws1.cell(row, 1, 'TOTALE').font = Font(bold=True)
        ws1.cell(row, 3, report['installati_totale']).font = Font(bold=True)
        ws1.cell(row, 4, report['pianificati_totale']).font = Font(bold=True)

        # Auto-width
        for col in range(1, len(riepilogo_headers) + 1):
            ws1.column_dimensions[get_column_letter(col)].width = 16

        # ─── FOGLIO 2: Dettaglio Giornaliero ───
        ws2 = wb.create_sheet('Dettaglio Giornaliero')
        dates = report['date_labels']

        # Header: Operatore + date
        ws2.cell(1, 1, 'Operatore')
        for c, d in enumerate(dates, 2):
            try:
                dt = date.fromisoformat(d)
                ws2.cell(1, c, dt.strftime('%d/%m'))
            except Exception:
                ws2.cell(1, c, d)
        ws2.cell(1, len(dates) + 2, 'Totale')
        style_header_row(ws2, 1, len(dates) + 2)

        row = 2
        for op in report['operatori']:
            ws2.cell(row, 1, op['nome']).font = Font(bold=True)
            for c, val in enumerate(op['serie_installati'], 2):
                cell = ws2.cell(row, c, val)
                cell.border = thin_border
                if val > 0:
                    cell.font = Font(bold=True)
            ws2.cell(row, len(dates) + 2, op['installati']).font = Font(bold=True)
            row += 1

        # Riga totali
        ws2.cell(row, 1, 'TOTALE').font = Font(bold=True)
        for c, val in enumerate(report['serie_installati'], 2):
            ws2.cell(row, c, val).font = Font(bold=True)
        ws2.cell(row, len(dates) + 2, report['installati_totale']).font = Font(bold=True)

        ws2.column_dimensions['A'].width = 16
        for c in range(2, len(dates) + 3):
            ws2.column_dimensions[get_column_letter(c)].width = 8

        # ─── FOGLIO 3: Esiti ───
        ws3 = wb.create_sheet('Esiti')
        ws3.cell(1, 1, 'Esito')
        ws3.cell(1, 2, 'Conteggio')
        style_header_row(ws3, 1, 2)
        row = 2
        for esito, count in sorted(report['esiti'].items(), key=lambda x: -x[1]):
            ws3.cell(row, 1, esito)
            ws3.cell(row, 2, count)
            row += 1
        ws3.column_dimensions['A'].width = 45
        ws3.column_dimensions['B'].width = 12

        # ─── FOGLIO 4: Comuni ───
        ws4 = wb.create_sheet('Comuni')
        ws4.cell(1, 1, 'Comune')
        ws4.cell(1, 2, 'Contatori pianificati')
        style_header_row(ws4, 1, 2)
        row = 2
        for c_item in report['top_comuni']:
            ws4.cell(row, 1, c_item['comune'])
            ws4.cell(row, 2, c_item['count'])
            row += 1
        ws4.column_dimensions['A'].width = 25
        ws4.column_dimensions['B'].width = 20

        # ─── FOGLIO 5: Dati Filone Completo (se richiesto) ───
        completo = request.args.get('completo', '0') == '1'
        if completo:
            headers = backup.get('headers', [])
            righe = backup.get('righe', [])
            ws5 = wb.create_sheet('Dati Filone Completo')
            # Header
            for c, h in enumerate(headers, 1):
                ws5.cell(1, c, h)
            style_header_row(ws5, 1, len(headers))
            # Dati
            for r_idx, riga in enumerate(righe, 2):
                for c_idx, val in enumerate(riga, 1):
                    ws5.cell(r_idx, c_idx, val)
            # Auto-width (limita a max 30 per performance)
            for c in range(1, min(len(headers) + 1, 31)):
                ws5.column_dimensions[get_column_letter(c)].width = 16

            # ─── FOGLIO 6: LCL Dettaglio ───
            lcl_data = report.get('lcl', [])
            if lcl_data:
                ws6 = wb.create_sheet('LCL')
                ws6.cell(1, 1, 'Codice LCL')
                ws6.cell(1, 2, 'Installati')
                ws6.cell(1, 3, 'Pianificati')
                style_header_row(ws6, 1, 3)
                for r_idx, l in enumerate(lcl_data, 2):
                    ws6.cell(r_idx, 1, l['codice'])
                    ws6.cell(r_idx, 2, l['installati'])
                    ws6.cell(r_idx, 3, l['pianificati'])
                ws6.column_dimensions['A'].width = 20
                ws6.column_dimensions['B'].width = 14
                ws6.column_dimensions['C'].width = 14

        # ─── Salva in buffer ───
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)

        tag = 'completo' if completo else 'report'
        filename = f'{tag}_{PROVINCIA_ATTIVA.lower()}_{data_da}_{data_a}.xlsx'
        return send_file(
            buf,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename,
        )

    except Exception as e:
        logger.error(f"[Report Export] Errore: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Errore export: {e}'}), 500
