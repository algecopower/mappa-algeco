# -*- coding: utf-8 -*-
"""Importer del filone da file Excel.

Sostituisce il pull periodico da Google Sheets (rimosso il 21/05/2026).
L'admin scarica manualmente il foglio Sheets come .xlsx e lo carica via
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
import logging
import os
import time
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

# Date ISO YYYY-MM-DD (eventualmente con HH:MM:SS dopo)
import re as _re
_ISO_DATE_RE = _re.compile(r'^(\d{4})-(\d{2})-(\d{2})(?:\s+(\d{2}:\d{2}:\d{2}))?$')


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


def _find_col_idx(headers, candidates):
    """Cerca uno dei candidati in headers (case-insensitive). Ritorna idx o None."""
    lower = {h.strip().lower(): i for i, h in enumerate(headers) if h}
    for c in candidates:
        idx = lower.get(c.strip().lower())
        if idx is not None:
            return idx
    return None


def import_excel(file_path, *, export_ts=None, sheet_name=None, dry_run=False, user=None):
    """Importa il filone da file Excel applicando merge most-recent-wins.

    Args:
        file_path: path al .xlsx.
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
            'celle_modificate': int,
            'eneltel_solo_db': int,         # in DB ma NON in Excel (non cancellati)
            'dry_run': bool,
            'sample_db_vince': list[str],   # primi eneltel saltati per DB più recente
            'errori': list[str],
        }
    """
    export_ts_int = _parse_export_ts(export_ts)
    headers_xlsx, rows_xlsx = parse_excel(file_path, sheet_name=sheet_name)

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
        'header_mapping': header_mapping,
        'rows_excel': len(rows_xlsx),
        'rows_nuove': 0,
        'rows_aggiornate': 0,
        'rows_db_vince': 0,
        'rows_skip_no_eneltel': 0,
        'celle_modificate': 0,
        'eneltel_solo_db': 0,
        'dry_run': dry_run,
        'sample_db_vince': [],
        'errori': [],
    }

    if dry_run:
        # In dry-run NON modifichiamo nulla, ma proviamo a fare il merge
        # "virtuale" per riportare statistiche accurate.
        pass

    eneltel_visti_excel = set()

    with conn:
        if not dry_run:
            conn.execute('BEGIN IMMEDIATE')
            # Aggiorna column_registry con eventuali nuove colonne dell'Excel
            _db.update_column_registry(conn, headers_xlsx)

        for row_excel_idx, row in enumerate(rows_xlsx, start=2):  # +2 perché riga 1 = header
            eneltel_raw = row[eneltel_idx] if eneltel_idx < len(row) else ''
            eneltel_key = _db.normalize_eneltel(eneltel_raw)
            if not eneltel_key:
                summary['rows_skip_no_eneltel'] += 1
                continue
            eneltel_visti_excel.add(eneltel_key)

            lcl_raw = row[lcl_idx] if lcl_idx is not None and lcl_idx < len(row) else ''
            lcl = (lcl_raw or '').strip()

            # Costruisci dict {nome_colonna_excel: valore} per questa riga
            fields = {}
            for i, h in enumerate(headers_xlsx):
                if not h or h == 'Eneltel' or h.lower() == 'eneltel':
                    continue
                fields[h] = row[i] if i < len(row) else ''

            # Cerca riga DB matching (eneltel + lcl). Se non esiste con quel LCL
            # specifico, prova fallback alla prima riga dell'eneltel.
            db_row = _db.get_filone_row(eneltel_key, lcl=lcl if lcl else None)

            if db_row is None:
                # Riga NUOVA: crea
                summary['rows_nuove'] += 1
                if not dry_run:
                    _insert_new_row(conn, eneltel_key, lcl, row_excel_idx,
                                    headers_xlsx, fields, header_mapping,
                                    export_ts=export_ts_int, user=user)
                continue

            # Riga ESISTENTE: regola most-recent-wins
            db_last_local = db_row.get('last_local_change_ts') or 0
            if db_last_local > export_ts_int:
                # DB ha modifiche locali successive all'export → DB vince
                summary['rows_db_vince'] += 1
                if len(summary['sample_db_vince']) < 20:
                    summary['sample_db_vince'].append(eneltel_key)
                continue

            # Excel vince: applica le celle che differiscono
            n_changed = _apply_excel_to_row(
                conn, eneltel_key, db_row.get('lcl') or '',
                row_excel_idx, fields, header_mapping,
                export_ts=export_ts_int, user=user, dry_run=dry_run,
            )
            if n_changed > 0:
                summary['rows_aggiornate'] += 1
                summary['celle_modificate'] += n_changed

        # Conteggio eneltel solo in DB (in Excel non presenti)
        all_db = conn.execute(
            'SELECT eneltel_key FROM filone WHERE is_orphan=0'
        ).fetchall()
        all_db_keys = {r['eneltel_key'] for r in all_db}
        summary['eneltel_solo_db'] = len(all_db_keys - eneltel_visti_excel)

        if dry_run:
            # Rollback transparente (with conn aprirebbe TX, ma noi non l'abbiamo aperta)
            pass

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

    if not dry_run:
        logger.info(
            f"[excel-import] file={os.path.basename(file_path)} "
            f"export_ts={summary['export_ts_iso']} "
            f"nuove={summary['rows_nuove']} aggiornate={summary['rows_aggiornate']} "
            f"db_vince={summary['rows_db_vince']} celle={summary['celle_modificate']} "
            f"solo_db={summary['eneltel_solo_db']} auto_marked={auto_marked}"
        )

    return summary


def _insert_new_row(conn, eneltel_key, lcl, sheets_row, headers, fields, header_mapping,
                    *, export_ts, user):
    """Inserisce una riga nuova in `filone` (+ `filone_extra`).

    Le colonne che hanno chiave interna nota vanno in `filone`; le altre
    vanno in `filone_extra`. `last_local_change_ts` non viene impostato
    (è un import esterno, non una modifica locale).
    """
    # Costruisci dict di valori per colonne note di `filone`
    filone_cols = {}
    extra_cols = {}
    for col_name, val in fields.items():
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
        return

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
