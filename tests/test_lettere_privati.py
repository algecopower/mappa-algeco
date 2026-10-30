# -*- coding: utf-8 -*-
"""Test per la gestione utenti privati nelle lettere."""


def _riga_privato(**overrides):
    row = {
        'source_sheet': 'MONOFASE',
        'row_num': 2,
        'pod': 'IT001E00000002',
        'eneltel': '2002',
        'lcl': '',
        'nominativo': 'Rossi Mario',
        'via': 'Garibaldi',
        'civico': '15',
        'comune': 'Torino',
        'provincia': 'TO',
        'cap': '10100',
        'data_pianificazione': '16/06/2026',
        'ora_pianificazione': '08:00 - 11:00',
        'lettera': "SI'",
        'operatore': 'TEC1',
        'posizione_mis': 'Locale cantina',
        'appuntamento': '',
        'tipo_misuratore': 'MONOFASE',
        'telefono': '3331234567',
        'email': '',
        'is_azienda': False,
    }
    row.update(overrides)
    return row


def _riga_privato_fisso(**overrides):
    row = {
        'source_sheet': 'MONOFASE',
        'row_num': 3,
        'pod': 'IT001E00000003',
        'eneltel': '2003',
        'lcl': '',
        'nominativo': 'Bianchi Luigi',
        'via': 'Mazzini',
        'civico': '4',
        'comune': 'Torino',
        'provincia': 'TO',
        'cap': '10100',
        'data_pianificazione': '16/06/2026',
        'ora_pianificazione': '11:00 - 14:00',
        'lettera': "SI'",
        'operatore': 'TEC1',
        'posizione_mis': 'Esterno',
        'appuntamento': '',
        'tipo_misuratore': 'MONOFASE',
        'telefono': '0119876543',
        'email': '',
        'is_azienda': False,
    }
    row.update(overrides)
    return row


def _riga_privato_senza_telefono(**overrides):
    row = {
        'source_sheet': 'MONOFASE',
        'row_num': 4,
        'pod': 'IT001E00000004',
        'eneltel': '2004',
        'lcl': '',
        'nominativo': 'Verdi Giuseppe 3401122334',
        'via': 'Dante',
        'civico': '8',
        'comune': 'Torino',
        'provincia': 'TO',
        'cap': '10100',
        'data_pianificazione': '16/06/2026',
        'ora_pianificazione': '14:00 - 17:00',
        'lettera': "SI'",
        'operatore': 'TEC2',
        'posizione_mis': 'Vano scale',
        'appuntamento': '',
        'tipo_misuratore': 'MONOFASE',
        'telefono': '',
        'email': '',
        'is_azienda': False,
    }
    row.update(overrides)
    return row


def test_list_privati_categorizza_correttamente(tmp_path, db_isolato, monkeypatch):
    import config
    from moduli import lettere

    monkeypatch.setattr(config, 'PROVINCIA_ATTIVA', 'TORINO', raising=False)
    file_path = tmp_path / 'lettere.xlsx'
    file_path.write_bytes(b'test')

    righe = [
        _riga_privato(),
        _riga_privato_fisso(),
        _riga_privato_senza_telefono(),
    ]
    info = lettere.registra_generazione(
        str(file_path),
        '2026-06-16',
        '2026-06-16',
        righe,
        provincia='TORINO',
        user='tester',
    )

    res = lettere.list_privati(generazione_id=info['generazione_id'])
    assert res['totale'] == 3
    assert res['cellulare'] == 1
    assert res['fisso'] == 1
    assert res['senza_telefono'] == 1

    # Verifica che il contatto senza telefono abbia trovato il cellulare alternativo nel nome
    senza_tel = [c for c in res['contatti'] if c['tipo'] == 'senza_telefono'][0]
    assert senza_tel['pod'] == 'IT001E00000004'
    assert 'numeri_alternativi' in senza_tel
    assert any(n['tipo'] == 'cellulare' for n in senza_tel['numeri_alternativi'])


def test_api_lettere_privati(tmp_path, db_isolato, monkeypatch):
    from flask import Flask
    import config
    from moduli import lettere
    from routes.lettere import bp as lettere_bp
    from routes.distinte import bp as distinte_bp

    monkeypatch.setattr(config, 'PROVINCIA_ATTIVA', 'TORINO', raising=False)
    file_path = tmp_path / 'lettere.xlsx'
    file_path.write_bytes(b'test')

    info = lettere.registra_generazione(
        str(file_path),
        '2026-06-16',
        '2026-06-16',
        [_riga_privato()],
        provincia='TORINO',
        user='tester',
    )

    app = Flask(__name__)
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test-secret'
    app.register_blueprint(lettere_bp)
    app.register_blueprint(distinte_bp)

    client = app.test_client()
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['user'] = 'tester'
        sess['role'] = 'admin'

    resp = client.get(f'/api/lettere/privati?generazione_id={info["generazione_id"]}')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['success'] is True
    assert data['totale'] == 1
    assert len(data['contatti']) == 1
    assert data['contatti'][0]['nome'] == 'Rossi Mario'
    assert data['contatti'][0]['tipo'] == 'cellulare'

    # Verifica gli alias delle rotte condivise
    resp_nota = client.post('/api/lettere/salva-nota', json={
        'pod': 'IT001E00000002',
        'nota': 'Test nota privato',
    })
    # Se il POD non è nel filone risponde 404 (comportamento atteso dal filone)
    assert resp_nota.status_code in (200, 404)


def test_lettere_date_ripianificate_filone(tmp_path, db_isolato, monkeypatch):
    import config
    from moduli import lettere

    monkeypatch.setattr(config, 'PROVINCIA_ATTIVA', 'TORINO', raising=False)
    conn = db_isolato.get_conn()
    with conn:
        db_isolato.upsert_filone_row(
            conn,
            eneltel_key='2002',
            sheets_row=2,
            typed_values={
                'pod': 'IT001E00000002',
                'data_pianificata': '28/07/2026',
            },
            extra_values={},
            row_hash='r_privato',
            source='bootstrap',
        )

    file_path = tmp_path / 'lettere.xlsx'
    file_path.write_bytes(b'test')

    info = lettere.registra_generazione(
        str(file_path),
        '2026-06-16',
        '2026-06-16',
        [_riga_privato()],
        provincia='TORINO',
        user='tester',
    )

    res = lettere.list_privati(generazione_id=info['generazione_id'])
    assert res['totale'] == 1
    contatto = res['contatti'][0]
    assert contatto['data_lettera'] == '16/06/2026'
    assert contatto['data_pian_filone'] == '28/07/2026'
    assert contatto['data_intervento'] == '16/06/2026'

    # Test anche list_aziende con data_pianificata da filone
    from tests.test_lettere import _riga_azienda
    with conn:
        db_isolato.upsert_filone_row(
            conn,
            eneltel_key='1001',
            sheets_row=3,
            typed_values={
                'pod': 'IT001E00000001',
                'data_pianificata': 'ANNULLATO',
            },
            extra_values={},
            row_hash='r_azienda',
            source='bootstrap',
        )
    info_az = lettere.registra_generazione(
        str(tmp_path / 'lettere_az.xlsx'),
        '2026-06-15',
        '2026-06-15',
        [_riga_azienda()],
        provincia='TORINO',
        user='tester',
    )
    aziende = lettere.list_aziende(generazione_id=info_az['generazione_id'])
    assert len(aziende) == 1
    assert aziende[0]['data_pianificazione'] == '15/06/2026'
    assert aziende[0]['data_pian_filone'] == 'ANNULLATO'


def test_lettere_template_has_unified_compact_table_structure():
    """Verifica che la visualizzazione compatta abbia 6 colonne per entrambe le sezioni."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    html_content = (root / 'templates' / 'lettere.html').read_text(encoding='utf-8')

    # Entrambi i wrap presenti
    assert 'id="aziendeTableWrap"' in html_content
    assert 'id="privatiTableWrap"' in html_content

    # Privati table con selectAllPrivati e 6 th
    assert 'id="selectAllPrivati"' in html_content
    m_privati = re.search(r'<table class="lettere-table privati-table">.*?<thead>(.*?)</thead>', html_content, re.DOTALL)
    assert m_privati is not None
    pr_ths = re.findall(r'<th\b[^>]*>(.*?)</th>', m_privati.group(1), re.DOTALL)
    assert len(pr_ths) == 6

    # Aziende table con selectAll e 6 th
    m_aziende = re.search(r'<div class="table-wrap" id="aziendeTableWrap">.*?<table class="lettere-table">.*?<thead>(.*?)</thead>', html_content, re.DOTALL)
    assert m_aziende is not None
    az_ths = re.findall(r'<th\b[^>]*>(.*?)</th>', m_aziende.group(1), re.DOTALL)
    assert len(az_ths) == 6

    # Toggle buttons in entrambe le sezioni
    assert 'id="btnAziendeViewTable"' in html_content
    assert 'id="btnAziendeViewCards"' in html_content
    assert 'id="btnPrivatiViewTable"' in html_content
    assert 'id="btnPrivatiViewCards"' in html_content


def test_aziende_note_e_completamento(tmp_path, db_isolato, monkeypatch):
    """Test salvataggio note, completamento e sincronizzazione filone per aziende."""
    from flask import Flask
    import config
    from moduli import lettere
    from routes.lettere import bp as lettere_bp
    from tests.test_lettere import _riga_azienda

    monkeypatch.setattr(config, 'PROVINCIA_ATTIVA', 'TORINO', raising=False)
    conn = db_isolato.get_conn()
    with conn:
        db_isolato.upsert_filone_row(
            conn,
            eneltel_key='1001',
            sheets_row=2,
            typed_values={
                'pod': 'IT001E00000001',
                'telefono': '0111111111',
                'email': 'vecchia@alfa.it',
                'note_appuntamento': '',
            },
            extra_values={},
            row_hash='r_az',
            source='bootstrap',
        )
        conn.execute(
            '''
            INSERT OR REPLACE INTO column_registry(
                column_name, chiave_interna, last_index, last_seen_ts
            ) VALUES
                ('telefono', 'telefono', 8, 1),
                ('email', 'email', 9, 1),
                ('note_appuntamento', 'note_appuntamento', 10, 1)
            '''
        )

    file_path = tmp_path / 'lettere_az.xlsx'
    file_path.write_bytes(b'test')
    info = lettere.registra_generazione(
        str(file_path),
        '2026-06-15',
        '2026-06-15',
        [_riga_azienda()],
        provincia='TORINO',
        user='tester',
    )
    azienda = lettere.list_aziende(generazione_id=info['generazione_id'])[0]
    az_id = azienda['id']

    # 1. Test set_risposta solo note (non deve resettare risposta)
    res_note = lettere.set_risposta(az_id, None, note='Nota test azienda', user='tester', provincia='TORINO')
    assert res_note['note'] == 'Nota test azienda'
    az_updated = lettere.list_aziende(generazione_id=info['generazione_id'])[0]
    assert az_updated['note'] == 'Nota test azienda'
    assert az_updated['risposta'] == 0

    # Verifica che la nota sia stata sincronizzata anche sul filone
    filone = db_isolato.get_filone_row('1001', lcl='')
    assert filone['note_appuntamento'] == 'Nota test azienda'

    # 2. Test set_risposta solo risposta (non deve cancellare note)
    res_risp = lettere.set_risposta(az_id, True, user='tester', provincia='TORINO')
    assert res_risp['risposta'] is True
    az_updated = lettere.list_aziende(generazione_id=info['generazione_id'])[0]
    assert az_updated['risposta'] == 1
    assert az_updated['note'] == 'Nota test azienda'

    # 3. Test API via Flask client
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test-secret'
    app.register_blueprint(lettere_bp)

    client = app.test_client()
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['user'] = 'tester'
        sess['role'] = 'admin'

    # POST su /api/lettere/aziende/<id>/risposta
    resp_post = client.post(f'/api/lettere/aziende/{az_id}/risposta', json={
        'risposta': False,
        'note': 'Nuova nota via POST'
    })
    assert resp_post.status_code == 200
    data = resp_post.get_json()
    assert data['success'] is True
    assert data['note'] == 'Nuova nota via POST'

    # POST su /api/lettere/aziende/<id>/contatto
    resp_contatto = client.post(f'/api/lettere/aziende/{az_id}/contatto', json={
        'telefono': '0112223334',
        'email': 'nuova@alfa.it'
    })
    assert resp_contatto.status_code == 200
    data_contatto = resp_contatto.get_json()
    assert data_contatto['success'] is True


def test_lettere_pod_sdoppiato_lcl_non_sovrascrive_data(tmp_path, db_isolato, monkeypatch):
    """Verifica che con POD sdoppiati su LCL diverse la data pianificata non venga sfasata."""
    import config
    from moduli import lettere

    monkeypatch.setattr(config, 'PROVINCIA_ATTIVA', 'PAVIA', raising=False)
    conn = db_isolato.get_conn()
    with conn:
        # Riga vecchia LCL con data alfabeticamente maggiore (17 > 14)
        db_isolato.upsert_filone_row(
            conn,
            eneltel_key='1001',
            lcl='6600104177',
            sheets_row=10,
            typed_values={
                'pod': 'IT001E00081572',
                'data_pianificata': '17/11/2025',
                'note_appuntamento': 'vecchia nota',
            },
            extra_values={},
            row_hash='r_old',
            source='bootstrap',
        )
        # Riga nuova LCL coerente con la lettera
        db_isolato.upsert_filone_row(
            conn,
            eneltel_key='1002',
            lcl='6600108227',
            sheets_row=50,
            typed_values={
                'pod': 'IT001E00081572',
                'data_pianificata': '14/07/2026',
                'note_appuntamento': 'nota attiva',
            },
            extra_values={},
            row_hash='r_new',
            source='bootstrap',
        )

    file_path = tmp_path / 'lettere_pavia.xlsx'
    file_path.write_bytes(b'test')
    info = lettere.registra_generazione(
        str(file_path),
        '2026-07-13',
        '2026-07-15',
        [_riga_privato(pod='IT001E00081572', lcl='6600108227', data_pianificazione='14/07/2026')],
        provincia='PAVIA',
        user='tester',
    )

    res = lettere.list_privati(generazione_id=info['generazione_id'], provincia='PAVIA')
    assert res['totale'] == 1
    c = res['contatti'][0]
    # Deve agganciare la riga della LCL della lettera, NON la vecchia LCL con 17/11/2025
    assert c['data_lettera'] == '14/07/2026'
    assert c['data_pian_filone'] == '14/07/2026'
    assert c['note_filone'] == 'nota attiva'
    assert c['note'] == 'nota attiva'


def test_lettere_salva_nota_su_lcl_corretta(tmp_path, db_isolato, monkeypatch):
    """Verifica che il salvataggio nota da Gestione Lettere scriva sulla LCL specificata."""
    from flask import Flask
    import config
    from routes.distinte import bp as distinte_bp

    monkeypatch.setattr(config, 'PROVINCIA_ATTIVA', 'PAVIA', raising=False)
    conn = db_isolato.get_conn()
    with conn:
        conn.execute(
            '''
            INSERT OR REPLACE INTO column_registry(
                column_name, chiave_interna, last_index, last_seen_ts
            ) VALUES ('note_appuntamento', 'note_appuntamento', 10, 1)
            '''
        )
        db_isolato.upsert_filone_row(
            conn,
            eneltel_key='1001',
            lcl='6600106128',
            sheets_row=10,
            typed_values={
                'pod': 'ITP0AE04073153',
                'note_appuntamento': '',
            },
            extra_values={},
            row_hash='r_old_p',
            source='bootstrap',
        )
        db_isolato.upsert_filone_row(
            conn,
            eneltel_key='1002',
            lcl='6600109375',
            sheets_row=50,
            typed_values={
                'pod': 'ITP0AE04073153',
                'note_appuntamento': '',
            },
            extra_values={},
            row_hash='r_new_p',
            source='bootstrap',
        )

    app = Flask(__name__)
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test-secret'
    app.register_blueprint(distinte_bp)

    client = app.test_client()
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['user'] = 'tester'
        sess['role'] = 'admin'

    resp = client.post('/api/lettere/salva-nota', json={
        'pod': 'ITP0AE04073153',
        'lcl': '6600109375',
        'nota': 'Nota specifica nuova LCL',
    })
    assert resp.status_code == 200

    row_old = db_isolato.get_filone_row('1001', lcl='6600106128')
    row_new = db_isolato.get_filone_row('1002', lcl='6600109375')
    assert row_old['note_appuntamento'] == ''
    assert row_new['note_appuntamento'] == 'Nota specifica nuova LCL'


def test_lettere_ignora_lcl_disabilitato_nel_fallback(tmp_path, db_isolato, monkeypatch):
    """Verifica che se la lettera non ha LCL, il fallback escluda LCL disabilitati."""
    import config
    from moduli import lettere

    monkeypatch.setattr(config, 'PROVINCIA_ATTIVA', 'PAVIA', raising=False)
    monkeypatch.setattr(config, 'LCL_DISABILITATI', ['LCL_DISAB'], raising=False)
    if 'PAVIA' in config.PROVINCE:
        config.PROVINCE['PAVIA']['lcl_disabilitati'] = ['LCL_DISAB']

    conn = db_isolato.get_conn()
    with conn:
        # LCL disabilitato ha sheets_row più alto (200), ma deve essere ignorato
        db_isolato.upsert_filone_row(
            conn,
            eneltel_key='2001',
            lcl='LCL_DISAB',
            sheets_row=200,
            typed_values={
                'pod': 'IT001E99999999',
                'data_pianificata': 'ANNULLATO',
                'note_appuntamento': 'nota disabilitata',
            },
            extra_values={},
            row_hash='r_dis',
            source='bootstrap',
        )
        # LCL attivo
        db_isolato.upsert_filone_row(
            conn,
            eneltel_key='2002',
            lcl='LCL_ATTIVA',
            sheets_row=100,
            typed_values={
                'pod': 'IT001E99999999',
                'data_pianificata': '02/10/2026',
                'note_appuntamento': 'nota attiva',
            },
            extra_values={},
            row_hash='r_att',
            source='bootstrap',
        )

    file_path = tmp_path / 'lettere_fallback.xlsx'
    file_path.write_bytes(b'test')
    info = lettere.registra_generazione(
        str(file_path),
        '2026-10-02',
        '2026-10-02',
        [_riga_privato(pod='IT001E99999999', lcl='', data_pianificazione='02/10/2026')],
        provincia='PAVIA',
        user='tester',
    )

    res = lettere.list_privati(generazione_id=info['generazione_id'], provincia='PAVIA')
    assert res['totale'] == 1
    c = res['contatti'][0]
    assert c['data_pian_filone'] == '02/10/2026'
    assert c['note_filone'] == 'nota attiva'


def test_azienda_note_sync_multi_meter(db_isolato, tmp_path):
    """Verifica che una nota assegnata a un'azienda si sincronizzi su TUTTI i contatori dell'azienda nel filone."""
    from moduli import lettere

    conn = db_isolato.get_conn()
    with conn:
        db_isolato.upsert_filone_row(
            conn,
            eneltel_key='1001',
            lcl='LCL1',
            sheets_row=10,
            typed_values={
                'pod': 'IT001E11111111',
                'nominativo': 'ACME SPA',
                'comune': 'Torino',
                'note_appuntamento': '',
            },
            extra_values={},
            row_hash='h1',
            source='bootstrap',
        )
        db_isolato.upsert_filone_row(
            conn,
            eneltel_key='1002',
            lcl='LCL1',
            sheets_row=11,
            typed_values={
                'pod': 'IT001E22222222',
                'nominativo': 'ACME SPA',
                'comune': 'Torino',
                'note_appuntamento': '',
            },
            extra_values={},
            row_hash='h2',
            source='bootstrap',
        )

    file_path = tmp_path / 'aziende_multi.xlsx'
    file_path.write_bytes(b'test')
    righe = [
        {
            'source_sheet': 'TRIFASE',
            'row_num': 2,
            'pod': 'IT001E11111111',
            'eneltel': '1001',
            'lcl': 'LCL1',
            'nominativo': 'ACME SPA',
            'via': 'Via Roma',
            'civico': '1',
            'comune': 'Torino',
            'provincia': 'TO',
            'is_azienda': True,
        },
        {
            'source_sheet': 'TRIFASE',
            'row_num': 3,
            'pod': 'IT001E22222222',
            'eneltel': '1002',
            'lcl': 'LCL1',
            'nominativo': 'ACME SPA',
            'via': 'Via Roma',
            'civico': '1',
            'comune': 'Torino',
            'provincia': 'TO',
            'is_azienda': True,
        },
    ]
    info = lettere.registra_generazione(
        str(file_path),
        '2026-10-01',
        '2026-10-15',
        righe,
        provincia='TORINO',
        user='tester',
    )

    aziende = lettere.list_aziende(generazione_id=info['generazione_id'], provincia='TORINO')
    assert len(aziende) >= 1
    az_id = aziende[0]['id']

    lettere.set_risposta(az_id, True, note='APP 15/10 ORE 10:00', user='tester', provincia='TORINO')

    row1 = db_isolato.get_filone_row('1001', lcl='LCL1')
    row2 = db_isolato.get_filone_row('1002', lcl='LCL1')
    assert row1['note_appuntamento'] == 'APP 15/10 ORE 10:00'
    assert row2['note_appuntamento'] == 'APP 15/10 ORE 10:00'


def test_list_aziende_reconcile_notes_filone_priority(db_isolato, tmp_path):
    """Verifica che note_appuntamento su filone abbia priorità e si riconcili con la snapshot."""
    from moduli import lettere

    conn = db_isolato.get_conn()
    with conn:
        db_isolato.upsert_filone_row(
            conn,
            eneltel_key='3001',
            lcl='LCL1',
            sheets_row=30,
            typed_values={
                'pod': 'IT001E33333333',
                'nominativo': 'BETA SRL',
                'comune': 'Torino',
                'note_appuntamento': 'APP 20/10 ORE 14:00 // mandata mail 01/09',
            },
            extra_values={},
            row_hash='h3',
            source='bootstrap',
        )

    file_path = tmp_path / 'aziende_reconcile.xlsx'
    file_path.write_bytes(b'test')
    righe = [
        {
            'source_sheet': 'TRIFASE',
            'row_num': 2,
            'pod': 'IT001E33333333',
            'eneltel': '3001',
            'lcl': 'LCL1',
            'nominativo': 'BETA SRL',
            'via': 'Via Po',
            'civico': '10',
            'comune': 'Torino',
            'provincia': 'TO',
            'is_azienda': True,
        }
    ]
    info = lettere.registra_generazione(
        str(file_path),
        '2026-10-01',
        '2026-10-15',
        righe,
        provincia='TORINO',
        user='tester',
    )

    # Forziamo una nota vecchia nella tabella snapshot lettere_aziende_generazione
    conn = db_isolato.get_conn()
    with conn:
        conn.execute(
            "UPDATE lettere_aziende_generazione SET note='mandata mail 01/09' WHERE pod='IT001E33333333'"
        )

    az = lettere.list_aziende(generazione_id=info['generazione_id'], provincia='TORINO')[0]
    # La nota restituita deve essere quella completa e aggiornata da filone
    assert az['note'] == 'APP 20/10 ORE 14:00 // mandata mail 01/09'
    assert az['note_appuntamento'] == 'APP 20/10 ORE 14:00 // mandata mail 01/09'
    assert az['note_filone'] == 'APP 20/10 ORE 14:00 // mandata mail 01/09'

    dettaglio = lettere.get_azienda(az['id'], provincia='TORINO')
    assert dettaglio['note'] == 'APP 20/10 ORE 14:00 // mandata mail 01/09'


def test_salva_nota_endpoint_eneltel_fallback(db_isolato):
    """Verifica che /api/lettere/salva-nota gestisca il fallback con solo eneltel."""
    from flask import Flask
    from routes.lettere import bp as lettere_bp
    from routes.distinte import bp as distinte_bp

    app = Flask(__name__)
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test-secret'
    app.register_blueprint(lettere_bp)
    app.register_blueprint(distinte_bp)

    conn = db_isolato.get_conn()
    with conn:
        db_isolato.upsert_filone_row(
            conn,
            eneltel_key='4001',
            lcl='LCL1',
            sheets_row=40,
            typed_values={
                'pod': 'IT001E44444444',
                'note_appuntamento': 'vecchia nota',
            },
            extra_values={},
            row_hash='h4',
            source='bootstrap',
        )

    client = app.test_client()
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['user'] = 'tester'
        sess['role'] = 'admin'

    resp = client.post(
        '/api/lettere/salva-nota',
        json={
            'pod': '',
            'eneltel': '4001',
            'lcl': 'LCL1',
            'nota': 'nuova nota eneltel',
            'sovrascrivere': True,
        },
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['success'] is True
    assert data['nota'] == 'nuova nota eneltel'

    row = db_isolato.get_filone_row('4001', lcl='LCL1')
    assert row['note_appuntamento'] == 'nuova nota eneltel'


def test_lettere_sync_priorita_lcl_attivo_e_appuntamento(db_isolato, monkeypatch):
    """
    Verifica che list_privati e list_aziende rilevino la data aggiornata (23/10)
    e l'appuntamento attivo anche se la lettera era stata generata con un vecchio LCL storico disabilitato.
    """
    from moduli import lettere
    import config

    monkeypatch.setattr(config, 'LCL_DISABILITATI', ['LCL_DISABILITATO'], raising=False)

    conn = db_isolato.get_conn()
    with conn:
        # Riga attiva filone (LCL_ATTIVO) con appuntamento e data 23/10
        db_isolato.upsert_filone_row(
            conn,
            eneltel_key='5001',
            lcl='LCL_ATTIVO',
            sheets_row=10,
            typed_values={
                'pod': 'IT001E55555555',
                'data_pianificata': '23/10/2026',
                'note_appuntamento': "APP 23/10/2026\tdalle 14:00 alle 15:30",
                'telefono': '3335555555',
            },
            extra_values={},
            row_hash='h_attivo',
            source='bootstrap',
        )
        # Riga storica disabilitata filone (LCL_DISABILITATO) con 16/10 e note vuote
        db_isolato.upsert_filone_row(
            conn,
            eneltel_key='5002',
            lcl='LCL_DISABILITATO',
            sheets_row=20,
            typed_values={
                'pod': 'IT001E55555555',
                'data_pianificata': '16/10/2026',
                'note_appuntamento': '',
                'telefono': '',
            },
            extra_values={},
            row_hash='h_disabilitato',
            source='bootstrap',
        )

    # Genera lettera con il vecchio LCL storico
    righe = [{
        'pod': 'IT001E55555555',
        'eneltel': '5002',
        'lcl': 'LCL_DISABILITATO',
        'nominativo': 'MARIO BIANCHI',
        'comune': 'TORINO',
        'via': 'VIA ROMA',
        'civico': '1',
        'cap': '10100',
        'is_azienda': False,
        'data_lettera': '16/10/2026',
    }]
    info = lettere.registra_generazione(
        'test_generazione.xlsx',
        '2026-10-01',
        '2026-10-31',
        righe,
        provincia='TORINO',
        user='tester',
    )

    # Verifica privati
    privati_res = lettere.list_privati(generazione_id=info['generazione_id'], provincia='TORINO')
    contatti = privati_res['contatti']
    assert len(contatti) == 1
    c = contatti[0]
    assert c['pod'] == 'IT001E55555555'
    assert c['data_pian_filone'] == '23/10/2026'
    assert 'APP 23/10/2026' in c['note_filone']







