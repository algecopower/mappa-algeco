# -*- coding: utf-8 -*-
"""
Modulo Calendario / Attività / Promemoria Appuntamento (BoxBeat).
Tutti i dati sono memorizzati in modo strettamente isolato per provincia:
  - Calendario: data/{provincia}/calendario.json
  - Registro Excel appuntamenti: data/{provincia}/boxbeat_excel.json
  - Registro chiavi viste: cache/cache_appuntamenti_visti_{provincia}.json
  - Impostazioni promemoria utente: data/{provincia}/promemoria_settings.json
  - File Excel generati: data/{provincia}/appuntamenti_{YYYY-MM-DD}.xlsx
"""
import os
import uuid
import threading
from datetime import datetime

from .safe_json import json_read, json_write
from config import PROVINCIA_ATTIVA

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Lock per accesso thread-safe
_lock = threading.Lock()
_excel_lock = threading.Lock()

_CAL_DEFAULT = {"attivita": []}


def _resolve_prov(provincia=None) -> str:
    """Risolve lo slug provincia (stringa uppercase), fallback su PROVINCIA_ATTIVA."""
    if provincia and str(provincia).strip():
        return str(provincia).strip().upper()
    return PROVINCIA_ATTIVA.upper()


def _get_calendario_file(provincia=None) -> str:
    prov = _resolve_prov(provincia).lower()
    return os.path.join(SCRIPT_DIR, 'data', prov, 'calendario.json')


def _get_boxbeat_excel_file(provincia=None) -> str:
    prov = _resolve_prov(provincia).lower()
    return os.path.join(SCRIPT_DIR, 'data', prov, 'boxbeat_excel.json')


def _get_chiavi_viste_file(provincia=None) -> str:
    prov = _resolve_prov(provincia).lower()
    return os.path.join(SCRIPT_DIR, 'cache', f'cache_appuntamenti_visti_{prov}.json')


def _get_promemoria_settings_file(provincia=None) -> str:
    prov = _resolve_prov(provincia).lower()
    return os.path.join(SCRIPT_DIR, 'data', prov, 'promemoria_settings.json')


# ============================================================================
# MIGRAZIONE AUTOMATICA DATI STORICI (una tantum)
# ============================================================================
def _migra_dati_storici_calendario():
    """Migra i vecchi file globali non separati dentro la rispettiva cartella per provincia."""
    old_cal = os.path.join(SCRIPT_DIR, 'data', 'calendario.json')
    if os.path.exists(old_cal):
        try:
            old_data = json_read(old_cal, default=None)
            if old_data and isinstance(old_data, dict) and old_data.get('attivita'):
                attivita = old_data.get('attivita', [])
                per_prov = {}
                for a in attivita:
                    p = (a.get('provincia') or 'TORINO').strip().upper()
                    per_prov.setdefault(p, []).append(a)

                for p, items in per_prov.items():
                    target_file = _get_calendario_file(p)
                    if not os.path.exists(target_file):
                        os.makedirs(os.path.dirname(target_file), exist_ok=True)
                        json_write(target_file, {"attivita": items}, indent=2)
                        print(f"[Calendario Migration] Migrati {len(items)} eventi in {target_file}")
            os.rename(old_cal, old_cal + '.migrated')
        except Exception as e:
            print(f"[Calendario Migration] ⚠️ Errore migrazione calendario: {e}")

    old_bb = os.path.join(SCRIPT_DIR, 'data', 'boxbeat_excel.json')
    if os.path.exists(old_bb):
        try:
            bb_data = json_read(old_bb, default=None)
            if bb_data and isinstance(bb_data, dict) and bb_data:
                target_bb = _get_boxbeat_excel_file('TORINO')
                if not os.path.exists(target_bb):
                    os.makedirs(os.path.dirname(target_bb), exist_ok=True)
                    json_write(target_bb, bb_data, indent=2)
            os.rename(old_bb, old_bb + '.migrated')
        except Exception as e:
            print(f"[Calendario Migration] ⚠️ Errore migrazione boxbeat_excel: {e}")


_migra_dati_storici_calendario()


# ============================================================================
# CALENDARIO ATTIVITÀ
# ============================================================================

def _load_data(provincia=None) -> dict:
    """Carica i dati del calendario della provincia dal file JSON."""
    path = _get_calendario_file(provincia)
    data = json_read(path, default=None)
    if not data or not isinstance(data, dict) or "attivita" not in data:
        return {"attivita": []}
    return data


def _save_data(data: dict, provincia=None) -> bool:
    """Salva i dati del calendario della provincia nel rispettivo file JSON (atomico)."""
    path = _get_calendario_file(provincia)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return json_write(path, data, indent=2)


def get_attivita(filtro_mese: str = None, filtro_utente: str = None,
                 mostra_completate: bool = True, provincia: str = None) -> list:
    """
    Restituisce la lista delle attività per la provincia specificata (default: PROVINCIA_ATTIVA).
    """
    prov = _resolve_prov(provincia)
    with _lock:
        data = _load_data(prov)

    attivita = list(data.get("attivita", []))

    # Applica filtri
    if filtro_mese:
        attivita = [a for a in attivita if a.get("data", "").startswith(filtro_mese)]

    if filtro_utente:
        attivita = [a for a in attivita if a.get("creato_da") == filtro_utente]

    if not mostra_completate:
        attivita = [a for a in attivita if not a.get("completata", False)]

    # Escludi SEMPRE i boxbeat completati dal calendario
    attivita = [a for a in attivita
                if not (a.get("categoria") == "boxbeat" and a.get("completata", False))]

    # Ordina per data e ora
    def sort_key(a):
        d = a.get("data", "9999-99-99")
        t = a.get("ora", "99:99")
        return (d, t)

    attivita.sort(key=sort_key)
    return attivita


def crea_attivita(titolo: str, data: str, ora: str = "",
                  ora_fine: str = "", descrizione: str = "",
                  categoria: str = "promemoria", creato_da: str = "sistema",
                  provincia: str = "") -> dict:
    """
    Crea una nuova attività associata rigorosamente alla provincia attiva.
    """
    if not titolo or not titolo.strip():
        raise ValueError("Il titolo è obbligatorio")
    if not data or not data.strip():
        raise ValueError("La data è obbligatoria")

    prov_resolved = _resolve_prov(provincia)

    attivita = {
        "id": str(uuid.uuid4())[:8],
        "titolo": titolo.strip(),
        "data": data.strip(),
        "ora": ora.strip() if ora else "",
        "ora_fine": ora_fine.strip() if ora_fine else "",
        "descrizione": descrizione.strip() if descrizione else "",
        "categoria": categoria if categoria in CATEGORIE_VALIDE else "promemoria",
        "provincia": prov_resolved,
        "creato_da": creato_da,
        "creato_il": datetime.now().isoformat(),
        "completata": False,
        "completata_da": None,
        "completata_il": None,
    }

    with _lock:
        data_file = _load_data(prov_resolved)
        data_file["attivita"].append(attivita)
        _save_data(data_file, prov_resolved)

    return attivita


def modifica_attivita(attivita_id: str, aggiornamenti: dict,
                      modificato_da: str = "sistema", provincia: str = None) -> dict:
    """
    Modifica un'attività esistente nella provincia attiva.
    """
    campi_modificabili = {"titolo", "data", "ora", "ora_fine", "descrizione",
                          "categoria", "provincia"}
    prov = _resolve_prov(provincia)

    with _lock:
        data = _load_data(prov)

        for att in data.get("attivita", []):
            if att["id"] == attivita_id:
                for campo, valore in aggiornamenti.items():
                    if campo in campi_modificabili:
                        att[campo] = valore.strip() if isinstance(valore, str) else valore

                att["modificato_da"] = modificato_da
                att["modificato_il"] = datetime.now().isoformat()

                _save_data(data, prov)
                return att

        raise ValueError(f"Attività '{attivita_id}' non trovata per {prov}")


def completa_attivita(attivita_id: str, completata_da: str = "sistema",
                      provincia: str = None) -> dict:
    """
    Segna un'attività come completata (o la riapre) per la provincia attiva.
    """
    prov = _resolve_prov(provincia)
    with _lock:
        data = _load_data(prov)

        for att in data.get("attivita", []):
            if att["id"] == attivita_id:
                if att.get("completata"):
                    att["completata"] = False
                    att["completata_da"] = None
                    att["completata_il"] = None
                else:
                    att["completata"] = True
                    att["completata_da"] = completata_da
                    att["completata_il"] = datetime.now().isoformat()

                _save_data(data, prov)
                return att

        raise ValueError(f"Attività '{attivita_id}' non trovata per {prov}")


def completa_multipli(ids: list, completata_da: str = "sistema",
                      forza_completa: bool = True, provincia: str = None) -> int:
    """
    Segna più attività come completate per la provincia attiva.
    """
    if not ids:
        return 0
    prov = _resolve_prov(provincia)
    completati = 0
    with _lock:
        data = _load_data(prov)
        for att in data.get("attivita", []):
            if att["id"] in ids:
                if forza_completa and att.get("completata"):
                    continue
                att["completata"] = True
                att["completata_da"] = completata_da
                att["completata_il"] = datetime.now().isoformat()
                completati += 1
        if completati > 0:
            _save_data(data, prov)
    return completati


def elimina_attivita(attivita_id: str, provincia: str = None) -> bool:
    """
    Elimina un'attività per la provincia attiva.
    """
    prov = _resolve_prov(provincia)
    with _lock:
        data = _load_data(prov)
        originale = len(data.get("attivita", []))
        data["attivita"] = [a for a in data.get("attivita", []) if a["id"] != attivita_id]

        if len(data["attivita"]) < originale:
            _save_data(data, prov)
            return True
        return False


def elimina_multipli(ids: list, provincia: str = None) -> int:
    """
    Elimina più attività in una sola operazione per la provincia attiva.
    """
    if not ids:
        return 0
    prov = _resolve_prov(provincia)
    ids_set = set(ids)
    with _lock:
        data = _load_data(prov)
        originale = len(data.get("attivita", []))
        data["attivita"] = [a for a in data.get("attivita", []) if a["id"] not in ids_set]
        eliminati = originale - len(data["attivita"])
        if eliminati > 0:
            _save_data(data, prov)
        return eliminati


def get_attivita_per_id(attivita_id: str, provincia: str = None) -> dict | None:
    """Restituisce una singola attività per ID nella provincia indicata."""
    prov = _resolve_prov(provincia)
    with _lock:
        data = _load_data(prov)

    for att in data.get("attivita", []):
        if att["id"] == attivita_id:
            return att
    return None


def get_riepilogo(provincia: str = None) -> dict:
    """
    Restituisce un riepilogo delle attività della provincia attiva.
    """
    prov = _resolve_prov(provincia)
    with _lock:
        data = _load_data(prov)

    oggi = datetime.now().strftime('%Y-%m-%d')
    mese_corrente = datetime.now().strftime('%Y-%m')

    attivita = data.get("attivita", [])

    # Escludi boxbeat completati
    attivita = [a for a in attivita
                if not (a.get("categoria") == "boxbeat" and a.get("completata", False))]

    totali = len(attivita)
    completate = sum(1 for a in attivita if a.get("completata"))
    oggi_count = sum(1 for a in attivita if a.get("data") == oggi and not a.get("completata"))
    mese_count = sum(1 for a in attivita if a.get("data", "").startswith(mese_corrente) and not a.get("completata"))
    scadute = sum(1 for a in attivita if a.get("data", "9999") < oggi and not a.get("completata"))

    return {
        "totali": totali,
        "completate": completate,
        "da_fare": totali - completate,
        "oggi": oggi_count,
        "mese_corrente": mese_count,
        "scadute": scadute,
    }


def pulisci_boxbeat_completati(giorni_retention: int = 7, provincia: str = None) -> int:
    """
    Rimuove dal file JSON della provincia i boxbeat completati più vecchi di N giorni.
    """
    from datetime import timedelta
    prov = _resolve_prov(provincia)
    soglia = (datetime.now() - timedelta(days=giorni_retention)).isoformat()

    with _lock:
        data = _load_data(prov)
        originale = len(data.get("attivita", []))
        data["attivita"] = [
            a for a in data.get("attivita", [])
            if not (
                a.get("categoria") == "boxbeat"
                and a.get("completata", False)
                and a.get("completata_il", "9999") < soglia
            )
        ]
        rimossi = originale - len(data["attivita"])
        if rimossi > 0:
            _save_data(data, prov)
            print(f"[Calendario {prov}] Pulizia: rimossi {rimossi} boxbeat completati (> {giorni_retention}gg)")
        return rimossi


CATEGORIE_VALIDE = {"appuntamento", "promemoria", "scadenza", "riunione", "altro", "boxbeat"}

COLORI_CATEGORIE = {
    "appuntamento": "#3498db",
    "promemoria": "#f39c12",
    "scadenza": "#e74c3c",
    "riunione": "#9b59b6",
    "altro": "#95a5a6",
    "boxbeat": "#e91e63",
}


# ============================================================================
# PROMEMORIA APPUNTAMENTO - Modello standardizzato
# ============================================================================

def _load_promemoria_settings(provincia=None) -> dict:
    """Carica le impostazioni promemoria per utente per la provincia attiva."""
    path = _get_promemoria_settings_file(provincia)
    data = json_read(path, default=None)
    if not data or not isinstance(data, dict):
        return {}
    return data


def _save_promemoria_settings(data: dict, provincia=None) -> bool:
    """Salva le impostazioni promemoria per utente (atomico)."""
    path = _get_promemoria_settings_file(provincia)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return json_write(path, data, indent=2)


def get_user_promemoria_settings(username: str, provincia: str = None) -> dict:
    """
    Restituisce le impostazioni promemoria per un utente nella provincia attiva.
    """
    settings = _load_promemoria_settings(provincia)
    user_settings = settings.get(username, {})
    return {
        "abilitato": user_settings.get("abilitato", True),
        "orario_scadenza": user_settings.get("orario_scadenza", "17:00"),
        "mostra_completate": user_settings.get("mostra_completate", False),
        "notifica_browser": user_settings.get("notifica_browser", True),
        "suono": user_settings.get("suono", True),
        "categorie": user_settings.get("categorie", []),
        "ultimo_alert_mostrato": user_settings.get("ultimo_alert_mostrato", ""),
        "aggiornato_il": user_settings.get("aggiornato_il", ""),
    }


def salva_user_promemoria_settings(username: str, nuove_settings: dict,
                                  provincia: str = None) -> bool:
    """
    Salva le impostazioni promemoria per un utente nella provincia attiva.
    """
    settings = _load_promemoria_settings(provincia)
    if username not in settings:
        settings[username] = {}

    campi_validi = {"abilitato", "orario_scadenza", "mostra_completate",
                    "notifica_browser", "suono", "categorie", "ultimo_alert_mostrato"}

    for campo, valore in nuove_settings.items():
        if campo in campi_validi:
            settings[username][campo] = valore

    settings[username]["aggiornato_il"] = datetime.now().isoformat()
    return _save_promemoria_settings(settings, provincia)


def crea_promemoria_boxbeat(data_appuntamento: str, ora_appuntamento: str,
                            pod: str = "", eneltel: str = "",
                            nominativo: str = "", via: str = "",
                            civico: str = "", comune: str = "",
                            note: str = "", provincia: str = "",
                            creato_da: str = "sistema",
                            origine: str = "manuale",
                            data_pianificata: str = "") -> dict:
    """
    Crea un promemoria Appuntamento standardizzato nella provincia attiva.
    """
    prov_resolved = _resolve_prov(provincia)

    parti_titolo = ["Appuntamento"]
    if nominativo:
        parti_titolo.append(nominativo.strip())
    if comune:
        parti_titolo.append(f"- {comune.strip()}")
    if ora_appuntamento:
        parti_titolo.append(f"ore {ora_appuntamento}")
    titolo = " ".join(parti_titolo)

    righe_desc = []
    if pod:
        righe_desc.append(f"POD: {pod}")
    if eneltel:
        righe_desc.append(f"ENELTEL: {eneltel}")
    if nominativo:
        righe_desc.append(f"Nominativo: {nominativo}")
    indirizzo_completo = ""
    if via:
        indirizzo_completo = via
        if civico:
            indirizzo_completo += f" {civico}"
    if indirizzo_completo:
        righe_desc.append(f"Indirizzo: {indirizzo_completo}")
    if comune:
        righe_desc.append(f"Comune: {comune}")
    if note:
        righe_desc.append(f"Note: {note}")
    righe_desc.append(f"Origine: {origine}")
    descrizione = "\n".join(righe_desc)

    attivita = {
        "id": str(uuid.uuid4())[:8],
        "titolo": titolo,
        "data": data_appuntamento,
        "ora": ora_appuntamento.split("-")[0].strip() if ora_appuntamento else "",
        "ora_fine": ora_appuntamento.split("-")[1].strip() if "-" in (ora_appuntamento or "") else "",
        "descrizione": descrizione,
        "categoria": "boxbeat",
        "provincia": prov_resolved,
        "creato_da": creato_da,
        "creato_il": datetime.now().isoformat(),
        "completata": False,
        "completata_da": None,
        "completata_il": None,
        "boxbeat_data": {
            "pod": pod.strip() if pod else "",
            "eneltel": eneltel.strip() if eneltel else "",
            "nominativo": nominativo.strip() if nominativo else "",
            "via": via.strip() if via else "",
            "civico": civico.strip() if civico else "",
            "comune": comune.strip() if comune else "",
            "note": note.strip() if note else "",
            "origine": origine,
            "ora_appuntamento": ora_appuntamento.strip() if ora_appuntamento else "",
            "data_pianificata": data_pianificata.strip() if data_pianificata else "",
        }
    }

    with _lock:
        data_file = _load_data(prov_resolved)
        data_file["attivita"].append(attivita)
        _save_data(data_file, prov_resolved)

    return attivita


def get_promemoria_boxbeat_oggi(completati: bool = False, username: str = None,
                                 provincia: str = None) -> list:
    """
    Restituisce i promemoria Appuntamento creati oggi per la provincia attiva.
    """
    prov = _resolve_prov(provincia)
    oggi = datetime.now().strftime('%Y-%m-%d')
    with _lock:
        data = _load_data(prov)

    risultati = []
    for att in data.get("attivita", []):
        if att.get("categoria") != "boxbeat":
            continue
        creato_il = att.get("creato_il", "")
        if not creato_il.startswith(oggi):
            continue
        if not completati and att.get("completata", False):
            continue
        if username:
            creato_da = att.get("creato_da", "")
            if creato_da != username and creato_da not in ("sistema", ""):
                continue
        risultati.append(att)

    risultati.sort(key=lambda a: a.get("ora", "99:99"))
    return risultati


def get_riepilogo_boxbeat(username: str = None, provincia: str = None) -> dict:
    """
    Restituisce un riepilogo dei promemoria Appuntamento creati oggi per la provincia attiva.
    """
    prov = _resolve_prov(provincia)
    oggi = datetime.now().strftime('%Y-%m-%d')
    with _lock:
        data = _load_data(prov)

    boxbeat_oggi = []
    for a in data.get("attivita", []):
        if a.get("categoria") != "boxbeat":
            continue
        creato_il = a.get("creato_il", "")
        if not creato_il.startswith(oggi):
            continue
        if username:
            creato_da = a.get("creato_da", "")
            if creato_da != username and creato_da not in ("sistema", ""):
                continue
        boxbeat_oggi.append(a)

    totali_oggi = len(boxbeat_oggi)
    completati_oggi = sum(1 for a in boxbeat_oggi if a.get("completata"))
    pendenti_oggi = totali_oggi - completati_oggi

    return {
        "totali_oggi": totali_oggi,
        "completati_oggi": completati_oggi,
        "pendenti_oggi": pendenti_oggi,
    }


# ===========================================================================
# REGISTRO APPUNTAMENTI EXCEL — Isolato per provincia
# ===========================================================================

def _load_chiavi_viste(provincia=None) -> set:
    """Carica il registro persistente dei POD/ENELTEL già visti dal sync filone per la provincia."""
    path = _get_chiavi_viste_file(provincia)
    data = json_read(path, default=None)
    if not data or not isinstance(data, dict):
        return set()
    return set(data.get('chiavi', []))


def _save_chiavi_viste(chiavi: set, provincia=None):
    """Salva il registro persistente dei POD/ENELTEL già visti per la provincia (atomico)."""
    path = _get_chiavi_viste_file(provincia)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json_write(path, {
        'aggiornato_il': datetime.now().isoformat(),
        'chiavi': sorted(chiavi)
    }, indent=2)


def _load_excel_data(provincia=None) -> dict:
    """Carica il registro appuntamenti Excel per la provincia."""
    path = _get_boxbeat_excel_file(provincia)
    data = json_read(path, default=None)
    if not data or not isinstance(data, dict):
        return {}
    return data


def _save_excel_data(data: dict, provincia=None):
    """Salva il registro appuntamenti Excel per la provincia (atomico)."""
    path = _get_boxbeat_excel_file(provincia)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json_write(path, data, indent=2)


def _cleanup_old_excel_data(provincia=None):
    """
    Rimuove dal registro boxbeat_excel.json tutte le date precedenti a oggi per la provincia.
    Elimina anche i file Excel (.xlsx) delle date vecchie dalla cartella data/{provincia}/.
    """
    prov = _resolve_prov(provincia)
    oggi = datetime.now().strftime('%Y-%m-%d')
    with _excel_lock:
        data = _load_excel_data(prov)
        date_da_rimuovere = [d for d in data if d < oggi]
        if not date_da_rimuovere:
            return
        for d in date_da_rimuovere:
            del data[d]
            data_dir = os.path.join(SCRIPT_DIR, 'data', prov.lower())
            xlsx_path = os.path.join(data_dir, f'appuntamenti_{d}.xlsx')
            if os.path.exists(xlsx_path):
                try:
                    os.remove(xlsx_path)
                    print(f"[Appuntamenti Cleanup {prov}] Rimosso file Excel vecchio: {xlsx_path}")
                except OSError:
                    pass
        _save_excel_data(data, prov)
        if date_da_rimuovere:
            print(f"[Appuntamenti Cleanup {prov}] Rimosse {len(date_da_rimuovere)} date vecchie dal registro")


# Esegui pulizia automatica all'avvio del modulo
_cleanup_old_excel_data()


def registra_appuntamento_excel(data_appuntamento: str, orario: str = '',
                                 pod: str = '', eneltel: str = '',
                                 nominativo: str = '', via: str = '',
                                 civico: str = '', comune: str = '',
                                 note: str = '', origine: str = '',
                                 data_pianificata: str = '',
                                 creato_da: str = 'sistema',
                                 provincia: str = ''):
    """
    Registra un appuntamento nel registro Excel indipendente della provincia.
    """
    prov_resolved = _resolve_prov(provincia)
    oggi = datetime.now().strftime('%Y-%m-%d')
    entry = {
        "id": str(uuid.uuid4())[:8],
        "data_appuntamento": data_appuntamento.strip() if data_appuntamento else '',
        "orario": orario.strip() if orario else '',
        "pod": pod.strip() if pod else '',
        "eneltel": eneltel.strip() if eneltel else '',
        "nominativo": nominativo.strip() if nominativo else '',
        "via": via.strip() if via else '',
        "civico": civico.strip() if civico else '',
        "comune": comune.strip() if comune else '',
        "note": note.strip() if note else '',
        "data_pianificata": data_pianificata.strip() if data_pianificata else '',
        "origine": origine,
        "provincia": prov_resolved,
        "creato_da": creato_da,
        "registrato_il": datetime.now().isoformat(),
    }

    with _excel_lock:
        data = _load_excel_data(prov_resolved)
        if oggi not in data:
            data[oggi] = []

        pod_check = entry.get('pod', '').strip().upper()
        enel_check = entry.get('eneltel', '').strip()
        idx_duplicato = None
        for i, existing in enumerate(data[oggi]):
            ex_pod = existing.get('pod', '').strip().upper()
            ex_enel = existing.get('eneltel', '').strip()
            if (pod_check and ex_pod and pod_check == ex_pod) or \
               (enel_check and ex_enel and enel_check == ex_enel):
                idx_duplicato = i
                break

        if idx_duplicato is not None:
            existing = data[oggi][idx_duplicato]
            new_note = entry.get('note', '').strip()
            ex_note = existing.get('note', '').strip()
            if new_note and not ex_note:
                data[oggi][idx_duplicato] = entry
        else:
            data[oggi].append(entry)

        _save_excel_data(data, prov_resolved)

        chiavi_da_aggiungere = set()
        if pod_check:
            chiavi_da_aggiungere.add(pod_check)
        if enel_check:
            chiavi_da_aggiungere.add(enel_check)
        if chiavi_da_aggiungere:
            chiavi_viste = _load_chiavi_viste(prov_resolved)
            chiavi_viste.update(chiavi_da_aggiungere)
            _save_chiavi_viste(chiavi_viste, prov_resolved)

    genera_excel_boxbeat(oggi, provincia=prov_resolved)
    return entry


def genera_excel_boxbeat(data_str: str = None, dati_filone: dict = None,
                         username: str = None,
                         provincia: str = None,
                         note_aggiornate: dict = None,
                         nominativi_aggiornati: dict = None,
                         date_pianificate: dict = None) -> str:
    """
    Genera il file Excel con gli appuntamenti registrati nella data indicata per la provincia.
    Salva il file in data/{provincia}/appuntamenti_{data_str}.xlsx.
    """
    prov_resolved = _resolve_prov(provincia)
    if dati_filone is None:
        dati_filone = {}
    _note = dati_filone.get('note', {}) or note_aggiornate or {}
    _nominativi = dati_filone.get('nominativi', {}) or nominativi_aggiornati or {}
    _date_pian = dati_filone.get('date_pian', {}) or date_pianificate or {}
    _vie = dati_filone.get('vie', {})
    _civici = dati_filone.get('civici', {})
    _comuni = dati_filone.get('comuni', {})
    _operatori = dati_filone.get('operatori', {})

    from config import OPERATORI_DICT
    _reverse_operatori = {v: k for k, v in OPERATORI_DICT.items()}
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    if not data_str:
        data_str = datetime.now().strftime('%Y-%m-%d')

    with _excel_lock:
        all_data = _load_excel_data(prov_resolved)
    appuntamenti = list(all_data.get(data_str, []))

    if username:
        appuntamenti = [
            a for a in appuntamenti
            if a.get('creato_da', '') == username or a.get('creato_da', '') in ('sistema', '')
        ]

    appuntamenti.sort(key=lambda a: a.get('orario', '99:99'))

    wb = Workbook()
    ws = wb.active
    ws.title = f"Appuntamenti {data_str}"

    header_font = Font(name='Calibri', bold=True, size=11, color='FFFFFF')
    header_fill = PatternFill(start_color='E91E63', end_color='E91E63', fill_type='solid')
    header_align = Alignment(horizontal='center', vertical='center', wrap_text=True)
    cell_align = Alignment(horizontal='center', vertical='center', wrap_text=True)
    cell_font = Font(name='Calibri', size=10)
    thin_border = Border(
        left=Side(style='thin', color='CCCCCC'),
        right=Side(style='thin', color='CCCCCC'),
        top=Side(style='thin', color='CCCCCC'),
        bottom=Side(style='thin', color='CCCCCC'),
    )
    alt_fill = PatternFill(start_color='FFF0F5', end_color='FFF0F5', fill_type='solid')

    giorni_it = ['lunedì', 'martedì', 'mercoledì', 'giovedì', 'venerdì', 'sabato', 'domenica']
    mesi_it = ['gennaio', 'febbraio', 'marzo', 'aprile', 'maggio', 'giugno',
               'luglio', 'agosto', 'settembre', 'ottobre', 'novembre', 'dicembre']

    def _formatta_data_it(data_yyyy_mm_dd: str) -> str:
        if not data_yyyy_mm_dd:
            return ''
        try:
            dt = datetime.strptime(data_yyyy_mm_dd, '%Y-%m-%d')
            return f"{giorni_it[dt.weekday()]} {dt.day} {mesi_it[dt.month - 1]}"
        except (ValueError, IndexError):
            return data_yyyy_mm_dd

    colonne = ['Operatore', 'Data', 'Orario', 'POD', 'Eneltel', 'Nominativo', 'Indirizzo', 'Civico', 'Comune', 'Note']
    larghezze = [14, 24, 16, 20, 16, 22, 24, 8, 18, 26]

    for c, (titolo, larg) in enumerate(zip(colonne, larghezze), 1):
        cell = ws.cell(row=1, column=c, value=titolo)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align
        cell.border = thin_border
        ws.column_dimensions[cell.column_letter].width = larg

    for r, app in enumerate(appuntamenti, 2):
        enel_key = app.get('eneltel', '').strip()
        pod_key = app.get('pod', '').strip().upper()

        def _da_filone(dizionario, fallback=''):
            if dizionario:
                if enel_key and enel_key in dizionario:
                    return dizionario[enel_key]
                if pod_key and pod_key in dizionario:
                    return dizionario[pod_key]
            return fallback

        nota = _da_filone(_note, app.get('note', ''))
        nominativo = _da_filone(_nominativi, app.get('nominativo', ''))
        via = _da_filone(_vie, app.get('via', ''))
        civico = _da_filone(_civici, app.get('civico', ''))
        comune = _da_filone(_comuni, app.get('comune', ''))

        data_app = app.get('data_appuntamento', '')
        data_pian_filone = _da_filone(_date_pian, '')
        data_pian_locale = app.get('data_pianificata', '')
        data_da_mostrare = data_pian_filone or data_app or data_pian_locale

        matricola = _da_filone(_operatori, '')
        nome_operatore = _reverse_operatori.get(matricola, matricola)

        valori = [
            nome_operatore,
            _formatta_data_it(data_da_mostrare),
            app.get('orario', ''),
            app.get('pod', ''),
            app.get('eneltel', ''),
            nominativo,
            via,
            civico,
            comune,
            nota,
        ]
        for c, val in enumerate(valori, 1):
            cell = ws.cell(row=r, column=c, value=val)
            cell.font = cell_font
            cell.alignment = cell_align
            cell.border = thin_border
            if r % 2 == 0:
                cell.fill = alt_fill

    ws.row_dimensions[1].height = 28

    data_dir = os.path.join(SCRIPT_DIR, 'data', prov_resolved.lower())
    os.makedirs(data_dir, exist_ok=True)
    filepath = os.path.join(data_dir, f'appuntamenti_{data_str}.xlsx')
    wb.save(filepath)
    print(f"[Appuntamenti Excel {prov_resolved}] Generato {filepath} con {len(appuntamenti)} appuntamenti")
    return filepath
