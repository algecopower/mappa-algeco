# -*- coding: utf-8 -*-
"""
tests/test_sms_gateway.py — Test unitari per il modulo SMS Gateway.
"""

from unittest.mock import patch, MagicMock
from moduli import sms_gateway
from moduli.bulk_contatori_email import build_nota_invio


def test_normalize_phone_number():
    # Numeri italiani standard
    assert sms_gateway.normalize_phone_number('3471234567') == '+393471234567'
    assert sms_gateway.normalize_phone_number('011 025 2702') == '+390110252702'
    assert sms_gateway.normalize_phone_number('+39 347 123 4567') == '+393471234567'
    assert sms_gateway.normalize_phone_number('393471234567') == '+393471234567'
    assert sms_gateway.normalize_phone_number('00393471234567') == '+393471234567'
    # Numeri con caratteri speciali / spazi
    assert sms_gateway.normalize_phone_number('+39 (347) 123-4567') == '+393471234567'
    # Vuoto o non valido
    assert sms_gateway.normalize_phone_number('') == ''
    assert sms_gateway.normalize_phone_number('abc') == ''


def test_count_sms_parts():
    # GSM-7 standard (fino a 160 caratteri = 1 SMS)
    msg_breve = "Algeco Power: sostituzione contatore programmata."
    info = sms_gateway.count_sms_parts(msg_breve)
    assert info['is_unicode'] is False
    assert info['parts'] == 1
    assert info['length'] == len(msg_breve)

    # GSM-7 lungo oltre 160 caratteri (usa 153 car per segmento)
    msg_lungo_gsm = "A" * 170
    info_lungo = sms_gateway.count_sms_parts(msg_lungo_gsm)
    assert info_lungo['is_unicode'] is False
    assert info_lungo['parts'] == 2

    # Unicode (presenza di caratteri non compresi nel charset GSM-7, es. caratteri cinesi o speciali)
    msg_unicode = "Avviso sostituzione contatore ⚡"
    info_uni = sms_gateway.count_sms_parts(msg_unicode)
    assert info_uni['is_unicode'] is True
    assert info_uni['parts'] == 1

    # Unicode lungo oltre 70 caratteri (usa 67 car per segmento)
    msg_unicode_lungo = "A" * 75 + "⚡"
    info_uni_lungo = sms_gateway.count_sms_parts(msg_unicode_lungo)
    assert info_uni_lungo['is_unicode'] is True
    assert info_uni_lungo['parts'] == 2


def test_load_save_sms_settings(tmp_path):
    settings_file = str(tmp_path / 'sms_settings.json')
    with patch('moduli.sms_gateway._SMS_SETTINGS_JSON', settings_file):
        # Default iniziale
        cfg = sms_gateway.load_sms_settings()
        assert cfg.get('provider') == 'android_sms_gateway'
        assert cfg.get('enabled') is False

        # Salvataggio e ricaricamento
        new_cfg = {
            'enabled': True,
            'provider': 'android_sms_gateway',
            'endpoint_url': 'https://api.sms-gate.app/3rdparty/v1/message',
            'auth_type': 'basic',
            'username': 'user123',
            'rate_limit_delay_seconds': 5
        }
        ok = sms_gateway.save_sms_settings(new_cfg)
        assert ok is True

        loaded = sms_gateway.load_sms_settings()
        assert loaded.get('enabled') is True
        assert loaded.get('provider') == 'android_sms_gateway'
        assert loaded.get('endpoint_url') == 'https://api.sms-gate.app/3rdparty/v1/message'
        assert loaded.get('auth_type') == 'basic'
        assert loaded.get('username') == 'user123'
        assert loaded.get('rate_limit_delay_seconds') == 5


def test_send_sms_disabled():
    settings = {'enabled': False, 'endpoint_url': 'http://localhost:8080'}
    ok, info, err = sms_gateway.send_sms('3471234567', 'Test', settings=settings)
    assert ok is False
    assert "disabilitato" in err.lower()


@patch('requests.post')
def test_send_sms_android_gateway_success(mock_post):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '{"id": "msg-12345"}'
    mock_resp.json.return_value = {"id": "msg-12345"}
    mock_post.return_value = mock_resp

    settings = {
        'enabled': True,
        'provider': 'android_sms_gateway',
        'endpoint_url': 'https://api.sms-gate.app/3rdparty/v1/message',
        'auth_type': 'basic',
        'username': 'testuser',
        'password': 'testpass',
        'timeout_seconds': 10
    }

    ok, info, err = sms_gateway.send_sms('3471234567', 'Test messaggio', settings=settings)
    assert ok is True
    assert err == ""
    assert info.get('gateway_id') == 'msg-12345'
    assert info.get('provider') == 'android_sms_gateway'

    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert kwargs['json']['phoneNumbers'] == ['+393471234567']
    assert kwargs['json']['message'] == 'Test messaggio'
    assert kwargs['auth'] == ('testuser', 'testpass')


@patch('requests.post')
def test_send_sms_generic_webhook_success(mock_post):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = 'OK'
    mock_post.return_value = mock_resp

    settings = {
        'enabled': True,
        'provider': 'generic_webhook',
        'endpoint_url': 'http://custom-webhook.local/sms',
        'auth_type': 'none',
        'timeout_seconds': 10
    }

    ok, info, err = sms_gateway.send_sms('3471234567', 'Messaggio Webhook', settings=settings)
    assert ok is True
    assert info.get('provider') == 'generic_webhook'


def test_nota_invio_sms():
    # Verifica che build_nota_invio supporti il tipo 'sms'
    nota = build_nota_invio('sms', data_intervento='15/09/2026')
    assert "mandato SMS" in nota
    assert "15/09" in nota


@patch('moduli.sms_gateway.send_sms')
def test_test_sms_gateway_helper(mock_send):
    mock_send.return_value = (True, {'parts': 1}, '')
    ok, msg = sms_gateway.test_sms_gateway({'enabled': True}, '+393471234567')
    assert ok is True
    assert "successo" in msg

    mock_send.return_value = (False, {}, 'Connection timeout')
    ok, msg = sms_gateway.test_sms_gateway({'enabled': True}, '+393471234567')
    assert ok is False
    assert "Connection timeout" in msg


@patch('routes.distinte.send_sms')
@patch('routes.distinte.registra_nota_su_db')
@patch('routes.distinte.load_sms_settings')
def test_route_invia_singolo_sms(mock_load, mock_reg, mock_send, db_isolato):
    from flask import Flask
    from routes.distinte import bp as distinte_bp

    mock_load.return_value = {'enabled': True, 'endpoint_url': 'http://127.0.0.1:8080'}
    mock_send.return_value = (True, {'parts': 1, 'gateway_id': 'id-123'}, '')

    app = Flask(__name__)
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test-secret'
    app.register_blueprint(distinte_bp)

    client = app.test_client()
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['user'] = 'testuser'

    # Test invio con payload flat
    res = client.post('/api/distinte/invia-singolo-sms', json={
        'pod': 'IT001E12345678',
        'telefono': '3471234567',
        'testo': 'Test avviso SMS',
        'nome': 'Mario Rossi'
    })
    assert res.status_code == 200
    data = res.get_json()
    assert data['ok'] is True
    assert data['pod'] == 'IT001E12345678'
    assert data['parts'] == 1
    mock_send.assert_called_once()
    mock_reg.assert_called_once()


def test_unified_sms_gateway_modal_assets():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    
    # 1. sms_gateway_modal.js
    js_content = (root / 'static' / 'sms_gateway_modal.js').read_text(encoding='utf-8')
    assert 'sms-modal-overlay' in js_content
    assert 'position: fixed' in js_content
    assert 'display: none' in js_content
    assert 'display: none;' in js_content  # inline style sul container
    assert 'SmsGatewayModal' in js_content
    assert 'window.apriSmsSettings' in js_content

    # 2. admin.html
    admin_content = (root / 'templates_router' / 'admin.html').read_text(encoding='utf-8')
    assert '/static/sms_gateway_modal.js' in admin_content
    assert '.sms-modal-overlay' in admin_content
    assert 'apriSmsSettings(' in admin_content

    # 3. distinte.html
    distinte_content = (root / 'templates' / 'distinte.html').read_text(encoding='utf-8')
    assert '/static/sms_gateway_modal.js' in distinte_content
    assert '.sms-modal-overlay' in distinte_content
    assert 'apriDistinteSmsConfigModal(' in distinte_content


