# -*- coding: utf-8 -*-
"""
Modulo Post-it — Promemoria persistenti sempre visibili.
Gestisce note/promemoria isolati rigorosamente per provincia:
  data/{provincia}/postit.json
"""
import os
import uuid
import threading
from datetime import datetime

from .safe_json import json_read, json_write
from config import PROVINCIA_ATTIVA

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_lock = threading.Lock()

_POSTIT_DEFAULT = {"notes": []}

# Colori post-it disponibili
COLORI_POSTIT = {
    'giallo': '#fef3c7',
    'rosa': '#fce7f3',
    'verde': '#d1fae5',
    'azzurro': '#dbeafe',
    'arancio': '#ffedd5',
    'viola': '#ede9fe',
}

# Categorie con icone
CATEGORIE_POSTIT = {
    'assenza': {'label': 'Assenza operatore', 'icona': '🚫'},
    'scadenza': {'label': 'Scadenza', 'icona': '⏰'},
    'attivita': {'label': 'Attività', 'icona': '📋'},
    'importante': {'label': 'Importante', 'icona': '⚠️'},
    'generico': {'label': 'Generico', 'icona': '📌'},
}


def _resolve_prov(provincia=None) -> str:
    if provincia and str(provincia).strip():
        return str(provincia).strip().upper()
    return PROVINCIA_ATTIVA.upper()


def _get_postit_file(provincia=None) -> str:
    prov = _resolve_prov(provincia).lower()
    return os.path.join(SCRIPT_DIR, 'data', prov, 'postit.json')


def _migra_dati_storici_postit():
    old_postit = os.path.join(SCRIPT_DIR, 'data', 'postit.json')
    if os.path.exists(old_postit):
        try:
            data = json_read(old_postit, default=None)
            if data and isinstance(data, dict) and data.get('notes'):
                notes = data.get('notes', [])
                per_prov = {}
                for n in notes:
                    p = (n.get('provincia') or 'TORINO').strip().upper()
                    per_prov.setdefault(p, []).append(n)
                for p, items in per_prov.items():
                    target = _get_postit_file(p)
                    if not os.path.exists(target):
                        os.makedirs(os.path.dirname(target), exist_ok=True)
                        json_write(target, {"notes": items}, indent=2)
            os.rename(old_postit, old_postit + '.migrated')
        except Exception as e:
            print(f"[PostIt Migration] ⚠️ Errore migrazione post-it: {e}")


_migra_dati_storici_postit()


def _load_data(provincia=None) -> dict:
    path = _get_postit_file(provincia)
    data = json_read(path, default=None)
    if not data or not isinstance(data, dict) or "notes" not in data:
        return {"notes": []}
    return data


def _save_data(data: dict, provincia=None) -> bool:
    path = _get_postit_file(provincia)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return json_write(path, data, indent=2)


def get_postit(provincia: str = None) -> list:
    """Restituisce tutti i post-it attivi della provincia, ordinati per priorità e data."""
    prov = _resolve_prov(provincia)
    with _lock:
        data = _load_data(prov)

    notes = list(data.get("notes", []))
    priorita_ord = {'alta': 0, 'media': 1, 'bassa': 2}
    notes.sort(key=lambda n: (priorita_ord.get(n.get('priorita', 'media'), 1), n.get('creato_il', '')))
    return notes


def crea_postit(testo: str, categoria: str = 'generico', colore: str = 'giallo',
                priorita: str = 'media', provincia: str = None,
                scadenza: str = None, creato_da: str = 'sistema') -> dict:
    """Crea un nuovo post-it nella provincia attiva."""
    if not testo or not testo.strip():
        return None

    prov_resolved = _resolve_prov(provincia)

    if categoria not in CATEGORIE_POSTIT:
        categoria = 'generico'
    if colore not in COLORI_POSTIT:
        colore = 'giallo'
    if priorita not in ('alta', 'media', 'bassa'):
        priorita = 'media'

    nota = {
        'id': uuid.uuid4().hex[:8],
        'testo': testo.strip(),
        'categoria': categoria,
        'colore': colore,
        'priorita': priorita,
        'provincia': prov_resolved,
        'scadenza': scadenza,
        'creato_da': creato_da,
        'creato_il': datetime.now().isoformat(),
        'modificato_il': None,
    }

    with _lock:
        data = _load_data(prov_resolved)
        data["notes"].append(nota)
        if not _save_data(data, prov_resolved):
            return None

    return nota


def modifica_postit(note_id: str, provincia: str = None, **kwargs) -> dict:
    """Modifica un post-it esistente nella provincia attiva."""
    campi_ammessi = {'testo', 'categoria', 'colore', 'priorita', 'provincia', 'scadenza'}
    prov_resolved = _resolve_prov(provincia)

    with _lock:
        data = _load_data(prov_resolved)
        for nota in data.get("notes", []):
            if nota["id"] == note_id:
                for k, v in kwargs.items():
                    if k in campi_ammessi:
                        if k == 'provincia' and v:
                            v = v.upper()
                        nota[k] = v
                nota['modificato_il'] = datetime.now().isoformat()
                _save_data(data, prov_resolved)
                return nota
    return None


def elimina_postit(note_id: str, provincia: str = None) -> bool:
    """Elimina un post-it per la provincia attiva."""
    prov_resolved = _resolve_prov(provincia)
    with _lock:
        data = _load_data(prov_resolved)
        prima = len(data.get("notes", []))
        data["notes"] = [n for n in data.get("notes", []) if n["id"] != note_id]
        if len(data["notes"]) < prima:
            return _save_data(data, prov_resolved)
    return False


def get_conteggio(provincia: str = None) -> int:
    """Restituisce il numero di post-it attivi per la provincia."""
    prov_resolved = _resolve_prov(provincia)
    with _lock:
        data = _load_data(prov_resolved)
    return len(data.get("notes", []))


def crea_postit_da_attivita(attivita: dict, creato_da: str = 'sistema', provincia: str = None) -> dict:
    """Crea un post-it a partire da un'attività del calendario."""
    cat_map = {
        'appuntamento': 'attivita',
        'promemoria': 'generico',
        'scadenza': 'scadenza',
        'riunione': 'attivita',
        'boxbeat': 'importante',
        'altro': 'generico',
    }
    cat = cat_map.get(attivita.get('categoria', ''), 'generico')
    colore_map = {
        'appuntamento': 'azzurro',
        'scadenza': 'rosa',
        'riunione': 'viola',
        'boxbeat': 'arancio',
    }
    colore = colore_map.get(attivita.get('categoria', ''), 'giallo')

    testo = attivita.get('titolo', '')
    desc = attivita.get('descrizione', '')
    if desc:
        testo += '\n' + desc

    prov_resolved = _resolve_prov(provincia or attivita.get('provincia'))

    return crea_postit(
        testo=testo,
        categoria=cat,
        colore=colore,
        priorita='alta' if attivita.get('categoria') == 'scadenza' else 'media',
        provincia=prov_resolved,
        scadenza=attivita.get('data'),
        creato_da=creato_da,
    )


def sync_postit_a_calendario(nota: dict, provincia: str = None) -> dict:
    """Crea un'attività calendario a partire da un post-it. Restituisce l'attività creata."""
    from moduli.calendario import crea_attivita

    cat_map = {
        'assenza': 'promemoria',
        'scadenza': 'scadenza',
        'attivita': 'appuntamento',
        'importante': 'promemoria',
        'generico': 'altro',
    }
    cat_cal = cat_map.get(nota.get('categoria', ''), 'altro')
    data = nota.get('scadenza') or datetime.now().strftime('%Y-%m-%d')
    prov_resolved = _resolve_prov(provincia or nota.get('provincia'))

    return crea_attivita(
        titolo=nota['testo'][:100],
        data=data,
        descrizione=f"[Da post-it {nota['id']}] {nota['testo']}",
        categoria=cat_cal,
        creato_da=nota.get('creato_da', 'sistema'),
        provincia=prov_resolved,
    )
