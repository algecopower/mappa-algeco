# -*- coding: utf-8 -*-
"""Test per la gestione degli errori SMTP, retry su codice 452 ed invio email."""

import smtplib
from unittest.mock import MagicMock, patch
from email.message import EmailMessage

from moduli.smtp import send_smtp_message


def test_send_smtp_message_success():
    smtp_settings = {
        'host': 'smtp.test.com',
        'port': 587,
        'username': 'user@test.com',
        'password': 'secretpassword',
    }
    msg = EmailMessage()
    msg['Subject'] = 'Test'
    msg['From'] = 'user@test.com'
    msg['To'] = 'dest@test.com'

    mock_server = MagicMock()
    with patch('moduli.smtp.connect_smtp', return_value=mock_server) as mock_connect, \
         patch('moduli.smtp.save_to_sent') as mock_save:
        ok, err = send_smtp_message(smtp_settings, msg, max_retries=3, save_sent=True)
        assert ok is True
        assert err is None
        assert mock_connect.call_count == 1
        mock_server.send_message.assert_called_once_with(msg)
        mock_server.quit.assert_called_once()
        mock_save.assert_called_once_with(smtp_settings, msg)


def test_send_smtp_message_retries_on_452_policy_violation():
    smtp_settings = {
        'host': 'smtp.test.com',
        'port': 587,
        'username': 'user@test.com',
        'password': 'secretpassword',
    }
    msg = EmailMessage()
    msg['Subject'] = 'Test'
    msg['From'] = 'user@test.com'
    msg['To'] = 'dest@test.com'

    mock_server_fail = MagicMock()
    # Simula errore 452 4.5.3 Policy violation
    mock_server_fail.send_message.side_effect = smtplib.SMTPResponseException(
        452, b'4.5.3 Policy violation: too many messages per minute'
    )

    mock_server_ok = MagicMock()

    # Tentativo 1 fallisce con 452, Tentativo 2 ha successo
    with patch('moduli.smtp.connect_smtp', side_effect=[mock_server_fail, mock_server_ok]) as mock_connect, \
         patch('moduli.smtp.time.sleep') as mock_sleep, \
         patch('moduli.smtp.save_to_sent') as mock_save:
        ok, err = send_smtp_message(smtp_settings, msg, max_retries=3, base_delay=1, save_sent=True)
        assert ok is True
        assert err is None
        assert mock_connect.call_count == 2
        mock_sleep.assert_called_once_with(1)
        mock_save.assert_called_once_with(smtp_settings, msg)


def test_send_smtp_message_exhausts_retries_on_452():
    smtp_settings = {
        'host': 'smtp.test.com',
        'port': 587,
        'username': 'user@test.com',
        'password': 'secretpassword',
    }
    msg = EmailMessage()
    msg['Subject'] = 'Test'
    msg['From'] = 'user@test.com'
    msg['To'] = 'dest@test.com'

    mock_server_fail = MagicMock()
    mock_server_fail.send_message.side_effect = smtplib.SMTPResponseException(
        452, b'4.5.3 Policy violation'
    )

    with patch('moduli.smtp.connect_smtp', return_value=mock_server_fail) as mock_connect, \
         patch('moduli.smtp.time.sleep') as mock_sleep:
        ok, err = send_smtp_message(smtp_settings, msg, max_retries=3, base_delay=1, save_sent=False)
        assert ok is False
        assert "Limite invio provider superato (452" in err
        assert mock_connect.call_count == 3
        assert mock_sleep.call_count == 2
