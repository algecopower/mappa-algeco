# -*- coding: utf-8 -*-
"""Test per normalizzazione import Excel."""
import importlib


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


def test_import_csv_popola_db_locale(db_isolato, tmp_path):
    """Import CSV reale: crea registry, filone tipizzato e colonne extra."""
    db = db_isolato
    from moduli import excel_importer
    excel_importer = importlib.reload(excel_importer)

    path = tmp_path / 'filone.csv'
    path.write_text(
        'Eneltel,LCL,NOMINATIVO,Comune,DATA PIAN,COLONNA_EXTRA\n'
        '000123,A,Mario Rossi,Torino,04/06/2026,valore\n',
        encoding='utf-8',
    )

    result = excel_importer.import_excel(
        str(path), export_ts='2026-05-28 08:00:00', user='tester',
    )

    assert result['success'] is True
    assert result['rows_nuove'] == 1
    assert result['rows_aggiornate'] == 0

    row = db.get_filone_row('123', lcl='A')
    assert row['nominativo'] == 'Mario Rossi'
    assert row['comune'] == 'Torino'
    assert row['data_pianificata'] == '04/06/2026'

    extra = db.get_conn().execute(
        "SELECT value FROM filone_extra WHERE eneltel_key=? AND lcl=? AND column_name=?",
        ('123', 'A', 'COLONNA_EXTRA'),
    ).fetchone()
    assert extra['value'] == 'valore'


def test_import_csv_non_sovrascrive_riga_modificata_dopo_export(db_isolato, tmp_path):
    """Most-recent-wins: se il DB e' piu' recente dell'export, vince il DB."""
    db = db_isolato
    conn = db.get_conn()
    with conn:
        db.update_column_registry(conn, ['Eneltel', 'NOMINATIVO'])
        db.upsert_filone_row(
            conn,
            eneltel_key='123',
            sheets_row=2,
            typed_values={'nominativo': 'Valore DB'},
            extra_values={},
            row_hash='h',
            source='bootstrap',
        )
    db.record_local_change(
        '123', {'NOMINATIVO': 'Valore DB recente'},
        origin='unit_test', initial_state='pending',
    )

    from moduli import excel_importer
    excel_importer = importlib.reload(excel_importer)

    path = tmp_path / 'filone.csv'
    path.write_text(
        'Eneltel,NOMINATIVO\n'
        '000123,Valore Excel vecchio\n',
        encoding='utf-8',
    )

    result = excel_importer.import_excel(
        str(path), export_ts='2000-01-01 00:00:00', user='tester',
    )

    assert result['success'] is True
    assert result['rows_db_vince'] == 1
    assert db.get_filone_row('123')['nominativo'] == 'Valore DB recente'
