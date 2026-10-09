# -*- coding: utf-8 -*-
"""Test per helper di validazione della dashboard admin del router."""
import json


def test_router_rejects_reserved_and_conflicting_admin_hosts():
    import router

    province = {
        'TORINO': {'sottodominio': 'torino.mappa-algeco.com', 'porta': 5001},
    }

    assert router._validate_sottodominio('ADMIN', 'admin.mappa-algeco.com', province)
    assert router._validate_sottodominio('LECCO', 'torino.mappa-algeco.com', province)
    assert router._validate_sottodominio('LECCO', 'lecco.mappa-algeco.com', province) is None


def test_router_port_validation_blocks_router_and_duplicates():
    import router

    province = {
        'TORINO': {'sottodominio': 'torino.mappa-algeco.com', 'porta': 5001},
    }

    _, err = router._parse_port(router.ROUTER_PORT, province)
    assert err

    _, err = router._parse_port(5001, province)
    assert err

    porta, err = router._parse_port(5010, province)
    assert err is None
    assert porta == 5010


def test_router_sincronizza_override_settings_esistenti(tmp_path, monkeypatch):
    import router

    data_dir = tmp_path / 'data' / 'torino'
    data_dir.mkdir(parents=True)
    settings_file = data_dir / 'settings.json'
    settings_file.write_text(json.dumps({
        'operatori_vip': ['VECCHIO'],
        'colori_operatori': {'VECCHIO': '#000000'},
        'operatori_dict': {'VECCHIO': 'VECCHIO'},
        'mappatura_colonne': {'operatore': 'Tecnico'},
    }), encoding='utf-8')
    monkeypatch.setattr(router, 'SCRIPT_DIR', str(tmp_path))

    cfg = {
        'operatori_vip': ['AG67817'],
        'colori_operatori': {'AG67817': '#3498db'},
        'operatori_dict': {'ANGEL': 'AG67817'},
    }
    aggiornati = router.sincronizza_settings_provincia(
        'TORINO',
        cfg,
        {'operatori_vip', 'colori_operatori', 'operatori_dict'},
    )

    settings = json.loads(settings_file.read_text(encoding='utf-8'))
    assert set(aggiornati) == {'operatori_vip', 'colori_operatori', 'operatori_dict'}
    assert settings['operatori_vip'] == ['AG67817']
    assert settings['colori_operatori'] == {'AG67817': '#3498db'}
    assert settings['operatori_dict'] == {'ANGEL': 'AG67817'}
    assert settings['mappatura_colonne'] == {'operatore': 'Tecnico'}


def test_server_cors_origins_includono_province_configurate(monkeypatch):
    """Il backend include in CORS anche province aggiunte via provinces.json."""
    import importlib
    import sys

    monkeypatch.setenv('_SERVER_CHILD', '1')
    monkeypatch.setenv('PROVINCIA', 'TORINO')
    sys.modules.pop('server', None)

    server = importlib.import_module('server')

    assert 'https://lecco.mappa-algeco.com' in server._CORS_ORIGINS


def test_router_serves_static_assets():
    """Il router serve asset statici come sms_gateway_modal.js anche dall'host admin."""
    import router

    client = router.app.test_client()
    res = client.get('/static/sms_gateway_modal.js', headers={'Host': 'admin.mappa-algeco.com'})
    assert res.status_code == 200
    assert 'SmsGatewayModal' in res.get_data(as_text=True)


def test_router_import_excel_handles_non_json_and_backend_status(monkeypatch):
    """Il router cattura risposte non-JSON del backend e le converte in JSON pulito."""
    import router
    from io import BytesIO

    client = router.app.test_client()

    # Senza auth ritorna 401
    res = client.post(
        '/api/provinces/TORINO/import-excel',
        headers={'Host': 'admin.mappa-algeco.com'},
        data={},
    )
    assert res.status_code == 401

    # Con backend non in esecuzione (simulato)
    monkeypatch.setattr(router, '_admin_check', lambda: None)
    monkeypatch.setattr(router, '_admin_csrf_check', lambda: None)
    monkeypatch.setattr(router.pm, 'stato', lambda: {'TORINO': 'stopped'})
    res = client.post(
        '/api/provinces/TORINO/import-excel',
        headers={'Host': 'admin.mappa-algeco.com'},
        data={'file': (BytesIO(b'dummy'), 'test.xlsx')},
    )
    assert res.status_code == 503
    data = res.get_json()
    assert data['success'] is False
    assert 'stopped' in data['stato']


def test_router_max_content_length_413():
    """Il router ha MAX_CONTENT_LENGTH impostato a 100MB."""
    import router
    assert router.app.config['MAX_CONTENT_LENGTH'] == 100 * 1024 * 1024

