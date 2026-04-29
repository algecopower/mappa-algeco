#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bootstrap del DB locale dal foglio Google Sheets (o da un backup JSON).

Idempotente: ri-eseguibile in qualsiasi momento. Usa il pull engine, quindi
righe già presenti vengono confrontate e aggiornate, righe nuove vengono
inserite, righe sparite vengono marcate orphan.

Uso:
    python tools/migrate_to_sqlite.py                # da Sheets
    python tools/migrate_to_sqlite.py --from-backup backup/torino/filone_torino_2026-04-29.json
    python tools/migrate_to_sqlite.py --reset        # cancella DB prima

Opzioni provincia: PROVINCIA=PAVIA python tools/migrate_to_sqlite.py
"""
import os
import sys
import json
import argparse
import logging

# Rendi importabili i moduli dal root del progetto
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
)


def _load_from_backup(path):
    """Carica headers + righe da un backup JSON di backup.py."""
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    headers = data.get('headers')
    righe = data.get('righe', [])
    if not headers:
        raise ValueError(f"Backup {path} senza chiave 'headers'")
    return [headers] + righe


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--from-backup', metavar='PATH',
                        help='Bootstrap da file backup JSON invece che da Sheets')
    parser.add_argument('--reset', action='store_true',
                        help='Cancella il DB esistente prima di ricostruire')
    args = parser.parse_args()

    from moduli import db, sync_engine

    if args.reset:
        path = db._get_db_path()
        for suffix in ('', '-wal', '-shm'):
            f = path + suffix
            if os.path.exists(f):
                print(f"🗑️  Cancello {f}")
                os.remove(f)
        # Reset cache thread-local
        db._db_initialized_paths.clear()
        if hasattr(db._thread_local, 'conn'):
            try:
                db._thread_local.conn.close()
            except Exception:
                pass
            del db._thread_local.conn

    # Inizializza schema (idempotente)
    db.get_conn()
    print(f"📊 Stato DB pre-migrazione: {db.stats()}")

    # Carica i dati
    if args.from_backup:
        print(f"📥 Carico dati da backup: {args.from_backup}")
        values = _load_from_backup(args.from_backup)
    else:
        print("📥 Carico dati da Google Sheets...")
        from moduli import shared
        ws = shared.get_worksheet()
        values = shared._sheets_retry(ws.get_all_values)
        print(f"   {len(values)-1} righe scaricate")

    # Esegui pull (idempotente: applica diff)
    print("🔄 Eseguo pull engine...")
    res = sync_engine.pull_once(values=values)
    print(f"✅ Pull completato: {res}")

    # Salva sheet_id per detection di sostituzione foglio
    try:
        from config import ID_FOGLIO_GOOGLE
        db.sync_state_set('last_sheet_id', ID_FOGLIO_GOOGLE)
    except ImportError:
        pass

    print(f"📊 Stato DB post-migrazione: {db.stats()}")
    print("✨ Bootstrap completato.")


if __name__ == '__main__':
    main()
