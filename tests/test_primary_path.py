# -*- coding: utf-8 -*-
"""
Test del path "primary" (Fase 2): le route scrivono solo nel DB, il drainer
asincrono pusherà su Sheets. La risposta API è in <100ms.

Verifichiamo:
- _route_to_db_primary: anchor Eneltel/POD/sheets_row
- pending in stato 'pending' (non 'done' come in shadow)
- _apply_pending_to_values: il merge nel data_loader sostituisce le celle
- _calcola_nuova_nota_app / _calcola_rimozione_app: regex parser
"""


def _setup_riga(db, eneltel='12345', pod='IT001E1234567', sheets_row=10,
                nominativo='Mario'):
    conn = db.get_conn()
    with conn:
        db.upsert_filone_row(
            conn,
            eneltel_key=eneltel,
            sheets_row=sheets_row,
            typed_values={'pod': pod, 'nominativo': nominativo},
            extra_values={},
            row_hash='abc',
            source='bootstrap',
        )


def test_route_primary_anchor_eneltel(db_isolato, monkeypatch):
    db = db_isolato
    _setup_riga(db, eneltel='12345', sheets_row=10)

    from moduli import shared
    items = [
        {'row': 10, 'col': 'Operatore', 'value': 'AG18966', 'anchor': '12345'},
        {'row': 10, 'col': 'NOTE APP', 'value': 'foo', 'anchor': '12345'},
    ]
    res = shared._route_to_db_primary(items, anchor_col='Eneltel',
                                       anchor_normalize='lstrip0')
    assert res['success'] is True
    assert res['written'] == 2
    assert res['primary_routed'] is True

    # Pending in stato 'pending' (non 'done'!)
    pending = db.list_pending_writes(state='pending')
    assert len(pending) == 2
    assert {p['column_name'] for p in pending} == {'Operatore', 'NOTE APP'}

    # Filone aggiornato (preview)
    row = db.get_filone_row('12345')
    assert row['operatore'] == 'AG18966'
    assert row['note_appuntamento'] == 'foo'


def test_route_primary_anchor_pod(db_isolato):
    db = db_isolato
    _setup_riga(db, eneltel='9', pod='IT002E0009999', sheets_row=20)
    from moduli import shared
    items = [
        {'row': 20, 'col': 'Operatore', 'value': 'X', 'anchor': 'IT002E0009999'},
    ]
    res = shared._route_to_db_primary(items, anchor_col='POD')
    assert res['success'] is True
    pending = db.list_pending_writes(state='pending')
    assert len(pending) == 1
    assert pending[0]['eneltel_key'] == '9'


def test_route_primary_pod_unresolved(db_isolato):
    """POD inesistente nel DB → success=False, niente pending creati."""
    db = db_isolato
    _setup_riga(db, eneltel='1', pod='IT001E0000001')
    from moduli import shared
    items = [
        {'row': 5, 'col': 'Esito', 'value': 'OK', 'anchor': 'POD_INESISTENTE'},
    ]
    res = shared._route_to_db_primary(items, anchor_col='POD')
    assert res['success'] is False
    assert res['written'] == 0
    assert len(db.list_pending_writes(state='pending')) == 0


def test_apply_pending_to_values(db_isolato):
    """_apply_pending_to_values sovrascrive le celle del raw values."""
    db = db_isolato
    _setup_riga(db, eneltel='42', sheets_row=2)

    # Crea una pending in volo
    db.record_local_change(
        '42', {'Operatore': 'NEW_VALUE'},
        origin='test', initial_state='pending',
    )

    from moduli import data_loader
    headers = ['Eneltel', 'NOMINATIVO', 'Operatore', 'NOTE APP']
    values = [
        headers,
        ['42', 'Mario', 'OLD_VALUE', ''],
        ['99', 'Anna',  'X',        'nota'],
    ]
    new_values = data_loader._apply_pending_to_values(values)
    # La riga di Mario deve avere il nuovo Operatore
    assert new_values[1][2] == 'NEW_VALUE'
    # La riga di Anna invariata
    assert new_values[2][2] == 'X'


def test_apply_pending_no_pending(db_isolato):
    """Senza pending il return deve essere identico all'input."""
    db = db_isolato  # noqa: F841 (necessario per init schema)
    from moduli import data_loader
    values = [
        ['Eneltel', 'NOMINATIVO'],
        ['1', 'A'],
    ]
    new_values = data_loader._apply_pending_to_values(values)
    assert new_values == values


def test_pending_case_insensitive(db_isolato):
    """Il client invia 'email' (minuscolo) ma il foglio ha 'EMAIL'.
    Il pending deve essere salvato col case del foglio, e
    _apply_pending_to_values deve sostituire la cella correttamente.
    """
    db = db_isolato
    conn = db.get_conn()
    # Simula il column_registry popolato da un pull (foglio ha 'EMAIL')
    db.update_column_registry(conn, ['Eneltel', 'NOMINATIVO', 'EMAIL'])
    # Setup riga
    _setup_riga(db, eneltel='42', sheets_row=2)

    # Il client invia 'email' (minuscolo) — record_local_change deve
    # canonicalizzare al case esatto del foglio.
    db.record_local_change(
        '42', {'email': 'mario@example.com'},
        origin='unit_test', initial_state='pending',
    )

    pending = db.list_pending_writes(state='pending')
    assert len(pending) == 1
    # Salvato col case esatto del foglio
    assert pending[0]['column_name'] == 'EMAIL'

    # _apply_pending_to_values deve sostituire la cella anche se case diverso
    from moduli import data_loader
    values = [
        ['Eneltel', 'NOMINATIVO', 'EMAIL'],
        ['42', 'Mario', 'old@example.com'],
    ]
    new_values = data_loader._apply_pending_to_values(values)
    assert new_values[1][2] == 'mario@example.com'


def test_calcola_nuova_nota_app():
    from moduli.shared import _calcola_nuova_nota_app
    # Nota vuota
    assert _calcola_nuova_nota_app('', '09:00') == 'APP 09:00'
    # Nota senza APP esistente
    assert _calcola_nuova_nota_app('cliente preferisce mattino', '09:00') \
        == 'APP 09:00 // cliente preferisce mattino'
    # Nota con APP da rimpiazzare
    assert _calcola_nuova_nota_app('APP 14:00 // cliente al lavoro', '09:00') \
        == 'APP 09:00 // cliente al lavoro'
    # Range orario
    assert _calcola_nuova_nota_app('APP 14:00-15:00 // foo', '10:00') \
        == 'APP 10:00 // foo'


def test_calcola_rimozione_app():
    from moduli.shared import _calcola_rimozione_app
    # Pin del comportamento originale (path Fase 1): la regex toglie il token
    # APP HH:MM ma non collassa i `//` adiacenti rimasti.
    assert _calcola_rimozione_app('') == ''
    assert _calcola_rimozione_app('APP 09:00') == ''
    assert _calcola_rimozione_app('APP 09:00 // foo bar') == 'foo bar'
    assert _calcola_rimozione_app('foo bar') == 'foo bar'  # niente APP
    # Quando APP è in mezzo, restano i due // adiacenti (comportamento originale)
    out = _calcola_rimozione_app('foo // APP 09:00 // bar')
    assert 'foo' in out and 'bar' in out
    assert 'APP' not in out


def test_route_primary_dedup_skips_unchanged(db_isolato):
    """Il client invia 9 campi ma 8 sono uguali al valore corrente: il
    backend deve saltare i duplicati e creare 1 sola pending.
    """
    db = db_isolato
    conn = db.get_conn()
    # Setup: registry + filone con valori esistenti
    db.update_column_registry(
        conn,
        ['Eneltel', 'NOMINATIVO', 'DATA PIAN', 'NOTE APP', 'EMAIL',
         'Operatore', 'Indirizzo', 'Num', 'telefono', 'Posizione_Ante_Mis'],
    )
    with conn:
        db.upsert_filone_row(
            conn, eneltel_key='42', sheets_row=10,
            typed_values={
                'nominativo': 'Mario',
                'data_pianificata': '02/05/2026',
                'note_appuntamento': 'foo',
                'email': 'old@x.com',
                'operatore': 'AE101392',
                'indirizzo': 'BRIGNOLE',
                'civico': '1',
                'telefono': '3388514851',
                'posizione_misuratore': '0 - Non conosciuta',
            },
            extra_values={},
            row_hash='h', source='bootstrap',
        )

    from moduli import shared
    items = [
        {'row': 10, 'col': 'NOMINATIVO', 'value': 'Mario', 'anchor': '42'},
        {'row': 10, 'col': 'DATA PIAN', 'value': '02/05/2026', 'anchor': '42'},
        {'row': 10, 'col': 'NOTE APP', 'value': 'foo', 'anchor': '42'},
        {'row': 10, 'col': 'EMAIL', 'value': 'new@x.com', 'anchor': '42'},  # unico modificato
        {'row': 10, 'col': 'Operatore', 'value': 'AE101392', 'anchor': '42'},
        {'row': 10, 'col': 'Indirizzo', 'value': 'BRIGNOLE', 'anchor': '42'},
        {'row': 10, 'col': 'Num', 'value': '1', 'anchor': '42'},
        {'row': 10, 'col': 'telefono', 'value': '3388514851', 'anchor': '42'},
        {'row': 10, 'col': 'Posizione_Ante_Mis', 'value': '0 - Non conosciuta', 'anchor': '42'},
    ]
    res = shared._route_to_db_primary(
        items, anchor_col='Eneltel', anchor_normalize='lstrip0',
    )
    assert res['written'] == 1
    assert res['unchanged'] == 8
    pending = db.list_pending_writes(state='pending')
    assert len(pending) == 1
    assert pending[0]['column_name'] == 'EMAIL'
    assert pending[0]['new_value'] == 'new@x.com'


def test_route_primary_duplicate_eneltel_lcl(db_isolato):
    """Due righe con stesso Eneltel ma LCL diverso → entrambe in DB.
    L'utente modifica via anchor=POD + anchor_lcl='LCL_B' → pending creata
    solo sulla riga giusta (LCL_B), non sulla primaria (LCL_A).
    """
    db = db_isolato
    conn = db.get_conn()
    db.update_column_registry(conn, ['Eneltel', 'LCL', 'EMAIL', 'POD'])
    with conn:
        db.upsert_filone_row(
            conn, eneltel_key='194202261', lcl='LCL_A', sheets_row=248,
            typed_values={'pod': 'IT001E19420657', 'email': 'a@x.com'},
            extra_values={}, row_hash='h_a', source='bootstrap',
        )
        db.upsert_filone_row(
            conn, eneltel_key='194202261', lcl='LCL_B', sheets_row=2132,
            typed_values={'pod': 'IT001E19420657', 'email': 'b@x.com'},
            extra_values={}, row_hash='h_b', source='bootstrap',
        )

    from moduli import shared
    items = [
        {'row': 2132, 'col': 'EMAIL', 'value': 'new_b@x.com',
         'anchor': 'IT001E19420657', 'anchor_lcl': 'LCL_B'},
    ]
    res = shared._route_to_db_primary(items, anchor_col='POD', anchor_normalize='upper')
    assert res['written'] == 1
    # Pending creata sulla (eneltel=194202261, lcl=LCL_B)
    pending = db.list_pending_writes(state='pending')
    assert len(pending) == 1
    p = pending[0]
    assert p['eneltel_key'] == '194202261'
    assert p['lcl'] == 'LCL_B'
    assert p['new_value'] == 'new_b@x.com'

    # La riga primaria (LCL_A) NON deve essere stata toccata
    row_a = conn.execute(
        "SELECT email FROM filone WHERE eneltel_key=? AND lcl=?",
        ('194202261', 'LCL_A'),
    ).fetchone()
    assert row_a['email'] == 'a@x.com'

    # La riga duplicata (LCL_B) deve avere preview ottimistica aggiornata
    row_b = conn.execute(
        "SELECT email FROM filone WHERE eneltel_key=? AND lcl=?",
        ('194202261', 'LCL_B'),
    ).fetchone()
    assert row_b['email'] == 'new_b@x.com'


def test_route_primary_anchor_eneltel_dedup_with_lcl(db_isolato):
    """Caso reale: modale mappa chiama PUT /api/contatore/<eneltel>, che
    passa anchor=Eneltel e NO anchor_lcl. Il DB ha la riga con LCL='X'.
    Il dedup deve confrontare contro i valori veri della riga in DB
    (non con stato vuoto). Senza il fix, il dedup vedeva tutto come ''
    e mancava tutti i match.
    """
    db = db_isolato
    conn = db.get_conn()
    db.update_column_registry(conn, ['Eneltel', 'LCL', 'NOMINATIVO', 'EMAIL', 'Operatore'])
    with conn:
        db.upsert_filone_row(
            conn, eneltel_key='16840441', lcl='6600101316', sheets_row=17306,
            typed_values={
                'nominativo': 'EDJ PAVANEL',
                'operatore': 'AG49808',
                'email': '',
            },
            extra_values={}, row_hash='h', source='bootstrap',
        )

    from moduli import shared
    # Simula: client manda 4 campi, di cui 3 uguali al DB
    items = [
        {'row': 17306, 'col': 'NOMINATIVO', 'value': 'EDJ PAVANEL', 'anchor': '16840441'},
        {'row': 17306, 'col': 'Operatore',  'value': 'AG49808',    'anchor': '16840441'},
        {'row': 17306, 'col': 'EMAIL',      'value': '',           'anchor': '16840441'},
        {'row': 17306, 'col': 'EMAIL',      'value': 'new@x.com',  'anchor': '16840441'},
    ]
    # NB: 4° è duplicato chiave per simulare; in pratica c'è una sola modifica.
    # Usiamo solo i primi 3 + l'ultimo che è il "vero cambio".
    items = items[:3] + [items[3]]  # tutti e 4; dedup dovrebbe prendere 3 e scriverne 1
    res = shared._route_to_db_primary(items, anchor_col='Eneltel', anchor_normalize='lstrip0')
    # 3 uguali al DB + 1 diverso (EMAIL='new@x.com' vs DB.'' poi diventa 'new...')
    assert res['unchanged'] >= 3
    assert res['written'] == 1


def test_route_primary_anchor_eneltel_send_empty_value(db_isolato):
    """Bug fix #2: l'utente cancella un campo (manda '' al backend). Il DB
    ha il valore vecchio. Dedup deve NON dichiarare match (cur != ''), e
    creare la pending per scrivere '' su Sheets.
    """
    db = db_isolato
    conn = db.get_conn()
    db.update_column_registry(conn, ['Eneltel', 'LCL', 'NOTE APP'])
    with conn:
        db.upsert_filone_row(
            conn, eneltel_key='16840441', lcl='6600101316', sheets_row=17306,
            typed_values={'note_appuntamento': 'testo precedente'},
            extra_values={}, row_hash='h', source='bootstrap',
        )

    from moduli import shared
    items = [
        {'row': 17306, 'col': 'NOTE APP', 'value': '', 'anchor': '16840441'},
    ]
    res = shared._route_to_db_primary(items, anchor_col='Eneltel', anchor_normalize='lstrip0')
    # Confronto: DB ha 'testo precedente', nuovo '' → DIVERSO → write
    assert res['written'] == 1
    pending = db.list_pending_writes(state='pending')
    assert len(pending) == 1
    assert (pending[0]['new_value'] or '') == ''
    # E deve essere associata alla riga giusta (LCL del DB)
    assert pending[0]['lcl'] == '6600101316'


def test_route_primary_lcl_fallback_to_primary(db_isolato):
    """Bug fix: il client manda anchor_lcl='' (non sa l'LCL della riga, es.
    modale contatore singolo), ma la riga in DB ha LCL valorizzato. Il
    backend deve fallback alla prima riga (legacy), NON quarantinare.
    """
    db = db_isolato
    conn = db.get_conn()
    db.update_column_registry(conn, ['Eneltel', 'LCL', 'EMAIL'])
    with conn:
        db.upsert_filone_row(
            conn, eneltel_key='16840441', lcl='6600101316', sheets_row=17306,
            typed_values={'email': 'old@x.com'},
            extra_values={}, row_hash='h', source='bootstrap',
        )

    from moduli import shared
    # Item con anchor_lcl='' (caso reale: modale contatore non manda LCL)
    items = [
        {'row': 17306, 'col': 'EMAIL', 'value': 'new@x.com',
         'anchor': '16840441', 'anchor_lcl': ''},
    ]
    res = shared._route_to_db_primary(items, anchor_col='Eneltel', anchor_normalize='lstrip0')
    assert res['written'] == 1
    pending = db.list_pending_writes(state='pending')
    assert len(pending) == 1
    # La pending deve avere il LCL della riga reale, non ''
    assert pending[0]['lcl'] == '6600101316'


def test_route_primary_no_lcl_takes_first(db_isolato):
    """Se non c'è anchor_lcl, prende la prima riga (legacy)."""
    db = db_isolato
    conn = db.get_conn()
    db.update_column_registry(conn, ['Eneltel', 'EMAIL'])
    with conn:
        db.upsert_filone_row(
            conn, eneltel_key='999', sheets_row=42,
            typed_values={'email': 'old@x.com'},
            extra_values={}, row_hash='h', source='bootstrap',
        )

    from moduli import shared
    res = shared._route_to_db_primary(
        [{'row': 42, 'col': 'EMAIL', 'value': 'new@x.com', 'anchor': '999'}],
        anchor_col='Eneltel', anchor_normalize='lstrip0',
    )
    assert res['written'] == 1
    pending = db.list_pending_writes(state='pending')
    assert len(pending) == 1


def test_route_primary_dedup_uses_pending_overlay(db_isolato):
    """Se l'utente fa due edit rapidi sulla stessa cella, la SECONDA edit
    con lo stesso valore della pending in volo non crea nuova pending."""
    db = db_isolato
    conn = db.get_conn()
    db.update_column_registry(conn, ['Eneltel', 'EMAIL'])
    with conn:
        db.upsert_filone_row(
            conn, eneltel_key='1', sheets_row=2,
            typed_values={'email': 'old@x.com'},
            extra_values={}, row_hash='h', source='bootstrap',
        )

    from moduli import shared
    # Prima edit: crea pending
    res1 = shared._route_to_db_primary(
        [{'row': 2, 'col': 'EMAIL', 'value': 'new@x.com', 'anchor': '1'}],
        anchor_col='Eneltel', anchor_normalize='lstrip0',
    )
    assert res1['written'] == 1
    assert len(db.list_pending_writes(state='pending')) == 1

    # Seconda edit: stesso valore della pending in volo → no-op
    res2 = shared._route_to_db_primary(
        [{'row': 2, 'col': 'EMAIL', 'value': 'new@x.com', 'anchor': '1'}],
        anchor_col='Eneltel', anchor_normalize='lstrip0',
    )
    assert res2['written'] == 0
    assert res2['unchanged'] == 1
    assert len(db.list_pending_writes(state='pending')) == 1


def test_route_primary_skipped_invalid_row(db_isolato):
    """row<2 (riga header o invalida) viene saltata senza errore."""
    db = db_isolato
    _setup_riga(db, eneltel='1', sheets_row=10)
    from moduli import shared
    items = [
        {'row': 1, 'col': 'X', 'value': 'v', 'anchor': '1'},  # invalida
        {'row': 10, 'col': 'Operatore', 'value': 'OK', 'anchor': '1'},
    ]
    res = shared._route_to_db_primary(items, anchor_col='Eneltel',
                                       anchor_normalize='lstrip0')
    assert res['written'] == 1
    assert res['skipped'] == 1


def test_safe_batch_update_routes_to_primary_in_primary_mode(db_isolato, monkeypatch):
    """In USE_DB_BACKEND=primary, safe_batch_update_by_name instrada al DB
    e NON tocca shared.get_worksheet (che fallirebbe senza Sheets)."""
    db = db_isolato
    _setup_riga(db, eneltel='1', sheets_row=10)

    import config
    monkeypatch.setattr(config, 'USE_DB_BACKEND', 'primary')

    from moduli import shared
    res = shared.safe_batch_update_by_name(
        [{'row': 10, 'col': 'Operatore', 'value': 'V', 'anchor': '1'}],
        anchor_col='Eneltel', anchor_normalize='lstrip0',
    )
    assert res.get('primary_routed') is True
    assert res['success'] is True
    assert res['written'] == 1

    pending = db.list_pending_writes(state='pending')
    assert len(pending) == 1
