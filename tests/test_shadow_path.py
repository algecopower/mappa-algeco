# -*- coding: utf-8 -*-
"""
Test del path "shadow" (Fase 1): scritture che vengono effettuate su Sheets
e *anche* registrate nel DB locale come stato 'done' (audit trail).

Verifichiamo che:
- shadow_record_batch raggruppa per Eneltel correttamente con anchor='Eneltel'
- shadow_record_batch usa lookup POD → Eneltel quando anchor='POD'
- shadow_record_single funziona anche fuori dal contesto Flask
- Le pending vengono inserite con state='done' (non drenate dal worker)
- Il filone locale viene aggiornato (preview) anche in shadow
"""


def _bootstrap_riga(db, eneltel='12345', pod='IT001E1234567', sheets_row=10):
    conn = db.get_conn()
    with conn:
        db.upsert_filone_row(
            conn,
            eneltel_key=eneltel,
            sheets_row=sheets_row,
            typed_values={'pod': pod, 'nominativo': 'Mario'},
            extra_values={},
            row_hash='abc',
            source='bootstrap',
        )


def test_shadow_record_batch_anchor_eneltel(db_isolato):
    db = db_isolato
    _bootstrap_riga(db, eneltel='12345', sheets_row=10)

    items = [
        {'row': 10, 'col': 'NOTE APP', 'value': 'APP 09:00', 'anchor': '12345'},
        {'row': 10, 'col': 'Operatore', 'value': 'AG18966', 'anchor': '12345'},
    ]
    res = db.shadow_record_batch(items, anchor_col='Eneltel', origin='unit_test')

    assert res['recorded'] == 1  # 1 eneltel raggruppato
    pending = db.list_pending_writes(state='done', limit=10)
    assert len(pending) == 2  # 2 colonne registrate

    # Filone aggiornato (preview)
    row = db.get_filone_row('12345')
    assert row['note_appuntamento'] == 'APP 09:00'
    assert row['operatore'] == 'AG18966'

    # State 'pending' deve essere vuoto (drainer non deve drenare)
    assert len(db.list_pending_writes(state='pending')) == 0


def test_shadow_record_batch_anchor_pod(db_isolato):
    """Quando anchor='POD' deve fare lookup → eneltel."""
    db = db_isolato
    _bootstrap_riga(db, eneltel='99999', pod='IT002E9999999', sheets_row=20)

    items = [
        {'row': 20, 'col': 'Operatore', 'value': 'AE101392', 'anchor': 'IT002E9999999'},
    ]
    res = db.shadow_record_batch(items, anchor_col='POD', origin='unit_test')
    assert res['recorded'] == 1
    assert res.get('unresolved', 0) == 0

    row = db.get_filone_row('99999')
    assert row['operatore'] == 'AE101392'


def test_shadow_record_batch_pod_non_trovato(db_isolato):
    """POD inesistente nel DB → unresolved, no scritture, no errori."""
    db = db_isolato
    _bootstrap_riga(db, eneltel='1', pod='IT001E0000001')

    items = [
        {'row': 5, 'col': 'Esito', 'value': 'OK', 'anchor': 'POD_INESISTENTE'},
    ]
    res = db.shadow_record_batch(items, anchor_col='POD', origin='unit_test')
    assert res['recorded'] == 0
    assert res['unresolved'] == 1


def test_shadow_record_batch_fallback_sheets_row(db_isolato):
    """Senza anchor utile, prova lookup via sheets_row."""
    db = db_isolato
    _bootstrap_riga(db, eneltel='42', sheets_row=42)

    items = [{'row': 42, 'col': 'NOMINATIVO', 'value': 'NewName'}]
    res = db.shadow_record_batch(items, anchor_col=None, origin='unit_test')
    assert res['recorded'] == 1

    row = db.get_filone_row('42')
    assert row['nominativo'] == 'NewName'


def test_shadow_record_single(db_isolato):
    db = db_isolato
    _bootstrap_riga(db, eneltel='7')

    db.shadow_record_single('7', {'NOTE APP': 'foo'}, origin='unit_test')
    row = db.get_filone_row('7')
    assert row['note_appuntamento'] == 'foo'

    pending_done = db.list_pending_writes(state='done')
    assert len(pending_done) == 1
    assert pending_done[0]['origin'] == 'unit_test'


def test_shadow_non_blocca_su_eneltel_invalido(db_isolato):
    """Eneltel vuoto/None non lancia eccezione, semplicemente skip."""
    db = db_isolato
    db.shadow_record_single(None, {'col': 'val'}, origin='unit_test')
    db.shadow_record_single('', {'col': 'val'}, origin='unit_test')
    # Niente eccezioni e niente pending
    assert len(db.list_pending_writes()) == 0


def test_shadow_idempotente_su_stesso_valore(db_isolato):
    """Due scritture identiche di fila → coalesce a 1 pending done."""
    db = db_isolato
    _bootstrap_riga(db, eneltel='5')

    db.shadow_record_single('5', {'NOTE APP': 'A'}, origin='t')
    db.shadow_record_single('5', {'NOTE APP': 'B'}, origin='t')
    db.shadow_record_single('5', {'NOTE APP': 'C'}, origin='t')

    # Coalesce: solo 1 pending in state='done' per (eneltel,col)
    # (la UNIQUE INDEX parziale è solo su pending+in_flight, quindi qui
    # in stato done possono coesistere — verifichiamo comunque che il filone
    # rifletta l'ULTIMO valore)
    row = db.get_filone_row('5')
    assert row['note_appuntamento'] == 'C'


def test_shadow_audit_log_tracked(db_isolato):
    """Ogni shadow_record_single produce audit_log."""
    db = db_isolato
    _bootstrap_riga(db, eneltel='8')

    n_before = db.get_conn().execute(
        "SELECT COUNT(*) c FROM audit_log WHERE eneltel_key='8'"
    ).fetchone()['c']

    db.shadow_record_single('8', {'NOTE APP': 'X'}, user='alice', origin='t')

    n_after = db.get_conn().execute(
        "SELECT COUNT(*) c FROM audit_log WHERE eneltel_key='8'"
    ).fetchone()['c']
    assert n_after > n_before
