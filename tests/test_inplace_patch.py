# -*- coding: utf-8 -*-
"""
Test della patch in-place di mappa_data.json (opzione B).

Verifica:
- Soft edit puro → patch applicata, ritorna True
- Edit con anche un campo strutturale → ritorna False (fallback al sync)
- File mappa_data.json effettivamente modificato per i campi soft
- Mapping db→json corretto (es. 'nominativo'→'cliente', 'indirizzo'→'via')
"""
import json


def _setup_filone_e_mappa(db, tmp_path, eneltel='42'):
    """Setup: registry + filone + mappa_data.json fittizia."""
    conn = db.get_conn()
    db.update_column_registry(
        conn,
        ['Eneltel', 'NOMINATIVO', 'EMAIL', 'telefono', 'Indirizzo',
         'DATA PIAN', 'Operatore', 'NOTE APP', 'Latitudine', 'Longitudine'],
    )
    with conn:
        db.upsert_filone_row(
            conn, eneltel_key=eneltel, sheets_row=10,
            typed_values={
                'nominativo': 'Mario',
                'email': 'old@example.com',
                'telefono': '123',
                'indirizzo': 'VIA ROMA',
                'data_pianificata': '2026-05-01',
                'operatore': 'TEC1',
            },
            extra_values={},
            row_hash='h', source='bootstrap',
        )

    # Crea mappa_data.json di prova
    data_dir = tmp_path / 'data' / 'torino'
    data_dir.mkdir(parents=True)
    mappa_data = {
        'timestamp': 1000,
        'zone': [],
        'rotte': {},
        'lcl_list': [],
        'contatori': [
            {
                'id': eneltel,
                'eneltel': eneltel,
                'cliente': 'Mario',
                'email': 'old@example.com',
                'telefono': '123',
                'via': 'VIA ROMA',
                'civico': '1',
                'data': '2026-05-01',
                'tecnico': 'TEC1',
                'lat': 45.0,
                'lon': 7.0,
                'is_appuntamento': False,
            },
        ],
    }
    json_path = data_dir / 'mappa_data.json'
    json_path.write_text(json.dumps(mappa_data))
    return str(json_path)


def test_patch_soft_edit_email(db_isolato, tmp_path, monkeypatch):
    """Modifica solo email → patch applicata."""
    db = db_isolato
    json_path = _setup_filone_e_mappa(db, tmp_path)

    # Patcha PROVINCIA_ATTIVA path
    import config
    monkeypatch.setattr(config, 'PROVINCIA_ATTIVA', 'TORINO')
    monkeypatch.chdir(tmp_path)

    # Simula una pending soft (email)
    db.record_local_change(
        '42', {'EMAIL': 'new@example.com'},
        origin='unit_test', initial_state='pending',
    )

    from moduli import mappa_data_patcher
    assert mappa_data_patcher.try_patch_inplace() is True

    # Verifica che il file sia stato modificato
    with open(json_path) as f:
        patched = json.load(f)
    contatore = patched['contatori'][0]
    assert contatore['email'] == 'new@example.com'
    assert contatore['cliente'] == 'Mario'  # invariato
    # Timestamp aggiornato
    assert patched['timestamp'] != 1000


def test_patch_soft_edit_multipli_campi(db_isolato, tmp_path, monkeypatch):
    """Modifica più campi soft insieme → patch applicata."""
    db = db_isolato
    json_path = _setup_filone_e_mappa(db, tmp_path)

    import config
    monkeypatch.setattr(config, 'PROVINCIA_ATTIVA', 'TORINO')
    monkeypatch.chdir(tmp_path)

    db.record_local_change(
        '42',
        {'EMAIL': 'new@x.com', 'telefono': '999', 'NOMINATIVO': 'Luigi'},
        origin='t', initial_state='pending',
    )

    from moduli import mappa_data_patcher
    assert mappa_data_patcher.try_patch_inplace() is True

    with open(json_path) as f:
        patched = json.load(f)
    c = patched['contatori'][0]
    assert c['email'] == 'new@x.com'
    assert c['telefono'] == '999'
    assert c['cliente'] == 'Luigi'  # mapping NOMINATIVO → cliente


def test_patch_struct_edit_falls_back(db_isolato, tmp_path, monkeypatch):
    """Modifica con un campo strutturale (DATA PIAN) → ritorna False."""
    db = db_isolato
    json_path = _setup_filone_e_mappa(db, tmp_path)

    import config
    monkeypatch.setattr(config, 'PROVINCIA_ATTIVA', 'TORINO')
    monkeypatch.chdir(tmp_path)

    db.record_local_change(
        '42',
        {'EMAIL': 'new@x.com', 'DATA PIAN': '2026-06-01'},
        origin='t', initial_state='pending',
    )

    from moduli import mappa_data_patcher
    assert mappa_data_patcher.try_patch_inplace() is False

    # File non deve essere stato toccato (timestamp invariato)
    with open(json_path) as f:
        unchanged = json.load(f)
    assert unchanged['timestamp'] == 1000


def test_patch_note_app_aggiunto_falls_back(db_isolato, tmp_path, monkeypatch):
    """Prima testo libero, poi 'APP 09:00' → IS_APPUNTAMENTO cambia: fallback."""
    db = db_isolato
    _setup_filone_e_mappa(db, tmp_path)

    import config
    monkeypatch.setattr(config, 'PROVINCIA_ATTIVA', 'TORINO')
    monkeypatch.chdir(tmp_path)

    # Aggiungi pending NOTE APP con base_value senza APP, new_value con APP
    db.record_local_change(
        '42', {'NOTE APP': 'APP 09:00'},
        base_values={'NOTE APP': 'testo libero qualunque'},
        origin='t', initial_state='pending',
    )

    from moduli import mappa_data_patcher
    assert mappa_data_patcher.try_patch_inplace() is False


def test_patch_note_app_rimosso_falls_back(db_isolato, tmp_path, monkeypatch):
    """Prima 'APP 09:00', poi testo libero → IS_APPUNTAMENTO cambia: fallback."""
    db = db_isolato
    _setup_filone_e_mappa(db, tmp_path)

    import config
    monkeypatch.setattr(config, 'PROVINCIA_ATTIVA', 'TORINO')
    monkeypatch.chdir(tmp_path)

    db.record_local_change(
        '42', {'NOTE APP': 'cliente non risponde'},
        base_values={'NOTE APP': 'APP 09:00'},
        origin='t', initial_state='pending',
    )

    from moduli import mappa_data_patcher
    assert mappa_data_patcher.try_patch_inplace() is False


def test_patch_note_app_orario_cambiato_falls_back(db_isolato, tmp_path, monkeypatch):
    """Da 'APP 09:00' a 'APP 10:30' → ORA_APPUNTAMENTO cambia: fallback."""
    db = db_isolato
    _setup_filone_e_mappa(db, tmp_path)

    import config
    monkeypatch.setattr(config, 'PROVINCIA_ATTIVA', 'TORINO')
    monkeypatch.chdir(tmp_path)

    db.record_local_change(
        '42', {'NOTE APP': 'APP 10:30'},
        base_values={'NOTE APP': 'APP 09:00'},
        origin='t', initial_state='pending',
    )

    from moduli import mappa_data_patcher
    assert mappa_data_patcher.try_patch_inplace() is False


def test_patch_note_libera_in_inplace(db_isolato, tmp_path, monkeypatch):
    """Modifica testo nota SENZA pattern APP né prima né dopo → patch in-place."""
    db = db_isolato
    json_path = _setup_filone_e_mappa(db, tmp_path)

    import config
    monkeypatch.setattr(config, 'PROVINCIA_ATTIVA', 'TORINO')
    monkeypatch.chdir(tmp_path)

    db.record_local_change(
        '42', {'NOTE APP': 'cliente al lavoro fino alle 18'},
        base_values={'NOTE APP': 'mandato wa'},
        origin='t', initial_state='pending',
    )

    from moduli import mappa_data_patcher
    assert mappa_data_patcher.try_patch_inplace() is True

    with open(json_path) as f:
        patched = json.load(f)
    c = patched['contatori'][0]
    # Il campo `note_app` nel JSON deve riflettere la nuova nota
    assert c['note_app'] == 'cliente al lavoro fino alle 18'


def test_patch_no_pending_returns_false(db_isolato, tmp_path, monkeypatch):
    """Senza pending in volo, ritorna False (fallback)."""
    db = db_isolato
    _setup_filone_e_mappa(db, tmp_path)

    import config
    monkeypatch.setattr(config, 'PROVINCIA_ATTIVA', 'TORINO')
    monkeypatch.chdir(tmp_path)

    from moduli import mappa_data_patcher
    assert mappa_data_patcher.try_patch_inplace() is False


def test_patch_done_pending_ignored(db_isolato, tmp_path, monkeypatch):
    """Pending già drenate (state='done') vengono ignorate dal patcher."""
    db = db_isolato
    _setup_filone_e_mappa(db, tmp_path)

    import config
    monkeypatch.setattr(config, 'PROVINCIA_ATTIVA', 'TORINO')
    monkeypatch.chdir(tmp_path)

    # Pending già done — non in volo
    db.record_local_change(
        '42', {'EMAIL': 'whatever'},
        origin='t', initial_state='done',
    )

    from moduli import mappa_data_patcher
    assert mappa_data_patcher.try_patch_inplace() is False


def test_patch_soft_edit_orari(db_isolato, tmp_path, monkeypatch):
    """Modifica di orari_lettere ('Orari') patcha sia orari_lettere che ora_pianificazione."""
    db = db_isolato
    json_path = _setup_filone_e_mappa(db, tmp_path)

    import config
    monkeypatch.setattr(config, 'PROVINCIA_ATTIVA', 'TORINO')
    monkeypatch.chdir(tmp_path)

    db.record_local_change(
        '42', {'Orari': 'dalle 10:00 alle 12:00'},
        origin='unit_test', initial_state='pending',
    )

    from moduli import mappa_data_patcher
    assert mappa_data_patcher.try_patch_inplace() is True

    with open(json_path) as f:
        patched = json.load(f)
    c = patched['contatori'][0]
    assert c['orari_lettere'] == 'dalle 10:00 alle 12:00'
    assert c['ora_pianificazione'] == 'dalle 10:00 alle 12:00'

