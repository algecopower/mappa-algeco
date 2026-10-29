#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Script di bonifica dati cross-provincia.

Rimuove dal DB di Torino i record appartenenti ai 20 LCL di Pavia
importati erroneamente il 20 ottobre 2026, oltre a eventuali record anomali
(es. 'quando la strada finisce').
Esegue prima un backup sicuro del database SQLite.
"""
import os
import sys
import time
import sqlite3

_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT_DIR)

PAVIA_LCLS_IN_TORINO = [
    '6600104176', '6600104177', '6600104434', '6600104435', '6600104439',
    '6600104453', '6600104454', '6600105540', '6600106128', '6600106129',
    '6600106610', '6600106612', '6600107120', '6600107229', '6600107703',
    '6600108227', '6600108760', '6600109375', '6600110023', '6600110478',
]

def backup_db(db_path: str) -> str:
    """Crea una copia di backup a caldo tramite SQLite backup API."""
    ts_str = time.strftime('%Y%m%d_%H%M%S')
    bak_path = f"{db_path}.bak_{ts_str}"
    print(f"Creazione backup di {db_path} in {bak_path}...")
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    dst = sqlite3.connect(bak_path)
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
    print(f"Backup completato: {bak_path} ({os.path.getsize(bak_path)} bytes)")
    return bak_path

def cleanup_torino(dry_run: bool = False):
    db_path = os.path.join(_ROOT_DIR, 'data', 'torino', 'state.db')
    if not os.path.exists(db_path):
        print(f"DB {db_path} non trovato!")
        return

    print(f"=== Bonifica Torino DB (dry_run={dry_run}) ===")
    
    if not dry_run:
        backup_db(db_path)

    conn = sqlite3.connect(db_path, timeout=30)
    cur = conn.cursor()

    placeholders = ','.join(['?'] * len(PAVIA_LCLS_IN_TORINO))
    
    # Conteggi preliminari
    cur.execute(f"SELECT COUNT(*) FROM filone WHERE lcl IN ({placeholders})", PAVIA_LCLS_IN_TORINO)
    count_filone_pavia = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM filone WHERE lcl = 'quando la strada finisce'")
    count_filone_strada = cur.fetchone()[0]

    cur.execute(f"SELECT COUNT(*) FROM filone_extra WHERE lcl IN ({placeholders})", PAVIA_LCLS_IN_TORINO)
    count_extra_pavia = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM filone_extra WHERE lcl = 'quando la strada finisce'")
    count_extra_strada = cur.fetchone()[0]

    cur.execute(f"SELECT COUNT(*) FROM cell_comments WHERE lcl IN ({placeholders})", PAVIA_LCLS_IN_TORINO)
    count_comments_pavia = cur.fetchone()[0]

    print("Trovate in filone da rimuovere:")
    print(f"  - Record Pavia LCL: {count_filone_pavia}")
    print(f"  - Record 'quando la strada finisce': {count_filone_strada}")
    print("Trovate in filone_extra da rimuovere:")
    print(f"  - Extra Pavia LCL: {count_extra_pavia}")
    print(f"  - Extra 'quando la strada finisce': {count_extra_strada}")
    print(f"Trovate in cell_comments da rimuovere: {count_comments_pavia}")

    if dry_run:
        print("Dry run completato. Nessuna modifica apportata.")
        conn.close()
        return

    # Esecuzione eliminazione in transazione
    print("\nEsecuzione eliminazione...")
    cur.execute("BEGIN IMMEDIATE")

    cur.execute(f"DELETE FROM filone WHERE lcl IN ({placeholders})", PAVIA_LCLS_IN_TORINO)
    deleted_filone = cur.rowcount

    cur.execute("DELETE FROM filone WHERE lcl = 'quando la strada finisce'")
    deleted_strada = cur.rowcount

    cur.execute(f"DELETE FROM filone_extra WHERE lcl IN ({placeholders})", PAVIA_LCLS_IN_TORINO)
    deleted_extra = cur.rowcount

    cur.execute("DELETE FROM filone_extra WHERE lcl = 'quando la strada finisce'")
    deleted_extra_strada = cur.rowcount

    cur.execute(f"DELETE FROM cell_comments WHERE lcl IN ({placeholders})", PAVIA_LCLS_IN_TORINO)
    deleted_comments = cur.rowcount

    # Audit log
    now_ts = int(time.time())
    detail = (
        f'{{"reason": "cleanup_cross_province_pavia_records", '
        f'"deleted_filone": {deleted_filone + deleted_strada}, '
        f'"deleted_extra": {deleted_extra + deleted_extra_strada}, '
        f'"deleted_comments": {deleted_comments}}}'
    )
    cur.execute(
        "INSERT INTO audit_log (ts, eneltel_key, column_name, old_value, new_value, source, user, detail) "
        "VALUES (?, NULL, NULL, NULL, NULL, 'maintenance_cleanup', 'antigravity', ?)",
        (now_ts, detail)
    )

    conn.commit()
    print("Eliminazione completata con successo:")
    print(f"  - filone rimossi: {deleted_filone + deleted_strada}")
    print(f"  - filone_extra rimossi: {deleted_extra + deleted_extra_strada}")
    print(f"  - cell_comments rimossi: {deleted_comments}")

    # Checkpoint
    try:
        cur.execute("PRAGMA wal_checkpoint(PASSIVE)")
    except Exception as e:
        print(f"Avviso checkpoint: {e}")

    conn.close()

if __name__ == '__main__':
    dry_run = '--dry-run' in sys.argv
    cleanup_torino(dry_run=dry_run)
