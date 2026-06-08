# -*- coding: utf-8 -*-
"""Test per l'endpoint bulk della vista filone."""

from flask import Flask


HEADERS = [
    'POD',
    'Eneltel',
    'LCL',
    'NOMINATIVO',
    'Comune',
    'DATA PIAN',
    'Operatore',
    'NOTE APP',
]


def _app():
    from routes.contatori import bp

    app = Flask(__name__)
    app.secret_key = 'test'
    app.register_blueprint(bp)
    return app


def _login(client):
    with client.session_transaction() as sess:
        sess['user'] = {'username': 'tester', 'ruolo': 'admin'}


def _setup_filone(db):
    conn = db.get_conn()
    with conn:
        db.update_column_registry(conn, HEADERS)
        db.upsert_filone_row(
            conn,
            eneltel_key='42',
            sheets_row=2,
            typed_values={
                'pod': 'IT001E00000042',
                'lcl': 'A',
                'nominativo': 'Mario Rossi',
                'comune': 'TORINO',
            },
            extra_values={},
            row_hash='r1',
            source='bootstrap',
        )
        db.upsert_filone_row(
            conn,
            eneltel_key='42',
            sheets_row=3,
            typed_values={
                'pod': 'IT001E00000042',
                'lcl': 'B',
                'nominativo': 'Mario Rossi',
                'comune': 'PAVIA',
            },
            extra_values={},
            row_hash='r2',
            source='bootstrap',
        )


def test_filone_bulk_scrive_pianificazione_contatto(db_isolato):
    """Il bulk aggiorna data, operatore e note usando ancora POD/LCL."""
    db = db_isolato
    _setup_filone(db)
    client = _app().test_client()
    _login(client)

    resp = client.put('/api/filone/bulk', json={
        'rows': {
            '2': {
                'DATA PIAN': '02/06/2026',
                'Operatore': 'AE12345',
                'NOTE APP': 'APP 09:00',
                '_anchor_pod': 'IT001E00000042',
                '_anchor_lcl': 'A',
            },
        },
    })

    assert resp.status_code == 200
    body = resp.get_json()
    assert body['success'] is True
    assert body['cells'] == 3

    headers, rows = db.dump_filone_as_rows()
    assert headers == HEADERS
    row_a = next(r for r in rows if r['LCL'] == 'A')
    assert row_a['DATA PIAN'] == '02/06/2026'
    assert row_a['Operatore'] == 'AE12345'
    assert row_a['NOTE APP'] == 'APP 09:00'


def test_filone_bulk_anchor_lcl_disambigua_duplicate_eneltel(db_isolato):
    """Con stesso Eneltel/POD, l'anchor LCL aggiorna solo la riga giusta."""
    db = db_isolato
    _setup_filone(db)
    client = _app().test_client()
    _login(client)

    resp = client.put('/api/filone/bulk', json={
        'rows': {
            '3': {
                'Operatore': 'AE99999',
                '_anchor_pod': 'IT001E00000042',
                '_anchor_lcl': 'B',
            },
        },
    })

    assert resp.status_code == 200
    rows = db.dump_filone_as_rows()[1]
    row_a = next(r for r in rows if r['LCL'] == 'A')
    row_b = next(r for r in rows if r['LCL'] == 'B')
    assert row_a['Operatore'] == ''
    assert row_b['Operatore'] == 'AE99999'


def test_filone_bulk_rifiuta_colonne_non_registrate(db_isolato):
    """Una colonna assente dal registry viene segnalata e non scritta."""
    db = db_isolato
    _setup_filone(db)
    client = _app().test_client()
    _login(client)

    resp = client.put('/api/filone/bulk', json={
        'rows': {
            '2': {
                'COLONNA FANTASMA': 'x',
                '_anchor_pod': 'IT001E00000042',
                '_anchor_lcl': 'A',
            },
        },
    })

    assert resp.status_code == 400
    body = resp.get_json()
    assert body['invalid_cols'] == ['COLONNA FANTASMA']
    extra = db.get_conn().execute(
        "SELECT 1 FROM filone_extra WHERE column_name='COLONNA FANTASMA'"
    ).fetchall()
    assert extra == []


def test_filone_bulk_segnala_riga_non_trovata(db_isolato):
    """Una riga/ancora non risolta ritorna conflitto invece di falso successo."""
    db = db_isolato
    _setup_filone(db)
    client = _app().test_client()
    _login(client)

    resp = client.put('/api/filone/bulk', json={
        'rows': {
            '999': {'Operatore': 'AE12345'},
        },
    })

    assert resp.status_code == 409
    assert 'Righe o ancore' in resp.get_json()['error']


def test_filone_notes_salva_commenti_persistenti(db_isolato):
    """Il popup commenti salva nel DB e rilegge via API."""
    db = db_isolato
    _setup_filone(db)
    client = _app().test_client()
    _login(client)

    resp = client.put('/api/filone/notes', json={
        'notes': {'2:NOMINATIVO': 'Da richiamare domani'},
    })

    assert resp.status_code == 200
    body = resp.get_json()
    assert body['success'] is True
    assert body['written'] == 1

    resp = client.get('/api/filone/notes')
    assert resp.status_code == 200
    assert resp.get_json()['notes'] == {'2:NOMINATIVO': 'Da richiamare domani'}

    resp = client.put('/api/filone/notes', json={
        'cleared': ['2:NOMINATIVO'],
    })

    assert resp.status_code == 200
    assert resp.get_json()['deleted'] == 1
    assert client.get('/api/filone/notes').get_json()['notes'] == {}
