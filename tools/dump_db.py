#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dump diagnostico del DB locale.

Uso:
    python tools/dump_db.py                          # statistiche generali
    python tools/dump_db.py --eneltel 12345          # dettagli su una riga
    python tools/dump_db.py --pending                # tutte le pending non ancora pushate
    python tools/dump_db.py --conflicts              # ultimi 50 conflitti audit
    python tools/dump_db.py --anomalies              # anomalie rilevate
    python tools/dump_db.py --columns                # column_registry (mappatura nome→chiave)
"""
import os
import sys
import json
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _print_rows(label, rows):
    print(f"\n=== {label} ({len(rows)} righe) ===")
    for r in rows:
        print(json.dumps(dict(r), default=str, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--eneltel')
    parser.add_argument('--pending', action='store_true')
    parser.add_argument('--conflicts', action='store_true')
    parser.add_argument('--anomalies', action='store_true')
    parser.add_argument('--columns', action='store_true')
    args = parser.parse_args()

    from moduli import db
    conn = db.get_conn()

    if args.eneltel:
        ek = db.normalize_eneltel(args.eneltel)
        print(f"\n=== filone[{ek}] ===")
        row = db.get_filone_row(ek)
        print(json.dumps(row, default=str, ensure_ascii=False, indent=2))

        rows = conn.execute(
            'SELECT * FROM filone_extra WHERE eneltel_key=?', (ek,)
        ).fetchall()
        _print_rows('filone_extra', rows)

        rows = conn.execute(
            'SELECT * FROM pending_writes WHERE eneltel_key=? ORDER BY id', (ek,)
        ).fetchall()
        _print_rows('pending_writes', rows)

        rows = conn.execute(
            'SELECT * FROM audit_log WHERE eneltel_key=? ORDER BY ts DESC LIMIT 30', (ek,)
        ).fetchall()
        _print_rows('audit_log (ultimi 30)', rows)
        return

    if args.pending:
        rows = conn.execute(
            "SELECT * FROM pending_writes WHERE state IN ('pending','in_flight') "
            "ORDER BY id"
        ).fetchall()
        _print_rows('pending_writes (in volo)', rows)
        return

    if args.conflicts:
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE source='conflict_local_wins' "
            "ORDER BY ts DESC LIMIT 50"
        ).fetchall()
        _print_rows('audit_log[conflict_local_wins]', rows)
        return

    if args.anomalies:
        rows = conn.execute(
            'SELECT * FROM filone_anomalies ORDER BY ts DESC LIMIT 100'
        ).fetchall()
        _print_rows('filone_anomalies', rows)
        return

    if args.columns:
        rows = conn.execute(
            'SELECT * FROM column_registry ORDER BY last_index'
        ).fetchall()
        _print_rows('column_registry', rows)
        return

    print(json.dumps(db.stats(), default=str, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
