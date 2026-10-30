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
import math
import os
import sqlite3
import time
from collections import Counter
from datetime import date, datetime

from . import db as _db
from .safe_json import json_read  # noqa  (potrebbe servire per cache future)

logger = logging.getLogger('mappa_algeco')

_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _parse_export_ts(export_ts):
    """Normalizza il timestamp di export in int (epoch s).

    Accetta:
      - int/float (epoch s) → ritornato così com'è (int)
      - str 'YYYY-MM-DD HH:MM:SS' o 'YYYY-MM-DD' o 'YYYY-MM-DDTHH:MM:SS' o 'YYYY-MM-DDTHH:MM'
      - None → usa now() (assume export appena fatto)
    """
    if export_ts is None:
        return int(time.time())
    if isinstance(export_ts, (int, float)):
        return int(export_ts)
    s = str(export_ts).strip()
    for fmt in (
        '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%dT%H:%M',
        '%Y-%m-%d %H:%M',
        '%Y-%m-%d',
    ):
        try:
            return int(datetime.strptime(s, fmt).timestamp())
        except ValueError:
            continue
    raise ValueError(f"Formato timestamp non riconosciuto: {export_ts!r}")


def _cell_to_str(v):
    """Converte una cella Excel in stringa pulita (gestisce datetime, date, float, None)."""
    if v is None:
        return ''
    if isinstance(v, datetime):
        # Date: formato italiano (DD/MM/YYYY) per matchare il legacy del foglio
        if v.hour == 0 and v.minute == 0 and v.second == 0:
            return v.strftime('%d/%m/%Y')
        return v.strftime('%d/%m/%Y %H:%M:%S')
    if isinstance(v, date):
        return v.strftime('%d/%m/%Y')
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


def normalize_lcl(val):
    """Estrae e normalizza il codice LCL canonico (numerico).

    Gestisce:
      - float Excel (es. '6600108760.0' -> '6600108760')
      - suffissi operativi/anomalie (es. '6600109363 GUASTI', '6600109363 GUASTO' -> '6600109363')
      - prefissi o separatori (es. 'LCL 6600109363', '6600109363-GUASTI')
    """
    if val is None:
        return ''
    s = str(val).strip()
    if not s:
        return ''
    if s.endswith('.0') and s[:-2].isdigit():
        s = s[:-2]
    if s.isdigit():
        return s
    m = _re.search(r'(?:^|[^\d])(\d{4,20})(?:[^\d]|$)', s)
    if m:
        return m.group(1)
    return s


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
    if chiave_interna == 'lcl':
        return normalize_lcl(old_cmp) == normalize_lcl(new_cmp)
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
    - lcl: normalizzazione codice numerico (ignora suffissi operativi tipo GUASTI).
    - altre colonne: invariato (solo strip).
    """
    if value is None:
        return ''
    s = str(value).strip()
    if not s:
        return ''
    if chiave_interna == 'lcl':
        return normalize_lcl(s)
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


def parse_excel(file_path, sheet_name=None, return_meta=False):
    """Apre un .xlsx e ritorna (headers, rows) o (headers, rows, sheet_name, physical_to_logical).

    Supporta:
      - Selezione automatica del foglio contenente la colonna 'Eneltel' se non specificato
      - Rilevamento automatico della riga di intestazione (gestione righe vuote o banner)
      - Mappatura precisa tra riga fisica Excel e riga logica per commenti cella
    """
    try:
        import openpyxl
    except ImportError as e:
        raise RuntimeError(
            f"openpyxl non installato ({e}). pip install openpyxl"
        )

    wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
    if not wb.sheetnames:
        wb.close()
        raise ValueError("Nessun foglio trovato nel file Excel")

    target_sheet = None
    if sheet_name:
        if sheet_name in wb.sheetnames:
            target_sheet = sheet_name
        else:
            lower_map = {s.lower(): s for s in wb.sheetnames}
            if sheet_name.lower() in lower_map:
                target_sheet = lower_map[sheet_name.lower()]
            else:
                available = ', '.join(f"'{s}'" for s in wb.sheetnames)
                wb.close()
                raise ValueError(
                    f"Foglio '{sheet_name}' non trovato nel file Excel. "
                    f"Fogli disponibili: {available}"
                )

    if not target_sheet:
        active_title = wb.active.title if wb.active else wb.sheetnames[0]
        candidate_sheets = [active_title] + [s for s in wb.sheetnames if s != active_title]
        found_sheet = None
        for sname in candidate_sheets:
            try:
                ws_check = wb[sname]
                for row in ws_check.iter_rows(max_row=15, values_only=True):
                    if any('eneltel' in str(c or '').strip().lower() for c in row):
                        found_sheet = sname
                        break
                if found_sheet:
                    break
            except Exception:
                continue

        if found_sheet:
            target_sheet = found_sheet
            if target_sheet != active_title:
                logger.info(
                    f"[excel-import] Foglio '{target_sheet}' auto-selezionato "
                    f"(il foglio predefinito '{active_title}' non conteneva 'Eneltel')"
                )
        else:
            target_sheet = active_title

    ws = wb[target_sheet]

    # Prima passata rapida sulle prime 15 righe per determinare la riga fisica degli header
    header_physical_row = None
    for row in ws.iter_rows(max_row=15, values_only=False):
        if not row:
            continue
        row_vals = [_cell_to_str(c.value) for c in row]
        if all(v == '' for v in row_vals):
            continue
        phys_r = row[0].row if hasattr(row[0], 'row') and row[0].row else None
        if any('eneltel' in v.lower() for v in row_vals):
            header_physical_row = phys_r
            break
        if header_physical_row is None and any(v != '' for v in row_vals):
            header_physical_row = phys_r

    headers = []
    rows = []
    physical_to_logical = {}
    logical_row = 0

    for row in ws.iter_rows(values_only=False):
        if not row:
            continue
        row_vals = [_cell_to_str(c.value) for c in row]
        if all(v == '' for v in row_vals):
            continue

        phys_row = row[0].row if hasattr(row[0], 'row') and row[0].row else None

        if header_physical_row is not None and phys_row is not None and phys_row < header_physical_row:
            continue

        if not headers:
            headers = row_vals
            logical_row = 1
            if phys_row is not None:
                physical_to_logical[phys_row] = 1
            continue

        logical_row += 1
        if phys_row is not None:
            physical_to_logical[phys_row] = logical_row
        rows.append(row_vals)

    wb.close()

    if return_meta:
        return (headers, rows, target_sheet, physical_to_logical)
    return (headers, rows)


def _extract_xlsx_cell_comments(file_path, sheet_name=None, headers=None, physical_to_logical=None):
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

    if headers is None or physical_to_logical is None:
        headers, _, sheet_name, physical_to_logical = parse_excel(
            file_path, sheet_name=sheet_name, return_meta=True
        )

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


def parse_tabular_file(file_path, sheet_name=None, return_meta=False):
    """Legge .csv o .xlsx e ritorna (headers, rows, file_format) oppure se return_meta=True
    (headers, rows, file_format, actual_sheet, physical_to_logical)."""
    ext = os.path.splitext(file_path)[1].lower()
    if ext == '.csv':
        headers, rows = parse_csv(file_path)
        if return_meta:
            return headers, rows, 'csv', None, {}
        return headers, rows, 'csv'
    if ext == '.xlsx':
        headers, rows, actual_sheet, p2l = parse_excel(file_path, sheet_name=sheet_name, return_meta=True)
        if return_meta:
            return headers, rows, 'xlsx', actual_sheet, p2l
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
    lcl_clean = normalize_lcl(_normalize_for_compare(lcl))
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


def _get_province_db_lcls(province_slug):
    """Restituisce il set di LCL registrati nel DB locale della provincia indicata."""
    db_path = os.path.join(_ROOT_DIR, 'data', str(province_slug).lower(), 'state.db')
    if not os.path.exists(db_path):
        return set()
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        c = conn.cursor()
        lcls = {
            r[0] for r in c.execute(
                "SELECT DISTINCT lcl FROM filone WHERE is_orphan=0 AND lcl IS NOT NULL AND TRIM(lcl) != ''"
            ).fetchall() if r[0]
        }
        conn.close()
        return lcls
    except Exception:
        return set()


def validate_province_fit(rows_xlsx, headers_xlsx, current_province=None):
    """Verifica dinamicamente che i dati nel file appartengano alla provincia attiva.

    Confronta i dati del file (LCL, coordinate geografiche) con tutte le province
    configurate in `data/provinces.json`. Se rileva che il file appartiene palesemente
    a un'altra provincia registrata, solleva ValueError per bloccare l'inquinamento del DB.
    """
    if not rows_xlsx or not headers_xlsx:
        return

    if not current_province:
        import config
        current_province = os.environ.get('PROVINCIA') or getattr(config, 'PROVINCIA_ATTIVA', 'TORINO')
    current_province = str(current_province).upper().strip()

    provinces_path = os.path.join(_ROOT_DIR, 'data', 'provinces.json')
    if not os.path.exists(provinces_path):
        return

    try:
        with open(provinces_path, 'r', encoding='utf-8') as f:
            provinces = json.load(f)
    except Exception as e:
        logger.warning(f"[excel-import] lettura provinces.json non riuscita: {e}")
        return

    if len(provinces) <= 1:
        return

    lcl_idx = _find_col_idx(headers_xlsx, ['LCL', 'lcl'])
    lat_idx = _find_col_idx(headers_xlsx, ['Latitudine', 'latitudine', 'Lat', 'lat'])
    lon_idx = _find_col_idx(headers_xlsx, ['Longitudine', 'longitudine', 'Lon', 'lon'])

    file_lcls = set()
    file_coords = []

    for r in rows_xlsx[:1000]:
        if lcl_idx is not None and lcl_idx < len(r):
            val_lcl = normalize_lcl(r[lcl_idx])
            if val_lcl and _LCL_RE.match(val_lcl):
                file_lcls.add(val_lcl)
        if lat_idx is not None and lon_idx is not None and lat_idx < len(r) and lon_idx < len(r):
            lat_val = _parse_decimal(r[lat_idx])
            lon_val = _parse_decimal(r[lon_idx])
            if lat_val is not None and lon_val is not None:
                if 35.0 <= lat_val <= 48.0 and 5.0 <= lon_val <= 20.0:
                    file_coords.append((lat_val, lon_val))

    def _geo_dist(lat1, lon1, lat2, lon2):
        x = (math.radians(lon2) - math.radians(lon1)) * math.cos(math.radians((lat1 + lat2) / 2))
        y = math.radians(lat2) - math.radians(lat1)
        return math.sqrt(x * x + y * y) * 6371.0

    curr_cfg = provinces.get(current_province, {})
    curr_nome = curr_cfg.get('nome') or current_province
    curr_dep = curr_cfg.get('deposito', {})

    # 1. Controllo incrociato LCL con i database delle altre province
    if file_lcls:
        curr_db_lcls = _get_province_db_lcls(current_province)

        for other_slug, other_cfg in provinces.items():
            if other_slug.upper() == current_province:
                continue
            other_db_lcls = _get_province_db_lcls(other_slug)
            if not other_db_lcls:
                continue

            match_other = file_lcls.intersection(other_db_lcls)
            match_curr = file_lcls.intersection(curr_db_lcls)

            if match_other:
                if len(match_other) > len(match_curr) and len(match_other) >= max(1, int(len(file_lcls) * 0.3)):
                    other_nome = other_cfg.get('nome') or other_slug
                    sample_str = ', '.join(sorted(list(match_other))[:4])
                    raise ValueError(
                        f"Import bloccato: il file sembra appartenere alla provincia di {other_nome} "
                        f"(rilevati LCL di {other_nome}: {sample_str}), mentre sei sull'istanza di {curr_nome}. "
                        f"Verifica la provincia corretta prima di caricare il file."
                    )

    # 2. Controllo geografico delle coordinate
    if len(file_coords) >= 10 and curr_dep and 'Lat' in curr_dep and 'Lon' in curr_dep:
        try:
            c_lat, c_lon = float(curr_dep['Lat']), float(curr_dep['Lon'])
            dist_curr = [_geo_dist(lat, lon, c_lat, c_lon) for lat, lon in file_coords]
            avg_dist_curr = sum(dist_curr) / len(dist_curr)
        except Exception as e_calc:
            avg_dist_curr = None
            logger.debug(f"[excel-import] calcolo distanze provincia corrente fallito: {e_calc}")

        if avg_dist_curr is not None:
            for other_slug, other_cfg in provinces.items():
                if other_slug.upper() == current_province:
                    continue
                o_dep = other_cfg.get('deposito', {})
                if not o_dep or 'Lat' not in o_dep or 'Lon' not in o_dep:
                    continue
                try:
                    o_lat, o_lon = float(o_dep['Lat']), float(o_dep['Lon'])
                    dist_other = [_geo_dist(lat, lon, o_lat, o_lon) for lat, lon in file_coords]
                    avg_dist_other = sum(dist_other) / len(dist_other)
                except Exception:
                    continue

                if avg_dist_curr > 70.0 and avg_dist_other < 35.0 and (avg_dist_curr - avg_dist_other) > 35.0:
                    other_nome = other_cfg.get('nome') or other_slug
                    raise ValueError(
                        f"Import bloccato: le coordinate nel file ricadono nella zona di {other_nome} "
                        f"(distanza media {avg_dist_other:.1f} km da {other_nome} vs {avg_dist_curr:.1f} km da {curr_nome}). "
                        f"Verifica la provincia corretta prima di caricare il file."
                    )


def import_excel(
    file_path,
    *,
    export_ts=None,
    sheet_name=None,
    dry_run=False,
    user=None,
    auto_mark_delivered=True,
    protect_pending_rows=False,
    current_province=None,
):
    """Importa il filone da file Excel/CSV applicando merge most-recent-wins.

    Args:
        file_path: path al .csv/.xlsx.
        export_ts: timestamp dell'export (str o int). Default: now.
        sheet_name: nome scheda Excel da leggere (default: prima scheda).
        dry_run: se True, calcola tutto ma NON scrive sul DB.
        user: nome utente che ha caricato il file (per audit).
        auto_mark_delivered: se True, marca pending_writes <= export_ts come 'done'.
                             Se False (es. auto-import orario), non tocca la outbox.
        protect_pending_rows: se True, protegge da sovrascrittura qualunque riga
                              con modifiche locali in stato 'pending' nella outbox.

    Returns:
        dict summary
    """
    export_ts_int = _parse_export_ts(export_ts)
    headers_xlsx, rows_xlsx, file_format, actual_sheet, p2l = parse_tabular_file(
        file_path, sheet_name=sheet_name, return_meta=True,
    )

    if not headers_xlsx:
        fogli_info = f" (foglio: '{actual_sheet}')" if actual_sheet else ""
        return {
            'success': False, 'errori': [f"Excel vuoto / nessuna intestazione{fogli_info}"],
            'rows_excel': 0,
        }

    # 1. Header mapping: per ogni colonna Excel, cerca la chiave interna
    # Usa prima il lookup da config/settings per riflettere le definizioni correnti.
    # Evita collisioni: se più colonne mappano sulla stessa chiave interna,
    # solo la prima mantiene la chiave e le altre diventano extra (None).
    header_mapping = {}
    conn = _db.get_conn()
    lookup = _db._build_internal_lookup()
    seen_keys = {}
    for h in headers_xlsx:
        if not h:
            continue
        ch = lookup.get(h.strip().lower())
        if not ch:
            ch = _db.column_to_internal_key(conn, h)
        if ch:
            if ch in seen_keys:
                prev_h = seen_keys[ch]
                logger.warning(
                    f"[excel-import] Collisione mappatura colonne: {prev_h!r} e {h!r} "
                    f"mappano entrambe su {ch!r}. {prev_h!r} mantiene la chiave, "
                    f"{h!r} viene trattata come colonna extra."
                )
                header_mapping[h] = None
            else:
                seen_keys[ch] = h
                header_mapping[h] = ch
        else:
            header_mapping[h] = None

    # 2. Identifica colonna Eneltel (obbligatoria)
    eneltel_idx = _find_col_idx(headers_xlsx, ['Eneltel', 'ENELTEL', 'eneltel'])
    if eneltel_idx is None:
        for col_name, ch_int in header_mapping.items():
            if ch_int == 'eneltel':
                eneltel_idx = _find_col_idx(headers_xlsx, [col_name])
                break

    if eneltel_idx is None:
        sheet_info = f" nel foglio '{actual_sheet}'" if actual_sheet else ""
        return {
            'success': False,
            'errori': [f"Colonna Eneltel non trovata{sheet_info}. Headers presenti: {headers_xlsx[:30]}"],
            'rows_excel': len(rows_xlsx),
        }

    # 3. LCL opzionale (per Pavia: multi-LCL per stesso eneltel)
    lcl_idx = _find_col_idx(headers_xlsx, ['LCL', 'lcl'])
    required_row_indices = _structural_col_indices(headers_xlsx) if file_format == 'csv' else []
    expected_row_cols = len(headers_xlsx) if file_format == 'csv' else None

    # Controllo dinamico coerenza geografica / LCL con la provincia attiva
    validate_province_fit(rows_xlsx, headers_xlsx, current_province=current_province)

    summary = {
        'success': True,
        'export_ts': export_ts_int,
        'export_ts_iso': datetime.fromtimestamp(export_ts_int).strftime('%Y-%m-%d %H:%M:%S'),
        'headers_excel': headers_xlsx,
        'file_format': file_format,
        'sheet_name': actual_sheet,
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
            for item in _extract_xlsx_cell_comments(
                file_path, sheet_name=actual_sheet, headers=headers_xlsx, physical_to_logical=p2l
            ):
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

        # Eneltel con pending_writes attive (se protect_pending_rows=True)
        pending_eneltels = set()
        if protect_pending_rows:
            try:
                p_rows = conn.execute(
                    "SELECT DISTINCT eneltel_key FROM pending_writes WHERE state='pending'"
                ).fetchall()
                pending_eneltels = {r[0] for r in p_rows if r[0]}
                logger.info(
                    f"[excel-import] {len(pending_eneltels)} contatori con modifiche "
                    f"pendenti protetti da overwrite"
                )
            except Exception as e:
                logger.warning(f"[excel-import] errore recupero pending_eneltels: {e}")

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
            lcl = normalize_lcl(lcl_raw)
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

            # Most-recent-wins a livello riga (o protezione riga pendente attiva)
            db_last_local = db_row.get('last_local_change_ts') or 0
            if (protect_pending_rows and eneltel_key in pending_eneltels) or db_last_local > export_ts_int:
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
    # Se auto_mark_delivered=False (es. auto-import da Google Sheets), non tocca l'outbox.
    auto_marked = 0
    if not dry_run and auto_mark_delivered:
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
