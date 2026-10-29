#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI per l'import automatico del filone da Google Sheets.

Esegue il download e l'import sicuro con merge per una o tutte le province
configurate. Pensato per essere eseguito via cron ogni ora o invocato manualmente.

Uso:
    python tools/auto_import_filone.py --all
    python tools/auto_import_filone.py --provincia TORINO
    python tools/auto_import_filone.py --provincia PAVIA --force
    python tools/auto_import_filone.py --all --dry-run
"""
import argparse
import json
import os
import subprocess
import sys
import time

_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT_DIR)


def run_single_province(slug: str, force: bool = False, dry_run: bool = False) -> bool:
    """Esegue l'import per una singola provincia nel processo corrente."""
    os.environ['PROVINCIA'] = slug.upper()

    # Ricarica config e db se già importati
    import config
    import importlib
    importlib.reload(config)
    from moduli import db, excel_importer, auto_import
    importlib.reload(db)
    importlib.reload(excel_importer)
    importlib.reload(auto_import)

    print(f"\n[{slug.upper()}] Avvio verifica ed eventuale auto-import (force={force}, dry_run={dry_run})...")
    res = auto_import.esegui_auto_import(
        slug.upper(),
        force=force,
        dry_run=dry_run,
        user='cron_auto_import',
    )

    if res.get('skipped'):
        print(f"[{slug.upper()}] ⏭️ Invariato: {res.get('message')}")
        return True
    elif res.get('success'):
        print(f"[{slug.upper()}] ✅ Successo: {res.get('rows_aggiornate', 0)} aggiornate, {res.get('rows_nuove', 0)} nuove, {res.get('celle_modificate', 0)} celle modificate")
        return True
    else:
        err = res.get('error') or res.get('message') or 'Errore sconosciuto'
        print(f"[{slug.upper()}] ❌ Errore: {err}", file=sys.stderr)
        return False


def run_all_provinces(force: bool = False, dry_run: bool = False) -> int:
    """Esegue l'import per ciascuna provincia abilitata in un sottoprocesso separato."""
    provinces_path = os.path.join(_ROOT_DIR, 'data', 'provinces.json')
    if not os.path.exists(provinces_path):
        print(f"File provinces.json non trovato in {provinces_path}", file=sys.stderr)
        return 1

    with open(provinces_path, 'r', encoding='utf-8') as f:
        provinces = json.load(f)

    now_str = time.strftime('%Y-%m-%d %H:%M:%S')
    print(f"=== Auto-Import Google Sheets — {now_str} ===")

    tot_ok = 0
    tot_fail = 0
    tot_skipped_disabled = 0

    this_script = os.path.abspath(__file__)

    for slug, p_cfg in provinces.items():
        url = p_cfg.get('google_sheet_url', '').strip()
        enabled = p_cfg.get('auto_import_enabled', True)

        if not url or not enabled:
            tot_skipped_disabled += 1
            print(f"[{slug}] Saltata (URL non configurato o auto-import disabilitato)")
            continue

        cmd = [sys.executable, this_script, '--provincia', slug]
        if force:
            cmd.append('--force')
        if dry_run:
            cmd.append('--dry-run')

        p = subprocess.run(cmd)
        if p.returncode == 0:
            tot_ok += 1
        else:
            tot_fail += 1

    print(f"\nRiepilogo: {tot_ok} completate con successo, {tot_fail} errori, {tot_skipped_disabled} disabilitate.")
    return 1 if tot_fail > 0 else 0


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        '--all',
        action='store_true',
        help='Elabora tutte le province con auto-import abilitato',
    )
    group.add_argument(
        '--provincia',
        type=str,
        help='Elabora solo la provincia specificata (es. TORINO, PAVIA)',
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Forza l\'import anche se il file è invariato (ignora controllo hash SHA-256)',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Simula l\'import senza scrivere nel database',
    )
    args = parser.parse_args()

    if args.all:
        exit_code = run_all_provinces(force=args.force, dry_run=args.dry_run)
        sys.exit(exit_code)
    else:
        ok = run_single_province(args.provincia, force=args.force, dry_run=args.dry_run)
        sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
