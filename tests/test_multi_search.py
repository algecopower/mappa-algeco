# -*- coding: utf-8 -*-
"""Test per la ricerca multipla POD ed Eneltel nel modal Ricerca Utente."""

import subprocess
from pathlib import Path


def test_mappa_template_contains_multi_search_elements():
    template_path = Path("/opt/mappa-algeco/templates/mappa.html")
    content = template_path.read_text(encoding="utf-8")

    # POD multi search elements
    assert 'id="modalMultiSearchPodToggle"' in content
    assert 'id="modalSearchPodMulti"' in content
    assert 'id="modalSearchPodBadge"' in content
    assert "toggleMultiSearch('pod')" in content

    # Eneltel multi search elements
    assert 'id="modalMultiSearchEneltelToggle"' in content
    assert 'id="modalSearchEneltelMulti"' in content
    assert 'id="modalSearchEneltelBadge"' in content
    assert "toggleMultiSearch('eneltel')" in content

    # Check labels
    assert "Ricerca multipla" in content


def test_mappa_css_contains_multi_search_styles():
    css_path = Path("/opt/mappa-algeco/static/mappa.css")
    css = css_path.read_text(encoding="utf-8")

    assert ".multi-search-toggle" in css
    assert ".multi-toggle-track" in css
    assert ".multi-toggle-thumb" in css
    assert ".multi-search-textarea" in css
    assert ".multi-tokens-count-badge" in css
    assert ".multi-search-missing-box" in css
    assert ".missing-token-pill" in css


def test_js_multi_search_tokens_parser_and_filtering_logic():
    # Eseguiamo uno script Node che testa direttamente la logica JS implementata in mappa.js
    node_script = r"""
    const fs = require('fs');
    const content = fs.readFileSync('/opt/mappa-algeco/static/mappa.js', 'utf-8');

    // Estrai la definizione di parseMultiSearchTokens
    const fnMatch = content.match(/function parseMultiSearchTokens\([\s\S]*?\n\}/);
    if (!fnMatch) {
        console.error("parseMultiSearchTokens non trovata!");
        process.exit(1);
    }
    eval(fnMatch[0]);

    // Test 1: Separati da spazio
    const resSpazio = parseMultiSearchTokens("IT001E12345678 IT001E87654321 IT001E99999999");
    if (resSpazio.length !== 3 || resSpazio[0] !== 'it001e12345678' || resSpazio[1] !== 'it001e87654321') {
        throw new Error("Fallito test spazio: " + JSON.stringify(resSpazio));
    }

    // Test 2: Separati da a capo (incolla da Excel)
    const resExcel = parseMultiSearchTokens("IT001E11111111\r\nIT001E22222222\nIT001E33333333");
    if (resExcel.length !== 3 || resExcel[2] !== 'it001e33333333') {
        throw new Error("Fallito test excel newline: " + JSON.stringify(resExcel));
    }

    // Test 3: Separati da virgola o punto e virgola con spazi
    const resVirgola = parseMultiSearchTokens("12345678, 23456789; 34567890");
    if (resVirgola.length !== 3 || resVirgola[1] !== '23456789') {
        throw new Error("Fallito test virgola: " + JSON.stringify(resVirgola));
    }

    // Test 4: Deduplicazione mantenendo l'ordine
    const resDedup = parseMultiSearchTokens("IT001E1111 IT001E2222 IT001E1111");
    if (resDedup.length !== 2) {
        throw new Error("Fallito test deduplica: " + JSON.stringify(resDedup));
    }

    // Test 5: Match tokens logic
    const tokens = ['it001e11111111', '22222222'];
    const matchesTokens = (fieldValue, toks) => {
        if (!fieldValue || toks.length === 0) return false;
        const val = String(fieldValue).toLowerCase().trim();
        return toks.some(t => {
            return val === t || (t.length >= 3 && val.includes(t)) || (val.length >= 3 && t.includes(val));
        });
    };

    if (!matchesTokens('IT001E11111111', tokens)) throw new Error("Match fallito per esatto");
    if (!matchesTokens('IT001E22222222', tokens)) throw new Error("Match fallito per inclusione");
    if (matchesTokens('IT001E99999999', tokens)) throw new Error("Match non doveva essere vero");

    console.log("OK");
    """

    res = subprocess.run(
        ["node", "-e", node_script],
        capture_output=True,
        text=True,
        check=True
    )
    assert "OK" in res.stdout


def test_find_inactive_lcl_contatori_isolated_db(db_isolato):
    """Verifica che db.find_inactive_lcl_contatori restituisca solo contatori
    di LCL disabilitate senza duplicati attivi."""
    db = db_isolato
    conn = db.get_conn()

    disabled_lcls = ["LCL_DISAB_100", "LCL_DISAB_200"]

    # Inseriamo record di test:
    # 1. Contatore INATTIVO senza alcun duplicato
    conn.execute(
        """INSERT INTO filone (eneltel_key, lcl, sheets_row, pod, nominativo, comune, row_hash, last_remote_pull_ts)
           VALUES ('EN_INACT_1', 'LCL_DISAB_100', 1, 'IT001E_INACT_1', 'MARIO ROSSI', 'TORINO', 'h1', 1000)"""
    )
    # 2. Contatore INATTIVO con duplicato in LCL ATTIVA
    conn.execute(
        """INSERT INTO filone (eneltel_key, lcl, sheets_row, pod, nominativo, comune, row_hash, last_remote_pull_ts)
           VALUES ('EN_DUP_DIS', 'LCL_DISAB_100', 2, 'IT001E_DUP_ACTIVE', 'LUIGI VERDI', 'TORINO', 'h2', 1000)"""
    )
    conn.execute(
        """INSERT INTO filone (eneltel_key, lcl, sheets_row, pod, nominativo, comune, row_hash, last_remote_pull_ts)
           VALUES ('EN_DUP_ACT', 'LCL_ATTIVA_999', 3, 'IT001E_DUP_ACTIVE', 'LUIGI VERDI', 'TORINO', 'h3', 1000)"""
    )
    # 3. Contatore ATTIVO senza duplicati
    conn.execute(
        """INSERT INTO filone (eneltel_key, lcl, sheets_row, pod, nominativo, comune, row_hash, last_remote_pull_ts)
           VALUES ('EN_ACT_ONLY', 'LCL_ATTIVA_999', 4, 'IT001E_ACT_ONLY', 'GIUSEPPE BIANCHI', 'TORINO', 'h4', 1000)"""
    )
    # 4. Contatore INATTIVO con nominativo specifico
    conn.execute(
        """INSERT INTO filone (eneltel_key, lcl, sheets_row, pod, nominativo, comune, row_hash, last_remote_pull_ts)
           VALUES ('EN_NOM_INACT', 'LCL_DISAB_200', 5, 'IT001E_NOM_5', 'FRANCESCA BALLAERA', 'NICHELINO', 'h5', 1000)"""
    )

    # Test A: LCL disabilitati vuoto -> ritorna vuoto
    assert db.find_inactive_lcl_contatori(pod_list=['IT001E_INACT_1'], disabled_lcls=[]) == []

    # Test B: POD inattivo senza duplicati -> deve essere trovato
    res_b = db.find_inactive_lcl_contatori(pod_list=['IT001E_INACT_1'], disabled_lcls=disabled_lcls)
    assert len(res_b) == 1
    assert res_b[0]['pod'] == 'IT001E_INACT_1'
    assert res_b[0]['lcl'] == 'LCL_DISAB_100'

    # Test C: POD con duplicato attivo -> NON deve essere trovato (ha duplicato attivo)
    res_c = db.find_inactive_lcl_contatori(pod_list=['IT001E_DUP_ACTIVE'], disabled_lcls=disabled_lcls)
    assert len(res_c) == 0

    # Test D: POD attivo -> non appartiene a LCL disabilitata -> NON deve essere trovato
    res_d = db.find_inactive_lcl_contatori(pod_list=['IT001E_ACT_ONLY'], disabled_lcls=disabled_lcls)
    assert len(res_d) == 0

    # Test E: Ricerca multipla (un inattivo senza dup, uno con dup attivo, uno non esistente)
    res_e = db.find_inactive_lcl_contatori(
        pod_list=['IT001E_INACT_1', 'IT001E_DUP_ACTIVE', 'IT001E_NON_EXISTENT'],
        disabled_lcls=disabled_lcls
    )
    assert len(res_e) == 1
    assert res_e[0]['pod'] == 'IT001E_INACT_1'

    # Test F: Ricerca per Nominativo inattivo
    res_f = db.find_inactive_lcl_contatori(nominativo='BALLAERA', disabled_lcls=disabled_lcls)
    assert len(res_f) == 1
    assert res_f[0]['eneltel_key'] == 'EN_NOM_INACT'
    assert res_f[0]['lcl'] == 'LCL_DISAB_200'


def test_inactive_lcl_api_endpoint(monkeypatch, db_isolato):
    """Test dell'endpoint Flask /api/contatori/check-inactive-lcl."""
    from flask import Flask
    import routes.contatori as contatori_mod
    import config

    db = db_isolato
    conn = db.get_conn()
    conn.execute(
        """INSERT INTO filone (eneltel_key, lcl, sheets_row, pod, nominativo, comune, row_hash, last_remote_pull_ts)
           VALUES ('EN_API_1', 'LCL_DIS_API', 1, 'IT001E_API_TEST', 'TEST INATTIVO', 'TORINO', 'h1', 1000)"""
    )
    monkeypatch.setattr(config, 'LCL_DISABILITATI', ['LCL_DIS_API'], raising=False)

    app = Flask(__name__)
    app.secret_key = 'test-secret-key'
    app.register_blueprint(contatori_mod.bp)
    app.config['TESTING'] = True

    with app.test_client() as client:
        # Non autenticato -> 401
        resp_anon = client.post('/api/contatori/check-inactive-lcl', json={'pod': 'IT001E_API_TEST'})
        assert resp_anon.status_code == 401

        # Autenticato
        with client.session_transaction() as sess:
            sess['user'] = 'test_admin'

        resp = client.post('/api/contatori/check-inactive-lcl', json={'pod': 'IT001E_API_TEST'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['total_inactive'] == 1
        assert len(data['inactive_found']) == 1
        assert data['inactive_found'][0]['pod'] == 'IT001E_API_TEST'
        assert data['inactive_found'][0]['lcl'] == 'LCL_DIS_API'

        # Test codice non presente
        resp_missing = client.post('/api/contatori/check-inactive-lcl', json={'pod': 'IT001E_NON_EXISTENT'})
        assert resp_missing.status_code == 200
        data_missing = resp_missing.get_json()
        assert data_missing['total_inactive'] == 0
        assert data_missing['inactive_found'] == []


def test_inactive_lcl_ui_elements_in_assets():
    """Verifica presenza degli stili CSS e logica JS per avvisi LCL disabilitate."""
    css = Path("/opt/mappa-algeco/static/mappa.css").read_text(encoding="utf-8")
    assert ".inactive-lcl-alert-box" in css
    assert ".inactive-lcl-badge" in css
    assert ".missing-token-pill--inactive-lcl" in css

    js = Path("/opt/mappa-algeco/static/mappa.js").read_text(encoding="utf-8")
    assert "checkInactiveLclForSearch" in js
    assert "renderInactiveLclAlertInResults" in js
    assert "/api/contatori/check-inactive-lcl" in js
    assert "mappa-toast--warn" in js

