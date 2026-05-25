# -*- coding: utf-8 -*-
"""Test per normalizzazione import Excel."""


def test_import_compare_ignora_rumore_numerico():
    from moduli.excel_importer import _values_equivalent

    assert _values_equivalent('45.0', '45', 'latitudine')
    assert _values_equivalent('45.04024', '45,04024', 'latitudine')
    assert _values_equivalent('7.6621842000000004', '7.6621842', 'longitudine')


def test_import_compare_ignora_formati_data_equivalenti():
    from moduli.excel_importer import _values_equivalent

    assert _values_equivalent('14/01/2026', '2026-01-14', 'data_installazione')
    assert _values_equivalent('04/06/2026', '2026-06-04 00:00:00', 'data_pianificata')


def test_import_riconosce_errori_formula_su_colonne_numeriche():
    from moduli.excel_importer import _is_invalid_numeric_value

    assert _is_invalid_numeric_value('#REF!')
    assert _is_invalid_numeric_value('ANNULLATO')
    assert not _is_invalid_numeric_value('')
    assert not _is_invalid_numeric_value('45,04024')


def test_parse_csv_rileva_delimitatore_semicolon(tmp_path):
    from moduli.excel_importer import parse_csv

    path = tmp_path / 'filone.csv'
    path.write_text(
        'Eneltel;NOMINATIVO;Comune\n'
        '00123;"Mario Rossi";Torino\n'
        '\n',
        encoding='utf-8',
    )

    headers, rows = parse_csv(str(path))

    assert headers == ['Eneltel', 'NOMINATIVO', 'Comune']
    assert rows == [['00123', 'Mario Rossi', 'Torino']]


def test_parse_tabular_file_riconosce_csv(tmp_path):
    from moduli.excel_importer import parse_tabular_file

    path = tmp_path / 'filone.csv'
    path.write_text('Eneltel,Comune\n123,Torino\n', encoding='utf-8')

    headers, rows, file_format = parse_tabular_file(str(path))

    assert file_format == 'csv'
    assert headers == ['Eneltel', 'Comune']
    assert rows == [['123', 'Torino']]
