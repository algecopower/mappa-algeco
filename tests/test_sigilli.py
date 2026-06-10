# -*- coding: utf-8 -*-
"""Test delle API per matricole e sigilli."""

import importlib

from flask import Flask


HEADERS_TORINO_RINOMINATI = [
    'LCL',
    'Tipo_Misuratore',
    'SIGILLO',
    'POD',
    'Eneltel',
    'NOMINATIVO',
    'Indirizzo',
    'Comune',
    'Cod. Installato',
    'Matr. Installato',
    'Data Installazione',
    'Operatore',
    'Esito',
    'SMART TEST',
]


def _reset_shared_cache():
    from moduli import shared

    shared._headers = None
    shared._header_map = None
    shared._worksheet_initialized = False
    shared._headers_ts = 0
    shared._cached_all_values = None
    shared._cached_all_values_ts = 0


def _app():
    import routes.sigilli as sigilli

    importlib.reload(sigilli)
    app = Flask(__name__)
    app.secret_key = 'test'
    app.register_blueprint(sigilli.bp)
    return app


def _login(client):
    with client.session_transaction() as sess:
        sess['user'] = {'username': 'tester', 'ruolo': 'admin'}


def _setup_contatore(db, *, matricola='25E4E5KE106521808'):
    conn = db.get_conn()
    with conn:
        db.update_column_registry(conn, HEADERS_TORINO_RINOMINATI)
        db.upsert_filone_row(
            conn,
            eneltel_key='123456',
            sheets_row=2,
            typed_values={
                'lcl': '6600107660',
                'pod': 'IT001E00000123',
                'nominativo': 'Mario Rossi',
                'indirizzo': 'Via Roma 1',
                'comune': 'TORINO',
                'data_installazione': '10/06/2026',
                'operatore': 'AE101392',
                'esito': 'ESITO OK',
            },
            extra_values={},
            row_hash='r1',
            source='bootstrap',
        )
        # Riproduce il registry già importato prima dell'aggiunta dell'alias.
        conn.execute(
            "UPDATE column_registry SET chiave_interna=NULL "
            "WHERE column_name IN ('Cod. Installato', 'Matr. Installato')"
        )
        conn.execute(
            'INSERT OR REPLACE INTO filone_extra'
            '(eneltel_key, lcl, column_name, value) VALUES (?, ?, ?, ?)',
            ('123456', '6600107660', 'Cod. Installato', matricola),
        )
    _reset_shared_cache()


def test_matricole_pending_riconosce_header_torino_rinominato(db_isolato):
    """Cod. Installato valorizzato non deve risultare come matricola mancante."""
    _setup_contatore(db_isolato)
    client = _app().test_client()
    _login(client)

    resp = client.get('/api/matricole/pending')

    assert resp.status_code == 200
    assert resp.get_json()['total'] == 0


def test_salva_matricola_scrive_su_cod_installato(db_isolato):
    """Il salvataggio usa il nuovo header e aggiorna il campo interno storico."""
    _setup_contatore(db_isolato, matricola='')
    client = _app().test_client()
    _login(client)

    resp = client.put(
        '/api/matricola/123456',
        json={'matricola': '26E4E5XA100012345', '_anchor_lcl': '6600107660'},
    )

    assert resp.status_code == 200
    assert resp.get_json()['success'] is True
    row = db_isolato.get_filone_row('123456', lcl='6600107660')
    assert row['matricola_installata'] == '26E4E5XA100012345'
    pending = db_isolato.get_conn().execute(
        "SELECT column_name, new_value FROM pending_writes WHERE state='pending'"
    ).fetchone()
    assert tuple(pending) == ('Cod. Installato', '26E4E5XA100012345')


def test_configurazione_non_espone_alias_storici():
    """I soli nomi canonici sono quelli dell'export corrente."""
    from config import MAPPATURA_COLONNE_DEFAULT

    assert MAPPATURA_COLONNE_DEFAULT['matricola_installata']['nomi_default'] == [
        'Cod. Installato'
    ]
    assert MAPPATURA_COLONNE_DEFAULT['codice_installato']['nomi_default'] == [
        'Matr. Installato'
    ]
