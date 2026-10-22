# -*- coding: utf-8 -*-
"""Test del parser delle distinte PDF."""

from moduli.distinte_manager import estrai_lavori_da_testo


def test_formazione_con_piu_parole_e_ampersand():
    """La formazione può contenere più parole e simboli prima della data."""
    testo = """NOUR EDDINE MOHAMED AMINE AG63397 1 399
Nour & Eddine 09/06/2026 00:00 0.0
09/06/2026 00:00
RCMI CC CC2G Completamento Passivi e Scambio disalimentabili 530 18 125943847
CONDOMINIO TORRE LESNA DI VITTORIO SN
335207577 10095 GRUGLIASCO D000BJ7
020562706 Non conosciuta 000000000000 0000
IT001E02056270
"""

    lavori = estrai_lavori_da_testo(testo)

    assert len(lavori) == 1
    assert lavori[0]['lavoro'] == 1
    assert lavori[0]['matricola_operatore'] == 'AG63397'
    assert lavori[0]['data_intervento'] == '09/06/2026'
    assert lavori[0]['pod'] == 'IT001E02056270'


def test_distinte_templates_api():
    """Verifica che l'endpoint templates restituisca i template con campi e body validi."""
    import importlib
    from flask import Flask
    import routes.distinte as distinte
    importlib.reload(distinte)
    app = Flask(__name__)
    app.secret_key = 'test-secret'
    app.register_blueprint(distinte.bp)

    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user'] = {'username': 'tester', 'ruolo': 'admin'}

    resp = client.get('/api/distinte/templates')
    assert resp.status_code == 200
    data = resp.get_json()
    assert 'templates' in data
    assert 'fields' in data
    assert 'settings' in data

    # Verifica la presenza di almeno un template email con body e subject
    email_templates = [t for t in data['templates'].values() if t.get('type') == 'email']
    assert len(email_templates) > 0
    for tmpl in email_templates:
        assert 'body' in tmpl and tmpl['body']
        assert 'subject' in tmpl and tmpl['subject']


def test_dati_filone_priorita_lcl_attivo_e_note(db_isolato, monkeypatch):
    """
    Verifica che get_dati_filone_dict e find_filone_row_by_pod diano priorità
    all'LCL attivo rispetto a quello disabilitato/storico e preservino
    l'appuntamento e la data corretta (es. 23/10 invece di 16/10).
    """
    db = db_isolato
    import config
    from moduli.distinte_manager import confronta_con_filone

    monkeypatch.setattr(config, 'LCL_DISABILITATI', ['6600110809'], raising=False)

    conn = db.get_conn()
    with conn:
        # Riga attiva (LCL 6600111342) con appuntamento e data 23/10
        db.upsert_filone_row(
            conn,
            eneltel_key='1001',
            sheets_row=10,
            typed_values={
                'lcl': '6600111342',
                'pod': 'IT001E99999999',
                'data_pianificata': '23/10/2026',
                'note_appuntamento': "APP 23/10/2026\tdalle 14:00 alle 15:30 CI SARA' IL PAPA'",
                'telefono': '3331112233',
            },
            extra_values={},
            row_hash='h1',
            source='test',
        )
        # Riga disabilitata storica (LCL 6600110809) con data vecchia 16/10 e note vuote
        # Ha sheets_row più alto (20), che in una scansione ingenua sovrascriverebbe la riga attiva
        db.upsert_filone_row(
            conn,
            eneltel_key='1002',
            sheets_row=20,
            typed_values={
                'lcl': '6600110809',
                'pod': 'IT001E99999999',
                'data_pianificata': '16/10/2026',
                'note_appuntamento': '',
                'telefono': '',
            },
            extra_values={},
            row_hash='h2',
            source='test',
        )

    # 1. get_dati_filone_dict deve restituire la riga attiva con appuntamento e 23/10
    dati = db.get_dati_filone_dict(['IT001E99999999'])
    assert 'IT001E99999999' in dati
    pod_data = dati['IT001E99999999']
    assert pod_data['lcl'] == '6600111342'
    assert pod_data['data_pian'] == '23/10/2026'
    assert "APP 23/10/2026" in pod_data['note']
    assert pod_data['telefono'] == '3331112233'

    # 2. confronta_con_filone non deve segnalare 'data_diversa' se la distinta ha 23/10/2026
    lavori = [{
        'lavoro': 1,
        'pod': 'IT001E99999999',
        'data_intervento': '23/10/2026',
        'telefono': '3331112233',
    }]
    diffs = confronta_con_filone(lavori, dati)
    assert len(diffs) == 0

    # 3. find_filone_row_by_pod deve trovare la chiave dell'LCL attivo
    found = db.find_filone_row_by_pod('IT001E99999999')
    assert found == ('1001', '6600111342')


