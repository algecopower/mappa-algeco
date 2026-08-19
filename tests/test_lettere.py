# -*- coding: utf-8 -*-
"""Test dell'Archivio Lettere e della rubrica aziende."""

from openpyxl import Workbook


def _riga_azienda(**overrides):
    row = {
        'source_sheet': 'MONOFASE',
        'row_num': 2,
        'pod': 'IT001E00000001',
        'eneltel': '1001',
        'lcl': '',
        'nominativo': 'Alfa SRL',
        'via': 'Roma',
        'civico': '10',
        'comune': 'Torino',
        'provincia': 'TO',
        'cap': '10100',
        'data_pianificazione': '15/06/2026',
        'ora_pianificazione': '08:00 - 11:00',
        'lettera': "SI'",
        'operatore': 'TEC1',
        'posizione_mis': 'Locale tecnico',
        'appuntamento': '',
        'tipo_misuratore': 'TRIFASE DIRETTO',
        'telefono': '',
        'email': '',
        'is_azienda': True,
    }
    row.update(overrides)
    return row


def test_contatto_unico_email_pec_si_sincronizza_sul_filone(
    tmp_path, db_isolato, monkeypatch,
):
    import config
    from moduli import lettere

    monkeypatch.setattr(config, 'PROVINCIA_ATTIVA', 'TORINO', raising=False)
    conn = db_isolato.get_conn()
    with conn:
        db_isolato.upsert_filone_row(
            conn,
            eneltel_key='1001',
            sheets_row=2,
            typed_values={
                'pod': 'IT001E00000001',
                'telefono': '',
                'email': '',
            },
            extra_values={},
            row_hash='r1',
            source='bootstrap',
        )
        conn.execute(
            '''
            INSERT OR REPLACE INTO column_registry(
                column_name, chiave_interna, last_index, last_seen_ts
            ) VALUES
                ('telefono', 'telefono', 8, 1),
                ('email', 'email', 9, 1)
            '''
        )

    file_path = tmp_path / 'lettere.xlsx'
    file_path.write_bytes(b'lettere-test')
    info = lettere.registra_generazione(
        str(file_path),
        '2026-06-15',
        '2026-06-15',
        [_riga_azienda()],
        provincia='TORINO',
        user='tester',
    )
    azienda = lettere.list_aziende(generazione_id=info['generazione_id'])[0]

    email = 'ufficio@alfa.it / alfa@legalmail.it'
    updated = lettere.update_contatto(
        azienda['contact_key'],
        telefono='0111234567',
        email=email,
        user='tester',
        provincia='TORINO',
    )

    assert updated['email'] == email
    assert updated['sync_filone']['synced'] is True
    filone = db_isolato.get_filone_row('1001', lcl='')
    assert filone['telefono'] == '0111234567'
    assert filone['email'] == email

    indirizzi = lettere.split_emails(email)
    assert indirizzi == [
        {'email': 'ufficio@alfa.it', 'pec': False},
        {'email': 'alfa@legalmail.it', 'pec': True},
    ]


def test_feedback_e_invii_vengono_calcolati_dal_db(
    tmp_path, db_isolato, monkeypatch,
):
    import config
    from moduli import lettere

    monkeypatch.setattr(config, 'PROVINCIA_ATTIVA', 'TORINO', raising=False)
    file_path = tmp_path / 'lettere.xlsx'
    file_path.write_bytes(b'feedback-test')
    info = lettere.registra_generazione(
        str(file_path),
        '2026-06-15',
        '2026-06-15',
        [_riga_azienda(email='alfa@pec.it')],
        provincia='TORINO',
        user='tester',
    )
    azienda = lettere.list_aziende(generazione_id=info['generazione_id'])[0]

    lettere.registra_invio(
        azienda_generazione_id=azienda['id'],
        generazione_id=info['generazione_id'],
        contact_key_value=azienda['contact_key'],
        pod=azienda['pod'],
        email_dest='alfa@pec.it',
        template_id='email_societa',
        subject='Test',
        status='sent',
        user='tester',
        provincia='TORINO',
    )
    lettere.set_risposta(
        azienda['id'],
        True,
        note='Confermata disponibilità',
        user='tester',
        provincia='TORINO',
    )

    stats = lettere.statistiche_feedback(provincia='TORINO')
    assert stats['generazioni'] == 1
    assert stats['aziende'] == 1
    assert stats['contattabili'] == 1
    assert stats['contattate'] == 1
    assert stats['risposte'] == 1
    assert stats['invii_pec'] == 1
    assert stats['tasso_feedback'] == 100.0


def test_lista_mostra_lo_storico_invii_del_contatto(
    tmp_path, db_isolato, monkeypatch,
):
    import config
    from moduli import lettere

    monkeypatch.setattr(config, 'PROVINCIA_ATTIVA', 'TORINO', raising=False)
    file_path = tmp_path / 'lettere.xlsx'
    file_path.write_bytes(b'invii-contatto')
    info = lettere.registra_generazione(
        str(file_path),
        '2026-06-15',
        '2026-06-15',
        [_riga_azienda(email='alfa@example.it')],
        provincia='TORINO',
    )
    azienda = lettere.list_aziende(generazione_id=info['generazione_id'])[0]

    lettere.registra_invio(
        contact_key_value=azienda['contact_key'],
        pod=azienda['pod'],
        email_dest='alfa@example.it',
        template_id='legacy_imap',
        subject='Storico',
        status='sent',
        sent_ts=1_765_000_000,
        provincia='TORINO',
    )

    aggiornata = lettere.list_aziende(generazione_id=info['generazione_id'])[0]
    assert aggiornata['invii_ok'] == 1
    assert aggiornata['invii_generazione'] == 0
    assert aggiornata['last_sent_ts'] == 1_765_000_000


def test_import_storico_unisce_mail_e_pec_nel_campo_email(tmp_path):
    from tools.import_lettere_storiche import leggi_file

    wb = Workbook()
    ws = wb.active
    ws.title = 'MONOFASE'
    ws.append([
        'Nominativo_Cliente', 'VIA', 'Numero_Civico', 'Comune', 'PROVINCIA',
        'CAP', 'Data Pianificazione', 'Ora pianificazione', 'LETTERA', 'POD',
        'Operatore', 'Posizione mis.', 'Appuntamento',
    ])
    ws.append([
        'Alfa SRL', 'Roma', '10', 'Torino', 'TO', '10100', '15/06/2026',
        '08:00 - 11:00', "SI'", 'IT001E00000001', 'TEC1', '', '',
    ])

    contatti = wb.create_sheet('Contatti Aziendali')
    contatti.append(['Titolo'])
    contatti.append([])
    contatti.append([])
    contatti.append([])
    contatti.append(['NOMINATIVO', 'POD', 'TEL', 'MAIL', 'PEC'])
    contatti.append([
        'Alfa SRL', 'IT001E00000001', '0111234567',
        'info@alfa.it', 'alfa@legalmail.it',
    ])

    path = tmp_path / 'LETTERE TORINO DAL 15-06 AL 15-06.xlsx'
    wb.save(path)

    righe, periodo_da, periodo_a = leggi_file(path)
    assert periodo_da == '2026-06-15'
    assert periodo_a == '2026-06-15'
    assert righe[0]['telefono'] == '0111234567'
    assert righe[0]['email'] == 'info@alfa.it / alfa@legalmail.it'
    assert righe[0]['is_azienda'] is True


def test_generazioni_ordinate_per_periodo_piu_recente(
    tmp_path, db_isolato, monkeypatch,
):
    import config
    from moduli import lettere

    monkeypatch.setattr(config, 'PROVINCIA_ATTIVA', 'TORINO', raising=False)
    for idx, (periodo_da, periodo_a) in enumerate((
        ('2026-04-01', '2026-04-05'),
        ('2026-07-01', '2026-07-10'),
        ('2026-06-01', '2026-06-30'),
    )):
        path = tmp_path / f'lettere_{idx}.xlsx'
        path.write_bytes(f'file-{idx}'.encode())
        lettere.registra_generazione(
            str(path),
            periodo_da,
            periodo_a,
            [],
            provincia='TORINO',
        )

    periodi = [g['periodo_a'] for g in lettere.list_generazioni(provincia='TORINO')]
    assert periodi == ['2026-07-10', '2026-06-30', '2026-04-05']


def test_sincronizza_recapiti_vuoti_dal_filone_senza_sovrascrivere(
    tmp_path, db_isolato, monkeypatch,
):
    import config
    from moduli import lettere

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
                'email': 'filone@azienda.it',
            },
            extra_values={},
            row_hash='r1',
            source='bootstrap',
        )

    path = tmp_path / 'lettere.xlsx'
    path.write_bytes(b'sync-recapiti')
    lettere.registra_generazione(
        str(path),
        '2026-06-15',
        '2026-06-15',
        [_riga_azienda(telefono='', email='manuale@azienda.it')],
        provincia='TORINO',
    )

    result = lettere.sincronizza_contatti_da_filone(provincia='TORINO')
    azienda = lettere.list_aziende(provincia='TORINO')[0]

    assert result['telefoni_compilati'] == 1
    assert result['email_compilate'] == 0
    assert azienda['telefono'] == '0111111111'
    assert azienda['email'] == 'manuale@azienda.it'


def test_list_aziende_include_indirizzo_e_coordinate(
    tmp_path, db_isolato, monkeypatch,
):
    import config
    from moduli import lettere

    monkeypatch.setattr(config, 'PROVINCIA_ATTIVA', 'TORINO', raising=False)
    conn = db_isolato.get_conn()
    with conn:
        db_isolato.upsert_filone_row(
            conn,
            eneltel_key='1001',
            sheets_row=2,
            typed_values={
                'pod': 'IT001E00000001',
                'indirizzo': 'VIA ROMA',
                'civico': '10',
                'latitudine': 45.0703,
                'longitudine': 7.6869,
            },
            extra_values={},
            row_hash='r1',
            source='bootstrap',
        )

    path = tmp_path / 'lettere_coord.xlsx'
    path.write_bytes(b'coord-test')
    info = lettere.registra_generazione(
        str(path),
        '2026-06-15',
        '2026-06-15',
        [_riga_azienda(via='VIA ROMA', civico='10')],
        provincia='TORINO',
    )

    aziende = lettere.list_aziende(generazione_id=info['generazione_id'])
    assert len(aziende) == 1
    assert aziende[0]['via'] == 'VIA ROMA'
    assert aziende[0]['civico'] == '10'
    assert aziende[0]['latitudine'] == 45.0703
    assert aziende[0]['longitudine'] == 7.6869


def test_list_aziende_filtra_blacklist_parole_chiave(tmp_path, db_isolato, monkeypatch):
    import config
    from moduli import lettere

    monkeypatch.setattr(config, 'PROVINCIA_ATTIVA', 'TORINO', raising=False)
    path = tmp_path / 'lettere_blacklist.xlsx'
    path.write_bytes(b'blacklist-test')

    righe = [
        _riga_azienda(nominativo='ACME SRL', pod='IT001E00000001'),
        _riga_azienda(nominativo='COMUNE DI MONCALIERI', pod='IT001E00000002'),
        _riga_azienda(nominativo='PROVINCIA DI TORINO', pod='IT001E00000003'),
        _riga_azienda(nominativo='CONDOMINIO CORSO FRANCIA 10', pod='IT001E00000004'),
        _riga_azienda(nominativo='FIBERCOP SPA', pod='IT001E00000005'),
    ]
    info = lettere.registra_generazione(
        str(path),
        '2026-06-15',
        '2026-06-15',
        righe,
        provincia='TORINO',
    )

    aziende = lettere.list_aziende(generazione_id=info['generazione_id'])
    nomi = [a['nominativo'] for a in aziende]
    assert nomi == ['ACME SRL']


def test_propagazione_contatti_stessa_azienda_comune(tmp_path, db_isolato, monkeypatch):
    import config
    from moduli import lettere

    monkeypatch.setattr(config, 'PROVINCIA_ATTIVA', 'TORINO', raising=False)
    path = tmp_path / 'lettere_multi.xlsx'
    path.write_bytes(b'multi-test')

    righe = [
        _riga_azienda(nominativo='BETA LOGISTICA SRL', comune='TORINO', pod='IT001E00000011', telefono='', email=''),
        _riga_azienda(nominativo='BETA LOGISTICA SRL', comune='TORINO', pod='IT001E00000012', telefono='', email=''),
    ]
    info = lettere.registra_generazione(
        str(path),
        '2026-06-15',
        '2026-06-15',
        righe,
        provincia='TORINO',
    )

    aziende = lettere.list_aziende(generazione_id=info['generazione_id'])
    assert len(aziende) == 2
    assert not aziende[0]['email']
    assert not aziende[1]['email']

    # Compila il primo contatore
    lettere.update_contatto(
        aziende[0]['contact_key'],
        telefono='011999999',
        email='info@betalogistica.it',
        user='test',
    )

    # Entrambi i contatori ora hanno i recapiti propagati
    aziende_dopo = lettere.list_aziende(generazione_id=info['generazione_id'])
    assert aziende_dopo[0]['telefono'] == '011999999'
    assert aziende_dopo[0]['email'] == 'info@betalogistica.it'
    assert aziende_dopo[1]['telefono'] == '011999999'
    assert aziende_dopo[1]['email'] == 'info@betalogistica.it'


def test_note_appuntamento_filone_in_list_aziende(tmp_path, db_isolato, monkeypatch):
    import config
    from moduli import lettere

    monkeypatch.setattr(config, 'PROVINCIA_ATTIVA', 'TORINO', raising=False)
    conn = db_isolato.get_conn()
    with conn:
        db_isolato.upsert_filone_row(
            conn,
            eneltel_key='2001',
            sheets_row=3,
            typed_values={
                'pod': 'IT001E00000099',
                'telefono': '011111111',
                'email': 'gamma@test.it',
                'note_appuntamento': 'Citofonare custode scala B',
            },
            extra_values={},
            row_hash='r_gamma',
            source='bootstrap',
        )

    path = tmp_path / 'lettere_gamma.xlsx'
    path.write_bytes(b'gamma-test')

    righe = [
        _riga_azienda(
            nominativo='GAMMA SPA',
            comune='TORINO',
            pod='IT001E00000099',
            eneltel='2001',
        ),
    ]
    info = lettere.registra_generazione(
        str(path),
        '2026-06-15',
        '2026-06-15',
        righe,
        provincia='TORINO',
    )

    aziende = lettere.list_aziende(generazione_id=info['generazione_id'])
    assert len(aziende) == 1
    assert aziende[0]['note_appuntamento'] == 'Citofonare custode scala B'



