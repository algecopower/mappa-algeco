# -*- coding: utf-8 -*-
"""Test per autenticazione e registry province dinamico."""
import importlib
import json


def test_get_available_provinces_legge_provinces_json(tmp_path, monkeypatch):
    from moduli import auth

    users_file = tmp_path / 'users.json'
    provinces_file = tmp_path / 'provinces.json'
    provinces_file.write_text(json.dumps({'LECCO': {}, 'NOVARA': {}}), encoding='utf-8')

    monkeypatch.setattr(auth, 'USERS_FILE', str(users_file))
    monkeypatch.setattr(auth, 'PROVINCES_FILE', str(provinces_file))
    auth = importlib.reload(auth)
    monkeypatch.setattr(auth, 'USERS_FILE', str(users_file))
    monkeypatch.setattr(auth, 'PROVINCES_FILE', str(provinces_file))

    assert auth.get_available_provinces() == ['LECCO', 'NOVARA']

    ok, msg, generated = auth.create_user('Mario', 'password-sicura')

    assert ok, msg
    assert generated is None
    users = auth.load_users()
    assert users['mario']['province'] == ['LECCO', 'NOVARA']
