# -*- coding: utf-8 -*-
"""
Modulo per la gestione del Workflow Giornaliero (La mia Giornata).
"""
import os
import json
import logging
from datetime import datetime

logger = logging.getLogger('mappa_algeco')

WORKFLOW_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'user_workflows.json')

def _load_workflows():
    if not os.path.exists(WORKFLOW_FILE):
        return {}
    try:
        with open(WORKFLOW_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Errore lettura {WORKFLOW_FILE}: {e}")
        return {}

def _save_workflows(data):
    try:
        os.makedirs(os.path.dirname(WORKFLOW_FILE), exist_ok=True)
        with open(WORKFLOW_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        logger.error(f"Errore scrittura {WORKFLOW_FILE}: {e}")

def get_user_workflow(username):
    """
    Ritorna il workflow giornaliero per l'utente.
    Se la data salvata è diversa dalla data odierna, resetta le spunte.
    """
    data = _load_workflows()
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
        _save_workflows(data)
        
    return user_data

def update_workflow_state(username, task_id, is_completed):
    """Aggiorna lo stato di completamento di un task del workflow."""
    data = _load_workflows()
    user_data = get_user_workflow(username)
    
    if 'current_state' not in user_data:
        user_data['current_state'] = {}
        
    user_data['current_state'][task_id] = is_completed
    data[username] = user_data
    _save_workflows(data)
    return user_data

def update_workflow_template(username, new_template):
    """Aggiorna la struttura (template) delle procedure standard dell'utente."""
    data = _load_workflows()
    user_data = get_user_workflow(username)
    
    user_data['template'] = new_template
    data[username] = user_data
    _save_workflows(data)
    return user_data
