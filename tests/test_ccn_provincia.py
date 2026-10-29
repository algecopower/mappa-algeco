# -*- coding: utf-8 -*-
"""
Test per la funzionalità di aggiunta automatica in CCn (copia nascosta)
dell'email configurata per la provincia a tutti gli invii email dal gestionale.
"""
import smtplib
from email.mime.text import MIMEText
from unittest.mock import MagicMock

from moduli.smtp import (
    get_provincia_email,
    add_provincia_ccn_to_msg,
    send_smtp_message,
)
from moduli.bulk_contatori_email import send_contatori_email
from moduli.comuni_manager import _build_comune_email_message
from moduli import mail_manager


def test_get_provincia_email():
    """Verifica il corretto recupero dell'email impostata per ciascuna provincia."""
    email_to = get_provincia_email("TORINO")
    email_pv = get_provincia_email("PAVIA")
    email_lc = get_provincia_email("LECCO")

    assert email_to == "tecnico_to2@algecopower.it"
    assert email_pv == "tecnico_pv1@algecopower.it"
    assert email_lc == "tecnico_lecco@algecopower.it"


def test_add_provincia_ccn_to_msg_basic_and_hiding():
    """Verifica che:
    1. Il CCn della provincia venga inserito correttamente nel messaggio.
    2. Al momento della trasmissione SMTP reale (send_message), l'indirizzo CCn
       sia presente nella busta (envelope to_addrs) ma TOTALMENTE nascosto dal
       payload trasmesso al destinatario (nessun header Bcc nel body/header inviato).
    """
    msg = MIMEText("Testo di prova")
    msg["From"] = "tecnico_to2@algecopower.it"
    msg["To"] = "cliente@example.com"
    msg["Subject"] = "Sostituzione contatore"

    # Applica CCn
    add_provincia_ccn_to_msg(msg, prov="TORINO")
    assert msg["Bcc"] == "tecnico_to2@algecopower.it"
    assert msg["To"] == "cliente@example.com"

    # Simula la trasmissione SMTP tramite smtplib standard
    server = smtplib.SMTP()
    server.sendmail = MagicMock(return_value={})
    server.ehlo_or_helo_if_needed = MagicMock()
    server.has_extn = MagicMock(return_value=False)

    server.send_message(msg)

    # Verifica gli argomenti passati a sendmail
    assert server.sendmail.called
    from_addr, to_addrs, flatmsg = server.sendmail.call_args[0][:3]

    assert from_addr == "tecnico_to2@algecopower.it"
    # L'envelope deve includere sia il destinatario sia la copia nascosta
    assert "cliente@example.com" in to_addrs
    assert "tecnico_to2@algecopower.it" in to_addrs

    # Il payload trasmesso al destinatario NON deve contenere l'header Bcc (nascosta)
    payload_str = flatmsg.decode("utf-8")
    assert "To: cliente@example.com" in payload_str
    assert "Bcc:" not in payload_str
    assert "tecnico_to2@algecopower.it" not in payload_str.split("\n\n")[0].split("To:")[1]


def test_add_provincia_ccn_no_duplicate():
    """Verifica che l'indirizzo della provincia non venga duplicato se già presente."""
    # 1. Se già in To
    msg1 = MIMEText("Corpo")
    msg1["To"] = "tecnico_to2@algecopower.it"
    add_provincia_ccn_to_msg(msg1, prov="TORINO")
    assert msg1.get("Bcc") is None

    # 2. Se già in Cc
    msg2 = MIMEText("Corpo")
    msg2["To"] = "cliente@example.com"
    msg2["Cc"] = "tecnico_to2@algecopower.it"
    add_provincia_ccn_to_msg(msg2, prov="TORINO")
    assert msg2.get("Bcc") is None

    # 3. Se già in Bcc
    msg3 = MIMEText("Corpo")
    msg3["To"] = "cliente@example.com"
    msg3["Bcc"] = "tecnico_to2@algecopower.it"
    add_provincia_ccn_to_msg(msg3, prov="TORINO")
    assert msg3["Bcc"] == "tecnico_to2@algecopower.it"

    # 4. Se in Bcc c'è un altro destinatario, viene appeso
    msg4 = MIMEText("Corpo")
    msg4["To"] = "cliente@example.com"
    msg4["Bcc"] = "altro@example.com"
    add_provincia_ccn_to_msg(msg4, prov="TORINO")
    assert "altro@example.com" in msg4["Bcc"]
    assert "tecnico_to2@algecopower.it" in msg4["Bcc"]


def test_send_smtp_message_adds_ccn_automatically():
    """Verifica che send_smtp_message applichi automaticamente il CCn della provincia."""
    captured_messages = []

    class DummyServer:
        def send_message(self, msg):
            captured_messages.append(msg)

        def quit(self):
            pass

        def close(self):
            pass

    dummy_smtp = {
        "host": "smtp.example.com",
        "port": 587,
        "username": "user",
        "password": "pwd",
    }

    msg = MIMEText("Contenuto")
    msg["To"] = "destinatario@example.com"

    import moduli.smtp as smtp_mod
    orig_connect = smtp_mod.connect_smtp
    smtp_mod.connect_smtp = lambda settings, timeout=20: DummyServer()

    try:
        ok, err = send_smtp_message(
            dummy_smtp, msg, max_retries=1, save_sent=False, add_provincia_ccn=True
        )
        assert ok is True
        assert len(captured_messages) == 1
        sent_msg = captured_messages[0]
        assert "tecnico_to2@algecopower.it" in (sent_msg.get("Bcc") or "")
    finally:
        smtp_mod.connect_smtp = orig_connect


def test_send_contatori_email_includes_ccn(monkeypatch):
    """Verifica che la funzione send_contatori_email includa il CCn della provincia."""
    captured_msg = {}

    def fake_send_smtp(smtp_settings, msg, **kwargs):
        captured_msg["msg"] = msg
        return True, None

    monkeypatch.setattr(
        "moduli.bulk_contatori_email.load_smtp_settings",
        lambda prov="torino": {
            "host": "smtp.test.com",
            "port": 587,
            "username": "test@algecopower.it",
            "password": "secret_password",
            "from_email": "tecnico_to2@algecopower.it",
        },
    )
    monkeypatch.setattr(
        "moduli.bulk_contatori_email.send_smtp_message",
        fake_send_smtp,
    )

    contatori = [
        {"pod": "IT001E12345678", "comune": "TORINO", "indirizzo": "Via Roma 1", "cliente": "Test"}
    ]
    res = send_contatori_email(
        email_dest="cliente@example.com",
        subject="Avviso sostituzione",
        body_html="<p>Test</p>",
        contatto={"nome": "Test Azienda", "pod": "IT001E12345678"},
        contatori_list=contatori,
        provincia="TORINO",
    )
    assert res["ok"] is True
    assert "msg" in captured_msg
    msg = captured_msg["msg"]
    assert msg["To"] == "cliente@example.com"
    assert msg["Bcc"] == "tecnico_to2@algecopower.it"


def test_comune_email_includes_ccn():
    """Verifica che l'invio email ai comuni includa il CCn della provincia."""
    smtp_settings = {
        "host": "smtp.test.com",
        "port": 587,
        "username": "test@algecopower.it",
        "from_email": "tecnico_to2@algecopower.it",
    }
    msg = _build_comune_email_message(
        dest_email="polizia.locale@comune.torino.it",
        subject="Comunicazione lavori",
        body_html="<p>Lavori in corso</p>",
        allegati_nomi=[],
        comune_name="TORINO",
        smtp_settings=smtp_settings,
    )
    assert msg["To"] == "polizia.locale@comune.torino.it"
    assert msg["Bcc"] == "tecnico_to2@algecopower.it"


def test_mail_manager_build_and_send_includes_ccn(db_isolato, monkeypatch):
    """Verifica che mail_manager imposti il CCn della provincia in composizione/invio."""
    # 1. build_mime_message imposta msg['Bcc']
    msg = mail_manager.build_mime_message(
        from_addr="tecnico_to2@algecopower.it",
        from_name="Ufficio Tecnico",
        to_addrs=["cliente@example.com"],
        bcc_addrs=["tecnico_to2@algecopower.it"],
        subject="Test Webmail",
        body_text="Test",
    )
    assert msg["To"] == "cliente@example.com"
    assert msg["Bcc"] == "tecnico_to2@algecopower.it"

    # 2. send_or_schedule_mail aggiunge automaticamente la provincia in bcc_addrs
    captured = {}

    def fake_send(settings, mime_msg, max_retries=3, save_sent=True):
        captured["mime_msg"] = mime_msg
        return True, None

    monkeypatch.setattr(mail_manager, "send_smtp_message", fake_send)

    res = mail_manager.send_or_schedule_mail(
        {
            "to": ["dest@example.com"],
            "subject": "Email da gestionale",
            "body_text": "Corpo",
        },
        prov="torino",
    )
    assert res["success"] is True
    assert "mime_msg" in captured
    sent_mime = captured["mime_msg"]
    assert sent_mime["To"] == "dest@example.com"
    assert sent_mime["Bcc"] == "tecnico_to2@algecopower.it"
