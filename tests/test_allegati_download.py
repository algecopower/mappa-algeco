# -*- coding: utf-8 -*-
"""Test per gli endpoint di download e anteprima allegati (PDF statici e Excel dinamici)."""

import io
import importlib
from flask import Flask
from openpyxl import load_workbook


def _create_app():
    import routes.distinte as distinte
    importlib.reload(distinte)
    app = Flask(__name__)
    app.secret_key = 'test-secret'
    app.register_blueprint(distinte.bp)
    return app


def _login(client):
    with client.session_transaction() as sess:
        sess['user'] = {'username': 'tester', 'ruolo': 'admin'}


def test_download_allegato_statico(monkeypatch):
    app = _create_app()
    client = app.test_client()
    _login(client)

    # Test per file statico esistente (es. attestazione_piemonte.pdf)
    resp = client.get('/api/allegati/file/attestazione_piemonte.pdf')
    assert resp.status_code == 200
    assert resp.mimetype == 'application/pdf'
    assert len(resp.data) > 0

    # Test per file inesistente
    resp_404 = client.get('/api/allegati/file/non_esistente.pdf')
    assert resp_404.status_code == 404


def test_anteprima_excel_contatori():
    app = _create_app()
    client = app.test_client()
    _login(client)

    payload = {
        'contatori': [
            {
                'pod': 'IT001E11223344',
                'tipologia_contatore': 'Trifase',
                'indirizzo': 'Via Po',
                'civico': '1',
                'comune': 'Torino',
                'matricola': '11223344',
                'data_intervento': '20/08/2026',
            },
            {
                'pod': 'IT001E55667788',
                'tipologia_contatore': 'Monofase',
                'indirizzo': 'Via Roma',
                'civico': '2',
                'comune': 'Torino',
                'matricola': '55667788',
                'data_intervento': '20/08/2026',
            }
        ],
        'nome_azienda': 'ENGIE SERVIZI SPA',
        'filename': 'Elenco_Contatori_ENGIE_SERVIZI_SPA.xlsx',
    }

    resp = client.post('/api/allegati/anteprima-excel-contatori', json=payload)
    assert resp.status_code == 200
    assert 'spreadsheetml' in resp.mimetype

    # Verifica che il file generato sia un Excel valido con i dati forniti
    wb = load_workbook(io.BytesIO(resp.data))
    ws = wb.active
    assert "ENGIE SERVIZI SPA" in ws.cell(row=1, column=1).value
    assert ws.cell(row=5, column=2).value == 'IT001E11223344'
    assert ws.cell(row=6, column=2).value == 'IT001E55667788'


def test_upload_allegato_manuale(tmp_path, monkeypatch):
    import routes.distinte as distinte
    app = _create_app()
    # Patch allegati dir to tmp_path
    monkeypatch.setattr(distinte, '_ALLEGATI_DIR', str(tmp_path), raising=False)
    client = app.test_client()
    _login(client)

    # Test upload valido (PDF)
    data = {
        'file': (io.BytesIO(b'%PDF-1.4 test upload content'), 'modulo_personalizzato.pdf')
    }
    resp = client.post('/api/allegati/upload', data=data, content_type='multipart/form-data')
    assert resp.status_code == 200
    res_json = resp.get_json()
    assert res_json['ok'] is True
    assert 'modulo_personalizzato.pdf' in res_json['filename']
    assert (tmp_path / res_json['filename']).exists()

    # Test upload estensione non valida (.exe)
    bad_data = {
        'file': (io.BytesIO(b'bad binary'), 'malware.exe')
    }
    resp_bad = client.post('/api/allegati/upload', data=bad_data, content_type='multipart/form-data')
    assert resp_bad.status_code == 400
    assert 'non consentita' in resp_bad.get_json()['error']

