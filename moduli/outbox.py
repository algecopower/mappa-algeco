# -*- coding: utf-8 -*-
"""Outbox: modifiche locali pronte per essere consegnate al foglio Google.

Post-disconnessione Google Sheets (2026-05-21), il programma scrive solo
nel DB. Le `pending_writes` in stato 'pending' rappresentano la coda di
modifiche che la collega deve riportare a mano sul foglio.

Flusso:
  - Utente fa modifica → record in `pending_writes` (state='pending')
  - Utente clicca "Scarica delta" → questo modulo produce un .xlsx sparso
    con highlight sulle celle da copiare. Ordinato per `sheets_row`.
  - Utente lo manda alla collega che lo applica sul foglio.
  - Utente clicca "Segna consegnate" → tutte le pending correnti → state='done'.
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
    valore "ultimo" da applicare al foglio.

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
    """Genera il file Excel "delta" con SOLO le celle da copiare sul foglio.

    Layout sparso:
      - Header riga 1: stessi nomi colonna del foglio (da column_registry).
      - Una riga per ogni eneltel con almeno una pending; le celle senza
        pending sono VUOTE (= "non toccare"); le celle con pending hanno
        il nuovo valore + highlight giallo. Eneltel e LCL sempre valorizzati
        (per identificare la riga).
      - Ordine: per `sheets_row` (così la collega scorre il file nello stesso
        ordine del foglio).

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

    # Mappa headers dal DB (riusa lo stesso ordine del foglio originale)
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
    headers = [r['column_name'] for r in headers_rows]
    # chiave_interna 'eneltel' → quale header è l'Eneltel; per fallback assumiamo 'Eneltel'
    eneltel_col_name = None
    lcl_col_name = None
    for r in headers_rows:
        if r['chiave_interna'] == 'eneltel':
            eneltel_col_name = r['column_name']
        if r['chiave_interna'] == 'lcl' or r['column_name'].strip().upper() == 'LCL':
            lcl_col_name = r['column_name']
    if not eneltel_col_name:
        eneltel_col_name = 'Eneltel'

    # Aggregazione pending per (eneltel, lcl) → {col_name: new_value}
    by_row = {}
    for p in pending:
        ek = p['eneltel_key']
        lcl = p['lcl'] or ''
        key = (ek, lcl)
        if key not in by_row:
            by_row[key] = {
                '_sheets_row': p['sheets_row'] or 999999,
                '_eneltel': ek,
                '_lcl': lcl,
                '_pod': p['pod'] or '',
                'cells': {},
            }
        by_row[key]['cells'][p['column_name']] = p['new_value'] or ''

    # Crea workbook
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Da applicare al foglio'

    # Header (con stile)
    bold = Font(bold=True)
    for col_idx, h in enumerate(headers, start=1):
        c = ws.cell(row=1, column=col_idx, value=h)
        c.font = bold

    # Fill per highlight celle modificate
    yellow = PatternFill(start_color='FFF59D', end_color='FFF59D', fill_type='solid')

    # Mappa header → indice 1-based (case-sensitive + lookup case-insensitive)
    header_to_idx = {h: i + 1 for i, h in enumerate(headers)}
    header_lookup_ci = {h.strip().lower(): h for h in headers}

    def _resolve_header(name):
        """Match esatto, poi case-insensitive."""
        if name in header_to_idx:
            return name
        match = header_lookup_ci.get(name.strip().lower())
        return match if match else name

    # Scrivi righe ordinate per sheets_row
    ordered = sorted(by_row.values(), key=lambda r: (r['_sheets_row'], r['_eneltel']))
    n_celle = 0
    for row_idx, info in enumerate(ordered, start=2):
        # Eneltel sempre presente per identificare
        ek_col = header_to_idx.get(eneltel_col_name)
        if ek_col:
            ws.cell(row=row_idx, column=ek_col, value=info['_eneltel'])
        # LCL se esiste
        if lcl_col_name and info['_lcl']:
            lcl_col = header_to_idx.get(lcl_col_name)
            if lcl_col:
                ws.cell(row=row_idx, column=lcl_col, value=info['_lcl'])
        # Celle modificate con highlight
        for col_name, new_val in info['cells'].items():
            resolved = _resolve_header(col_name)
            idx = header_to_idx.get(resolved)
            if idx is None:
                # Colonna non nel registry: la aggiungiamo in coda (caso raro,
                # es. pending verso una colonna creata dopo l'ultimo import)
                headers.append(col_name)
                header_to_idx[col_name] = len(headers)
                header_lookup_ci[col_name.strip().lower()] = col_name
                idx = len(headers)
                c = ws.cell(row=1, column=idx, value=col_name)
                c.font = bold
            c = ws.cell(row=row_idx, column=idx, value=new_val)
            c.fill = yellow
            n_celle += 1

    # Larghezza colonne auto (approssimativa)
    for col_idx in range(1, len(headers) + 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(col_idx)].width = 18

    # Freeze prima riga
    ws.freeze_panes = 'A2'

    # Aggiungi un secondo foglio "Riepilogo" più compatto (per audit visivo)
    ws2 = wb.create_sheet('Riepilogo cambi')
    ws2.append(['POD', 'LCL', 'Colonna', 'Nuovo valore', 'Eneltel'])
    for c in ws2[1]:
        c.font = bold
    n_riep = 0
    for info in ordered:
        for col_name, new_val in info['cells'].items():
            ws2.append([info['_pod'], info['_lcl'], col_name, new_val, info['_eneltel']])
            n_riep += 1
    for col_idx in range(1, 6):
        ws2.column_dimensions[openpyxl.utils.get_column_letter(col_idx)].width = 22
    ws2.freeze_panes = 'A2'

    # Serializza
    buf = io.BytesIO()
    wb.save(buf)
    wb.close()
    return (buf.getvalue(), len(ordered), n_celle)


def build_full_xlsx():
    """Snapshot completo del filone dal DB → Excel (.xlsx).

    Esporta TUTTE le righe (escluse le orfane) e TUTTE le colonne registrate,
    nell'ordine del foglio originale. Include il pending overlay (così l'export
    riflette lo stato corrente come lo vede l'utente, comprese modifiche non
    ancora consegnate). Niente highlight: è un dump pulito.

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
    """Marca pending in stato 'done' (= consegnate alla collega).

    Args:
        before_ts: se fornito, marca SOLO le pending con created_ts <= before_ts.
                   Utile per auto-mark dopo import-excel ("le precedenti
                   all'export sono già nel foglio"). Se None, marca TUTTE
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
