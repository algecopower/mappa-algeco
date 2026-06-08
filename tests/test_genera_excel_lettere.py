# -*- coding: utf-8 -*-
import pandas as pd
from openpyxl import load_workbook

import genera_excel_lettere as gel


def test_richiede_lettera_esclude_provincia_e_civico_vuoto():
    base = {
        'IS_APPUNTAMENTO': False,
        'NOTE_K': '',
        'CLI_K': 'Mario Rossi',
        'CIVICO_K': '10',
    }

    assert gel.richiede_lettera(base) is True
    assert gel.richiede_lettera({**base, 'CLI_K': 'Provincia di Torino'}) is False
    assert gel.richiede_lettera({**base, 'CIVICO_K': ''}) is False
    assert gel.richiede_lettera({**base, 'CIVICO_K': None}) is False
    assert gel.richiede_lettera({**base, 'CIVICO_K': 'SNC'}) is False


def test_genera_excel_lettere_preserva_comune_none(tmp_path, monkeypatch):
    import config
    from moduli import percorso

    monkeypatch.setattr(config, 'MATRICOLE_VIP', ['TEC1'], raising=False)
    monkeypatch.setattr(percorso, 'ordina_percorso', lambda punti, *_args, **_kwargs: punti)
    monkeypatch.setattr(gel, 'cerca_cap', lambda comune: '10060' if comune == 'None' else '')
    monkeypatch.setattr(gel, 'cerca_provincia', lambda comune: 'TO' if comune == 'None' else '')

    df = pd.DataFrame([{
        'DATA_K': '2026-07-08',
        'TEC_K': 'TEC1',
        'ENEL_K': 'ENEL-NONE',
        'TIPO_CE_K': 'MONOFASE',
        'CLI_K': 'Mario Rossi',
        'NOTE_K': '',
        'CIVICO_K': '10',
        'VIA_K': 'VIA ROMA',
        'COMUNE': 'None',
        'LOCALITA': '',
        'POD_K': 'POD-NONE',
        'IS_APPUNTAMENTO': False,
        'ORA_APPUNTAMENTO': '',
    }])

    out = tmp_path / 'lettere_none.xlsx'
    gel.genera_excel_lettere(df, ['2026-07-08'], filename=str(out))

    wb = load_workbook(out, data_only=True)
    assert wb['MONOFASE'].cell(2, 4).value == 'None'
    assert wb['MONOFASE'].cell(2, 5).value == 'TO'
    assert wb['MONOFASE'].cell(2, 6).value == '10060'


def test_genera_excel_lettere_template_fogli_e_filtri(tmp_path, monkeypatch):
    import config
    from moduli import percorso

    monkeypatch.setattr(config, 'MATRICOLE_VIP', ['TEC1'], raising=False)
    monkeypatch.setattr(percorso, 'ordina_percorso', lambda punti, *_args, **_kwargs: punti)
    monkeypatch.setattr(gel, 'cerca_cap', lambda _comune: '10000')
    monkeypatch.setattr(gel, 'cerca_provincia', lambda _comune: 'TO')

    df = pd.DataFrame([
        {
            'DATA_K': '2026-06-01',
            'TEC_K': 'TEC1',
            'ENEL_K': 'ENEL1',
            'TIPO_CE_K': 'MONOFASE',
            'CLI_K': 'Mario Rossi',
            'NOTE_K': '',
            'CIVICO_K': '10',
            'VIA_K': 'VIA ROMA',
            'COMUNE': 'Torino',
            'LOCALITA': '',
            'POD_K': 'POD1',
            'IS_APPUNTAMENTO': False,
            'ORA_APPUNTAMENTO': '',
        },
        {
            'DATA_K': '2026-06-01',
            'TEC_K': 'TEC1',
            'ENEL_K': 'ENEL2',
            'TIPO_CE_K': 'TRIFASE DIRETTO',
            'CLI_K': 'Alfa SRL',
            'NOTE_K': '',
            'CIVICO_K': '20',
            'VIA_K': 'VIA MILANO',
            'COMUNE': 'Torino',
            'LOCALITA': '',
            'POD_K': 'POD2',
            'IS_APPUNTAMENTO': False,
            'ORA_APPUNTAMENTO': '',
        },
        {
            'DATA_K': '2026-06-01',
            'TEC_K': 'TEC1',
            'ENEL_K': 'ENEL3A',
            'TIPO_CE_K': 'MONOFASE',
            'CLI_K': 'Provincia di Torino',
            'NOTE_K': '',
            'CIVICO_K': '5',
            'VIA_K': 'VIA PROVINCIA',
            'COMUNE': 'Torino',
            'LOCALITA': '',
            'POD_K': 'POD3',
            'IS_APPUNTAMENTO': False,
            'ORA_APPUNTAMENTO': '',
        },
        {
            'DATA_K': '2026-06-01',
            'TEC_K': 'TEC1',
            'ENEL_K': 'ENEL4',
            'TIPO_CE_K': 'SEMIDIRETTO',
            'CLI_K': 'Comune di Chieri',
            'NOTE_K': '',
            'CIVICO_K': '2',
            'VIA_K': 'VIA COMUNE',
            'COMUNE': 'Chieri',
            'LOCALITA': '',
            'POD_K': 'POD4',
            'IS_APPUNTAMENTO': False,
            'ORA_APPUNTAMENTO': '',
        },
        {
            'DATA_K': '2026-06-01',
            'TEC_K': 'TEC1',
            'ENEL_K': 'ENEL5',
            'TIPO_CE_K': 'SEMIDIRETTO',
            'CLI_K': 'Beta SRL',
            'NOTE_K': '',
            'CIVICO_K': '',
            'VIA_K': 'VIA BETA 7',
            'COMUNE': 'Chieri',
            'LOCALITA': '',
            'POD_K': 'POD5',
            'IS_APPUNTAMENTO': False,
            'ORA_APPUNTAMENTO': '',
        },
    ])

    out = tmp_path / 'lettere.xlsx'
    risultato = gel.genera_excel_lettere(df, ['2026-06-01'], filename=str(out))

    assert risultato == str(out)
    wb = load_workbook(out, data_only=True)
    assert wb.sheetnames == [
        'MONOFASE',
        'EMME2',
        'SEMIDIRETTI',
        'Contatti Aziendali',
        'SOLO MONOFASE',
        'SOLO TRIFASE DIRETTI',
    ]
    assert 'RIEPILOGO' not in wb.sheetnames

    for sheet_name in ['MONOFASE', 'EMME2', 'SEMIDIRETTI']:
        ws = wb[sheet_name]
        assert [ws.cell(1, col).value for col in range(1, 14)] == gel.HEADERS_LETTERE

    monofase_rows = list(wb['MONOFASE'].iter_rows(min_row=2, values_only=True))
    solo_mono_rows = list(wb['SOLO MONOFASE'].iter_rows(min_row=2, values_only=True))
    solo_tri_rows = list(wb['SOLO TRIFASE DIRETTI'].iter_rows(min_row=2, values_only=True))

    assert {row[0] for row in monofase_rows} == {'Mario Rossi', 'Alfa SRL'}
    assert {row[0] for row in solo_mono_rows} == {'Mario Rossi'}
    assert {row[0] for row in solo_tri_rows} == {'Alfa SRL'}

    emme2_by_name = {row[0]: row for row in wb['EMME2'].iter_rows(min_row=2, values_only=True)}
    semidiretti_by_name = {
        row[0]: row for row in wb['SEMIDIRETTI'].iter_rows(min_row=2, values_only=True)
    }
    assert emme2_by_name['Provincia di Torino'][8] == 'NO'
    assert semidiretti_by_name['Comune di Chieri'][8] == 'NO'
    assert semidiretti_by_name['Beta SRL'][8] == 'NO'

    contatti = [
        row[0]
        for row in wb['Contatti Aziendali'].iter_rows(min_row=6, values_only=True)
        if row[0]
    ]
    assert contatti == ['Alfa SRL', 'Beta SRL']


def test_genera_excel_lettere_usa_config_personalizzata(tmp_path, monkeypatch):
    import config
    from moduli import percorso

    monkeypatch.setattr(config, 'MATRICOLE_VIP', ['TEC1'], raising=False)
    monkeypatch.setattr(percorso, 'ordina_percorso', lambda punti, *_args, **_kwargs: punti)
    monkeypatch.setattr(gel, 'cerca_cap', lambda _comune: '10000')
    monkeypatch.setattr(gel, 'cerca_provincia', lambda _comune: 'TO')
    monkeypatch.setattr(config, 'LETTERE_EXCEL_CONFIG', config.merge_lettere_excel_config({
        'fogli': {
            'monofase': 'PRIVATI',
            'solo_trifase_diretti': 'TRI DIRETTI',
        },
        'nomi_colonne': {
            'lettera': 'INVIO LETTERA',
        },
        'valori_lettera': {
            'si': 'SI',
            'no': 'NOPE',
        },
        'regole_no': {
            'civico_vuoto': False,
        },
        'parole_chiave': {
            'lettera_no': ['SCUOLA'],
            'contatti_aziendali_escludi': ['SCUOLA'],
        },
        'opzioni': {
            'unisci_trifase_diretti_in_monofase': False,
            'crea_fogli_dettaglio': False,
            'escludi_enti_da_contatti_aziendali': True,
        },
    }), raising=False)

    df = pd.DataFrame([
        {
            'DATA_K': '2026-06-01',
            'TEC_K': 'TEC1',
            'ENEL_K': 'ENEL10',
            'TIPO_CE_K': 'MONOFASE',
            'CLI_K': 'Provincia di Torino',
            'NOTE_K': '',
            'CIVICO_K': '',
            'VIA_K': 'VIA ROMA',
            'COMUNE': 'Torino',
            'LOCALITA': '',
            'POD_K': 'POD10',
            'IS_APPUNTAMENTO': False,
            'ORA_APPUNTAMENTO': '',
        },
        {
            'DATA_K': '2026-06-01',
            'TEC_K': 'TEC1',
            'ENEL_K': 'ENEL20',
            'TIPO_CE_K': 'TRIFASE DIRETTO',
            'CLI_K': 'Alfa SRL',
            'NOTE_K': '',
            'CIVICO_K': '20',
            'VIA_K': 'VIA MILANO',
            'COMUNE': 'Torino',
            'LOCALITA': '',
            'POD_K': 'POD20',
            'IS_APPUNTAMENTO': False,
            'ORA_APPUNTAMENTO': '',
        },
        {
            'DATA_K': '2026-06-01',
            'TEC_K': 'TEC1',
            'ENEL_K': 'ENEL30',
            'TIPO_CE_K': 'SEMIDIRETTO',
            'CLI_K': 'Scuola di Chieri SRL',
            'NOTE_K': '',
            'CIVICO_K': '2',
            'VIA_K': 'VIA COMUNE',
            'COMUNE': 'Chieri',
            'LOCALITA': '',
            'POD_K': 'POD30',
            'IS_APPUNTAMENTO': False,
            'ORA_APPUNTAMENTO': '',
        },
    ])

    out = tmp_path / 'lettere_custom.xlsx'
    gel.genera_excel_lettere(df, ['2026-06-01'], filename=str(out))

    wb = load_workbook(out, data_only=True)
    assert wb.sheetnames == ['PRIVATI', 'EMME2', 'SEMIDIRETTI', 'Contatti Aziendali', 'TRI DIRETTI']
    assert wb['PRIVATI'].cell(1, 9).value == 'INVIO LETTERA'
    assert wb['PRIVATI'].cell(2, 1).value == 'Provincia di Torino'
    assert wb['PRIVATI'].cell(2, 9).value == 'SI'
    assert wb['SEMIDIRETTI'].cell(2, 1).value == 'Scuola di Chieri SRL'
    assert wb['SEMIDIRETTI'].cell(2, 9).value == 'NOPE'
    assert wb['TRI DIRETTI'].cell(2, 1).value == 'Alfa SRL'

    contatti = [
        row[0]
        for row in wb['Contatti Aziendali'].iter_rows(min_row=6, values_only=True)
        if row[0]
    ]
    assert 'Scuola di Chieri SRL' not in contatti
