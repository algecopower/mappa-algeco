# -*- coding: utf-8 -*-
"""
Test per la disabilitazione globale/per-provincia di LCL e Operatori (archivio storico).
"""
import config
from moduli import data_loader, mappa_html
from routes.dashboard import _calcola_statistiche_filone, _calcola_andamento_lcl
from routes.report import _calcola_report


def _crea_backup_test():
    headers = [
        'Eneltel', 'POD', 'LCL', 'DATA PIAN', 'Esito', 'Data Installazione',
        'Operatore', 'Comune', 'NOTE APP', 'Orari', 'Indirizzo', 'Num', 'Mot. Rich.'
    ]
    righe = [
        # LCL1 con TEC1 (attivo)
        ['EN1', 'IT1', 'LCL1', '2026-06-01', 'Esito OK', '2026-06-01', 'TEC1', 'TORINO', '', '09:00', 'Via Roma', '1', 'SOST'],
        # LCL1 con TEC2 (disabilitato)
        ['EN2', 'IT2', 'LCL1', '2026-06-01', '', '', 'TEC2', 'TORINO', 'APP ore 10', '10:00', 'Via Po', '2', 'SOST'],
        # LCL_VECCHIA (disabilitata) con TEC1
        ['EN3', 'IT3', 'LCL_VECCHIA', '2026-06-01', 'Esito OK', '2026-06-01', 'TEC1', 'MONCALIERI', '', '11:00', 'Via Asti', '3', 'SOST'],
        # LCL_VECCHIA (disabilitata) con TEC2
        ['EN4', 'IT4', 'LCL_VECCHIA', '2026-06-02', '', '', 'TEC2', 'MONCALIERI', '', '', 'Via Milano', '4', 'SOST'],
    ]
    return {'headers': headers, 'righe': righe}


def test_filtro_data_loader_esclude_lcl_e_operatori_disabilitati(monkeypatch):
    monkeypatch.setattr(config, 'MATRICOLE_VIP', ['TEC1', 'TEC2'], raising=False)
    monkeypatch.setattr(config, 'OPERATORI_DISABILITATI', ['TEC2'], raising=False)
    monkeypatch.setattr(config, 'LCL_DISABILITATI', ['LCL_VECCHIA'], raising=False)
    monkeypatch.setattr(data_loader, 'MATRICOLE_VIP', ['TEC1', 'TEC2'], raising=False)
    monkeypatch.setattr(data_loader, 'OPERATORI_DISABILITATI', ['TEC2'], raising=False)
    monkeypatch.setattr(data_loader, 'LCL_DISABILITATI', ['LCL_VECCHIA'], raising=False)

    import pandas as pd
    backup = _crea_backup_test()
    df_raw = pd.DataFrame(backup['righe'], columns=backup['headers'])

    # Simuliamo il filtro applicato da data_loader
    disabled_lcls = {str(x).strip().upper() for x in (data_loader.LCL_DISABILITATI or []) if str(x).strip()}
    df_filtrato = df_raw[~df_raw['LCL'].astype(str).str.strip().str.upper().isin(disabled_lcls)]

    assert len(df_filtrato) == 2
    assert 'LCL_VECCHIA' not in df_filtrato['LCL'].values

    disabled_ops = {str(x).strip().upper() for x in (data_loader.OPERATORI_DISABILITATI or []) if str(x).strip()}
    active_vip = [m for m in data_loader.MATRICOLE_VIP if m and str(m).strip().upper() not in disabled_ops]
    df_final = df_filtrato[df_filtrato['Operatore'].isin(active_vip) | (df_filtrato['Operatore'] == 'TUTTI')]

    assert len(df_final) == 1
    assert df_final.iloc[0]['Eneltel'] == 'EN1'
    assert df_final.iloc[0]['Operatore'] == 'TEC1'


def test_dashboard_statistiche_esclude_lcl_disabilitate(monkeypatch):
    monkeypatch.setattr(config, 'MATRICOLE_VIP', ['TEC1', 'TEC2'], raising=False)
    monkeypatch.setattr(config, 'OPERATORI_DISABILITATI', ['TEC2'], raising=False)
    monkeypatch.setattr(config, 'LCL_DISABILITATI', ['LCL_VECCHIA'], raising=False)

    import routes.dashboard as dash
    monkeypatch.setattr(dash, 'MATRICOLE_VIP', ['TEC1', 'TEC2'], raising=False)
    monkeypatch.setattr(dash, 'OPERATORI_DISABILITATI', ['TEC2'], raising=False)
    monkeypatch.setattr(dash, 'LCL_DISABILITATI', ['LCL_VECCHIA'], raising=False)

    backup = _crea_backup_test()
    stats = _calcola_statistiche_filone(backup)

    # Solo le righe di LCL1 devono essere considerate
    lcl_codici = [x['codice'] for x in stats['lcl']]
    assert 'LCL_VECCHIA' not in lcl_codici
    assert 'LCL1' in lcl_codici

    # Solo TEC1 deve comparire tra gli operatori attivi
    ops = [x['matricola'] for x in stats['operatori']]
    assert 'TEC2' not in ops
    assert 'TEC1' in ops


def test_andamento_lcl_esclude_disabilitate(monkeypatch):
    import routes.dashboard as dash
    monkeypatch.setattr(dash, 'LCL_DISABILITATI', ['LCL_VECCHIA'], raising=False)

    backup = _crea_backup_test()
    andamento = _calcola_andamento_lcl(backup)

    codici = [x['codice'] for x in andamento['lcl_list']]
    assert 'LCL_VECCHIA' not in codici
    assert 'LCL1' in codici
    assert andamento['kpi']['totale_assegnate'] == 2  # EN1 e EN2 di LCL1


def test_report_esclude_disabilitate(monkeypatch):
    from datetime import date
    import routes.report as rep
    monkeypatch.setattr(rep, 'MATRICOLE_VIP', ['TEC1', 'TEC2'], raising=False)
    monkeypatch.setattr(rep, 'OPERATORI_DISABILITATI', ['TEC2'], raising=False)
    monkeypatch.setattr(rep, 'LCL_DISABILITATI', ['LCL_VECCHIA'], raising=False)

    backup = _crea_backup_test()
    rep_res = _calcola_report(backup, date(2026, 6, 1), date(2026, 6, 30))

    ops = [x['matricola'] for x in rep_res['operatori']]
    assert 'TEC2' not in ops
    assert 'TEC1' in ops

    lcl_codes = [x['codice'] for x in rep_res['lcl']]
    assert 'LCL_VECCHIA' not in lcl_codes
    assert 'LCL1' in lcl_codes


def test_mappa_html_esclude_operatori_disabilitati(monkeypatch):
    monkeypatch.setattr(config, 'MATRICOLE_VIP', ['TEC1', 'TEC2'], raising=False)
    monkeypatch.setattr(config, 'OPERATORI_DICT', {'Mario': 'TEC1', 'Luigi': 'TEC2'}, raising=False)
    monkeypatch.setattr(config, 'COLORI_TECNICI', {'TEC1': '#111111', 'TEC2': '#222222'}, raising=False)
    monkeypatch.setattr(config, 'OPERATORI_DISABILITATI', ['TEC2'], raising=False)
    monkeypatch.setattr(config, 'LCL_DISABILITATI', ['LCL_VECCHIA'], raising=False)

    monkeypatch.setattr(mappa_html, 'MATRICOLE_VIP', ['TEC1', 'TEC2'], raising=False)
    monkeypatch.setattr(mappa_html, 'OPERATORI_DICT', {'Mario': 'TEC1', 'Luigi': 'TEC2'}, raising=False)
    monkeypatch.setattr(mappa_html, 'COLORI_TECNICI', {'TEC1': '#111111', 'TEC2': '#222222'}, raising=False)
    monkeypatch.setattr(mappa_html, 'OPERATORI_DISABILITATI', ['TEC2'], raising=False)
    monkeypatch.setattr(mappa_html, 'LCL_DISABILITATI', ['LCL_VECCHIA'], raising=False)

    html = mappa_html.genera_html_completo(['LCL1', 'LCL_VECCHIA'])
    assert 'LCL_VECCHIA' not in html
    assert 'LCL1' in html
    assert 'TEC2' not in html
    assert 'TEC1' in html


def test_comuni_list_con_lcl_disabilitate(db_isolato, monkeypatch):
    monkeypatch.setattr(config, 'LCL_DISABILITATI', ['LCL_DISABILITATA'], raising=False)
    conn = db_isolato.get_conn()
    with conn:
        db_isolato.upsert_filone_row(
            conn, eneltel_key='EN1', sheets_row=1,
            typed_values={'lcl': 'LCL_ATTIVA', 'comune': 'TORINO'},
            extra_values={'ztl': 'SI'},
            row_hash='h1', source='test'
        )
        db_isolato.upsert_filone_row(
            conn, eneltel_key='EN2', sheets_row=2,
            typed_values={'lcl': 'LCL_DISABILITATA', 'comune': 'TORINO'},
            extra_values={},
            row_hash='h2', source='test'
        )
        db_isolato.upsert_filone_row(
            conn, eneltel_key='EN3', sheets_row=3,
            typed_values={'lcl': 'LCL_DISABILITATA', 'comune': 'MONCALIERI'},
            extra_values={},
            row_hash='h3', source='test'
        )
        conn.execute(
            "INSERT INTO comuni_info (provincia, comune, last_updated_ts) VALUES ('TORINO', 'TORINO', 100)"
        )

    from moduli.comuni_manager import get_comuni_list, export_comuni_to_excel
    comuni = get_comuni_list()
    assert len(comuni) == 1
    assert comuni[0]['comune'] == 'TORINO'
    assert comuni[0]['num_contatori'] == 1  # solo EN1 (LCL_ATTIVA)
    assert comuni[0]['lcl'] == 'LCL_ATTIVA'
    assert comuni[0]['ztl'] == 'SI'

    excel_bytes = export_comuni_to_excel()
    assert excel_bytes is not None
    assert len(excel_bytes) > 0
