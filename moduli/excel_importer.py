# -*- coding: utf-8 -*-
"""Importer del filone da file Excel/CSV.

Sostituisce il pull periodico da Google Sheets (rimosso il 21/05/2026).
L'admin scarica manualmente il foglio Sheets come .csv/.xlsx e lo carica via
endpoint admin; questo modulo lo legge, risolve il mapping colonne, e fa
un merge cella-per-cella col DB locale.

POLITICA DI MERGE: "vince il più recente".
  Per ogni riga (eneltel) confronta `last_local_change_ts` del DB con
  l'`excel_export_ts` (fornito dall'utente). Se la riga è stata modificata
  nel programma DOPO l'export Excel, il DB vince (Excel ignorato per quella
  riga). Altrimenti vincono i valori Excel, cella per cella.

  Granularità: a livello RIGA. Una sola modifica locale post-export
  protegge tutta la riga dall'import. È la scelta più sicura.
"""
import csv
import io
import json
import logging
import os
import time
from collections import Counter
from datetime import datetime

from . import db as _db
from .safe_json import json_read  # noqa  (potrebbe servire per cache future)

logger = logging.getLogger('mappa_algeco')


def _parse_export_ts(export_ts):
    """Normalizza il timestamp di export in int (epoch s).

    Accetta:
      - int/float (epoch s) → ritornato così com'è (int)
      - str 'YYYY-MM-DD HH:MM:SS' o 'YYYY-MM-DD' o 'YYYY-MM-DDTHH:MM:SS'
      - None → usa now() (assume export appena fatto)
    """
    if export_ts is None:
        return int(time.time())
    if isinstance(export_ts, (int, float)):
        return int(export_ts)
    s = str(export_ts).strip()
    for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            return int(datetime.strptime(s, fmt).timestamp())
        except ValueError:
            continue
    raise ValueError(f"Formato timestamp non riconosciuto: {export_ts!r}")


def _cell_to_str(v):
    """Converte una cella Excel in stringa pulita (gestisce datetime, float, None)."""
    if v is None:
        return ''
    if isinstance(v, datetime):
        # Date: formato italiano (DD/MM/YYYY) per matchare il legacy del foglio
        if v.hour == 0 and v.minute == 0 and v.second == 0:
            return v.strftime('%d/%m/%Y')
        return v.strftime('%d/%m/%Y %H:%M:%S')
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


# Chiavi interne che richiedono normalizzazione canonica per evitare diff "rumorosi"
# tra import successivi (formato italiano vs Python, ISO vs DD/MM/YYYY, ecc.).
_KEYS_NUMERIC_DOT = {'latitudine', 'longitudine'}
_KEYS_DATE = {'data_pianificata', 'data_installazione'}
_INVALID_NUMERIC_TOKENS = {
    '#REF!', '#VALUE!', '#DIV/0!', '#N/A', '#NAME?', '#NULL!', '#NUM!',
    'ANNULLATO', 'ANNULLATA',
}

# Date ISO YYYY-MM-DD (eventualmente con HH:MM:SS dopo)
import re as _re
_ISO_DATE_RE = _re.compile(r'^(\d{4})-(\d{2})-(\d{2})(?:\s+(\d{2}:\d{2}:\d{2}))?$')
_IT_DATE_RE = _re.compile(r'^(\d{1,2})/(\d{1,2})/(\d{4})(?:\s+(\d{2}:\d{2}(?::\d{2})?))?$')
_ENELTEL_KEY_RE = _re.compile(r'^[A-Z0-9]{4,15}$')
_LCL_RE = _re.compile(r'^\d{4,20}$')
_POD_RE = _re.compile(r'^IT[A-Z0-9]{8,}$')
_OPERATOR_RE = _re.compile(r'^[A-Z]{2,3}\d{4,8}$')
_POSITION_RE = _re.compile(r'^\d+\s*-?')


def _normalize_for_compare(s):
    """Normalizzazione aggressiva PER IL CONFRONTO (non per il salvataggio).

    Riduce le differenze "rumorose" che non corrispondono a un cambio reale:
      - strip iniziale/finale
      - sequenze di whitespace collassate in un singolo spazio
      - None → ''
    Restituisce sempre una stringa. Non altera maiuscole/minuscole né punteggiatura
    (cambi tipo "Mario" → "MARIO" sono comunque cambi reali e li riportiamo).
    """
    if s is None:
        return ''
    s = str(s)
    if not s:
        return ''
    # Strip + collasso whitespace
    return ' '.join(s.split())


def _parse_decimal(value):
    """Ritorna float per confronti numerici, accettando virgola decimale."""
    s = _normalize_for_compare(value)
    if not s:
        return None
    try:
        return float(s.replace(',', '.'))
    except (TypeError, ValueError):
        return None


def _is_invalid_numeric_value(value):
    """True per errori/formule testuali che non vanno scritti in colonne REAL."""
    s = _normalize_for_compare(value).upper()
    if not s:
        return False
    return s in _INVALID_NUMERIC_TOKENS or s.startswith('#')


def _date_compare_key(value):
    """Normalizza date italiane/ISO alla stessa chiave di confronto."""
    s = _normalize_for_compare(value)
    if not s:
        return ''
    m = _ISO_DATE_RE.match(s)
    if m:
        tail = m.group(4) or ''
        if tail in ('', '00:00:00'):
            tail = ''
        return f'{m.group(1)}-{m.group(2)}-{m.group(3)}{(" " + tail) if tail else ""}'
    m = _IT_DATE_RE.match(s)
    if m:
        giorno = m.group(1).zfill(2)
        mese = m.group(2).zfill(2)
        tail = m.group(4) or ''
        if tail in ('', '00:00', '00:00:00'):
            tail = ''
        return f'{m.group(3)}-{mese}-{giorno}{(" " + tail) if tail else ""}'
    return s


def _values_equivalent(old_value, new_value, chiave_interna):
    """Confronto semantico per evitare diff rumorosi da Excel/SQLite."""
    old_cmp = _normalize_for_compare(old_value)
    new_cmp = _normalize_for_compare(new_value)
    if old_cmp == new_cmp:
        return True
    if chiave_interna in _KEYS_NUMERIC_DOT:
        old_num = _parse_decimal(old_cmp)
        new_num = _parse_decimal(new_cmp)
        if old_num is not None and new_num is not None:
            return abs(old_num - new_num) < 0.0000001
    if chiave_interna in _KEYS_DATE:
        return _date_compare_key(old_cmp) == _date_compare_key(new_cmp)
    return False


def _normalize_for_db(value, chiave_interna):
    """Normalizza un valore Excel per renderlo confrontabile con il DB.

    - lat/lon: virgola → punto (formato Python standard).
    - date: ISO `YYYY-MM-DD` → italiano `DD/MM/YYYY` (formato legacy del foglio).
    - altre colonne: invariato (solo strip).
    """
    if value is None:
        return ''
    s = str(value).strip()
    if not s:
        return ''
    if chiave_interna in _KEYS_NUMERIC_DOT:
        if _is_invalid_numeric_value(s):
            return s.upper()
        # Solo se sembra un numero con virgola decimale (no virgole multiple)
        if s.count(',') == 1 and s.count('.') == 0:
            return s.replace(',', '.')
        return s
    if chiave_interna in _KEYS_DATE:
        m = _ISO_DATE_RE.match(s)
        if m:
            anno, mese, giorno = m.group(1), m.group(2), m.group(3)
            tail = f' {m.group(4)}' if m.group(4) else ''
            return f'{giorno}/{mese}/{anno}{tail}'
        return s
    return s


def parse_excel(file_path, sheet_name=None):
    """Apre un .xlsx e ritorna (headers, rows).

    headers: list[str] dalla riga 1.
    rows: list[list[str]] — solo righe non vuote.
    """
    try:
        import openpyxl
    except ImportError as e:
        raise RuntimeError(
            f"openpyxl non installato ({e}). pip install openpyxl"
        )

    wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
    ws = wb[sheet_name] if sheet_name else wb.active
    if ws is None:
        raise ValueError("Nessun foglio trovato nel file Excel")

    raw_rows = list(ws.iter_rows(values_only=True))
    if not raw_rows:
        return ([], [])

    headers = [_cell_to_str(c) for c in raw_rows[0]]
    rows = []
    for r in raw_rows[1:]:
        if r is None:
            continue
        row_vals = [_cell_to_str(c) for c in r]
        if all(v == '' for v in row_vals):
            continue
        rows.append(row_vals)
    wb.close()
    return (headers, rows)


def _extract_xlsx_cell_comments(file_path, sheet_name=None):
    """Estrae i commenti strutturati delle celle da un .xlsx.

    Ritorna list[dict] con:
      - row: riga logica import 1-based (dopo aver ignorato righe vuote)
      - col_name: header della colonna
      - text: testo del commento

    I CSV non hanno un canale equivalente per commenti cella: eventuali note
    devono essere dentro una normale colonna testuale (es. NOTE APP).
    """
    import posixpath
    import zipfile
    from xml.etree import ElementTree as ET

    ns_main = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    ns_rel_doc = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    ns_rel_pkg = 'http://schemas.openxmlformats.org/package/2006/relationships'

    def q(ns, tag):
        return f'{{{ns}}}{tag}'

    def resolve_target(base_path, target):
        if target.startswith('/'):
            return target.lstrip('/')
        return posixpath.normpath(posixpath.join(posixpath.dirname(base_path), target))

    def rels_path_for(part_path):
        return posixpath.join(
            posixpath.dirname(part_path),
            '_rels',
            posixpath.basename(part_path) + '.rels',
        )

    def column_index_from_ref(cell_ref):
        m = _re.match(r'^\$?([A-Z]+)\$?(\d+)$', str(cell_ref or '').upper())
        if not m:
            return None, None
        idx = 0
        for ch in m.group(1):
            idx = idx * 26 + (ord(ch) - ord('A') + 1)
        return idx - 1, int(m.group(2))

    def sheet_headers_and_rows():
        try:
            import openpyxl
        except ImportError as e:
            raise RuntimeError(
                f"openpyxl non installato ({e}). pip install openpyxl"
            )
        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
        try:
            ws = wb[sheet_name] if sheet_name else wb.active
            headers = []
            physical_to_logical = {}
            logical_row = 0
            for row in ws.iter_rows(values_only=False):
                if not row:
                    continue
                values = [_cell_to_str(cell.value) for cell in row]
                if all(v == '' for v in values):
                    continue
                logical_row += 1
                physical_to_logical[row[0].row] = logical_row
                if logical_row == 1:
                    headers = values
            return headers, physical_to_logical
        finally:
            wb.close()

    headers, physical_to_logical = sheet_headers_and_rows()
    if not headers:
        return []

    with zipfile.ZipFile(file_path) as zf:
        names = set(zf.namelist())
        if 'xl/workbook.xml' not in names or 'xl/_rels/workbook.xml.rels' not in names:
            return []

        workbook = ET.fromstring(zf.read('xl/workbook.xml'))
        workbook_rels = ET.fromstring(zf.read('xl/_rels/workbook.xml.rels'))
        rel_targets = {
            rel.attrib.get('Id'): resolve_target('xl/workbook.xml', rel.attrib.get('Target', ''))
            for rel in workbook_rels.findall(q(ns_rel_pkg, 'Relationship'))
        }

        selected_sheet = None
        for sheet in workbook.findall(f'.//{q(ns_main, "sheet")}'):
            if sheet_name and sheet.attrib.get('name') != sheet_name:
                continue
            selected_sheet = sheet
            break
        if selected_sheet is None:
            return []

        rel_id = selected_sheet.attrib.get(q(ns_rel_doc, 'id'))
        sheet_path = rel_targets.get(rel_id)
        if not sheet_path:
            return []

        sheet_rels_path = rels_path_for(sheet_path)
        if sheet_rels_path not in names:
            return []
        sheet_rels = ET.fromstring(zf.read(sheet_rels_path))

        comments_paths = []
        for rel in sheet_rels.findall(q(ns_rel_pkg, 'Relationship')):
            rel_type = rel.attrib.get('Type', '')
            if rel_type.endswith('/comments'):
                comments_paths.append(resolve_target(sheet_path, rel.attrib.get('Target', '')))

        comments = []
        for comments_path in comments_paths:
            if comments_path not in names:
                continue
            root = ET.fromstring(zf.read(comments_path))
            for item in root.findall(f'.//{q(ns_main, "comment")}'):
                col_idx, physical_row = column_index_from_ref(item.attrib.get('ref'))
                if col_idx is None or physical_row is None:
                    continue
                if col_idx >= len(headers):
                    continue
                col_name = headers[col_idx]
                if not col_name:
                    continue
                logical_row = physical_to_logical.get(physical_row)
                if logical_row is None:
                    continue
                text = _normalize_for_compare(
                    ''.join(
                        t.text or ''
                        for t in item.findall(f'.//{q(ns_main, "t")}')
                    )
                )
                if not text:
                    continue
                comments.append({
                    'row': logical_row,
                    'col_name': col_name,
                    'text': text,
                })
        return comments


def _decode_csv_bytes(raw):
    """Decodifica CSV esportati da Sheets/Excel con encoding comuni."""
    for enc in ('utf-8-sig', 'utf-8', 'cp1252', 'latin-1'):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='replace'), 'utf-8-replace'


def parse_csv(file_path):
    """Apre un .csv e ritorna (headers, rows).

    Rileva delimiter tra virgola, punto e virgola, tab e pipe. Google Sheets
    esporta normalmente UTF-8 con virgola, ma in Italia capita spesso di
    ricevere CSV semicolon-separated da Excel.
    """
    with open(file_path, 'rb') as f:
        raw = f.read()
    text, _encoding = _decode_csv_bytes(raw)
    if not text.strip():
        return ([], [])

    sample = text[:65536]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=',;\t|')
    except csv.Error:
        dialect = csv.excel

    parsed = []
    try:
        reader = csv.reader(io.StringIO(text, newline=''), dialect=dialect, strict=True)
        for row in reader:
            vals = [_cell_to_str(c) for c in row]
            if all(v == '' for v in vals):
                continue
            parsed.append(vals)
    except csv.Error as e:
        raise ValueError(
            "CSV malformato: alcune celle sembrano contenere virgolette o a-capo "
            "non esportati correttamente. Reimporta un file .xlsx oppure riesporta "
            f"il CSV da Sheets. Dettaglio: {e}"
        ) from e
    if not parsed:
        return ([], [])
    return (parsed[0], parsed[1:])


def parse_tabular_file(file_path, sheet_name=None):
    """Legge .csv o .xlsx e ritorna (headers, rows, file_format)."""
    ext = os.path.splitext(file_path)[1].lower()
    if ext == '.csv':
        headers, rows = parse_csv(file_path)
        return headers, rows, 'csv'
    if ext == '.xlsx':
        headers, rows = parse_excel(file_path, sheet_name=sheet_name)
        return headers, rows, 'xlsx'
    raise ValueError("Formato file non supportato: usa .csv o .xlsx")


def _find_col_idx(headers, candidates):
    """Cerca uno dei candidati in headers (case-insensitive). Ritorna idx o None."""
    lower = {h.strip().lower(): i for i, h in enumerate(headers) if h}
    for c in candidates:
        idx = lower.get(c.strip().lower())
        if idx is not None:
            return idx
    return None


def _looks_like_pod(value):
    """True se il valore ha l'aspetto di un POD, non di un Eneltel."""
    s = _normalize_for_compare(value).upper()
    return bool(_POD_RE.match(s))


def _looks_like_date_or_marker(value):
    """True per date reali o marker storici ammessi nelle colonne data."""
    s = _normalize_for_compare(value)
    if not s:
        return True
    upper = s.upper()
    if upper in _INVALID_NUMERIC_TOKENS:
        return True
    return bool(_IT_DATE_RE.match(s) or _ISO_DATE_RE.match(s))


def _structural_col_indices(headers):
    """Indici di colonne che devono esistere fisicamente in ogni riga CSV."""
    required = []
    for candidates in (
        ('Pod', 'POD'),
        ('NOMINATIVO',),
        ('Indirizzo', 'INDIRIZZO'),
        ('Comune', 'COMUNE'),
    ):
        idx = _find_col_idx(headers, candidates)
        if idx is not None:
            required.append(idx)
    return required


def _invalid_import_row_reason(
    eneltel_raw, eneltel_key, lcl, row=None, required_indices=None, expected_cols=None,
):
    """Motivo di scarto per righe che hanno colonne slittate o chiavi impossibili."""
    key = _normalize_for_compare(eneltel_key).upper()
    lcl_clean = _normalize_for_compare(lcl)
    if not key:
        return ''
    if row is not None and required_indices:
        missing = [idx for idx in required_indices if idx >= len(row)]
        if missing:
            return 'riga CSV tronca: colonne strutturali mancanti'
    if row is not None and expected_cols is not None and len(row) > expected_cols:
        extra_values = row[expected_cols:]
        if any(_normalize_for_compare(v) for v in extra_values):
            return 'riga CSV con più celle dell’intestazione: probabile separatore non quotato'
    if _looks_like_pod(eneltel_raw) or _looks_like_pod(key):
        return 'valore POD nella colonna Eneltel'
    if not _ENELTEL_KEY_RE.match(key):
        return 'Eneltel con formato non valido'
    if lcl_clean and not _LCL_RE.match(lcl_clean):
        return 'LCL con formato non valido'
    return ''


def _csv_structural_cell_anomalies(
    fields, header_mapping, known_comuni=None, known_operators=None, known_positions=None,
):
    """Ritorna celle CSV sospette senza usare un singolo valore per scartare la riga.

    Il CSV è fragile quando testo libero contiene virgole non quotate: invece di
    bloccare righe per valori perfettamente possibili (es. note nella DATA),
    contiamo solo anomalie ad alta confidenza e usiamo il risultato per:
      - scartare la riga se le anomalie sono multiple;
      - ignorare la singola cella sospetta se è isolata.
    """
    known_comuni = known_comuni or set()
    known_operators = known_operators or set()
    known_positions = known_positions or set()
    anomalies = {}
    for col_name, raw_value in fields.items():
        chiave = header_mapping.get(col_name)
        value = _normalize_for_compare(raw_value)
        if not value:
            continue
        value_upper = value.upper()
        if chiave == 'pod' and not _looks_like_pod(value):
            anomalies[col_name] = 'POD con formato non valido'
        if chiave == 'comune' and value.isdigit():
            anomalies[col_name] = 'Comune numerico'
        if chiave == 'civico':
            compact = value.replace('/', '').replace('-', '').replace(' ', '')
            if compact and not any(ch.isdigit() for ch in compact):
                anomalies[col_name] = 'Civico senza numeri'
        if chiave in _KEYS_NUMERIC_DOT:
            if _is_invalid_numeric_value(value):
                continue
            if _parse_decimal(value) is None:
                anomalies[col_name] = f'{col_name} non numerico'
        if chiave == 'telefono':
            if value_upper in known_comuni and not any(ch.isdigit() for ch in value):
                anomalies[col_name] = 'Telefono contiene un comune'
        if chiave == 'operatore':
            if value_upper in _INVALID_NUMERIC_TOKENS:
                continue
            if value_upper not in known_operators and not _OPERATOR_RE.match(value_upper):
                anomalies[col_name] = 'Operatore con formato non valido'
        if chiave == 'posizione_misuratore':
            # La posizione ha molte varianti testuali legittime
            # ("nell'appartamento", "esterno", ecc.): non la usiamo come blocco.
            if value_upper in known_positions or _POSITION_RE.match(value_upper):
                continue
    return anomalies


def import_excel(file_path, *, export_ts=None, sheet_name=None, dry_run=False, user=None):
    """Importa il filone da file Excel/CSV applicando merge most-recent-wins.

    Args:
        file_path: path al .csv/.xlsx.
        export_ts: timestamp dell'export (str o int). Default: now.
        sheet_name: nome scheda Excel da leggere (default: prima scheda).
        dry_run: se True, calcola tutto ma NON scrive sul DB.
        user: nome utente che ha caricato il file (per audit).

    Returns:
        dict summary {
            'success': bool,
            'export_ts': int,
            'export_ts_iso': str,
            'headers_excel': list[str],
            'header_mapping': {nome_excel: chiave_interna | None},
            'rows_excel': int,
            'rows_nuove': int,
            'rows_aggiornate': int,
            'rows_db_vince': int,           # righe il cui DB è più recente
            'rows_skip_no_eneltel': int,    # righe Excel senza eneltel valido
            'rows_skip_invalid_key': int,   # righe con chiavi/valori strutturali impossibili
            'celle_modificate': int,
            'eneltel_solo_db': int,         # in DB ma NON in Excel (non cancellati)
            'dry_run': bool,
            'sample_db_vince': list[str],   # primi eneltel saltati per DB più recente
            'sample_invalid_rows': list[dict],
            'errori': list[str],
        }
    """
    export_ts_int = _parse_export_ts(export_ts)
    headers_xlsx, rows_xlsx, file_format = parse_tabular_file(
        file_path, sheet_name=sheet_name,
    )

    if not headers_xlsx:
        return {
            'success': False, 'errori': ['Excel vuoto / nessuna intestazione'],
            'rows_excel': 0,
        }

    # 1. Identifica colonna Eneltel (obbligatoria)
    eneltel_idx = _find_col_idx(headers_xlsx, ['Eneltel', 'ENELTEL', 'eneltel'])
    if eneltel_idx is None:
        return {
            'success': False,
            'errori': [f"Colonna Eneltel non trovata. Headers presenti: {headers_xlsx[:30]}"],
            'rows_excel': len(rows_xlsx),
        }

    # 2. LCL opzionale (per Pavia: multi-LCL per stesso eneltel)
    lcl_idx = _find_col_idx(headers_xlsx, ['LCL', 'lcl'])
    required_row_indices = _structural_col_indices(headers_xlsx) if file_format == 'csv' else []
    expected_row_cols = len(headers_xlsx) if file_format == 'csv' else None

    # 3. Header mapping: per ogni colonna Excel, cerca la chiave interna nel DB
    header_mapping = {}
    conn = _db.get_conn()
    for h in headers_xlsx:
        if not h:
            continue
        header_mapping[h] = _db.column_to_internal_key(conn, h)

    summary = {
        'success': True,
        'export_ts': export_ts_int,
        'export_ts_iso': datetime.fromtimestamp(export_ts_int).strftime('%Y-%m-%d %H:%M:%S'),
        'headers_excel': headers_xlsx,
        'file_format': file_format,
        'header_mapping': header_mapping,
        'rows_excel': len(rows_xlsx),
        'rows_nuove': 0,
        'rows_aggiornate': 0,
        'rows_db_vince': 0,
        'rows_skip_no_eneltel': 0,
        'rows_skip_invalid_key': 0,
        'celle_modificate': 0,
        'celle_ignorate_rumorose': 0,
        'diff_per_colonna': {},
        'valori_invalidi_per_colonna': {},
        'eneltel_solo_db': 0,
        'commenti_importati': 0,
        'dry_run': dry_run,
        'sample_db_vince': [],
        'sample_invalid_rows': [],
        'errori': [],
    }

    comments_by_excel_row = {}
    if file_format == 'xlsx':
        try:
            for item in _extract_xlsx_cell_comments(file_path, sheet_name=sheet_name):
                comments_by_excel_row.setdefault(item['row'], []).append(
                    (item['col_name'], item['text'])
                )
        except Exception as e:
            logger.warning(f"[excel-import] lettura commenti XLSX fallita: {e}")
            summary['errori'].append(f"Lettura commenti XLSX non riuscita: {e}")

    if dry_run:
        # In dry-run NON modifichiamo nulla, ma proviamo a fare il merge
        # "virtuale" per riportare statistiche accurate.
        pass

    eneltel_visti_excel = set()
    t0 = time.time()

    with conn:
        if not dry_run:
            conn.execute('BEGIN IMMEDIATE')
            _db.update_column_registry(conn, headers_xlsx)

        # ────────────────────────────────────────────────────────────────
        # PRE-LOAD BATCH (riduce ~2N query SELECT in N=righe_db a 2 query totali)
        # ────────────────────────────────────────────────────────────────
        filone_by_key = {}  # (eneltel_key, lcl) -> dict riga
        for r in conn.execute('SELECT * FROM filone WHERE is_orphan=0'):
            d = dict(r)
            key = (d['eneltel_key'], d.get('lcl') or '')
            filone_by_key[key] = d
        known_comuni = {
            comune.upper()
            for row_db in filone_by_key.values()
            if (comune := _normalize_for_compare(row_db.get('comune')))
            and not comune.isdigit()
        }
        known_operators = {
            operatore.upper()
            for row_db in filone_by_key.values()
            if (operatore := _normalize_for_compare(row_db.get('operatore')))
        }
        known_positions = {
            posizione.upper()
            for row_db in filone_by_key.values()
            if (posizione := _normalize_for_compare(row_db.get('posizione_misuratore')))
        }
        extras_by_key = {}  # (eneltel_key, lcl) -> {column_name: value}
        for r in conn.execute(
            "SELECT eneltel_key, COALESCE(lcl,'') AS lcl, column_name, value FROM filone_extra"
        ):
            key = (r['eneltel_key'], r['lcl'])
            extras_by_key.setdefault(key, {})[r['column_name']] = r['value'] or ''
        # Index by eneltel only (per il fallback "primo LCL" se lcl=='')
        first_lcl_by_eneltel = {}
        for (ek, lcl), _row in filone_by_key.items():
            if ek not in first_lcl_by_eneltel:
                first_lcl_by_eneltel[ek] = lcl
        logger.info(
            f"[excel-import] preload: {len(filone_by_key)} righe filone, "
            f"{sum(len(v) for v in extras_by_key.values())} extras "
            f"in {time.time() - t0:.2f}s"
        )

        # Audit batch: accumuliamo tutto e flushiamo con executemany alla fine
        audit_batch = []  # list of (ts, eneltel_key, column_name, old, new, source, user, detail_json)
        diff_counter = Counter()
        invalid_counter = Counter()
        noisy_counter = Counter()
        # Updates per filone (un'unica UPDATE per riga con N campi)
        # Mantenuti come (eneltel_key, lcl, {chiave: new_value}) in lista,
        # eseguiti uno per uno (UPDATE filone con set dinamico è già veloce
        # grazie all'index PK).
        def import_comments_for_row(row_excel_idx, eneltel_key, lcl):
            if dry_run:
                return 0
            row_comments = comments_by_excel_row.get(row_excel_idx)
            if not row_comments:
                return 0
            comments = {col_name: text for col_name, text in row_comments}
            result = _db.set_cell_comments_by_anchor(
                conn,
                eneltel_key=eneltel_key,
                lcl=lcl,
                comments=comments,
                user=user,
                source='excel_import',
            )
            return result['written']

        for row_excel_idx, row in enumerate(rows_xlsx, start=2):
            eneltel_raw = row[eneltel_idx] if eneltel_idx < len(row) else ''
            eneltel_key = _db.normalize_eneltel(eneltel_raw)
            if not eneltel_key:
                summary['rows_skip_no_eneltel'] += 1
                continue

            lcl_raw = row[lcl_idx] if lcl_idx is not None and lcl_idx < len(row) else ''
            lcl = (lcl_raw or '').strip()
            invalid_reason = _invalid_import_row_reason(
                eneltel_raw, eneltel_key, lcl,
                row=row, required_indices=required_row_indices,
                expected_cols=expected_row_cols,
            )
            if invalid_reason:
                summary['rows_skip_invalid_key'] += 1
                if len(summary['sample_invalid_rows']) < 20:
                    summary['sample_invalid_rows'].append({
                        'row': row_excel_idx,
                        'reason': invalid_reason,
                        'eneltel': str(eneltel_raw or ''),
                        'lcl': str(lcl or ''),
                    })
                continue
            eneltel_visti_excel.add(eneltel_key)

            # Costruisci dict {nome_colonna_excel: valore} per questa riga
            fields = {}
            for i, h in enumerate(headers_xlsx):
                if not h or h == 'Eneltel' or h.lower() == 'eneltel':
                    continue
                fields[h] = row[i] if i < len(row) else ''

            skip_cell_columns = set()
            cell_anomalies = {}
            if file_format == 'csv':
                cell_anomalies = _csv_structural_cell_anomalies(
                    fields, header_mapping, known_comuni, known_operators, known_positions,
                )
            if len(cell_anomalies) >= 2:
                summary['rows_skip_invalid_key'] += 1
                if len(summary['sample_invalid_rows']) < 20:
                    reasons = '; '.join(cell_anomalies.values())
                    summary['sample_invalid_rows'].append({
                        'row': row_excel_idx,
                        'reason': f'anomalie strutturali multiple: {reasons}',
                        'eneltel': str(eneltel_raw or ''),
                        'lcl': str(lcl or ''),
                    })
                continue
            if cell_anomalies:
                skip_cell_columns = set(cell_anomalies.keys())
                invalid_counter.update(
                    header_mapping.get(col) or col for col in skip_cell_columns
                )

            # Lookup riga DB (memoria, no SELECT)
            key = (eneltel_key, lcl)
            db_row = filone_by_key.get(key)
            if db_row is None and not lcl:
                # Fallback alla prima riga dell'eneltel (per backward-compat)
                fallback_lcl = first_lcl_by_eneltel.get(eneltel_key)
                if fallback_lcl is not None:
                    key = (eneltel_key, fallback_lcl)
                    db_row = filone_by_key.get(key)
                    lcl = fallback_lcl

            if db_row is None:
                summary['rows_nuove'] += 1
                if not dry_run:
                    inserted = _insert_new_row(
                        conn, eneltel_key, lcl, row_excel_idx,
                        headers_xlsx, fields, header_mapping,
                        export_ts=export_ts_int, user=user,
                        skip_columns=skip_cell_columns,
                    )
                    if inserted:
                        summary['commenti_importati'] += import_comments_for_row(
                            row_excel_idx, eneltel_key, lcl,
                        )
                continue

            # Most-recent-wins a livello riga
            db_last_local = db_row.get('last_local_change_ts') or 0
            if db_last_local > export_ts_int:
                summary['rows_db_vince'] += 1
                if len(summary['sample_db_vince']) < 20:
                    summary['sample_db_vince'].append(eneltel_key)
                continue

            summary['commenti_importati'] += import_comments_for_row(
                row_excel_idx, eneltel_key, lcl,
            )

            # Diff cella-per-cella usando i dict in memoria
            cur_extra = extras_by_key.get(key, {})
            filone_updates = {}  # {chiave: new_val}
            extra_updates = {}   # {col_name: new_val}
            for col_name, new_val_raw in fields.items():
                if col_name in skip_cell_columns:
                    continue
                chiave = header_mapping.get(col_name)
                new_val = _normalize_for_db(new_val_raw, chiave)
                if chiave == 'eneltel':
                    continue
                if chiave and chiave in _db.COLONNE_NOTE:
                    old_val_raw = db_row.get(chiave)
                    old_val_db = '' if old_val_raw is None else str(old_val_raw).strip()
                    if chiave in _KEYS_NUMERIC_DOT and _is_invalid_numeric_value(new_val):
                        invalid_counter[chiave] += 1
                        continue
                    # Confronto semantico: ignora differenze rumorose
                    # (spazi, date ISO vs italiane, float SQLite vs Excel).
                    if _values_equivalent(old_val_db, new_val, chiave):
                        if _normalize_for_compare(old_val_db) != _normalize_for_compare(new_val):
                            noisy_counter[chiave] += 1
                        continue
                    filone_updates[chiave] = (old_val_db, new_val)
                else:
                    old_val_db = cur_extra.get(col_name, '')
                    if _values_equivalent(old_val_db, new_val, chiave):
                        if _normalize_for_compare(old_val_db) != _normalize_for_compare(new_val):
                            noisy_counter[col_name] += 1
                        continue
                    extra_updates[col_name] = (old_val_db, new_val)

            n_changed = len(filone_updates) + len(extra_updates)
            if n_changed == 0:
                continue
            summary['rows_aggiornate'] += 1
            summary['celle_modificate'] += n_changed
            diff_counter.update(list(filone_updates.keys()) + list(extra_updates.keys()))

            if dry_run:
                continue

            # Applica updates su filone (1 UPDATE con set dinamico)
            if filone_updates:
                set_clause = ', '.join(f'{k}=?' for k in filone_updates)
                vals = [v[1] for v in filone_updates.values()]
                vals.extend([export_ts_int, eneltel_key, lcl])
                conn.execute(
                    f"UPDATE filone SET {set_clause}, last_remote_pull_ts=? "
                    f"WHERE eneltel_key=? AND COALESCE(lcl,'')=?",
                    vals,
                )
                # In-memory: aggiorna anche il dict per coerenza
                for chiave, (_old, new) in filone_updates.items():
                    db_row[chiave] = new
                for chiave, (old, new) in filone_updates.items():
                    audit_batch.append((
                        export_ts_int, eneltel_key, chiave, old, new,
                        'excel_import', user,
                        json.dumps({'lcl': lcl, 'export_ts': export_ts_int}, ensure_ascii=False),
                    ))

            # Applica updates su filone_extra (INSERT OR REPLACE per cella)
            for col_name, (old, new) in extra_updates.items():
                conn.execute(
                    'INSERT OR REPLACE INTO filone_extra(eneltel_key, lcl, column_name, value) '
                    'VALUES (?, ?, ?, ?)',
                    (eneltel_key, lcl, col_name, new),
                )
                cur_extra[col_name] = new
                audit_batch.append((
                    export_ts_int, eneltel_key, col_name, old, new,
                    'excel_import', user,
                    json.dumps({'lcl': lcl, 'export_ts': export_ts_int}, ensure_ascii=False),
                ))

        # Flush audit log in batch (executemany è ~10x più veloce di N exec)
        if not dry_run and audit_batch:
            now_ts = _db._now_ts()
            # audit_log columns: ts, eneltel_key, column_name, old_value,
            # new_value, source, user, detail
            conn.executemany(
                'INSERT INTO audit_log(ts, eneltel_key, column_name, old_value, '
                'new_value, source, user, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                [(now_ts, ek, col, old, new, src, usr, det)
                 for (_ts_eff, ek, col, old, new, src, usr, det) in audit_batch],
            )

        # Conteggio eneltel solo in DB (in Excel non presenti)
        summary['eneltel_solo_db'] = len(
            {ek for (ek, _lcl) in filone_by_key.keys()} - eneltel_visti_excel
        )
        summary['diff_per_colonna'] = dict(diff_counter.most_common())
        summary['valori_invalidi_per_colonna'] = dict(invalid_counter.most_common())
        summary['celle_ignorate_rumorose'] = sum(noisy_counter.values())
        summary['rumore_ignorato_per_colonna'] = dict(noisy_counter.most_common())

    # Auto-mark-delivered: tutte le pending create PRIMA dell'export_ts si
    # considerano già applicate sul foglio (perché il file Excel le riflette).
    # Quelle create DOPO restano in outbox per la prossima consegna.
    auto_marked = 0
    if not dry_run:
        try:
            from . import outbox as _ob
            r = _ob.mark_delivered(before_ts=export_ts_int, user=user)
            auto_marked = r.get('marked', 0)
        except Exception:
            logger.exception('[excel-import] auto-mark-delivered fallito (non bloccante)')
    summary['auto_marked_delivered'] = auto_marked
    summary['elapsed_seconds'] = round(time.time() - t0, 2)

    if not dry_run:
        logger.info(
            f"[excel-import] file={os.path.basename(file_path)} "
            f"export_ts={summary['export_ts_iso']} "
            f"nuove={summary['rows_nuove']} aggiornate={summary['rows_aggiornate']} "
            f"db_vince={summary['rows_db_vince']} celle={summary['celle_modificate']} "
            f"solo_db={summary['eneltel_solo_db']} auto_marked={auto_marked} "
            f"elapsed={summary['elapsed_seconds']}s"
        )

    return summary


def _insert_new_row(conn, eneltel_key, lcl, sheets_row, headers, fields, header_mapping,
                    *, export_ts, user, skip_columns=None):
    """Inserisce una riga nuova in `filone` (+ `filone_extra`).

    Le colonne che hanno chiave interna nota vanno in `filone`; le altre
    vanno in `filone_extra`. `last_local_change_ts` non viene impostato
    (è un import esterno, non una modifica locale).
    """
    # Costruisci dict di valori per colonne note di `filone`
    skip_columns = skip_columns or set()
    filone_cols = {}
    extra_cols = {}
    for col_name, val in fields.items():
        if col_name in skip_columns:
            continue
        chiave = header_mapping.get(col_name)
        val_str = _normalize_for_db(val, chiave)
        if chiave and chiave in _db.COLONNE_NOTE:
            filone_cols[chiave] = val_str
        elif chiave == 'eneltel':
            continue  # già gestito
        else:
            extra_cols[col_name] = val_str

    # Inserisci in filone
    row_hash = _db.compute_row_hash(list(filone_cols.values()))
    # Costruisci INSERT dinamico
    cols_sql = ['eneltel_key', 'lcl', 'sheets_row', 'row_hash', 'last_remote_pull_ts', 'is_orphan']
    vals_sql = [eneltel_key, lcl, sheets_row, row_hash, export_ts, 0]
    for k, v in filone_cols.items():
        cols_sql.append(k)
        vals_sql.append(v)
    placeholders = ', '.join('?' * len(cols_sql))
    cols_clause = ', '.join(cols_sql)
    try:
        conn.execute(
            f'INSERT INTO filone ({cols_clause}) VALUES ({placeholders})',
            vals_sql,
        )
    except Exception as e:
        logger.warning(f"[excel-import] INSERT filone fallita per {eneltel_key}: {e}")
        return False

    # Inserisci colonne extra
    for col_name, val in extra_cols.items():
        conn.execute(
            'INSERT OR REPLACE INTO filone_extra(eneltel_key, lcl, column_name, value) '
            'VALUES (?, ?, ?, ?)',
            (eneltel_key, lcl, col_name, val),
        )

    _db.audit(conn, eneltel_key=eneltel_key, column_name=None,
              old_value=None, new_value=None, source='excel_import',
              user=user, detail={'action': 'new_row', 'lcl': lcl,
                                 'sheets_row': sheets_row, 'export_ts': export_ts})
    return True


def _apply_excel_to_row(conn, eneltel_key, lcl, sheets_row, fields, header_mapping,
                        *, export_ts, user, dry_run):
    """Applica i valori Excel a una riga esistente, solo per le celle che differiscono.

    Ritorna il numero di celle effettivamente modificate.
    """
    n_changed = 0

    # Recupera stato corrente
    cur_row = conn.execute(
        "SELECT * FROM filone WHERE eneltel_key=? AND COALESCE(lcl,'')=?",
        (eneltel_key, lcl),
    ).fetchone()
    if cur_row is None:
        return 0
    cur_dict = dict(cur_row)

    # Cache delle colonne extra correnti
    cur_extra = {}
    for r in conn.execute(
        "SELECT column_name, value FROM filone_extra WHERE eneltel_key=? AND COALESCE(lcl,'')=?",
        (eneltel_key, lcl),
    ):
        cur_extra[r['column_name']] = r['value'] or ''

    filone_updates = {}   # {chiave_interna: nuovo_valore}
    extra_updates = {}    # {nome_colonna: nuovo_valore}

    for col_name, new_val_raw in fields.items():
        chiave = header_mapping.get(col_name)
        new_val = _normalize_for_db(new_val_raw, chiave)

        if chiave and chiave in _db.COLONNE_NOTE:
            old_val = ('' if cur_dict.get(chiave) is None
                       else str(cur_dict.get(chiave)).strip())
            if old_val == new_val:
                continue
            filone_updates[chiave] = (old_val, new_val)
        elif chiave == 'eneltel':
            continue
        else:
            old_val = cur_extra.get(col_name, '')
            if old_val == new_val:
                continue
            extra_updates[col_name] = (old_val, new_val)

    n_changed = len(filone_updates) + len(extra_updates)
    if n_changed == 0 or dry_run:
        return n_changed

    # Applica updates su filone
    if filone_updates:
        set_clause = ', '.join(f'{k}=?' for k in filone_updates)
        vals = [v[1] for v in filone_updates.values()]
        vals.extend([export_ts, eneltel_key, lcl])
        conn.execute(
            f"UPDATE filone SET {set_clause}, last_remote_pull_ts=? "
            f"WHERE eneltel_key=? AND COALESCE(lcl,'')=?",
            vals,
        )
        # Audit per cella
        for chiave, (old, new) in filone_updates.items():
            _db.audit(conn, eneltel_key=eneltel_key, column_name=chiave,
                      old_value=old, new_value=new, source='excel_import',
                      user=user, detail={'lcl': lcl, 'export_ts': export_ts})

    # Applica updates su filone_extra
    for col_name, (old, new) in extra_updates.items():
        conn.execute(
            'INSERT OR REPLACE INTO filone_extra(eneltel_key, lcl, column_name, value) '
            'VALUES (?, ?, ?, ?)',
            (eneltel_key, lcl, col_name, new),
        )
        _db.audit(conn, eneltel_key=eneltel_key, column_name=col_name,
                  old_value=old, new_value=new, source='excel_import',
                  user=user, detail={'lcl': lcl, 'export_ts': export_ts})

    return n_changed
