# -*- coding: utf-8 -*-
"""Outbox: modifiche locali pronte per l'export cambiamenti.

Post-disconnessione Google Sheets (2026-05-21), il programma scrive solo
nel DB. Le `pending_writes` in stato 'pending' rappresentano la coda di
modifiche ancora da gestire fuori dal DB locale.

Flusso:
  - Utente fa modifica → record in `pending_writes` (state='pending')
  - Utente clicca "Scarica cambiamenti" → questo modulo produce un .xlsx
    compatto con solo POD e celle modificate. Ordinato per `sheets_row`.
  - Utente clicca "Segna gestite" → tutte le pending correnti → state='done'.
  - (Oppure) al prossimo import-excel le pending pre-export vengono
    auto-marcate (vedi `excel_importer.py`).
"""
import io
import logging

from . import db as _db

logger = logging.getLogger('mappa_algeco')


def _list_pending_coalesced():
    """Per ogni (eneltel_key, lcl, column_name), tiene solo il pending più
    recente (created_ts massimo). Coalescing della storia in un singolo
    valore "ultimo" da esportare.

    Returns:
        list[dict] con chiavi: eneltel_key, lcl, pod, column_name, new_value,
        created_ts, sheets_row.
    """
    conn = _db.get_conn()
    rows = conn.execute('''
        SELECT pw.eneltel_key, pw.lcl, pw.column_name, pw.new_value,
               pw.created_ts, f.sheets_row, f.pod
        FROM pending_writes pw
        LEFT JOIN filone f ON f.eneltel_key = pw.eneltel_key
                          AND COALESCE(f.lcl, '') = COALESCE(pw.lcl, '')
        WHERE pw.state = 'pending'
          AND pw.id IN (
              SELECT MAX(id) FROM pending_writes
              WHERE state = 'pending'
              GROUP BY eneltel_key, COALESCE(lcl, ''), column_name
          )
        ORDER BY f.sheets_row, pw.eneltel_key, pw.column_name
    ''').fetchall()
    return [dict(r) for r in rows]


def stats():
    """Conteggio pending + ultime info (per la dashboard admin)."""
    conn = _db.get_conn()
    r = conn.execute(
        "SELECT COUNT(*) AS n_pending, COUNT(DISTINCT eneltel_key) AS n_eneltel, "
        "MIN(created_ts) AS oldest_ts, MAX(created_ts) AS newest_ts "
        "FROM pending_writes WHERE state = 'pending'"
    ).fetchone()
    # Coalesced count (le celle uniche da scrivere realmente)
    n_unique = conn.execute(
        "SELECT COUNT(*) AS n FROM ("
        "  SELECT 1 FROM pending_writes WHERE state='pending' "
        "  GROUP BY eneltel_key, COALESCE(lcl,''), column_name"
        ")"
    ).fetchone()['n']
    return {
        'n_pending_total': r['n_pending'] or 0,
        'n_pending_unique_cells': n_unique,
        'n_eneltel_touched': r['n_eneltel'] or 0,
        'oldest_ts': r['oldest_ts'],
        'newest_ts': r['newest_ts'],
    }


def build_outbox_xlsx():
    """Genera il file Excel dei cambiamenti con SOLO le celle interessate.

    Layout compatto:
      - Un solo foglio.
      - Prima colonna sempre `POD`, usata come identificativo leggibile.
      - A seguire solo le colonne con modifiche pendenti, nell'ordine del
        `column_registry`; niente colonne vuote globali, niente Eneltel.
      - Una riga per ogni contatore con almeno una modifica esportabile.
      - Celle modificate con nuovo valore + highlight giallo.

    Returns:
        (bytes, n_righe, n_celle): payload xlsx, n. righe scritte, n. celle valorizzate.
    """
    try:
        import openpyxl
        from openpyxl.styles import PatternFill, Font
    except ImportError as e:
        raise RuntimeError(f"openpyxl non installato: {e}")

    pending = _list_pending_coalesced()
    if not pending:
        return (None, 0, 0)

    # Mappa headers dal DB: serve solo per ordinare le colonne modificate.
    conn = _db.get_conn()
    headers_rows = conn.execute('''
        SELECT cr.column_name, cr.last_index, cr.chiave_interna
        FROM column_registry cr
        WHERE cr.last_seen_ts = (
            SELECT MAX(cr2.last_seen_ts)
            FROM column_registry cr2
            WHERE cr2.last_index = cr.last_index
        )
        ORDER BY cr.last_index
    ''').fetchall()
    header_order = {
        (r['column_name'] or '').strip().lower(): (r['last_index'] or 999999)
        for r in headers_rows
        if r['column_name']
    }

    # Aggregazione pending per (eneltel, lcl) → {col_name: new_value}
    by_row = {}
    changed_cols = {}
    for p in pending:
        col_name = p['column_name'] or ''
        chiave = _db.column_to_internal_key(conn, col_name)
        if chiave == 'eneltel' or col_name.strip().lower() in ('eneltel', 'eneltel_key'):
            continue

        ek = p['eneltel_key']
        lcl = p['lcl'] or ''
        key = (ek, lcl)
        if key not in by_row:
            by_row[key] = {
                '_sheets_row': p['sheets_row'] or 999999,
                '_eneltel': ek,
                '_lcl': lcl,
                '_pod': p['pod'] or '',
                '_pod_changed': False,
                'cells': {},
            }

        if chiave == 'pod':
            by_row[key]['_pod'] = p['new_value'] or ''
            by_row[key]['_pod_changed'] = True
            continue

        by_row[key]['cells'][col_name] = p['new_value'] or ''
        col_key = col_name.strip().lower()
        if col_key and col_key not in changed_cols:
            changed_cols[col_key] = col_name

    ordered = sorted(
        (r for r in by_row.values() if r['_pod_changed'] or r['cells']),
        key=lambda r: (r['_sheets_row'], r['_eneltel']),
    )
    if not ordered:
        return (None, 0, 0)

    changed_headers = sorted(
        changed_cols.values(),
        key=lambda h: (header_order.get(h.strip().lower(), 999999), h.strip().lower()),
    )
    headers = ['POD'] + changed_headers

    # Crea workbook
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Cambiamenti'

    # Header (con stile)
    bold = Font(bold=True)
    for col_idx, h in enumerate(headers, start=1):
        c = ws.cell(row=1, column=col_idx, value=h)
        c.font = bold

    # Fill per highlight celle modificate
    yellow = PatternFill(start_color='FFF59D', end_color='FFF59D', fill_type='solid')

    # Mappa header → indice 1-based
    header_to_idx = {h: i + 1 for i, h in enumerate(headers)}
    header_to_idx_ci = {h.strip().lower(): i + 1 for i, h in enumerate(headers)}

    # Scrivi righe ordinate per sheets_row
    n_celle = 0
    for row_idx, info in enumerate(ordered, start=2):
        pod_cell = ws.cell(row=row_idx, column=1, value=info['_pod'])
        if info['_pod_changed']:
            pod_cell.fill = yellow
            n_celle += 1
        # Celle modificate con highlight
        for col_name, new_val in info['cells'].items():
            idx = header_to_idx.get(col_name) or header_to_idx_ci.get(col_name.strip().lower())
            if idx is None:
                continue
            c = ws.cell(row=row_idx, column=idx, value=new_val)
            c.fill = yellow
            n_celle += 1

    # Larghezza colonne auto (approssimativa)
    for col_idx in range(1, len(headers) + 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(col_idx)].width = 18

    # Freeze prima riga
    ws.freeze_panes = 'A2'

    # Serializza
    buf = io.BytesIO()
    wb.save(buf)
    wb.close()
    return (buf.getvalue(), len(ordered), n_celle)


def build_full_xlsx():
    """Snapshot completo del filone dal DB → Excel (.xlsx).

    Esporta TUTTE le righe (escluse le orfane) e TUTTE le colonne registrate,
    nell'ordine delle colonne registrate. Include il pending overlay (così l'export
    riflette lo stato corrente come lo vede l'utente, comprese modifiche non
    ancora da gestire). Niente highlight: è un dump pulito.

    Returns:
        (bytes, n_righe, n_colonne): payload xlsx + statistiche.
    """
    try:
        import openpyxl
        from openpyxl.styles import Font
    except ImportError as e:
        raise RuntimeError(f"openpyxl non installato: {e}")

    headers, rows = _db.dump_filone_as_rows(
        include_orphans=False,
        apply_pending_overlay=True,
    )
    if not headers:
        return (None, 0, 0)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Filone'

    bold = Font(bold=True)
    for col_idx, h in enumerate(headers, start=1):
        c = ws.cell(row=1, column=col_idx, value=h)
        c.font = bold

    for row_idx, r in enumerate(rows, start=2):
        for col_idx, h in enumerate(headers, start=1):
            val = r.get(h, '')
            ws.cell(row=row_idx, column=col_idx, value=val)

    for col_idx in range(1, len(headers) + 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(col_idx)].width = 16
    ws.freeze_panes = 'A2'

    buf = io.BytesIO()
    wb.save(buf)
    wb.close()
    return (buf.getvalue(), len(rows), len(headers))


def mark_delivered(*, before_ts=None, user=None):
    """Marca pending in stato 'done' (= gestite).

    Args:
        before_ts: se fornito, marca SOLO le pending con created_ts <= before_ts.
                   Utile per auto-mark dopo import-excel (le precedenti
                   all'export sono già state gestite). Se None, marca TUTTE
                   le pending correnti.
        user: chi ha cliccato (per audit).

    Returns:
        dict {marked: int, before_ts: int|None}
    """
    conn = _db.get_conn()
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        if before_ts is not None:
            cur = conn.execute(
                "UPDATE pending_writes SET state='done' "
                "WHERE state='pending' AND created_ts <= ?",
                (int(before_ts),),
            )
        else:
            cur = conn.execute(
                "UPDATE pending_writes SET state='done' WHERE state='pending'"
            )
        marked = cur.rowcount or 0
        # Audit aggregato
        _db.audit(
            conn,
            eneltel_key=None, column_name=None,
            old_value=None, new_value=str(marked),
            source='outbox_delivered', user=user,
            detail={'before_ts': before_ts, 'marked': marked},
        )
    logger.info(f"[outbox] mark_delivered: {marked} pending → done (before_ts={before_ts}, user={user})")
    return {'marked': marked, 'before_ts': before_ts}
