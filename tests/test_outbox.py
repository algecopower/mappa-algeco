# -*- coding: utf-8 -*-
"""Test per l'export Excel dei cambiamenti."""
import importlib
import io
import sys

import pytest


def _load_outbox_module():
    """Ricarica outbox dopo il reload di moduli.db fatto dalla fixture."""
    if 'moduli.outbox' in sys.modules:
        return importlib.reload(sys.modules['moduli.outbox'])
    from moduli import outbox
    return outbox


def test_export_cambiamenti_excel_compatto(db_isolato):
    """L'export contiene un solo foglio: POD + sole colonne modificate."""
    openpyxl = pytest.importorskip('openpyxl')
    db = db_isolato
    conn = db.get_conn()

    with conn:
        db.update_column_registry(
            conn,
            ['Eneltel', 'POD', 'LCL', 'NOMINATIVO', 'EMAIL', 'NOTE APP'],
        )
        db.upsert_filone_row(
            conn,
            eneltel_key='42',
            sheets_row=7,
            typed_values={
                'pod': 'POD42',
                'lcl': 'L1',
                'nominativo': 'Mario',
                'email': 'old@example.com',
                'note_appuntamento': '',
            },
            extra_values={},
            row_hash='h42',
            source='bootstrap',
            lcl='L1',
        )
        db.upsert_filone_row(
            conn,
            eneltel_key='43',
            sheets_row=8,
            typed_values={
                'pod': 'POD43',
                'lcl': 'L2',
                'nominativo': 'Anna',
                'email': 'anna@example.com',
                'note_appuntamento': '',
            },
            extra_values={},
            row_hash='h43',
            source='bootstrap',
            lcl='L2',
        )

    db.record_local_change(
        '42',
        {'EMAIL': 'new@example.com', 'NOTE APP': 'richiamare'},
        origin='unit_test',
        initial_state='pending',
        lcl='L1',
    )
    db.record_local_change(
        '42',
        {'Eneltel': '42'},
        origin='unit_test',
        initial_state='pending',
        lcl='L1',
    )
    db.record_local_change(
        '43',
        {'NOMINATIVO': 'Anna Rossi'},
        origin='unit_test',
        initial_state='pending',
        lcl='L2',
    )

    outbox = _load_outbox_module()
    data, n_rows, n_cells = outbox.build_outbox_xlsx()

    assert n_rows == 2
    assert n_cells == 3
    wb = openpyxl.load_workbook(io.BytesIO(data))
    assert wb.sheetnames == ['Cambiamenti']

    ws = wb.active
    headers = [ws.cell(1, c).value for c in range(1, ws.max_column + 1)]
    assert headers == ['POD', 'NOMINATIVO', 'EMAIL', 'NOTE APP']
    assert 'Eneltel' not in headers
    assert 'LCL' not in headers

    values = [
        [ws.cell(r, c).value for c in range(1, ws.max_column + 1)]
        for r in range(2, ws.max_row + 1)
    ]
    assert values == [
        ['POD42', None, 'new@example.com', 'richiamare'],
        ['POD43', 'Anna Rossi', None, None],
    ]
