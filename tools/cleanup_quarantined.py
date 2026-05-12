#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pulizia pending in stato 'quarantined' più vecchie di N giorni.

Sono pending che il drainer non è riuscito a pushare (es. colonna non
trovata sul foglio per case-sensitivity, ENELTEL sparito, ecc.) e che
con alta probabilità sono già state superate da pending successive
andate a buon fine.

Per safety, non cancelliamo: le marchiamo come 'failed' con motivazione
'stale_cleanup_<data>' e logghiamo in audit_log. Restano nel DB per
ispezione storica.

Uso:
    python tools/cleanup_quarantined.py                # dry-run (mostra cosa farebbe)
    python tools/cleanup_quarantined.py --apply        # esegue
    python tools/cleanup_quarantined.py --apply --max-age-days 7
"""
import os
import sys
import time
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--apply', action='store_true',
                        help='Esegue il cleanup (default: dry-run)')
    parser.add_argument('--max-age-days', type=int, default=7,
                        help='Quarantined più vecchie di N giorni vengono pulite (default 7)')
    args = parser.parse_args()

    from moduli import db as _db

    conn = _db.get_conn()
    cutoff = int(time.time()) - args.max_age_days * 86400

    rows = conn.execute(
        "SELECT id, eneltel_key, column_name, new_value, base_value, "
        "       last_error, created_ts FROM pending_writes "
        "WHERE state='quarantined' AND created_ts < ? ORDER BY created_ts",
        (cutoff,),
    ).fetchall()

    if not rows:
        print(f"✅ Nessuna pending quarantined più vecchia di {args.max_age_days} giorni.")
        return

    print(f"🧹 Trovate {len(rows)} pending quarantined da pulire:")
    for r in rows:
        from datetime import datetime
        creata = datetime.fromtimestamp(r['created_ts']).strftime('%Y-%m-%d %H:%M')
        print(f"  [{r['id']}] {creata}  ek={r['eneltel_key']} col={r['column_name']!r}  "
              f"new={r['new_value']!r}  error={r['last_error']}")

    if not args.apply:
        print()
        print("🔍 DRY-RUN (niente è stato modificato). Esegui con --apply per applicare.")
        return

    # Applica
    reason = f'stale_cleanup_{time.strftime("%Y-%m-%d")}'
    ids = [r['id'] for r in rows]
    placeholders = ','.join('?' * len(ids))
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        # Audit per ognuna prima del flip
        for r in rows:
            _db.audit(
                conn,
                eneltel_key=r['eneltel_key'],
                column_name=r['column_name'],
                new_value=r['new_value'],
                source='quarantine',
                detail={
                    'pending_id': r['id'],
                    'reason': reason,
                    'original_error': r['last_error'],
                },
            )
        conn.execute(
            f"UPDATE pending_writes SET state='failed', last_error=? "
            f"WHERE id IN ({placeholders})",
            [reason] + list(ids),
        )
    print(f"✨ {len(ids)} pending marchiate state='failed' con reason='{reason}'")


if __name__ == '__main__':
    main()
