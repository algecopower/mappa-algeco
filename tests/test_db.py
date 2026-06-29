# -*- coding: utf-8 -*-
"""
Test unitari per moduli/db.py.

Coperti:
- Bootstrap schema idempotente
- Coalesce di pending_writes duplicate (5 modifiche stesso campo → 1 sola pending)
- Quarantena pending
- Upsert filone + filone_extra
- Mark orphans
- Sync state get/set
"""


def test_bootstrap_schema_idempotente(db_isolato):
    """Eseguire il bootstrap due volte non rompe nulla."""
    db = db_isolato
    s1 = db.stats()
    db._db_initialized_paths.clear()
    db.get_conn()  # ri-esegue _bootstrap_schema
    s2 = db.stats()
    assert s1 == s2
    assert s1['schema_version'] == '6'


def test_migrazione_v3_rinomina_colonne_installato(db_isolato):
    """La v3 mantiene i valori e rimuove gli header storici."""
    db = db_isolato
    conn = db.get_conn()
    with conn:
        db.upsert_filone_row(
            conn,
            eneltel_key='42',
            sheets_row=2,
            typed_values={
                'lcl': 'A',
                'matricola_installata': 'VALORE-STORICO',
                'codice_installato': 'CODICE-STORICO',
            },
            extra_values={},
            row_hash='r1',
            source='bootstrap',
        )
        conn.execute(
            '''
            INSERT OR REPLACE INTO filone_extra(
                eneltel_key, lcl, column_name, value
            ) VALUES ('42', 'A', 'Cod. Installato', 'VALORE-ATTUALE')
            '''
        )
        conn.execute(
            "DELETE FROM column_registry WHERE column_name IN "
            "('Cod. Installato', 'Matr. Installato')"
        )
        conn.execute(
            '''
            INSERT INTO column_registry(
                column_name, chiave_interna, last_index, last_seen_ts
            ) VALUES
                ('Matricola CE inst', 'matricola_installata', 23, 100),
                ('cod.', 'codice_installato', 24, 100)
            '''
        )
        conn.execute(
            "UPDATE sync_state SET value='2' WHERE key='schema_version'"
        )

    db.close_conn()
    db._db_initialized_paths.clear()
    conn = db.get_conn()

    row = db.get_filone_row('42', lcl='A')
    assert row['matricola_installata'] == 'VALORE-ATTUALE'
    assert row['codice_installato'] == 'CODICE-STORICO'
    registry = {
        r['column_name']: r['chiave_interna']
        for r in conn.execute(
            "SELECT column_name, chiave_interna FROM column_registry"
        )
    }
    assert registry['Cod. Installato'] == 'matricola_installata'
    assert registry['Matr. Installato'] == 'codice_installato'
    assert 'Matricola CE inst' not in registry
    assert 'cod.' not in registry
    assert conn.execute(
        "SELECT COUNT(*) FROM filone_extra WHERE column_name IN "
        "('Cod. Installato', 'Matr. Installato')"
    ).fetchone()[0] == 0


def test_record_local_change_coalesce_duplicates(db_isolato):
    """5 modifiche allo stesso campo → 1 sola pending, 5 audit + 4 replace."""
    db = db_isolato
    eneltel = '12345'

    # Riga base nel filone (simula popolata da pull)
    conn = db.get_conn()
    with conn:
        db.upsert_filone_row(
            conn,
            eneltel_key=eneltel,
            sheets_row=10,
            typed_values={'note_appuntamento': 'iniziale'},
            extra_values={},
            row_hash='abc',
            source='bootstrap',
        )

    for v in ['v1', 'v2', 'v3', 'v4', 'v5']:
        db.record_local_change(
            eneltel, {'NOTE APP': v}, user='tester', origin='unit_test',
        )

    pending = db.list_pending_writes(state='pending', limit=100)
    assert len(pending) == 1, f"Atteso 1 pending coalesced, trovate {len(pending)}"
    assert pending[0]['new_value'] == 'v5'

    # Audit deve avere tracciato tutte le 5 transizioni
    audit_rows = conn.execute(
        "SELECT * FROM audit_log WHERE eneltel_key=? AND source='local'",
        (eneltel,)
    ).fetchall()
    # 5 inserimenti audit (uno per ogni record_local_change) + 4 replaced
    assert len(audit_rows) >= 5


def test_pending_state_iniziale_done(db_isolato):
    """initial_state='done' (Fase 1 shadow): pending non viene drenata."""
    db = db_isolato
    conn = db.get_conn()
    with conn:
        db.upsert_filone_row(
            conn, eneltel_key='999', sheets_row=2,
            typed_values={}, extra_values={},
            row_hash='x', source='bootstrap',
        )

    db.record_local_change(
        '999', {'NOTE APP': 'foo'}, origin='shadow', initial_state='done',
    )
    pending = db.list_pending_writes(state='pending', limit=100)
    assert len(pending) == 0
    done = conn.execute(
        "SELECT COUNT(*) c FROM pending_writes WHERE state='done'"
    ).fetchone()['c']
    assert done == 1


def test_quarantine_pending(db_isolato):
    """quarantine_pending sposta lo stato e logga audit."""
    db = db_isolato
    conn = db.get_conn()
    with conn:
        db.upsert_filone_row(
            conn, eneltel_key='5', sheets_row=2,
            typed_values={}, extra_values={},
            row_hash='x', source='bootstrap',
        )
    db.record_local_change('5', {'NOTE APP': 'x'}, origin='t')

    pending_id = db.list_pending_writes()[0]['id']
    db.quarantine_pending([pending_id], reason='anchor_lost')

    after = conn.execute(
        'SELECT state, last_error FROM pending_writes WHERE id=?', (pending_id,)
    ).fetchone()
    assert after['state'] == 'quarantined'
    assert 'anchor_lost' in (after['last_error'] or '')

    audit = conn.execute(
        "SELECT * FROM audit_log WHERE source='quarantine'"
    ).fetchall()
    assert len(audit) == 1


def test_mark_orphans(db_isolato):
    """Righe non viste nell'ultimo pull vengono marcate is_orphan=1."""
    db = db_isolato
    conn = db.get_conn()
    with conn:
        for ek in ['1', '2', '3']:
            db.upsert_filone_row(
                conn, eneltel_key=ek, sheets_row=int(ek)+1,
                typed_values={}, extra_values={},
                row_hash='h'+ek, source='bootstrap',
            )

    with conn:
        # mark_orphans v2: keys = set di tuple (eneltel_key, lcl)
        n = db.mark_orphans(conn, alive_keys={('1', ''), ('3', '')})

    assert n == 1
    orphan = conn.execute(
        'SELECT eneltel_key FROM filone WHERE is_orphan=1'
    ).fetchall()
    assert len(orphan) == 1
    assert orphan[0]['eneltel_key'] == '2'


def test_sync_state_kv(db_isolato):
    db = db_isolato
    assert db.sync_state_get('foo') is None
    db.sync_state_set('foo', 'bar')
    assert db.sync_state_get('foo') == 'bar'
    db.sync_state_set('foo', 'baz')
    assert db.sync_state_get('foo') == 'baz'


def test_normalize_eneltel(db_isolato):
    db = db_isolato
    assert db.normalize_eneltel('00012345') == '12345'
    assert db.normalize_eneltel('  12345  ') == '12345'
    assert db.normalize_eneltel(None) == ''
    assert db.normalize_eneltel('') == ''


def test_upsert_filone_with_extra(db_isolato):
    """Colonne note vanno in tipizzato, extra vanno in filone_extra."""
    db = db_isolato
    conn = db.get_conn()
    with conn:
        db.upsert_filone_row(
            conn, eneltel_key='42', sheets_row=2,
            typed_values={'pod': 'IT001E1234567', 'nominativo': 'Mario'},
            extra_values={'COLONNA_STRANA': 'valore_extra'},
            row_hash='h', source='remote',
        )

    row = db.get_filone_row('42')
    assert row['pod'] == 'IT001E1234567'
    assert row['nominativo'] == 'Mario'
    assert row['is_orphan'] == 0

    extra = conn.execute(
        'SELECT * FROM filone_extra WHERE eneltel_key=?', ('42',)
    ).fetchone()
    assert extra['column_name'] == 'COLONNA_STRANA'
    assert extra['value'] == 'valore_extra'


def test_compute_row_hash_stabile(db_isolato):
    """Stesso input → stesso hash. Input diverso → hash diverso."""
    db = db_isolato
    h1 = db.compute_row_hash(['a', 'b', 'c'])
    h2 = db.compute_row_hash(['a', 'b', 'c'])
    h3 = db.compute_row_hash(['a', 'b', 'd'])
    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 32


def test_recovery_in_flight_pending_a_startup(db_isolato):
    """Pending in_flight rimaste a startup vengono riportate a 'pending'."""
    db = db_isolato
    conn = db.get_conn()
    with conn:
        db.upsert_filone_row(
            conn, eneltel_key='7', sheets_row=2,
            typed_values={}, extra_values={},
            row_hash='h', source='bootstrap',
        )
    db.record_local_change('7', {'NOTE APP': 'v'}, origin='t')
    pending_id = db.list_pending_writes()[0]['id']
    db.mark_pending_in_flight([pending_id])

    # Simula riavvio: chiudi la connessione e ri-bootstrap
    db.close_conn()
    db._db_initialized_paths.clear()
    db.get_conn()  # rileva in_flight orfana e la riporta a pending

    pending = db.list_pending_writes(state='pending')
    assert len(pending) == 1
    assert pending[0]['id'] == pending_id
