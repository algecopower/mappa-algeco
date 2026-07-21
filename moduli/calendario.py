# -*- coding: utf-8 -*-
"""
Modulo Calendario/Attività Condivise.
Gestisce una lista di attività condivisa tra tutti gli utenti registrati.
I dati vengono salvati in un file JSON persistente.
"""
import os
import uuid
import threading
from datetime import datetime

from .safe_json import json_read, json_write

# Directory base per i dati
SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CALENDARIO_FILE = os.path.join(SCRIPT_DIR, 'data', 'calendario.json')

# Lock per accesso thread-safe (usato per operazioni read-modify-write
# che NON passano da json_modify, es. letture semplici)
_lock = threading.Lock()

_CAL_DEFAULT = {"attivita": []}


def _load_data() -> dict:
    """Carica i dati del calendario dal file JSON (thread-safe, via safe_json)."""
    data = json_read(CALENDARIO_FILE, default=_CAL_DEFAULT)
    if "attivita" not in data:
        data["attivita"] = []
    return data


def _save_data(data: dict) -> bool:
    """Salva i dati del calendario nel file JSON (atomico, via safe_json)."""
    return json_write(CALENDARIO_FILE, data, indent=2)


def get_attivita(filtro_mese: str = None, filtro_utente: str = None,
                 mostra_completate: bool = True, provincia: str = None) -> list:
    """
    Restituisce la lista delle attività, opzionalmente filtrate.
    
    Args:
        filtro_mese: Filtra per mese (formato 'YYYY-MM')
        filtro_utente: Filtra per utente creatore
        mostra_completate: Se False, esclude le attività completate
        provincia: Se specificato, mostra solo attività della provincia (o senza provincia)
    
    Returns:
        Lista di attività ordinate per data/ora
    """
    with _lock:
        data = _load_data()
    
    attivita = data["attivita"]
    
    # Applica filtri
    if filtro_mese:
        attivita = [a for a in attivita if a.get("data", "").startswith(filtro_mese)]
    
    if filtro_utente:
        attivita = [a for a in attivita if a.get("creato_da") == filtro_utente]
    
    if not mostra_completate:
        attivita = [a for a in attivita if not a.get("completata", False)]
    
    # Escludi SEMPRE i boxbeat completati dal calendario
    # (vengono gestiti solo dalla sezione Promemoria, non dal calendario)
    attivita = [a for a in attivita
                if not (a.get("categoria") == "boxbeat" and a.get("completata", False))]
    
    # Filtra per provincia: mostra attività della provincia attiva o senza provincia
    if provincia:
        prov_upper = provincia.upper()
        attivita = [a for a in attivita
                    if not a.get("provincia", "") or a.get("provincia", "").upper() == prov_upper]
    
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
    Crea una nuova attività.
    
    Args:
        titolo: Titolo dell'attività (obbligatorio)
        data: Data (formato YYYY-MM-DD, obbligatorio)
        ora: Ora inizio (formato HH:MM, opzionale)
        ora_fine: Ora fine (formato HH:MM, opzionale)
        descrizione: Descrizione dettagliata (opzionale)
        categoria: appuntamento|promemoria|scadenza|riunione|altro
        creato_da: Username di chi crea
        provincia: Provincia associata (opzionale)
    
    Returns:
        L'attività creata con ID assegnato
    """
    if not titolo or not titolo.strip():
        raise ValueError("Il titolo è obbligatorio")
    if not data or not data.strip():
        raise ValueError("La data è obbligatoria")
    
    attivita = {
        "id": str(uuid.uuid4())[:8],
        "titolo": titolo.strip(),
        "data": data.strip(),
        "ora": ora.strip() if ora else "",
        "ora_fine": ora_fine.strip() if ora_fine else "",
        "descrizione": descrizione.strip() if descrizione else "",
        "categoria": categoria if categoria in CATEGORIE_VALIDE else "promemoria",
        "provincia": provincia.strip() if provincia else "",
        "creato_da": creato_da,
        "creato_il": datetime.now().isoformat(),
        "completata": False,
        "completata_da": None,
        "completata_il": None,
    }
    
    with _lock:
        data_file = _load_data()
        data_file["attivita"].append(attivita)
        _save_data(data_file)
    
    return attivita


def modifica_attivita(attivita_id: str, aggiornamenti: dict,
                      modificato_da: str = "sistema") -> dict:
    """
    Modifica un'attività esistente.
    
    Args:
        attivita_id: ID dell'attività
        aggiornamenti: Dict con i campi da aggiornare
        modificato_da: Username di chi modifica
    
    Returns:
        L'attività aggiornata
    
    Raises:
        ValueError: Se l'attività non è trovata
    """
    campi_modificabili = {"titolo", "data", "ora", "ora_fine", "descrizione",
                          "categoria", "provincia"}
    
    with _lock:
        data = _load_data()
        
        for att in data["attivita"]:
            if att["id"] == attivita_id:
                for campo, valore in aggiornamenti.items():
                    if campo in campi_modificabili:
                        att[campo] = valore.strip() if isinstance(valore, str) else valore
                
                att["modificato_da"] = modificato_da
                att["modificato_il"] = datetime.now().isoformat()
                
                _save_data(data)
                return att
        
        raise ValueError(f"Attività '{attivita_id}' non trovata")


def completa_attivita(attivita_id: str, completata_da: str = "sistema") -> dict:
    """
    Segna un'attività come completata (o la riapre se era già completata).
    
    Args:
        attivita_id: ID dell'attività
        completata_da: Username di chi completa
    
    Returns:
        L'attività aggiornata
    """
    with _lock:
        data = _load_data()
        
        for att in data["attivita"]:
            if att["id"] == attivita_id:
                if att.get("completata"):
                    # Riapri
                    att["completata"] = False
                    att["completata_da"] = None
                    att["completata_il"] = None
                else:
                    # Completa
                    att["completata"] = True
                    att["completata_da"] = completata_da
                    att["completata_il"] = datetime.now().isoformat()
                
                _save_data(data)
                return att
        
        raise ValueError(f"Attività '{attivita_id}' non trovata")


def completa_multipli(ids: list, completata_da: str = "sistema", forza_completa: bool = True) -> int:
    """
    Segna più attività come completate in una sola operazione.
    
    Args:
        ids: Lista di ID delle attività
        completata_da: Username di chi completa
        forza_completa: Se True, completa solo quelle non ancora completate
    
    Returns:
        Numero di attività completate
    """
    completati = 0
    with _lock:
        data = _load_data()
        for att in data["attivita"]:
            if att["id"] in ids:
                if forza_completa and att.get("completata"):
                    continue
                att["completata"] = True
                att["completata_da"] = completata_da
                att["completata_il"] = datetime.now().isoformat()
                completati += 1
        if completati > 0:
            _save_data(data)
    return completati


def elimina_attivita(attivita_id: str) -> bool:
    """
    Elimina un'attività.
    
    Args:
        attivita_id: ID dell'attività
    
    Returns:
        True se eliminata, False se non trovata
    """
    with _lock:
        data = _load_data()
        originale = len(data["attivita"])
        data["attivita"] = [a for a in data["attivita"] if a["id"] != attivita_id]
        
        if len(data["attivita"]) < originale:
            _save_data(data)
            return True
        return False


def elimina_multipli(ids: list) -> int:
    """
    Elimina più attività in una sola operazione.
    
    Args:
        ids: Lista di ID delle attività da eliminare
    
    Returns:
        Numero di attività effettivamente eliminate
    """
    if not ids:
        return 0
    ids_set = set(ids)
    with _lock:
        data = _load_data()
        originale = len(data["attivita"])
        data["attivita"] = [a for a in data["attivita"] if a["id"] not in ids_set]
        eliminati = originale - len(data["attivita"])
        if eliminati > 0:
            _save_data(data)
        return eliminati


def get_attivita_per_id(attivita_id: str) -> dict:
    """Restituisce una singola attività per ID."""
    with _lock:
        data = _load_data()
    
    for att in data["attivita"]:
        if att["id"] == attivita_id:
            return att
    return None


def get_riepilogo(provincia: str = None) -> dict:
    """
    Restituisce un riepilogo delle attività.
    Utile per badge/contatori nella UI.
    
    Args:
        provincia: Se specificato, filtra per provincia
    """
    with _lock:
        data = _load_data()
    
    oggi = datetime.now().strftime('%Y-%m-%d')
    mese_corrente = datetime.now().strftime('%Y-%m')
    
    attivita = data["attivita"]
    
    # Filtra per provincia
    if provincia:
        prov_upper = provincia.upper()
        attivita = [a for a in attivita
                    if not a.get("provincia", "") or a.get("provincia", "").upper() == prov_upper]
    
    # Escludi boxbeat completati (gestiti dalla sezione Promemoria)
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


def pulisci_boxbeat_completati(giorni_retention: int = 7) -> int:
    """
    Rimuove dal file JSON i boxbeat completati più vecchi di N giorni.
    Viene chiamata automaticamente per evitare accumulo di dati obsoleti.
    
    Args:
        giorni_retention: Numero di giorni dopo i quali eliminare (default 7)
    
    Returns:
        Numero di boxbeat eliminati
    """
    from datetime import timedelta
    soglia = (datetime.now() - timedelta(days=giorni_retention)).isoformat()
    
    with _lock:
        data = _load_data()
        originale = len(data["attivita"])
        data["attivita"] = [
            a for a in data["attivita"]
            if not (
                a.get("categoria") == "boxbeat"
                and a.get("completata", False)
                and a.get("completata_il", "9999") < soglia
            )
        ]
        rimossi = originale - len(data["attivita"])
        if rimossi > 0:
            _save_data(data)
            print(f"[Calendario] Pulizia: rimossi {rimossi} boxbeat completati (> {giorni_retention}gg)")
        return rimossi


# Categorie valide per le attività
CATEGORIE_VALIDE = {"appuntamento", "promemoria", "scadenza", "riunione", "altro", "boxbeat"}

# Colori associati alle categorie (per la UI)
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

PROMEMORIA_SETTINGS_FILE = os.path.join(SCRIPT_DIR, 'data', 'promemoria_settings.json')


def _load_promemoria_settings() -> dict:
    """Carica le impostazioni promemoria per utente."""
    return json_read(PROMEMORIA_SETTINGS_FILE, default={})


def _save_promemoria_settings(data: dict) -> bool:
    """Salva le impostazioni promemoria per utente (atomico)."""
    return json_write(PROMEMORIA_SETTINGS_FILE, data, indent=2)


def get_user_promemoria_settings(username: str) -> dict:
    """
    Restituisce le impostazioni promemoria per un utente.
    
    Returns:
        Dict con: orario_scadenza, abilitato, notifica_browser, suono
    """
    settings = _load_promemoria_settings()
    user_settings = settings.get(username, {})
    # Defaults
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


def salva_user_promemoria_settings(username: str, nuove_settings: dict) -> bool:
    """
    Salva le impostazioni promemoria per un utente.
    """
    settings = _load_promemoria_settings()
    if username not in settings:
        settings[username] = {}
    
    campi_validi = {"abilitato", "orario_scadenza", "mostra_completate",
                    "notifica_browser", "suono", "categorie", "ultimo_alert_mostrato"}
    
    for campo, valore in nuove_settings.items():
        if campo in campi_validi:
            settings[username][campo] = valore
    
    settings[username]["aggiornato_il"] = datetime.now().isoformat()
    return _save_promemoria_settings(settings)


def crea_promemoria_boxbeat(data_appuntamento: str, ora_appuntamento: str,
                            pod: str = "", eneltel: str = "",
                            nominativo: str = "", via: str = "",
                            civico: str = "", comune: str = "",
                            note: str = "", provincia: str = "",
                            creato_da: str = "sistema",
                            origine: str = "manuale",
                            data_pianificata: str = "") -> dict:
    """
    Crea un promemoria Appuntamento standardizzato.
    Viene creato come attività nel calendario con categoria 'boxbeat'
    e dati strutturati nel campo descrizione_strutturata.
    
    Args:
        data_appuntamento: Data (YYYY-MM-DD)
        ora_appuntamento: Orario dell'appuntamento (HH:MM o HH:MM-HH:MM)
        pod: Codice POD del contatore
        eneltel: Codice ENELTEL del contatore
        nominativo: Nome del cliente
        via: Indirizzo (via/piazza)
        civico: Numero civico
        comune: Comune
        note: Note aggiuntive
        provincia: Provincia (TORINO/PAVIA)
        creato_da: Username
        origine: contatore|distinte|manuale
    
    Returns:
        L'attività creata
    """
    # Costruisci titolo automatico
    parti_titolo = ["Appuntamento"]
    if nominativo:
        parti_titolo.append(nominativo.strip())
    if comune:
        parti_titolo.append(f"- {comune.strip()}")
    if ora_appuntamento:
        parti_titolo.append(f"ore {ora_appuntamento}")
    titolo = " ".join(parti_titolo)
    
    # Costruisci descrizione leggibile
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
    
    # Crea l'attività base
    attivita = {
        "id": str(uuid.uuid4())[:8],
        "titolo": titolo,
        "data": data_appuntamento,
        "ora": ora_appuntamento.split("-")[0].strip() if ora_appuntamento else "",
        "ora_fine": ora_appuntamento.split("-")[1].strip() if "-" in (ora_appuntamento or "") else "",
        "descrizione": descrizione,
        "categoria": "boxbeat",
        "provincia": provincia,
        "creato_da": creato_da,
        "creato_il": datetime.now().isoformat(),
        "completata": False,
        "completata_da": None,
        "completata_il": None,
        # Dati strutturati Appuntamento
        "boxbeat_data": {
            "pod": pod.strip() if pod else "",
            "eneltel": eneltel.strip() if eneltel else "",
            "nominativo": nominativo.strip() if nominativo else "",
            "via": via.strip() if via else "",
            "civico": civico.strip() if civico else "",
            "comune": comune.strip() if comune else "",
            "note": note.strip() if note else "",
            "origine": origine,
            "ora_appuntamento": ora_appuntamento.strip() if ora_appuntamento else "",            "data_pianificata": data_pianificata.strip() if data_pianificata else "",        }
    }
    
    with _lock:
        data_file = _load_data()
        data_file["attivita"].append(attivita)
        _save_data(data_file)
    
    return attivita


def get_promemoria_boxbeat_oggi(completati: bool = False, username: str = None,
                                 provincia: str = None) -> list:
    """
    Restituisce tutti i promemoria Appuntamento creati oggi.
    Filtra per data di creazione (oggi) e opzionalmente per utente e provincia.
    
    Args:
        completati: Se True include anche i completati
        username: Se specificato, mostra solo promemoria dell'utente (+ quelli di sistema)
        provincia: Se specificato, mostra solo promemoria della provincia indicata
    
    Returns:
        Lista di promemoria Appuntamento creati nella giornata odierna
    """
    oggi = datetime.now().strftime('%Y-%m-%d')
    with _lock:
        data = _load_data()
    
    risultati = []
    for att in data["attivita"]:
        if att.get("categoria") != "boxbeat":
            continue
        # Filtra per data di CREAZIONE (oggi), non per data appuntamento
        creato_il = att.get("creato_il", "")
        if not creato_il.startswith(oggi):
            continue
        if not completati and att.get("completata", False):
            continue
        # Filtro per utente: mostra propri + quelli di sistema
        if username:
            creato_da = att.get("creato_da", "")
            if creato_da != username and creato_da not in ("sistema", ""):
                continue
        # Filtro per provincia
        if provincia:
            att_prov = att.get("provincia", "").upper()
            if att_prov and att_prov != provincia.upper():
                continue
        risultati.append(att)
    
    # Ordina per ora
    risultati.sort(key=lambda a: a.get("ora", "99:99"))
    return risultati


def get_riepilogo_boxbeat(username: str = None, provincia: str = None) -> dict:
    """
    Restituisce un riepilogo dei promemoria Appuntamento creati oggi.
    
    Args:
        username: Se specificato, filtra per utente (+ sistema)
        provincia: Se specificato, filtra per provincia
    """
    oggi = datetime.now().strftime('%Y-%m-%d')
    with _lock:
        data = _load_data()
    
    boxbeat_oggi = []
    for a in data["attivita"]:
        if a.get("categoria") != "boxbeat":
            continue
        creato_il = a.get("creato_il", "")
        if not creato_il.startswith(oggi):
            continue
        if username:
            creato_da = a.get("creato_da", "")
            if creato_da != username and creato_da not in ("sistema", ""):
                continue
        # Filtro per provincia
        if provincia:
            att_prov = a.get("provincia", "").upper()
            if att_prov and att_prov != provincia.upper():
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
# REGISTRO APPUNTAMENTI EXCEL — Indipendente dai promemoria calendario
# ===========================================================================

BOXBEAT_EXCEL_FILE = os.path.join(SCRIPT_DIR, 'data', 'boxbeat_excel.json')
CHIAVI_VISTE_FILE = os.path.join(SCRIPT_DIR, 'cache', 'cache_appuntamenti_visti.json')
_excel_lock = threading.Lock()


def _load_chiavi_viste() -> set:
    """
    Carica il registro persistente dei POD/ENELTEL già visti dal sync filone.
    Questo impedisce di ri-registrare appuntamenti vecchi dopo la pulizia giornaliera.
    """
    data = json_read(CHIAVI_VISTE_FILE, default={})
    return set(data.get('chiavi', []))


def _save_chiavi_viste(chiavi: set):
    """Salva il registro persistente dei POD/ENELTEL già visti (atomico)."""
    json_write(CHIAVI_VISTE_FILE, {
        'aggiornato_il': datetime.now().isoformat(),
        'chiavi': sorted(chiavi)
    }, indent=2)


def _load_excel_data() -> dict:
    """Carica il registro appuntamenti Excel."""
    return json_read(BOXBEAT_EXCEL_FILE, default={})


def _save_excel_data(data: dict):
    """Salva il registro appuntamenti Excel (atomico)."""
    json_write(BOXBEAT_EXCEL_FILE, data, indent=2)


def _cleanup_old_excel_data():
    """
    Rimuove dal registro boxbeat_excel.json tutte le date precedenti a oggi.
    Elimina anche i file Excel (.xlsx) delle date vecchie dalla cartella data/.
    """
    oggi = datetime.now().strftime('%Y-%m-%d')
    with _excel_lock:
        data = _load_excel_data()
        date_da_rimuovere = [d for d in data if d < oggi]
        if not date_da_rimuovere:
            return
        for d in date_da_rimuovere:
            del data[d]
            # Rimuovi anche il file Excel corrispondente
            xlsx_path = os.path.join(SCRIPT_DIR, 'data', f'boxbeat_appuntamenti_{d}.xlsx')
            if os.path.exists(xlsx_path):
                try:
                    os.remove(xlsx_path)
                    print(f"[Appuntamenti Cleanup] Rimosso file Excel vecchio: {xlsx_path}")
                except OSError:
                    pass
        _save_excel_data(data)
        print(f"[Appuntamenti Cleanup] Rimosse {len(date_da_rimuovere)} date vecchie dal registro")


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
    Registra un appuntamento nel registro Excel indipendente.
    Indicizzato per data di CREAZIONE (oggi), non per data appuntamento.
    Dopo la registrazione, rigenera automaticamente il file Excel.
    
    Args:
        data_appuntamento: data dell'appuntamento (YYYY-MM-DD) - può essere futura
        data_pianificata: data pianificata del contatore nel filone (YYYY-MM-DD)
        creato_da: username dell'utente che ha creato l'appuntamento
    """
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
        "provincia": provincia.strip().upper() if provincia else '',
        "creato_da": creato_da,
        "registrato_il": datetime.now().isoformat(),
    }

    with _excel_lock:
        data = _load_excel_data()
        if oggi not in data:
            data[oggi] = []

        # Deduplicazione: se esiste già un entry con lo stesso POD o ENELTEL per oggi, aggiorna invece di duplicare
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
            # Duplicato trovato: tieni l'entry con più informazioni (note non vuote)
            existing = data[oggi][idx_duplicato]
            new_note = entry.get('note', '').strip()
            ex_note = existing.get('note', '').strip()
            if new_note and not ex_note:
                # Il nuovo ha note, l'esistente no → sostituisci
                data[oggi][idx_duplicato] = entry
            # Altrimenti tieni l'esistente (ha già note o entrambi vuoti)
        else:
            data[oggi].append(entry)

        _save_excel_data(data)

        # Aggiorna chiavi_viste per evitare ri-registrazioni dal sync filone
        chiavi_da_aggiungere = set()
        if pod_check:
            chiavi_da_aggiungere.add(pod_check)
        if enel_check:
            chiavi_da_aggiungere.add(enel_check)
        if chiavi_da_aggiungere:
            chiavi_viste = _load_chiavi_viste()
            chiavi_viste.update(chiavi_da_aggiungere)
            _save_chiavi_viste(chiavi_viste)

    # Rigenera Excel per la data odierna
    genera_excel_boxbeat(oggi)
    return entry


def genera_excel_boxbeat(data_str: str = None, dati_filone: dict = None,
                         username: str = None,
                         provincia: str = None,
                         # Parametri legacy (retrocompatibilità)
                         note_aggiornate: dict = None,
                         nominativi_aggiornati: dict = None,
                         date_pianificate: dict = None) -> str:
    """
    Genera il file Excel con gli appuntamenti registrati nella data indicata.
    Legge dal registro indipendente (boxbeat_excel.json), NON dai promemoria.
    La data è la data di REGISTRAZIONE/CREAZIONE, non dell'appuntamento.

    Colonne: Operatore, Data, Orario, POD, Eneltel, Nominativo, Indirizzo, Civico, Comune, Note
    Formattazione: testo a capo automatico, allineamento centrale.
    
    Args:
        data_str: data di registrazione in formato YYYY-MM-DD (default: oggi)
        dati_filone: dict unificato dal filone con sotto-dict:
            note, nominativi, date_pian, vie, civici, comuni
        username: se specificato, filtra appuntamenti per utente (+ sistema)
        provincia: se specificato, filtra appuntamenti per provincia
        note_aggiornate: (legacy) dict {eneltel_or_pod: nota}
        nominativi_aggiornati: (legacy) dict {eneltel_or_pod: nominativo}
        date_pianificate: (legacy) dict {eneltel_or_pod: data_YYYY-MM-DD}
    """
    # Unifica parametri legacy con il nuovo formato
    if dati_filone is None:
        dati_filone = {}
    _note = dati_filone.get('note', {}) or note_aggiornate or {}
    _nominativi = dati_filone.get('nominativi', {}) or nominativi_aggiornati or {}
    _date_pian = dati_filone.get('date_pian', {}) or date_pianificate or {}
    _vie = dati_filone.get('vie', {})
    _civici = dati_filone.get('civici', {})
    _comuni = dati_filone.get('comuni', {})
    _operatori = dati_filone.get('operatori', {})
    
    # Mappa inversa matricola → nome visualizzato
    from config import OPERATORI_DICT
    _reverse_operatori = {v: k for k, v in OPERATORI_DICT.items()}
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    if not data_str:
        data_str = datetime.now().strftime('%Y-%m-%d')

    with _excel_lock:
        all_data = _load_excel_data()
    appuntamenti = all_data.get(data_str, [])
    # Filtra per utente se richiesto
    if username:
        appuntamenti = [
            a for a in appuntamenti
            if a.get('creato_da', '') == username or a.get('creato_da', '') in ('sistema', '')
        ]
    # Filtra per provincia se richiesto
    if provincia:
        prov_upper = provincia.upper()
        appuntamenti = [
            a for a in appuntamenti
            if not a.get('provincia', '') or a.get('provincia', '').upper() == prov_upper
        ]
    # Ordina per orario
    appuntamenti.sort(key=lambda a: a.get('orario', '99:99'))

    wb = Workbook()
    ws = wb.active
    ws.title = f"Appuntamenti {data_str}"

    # --- Stili ---
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

    # Helper per formattare data in italiano: "giorno della settimana DD mese"
    giorni_it = ['lunedì', 'martedì', 'mercoledì', 'giovedì', 'venerdì', 'sabato', 'domenica']
    mesi_it = ['gennaio', 'febbraio', 'marzo', 'aprile', 'maggio', 'giugno',
               'luglio', 'agosto', 'settembre', 'ottobre', 'novembre', 'dicembre']
    def _formatta_data_it(data_yyyy_mm_dd: str) -> str:
        """Converte YYYY-MM-DD in 'giorno della settimana DD mese'."""
        if not data_yyyy_mm_dd:
            return ''
        try:
            dt = datetime.strptime(data_yyyy_mm_dd, '%Y-%m-%d')
            return f"{giorni_it[dt.weekday()]} {dt.day} {mesi_it[dt.month - 1]}"
        except (ValueError, IndexError):
            return data_yyyy_mm_dd  # fallback: restituisci il valore grezzo

    colonne = ['Operatore', 'Data', 'Orario', 'POD', 'Eneltel', 'Nominativo', 'Indirizzo', 'Civico', 'Comune', 'Note']
    larghezze = [14, 24, 16, 20, 16, 22, 24, 8, 18, 26]

    # Intestazioni
    for c, (titolo, larg) in enumerate(zip(colonne, larghezze), 1):
        cell = ws.cell(row=1, column=c, value=titolo)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align
        cell.border = thin_border
        ws.column_dimensions[cell.column_letter].width = larg

    # Dati
    for r, app in enumerate(appuntamenti, 2):
        enel_key = app.get('eneltel', '').strip()
        pod_key = app.get('pod', '').strip().upper()
        
        # Helper: cerca un valore aggiornato dal filone per ENELTEL o POD
        def _da_filone(dizionario, fallback=''):
            if dizionario:
                if enel_key and enel_key in dizionario:
                    return dizionario[enel_key]
                if pod_key and pod_key in dizionario:
                    return dizionario[pod_key]
            return fallback
        
        # Recupera ogni campo: se aggiornato nel filone usa quello, altrimenti il dato locale
        nota = _da_filone(_note, app.get('note', ''))
        nominativo = _da_filone(_nominativi, app.get('nominativo', ''))
        via = _da_filone(_vie, app.get('via', ''))
        civico = _da_filone(_civici, app.get('civico', ''))
        comune = _da_filone(_comuni, app.get('comune', ''))
        
        # Data appuntamento: prima dal filone (sempre aggiornata), poi fallback locale
        data_app = app.get('data_appuntamento', '')
        data_pian_filone = _da_filone(_date_pian, '')
        data_pian_locale = app.get('data_pianificata', '')
        data_da_mostrare = data_pian_filone or data_app or data_pian_locale
        
        # Operatore: recupera matricola dal filone, poi mappa a nome visualizzato
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

    # Salva
    data_dir = os.path.join(SCRIPT_DIR, 'data')
    os.makedirs(data_dir, exist_ok=True)
    filepath = os.path.join(data_dir, f'appuntamenti_{data_str}.xlsx')
    wb.save(filepath)
    print(f"[Appuntamenti Excel] Generato {filepath} con {len(appuntamenti)} appuntamenti")
    return filepath
