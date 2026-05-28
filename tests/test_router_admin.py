# -*- coding: utf-8 -*-
"""Test per helper di validazione della dashboard admin del router."""


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


def test_server_cors_origins_includono_province_configurate(monkeypatch):
    """Il backend include in CORS anche province aggiunte via provinces.json."""
    import importlib
    import sys

    monkeypatch.setenv('_SERVER_CHILD', '1')
    monkeypatch.setenv('PROVINCIA', 'TORINO')
    sys.modules.pop('server', None)

    server = importlib.import_module('server')

    assert 'https://lecco.mappa-algeco.com' in server._CORS_ORIGINS
