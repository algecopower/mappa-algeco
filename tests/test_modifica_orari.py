# -*- coding: utf-8 -*-
"""
Test per la modifica della fascia oraria / orari lettere dei contatori.

Verifica che:
- La configurazione mappi 'ora_pianificazione' su 'Orari' (e non su 'Ora pianificazione').
- L'aggiornamento di 'Orari' / 'ora_pianificazione' tramite update_row_fields o PUT /api/contatore/<eneltel>
  scriva correttamente nella colonna tipizzata filone.orari_lettere del DB SQLite.
- GET /api/contatore/<eneltel> restituisca sia 'orari_lettere' sia 'ora_pianificazione'.
"""
from flask import Flask
from config import _CAMPI_MODIFICABILI_COMUNI, CAMPI_MODIFICABILI, MAPPATURA_COLONNE_DEFAULT


def test_config_ora_pianificazione_mappata_a_orari():
    """Verifica che _CAMPI_MODIFICABILI_COMUNI contenga 'ora_pianificazione': 'Orari'."""
    assert _CAMPI_MODIFICABILI_COMUNI['ora_pianificazione'] == 'Orari'
    for prov, campi in CAMPI_MODIFICABILI.items():
        assert campi.get('ora_pianificazione') == 'Orari'
    assert 'Ora pianificazione' in MAPPATURA_COLONNE_DEFAULT['orari_lettere']['nomi_default']
    assert 'Orari' in MAPPATURA_COLONNE_DEFAULT['orari_lettere']['nomi_default']


def test_update_row_fields_aggiorna_filone_orari_lettere(db_isolato):
    """Verifica che update_row_fields aggiorni filone.orari_lettere."""
    db = db_isolato
    conn = db.get_conn()
    db.update_column_registry(
        conn,
        ['Eneltel', 'Orari', 'DATA PIAN', 'NOTE APP', 'NOMINATIVO'],
    )
    with conn:
        db.upsert_filone_row(
            conn,
            eneltel_key='ENELTEL_TEST_1',
            sheets_row=15,
            typed_values={'orari_lettere': 'dalle 09:00 alle 12:00', 'nominativo': 'Test User'},
            extra_values={},
            row_hash='hash1',
            source='bootstrap',
        )

    # Verifica stato iniziale
    row_before = db.get_filone_row('ENELTEL_TEST_1')
    assert row_before['orari_lettere'] == 'dalle 09:00 alle 12:00'

    # Aggiorna campo 'Orari'
    from moduli.shared import update_row_fields
    success, msg = update_row_fields('ENELTEL_TEST_1', {'Orari': 'dalle 10:00 alle 12:00'})
    assert success is True

    # Verifica che nel DB filone.orari_lettere sia stato aggiornato
    row_after = db.get_filone_row('ENELTEL_TEST_1')
    assert row_after['orari_lettere'] == 'dalle 10:00 alle 12:00'


def test_api_update_contatore_orari(db_isolato, monkeypatch):
    """Verifica endpoint PUT /api/contatore/<eneltel> e GET /api/contatore/<eneltel>."""
    db = db_isolato
    conn = db.get_conn()
    db.update_column_registry(
        conn,
        ['Eneltel', 'Orari', 'DATA PIAN', 'NOTE APP', 'NOMINATIVO', 'Indirizzo', 'Num', 'telefono', 'email', 'Posizione_Ante_Mis', 'Operatore', 'Esito', 'Smart Test'],
    )
    with conn:
        db.upsert_filone_row(
            conn,
            eneltel_key='1234567890',
            sheets_row=20,
            typed_values={'orari_lettere': 'dalle 08:00 alle 11:00', 'nominativo': 'Mario Rossi'},
            extra_values={},
            row_hash='hash2',
            source='bootstrap',
        )

    # Crea app Flask di test con blueprint contatori
    from routes.contatori import bp as contatori_bp
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test-secret'
    app.register_blueprint(contatori_bp)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['user'] = {'username': 'test_admin', 'ruolo': 'admin', 'province': ['TORINO', 'PAVIA']}

        # 1. Chiama GET iniziale
        resp = client.get('/api/contatore/1234567890')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['contatore']['orari_lettere'] == 'dalle 08:00 alle 11:00'
        assert data['contatore']['ora_pianificazione'] == 'dalle 08:00 alle 11:00'

        # 2. Invia PUT con 'ora_pianificazione'
        resp_put = client.put(
            '/api/contatore/1234567890',
            json={'ora_pianificazione': 'dalle 14:00 alle 16:00'},
        )
        assert resp_put.status_code == 200
        res_json = resp_put.get_json()
        assert res_json['success'] is True

        # 3. Verifica sul DB
        row_db = db.get_filone_row('1234567890')
        assert row_db['orari_lettere'] == 'dalle 14:00 alle 16:00'

        # 4. Chiama GET per verificare la risposta formattata
        resp_get2 = client.get('/api/contatore/1234567890')
        assert resp_get2.status_code == 200
        data2 = resp_get2.get_json()
        assert data2['contatore']['orari_lettere'] == 'dalle 14:00 alle 16:00'
        assert data2['contatore']['ora_pianificazione'] == 'dalle 14:00 alle 16:00'
