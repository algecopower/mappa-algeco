# -*- coding: utf-8 -*-
"""
Test della conflict resolution durante il pull.

Scenari coperti (policy: local_wins):
1. locale-only → pending applicato, remote invariato
2. remote-only → remote applicato al DB
3. both-different-field → entrambi i cambi convivono (merge campo-per-campo)
4. both-same-field, base==remote → no conflict, pending procede
5. both-same-field, base!=remote → CONFLITTO, audit log, locale vince
"""


def _headers():
    return ['Eneltel', 'NOMINATIVO', 'DATA PIAN', 'Operatore', 'NOTE APP', 'POD']


def _row(eneltel, nom='Mario', data='2026-04-29', op='AG18966', note='', pod='POD1'):
    return [eneltel, nom, data, op, note, pod]


def test_scenario_locale_only(sync_engine_isolato, db_isolato):
    """Solo cambio locale: il pull non applica nulla, pending resta in volo."""
    se = sync_engine_isolato
    db = db_isolato

    se.pull_once(values=[_headers(), _row('1', op='AA11111')])
    db.record_local_change('1', {'Operatore': 'AA22222'}, origin='unit_test')

    # Pull con foglio invariato
    se.pull_once(values=[_headers(), _row('1', op='AA11111')])

    pending = db.list_pending_writes()
    assert len(pending) == 1
    assert pending[0]['new_value'] == 'AA22222'

    # filone[operatore] è 'AA22222' (preview ottimistica scritta da record_local_change)
    row = db.get_filone_row('1')
    assert row['operatore'] == 'AA22222'


def test_scenario_remote_only(sync_engine_isolato, db_isolato):
    """Solo cambio remoto: viene applicato al DB."""
    se = sync_engine_isolato
    db = db_isolato

    se.pull_once(values=[_headers(), _row('2', op='AA11111')])
    se.pull_once(values=[_headers(), _row('2', op='AC33333')])

    row = db.get_filone_row('2')
    assert row['operatore'] == 'AC33333'


def test_scenario_both_different_field(sync_engine_isolato, db_isolato):
    """
    Locale modifica colonna A, Sheets modifica colonna B → entrambi i
    cambi convivono. NESSUN conflitto.
    """
    se = sync_engine_isolato
    db = db_isolato

    # Stato iniziale comune
    se.pull_once(values=[_headers(), _row('3', op='AB11111', note='NOTEold')])

    # Locale modifica Operatore
    db.record_local_change('3', {'Operatore': 'AB22222'}, origin='unit_test')

    # Sheets modifica NOTE APP (Operatore invariato)
    se.pull_once(values=[_headers(), _row('3', op='AB11111', note='NOTEnew')])

    row = db.get_filone_row('3')
    # Note remote → applicato
    assert row['note_appuntamento'] == 'NOTEnew'
    # Operatore: pending in volo, preview ottimistica = OPnew
    assert row['operatore'] == 'AB22222'

    # Conflitti: zero
    n_conflicts = db.get_conn().execute(
        "SELECT COUNT(*) c FROM audit_log WHERE source='conflict_local_wins'"
    ).fetchone()['c']
    assert n_conflicts == 0

    # Pending ancora in volo (pronta per il drainer)
    pending = db.list_pending_writes()
    assert len(pending) == 1
    assert pending[0]['column_name'] == 'Operatore'
    assert pending[0]['new_value'] == 'AB22222'


def test_scenario_both_same_field_base_uguale_remote(sync_engine_isolato, db_isolato):
    """
    Locale modifica X (base=OLD, new=NEW). Remoto è ancora OLD → no conflict.
    """
    se = sync_engine_isolato
    db = db_isolato

    se.pull_once(values=[_headers(), _row('4', op='AA11111')])
    db.record_local_change('4', {'Operatore': 'AA22222'}, origin='unit_test')
    # Remoto invariato
    se.pull_once(values=[_headers(), _row('4', op='AA11111')])

    n_conflicts = db.get_conn().execute(
        "SELECT COUNT(*) c FROM audit_log WHERE source='conflict_local_wins'"
    ).fetchone()['c']
    assert n_conflicts == 0


def test_scenario_both_same_field_conflict_local_wins(sync_engine_isolato, db_isolato):
    """
    Locale modifica X (base=OLD, new=LOCAL).
    Sheets modifica X a REMOTE (qualcuno l'ha cambiato manualmente).
    Pull rileva conflitto: audit log scritto, DB NON aggiornato (resta LOCAL).
    """
    se = sync_engine_isolato
    db = db_isolato

    # Stato iniziale: OLD
    se.pull_once(values=[_headers(), _row('5', op='AA11111')])

    # Locale modifica → LOCAL (base=OLD)
    db.record_local_change('5', {'Operatore': 'LOCAL'}, origin='unit_test')

    # Sheets modificato esternamente → REMOTE
    res = se.pull_once(values=[_headers(), _row('5', op='REMOTE')])

    assert res['conflicts'] == 1

    # filone[operatore] resta LOCAL (preview ottimistica, sarà confermato dal drain)
    row = db.get_filone_row('5')
    assert row['operatore'] == 'LOCAL'

    # Audit log con dettaglio conflitto
    audit = db.get_conn().execute(
        "SELECT * FROM audit_log WHERE source='conflict_local_wins'"
    ).fetchone()
    assert audit is not None
    assert audit['eneltel_key'] == '5'
    assert audit['column_name'] == 'Operatore'
    assert audit['old_value'] == 'AA11111'      # base_value
    assert audit['new_value'] == 'REMOTE'   # remote_val al momento del pull

    # La pending è ancora in volo (sarà drenata e sovrascriverà REMOTE su Sheets)
    pending = db.list_pending_writes()
    assert len(pending) == 1
    assert pending[0]['new_value'] == 'LOCAL'


def test_pending_in_flight_blocca_remote_pull(sync_engine_isolato, db_isolato):
    """
    Pending in stato in_flight (drainer sta scrivendo). Il pull NON deve
    sovrascrivere quella cella anche se remoto sembra invariato vs base —
    aspettiamo l'esito del drain.
    """
    se = sync_engine_isolato
    db = db_isolato

    se.pull_once(values=[_headers(), _row('6', op='AA11111')])
    db.record_local_change('6', {'Operatore': 'AA22222'}, origin='unit_test')

    pending_id = db.list_pending_writes()[0]['id']
    db.mark_pending_in_flight([pending_id])

    # Pull con remote invariato a OLD: il pull NON deve scrivere OLD
    # sul DB (preview ottimistica = NEW deve restare).
    se.pull_once(values=[_headers(), _row('6', op='AA11111')])

    row = db.get_filone_row('6')
    assert row['operatore'] == 'AA22222'
