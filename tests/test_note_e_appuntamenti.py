# -*- coding: utf-8 -*-
"""
tests/test_note_e_appuntamenti.py - Test per la preservazione delle note
durante la gestione appuntamenti e per la formattazione automatica delle note
di invio email e messaggi WhatsApp.
"""

from flask import Flask
from moduli.shared import (
    _calcola_nuova_nota_app,
    _calcola_rimozione_app,
    _imposta_appuntamento_su_filone,
    _rimuovi_appuntamento_da_filone,
)
import routes.contatori as r_contatori
import routes.distinte as r_distinte


def test_calcola_nuova_nota_app_preserva_note_esistenti():
    """Verifica che _calcola_nuova_nota_app preservi le note precedenti concatenando con //."""
    # 1. Nessuna nota precedente
    nuova = _calcola_nuova_nota_app("", "10:00-12:00")
    assert nuova == "APP 10:00-12:00"

    # 2. Nota precedente singola
    nuova = _calcola_nuova_nota_app("cliente assente", "10:00-12:00")
    assert nuova == "APP 10:00-12:00 // cliente assente"

    # 3. Note multiple concatenate
    nota_esistente = "29/10 mandata mail per 30/10 // cliente assente"
    nuova = _calcola_nuova_nota_app(nota_esistente, "10:00-12:00")
    assert nuova == "APP 10:00-12:00 // 29/10 mandata mail per 30/10 // cliente assente"

    # 4. Nota con APP già presente all'inizio -> sostituisce solo l'APP
    nota_esistente = "APP 09:00 // 29/10 mandato wa per 30/10 // scala B"
    nuova = _calcola_nuova_nota_app(nota_esistente, "14:30")
    assert nuova == "APP 14:30 // 29/10 mandato wa per 30/10 // scala B"

    # 5. Nota con APP in mezzo o in fondo -> sostituisce e posiziona in testa
    nota_esistente = "cancello chiuso // APP 09:00-11:00 // cane da guardia"
    nuova = _calcola_nuova_nota_app(nota_esistente, "15:00")
    assert nuova == "APP 15:00 // cancello chiuso // cane da guardia"

    # 6. Con note extra nella funzione imposta appuntamento
    nuova_con_extra = _calcola_nuova_nota_app("citofono rotto", "11:00 (chiamare prima)")
    assert nuova_con_extra == "APP 11:00 (chiamare prima) // citofono rotto"


def test_calcola_rimozione_app_preserva_altre_note():
    """Verifica che la rimozione dell'appuntamento preservi tutte le altre note."""
    # 1. Solo APP -> stringa vuota
    assert _calcola_rimozione_app("APP 10:00") == ""
    assert _calcola_rimozione_app("APP 10:00-12:00") == ""

    # 2. APP con altra nota
    assert _calcola_rimozione_app("APP 10:00 // cliente assente") == "cliente assente"

    # 3. APP all'inizio con più note
    nota = "APP 10:00-12:00 // 29/10 mandata mail per 30/10 // citofonare Rossi"
    assert _calcola_rimozione_app(nota) == "29/10 mandata mail per 30/10 // citofonare Rossi"

    # 4. APP in mezzo
    nota = "29/10 mandato wa per 30/10 // APP 10:00-12:00 // citofonare Rossi"
    assert _calcola_rimozione_app(nota) == "29/10 mandato wa per 30/10 // citofonare Rossi"


def test_appuntamento_filone_db_preserva_note(db_isolato):
    """Verifica che _imposta_appuntamento_su_filone salvi nel DB preservando le note."""
    db = db_isolato
    pod = "IT001E11223344"
    eneltel = "112233445"
    lcl = "6600112233"

    conn = db.get_conn()
    with conn:
        db.upsert_filone_row(
            conn,
            eneltel_key=eneltel,
            sheets_row=12,
            typed_values={
                "pod": pod,
                "lcl": lcl,
                "data_pianificata": "30/10/2026",
                "note_appuntamento": "29/10 mandata mail per 30/10 // cancello chiuso",
            },
            extra_values={},
            row_hash="test1",
            source="test",
        )

    # Imposta appuntamento
    res = _imposta_appuntamento_su_filone(
        12,
        "10:00-11:00",
        origine="distinte",
        pod_override=pod,
        eneltel_override=eneltel,
        lcl=lcl,
    )
    assert (
        res["nuova_nota"]
        == "APP 10:00-11:00 // 29/10 mandata mail per 30/10 // cancello chiuso"
    )

    # Verifica nel DB
    row = db.get_filone_row(eneltel, lcl=lcl)
    assert row["note_appuntamento"] == res["nuova_nota"]

    # Rimuovi appuntamento
    res_rem = _rimuovi_appuntamento_da_filone(
        12,
        pod_override=pod,
        eneltel_override=eneltel,
        lcl=lcl,
    )
    assert res_rem["nuova_nota"] == "29/10 mandata mail per 30/10 // cancello chiuso"

    # Verifica nel DB
    row2 = db.get_filone_row(eneltel, lcl=lcl)
    assert row2["note_appuntamento"] == "29/10 mandata mail per 30/10 // cancello chiuso"


def test_appuntamento_filone_db_con_nota_corrente_client(db_isolato):
    """Verifica che se il client invia una nota_corrente modificata, essa venga preservata."""
    db = db_isolato
    pod = "IT001E99887766"
    eneltel = "998877665"
    lcl = "6600998877"

    conn = db.get_conn()
    with conn:
        db.upsert_filone_row(
            conn,
            eneltel_key=eneltel,
            sheets_row=15,
            typed_values={
                "pod": pod,
                "lcl": lcl,
                "data_pianificata": "31/10/2026",
                "note_appuntamento": "nota vecchia",
            },
            extra_values={},
            row_hash="test2",
            source="test",
        )

    # Client ha nelle note locali: "29/10 mandato wa per 31/10 // cliente preferisce pomeriggio"
    nota_client = "29/10 mandato wa per 31/10 // cliente preferisce pomeriggio"
    res = _imposta_appuntamento_su_filone(
        15,
        "15:00-16:00",
        origine="distinte",
        pod_override=pod,
        eneltel_override=eneltel,
        lcl=lcl,
        nota_corrente=nota_client,
    )

    # La nuova nota deve contenere l'APP e la nota del client
    assert "APP 15:00-16:00" in res["nuova_nota"]
    assert "29/10 mandato wa per 31/10" in res["nuova_nota"]
    assert "cliente preferisce pomeriggio" in res["nuova_nota"]


def test_api_distinte_appuntamento_preserva_note(db_isolato):
    """Test endpoint Flask /api/distinte/appuntamento e rimuovi-appuntamento."""
    db = db_isolato
    pod = "IT001E55443322"
    eneltel = "554433221"
    lcl = "6600554433"

    conn = db.get_conn()
    with conn:
        db.upsert_filone_row(
            conn,
            eneltel_key=eneltel,
            sheets_row=20,
            typed_values={
                "pod": pod,
                "lcl": lcl,
                "data_pianificata": "01/11/2026",
                "note_appuntamento": "29/10 mandata mail per 01/11 // citofono rotto",
            },
            extra_values={},
            row_hash="test3",
            source="test",
        )

    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(r_distinte.bp)

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user"] = {"username": "operatore", "ruolo": "admin"}

    # Fissa appuntamento
    resp = client.post(
        "/api/distinte/appuntamento",
        json={"pod": pod, "orario": "09:30-10:30", "lcl": lcl},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert (
        data["nota"]
        == "APP 09:30-10:30 // 29/10 mandata mail per 01/11 // citofono rotto"
    )

    # Rimuovi appuntamento
    resp_rem = client.post(
        "/api/distinte/rimuovi-appuntamento",
        json={"pod": pod, "lcl": lcl},
    )
    assert resp_rem.status_code == 200
    data_rem = resp_rem.get_json()
    assert data_rem["success"] is True
    assert data_rem["nota"] == "29/10 mandata mail per 01/11 // citofono rotto"


def test_api_contatori_appuntamento_preserva_note(db_isolato):
    """Test endpoint Flask /api/contatore/<eneltel>/appuntamento e rimuovi-appuntamento."""
    db = db_isolato
    pod = "IT001E66778899"
    eneltel = "667788990"
    lcl = "6600667788"

    conn = db.get_conn()
    with conn:
        db.upsert_filone_row(
            conn,
            eneltel_key=eneltel,
            sheets_row=25,
            typed_values={
                "pod": pod,
                "lcl": lcl,
                "data_pianificata": "02/11/2026",
                "note_appuntamento": "cliente presente solo al mattino",
            },
            extra_values={},
            row_hash="test4",
            source="test",
        )

    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(r_contatori.bp)

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user"] = {"username": "operatore", "ruolo": "admin"}

    # Fissa appuntamento
    resp = client.post(
        f"/api/contatore/{eneltel}/appuntamento",
        json={"orario": "08:30-09:30", "lcl": lcl},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert (
        data["nota"]
        == "APP 08:30-09:30 // cliente presente solo al mattino"
    )

    # Rimuovi appuntamento
    resp_rem = client.post(
        f"/api/contatore/{eneltel}/rimuovi-appuntamento",
        json={"lcl": lcl},
    )
    assert resp_rem.status_code == 200
    data_rem = resp_rem.get_json()
    assert data_rem["success"] is True
    assert data_rem["nota"] == "cliente presente solo al mattino"


def test_api_distinte_salva_nota_auto_arricchimento(db_isolato):
    """Test che /api/distinte/salva-nota arricchisca mandata mail / mandato wa con data target e concateni con //."""
    db = db_isolato
    pod = "IT001E33445566"
    eneltel = "334455667"
    lcl = "6600334455"

    conn = db.get_conn()
    with conn:
        db.upsert_filone_row(
            conn,
            eneltel_key=eneltel,
            sheets_row=30,
            typed_values={
                "pod": pod,
                "lcl": lcl,
                "data_pianificata": "05/11/2026",
                "note_appuntamento": "chiamare prima di passare",
            },
            extra_values={},
            row_hash="test5",
            source="test",
        )

    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(r_distinte.bp)

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user"] = {"username": "operatore", "ruolo": "admin"}

    # Invio nota "mandata mail" con sovrascrivere=False
    resp = client.post(
        "/api/distinte/salva-nota",
        json={"pod": pod, "nota": "mandata mail", "sovrascrivere": False, "lcl": lcl},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert "mandata mail per 05/11" in data["nota"]
    assert " // chiamare prima di passare" in data["nota"]

    # Invio nota "mandato wa" con sovrascrivere=False
    resp2 = client.post(
        "/api/distinte/salva-nota",
        json={"pod": pod, "nota": "mandato wa", "sovrascrivere": False, "lcl": lcl},
    )
    assert resp2.status_code == 200
    data2 = resp2.get_json()
    assert data2["success"] is True
    assert "mandato wa per 05/11" in data2["nota"]
    assert "mandata mail per 05/11" in data2["nota"]
    assert "chiamare prima di passare" in data2["nota"]


def test_api_invia_singola_email_distinte_e_lettere(db_isolato, monkeypatch):
    """Test invio singola email da endpoint distinte e lettere con aggiornamento note a DB."""
    db = db_isolato
    pod = "IT001E88776655"
    eneltel = "887766554"
    lcl = "6600887766"

    conn = db.get_conn()
    with conn:
        db.upsert_filone_row(
            conn,
            eneltel_key=eneltel,
            sheets_row=40,
            typed_values={
                "pod": pod,
                "lcl": lcl,
                "data_pianificata": "12/11/2026",
                "note_appuntamento": "preferisce contatto pomeridiano",
            },
            extra_values={},
            row_hash="test_email_1",
            source="test",
        )

    # Mock SMTP
    import moduli.bulk_contatori_email as bce
    monkeypatch.setattr(
        bce,
        "load_smtp_settings",
        lambda prov="torino": {
            "host": "smtp.test.com",
            "port": 587,
            "username": "test@algecopower.it",
            "password": "secret_password",
            "from_email": "test@algecopower.it",
            "from_name": "Algeco Test",
        },
    )
    monkeypatch.setattr(
        bce,
        "send_smtp_message",
        lambda *args, **kwargs: (True, None),
    )

    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(r_distinte.bp)

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user"] = {"username": "operatore", "ruolo": "admin"}

    # 1. Invio singola email via distinte endpoint
    payload = {
        "email": "cliente@example.com",
        "contatto": {
            "pod": pod,
            "nome": "Mario Rossi",
            "data_intervento": "12/11/2026",
            "lcl": lcl,
            "eneltel": eneltel,
        },
        "subject_override": "Avviso sostituzione contatore",
        "body_override": "<p>Gentile cliente, le comunichiamo che...</p>",
        "context": "distinte",
    }
    resp = client.post("/api/distinte/invia-singola-email", json=payload)
    assert resp.status_code == 200
    res_data = resp.get_json()
    assert res_data["ok"] is True
    assert res_data["email"] == "cliente@example.com"

    # Verifica aggiornamento nota sul DB filone
    row = db.get_filone_row(eneltel, lcl=lcl)
    assert "mandata mail per 12/11" in row["note_appuntamento"]
    assert "preferisce contatto pomeridiano" in row["note_appuntamento"]
    assert " // " in row["note_appuntamento"]

    # 2. Invio singola email via lettere endpoint
    payload_lettere = {
        "email": "privato@example.com",
        "contatto": {
            "pod": pod,
            "nome": "Mario Rossi",
            "data_pian_filone": "12/11/2026",
            "lcl": lcl,
            "eneltel": eneltel,
        },
        "subject_override": "Avviso sostituzione privati",
        "body_override": "<p>Testo lettera</p>",
        "context": "lettere_privati",
    }
    resp_let = client.post("/api/lettere/invia-singola-email", json=payload_lettere)
    assert resp_let.status_code == 200
    res_let_data = resp_let.get_json()
    assert res_let_data["ok"] is True

    # 3. Chiamata con email mancante -> errore 400
    payload_no_email = {
        "contatto": {"pod": pod},
        "subject_override": "Test",
    }
    resp_no = client.post("/api/distinte/invia-singola-email", json=payload_no_email)
    assert resp_no.status_code == 400

