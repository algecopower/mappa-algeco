# -*- coding: utf-8 -*-
"""
Blueprint Gestione Appuntamenti — vista dedicata per il monitoraggio degli
appuntamenti estratti dal filone (colonna note_appuntamento).

Fornisce:
- GET /appuntamenti           → pagina HTML
- GET /api/appuntamenti       → JSON con righe filtrate + statistiche aggregate
"""
import os
import re
import time
import logging
from datetime import date, timedelta, datetime
from collections import Counter

from flask import Blueprint, jsonify, request, send_file
from moduli.auth import login_required
from moduli import db as _db
from config import (
    PROVINCIA_ATTIVA, MATRICOLE_VIP, COLORI_TECNICI, OPERATORI_DICT,
)
from moduli.appuntamenti_core import rileva_appuntamento

logger = logging.getLogger('mappa_algeco')

bp = Blueprint('appuntamenti', __name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


from moduli.percorso import estrai_orario

# Pre-compilato per parsing data rapido (copiato da dashboard).
_DATE_RE = re.compile(r'^(\d{1,4})[/\-.]+(\d{1,2})[/\-.]+(\d{1,4})$')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _parse_data(val: str) -> str | None:
    """Converte una data grezza in YYYY-MM-DD (best-effort).

    Stessa logica della dashboard per coerenza nei risultati.
    """
    if not val:
        return None
    val = val.strip()
    if len(val) < 6 or 'annullat' in val.lower():
        return None
    sp = val.find(' ')
    val_clean = val[:sp] if sp != -1 else val
    m = _DATE_RE.match(val_clean)
    if not m:
        return None
    a, b, c = m.groups()
    if len(a) == 4:
        y, mo, d = a, b, c
    elif len(c) == 4:
        d, mo, y = a, b, c
    elif len(c) == 2:
        d, mo, y = a, b, '20' + c
    else:
        return None
    try:
        di, moi, yi = int(d), int(mo), int(y)
        if not (1 <= di <= 31 and 1 <= moi <= 12 and 1900 <= yi <= 2999):
            return None
    except ValueError:
        return None
    return f"{yi:04d}-{moi:02d}-{di:02d}"



def _nome_operatore(matricola: str) -> str:
    """Ritorna il nome leggibile dell'operatore dalla matricola."""
    for nome, matr in OPERATORI_DICT.items():
        if matr == matricola:
            return nome
    return matricola


# ---------------------------------------------------------------------------
# Cache leggera (stile dashboard)
# ---------------------------------------------------------------------------
_CACHE: dict[tuple, tuple[float, dict]] = {}
_CACHE_TTL_S = 5.0


def _db_data_version() -> int:
    try:
        row = _db.get_conn().execute('PRAGMA data_version').fetchone()
        return row[0] if row else 0
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Lettura dati dal DB
# ---------------------------------------------------------------------------
def _carica_appuntamenti() -> list[dict]:
    """Legge dal DB locale tutte le righe con appuntamento rilevato.

    Applica overlay pending_writes per coerenza con la dashboard.
    Ritorna lista di dict con tutti i campi utili.
    """
    conn = _db.get_conn()

    # 1. Righe con appuntamento (filtra in Python: la regex SQLite non
    #    supporta word boundary, e il volume è gestibile ~22k righe).
    sql = """
        SELECT eneltel_key, lcl, pod, nominativo, indirizzo, civico,
               comune, localita, telefono, email, operatore,
               data_pianificata, note_appuntamento, orari_lettere, esito,
               data_installazione, sheets_row
        FROM filone
        WHERE is_orphan = 0
        ORDER BY sheets_row
    """
    rows = conn.execute(sql).fetchall()

    # 2. Pending overlay per le colonne che ci interessano.
    pending_cols = [
        'NOTE APP', 'DATA PIAN', 'Operatore', 'Esito',
        'Data Installazione', 'NOMINATIVO', 'telefono', 'email',
        'Comune', 'Indirizzo', 'Num', 'Orari',
    ]
    placeholders = ','.join('?' for _ in pending_cols)
    pending_by_key: dict[tuple, dict] = {}
    cur = conn.execute(
        "SELECT eneltel_key, COALESCE(lcl,'') AS lcl, column_name, new_value "
        "FROM pending_writes "
        f"WHERE state IN ('pending','in_flight') AND column_name IN ({placeholders})",
        pending_cols,
    )
    for r in cur:
        pending_by_key.setdefault(
            (r['eneltel_key'], r['lcl']), {}
        )[r['column_name']] = r['new_value']

    # Mappa colonna pending → chiave interna per overlay.
    _PENDING_TO_KEY = {
        'NOTE APP': 'note_appuntamento',
        'DATA PIAN': 'data_pianificata',
        'Operatore': 'operatore',
        'Esito': 'esito',
        'Data Installazione': 'data_installazione',
        'NOMINATIVO': 'nominativo',
        'telefono': 'telefono',
        'email': 'email',
        'Comune': 'comune',
        'Indirizzo': 'indirizzo',
        'Num': 'civico',
        'Orari': 'orari_lettere',
    }

    risultati = []
    for fr in rows:
        rec = dict(fr)
        ek = rec['eneltel_key']
        lcl_val = rec.get('lcl') or ''

        # Applica pending overlay
        pend = pending_by_key.get((ek, lcl_val))
        if pend:
            for col_pend, chiave in _PENDING_TO_KEY.items():
                if col_pend in pend:
                    val = pend[col_pend]
                    rec[chiave] = '' if val is None else str(val)

        # Filtra: è un appuntamento?
        note = (rec.get('note_appuntamento') or '').strip()
        if not rileva_appuntamento(note):
            continue

        # Parsing data
        data_raw = rec.get('data_pianificata') or ''
        data_parsed = _parse_data(data_raw)

        # Estrai orario dalle note (con fallback su colonna orari_lettere)
        orario = estrai_orario(note) or ''
        if not orario:
            orario = (rec.get('orari_lettere') or '').strip()

        # Nome operatore leggibile
        operatore = (rec.get('operatore') or '').strip()
        nome_op = _nome_operatore(operatore) if operatore else ''

        risultati.append({
            'eneltel_key': ek,
            'lcl': lcl_val,
            'pod': (rec.get('pod') or '').strip(),
            'nominativo': (rec.get('nominativo') or '').strip(),
            'indirizzo': (rec.get('indirizzo') or '').strip(),
            'civico': (rec.get('civico') or '').strip(),
            'comune': (rec.get('comune') or '').strip(),
            'localita': (rec.get('localita') or '').strip(),
            'telefono': (rec.get('telefono') or '').strip(),
            'email': (rec.get('email') or '').strip(),
            'operatore': operatore,
            'nome_operatore': nome_op,
            'data_pianificata': data_parsed or '',
            'data_pianificata_raw': data_raw,
            'orario': orario,
            'note_appuntamento': note,
            'esito': (rec.get('esito') or '').strip(),
            'data_installazione': (rec.get('data_installazione') or '').strip(),
            'sheets_row': rec.get('sheets_row'),
        })

    return risultati


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@bp.route('/appuntamenti')
@login_required
def appuntamenti_page():
    """Serve la pagina HTML Gestione Appuntamenti."""
    return send_file(os.path.join(BASE_DIR, 'templates', 'appuntamenti.html'))


@bp.route('/api/appuntamenti', methods=['GET'])
@login_required
def api_appuntamenti():
    """Ritorna gli appuntamenti filtrati + statistiche aggregate.

    Query params:
        data_da    — YYYY-MM-DD (default: 7 giorni fa)
        data_a     — YYYY-MM-DD (default: +30 giorni)
        operatore  — matricola (ripetibile per multi-select)
        comune     — filtro case-insensitive (substring)
        esito      — 'tutti' | 'da_fare' | 'completati'
        q          — ricerca libera su nominativo/indirizzo/note/telefono
    """
    try:
        # Cache
        data_ver = _db_data_version()
        today_iso = date.today().isoformat()
        cache_key = (data_ver, today_iso)
        cached = _CACHE.get(cache_key)
        all_apps = None
        if cached:
            created, payload = cached
            if (time.monotonic() - created) < _CACHE_TTL_S:
                all_apps = payload
        if all_apps is None:
            all_apps = _carica_appuntamenti()
            _CACHE.clear()
            _CACHE[cache_key] = (time.monotonic(), all_apps)

        # --- Filtri ---
        oggi = date.today()
        data_da = request.args.get('data_da', '')
        data_a = request.args.get('data_a', '')
        filtro_operatori = request.args.getlist('operatore')
        filtro_comune = (request.args.get('comune') or '').strip().lower()
        filtro_esito = request.args.get('esito', 'tutti')
        filtro_q = (request.args.get('q') or '').strip().lower()

        filtrati = []
        for app in all_apps:
            dp = app['data_pianificata']

            # Filtro data range
            if data_da and dp and dp < data_da:
                continue
            if data_a and dp and dp > data_a:
                continue

            # Filtro operatore
            if filtro_operatori and app['operatore'] not in filtro_operatori:
                continue

            # Filtro comune
            if filtro_comune and filtro_comune not in app['comune'].lower():
                continue

            # Filtro esito
            if filtro_esito == 'da_fare':
                if app['esito'] and 'ok' in app['esito'].lower():
                    continue
            elif filtro_esito == 'completati':
                if not app['esito'] or 'ok' not in app['esito'].lower():
                    continue

            # Ricerca libera
            if filtro_q:
                searchable = ' '.join([
                    app['nominativo'], app['indirizzo'], app['comune'],
                    app['note_appuntamento'], app['telefono'],
                    app['email'], app['nome_operatore'], app['pod'],
                ]).lower()
                if filtro_q not in searchable:
                    continue

            filtrati.append(app)

        # --- Statistiche aggregate ---
        domani = (oggi + timedelta(days=1)).isoformat()
        oggi_iso = oggi.isoformat()
        inizio_sett = (oggi - timedelta(days=oggi.weekday())).isoformat()
        fine_sett = (oggi + timedelta(days=6 - oggi.weekday())).isoformat()

        stats_oggi = 0
        stats_domani = 0
        stats_settimana = 0
        stats_completati = 0
        stats_da_fare = 0
        stats_senza_op = 0
        stats_scaduti = 0

        per_operatore: dict[str, dict] = {}
        per_comune: Counter = Counter()
        per_data: Counter = Counter()

        for app in filtrati:
            dp = app['data_pianificata']
            op = app['operatore']
            is_completato = bool(app['esito'] and 'ok' in app['esito'].lower())

            if is_completato:
                stats_completati += 1
            else:
                stats_da_fare += 1
                if dp and dp < oggi_iso:
                    stats_scaduti += 1

            if dp == oggi_iso:
                stats_oggi += 1
            if dp == domani:
                stats_domani += 1
            if dp and inizio_sett <= dp <= fine_sett:
                stats_settimana += 1

            if not op or op == 'TUTTI':
                stats_senza_op += 1
            else:
                if op not in per_operatore:
                    per_operatore[op] = {
                        'matricola': op,
                        'nome': _nome_operatore(op),
                        'colore': COLORI_TECNICI.get(op, '#888'),
                        'oggi': 0,
                        'domani': 0,
                        'settimana': 0,
                        'totale': 0,
                        'completati': 0,
                        'da_fare': 0,
                        'scaduti': 0,
                    }
                s = per_operatore[op]
                s['totale'] += 1
                if is_completato:
                    s['completati'] += 1
                else:
                    s['da_fare'] += 1
                    if dp and dp < oggi_iso:
                        s['scaduti'] += 1
                if dp == oggi_iso:
                    s['oggi'] += 1
                if dp == domani:
                    s['domani'] += 1
                if dp and inizio_sett <= dp <= fine_sett:
                    s['settimana'] += 1

            if app['comune']:
                per_comune[app['comune'].upper()] += 1
            if dp:
                per_data[dp] += 1

        # Ordina operatori: prima quelli in MATRICOLE_VIP, poi alfabetico
        vip_set = set(MATRICOLE_VIP)
        operatori_list = sorted(
            per_operatore.values(),
            key=lambda o: (0 if o['matricola'] in vip_set else 1,
                           o['matricola']),
        )

        # Top comuni
        top_comuni = [{'comune': c, 'count': n}
                      for c, n in per_comune.most_common(15)]

        # Distribuzione per data (ultime 2 settimane + prossime 2)
        range_da = (oggi - timedelta(days=14)).isoformat()
        range_a = (oggi + timedelta(days=14)).isoformat()
        distribuzione_date = {
            d: n for d, n in sorted(per_data.items())
            if range_da <= d <= range_a
        }

        return jsonify({
            'success': True,
            'provincia': PROVINCIA_ATTIVA,
            'timestamp': oggi_iso,
            'appuntamenti': filtrati,
            'stats': {
                'totale': len(filtrati),
                'oggi': stats_oggi,
                'domani': stats_domani,
                'settimana': stats_settimana,
                'completati': stats_completati,
                'da_fare': stats_da_fare,
                'senza_operatore': stats_senza_op,
                'scaduti': stats_scaduti,
            },
            'operatori': operatori_list,
            'top_comuni': top_comuni,
            'distribuzione_date': distribuzione_date,
            'filtri_applicati': {
                'data_da': data_da,
                'data_a': data_a,
                'operatore': filtro_operatori,
                'comune': filtro_comune,
                'esito': filtro_esito,
                'q': filtro_q,
            },
        })

    except Exception as e:
        logger.error(f"[Appuntamenti] Errore API: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Errore caricamento appuntamenti: {e}'}), 500


@bp.route('/api/appuntamenti/excel', methods=['POST'])
@login_required
def api_download_appuntamenti_excel():
    """Riceve le righe JSON dalla UI e genera un file Excel ben formattato."""
    try:
        data = request.get_json()
        rows = data.get('rows', [])
        if not rows:
            return jsonify({'error': 'Nessun dato fornito'}), 400
            
        import tempfile
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        
        wb = Workbook()
        ws = wb.active
        ws.title = "Appuntamenti Filtrati"
        
        header_font = Font(name='Calibri', bold=True, size=11, color='FFFFFF')
        header_fill = PatternFill(start_color='E91E63', end_color='E91E63', fill_type='solid')
        header_align = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell_align = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell_font = Font(name='Calibri', size=10)
        thin_border = Border(
            left=Side(style='thin', color='CCCCCC'),
            right=Side(style='thin', color='CCCCCC'),
            top=Side(style='thin', color='CCCCCC'),
            bottom=Side(style='thin', color='CCCCCC'),
        )
        alt_fill = PatternFill(start_color='FFF0F5', end_color='FFF0F5', fill_type='solid')
        
        giorni_it = ['lunedì', 'martedì', 'mercoledì', 'giovedì', 'venerdì', 'sabato', 'domenica']
        mesi_it = ['gennaio', 'febbraio', 'marzo', 'aprile', 'maggio', 'giugno',
                   'luglio', 'agosto', 'settembre', 'ottobre', 'novembre', 'dicembre']
        def _formatta_data_it(data_yyyy_mm_dd: str) -> str:
            if not data_yyyy_mm_dd:
                return ''
            try:
                dt = datetime.strptime(data_yyyy_mm_dd, '%Y-%m-%d')
                return f"{giorni_it[dt.weekday()]} {dt.day} {mesi_it[dt.month - 1]}"
            except (ValueError, IndexError):
                return data_yyyy_mm_dd
        
        colonne = ['Operatore', 'Data', 'Orario', 'POD', 'Eneltel', 'Nominativo', 'Indirizzo', 'Civico', 'Comune', 'Note', 'Esito']
        larghezze = [14, 24, 16, 20, 16, 22, 24, 8, 18, 26, 15]
        
        for c, (titolo, larg) in enumerate(zip(colonne, larghezze), 1):
            cell = ws.cell(row=1, column=c, value=titolo)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_align
            cell.border = thin_border
            ws.column_dimensions[cell.column_letter].width = larg
            
        for r, app in enumerate(rows, 2):
            op = app.get('nome_operatore') or app.get('operatore', '')
            valori = [
                op,
                _formatta_data_it(app.get('data_pianificata', '')),
                app.get('orario', ''),
                app.get('pod', ''),
                app.get('eneltel_key', ''),
                app.get('nominativo', ''),
                app.get('indirizzo', ''),
                app.get('civico', ''),
                app.get('comune', ''),
                app.get('note_appuntamento', ''),
                app.get('esito', ''),
            ]
            for c, val in enumerate(valori, 1):
                cell = ws.cell(row=r, column=c, value=val)
                cell.font = cell_font
                cell.alignment = cell_align
                cell.border = thin_border
                if r % 2 == 0:
                    cell.fill = alt_fill
                    
        ws.row_dimensions[1].height = 28
        
        fd, path = tempfile.mkstemp(suffix='.xlsx')
        os.close(fd)
        wb.save(path)
        
        timestamp = datetime.now().strftime('%Y-%m-%d_%H%M')
        response = send_file(
            path,
            as_attachment=True,
            download_name=f'appuntamenti_filtrati_{timestamp}.xlsx',
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response.call_on_close(lambda: os.unlink(path))
        return response
        
    except Exception as e:
        logger.error(f"[Appuntamenti] Errore Export Excel: {e}")
        return jsonify({'error': str(e)}), 500
