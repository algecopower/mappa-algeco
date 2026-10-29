# -*- coding: utf-8 -*-
"""Test per moduli/auto_import.py e relative route in server.py e router.py."""
import hashlib
import json
import pytest

from moduli import auto_import


def test_extract_sheet_id_and_gid():
    # URL classico con edit?gid=...
    url1 = "https://docs.google.com/spreadsheets/d/1SXRlFDtagjNVpxfzEViMCEoi9cI6FBtt/edit?gid=229428995"
    sheet_id, gid = auto_import.extract_sheet_id_and_gid(url1)
    assert sheet_id == "1SXRlFDtagjNVpxfzEViMCEoi9cI6FBtt"
    assert gid == "229428995"

    # URL con edit#gid=...
    url2 = "https://docs.google.com/spreadsheets/d/1j2twW9aaYB7mak_6vnyr7oJcePPg-XyN/edit#gid=375972784"
    sheet_id, gid = auto_import.extract_sheet_id_and_gid(url2)
    assert sheet_id == "1j2twW9aaYB7mak_6vnyr7oJcePPg-XyN"
    assert gid == "375972784"

    # URL semplice senza gid
    url3 = "https://docs.google.com/spreadsheets/d/1j2twW9aaYB7mak_6vnyr7oJcePPg-XyN/edit"
    sheet_id, gid = auto_import.extract_sheet_id_and_gid(url3)
    assert sheet_id == "1j2twW9aaYB7mak_6vnyr7oJcePPg-XyN"
    assert gid is None

    # Solo sheet_id
    url4 = "1SXRlFDtagjNVpxfzEViMCEoi9cI6FBtt"
    sheet_id, gid = auto_import.extract_sheet_id_and_gid(url4)
    assert sheet_id == "1SXRlFDtagjNVpxfzEViMCEoi9cI6FBtt"
    assert gid is None

    # Errori
    with pytest.raises(ValueError):
        auto_import.extract_sheet_id_and_gid("")

    with pytest.raises(ValueError):
        auto_import.extract_sheet_id_and_gid("https://google.com/search")


def test_build_export_url():
    url = auto_import.build_export_url("12345", gid="999")
    assert url == "https://docs.google.com/spreadsheets/d/12345/export?format=xlsx&gid=999"

    url_nogid = auto_import.build_export_url("12345")
    assert url_nogid == "https://docs.google.com/spreadsheets/d/12345/export?format=xlsx"


def test_compute_sha256():
    data = b"hello test world"
    expected = hashlib.sha256(data).hexdigest()
    assert auto_import.compute_sha256(data) == expected


def test_auto_import_skip_if_unchanged(tmp_path, monkeypatch):
    test_content = b"PK\x03\x04" + b"X" * 600
    sha = hashlib.sha256(test_content).hexdigest()

    # Mock download_sheet_xlsx
    monkeypatch.setattr(auto_import, 'download_sheet_xlsx', lambda *a, **kw: test_content)

    # Mock status path
    status_file = tmp_path / "auto_import_test.json"
    monkeypatch.setattr(auto_import, 'get_status_file_path', lambda prov: str(status_file))

    # Pre-popola status con lo stesso hash
    status_data = {
        'last_import_ts': 1234567,
        'last_check_ts': 1234567,
        'hash': sha,
        'success': True,
        'skipped': False,
    }
    with open(status_file, 'w', encoding='utf-8') as f:
        json.dump(status_data, f)

    res = auto_import.esegui_auto_import(
        provincia='TEST',
        url='https://docs.google.com/spreadsheets/d/dummy/edit',
        force=False,
    )

    assert res['success'] is True
    assert res['skipped'] is True
    assert 'invariato' in res['message'].lower()


def test_auto_import_missing_url(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_import, 'get_google_sheet_config', lambda prov: {'google_sheet_url': ''})
    res = auto_import.esegui_auto_import(provincia='TEST_NO_URL', url=None)
    assert res['success'] is False
    assert 'Nessun URL' in res['error']
