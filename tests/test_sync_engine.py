# -*- coding: utf-8 -*-
"""
Test del pull engine (Sheets → DB).

Niente chiamate reali a Google Sheets: passiamo `values=...` direttamente
a `pull_once()`, che è una API pensata anche per testabilità.
"""


def _values_riga_singola(eneltel='12345', nominativo='Mario',
                         data_pian='2026-04-29', operatore='AG18966',
                         note=''):
    """Costruisce un (headers, body) minimale ma realistico."""
    headers = ['Eneltel', 'NOMINATIVO', 'DATA PIAN', 'Operatore', 'NOTE APP', 'POD']
    row = [eneltel, nominativo, data_pian, operatore, note, 'IT001E1234567']
    return [headers, row]


def test_pull_riga_nuova(sync_engine_isolato, db_isolato):
    """Pull su DB vuoto → riga inserita."""
    se = sync_engine_isolato
    db = db_isolato

    res = se.pull_once(values=_values_riga_singola())
    assert res['new'] == 1
    assert res['changed'] == 0
    assert res['conflicts'] == 0

    row = db.get_filone_row('12345')
    assert row is not None
    assert row['nominativo'] == 'Mario'
    assert row['operatore'] == 'AG18966'
    assert row['data_pianificata'] == '2026-04-29'
    assert row['sheets_row'] == 2
    assert row['is_orphan'] == 0


def test_pull_idempotente(sync_engine_isolato, db_isolato):
    """Stesso pull 3 volte → 1 nuova riga + 2 no-op."""
    se = sync_engine_isolato
    values = _values_riga_singola()

    r1 = se.pull_once(values=values)
    r2 = se.pull_once(values=values)
    r3 = se.pull_once(values=values)

    assert r1['new'] == 1
    assert r2.get('skipped_global_hash') is True
    assert r3.get('skipped_global_hash') is True


def test_pull_cambio_remoto_puro(sync_engine_isolato, db_isolato):
    """Riga già presente, valore remoto cambiato senza pending → applica."""
    se = sync_engine_isolato
    db = db_isolato

    se.pull_once(values=_values_riga_singola(operatore='AG18966'))
    se.pull_once(values=_values_riga_singola(operatore='AE101392'))

    row = db.get_filone_row('12345')
    assert row['operatore'] == 'AE101392'

    audit_row = db.get_conn().execute(
        "SELECT * FROM audit_log WHERE source='remote' AND column_name='Operatore'"
    ).fetchone()
    assert audit_row is not None
    assert audit_row['old_value'] == 'AG18966'
    assert audit_row['new_value'] == 'AE101392'


def test_pull_marca_orphan_riga_sparita(sync_engine_isolato, db_isolato):
    """Riga in DB ma non nel pull successivo → is_orphan=1."""
    se = sync_engine_isolato
    db = db_isolato

    headers = ['Eneltel', 'NOMINATIVO', 'DATA PIAN', 'Operatore', 'NOTE APP', 'POD']
    se.pull_once(values=[headers,
                          ['111', 'A', '2026-01-01', 'X', '', 'P1'],
                          ['222', 'B', '2026-01-02', 'Y', '', 'P2']])

    # Nuovo pull senza la riga 222
    se.pull_once(values=[headers,
                          ['111', 'A', '2026-01-01', 'X', '', 'P1']])

    row222 = db.get_filone_row('222')
    assert row222['is_orphan'] == 1


def test_pull_gestisce_eneltel_duplicato(sync_engine_isolato, db_isolato):
    """Due righe con stesso Eneltel → la seconda finisce in filone_anomalies."""
    se = sync_engine_isolato
    db = db_isolato
    headers = ['Eneltel', 'NOMINATIVO', 'DATA PIAN', 'Operatore', 'NOTE APP', 'POD']
    res = se.pull_once(values=[headers,
                                ['77', 'Primo', '2026-01-01', '', '', ''],
                                ['77', 'Secondo', '2026-01-02', '', '', '']])
    assert res['duplicates'] == 1
    anomalie = db.get_conn().execute(
        "SELECT * FROM filone_anomalies WHERE kind='duplicate_eneltel'"
    ).fetchall()
    assert len(anomalie) == 1


def test_pull_riga_senza_eneltel(sync_engine_isolato, db_isolato):
    """Riga senza Eneltel ma con altri valori → anomalia, ma non blocca."""
    se = sync_engine_isolato
    db = db_isolato
    headers = ['Eneltel', 'NOMINATIVO', 'DATA PIAN', 'Operatore', 'NOTE APP', 'POD']
    res = se.pull_once(values=[headers,
                                ['', 'Senza ENELTEL', '2026-01-01', '', '', '']])
    assert res['new'] == 0
    anomalie = db.get_conn().execute(
        "SELECT * FROM filone_anomalies WHERE kind='row_without_eneltel'"
    ).fetchall()
    assert len(anomalie) == 1


def test_pull_aggiorna_column_registry(sync_engine_isolato, db_isolato):
    """Dopo il pull, column_registry ha tutti gli header con la chiave_interna."""
    se = sync_engine_isolato
    db = db_isolato
    se.pull_once(values=_values_riga_singola())

    rows = {r['column_name']: r for r in db.get_conn().execute(
        'SELECT * FROM column_registry'
    ).fetchall()}
    assert 'Eneltel' in rows
    assert rows['Eneltel']['chiave_interna'] == 'eneltel'
    assert rows['NOMINATIVO']['chiave_interna'] == 'nominativo'
    assert rows['DATA PIAN']['chiave_interna'] == 'data_pianificata'
    assert rows['Operatore']['chiave_interna'] == 'operatore'


def test_pull_colonna_extra_finisce_in_filone_extra(sync_engine_isolato, db_isolato):
    """Colonne non note vanno in filone_extra."""
    se = sync_engine_isolato
    db = db_isolato
    headers = ['Eneltel', 'NOMINATIVO', 'CAMPO_CUSTOM']
    se.pull_once(values=[headers, ['9', 'A', 'valore_custom']])
    extra = db.get_conn().execute(
        'SELECT * FROM filone_extra WHERE eneltel_key=?', ('9',)
    ).fetchone()
    assert extra['column_name'] == 'CAMPO_CUSTOM'
    assert extra['value'] == 'valore_custom'


def test_eneltel_normalizzato_con_zeri(sync_engine_isolato, db_isolato):
    """Eneltel '0012345' viene normalizzato a '12345'."""
    se = sync_engine_isolato
    db = db_isolato
    headers = ['Eneltel', 'NOMINATIVO']
    se.pull_once(values=[headers, ['0012345', 'A']])
    assert db.get_filone_row('12345') is not None
    assert db.get_filone_row('0012345') is not None  # normalize_eneltel applicato anche in get
