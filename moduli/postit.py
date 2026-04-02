# -*- coding: utf-8 -*-
"""
Modulo Post-it — Promemoria persistenti sempre visibili.
Gestisce note/promemoria che restano fissati sulle pagine principali
(mappa, filone) come reminder costanti per gli operatori.
"""
import os
import json
import uuid
import threading
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POSTIT_FILE = os.path.join(SCRIPT_DIR, 'data', 'postit.json')

_lock = threading.Lock()

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


def _load_data() -> dict:
    if not os.path.exists(POSTIT_FILE):
        return {"notes": []}
    try:
        with open(POSTIT_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if "notes" not in data:
                data["notes"] = []
            return data
    except (json.JSONDecodeError, IOError):
        return {"notes": []}


def _save_data(data: dict) -> bool:
    try:
        os.makedirs(os.path.dirname(POSTIT_FILE), exist_ok=True)
        with open(POSTIT_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except IOError as e:
        print(f"[PostIt] Errore salvataggio: {e}")
        return False


def get_postit(provincia: str = None) -> list:
    """Restituisce tutti i post-it attivi, ordinati per priorità e data."""
    with _lock:
        data = _load_data()

    notes = data["notes"]

    # Filtra per provincia (o globali senza provincia)
    if provincia:
        prov_upper = provincia.upper()
        notes = [n for n in notes if not n.get("provincia") or n["provincia"] == prov_upper]

    # Ordina: priorità alta prima, poi per data creazione (recenti prima)
    priorita_ord = {'alta': 0, 'media': 1, 'bassa': 2}
    notes.sort(key=lambda n: (priorita_ord.get(n.get('priorita', 'media'), 1), n.get('creato_il', '')))

    return notes


def crea_postit(testo: str, categoria: str = 'generico', colore: str = 'giallo',
                priorita: str = 'media', provincia: str = None,
                scadenza: str = None, creato_da: str = 'sistema') -> dict:
    """Crea un nuovo post-it."""
    if not testo or not testo.strip():
        return None

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
        'provincia': provincia.upper() if provincia else None,
        'scadenza': scadenza,
        'creato_da': creato_da,
        'creato_il': datetime.now().isoformat(),
        'modificato_il': None,
    }

    with _lock:
        data = _load_data()
        data["notes"].append(nota)
        if not _save_data(data):
            return None

    return nota


def modifica_postit(note_id: str, **kwargs) -> dict:
    """Modifica un post-it esistente."""
    campi_ammessi = {'testo', 'categoria', 'colore', 'priorita', 'provincia', 'scadenza'}

    with _lock:
        data = _load_data()
        for nota in data["notes"]:
            if nota["id"] == note_id:
                for k, v in kwargs.items():
                    if k in campi_ammessi:
                        if k == 'provincia' and v:
                            v = v.upper()
                        nota[k] = v
                nota['modificato_il'] = datetime.now().isoformat()
                _save_data(data)
                return nota
    return None


def elimina_postit(note_id: str) -> bool:
    """Elimina un post-it."""
    with _lock:
        data = _load_data()
        prima = len(data["notes"])
        data["notes"] = [n for n in data["notes"] if n["id"] != note_id]
        if len(data["notes"]) < prima:
            return _save_data(data)
    return False


def get_conteggio() -> int:
    """Restituisce il numero di post-it attivi."""
    with _lock:
        data = _load_data()
    return len(data["notes"])


def crea_postit_da_attivita(attivita: dict, creato_da: str = 'sistema') -> dict:
    """Crea un post-it a partire da un'attività del calendario."""
    # Mappa categoria calendario → categoria post-it
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

    return crea_postit(
        testo=testo,
        categoria=cat,
        colore=colore,
        priorita='alta' if attivita.get('categoria') == 'scadenza' else 'media',
        provincia=attivita.get('provincia'),
        scadenza=attivita.get('data'),
        creato_da=creato_da,
    )


def sync_postit_a_calendario(nota: dict) -> dict:
    """Crea un'attività calendario a partire da un post-it. Restituisce l'attività creata."""
    from moduli.calendario import crea_attivita

    # Mappa categoria post-it → categoria calendario
    cat_map = {
        'assenza': 'promemoria',
        'scadenza': 'scadenza',
        'attivita': 'appuntamento',
        'importante': 'promemoria',
        'generico': 'altro',
    }
    cat_cal = cat_map.get(nota.get('categoria', ''), 'altro')
    data = nota.get('scadenza') or datetime.now().strftime('%Y-%m-%d')

    return crea_attivita(
        titolo=nota['testo'][:100],
        data=data,
        descrizione=f"[Da post-it {nota['id']}] {nota['testo']}",
        categoria=cat_cal,
        creato_da=nota.get('creato_da', 'sistema'),
        provincia=nota.get('provincia', ''),
    )
