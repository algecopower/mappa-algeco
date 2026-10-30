# -*- coding: utf-8 -*-
"""
Import idempotente dei file Excel storici da output/lettere nel DB locale.

Uso:
  /opt/mappa-algeco/venv/bin/python tools/import_lettere_storiche.py
  PROVINCIA=PAVIA /opt/mappa-algeco/venv/bin/python tools/import_lettere_storiche.py
"""
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from config import PROVINCIA_ATTIVA  # noqa: E402
from moduli.lettere import registra_generazione, sincronizza_contatti_da_filone  # noqa: E402
from moduli.rilevamento_aziende import is_azienda  # noqa: E402


SHEETS_DATI = {
    'monofase',
    'emme2',
    'semidiretti',
    'fotovoltaici',
    'trifase semidiretti',
    'trifase diretti',
}

ALIASES = {
    'nominativo': ('nominativo_cliente', 'nominativo'),
    'via': ('via',),
    'civico': ('numero_civico', 'civico'),
    'comune': ('comune', 'localita', 'località'),
    'provincia': ('provincia',),
    'cap': ('cap',),
    'pod': ('pod',),
    'operatore': ('operatore',),
    'appuntamento': ('appuntamento',),
    'posizione_mis': ('posizione mis.', 'posizione mis'),
    'data_pianificazione': ('data pianificazione',),
    'ora_pianificazione': ('ora pianificazione', 'fascia oraria'),
    'lettera': ('lettera',),
}


def clean(value):
    if value is None:
        return ''
    text = str(value).strip()
    return '' if text.lower() in ('none', 'nan', 'null') else text


def norm_header(value):
    return re.sub(r'\s+', ' ', clean(value).replace('_', ' ')).strip().lower()


def parse_date(value):
    text = clean(value)
    for fmt in ('%d/%m/%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return ''


def find_header(ws, required=('nominativo',)):
    for row_num in range(1, min(ws.max_row, 12) + 1):
        values = [norm_header(c.value) for c in ws[row_num]]
        mapping = {}
        for key, aliases in ALIASES.items():
            for alias in aliases:
                alias_norm = norm_header(alias)
                if alias_norm in values:
                    mapping[key] = values.index(alias_norm)
                    break
        if all(k in mapping for k in required):
            return row_num, mapping
    return None, {}


def tipo_da_foglio(nome):
    nome = nome.casefold()
    if 'semidirett' in nome:
        return 'SEMIDIRETTO'
    if 'fotovolta' in nome or nome == 'emme2':
        return 'FOTOVOLTAICO'
    if 'trifase dirett' in nome:
        return 'TRIFASE DIRETTO'
    return 'MONOFASE'


def leggi_contatti_aziendali(wb):
    out = {}
    sheet = next(
        (s for s in wb.sheetnames if 'contatti aziendali' in s.casefold()),
        None,
    )
    if not sheet:
        return out
    ws = wb[sheet]
    header_row = None
    headers = {}
    for row_num in range(1, min(ws.max_row, 12) + 1):
        values = [norm_header(c.value) for c in ws[row_num]]
        if 'nominativo' in values:
            header_row = row_num
            headers = {v: i for i, v in enumerate(values) if v}
            break
    if not header_row:
        return out

    for values in ws.iter_rows(min_row=header_row + 1, values_only=True):
        nominativo = clean(values[headers.get('nominativo', 0)])
        if not nominativo or nominativo.lower().startswith('nessuna azienda'):
            continue
        pod_idx = headers.get('pod')
        pod = clean(values[pod_idx]).upper() if pod_idx is not None else ''
        tel_idx = headers.get('tel')
        mail_idx = headers.get('mail')
        pec_idx = headers.get('pec')
        telefono = clean(values[tel_idx]) if tel_idx is not None else ''
        mail = clean(values[mail_idx]) if mail_idx is not None else ''
        pec = clean(values[pec_idx]) if pec_idx is not None else ''
        email = ' / '.join(v for v in (mail, pec) if v)
        key = pod or nominativo.upper()
        out[key] = {'telefono': telefono, 'email': email}
    return out


def leggi_file(path):
    wb = load_workbook(path, read_only=True, data_only=True)
    contatti = leggi_contatti_aziendali(wb)
    righe = []
    date_iso = []

    for sheet_name in wb.sheetnames:
        if sheet_name.casefold() not in SHEETS_DATI:
            continue
        ws = wb[sheet_name]
        header_row, mapping = find_header(ws)
        if not header_row:
            continue
        tipo = tipo_da_foglio(sheet_name)

        for row_num, values in enumerate(
            ws.iter_rows(min_row=header_row + 1, values_only=True),
            start=header_row + 1,
        ):
            def value(key):
                idx = mapping.get(key)
                return clean(values[idx]) if idx is not None and idx < len(values) else ''

            nominativo = value('nominativo')
            if not nominativo or nominativo.lower().startswith('nessun contatore'):
                continue
            pod = value('pod').upper()
            data_iso = parse_date(value('data_pianificazione'))
            if data_iso:
                date_iso.append(data_iso)
            recapiti = contatti.get(pod) or contatti.get(nominativo.upper()) or {}
            righe.append({
                'source_sheet': sheet_name,
                'row_num': row_num,
                'nominativo': nominativo,
                'via': value('via'),
                'civico': value('civico'),
                'comune': value('comune'),
                'provincia': value('provincia'),
                'cap': value('cap'),
                'data_pianificazione': value('data_pianificazione'),
                'ora_pianificazione': value('ora_pianificazione'),
                'lettera': value('lettera'),
                'pod': pod,
                'operatore': value('operatore'),
                'posizione_mis': value('posizione_mis'),
                'appuntamento': value('appuntamento'),
                'tipo_misuratore': tipo,
                'telefono': recapiti.get('telefono', ''),
                'email': recapiti.get('email', ''),
                'is_azienda': (
                    (pod in contatti or nominativo.upper() in contatti)
                    or is_azienda(nominativo, tipo, note=value('posizione_mis'))
                ),
            })

    periodo_da = min(date_iso) if date_iso else ''
    periodo_a = max(date_iso) if date_iso else ''
    return righe, periodo_da, periodo_a


def provincia_file(path):
    match = re.search(r'^LETTERE\s+([A-Z]+)\s+DAL', path.name.upper())
    return match.group(1) if match else ''


def main():
    output_dir = ROOT / 'output' / 'lettere'
    files = sorted(output_dir.glob('*.xlsx'))
    target = PROVINCIA_ATTIVA.upper()
    imported = 0
    skipped = 0
    errors = 0

    print(f"Provincia attiva: {target}")
    for path in files:
        if provincia_file(path) != target:
            continue
        try:
            righe, periodo_da, periodo_a = leggi_file(path)
            if not righe:
                print(f"SKIP {path.name}: nessuna riga riconosciuta")
                skipped += 1
                continue
            info = registra_generazione(
                str(path),
                periodo_da,
                periodo_a,
                righe,
                provincia=target,
                user='import_storico',
                note='Import da output/lettere',
            )
            if info.get('already_present'):
                print(f"OK   {path.name}: già presente")
                skipped += 1
            else:
                print(
                    f"OK   {path.name}: {info['totale_righe']} righe, "
                    f"{info['totale_aziende']} aziende"
                )
                imported += 1
        except Exception as e:
            print(f"ERR  {path.name}: {e}")
            errors += 1

    sync = sincronizza_contatti_da_filone(provincia=target, user='import_storico')
    print(
        f"Recapiti dal filone: {sync['email_compilate']} email, "
        f"{sync['telefoni_compilati']} telefoni"
    )
    print(f"Completato: {imported} importati, {skipped} già presenti/ignorati, {errors} errori")
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
