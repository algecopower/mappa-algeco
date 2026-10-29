# -*- coding: utf-8 -*-
"""Test per normalizzazione import Excel."""
import importlib

import pytest


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


def test_parse_csv_rifiuta_virgolette_malformate(tmp_path):
    from moduli.excel_importer import parse_csv

    path = tmp_path / 'filone.csv'
    path.write_text(
        'Eneltel,LCL,NOMINATIVO,Comune\n'
        '50146375,6600104200,"ILIANA VAYR\n'
        'quando la strada finisce,foo,bar\n',
        encoding='utf-8',
    )

    with pytest.raises(ValueError, match='CSV malformato'):
        parse_csv(str(path))


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
        '50146375,6600104200,Mario Rossi,Torino,04/06/2026,valore\n',
        encoding='utf-8',
    )

    result = excel_importer.import_excel(
        str(path), export_ts='2026-05-28 08:00:00', user='tester',
    )

    assert result['success'] is True
    assert result['rows_nuove'] == 1
    assert result['rows_aggiornate'] == 0

    row = db.get_filone_row('50146375', lcl='6600104200')
    assert row['nominativo'] == 'Mario Rossi'
    assert row['comune'] == 'Torino'
    assert row['data_pianificata'] == '04/06/2026'

    extra = db.get_conn().execute(
        "SELECT value FROM filone_extra WHERE eneltel_key=? AND lcl=? AND column_name=?",
        ('50146375', '6600104200', 'COLONNA_EXTRA'),
    ).fetchone()
    assert extra['value'] == 'valore'


def test_import_csv_accetta_testo_in_data_pianificata(db_isolato, tmp_path):
    """DATA PIAN nel filone storico può contenere anche note testuali."""
    db = db_isolato
    from moduli import excel_importer
    excel_importer = importlib.reload(excel_importer)

    path = tmp_path / 'filone.csv'
    path.write_text(
        'Eneltel,LCL,NOMINATIVO,Comune,DATA PIAN\n'
        '50146375,6600104200,Mario Rossi,Torino,cliente chiede appuntamento a luglio\n',
        encoding='utf-8',
    )

    result = excel_importer.import_excel(
        str(path), export_ts='2026-05-28 08:00:00', user='tester',
    )

    assert result['success'] is True
    assert result['rows_skip_invalid_key'] == 0
    row = db.get_filone_row('50146375', lcl='6600104200')
    assert row['data_pianificata'] == 'cliente chiede appuntamento a luglio'


def test_import_xlsx_importa_commenti_cella(db_isolato, tmp_path):
    """Gli XLSX conservano commenti cella: l'import li salva nel DB locale."""
    db = db_isolato
    openpyxl = pytest.importorskip('openpyxl')
    from openpyxl.comments import Comment

    from moduli import excel_importer
    excel_importer = importlib.reload(excel_importer)

    path = tmp_path / 'filone.xlsx'
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['Eneltel', 'LCL', 'NOMINATIVO', 'Comune'])
    ws.append(['50146375', '6600104200', 'Mario Rossi', 'Torino'])
    ws['C2'].comment = Comment('Richiamare dopo le 18', 'tester')
    wb.save(path)

    result = excel_importer.import_excel(
        str(path), export_ts='2026-05-28 08:00:00', user='tester',
    )

    assert result['success'] is True
    assert result['rows_nuove'] == 1
    assert result['commenti_importati'] == 1
    assert db.get_cell_comments() == {'2:NOMINATIVO': 'Richiamare dopo le 18'}


def test_import_xlsx_commenti_non_apre_workbook_pesante(db_isolato, tmp_path, monkeypatch):
    """I commenti XLSX non devono forzare load_workbook(read_only=False)."""
    openpyxl = pytest.importorskip('openpyxl')
    from openpyxl.comments import Comment

    path = tmp_path / 'filone.xlsx'
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['Eneltel', 'LCL', 'NOMINATIVO'])
    ws.append(['50146375', '6600104200', 'Mario Rossi'])
    ws['C2'].comment = Comment('Nota leggera', 'tester')
    wb.save(path)

    real_load_workbook = openpyxl.load_workbook
    read_only_values = []

    def spy_load_workbook(*args, **kwargs):
        read_only_values.append(kwargs.get('read_only'))
        assert kwargs.get('read_only') is True
        return real_load_workbook(*args, **kwargs)

    monkeypatch.setattr(openpyxl, 'load_workbook', spy_load_workbook)

    from moduli import excel_importer
    excel_importer = importlib.reload(excel_importer)

    result = excel_importer.import_excel(
        str(path), export_ts='2026-05-28 08:00:00', user='tester',
    )

    assert result['success'] is True
    assert len(read_only_values) >= 1
    assert all(v is True for v in read_only_values)


def test_import_csv_scarta_righe_con_chiavi_implausibili(db_isolato, tmp_path):
    db = db_isolato
    from moduli import excel_importer
    excel_importer = importlib.reload(excel_importer)

    path = tmp_path / 'filone.csv'
    path.write_text(
        'Eneltel,LCL,NOMINATIVO,Comune\n'
        '50146375,6600104200,Mario Rossi,Torino\n'
        'IT001E05003152,6600104200,50031527,3\n'
        "1 - Nell'appartamento,quando la strada finisce,ANNULLATO,346220\n",
        encoding='utf-8',
    )

    result = excel_importer.import_excel(
        str(path), export_ts='2026-05-28 08:00:00', user='tester',
    )

    assert result['success'] is True
    assert result['rows_nuove'] == 1
    assert result['rows_skip_invalid_key'] == 2
    assert db.get_filone_row('50146375', lcl='6600104200')['comune'] == 'Torino'
    assert db.get_filone_row('IT001E05003152', lcl='6600104200') is None
    assert db.get_filone_row("1 - Nell'appartamento", lcl='quando la strada finisce') is None


def test_import_csv_non_sovrascrive_con_riga_tronca(db_isolato, tmp_path):
    db = db_isolato
    conn = db.get_conn()
    with conn:
        db.update_column_registry(
            conn, ['Eneltel', 'LCL', 'NOMINATIVO', 'Indirizzo', 'Num', 'Comune'],
        )
        db.upsert_filone_row(
            conn,
            eneltel_key='50146375',
            lcl='6600104200',
            sheets_row=2,
            typed_values={
                'nominativo': 'ILIANA VAYR',
                'indirizzo': 'MAFFIOTTO',
                'civico': '36',
                'comune': 'CONDOVE',
            },
            extra_values={},
            row_hash='h',
            source='bootstrap',
        )

    from moduli import excel_importer
    excel_importer = importlib.reload(excel_importer)

    path = tmp_path / 'filone.csv'
    path.write_text(
        'Eneltel,LCL,NOMINATIVO,Indirizzo,Num,Comune\n'
        '50146375,6600104200,ILIANA VAYR con nota tagliata\n',
        encoding='utf-8',
    )

    result = excel_importer.import_excel(
        str(path), export_ts='2026-05-28 08:00:00', user='tester',
    )

    row = db.get_filone_row('50146375', lcl='6600104200')
    assert result['rows_skip_invalid_key'] == 1
    assert row['nominativo'] == 'ILIANA VAYR'
    assert row['indirizzo'] == 'MAFFIOTTO'
    assert row['comune'] == 'CONDOVE'


def test_import_csv_non_sovrascrive_con_colonne_slittate(db_isolato, tmp_path):
    db = db_isolato
    conn = db.get_conn()
    with conn:
        db.update_column_registry(
            conn,
            [
                'Eneltel', 'LCL', 'Pod', 'NOMINATIVO', 'Indirizzo', 'Num', 'Comune',
                'Posizione_Ante_Mis', 'Latitudine', 'Longitudine',
                'Codice_Ante_Sostituzione', 'Matricola_Ante_Sostituzione',
            ],
        )
        db.upsert_filone_row(
            conn,
            eneltel_key='44520478',
            lcl='6600101286',
            sheets_row=2,
            typed_values={
                'pod': 'IT001E01475674',
                'nominativo': 'TERNAVASIO UBALDO',
                'indirizzo': 'MINZONI',
                'civico': '16',
                'comune': 'PINEROLO',
                'posizione_misuratore': "1 - Nell'appartamento",
                'latitudine': '45.0',
                'longitudine': '7.0',
            },
            extra_values={},
            row_hash='h',
            source='bootstrap',
        )

    from moduli import excel_importer
    excel_importer = importlib.reload(excel_importer)

    path = tmp_path / 'filone.csv'
    path.write_text(
        'Eneltel,LCL,Pod,NOMINATIVO,Indirizzo,Num,Comune,'
        'Posizione_Ante_Mis,Latitudine,Longitudine,'
        'Codice_Ante_Sostituzione,Matricola_Ante_Sostituzione\n'
        '44520478,6600101286,IT001E01475674,TERNAVASIO UBALDO,'
        'mai andati a buon fine,MINZONI,16,,'
        "1 - Nell'appartamento,45,7,12E6E5121\n",
        encoding='utf-8',
    )

    result = excel_importer.import_excel(
        str(path), export_ts='2026-06-01 16:12:00', user='tester',
    )

    row = db.get_filone_row('44520478', lcl='6600101286')
    assert result['rows_skip_invalid_key'] == 1
    assert result['rows_aggiornate'] == 0
    assert row['indirizzo'] == 'MINZONI'
    assert row['civico'] == '16'
    assert row['comune'] == 'PINEROLO'
    assert row['latitudine'] == 45.0


def test_import_csv_non_sovrascrive_se_telefono_contiene_comune(db_isolato, tmp_path):
    db = db_isolato
    conn = db.get_conn()
    with conn:
        db.update_column_registry(
            conn,
            [
                'Eneltel', 'LCL', 'Pod', 'NOMINATIVO', 'Indirizzo', 'Num',
                'Comune', 'telefono', 'DATA PIAN', 'Operatore',
            ],
        )
        db.upsert_filone_row(
            conn,
            eneltel_key='49550731',
            lcl='6600101316',
            sheets_row=2,
            typed_values={
                'pod': 'IT001E04955073',
                'nominativo': 'GAGLIARDI MONICA',
                'indirizzo': 'S STEFANO',
                'civico': '27',
                'comune': 'GIAGLIONE',
                'telefono': '',
                'data_pianificata': '03/09/2025',
                'operatore': 'AG49808',
            },
            extra_values={},
            row_hash='h',
            source='bootstrap',
        )

    from moduli import excel_importer
    excel_importer = importlib.reload(excel_importer)

    path = tmp_path / 'filone.csv'
    path.write_text(
        'Eneltel,LCL,Pod,NOMINATIVO,Indirizzo,Num,Comune,telefono,DATA PIAN,Operatore\n'
        '49550731,6600101316,IT001E04955073,'
        'GAGLIARDI MONICA esterno civico 26,'
        "porta di casa e accanto c'è il contatore,sali le scale,,"
        'GIAGLIONE,03/09/2025,25E4E5KA1\n',
        encoding='utf-8',
    )

    result = excel_importer.import_excel(
        str(path), export_ts='2026-06-01 16:12:00', user='tester',
    )

    row = db.get_filone_row('49550731', lcl='6600101316')
    assert result['rows_skip_invalid_key'] == 1
    assert result['rows_aggiornate'] == 0
    assert row['nominativo'] == 'GAGLIARDI MONICA'
    assert row['indirizzo'] == 'S STEFANO'
    assert row['comune'] == 'GIAGLIONE'
    assert row['telefono'] == ''
    assert row['operatore'] == 'AG49808'


def test_import_csv_non_sovrascrive_se_operatore_e_slittato(db_isolato, tmp_path):
    db = db_isolato
    conn = db.get_conn()
    with conn:
        db.update_column_registry(
            conn,
            ['Eneltel', 'LCL', 'Pod', 'NOMINATIVO', 'Comune', 'DATA PIAN', 'Operatore'],
        )
        db.upsert_filone_row(
            conn,
            eneltel_key='49550731',
            lcl='6600101316',
            sheets_row=2,
            typed_values={
                'pod': 'IT001E04955073',
                'nominativo': 'GAGLIARDI MONICA',
                'comune': 'GIAGLIONE',
                'data_pianificata': '03/09/2025',
                'operatore': 'AG49808',
            },
            extra_values={},
            row_hash='h',
            source='bootstrap',
        )

    from moduli import excel_importer
    excel_importer = importlib.reload(excel_importer)

    path = tmp_path / 'filone.csv'
    path.write_text(
        'Eneltel,LCL,Pod,NOMINATIVO,Comune,DATA PIAN,Operatore\n'
        '49550731,6600101316,IT001E04955073,GAGLIARDI MONICA,'
        'GIAGLIONE,03/09/2025,25E4E5KA1\n',
        encoding='utf-8',
    )

    result = excel_importer.import_excel(
        str(path), export_ts='2026-06-01 16:12:00', user='tester',
    )

    row = db.get_filone_row('49550731', lcl='6600101316')
    assert result['rows_skip_invalid_key'] == 0
    assert result['rows_aggiornate'] == 0
    assert result['valori_invalidi_per_colonna'] == {'operatore': 1}
    assert row['operatore'] == 'AG49808'


def test_import_csv_non_sovrascrive_riga_modificata_dopo_export(db_isolato, tmp_path):
    """Most-recent-wins: se il DB e' piu' recente dell'export, vince il DB."""
    db = db_isolato
    conn = db.get_conn()
    with conn:
        db.update_column_registry(conn, ['Eneltel', 'NOMINATIVO'])
        db.upsert_filone_row(
            conn,
            eneltel_key='50146375',
            lcl='6600104200',
            sheets_row=2,
            typed_values={'nominativo': 'Valore DB'},
            extra_values={},
            row_hash='h',
            source='bootstrap',
        )
    db.record_local_change(
        '50146375', {'NOMINATIVO': 'Valore DB recente'},
        lcl='6600104200',
        origin='unit_test', initial_state='pending',
    )

    from moduli import excel_importer
    excel_importer = importlib.reload(excel_importer)

    path = tmp_path / 'filone.csv'
    path.write_text(
        'Eneltel,LCL,NOMINATIVO\n'
        '50146375,6600104200,Valore Excel vecchio\n',
        encoding='utf-8',
    )

    result = excel_importer.import_excel(
        str(path), export_ts='2000-01-01 00:00:00', user='tester',
    )

    assert result['success'] is True
    assert result['rows_db_vince'] == 1
    assert db.get_filone_row('50146375', lcl='6600104200')['nominativo'] == 'Valore DB recente'


def test_import_xlsx_multi_sheet_auto_detect(db_isolato, tmp_path):
    """Se il foglio attivo non ha Eneltel (es. Report), trova automaticamente il foglio dati."""
    openpyxl = pytest.importorskip('openpyxl')
    from moduli import excel_importer

    path = tmp_path / 'multi.xlsx'
    wb = openpyxl.Workbook()
    ws_report = wb.active
    ws_report.title = 'Report'
    ws_report.append(['Colonna A', 'Colonna B', 'Totale'])
    ws_report.append(['Dato 1', 'Dato 2', 100])

    ws_data = wb.create_sheet(title='PIEMONTE')
    ws_data.append(['Eneltel', 'LCL', 'NOMINATIVO', 'Comune'])
    ws_data.append(['11223344', '6600104200', 'Test Auto Sheet', 'Torino'])

    wb.save(path)

    result = excel_importer.import_excel(str(path), export_ts='2026-06-01 10:00:00')
    assert result['success'] is True
    assert result['sheet_name'] == 'PIEMONTE'
    assert result['rows_nuove'] == 1


def test_import_xlsx_con_banner_e_righe_vuote(db_isolato, tmp_path):
    """Gestisce banner iniziale e righe vuote prima della riga intestazione."""
    openpyxl = pytest.importorskip('openpyxl')
    from moduli import excel_importer

    path = tmp_path / 'banner.xlsx'
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append([])  # Riga vuota
    ws.append(['ESTRAZIONE DEL 09/10/2026 - CONFIDENZIALE'])  # Banner
    ws.append([])  # Altra riga vuota
    ws.append(['Eneltel', 'LCL', 'NOMINATIVO', 'Comune'])
    ws.append(['99887766', '6600104200', 'Test Banner', 'Moncalieri'])

    wb.save(path)

    result = excel_importer.import_excel(str(path), export_ts='2026-06-01 10:00:00')
    assert result['success'] is True
    assert result['rows_nuove'] == 1


def test_import_cell_to_str_date():
    from datetime import date
    from moduli.excel_importer import _cell_to_str, _parse_export_ts

    d = date(2026, 6, 18)
    assert _cell_to_str(d) == '18/06/2026'

    # Supporto ISO datetime senza secondi
    ts = _parse_export_ts('2026-05-21T14:30')
    assert ts > 0


def test_import_excel_non_tocca_outbox_se_auto_mark_delivered_false(db_isolato, tmp_path):
    """Verifica che con auto_mark_delivered=False le pending writes restino intatte nell'outbox."""
    db = db_isolato
    from moduli import excel_importer
    excel_importer = importlib.reload(excel_importer)

    # 1. Crea riga iniziale
    path1 = tmp_path / 'filone1.csv'
    path1.write_text(
        'Eneltel,LCL,NOMINATIVO,Comune\n'
        '10001,6600104200,Mario Rossi,Torino\n',
        encoding='utf-8',
    )
    excel_importer.import_excel(str(path1), export_ts='2026-06-01 10:00:00')

    # 2. Registra modifica locale (crea pending_write)
    db.record_local_change('10001', {'NOTE APP': 'Nota locale da preservare'}, user='operatore')
    pending = db.list_pending_writes(state='pending')
    assert len(pending) == 1

    # 3. Import successivo con auto_mark_delivered=False (come fa l'auto-import da Sheets)
    path2 = tmp_path / 'filone2.csv'
    path2.write_text(
        'Eneltel,LCL,NOMINATIVO,Comune\n'
        '10001,6600104200,Mario Rossi,Torino\n'
        '10002,6600104200,Luigi Verdi,Torino\n',
        encoding='utf-8',
    )
    res = excel_importer.import_excel(
        str(path2),
        export_ts='2026-06-02 10:00:00',
        auto_mark_delivered=False,
        protect_pending_rows=True,
    )
    assert res['success'] is True
    assert res['auto_marked_delivered'] == 0

    # 4. Verifica che la pending write sia ANCORA pending
    pending_dopo = db.list_pending_writes(state='pending')
    assert len(pending_dopo) == 1
    assert pending_dopo[0]['new_value'] == 'Nota locale da preservare'


def test_import_excel_protegge_righe_pending_se_protect_pending_rows_true(db_isolato, tmp_path):
    """Verifica che una riga con modifiche pending non venga sovrascritta dal foglio se protect_pending_rows=True."""
    db = db_isolato
    from moduli import excel_importer
    excel_importer = importlib.reload(excel_importer)

    # 1. Inserisci riga iniziale
    path1 = tmp_path / 'filone1.csv'
    path1.write_text(
        'Eneltel,LCL,NOMINATIVO,Comune,NOTE APP\n'
        '20001,6600104200,Mario Rossi,Torino,Nota iniziale\n',
        encoding='utf-8',
    )
    excel_importer.import_excel(str(path1), export_ts='2026-06-01 10:00:00')

    # 2. Modifica locale su NOTE APP
    db.record_local_change('20001', {'NOTE APP': 'Nota fatta da programma'}, user='operatore')

    # 3. Import con protect_pending_rows=True dove Excel prova a riportare "Nota vecchia dal foglio"
    path2 = tmp_path / 'filone2.csv'
    path2.write_text(
        'Eneltel,LCL,NOMINATIVO,Comune,NOTE APP\n'
        '20001,6600104200,Mario Rossi,Torino,Nota vecchia dal foglio\n',
        encoding='utf-8',
    )
    res = excel_importer.import_excel(
        str(path2),
        export_ts='2026-06-02 10:00:00',
        auto_mark_delivered=False,
        protect_pending_rows=True,
    )
    assert res['success'] is True
    assert res['rows_db_vince'] == 1

    # 4. Verifica che il valore nel DB sia ancora quello locale
    row = db.get_filone_row('20001', lcl='6600104200')
    assert row['note_appuntamento'] == 'Nota fatta da programma'

