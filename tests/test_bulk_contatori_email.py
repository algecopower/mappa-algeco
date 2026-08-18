# -*- coding: utf-8 -*-
"""
tests/test_bulk_contatori_email.py — Test per modulo bulk_contatori_email (Excel e pluralizzazione).
"""

import io
from openpyxl import load_workbook

from moduli.bulk_contatori_email import (
    generate_contatori_excel,
    pluralize_template_text,
    build_plural_email,
    sanitize_filename,
)


def test_sanitize_filename():
    assert sanitize_filename("Azienda S.p.A. - Test!") == "Azienda_SpA_Test"
    assert sanitize_filename("") == "Azienda"
    assert sanitize_filename("Società & Co.") == "Societa_Co"


def test_generate_contatori_excel():
    contatori = [
        {
            'pod': 'IT001E11111111',
            'tipologia_contatore': 'Trifase',
            'indirizzo': 'Via Roma',
            'civico': '10',
            'comune': 'Torino',
            'matricola': '12345678',
            'data_intervento': '15/06/2026',
        },
        {
            'pod': 'IT001E22222222',
            'tipologia_contatore': 'Monofase 2G',
            'indirizzo': 'Via Roma',
            'civico': '12',
            'comune': 'Torino',
            'matricola': '87654321',
            'data_intervento': '15/06/2026',
        },
        {
            'pod': 'IT001E33333333',
            'tipologia_contatore': 'Semidiretto',
            'indirizzo': 'Corso Francia',
            'civico': '50',
            'comune': 'Torino',
            'matricola': '99999999',
            'data_intervento': '16/06/2026',
        },
    ]

    excel_bytes = generate_contatori_excel(contatori, nome_azienda="ACME S.R.L.")
    assert isinstance(excel_bytes, bytes)
    assert len(excel_bytes) > 0

    # Load back with openpyxl to verify content
    wb = load_workbook(io.BytesIO(excel_bytes))
    ws = wb.active
    assert ws.title == "Elenco Contatori"
    assert "ACME S.R.L." in ws.cell(row=1, column=1).value

    # Header is at row 4
    headers = [ws.cell(row=4, column=c).value for c in range(1, 9)]
    assert headers == ["N°", "POD", "Tipologia Misuratore", "Indirizzo", "Civico", "Comune", "Matricola / Codice", "Data Prevista"]

    # Check 3 data rows
    assert ws.cell(row=5, column=2).value == 'IT001E11111111'
    assert ws.cell(row=5, column=3).value == 'Trifase'
    assert ws.cell(row=5, column=4).value == 'Via Roma'

    assert ws.cell(row=6, column=2).value == 'IT001E22222222'
    assert ws.cell(row=7, column=2).value == 'IT001E33333333'


def test_pluralize_template_text():
    sing = "La informiamo che effettueremo la sostituzione del vostro contatore associato al POD in oggetto."
    plur = pluralize_template_text(sing)
    assert "dei vostri contatori" in plur
    assert "associati ai POD indicati" in plur


def test_build_plural_email_single():
    contatori = [
        {
            'pod': 'IT001E11111111',
            'nome_societa': 'ACME S.R.L.',
            'indirizzo': 'Via Roma',
            'civico': '10',
            'comune': 'Torino',
        }
    ]
    subj_in = "ACME S.R.L.: SOSTITUZIONE CONTATORE E-DISTRIBUZIONE POD {pod}"
    body_in = "Buongiorno, vi contattiamo per la sostituzione del vostro contatore..."

    subj_out, body_out, excel_file = build_plural_email(body_in, subj_in, contatori, {})
    assert subj_out == subj_in
    assert body_out == body_in
    assert excel_file is None


def test_build_plural_email_two_meters():
    contatori = [
        {
            'pod': 'IT001E11111111',
            'nome_societa': 'ACME S.R.L.',
            'indirizzo': 'Via Roma',
            'civico': '10',
            'comune': 'Torino',
            'tipologia_contatore': 'Trifase',
        },
        {
            'pod': 'IT001E22222222',
            'nome_societa': 'ACME S.R.L.',
            'indirizzo': 'Via Roma',
            'civico': '12',
            'comune': 'Torino',
            'tipologia_contatore': 'Monofase',
        },
    ]
    subj_in = "ACME S.R.L.: SOSTITUZIONE CONTATORE E-DISTRIBUZIONE"
    body_in = "Buongiorno, vi contattiamo per la sostituzione del vostro contatore.\n\nPOD (presente in fattura): {pod}\n\nPrecisiamo che l'attività è gratuita."

    subj_out, body_out, excel_file = build_plural_email(body_in, subj_in, contatori, {})
    assert "IT001E11111111, IT001E22222222" in subj_out
    assert "dei vostri contatori" in body_out
    assert "1) POD: IT001E11111111" in body_out
    assert "2) POD: IT001E22222222" in body_out
    assert excel_file is None


def test_build_plural_email_more_than_two_meters():
    contatori = [
        {'pod': f'IT001E0000000{i}', 'nome_societa': 'ACME S.R.L.', 'indirizzo': f'Via {i}', 'civico': str(i), 'comune': 'Torino', 'tipologia_contatore': 'Trifase'}
        for i in range(1, 6)
    ]
    subj_in = "ACME S.R.L.: SOSTITUZIONE CONTATORE E-DISTRIBUZIONE"
    body_in = "Buongiorno, vi contattiamo per la sostituzione del vostro contatore.\n\nPOD (presente in fattura): {pod}\n\nPrecisiamo che l'attività è gratuita."

    subj_out, body_out, excel_file = build_plural_email(body_in, subj_in, contatori, {})
    assert "(5 contatori)" in subj_out
    assert "dei vostri contatori" in body_out
    assert excel_file == "Elenco_Contatori_ACME_SRL.xlsx"
    assert "Elenco_Contatori_ACME_SRL.xlsx" in body_out
