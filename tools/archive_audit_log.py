#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Archivia entries di `audit_log` più vecchie di N giorni in un file JSONL
compresso, poi le rimuove dal DB.

Pensato per girare via cron una volta al mese, così il DB resta snello e
le query di audit recenti restano veloci. L'archivio storico resta
disponibile come file sul disco per ispezione offline.

Uso:
    python tools/archive_audit_log.py                  # dry-run
    python tools/archive_audit_log.py --apply
    python tools/archive_audit_log.py --apply --max-age-days 90

Output file:
    backup/{provincia}/audit_log_archive_YYYY-MM.jsonl.gz

Una entry per riga, JSON lines. Multiple esecuzioni nello stesso mese
appendono allo stesso file (no duplicazione, le entry archiviate vengono
poi rimosse dal DB con DELETE).

Setup cron (esempio):
    0 4 1 * * cd /opt/mappa-algeco && /opt/mappa-algeco/venv/bin/python \\
        tools/archive_audit_log.py --apply >> /var/log/mappa-audit-archive.log 2>&1
"""
import os
import sys
import gzip
import json
import time
import argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--apply', action='store_true',
                        help='Esegue l\'archiviazione (default: dry-run)')
    parser.add_argument('--max-age-days', type=int, default=180,
                        help='Entries più vecchie di N giorni vengono archiviate (default 180)')
    parser.add_argument('--batch-size', type=int, default=5000,
                        help='Dimensione batch per delete (default 5000)')
    args = parser.parse_args()

    from moduli import db as _db
    from config import PROVINCIA_ATTIVA

    conn = _db.get_conn()
    cutoff = int(time.time()) - args.max_age_days * 86400

    n_total = conn.execute(
        'SELECT COUNT(*) FROM audit_log WHERE ts < ?', (cutoff,)
    ).fetchone()[0]

    print(f"[{PROVINCIA_ATTIVA}] Audit entries più vecchie di {args.max_age_days}g: {n_total}")

    if n_total == 0:
        print("✅ Niente da archiviare.")
        return

    if not args.apply:
        # Mostra range di date
        first = conn.execute(
            'SELECT MIN(ts), MAX(ts) FROM audit_log WHERE ts < ?', (cutoff,)
        ).fetchone()
        print(f"  Range date: {datetime.fromtimestamp(first[0]):%Y-%m-%d} → "
              f"{datetime.fromtimestamp(first[1]):%Y-%m-%d}")
        print("🔍 DRY-RUN. Esegui con --apply per archiviare.")
        return

    # File archivio: per mese dell'esecuzione (così multiple esecuzioni in
    # un mese appendono allo stesso file).
    archive_dir = os.path.join('backup', PROVINCIA_ATTIVA.lower())
    os.makedirs(archive_dir, exist_ok=True)
    month_tag = datetime.now().strftime('%Y-%m')
    archive_path = os.path.join(archive_dir, f'audit_log_archive_{month_tag}.jsonl.gz')

    print(f"📦 Archivio: {archive_path}")

    n_archived = 0
    n_deleted = 0
    # Append in modalità gzip text
    with gzip.open(archive_path, 'at', encoding='utf-8') as gz:
        while True:
            batch = conn.execute(
                'SELECT * FROM audit_log WHERE ts < ? ORDER BY id LIMIT ?',
                (cutoff, args.batch_size),
            ).fetchall()
            if not batch:
                break
            ids = [r['id'] for r in batch]
            for r in batch:
                gz.write(json.dumps(dict(r), ensure_ascii=False, default=str))
                gz.write('\n')
            n_archived += len(batch)

            # Delete archiviati
            placeholders = ','.join('?' * len(ids))
            with conn:
                conn.execute(
                    f'DELETE FROM audit_log WHERE id IN ({placeholders})',
                    ids,
                )
            n_deleted += len(ids)
            print(f"  ... archiviati {n_archived}/{n_total}", end='\r', flush=True)

    print()
    print(f"✨ Archiviati {n_archived} record. Rimossi {n_deleted} dal DB.")

    # Stats finali
    rest = conn.execute('SELECT COUNT(*) FROM audit_log').fetchone()[0]
    print(f"📊 Audit_log residuo (post-cleanup): {rest}")


if __name__ == '__main__':
    main()
