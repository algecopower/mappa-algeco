#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Validatore shadow — confronta il DB locale con un fresh dump del foglio.

Pensato per girare via cron una volta a notte durante la Fase 1 della
migrazione. Se il DB e Sheets sono allineati per N notti consecutive,
si può procedere alla Fase 2 (swap-read).

Uso:
    python tools/validate_shadow.py                     # confronta + stampa
    python tools/validate_shadow.py --report-dir DIR    # salva report JSON in DIR
    python tools/validate_shadow.py --quiet             # exit code 0/1, no stdout

Exit code:
    0 → DB allineato con Sheets (zero discrepanze)
    1 → ci sono discrepanze (vedi report)
    2 → errore tecnico (Sheets non raggiungibile, DB corrotto, ecc.)

Setup cron (esempio):
    0 3 * * * cd /opt/mappa-algeco && /opt/mappa-algeco/venv/bin/python \\
        tools/validate_shadow.py --report-dir data/torino/shadow_reports
"""
import os
import sys
import json
import argparse
import datetime
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# Campi che il DB conserva tipizzati: confrontiamo SOLO questi (sono quelli
# che le route possono modificare). I campi extra sono in filone_extra ma
# non sono direttamente usati dalla UI quindi un mismatch lì non è un blocco
# alla migrazione.
def _build_field_map(internal_lookup, headers):
    """Mappa idx_colonna_foglio → chiave_interna (solo per chiavi note)."""
    from moduli import db as _db
    out = {}
    for i, name in enumerate(headers):
        if not name:
            continue
        chiave = internal_lookup.get(name.strip().lower())
        if chiave and chiave in _db.COLONNE_NOTE:
            out[i] = chiave
    return out


def _norm_for_compare(value, chiave):
    """Normalizza un valore per confronto SQLite ↔ stringa foglio."""
    if value is None:
        return ''
    s = str(value).strip()
    if chiave in ('latitudine', 'longitudine'):
        if not s:
            return ''
        try:
            # Coerce sia DB (REAL) che foglio (string '45.123' o '45,123')
            return f"{float(s.replace(',', '.')):.6f}"
        except ValueError:
            # Valore non numerico (es. 'ANNULLATO'): il DB lo memorizza come NULL,
            # quindi normalizziamo a '' per non generare falsi positivi.
            return ''
    return s


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--report-dir',
                        help='Directory dove salvare il report JSON (creata se manca)')
    parser.add_argument('--quiet', action='store_true',
                        help='Niente stdout, solo exit code')
    parser.add_argument('--max-diffs', type=int, default=200,
                        help='Max differenze da includere nel report (default 200)')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
    )
    log = logging.getLogger('validate_shadow')

    try:
        from moduli import db, shared
    except Exception as e:
        log.error(f"Import fallito: {e}")
        sys.exit(2)

    # Snapshot Sheets
    try:
        ws = shared.get_worksheet()
        values = shared._sheets_retry(ws.get_all_values)
    except Exception as e:
        log.error(f"Lettura Sheets fallita: {e}")
        sys.exit(2)

    if not values or len(values) < 2:
        log.error("Foglio vuoto")
        sys.exit(2)

    headers = values[0]
    body = values[1:]

    internal_lookup = db._build_internal_lookup()
    field_map = _build_field_map(internal_lookup, headers)

    # Trova colonna eneltel
    eneltel_col_idx = None
    for i, name in enumerate(headers):
        if name and internal_lookup.get(name.strip().lower()) == 'eneltel':
            eneltel_col_idx = i
            break
    if eneltel_col_idx is None:
        log.error("Colonna Eneltel non trovata")
        sys.exit(2)

    # Snapshot Sheets → {eneltel_key: {chiave_interna: valore_normalizzato}}
    sheets_snap = {}
    for row in body:
        if eneltel_col_idx >= len(row):
            continue
        ek = db.normalize_eneltel(row[eneltel_col_idx])
        if not ek or ek in sheets_snap:
            continue  # duplicati ignorati nella validazione (loggati altrove)
        record = {}
        for idx, chiave in field_map.items():
            if idx < len(row):
                record[chiave] = _norm_for_compare(row[idx], chiave)
        sheets_snap[ek] = record

    # Snapshot DB → stesso schema
    db_snap = {}
    for r in db.dump_filone():
        ek = r['eneltel_key']
        record = {}
        for chiave in db.COLONNE_NOTE:
            record[chiave] = _norm_for_compare(r.get(chiave), chiave)
        db_snap[ek] = record

    # Diff
    only_in_sheets = sorted(set(sheets_snap) - set(db_snap))
    only_in_db = sorted(set(db_snap) - set(sheets_snap))
    in_both = set(sheets_snap) & set(db_snap)

    field_diffs = []
    for ek in in_both:
        s = sheets_snap[ek]
        d = db_snap[ek]
        for chiave in db.COLONNE_NOTE:
            if chiave not in s:
                continue
            sv = s.get(chiave, '')
            dv = d.get(chiave, '')
            if sv != dv:
                field_diffs.append({
                    'eneltel_key': ek,
                    'campo': chiave,
                    'sheets': sv,
                    'db': dv,
                })
                if len(field_diffs) >= args.max_diffs:
                    break
        if len(field_diffs) >= args.max_diffs:
            break

    n_diffs = len(only_in_sheets) + len(only_in_db) + len(field_diffs)

    report = {
        'timestamp': datetime.datetime.now().isoformat(),
        'sheets_rows': len(sheets_snap),
        'db_rows': len(db_snap),
        'only_in_sheets': only_in_sheets[:50],
        'only_in_sheets_count': len(only_in_sheets),
        'only_in_db': only_in_db[:50],
        'only_in_db_count': len(only_in_db),
        'field_diffs': field_diffs,
        'field_diffs_count': len(field_diffs),
        'total_discrepancies': n_diffs,
        'verdict': 'OK' if n_diffs == 0 else 'DISCREPANCIES',
    }

    if args.report_dir:
        os.makedirs(args.report_dir, exist_ok=True)
        date_str = datetime.date.today().isoformat()
        path = os.path.join(args.report_dir, f'shadow_diff_{date_str}.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False, default=str)
        if not args.quiet:
            print(f"📝 Report salvato: {path}")

    if not args.quiet:
        if n_diffs == 0:
            print(f"✅ OK — DB e Sheets allineati ({len(in_both)} righe verificate)")
        else:
            print(f"⚠️  DISCREPANZE: {n_diffs} totali")
            print(f"   - Solo su Sheets (mancano nel DB): {len(only_in_sheets)}")
            print(f"   - Solo nel DB (mancano su Sheets): {len(only_in_db)}")
            print(f"   - Field diffs: {len(field_diffs)}")
            for fd in field_diffs[:10]:
                print(f"     {fd['eneltel_key']}/{fd['campo']}: "
                      f"sheets='{fd['sheets']}' vs db='{fd['db']}'")

    sys.exit(0 if n_diffs == 0 else 1)


if __name__ == '__main__':
    main()
