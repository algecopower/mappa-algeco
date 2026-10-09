# -*- coding: utf-8 -*-
"""
Test per l'isolamento e la separazione completa dei dati tra province:
- Calendario
- Appuntamenti BoxBeat & Excel
- Post-it
- Workflow giornaliero
- Template distinte
- Backup
"""
import os
import json
from datetime import datetime

import config


def test_calendario_isolation(tmp_path, monkeypatch):
    """Verifica che le attività di una provincia siano isolate dalle altre."""
    import moduli.calendario as cal

    # Reindirizza SCRIPT_DIR su directory temporanea
    monkeypatch.setattr(cal, 'SCRIPT_DIR', str(tmp_path))

    # Crea attività per TORINO
    att_to = cal.crea_attivita(
        titolo="Riunione Torino",
        data="2026-09-01",
        ora="09:00",
        provincia="TORINO"
    )

    # Crea attività per PAVIA
    att_pv = cal.crea_attivita(
        titolo="Controllo Pavia",
        data="2026-09-01",
        ora="11:00",
        provincia="PAVIA"
    )

    # File su disco separati
    file_to = os.path.join(str(tmp_path), 'data', 'torino', 'calendario.json')
    file_pv = os.path.join(str(tmp_path), 'data', 'pavia', 'calendario.json')
    assert os.path.exists(file_to)
    assert os.path.exists(file_pv)

    # Verifica get_attivita per TORINO
    list_to = cal.get_attivita(provincia="TORINO")
    ids_to = [a['id'] for a in list_to]
    assert att_to['id'] in ids_to
    assert att_pv['id'] not in ids_to

    # Verifica get_attivita per PAVIA
    list_pv = cal.get_attivita(provincia="PAVIA")
    ids_pv = [a['id'] for a in list_pv]
    assert att_pv['id'] in ids_pv
    assert att_to['id'] not in ids_pv


def test_boxbeat_excel_isolation(tmp_path, monkeypatch):
    """Verifica che la registrazione appuntamenti Excel sia separata per provincia."""
    import moduli.calendario as cal

    monkeypatch.setattr(cal, 'SCRIPT_DIR', str(tmp_path))

    # Registra appuntamento per TORINO
    cal.registra_appuntamento_excel(
        data_appuntamento="2026-09-05",
        orario="10:00",
        pod="IT001ETO12345",
        eneltel="ENEL_TO_1",
        nominativo="Cliente Torino",
        provincia="TORINO"
    )

    # Registra appuntamento per PAVIA
    cal.registra_appuntamento_excel(
        data_appuntamento="2026-09-05",
        orario="14:00",
        pod="IT001EPV67890",
        eneltel="ENEL_PV_1",
        nominativo="Cliente Pavia",
        provincia="PAVIA"
    )

    # File Excel generati nelle rispettive cartelle
    oggi = datetime.now().strftime('%Y-%m-%d')
    excel_to = os.path.join(str(tmp_path), 'data', 'torino', f'appuntamenti_{oggi}.xlsx')
    excel_pv = os.path.join(str(tmp_path), 'data', 'pavia', f'appuntamenti_{oggi}.xlsx')
    assert os.path.exists(excel_to)
    assert os.path.exists(excel_pv)

    # Verifica che il registro JSON sia separato
    bb_to = cal._load_excel_data("TORINO")
    bb_pv = cal._load_excel_data("PAVIA")
    assert any(x['pod'] == "IT001ETO12345" for x in bb_to.get(oggi, []))
    assert not any(x['pod'] == "IT001EPV67890" for x in bb_to.get(oggi, []))
    assert any(x['pod'] == "IT001EPV67890" for x in bb_pv.get(oggi, []))
    assert not any(x['pod'] == "IT001ETO12345" for x in bb_pv.get(oggi, []))


def test_workflow_isolation(tmp_path, monkeypatch):
    """Verifica che i workflow siano isolati per provincia."""
    import moduli.workflow as wf

    monkeypatch.setattr(wf, 'SCRIPT_DIR', str(tmp_path))

    wf.get_user_workflow("mario", provincia="TORINO")
    wf.update_workflow_state("mario", "t1", True, provincia="TORINO")

    wf_pv = wf.get_user_workflow("mario", provincia="PAVIA")
    # In Pavia il task t1 deve essere non completato
    assert wf_pv['current_state'].get('t1') is not True

    # In Torino deve essere completato
    wf_to_check = wf.get_user_workflow("mario", provincia="TORINO")
    assert wf_to_check['current_state'].get('t1') is True


def test_distinte_templates_isolation(tmp_path, monkeypatch):
    """Verifica che il salvataggio dei template sia per-provincia."""
    import routes.distinte as dist_routes

    monkeypatch.setattr(dist_routes, '_ROOT_DIR', str(tmp_path))

    # Scrivi default template in data/distinte_templates.json
    default_dir = os.path.join(str(tmp_path), 'data')
    os.makedirs(default_dir, exist_ok=True)
    with open(os.path.join(default_dir, 'distinte_templates.json'), 'w', encoding='utf-8') as f:
        json.dump({"settings": {"email_tecnico": "default@test.it"}}, f)

    # Leggi template per PAVIA (deve fare fallback sul default)
    monkeypatch.setattr(dist_routes, 'PROVINCIA_ATTIVA', 'PAVIA')
    t_pv = dist_routes._load_templates_data()
    assert t_pv['settings']['email_tecnico'] == "default@test.it"

    # Salva template modificato per PAVIA
    dist_routes._save_templates_data({"settings": {"email_tecnico": "pavia@test.it"}})

    # Ora PAVIA ha il suo file
    assert os.path.exists(os.path.join(str(tmp_path), 'data', 'pavia', 'distinte_templates.json'))
    t_pv_new = dist_routes._load_templates_data()
    assert t_pv_new['settings']['email_tecnico'] == "pavia@test.it"

    # TORINO invece legge ancora il default
    monkeypatch.setattr(dist_routes, 'PROVINCIA_ATTIVA', 'TORINO')
    t_to = dist_routes._load_templates_data()
    assert t_to['settings']['email_tecnico'] == "default@test.it"


def test_backup_directory_isolation():
    """Verifica che la cartella di backup sia confinata in backup/{provincia}."""
    import moduli.backup as mb
    assert mb.BACKUP_DIR.endswith(os.path.join('backup', config.PROVINCIA_ATTIVA.lower()))
