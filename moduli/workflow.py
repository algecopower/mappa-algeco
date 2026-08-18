# -*- coding: utf-8 -*-
"""
Modulo per la gestione del Workflow Giornaliero (La mia Giornata).
Isolato per provincia: data/{provincia}/user_workflows.json
"""
import os
import json
import logging
from datetime import datetime

from config import PROVINCIA_ATTIVA

logger = logging.getLogger('mappa_algeco')

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve_prov(provincia=None) -> str:
    if provincia and str(provincia).strip():
        return str(provincia).strip().upper()
    return PROVINCIA_ATTIVA.upper()


def _get_workflow_file(provincia=None) -> str:
    prov = _resolve_prov(provincia).lower()
    return os.path.join(SCRIPT_DIR, 'data', prov, 'user_workflows.json')


def _load_workflows(provincia=None):
    filepath = _get_workflow_file(provincia)
    if not os.path.exists(filepath):
        # Migrazione da vecchio file globale se presente
        old_file = os.path.join(SCRIPT_DIR, 'data', 'user_workflows.json')
        if os.path.exists(old_file):
            try:
                with open(old_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                _save_workflows(data, provincia)
                return data
            except Exception:
                pass
        return {}
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Errore lettura {filepath}: {e}")
        return {}


def _save_workflows(data, provincia=None):
    filepath = _get_workflow_file(provincia)
    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        logger.error(f"Errore scrittura {filepath}: {e}")


def get_user_workflow(username, provincia=None):
    """
    Ritorna il workflow giornaliero per l'utente nella provincia indicata.
    Se la data salvata è diversa dalla data odierna, resetta le spunte.
    """
    prov = _resolve_prov(provincia)
    data = _load_workflows(prov)
    user_data = data.get(username, {
        "template": [
            {"id": "t1", "text": "Sync filone / Scarica dati", "time": "08:00"},
            {"id": "t2", "text": "Controllo appuntamenti in scadenza", "time": "12:00"},
            {"id": "t3", "text": "Chiamata clienti", "time": "16:30"}
        ],
        "current_date": "",
        "current_state": {}
    })

    oggi = datetime.now().strftime('%Y-%m-%d')
    if user_data.get('current_date') != oggi:
        user_data['current_date'] = oggi
        user_data['current_state'] = {}
        data[username] = user_data
        _save_workflows(data, prov)

    return user_data


def update_workflow_state(username, task_id, is_completed, provincia=None):
    """Aggiorna lo stato di completamento di un task del workflow per la provincia."""
    prov = _resolve_prov(provincia)
    data = _load_workflows(prov)
    user_data = get_user_workflow(username, prov)

    if 'current_state' not in user_data:
        user_data['current_state'] = {}

    user_data['current_state'][task_id] = is_completed
    data[username] = user_data
    _save_workflows(data, prov)
    return user_data


def update_workflow_template(username, new_template, provincia=None):
    """Aggiorna la struttura (template) delle procedure standard dell'utente per la provincia."""
    prov = _resolve_prov(provincia)
    data = _load_workflows(prov)
    user_data = get_user_workflow(username, prov)

    user_data['template'] = new_template
    data[username] = user_data
    _save_workflows(data, prov)
    return user_data
