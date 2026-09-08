# -*- coding: utf-8 -*-
"""
Test per la configurazione SMTP unificata e il motore di invio bulk email.
Verifica:
- moduli/smtp.py (test_smtp_connection, test_send_email)
- server.py (endpoint /api/smtp-settings, /api/smtp-test, /api/smtp-test-email, admin)
- router.py (proxying smtp-test e smtp-test-email)
- static/smtp_config_modal.js e static/bulk-email-preview.js
"""

from unittest.mock import patch, MagicMock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_smtp_connection_missing_host():
    from moduli.smtp import test_smtp_connection
    ok, msg = test_smtp_connection({})
    assert ok is False
    assert 'Host' in msg or 'mancanti' in msg


def test_smtp_connection_success():
    from moduli.smtp import test_smtp_connection
    mock_server = MagicMock()
    with patch('moduli.smtp.connect_smtp', return_value=mock_server):
        ok, msg = test_smtp_connection({
            'host': 'smtp.example.com',
            'port': 465,
            'username': 'user@example.com',
            'password': 'secret'
        })
        assert ok is True
        assert 'successo' in msg.lower()


def test_smtp_test_send_email():
    from moduli.smtp import test_send_email
    with patch('moduli.smtp.send_smtp_message', return_value=(True, '')):
        ok, msg = test_send_email(
            {
                'host': 'smtp.example.com',
                'port': 465,
                'username': 'user@example.com',
                'password': 'secret',
                'from_email': 'test@example.com'
            },
            recipient='destinatario@example.com',
            prov='TORINO'
        )
        assert ok is True
        assert 'destinatario@example.com' in msg


def test_smtp_endpoints_server_py(db_isolato, monkeypatch):
    monkeypatch.setenv('_SERVER_CHILD', '1')
    from server import app
    client = app.test_client()

    with client.session_transaction() as sess:
        sess['user'] = {'username': 'operatore', 'role': 'admin', 'province': ['TORINO']}
        sess['csrf_token'] = 'test-token'

    csrf_headers = {'X-CSRF-Token': 'test-token'}

    # GET /api/smtp-settings
    with patch('server.load_smtp_settings', return_value={'host': 'smtps.aruba.it', 'password': 'pwd'}):
        res = client.get('/api/smtp-settings')
        assert res.status_code == 200
        data = res.get_json()
        assert data['host'] == 'smtps.aruba.it'
        assert data['password_set'] is True
        assert data['password'] == ''

    # POST /api/smtp-test
    with patch('server.test_smtp_connection', return_value=(True, 'Connesso!')):
        res = client.post('/api/smtp-test', json={'host': 'smtps.aruba.it', 'username': 'user'}, headers=csrf_headers)
        assert res.status_code == 200
        assert res.get_json()['ok'] is True

    # POST /api/smtp-test-email
    with patch('server.test_send_email', return_value=(True, 'Inviata!')):
        res = client.post('/api/smtp-test-email', json={
            'email': 'dest@example.com',
            'host': 'smtps.aruba.it'
        }, headers=csrf_headers)
        assert res.status_code == 200
        assert res.get_json()['ok'] is True


def test_router_smtp_proxy():
    import router
    with patch.object(router, '_admin_check', return_value=None), \
         patch.object(router, '_admin_csrf_check', return_value=None):
        with patch.object(router, '_admin_proxy_simple', return_value=('{"ok":true}', 200, {'Content-Type': 'application/json'})) as mock_proxy:
            client = router.app.test_client()
            res = client.post('/api/provinces/torino/smtp-test', headers={'Host': 'admin.mappa-algeco.com'}, json={'settings': {}})
            assert res.status_code == 200
            mock_proxy.assert_called_once_with('torino', '/api/admin/smtp-test', 'POST')


def test_static_assets_smtp_modal():
    modal_js = (ROOT / 'static' / 'smtp_config_modal.js').read_text(encoding='utf-8')
    assert 'SmtpConfigModal' in modal_js
    assert 'applyPreset' in modal_js
    assert 'aruba_pec' in modal_js
    assert 'aruba_ord' in modal_js
    assert 'm365' in modal_js
    assert 'gmail' in modal_js
    assert 'testConnection' in modal_js
    assert 'sendTestEmail' in modal_js
    assert 'togglePasswordVisibility' in modal_js
    assert 'smtp-presets-grid' in modal_js
    assert 'smtp-grid-host-port-sec' in modal_js
    assert 'smtp-grid-tests' in modal_js
    assert '●●●●●●●● (già salvata)' in modal_js

    preview_js = (ROOT / 'static' / 'bulk-email-preview.js').read_text(encoding='utf-8')
    assert 'createBulkRunner' in preview_js
    assert '_handleSmtpErrorOnTheFly' in preview_js
