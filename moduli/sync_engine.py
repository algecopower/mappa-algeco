# -*- coding: utf-8 -*-
"""
Sync engine — sincronizza il DB locale con Google Sheets.

Due flussi indipendenti:

1. PULL (Sheets → DB)
   `pull_once()` legge l'intero foglio, calcola hash riga per riga, applica
   i cambi remoti al DB con merge campo-per-campo. Conflitti (cella con
   pending in volo + valore remoto cambiato dalla base) vengono loggati
   in audit_log con source='conflict_local_wins' SENZA toccare il DB:
   il valore locale (pending) prevarrà al prossimo drain.

2. PUSH (DB → Sheets)
   `drain_once()` prende un batch di pending_writes in stato 'pending',
   le marca 'in_flight', e chiama il già esistente
   `safe_batch_update_by_name()` con anchor=Eneltel. Su successo le marca
   'done' e ricalcola row_hash. Su anchor lost / colonna mancante, le
   marca 'quarantined'. Su errore transitorio Sheets, retry con backoff.

Entrambi i flussi sono callabili sia in modalità "one-shot" (per test e
per integrazione col loop di sync esistente in server.py) sia in modalità
"loop daemon" via `start_workers()`.
"""
import os
import time
import logging
import threading

from . import db
from . import shared

logger = logging.getLogger('mappa_algeco')


# ============================================================================
# CONFIG ACCESSOR — leggiamo lazy così il modulo è importabile in test
# ============================================================================
def _cfg(name, default=None):
    try:
        import config as _config
        return getattr(_config, name, default)
    except ImportError:
        return default


# ============================================================================
# PULL: Sheets → DB
# ============================================================================
def pull_once(values=None):
    """Esegue una sincronizzazione PULL completa.

    Args:
        values: list[list[str]] — opzionale. Se fornito, usa questi dati
            invece di chiamare `get_all_values()` (utile per test e per
            riusare un download appena fatto da `data_loader.py`).

    Returns:
        dict con statistiche del pull (n righe, n cambi, n conflitti, ecc.)
    """
    t0 = time.time()

    if values is None:
        ws = shared.get_worksheet()
        values = shared._sheets_retry(ws.get_all_values)

    if not values or len(values) < 2:
        logger.warning("[pull] Foglio vuoto o senza header")
        return {'rows': 0, 'changed': 0, 'new': 0, 'conflicts': 0, 'orphans': 0}

    headers = values[0]
    body = values[1:]

    # Hash globale per skip totale se invariato
    sheet_hash = db.compute_row_hash(headers + [v for row in body for v in row])
    last_hash = db.sync_state_get('last_full_pull_sheet_hash')
    if sheet_hash == last_hash:
        db.sync_state_set('last_full_pull_ts', db._now_ts())
        logger.info("[pull] Sheet invariato (hash globale identico), skip processing")
        return {'rows': len(body), 'changed': 0, 'new': 0, 'conflicts': 0,
                'orphans': 0, 'skipped_global_hash': True}

    conn = db.get_conn()

    # Trova la colonna eneltel nell'header (per identificare le righe)
    col_eneltel_idx = _find_eneltel_column_index(conn, headers)
    if col_eneltel_idx is None:
        logger.error("[pull] Colonna Eneltel non trovata negli header del foglio. Pull abortito.")
        return {'rows': 0, 'changed': 0, 'new': 0, 'conflicts': 0,
                'orphans': 0, 'error': 'eneltel_column_missing'}

    # Mappa nome_colonna_foglio -> chiave_interna (None per colonne extra)
    # Costruita una sola volta per tutto il pull.
    internal_lookup = db._build_internal_lookup()
    col_to_internal = {}  # idx_0based -> chiave_interna (o None)
    for i, name in enumerate(headers):
        if not name:
            continue
        col_to_internal[i] = internal_lookup.get(name.strip().lower())

    n_changed = 0
    n_new = 0
    n_conflicts = 0
    eneltel_seen = set()
    duplicates = []

    with conn:
        conn.execute('BEGIN IMMEDIATE')

        # Aggiorna column_registry con header attuale
        db.update_column_registry(conn, headers)

        # Pulisci anomalie ricalcolabili: i duplicati e le righe senza Eneltel
        # vengono ri-rilevati dal pull, quindi le entries precedenti sono solo
        # rumore. Le altre kind (es. anomalie semantiche aggiunte in futuro)
        # restano per audit storico.
        conn.execute(
            "DELETE FROM filone_anomalies "
            "WHERE kind IN ('duplicate_eneltel', 'row_without_eneltel')"
        )

        for row_offset, row_values in enumerate(body):
            sheets_row = row_offset + 2  # 1-based + skip header
            eneltel_raw = (row_values[col_eneltel_idx]
                           if col_eneltel_idx < len(row_values) else '')
            eneltel_key = db.normalize_eneltel(eneltel_raw)

            if not eneltel_key:
                # riga senza Eneltel — anomalia da loggare ma non bloccante
                if any(str(v).strip() for v in row_values):
                    db.record_anomaly(
                        conn, kind='row_without_eneltel',
                        sheets_row=sheets_row,
                        detail={'first_values': row_values[:5]},
                    )
                continue

            if eneltel_key in eneltel_seen:
                duplicates.append({'eneltel_key': eneltel_key, 'sheets_row': sheets_row})
                db.record_anomaly(
                    conn, kind='duplicate_eneltel',
                    eneltel_key=eneltel_key, sheets_row=sheets_row,
                )
                continue
            eneltel_seen.add(eneltel_key)

            # Hash della riga (basato sui valori nell'ordine del foglio)
            row_hash = db.compute_row_hash(row_values)

            # Confronto con stato locale
            cur = conn.execute(
                'SELECT * FROM filone WHERE eneltel_key=?', (eneltel_key,)
            )
            existing = cur.fetchone()

            if existing is not None and existing['row_hash'] == row_hash and \
                    existing['sheets_row'] == sheets_row:
                # Riga invariata anche di posizione → solo aggiorna pull_ts
                conn.execute(
                    'UPDATE filone SET last_remote_pull_ts=?, is_orphan=0 WHERE eneltel_key=?',
                    (db._now_ts(), eneltel_key),
                )
                continue

            # Cambio rilevato (o riga nuova). Carico pending in volo per la riga.
            pending_by_col = _load_pending_by_col(conn, eneltel_key)

            # Costruisco typed_values + extra_values per upsert
            typed_values = {}
            extra_values = {}
            skip_columns = set()  # colonne con conflitto pending: NON sovrascrivere

            for col_idx, col_name in enumerate(headers):
                if not col_name or col_idx >= len(row_values):
                    continue
                remote_val = row_values[col_idx] if col_idx < len(row_values) else ''
                chiave = col_to_internal.get(col_idx)

                pending = pending_by_col.get(col_name)
                if pending:
                    # Pending in volo per questa cella — confronto con base_value
                    base_val = pending.get('base_value') or ''
                    remote_norm = '' if remote_val is None else str(remote_val)
                    if remote_norm != base_val:
                        # CONFLITTO: il valore remoto è cambiato dopo la base.
                        # Policy local_wins: NON aggiorno il DB su questa cella;
                        # il pending vincerà al prossimo drain.
                        db.audit(
                            conn,
                            eneltel_key=eneltel_key,
                            column_name=col_name,
                            old_value=base_val,
                            new_value=remote_val,
                            source='conflict_local_wins',
                            detail={
                                'pending_id': pending.get('id'),
                                'pending_value': pending.get('new_value'),
                                'sheets_row': sheets_row,
                            },
                        )
                        n_conflicts += 1
                    if chiave and chiave in db.COLONNE_NOTE:
                        skip_columns.add(chiave)
                    else:
                        skip_columns.add(col_name)
                    continue

                # Nessun pending: cambio remoto puro o riga nuova
                if chiave == 'eneltel':
                    continue  # è la PK, non la salvo come campo
                if chiave and chiave in db.COLONNE_NOTE:
                    typed_values[chiave] = _coerce_value(remote_val, chiave)
                    if existing is not None:
                        old = existing[chiave] if chiave in existing.keys() else None
                        if str(old or '') != str(remote_val or ''):
                            db.audit(
                                conn,
                                eneltel_key=eneltel_key,
                                column_name=col_name,
                                old_value=old,
                                new_value=remote_val,
                                source='remote',
                            )
                else:
                    extra_values[col_name] = remote_val

            db.upsert_filone_row(
                conn,
                eneltel_key=eneltel_key,
                sheets_row=sheets_row,
                typed_values=typed_values,
                extra_values=extra_values,
                row_hash=row_hash,
                source='remote',
                skip_columns=skip_columns,
            )

            if existing is None:
                n_new += 1
            else:
                n_changed += 1

        n_orphans = db.mark_orphans(conn, eneltel_seen)

        db.sync_state_set('last_full_pull_ts', db._now_ts())
        db.sync_state_set('last_full_pull_sheet_hash', sheet_hash)

    elapsed = time.time() - t0
    result = {
        'rows': len(body),
        'changed': n_changed,
        'new': n_new,
        'conflicts': n_conflicts,
        'orphans': n_orphans,
        'duplicates': len(duplicates),
        'elapsed': round(elapsed, 2),
    }
    if n_changed or n_new or n_conflicts or n_orphans:
        logger.info(f"[pull] {result}")
    return result


def _find_eneltel_column_index(conn, headers):
    """Trova l'indice 0-based della colonna Eneltel negli header forniti."""
    lookup = db._build_internal_lookup()
    for i, name in enumerate(headers):
        if not name:
            continue
        if lookup.get(name.strip().lower()) == 'eneltel':
            return i
    return None


def _load_pending_by_col(conn, eneltel_key):
    """Mappa {column_name: pending_dict} per le pending non ancora pushate."""
    cur = conn.execute(
        "SELECT id, column_name, new_value, base_value, state FROM pending_writes "
        "WHERE eneltel_key=? AND state IN ('pending','in_flight')",
        (eneltel_key,),
    )
    return {r['column_name']: dict(r) for r in cur.fetchall()}


def _coerce_value(value, chiave):
    """Coerce di un valore stringa al tipo SQL della colonna (REAL per lat/lon)."""
    if value is None:
        return None
    if chiave in ('latitudine', 'longitudine'):
        s = str(value).strip().replace(',', '.')
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None
    return str(value)


# ============================================================================
# PUSH: DB → Sheets
# ============================================================================
def drain_once(*, batch_size=None):
    """Drena un batch di pending_writes verso Google Sheets.

    Returns:
        dict con statistiche.
    """
    if _cfg('USE_DB_BACKEND', 'shadow') == 'off':
        return {'drained': 0, 'skipped': True, 'reason': 'USE_DB_BACKEND=off'}

    batch_size = batch_size or _cfg('DRAIN_BATCH_SIZE', 400)
    pending = db.list_pending_writes(state='pending', limit=batch_size)
    if not pending:
        return {'drained': 0}

    # Marca tutte come in_flight (lock applicativo)
    ids = [p['id'] for p in pending]
    db.mark_pending_in_flight(ids)

    # Risolvi nome ANCORA (Eneltel) sul foglio attuale
    shared.force_refresh_headers()
    eneltel_col_name = shared._resolve_anchor_col_name('eneltel') or 'Eneltel'

    # Costruisci items per safe_batch_update_by_name. Una pending = un item.
    # Servono le posizioni di riga: le prendiamo da filone.sheets_row, ma
    # safe_batch_update_by_name le rimappa al volo via ancora se cambiate.
    conn = db.get_conn()
    eneltel_keys = list({p['eneltel_key'] for p in pending})
    placeholders = ','.join('?' * len(eneltel_keys))
    rows_map = {
        r['eneltel_key']: r['sheets_row']
        for r in conn.execute(
            f'SELECT eneltel_key, sheets_row FROM filone WHERE eneltel_key IN ({placeholders})',
            eneltel_keys,
        ).fetchall()
    }

    items = []
    item_to_pending_id = []
    pending_without_row = []
    for p in pending:
        sheets_row = rows_map.get(p['eneltel_key'])
        if sheets_row is None:
            pending_without_row.append(p['id'])
            continue
        items.append({
            'row': sheets_row,
            'col': p['column_name'],
            'value': p['new_value'],
            'anchor': p['eneltel_key'],
        })
        item_to_pending_id.append(p['id'])

    # Pending verso eneltel non in DB locale: quarantina (caso strano,
    # tipicamente solo durante la migrazione iniziale).
    if pending_without_row:
        db.quarantine_pending(pending_without_row, 'no_row_in_local_filone')

    if not items:
        return {'drained': 0, 'quarantined': len(pending_without_row)}

    # Esegui la scrittura tramite l'API canonica (riusa lock + anchor remap)
    try:
        res = shared.safe_batch_update_by_name(
            items, anchor_col=eneltel_col_name, anchor_normalize='lstrip0',
        )
    except Exception as e:
        logger.exception("[drain] safe_batch_update_by_name ha sollevato eccezione")
        db.mark_pending_failed(item_to_pending_id, str(e), retry_in_seconds=60)
        return {'drained': 0, 'error': str(e)}

    # Distribuisci esiti per item:
    #   - lost_anchors → quarantine
    #   - invalid_cols → quarantine (colonna mancante sul foglio)
    #   - written/remapped → done
    lost_anchor_keys = {(la.get('row'), la.get('col'), str(la.get('anchor', '')).lstrip('0'))
                       for la in res.get('lost_anchors', [])}
    invalid_cols = set(res.get('invalid_cols', []))

    done_ids = []
    quarantined_ids = []
    quarantined_reasons = {}

    for item, pid, p in zip(items, item_to_pending_id, pending):
        anchor_key = (item['row'], item['col'], str(item['anchor']).lstrip('0'))
        if anchor_key in lost_anchor_keys:
            quarantined_ids.append(pid)
            quarantined_reasons[pid] = 'anchor_lost'
            continue
        if item['col'] in invalid_cols:
            quarantined_ids.append(pid)
            quarantined_reasons[pid] = 'column_missing'
            continue
        done_ids.append(pid)

    if done_ids:
        db.mark_pending_done(done_ids)
        # Aggiorna last_local_change_ts e invalida row_hash così il prossimo
        # pull rileverà il cambio scritto e ricalcolerà l'hash.
        with conn:
            placeholders = ','.join('?' * len(done_ids))
            conn.execute(
                f"UPDATE filone SET row_hash='' WHERE eneltel_key IN ("
                f"  SELECT eneltel_key FROM pending_writes WHERE id IN ({placeholders})"
                f")",
                done_ids,
            )

    for pid in quarantined_ids:
        db.quarantine_pending([pid], quarantined_reasons[pid])

    if not res.get('success'):
        logger.warning(
            f"[drain] write parziale: written={res.get('written')} "
            f"skipped={res.get('skipped')} invalid_cols={res.get('invalid_cols')} "
            f"lost_anchors={len(res.get('lost_anchors', []))}"
        )

    db.sync_state_set('last_drain_ts', db._now_ts())

    return {
        'drained': len(done_ids),
        'quarantined': len(quarantined_ids) + len(pending_without_row),
        'remapped': res.get('remapped', 0),
        'gsheets_result': {
            'written': res.get('written', 0),
            'skipped': res.get('skipped', 0),
            'success': res.get('success', False),
        },
    }


# ============================================================================
# WORKERS (loop daemon)
# ============================================================================
_workers_started = False
_workers_stop = threading.Event()
_drain_wakeup = threading.Event()


def trigger_drain_now():
    """Risveglia il drain worker per far partire subito un ciclo."""
    _drain_wakeup.set()


def _drain_loop():
    """Loop daemon che drena pending_writes verso Sheets."""
    interval = _cfg('DRAIN_INTERVAL_SECONDI', 10)
    logger.info(f"[drain] worker avviato (interval={interval}s)")
    while not _workers_stop.is_set():
        try:
            res = drain_once()
            if res.get('drained') or res.get('quarantined'):
                logger.info(f"[drain] {res}")
        except Exception:
            logger.exception("[drain] errore nel ciclo")

        # Aspetta interval o risveglio anticipato
        _drain_wakeup.wait(timeout=interval)
        _drain_wakeup.clear()
    logger.info("[drain] worker fermato")


def start_workers():
    """Avvia i worker daemon. Chiamato da server.py al boot.

    Idempotente: chiamate ripetute non avviano worker duplicati.
    """
    global _workers_started
    if _workers_started:
        return
    if _cfg('USE_DB_BACKEND', 'shadow') == 'off':
        logger.info("[sync_engine] USE_DB_BACKEND=off, worker NON avviati")
        return

    # Recovery: integrity check + reset in_flight (già fatto in db.bootstrap,
    # questo è un secondo livello di sicurezza).
    integrity = db.integrity_check()
    if integrity != 'ok':
        logger.error(f"[sync_engine] PRAGMA integrity_check FAIL: {integrity}")
    else:
        logger.info(f"[sync_engine] DB integrity OK: {db.stats()}")

    # In Fase 1 (shadow) il drainer non ha nulla da fare (le route scrivono
    # direttamente su Sheets), ma lasciamo il loop attivo per coprire eventuali
    # pending creati con `initial_state='pending'` (es. test, futura Fase 2).
    t = threading.Thread(target=_drain_loop, daemon=True, name='db-drain')
    t.start()

    _workers_started = True


def stop_workers():
    """Ferma i worker (chiamato in shutdown)."""
    _workers_stop.set()
    _drain_wakeup.set()
