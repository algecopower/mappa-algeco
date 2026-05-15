#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pulizia pending in stato 'done' più vecchie di N giorni.

Le pending 'done' sono scritture già pushate su Sheets e non più necessarie
come "in volo". Restano nella tabella per breve audit/diagnostica, ma le
informazioni durature sono già in `audit_log`. Dopo N giorni vengono
cancellate fisicamente per evitare crescita lineare illimitata della
tabella `pending_writes`.

Differenza vs cleanup_quarantined.py:
  - Quarantined: scritture FALLITE, le marchiamo 'failed' (NON cancelliamo)
    per ispezione storica.
  - Done: scritture COMPLETATE, fisicamente cancellate (info conservata in
    audit_log).

Uso:
    python tools/cleanup_done_pendings.py                # dry-run
    python tools/cleanup_done_pendings.py --apply
    python tools/cleanup_done_pendings.py --apply --max-age-days 60
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
                        help='Esegue la cancellazione (default: dry-run)')
    parser.add_argument('--max-age-days', type=int, default=30,
                        help='Done più vecchie di N giorni vengono cancellate (default 30)')
    args = parser.parse_args()

    from moduli import db as _db

    conn = _db.get_conn()
    cutoff = int(time.time()) - args.max_age_days * 86400

    cur = conn.execute(
        "SELECT COUNT(*) AS c, MIN(created_ts) AS mn, MAX(created_ts) AS mx "
        "FROM pending_writes WHERE state='done' AND created_ts < ?",
        (cutoff,),
    ).fetchone()
    n = cur['c']

    if n == 0:
        print(f"✅ Nessuna pending done più vecchia di {args.max_age_days} giorni.")
        return

    import datetime
    mn = datetime.datetime.fromtimestamp(cur['mn']).strftime('%Y-%m-%d %H:%M')
    mx = datetime.datetime.fromtimestamp(cur['mx']).strftime('%Y-%m-%d %H:%M')
    print(f"🧹 {n} pending 'done' candidate alla cancellazione "
          f"(da {mn} a {mx}).")

    if not args.apply:
        print("🔍 DRY-RUN (niente è stato modificato). Esegui con --apply.")
        return

    with conn:
        res = conn.execute(
            "DELETE FROM pending_writes WHERE state='done' AND created_ts < ?",
            (cutoff,),
        )
    print(f"✨ {res.rowcount} pending done cancellate fisicamente.")
    print(f"   (info conservata in audit_log con source='local'/'remote')")


if __name__ == '__main__':
    main()
