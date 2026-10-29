# -*- coding: utf-8 -*-
"""
Modulo per la gestione dinamica dei dati comunali italiani.

Scarica il dataset completo dei comuni italiani da GitHub (comuni-json)
e lo salva in cache locale. Fornisce funzioni per cercare CAP e provincia
a partire dal nome del comune.

Fonte dati: https://github.com/matteocontrini/comuni-json
Contiene TUTTI i comuni italiani con CAP, provincia, regione, ecc.

La cache viene aggiornata automaticamente ogni 30 giorni.
Se il download fallisce, usa la cache esistente o un fallback hardcoded.
"""
import os
import re
import time
import unicodedata
import requests

from .safe_json import json_read, json_write

# ============================================================================
# CONFIGURAZIONE
# ============================================================================
_DIR_CORRENTE = os.path.dirname(os.path.abspath(__file__))
_DIR_PROGETTO = os.path.dirname(_DIR_CORRENTE)
_FILE_CACHE = os.path.join(_DIR_PROGETTO, 'cache', 'cache_comuni_italiani.json')
_URL_COMUNI = 'https://raw.githubusercontent.com/matteocontrini/comuni-json/master/comuni.json'
_DURATA_CACHE_SECONDI = 30 * 24 * 3600  # 30 giorni
_TIMEOUT_DOWNLOAD = 15  # secondi

# ============================================================================
# STATO INTERNO (caricato una sola volta per sessione)
# ============================================================================
_db_comuni = None  # dict: nome_normalizzato -> {cap, provincia, nome_originale}
_db_cap_provincia = None  # dict: cap -> sigla_provincia
_db_comuni_per_provincia = None  # dict: sigla -> {nome_normalizzato: info}
_db_comuni_tutti = None  # dict: nome_normalizzato -> list[info]
_db_provincia_a_sigla = None  # dict: nome_provincia_upper / sigla -> sigla
_db_provincia_a_regione = None  # dict: sigla -> regione_upper


def _normalizza_nome(nome):
    """
    Normalizza il nome di un comune per confronto robusto.
    Rimuove accenti, apostrofi, converte in maiuscolo.
    """
    if not nome:
        return ''
    nome = str(nome).strip().upper()
    # Sostituisci apostrofi e accenti tipografici
    nome = nome.replace("'", "'").replace("`", "'").replace("\u2019", "'")
    # Rimuovi accenti Unicode (è -> e, ò -> o, ecc.)
    nome_nfkd = unicodedata.normalize('NFKD', nome)
    nome_ascii = ''.join(c for c in nome_nfkd if not unicodedata.combining(c))
    # Versione senza apostrofi (per match aggiuntivo)
    return nome_ascii


def _normalizza_senza_apostrofi(nome):
    """Normalizza rimuovendo anche apostrofi."""
    return _normalizza_nome(nome).replace("'", "").replace(" ", " ")


def _normalizza_spazi(nome):
    """Normalizza sostituendo apostrofi con spazi e compattando spazi multipli."""
    nome_norm = _normalizza_nome(nome)
    return re.sub(r'\s+', ' ', nome_norm.replace("'", " ")).strip()


def _scarica_comuni():
    """
    Scarica il dataset dei comuni da GitHub.
    
    Returns:
        Lista di dizionari con i dati dei comuni, o None se fallisce
    """
    try:
        print("📥 Download database comuni italiani...")
        resp = requests.get(_URL_COMUNI, timeout=_TIMEOUT_DOWNLOAD)
        resp.raise_for_status()
        comuni_raw = resp.json()
        print(f"✅ Scaricati {len(comuni_raw)} comuni")
        return comuni_raw
    except Exception as e:
        print(f"⚠️  Errore download comuni: {e}")
        return None


def _salva_cache(comuni_raw):
    """Salva i dati scaricati in cache locale con timestamp."""
    cache_data = {
        'timestamp': time.time(),
        'comuni': comuni_raw
    }
    if json_write(_FILE_CACHE, cache_data):
        print(f"💾 Cache salvata: {_FILE_CACHE}")
    else:
        print("⚠️  Errore salvataggio cache")


def _carica_cache():
    """
    Carica i dati dalla cache locale se esistente e non scaduta.
    
    Returns:
        Lista di dizionari comuni, o None se cache assente/scaduta
    """
    cache_data = json_read(_FILE_CACHE, default=None)
    if cache_data is None:
        return None

    timestamp = cache_data.get('timestamp', 0)
    eta_secondi = time.time() - timestamp

    if eta_secondi > _DURATA_CACHE_SECONDI:
        giorni = int(eta_secondi / 86400)
        print(f"🔄 Cache comuni scaduta ({giorni} giorni). Aggiornamento...")
        return None

    comuni = cache_data.get('comuni', [])
    if comuni:
        giorni = int(eta_secondi / 86400)
        print(f"📦 Cache comuni caricata ({len(comuni)} comuni, {giorni}g fa)")
        return comuni
def _carica_cache_forzata():
    """Carica la cache anche se scaduta (fallback se download fallisce)."""
    cache_data = json_read(_FILE_CACHE, default=None)
    if cache_data is None:
        return None
    comuni = cache_data.get('comuni', [])
    if comuni:
        print(f"📦 Uso cache comuni esistente come fallback ({len(comuni)} comuni)")
        return comuni
    return None


def _costruisci_db(comuni_raw):
    """
    Costruisce i dizionari di lookup dai dati grezzi.
    
    Il dataset comuni-json ha questa struttura per ogni comune:
    {
        "nome": "Torino",
        "codice": "001272",
        "cap": ["10121", "10122", ...],   # lista di CAP (o singolo)
        "provincia": {"nome": "Torino", "codice": "TO"},
        "regione": {"nome": "Piemonte", "codice": "01"},
        ...
    }
    """
    db = {}                    # nome_normalizzato -> info (backward compatibility)
    cap_prov = {}              # cap -> sigla_provincia
    comuni_per_prov = {}       # sigla -> {nome_norm: info}
    comuni_tutti = {}          # nome_norm -> list[info]
    prov_a_sigla = {}          # nome_provincia_upper / sigla -> sigla
    prov_a_reg = {}            # sigla -> regione_upper
    
    for comune in comuni_raw:
        nome = comune.get('nome', '')
        provincia_info = comune.get('provincia', {})
        sigla = str(comune.get('sigla', '')).strip().upper()
        cap_list = comune.get('cap', [])
        p_nome = str(provincia_info.get('nome', '')).strip().upper()
        reg_nome = str(comune.get('regione', {}).get('nome', '')).strip().upper()
        
        if not nome or not sigla:
            continue
        
        # Usa il primo CAP (per comuni con più CAP, es. Torino ha 10121-10156)
        cap_principale = cap_list[0] if cap_list else ''
        
        info = {
            'cap': cap_principale,
            'tutti_cap': cap_list,
            'provincia': sigla,
            'provincia_nome': provincia_info.get('nome', ''),
            'regione': reg_nome,
            'nome_originale': nome,
        }
        
        if p_nome:
            prov_a_sigla[p_nome] = sigla
        prov_a_sigla[sigla] = sigla
        if reg_nome:
            prov_a_reg[sigla] = reg_nome
        
        # Indicizza per nome normalizzato (con e senza apostrofi, con spazi)
        nome_norm = _normalizza_nome(nome)
        nome_no_ap = _normalizza_senza_apostrofi(nome)
        nome_spazi = _normalizza_spazi(nome)
        nome_upper = nome.strip().upper()
        
        db[nome_norm] = info
        if nome_no_ap != nome_norm:
            db[nome_no_ap] = info
        if nome_spazi not in db:
            db[nome_spazi] = info
        if nome_upper not in db:
            db[nome_upper] = info
        
        # Indice per provincia
        p_dict = comuni_per_prov.setdefault(sigla, {})
        p_dict[nome_norm] = info
        p_dict[nome_no_ap] = info
        p_dict[nome_spazi] = info
        p_dict[nome_upper] = info
        
        # Indice per tutti i comuni (gestione omonimie)
        for chiave in (nome_norm, nome_no_ap, nome_spazi, nome_upper):
            lista = comuni_tutti.setdefault(chiave, [])
            if not any(x['provincia'] == sigla and x['nome_originale'] == nome for x in lista):
                lista.append(info)
        
        # Mappa CAP -> provincia
        for cap in cap_list:
            cap_prov[cap] = sigla
    
    return db, cap_prov, comuni_per_prov, comuni_tutti, prov_a_sigla, prov_a_reg


def _inizializza():
    """Inizializza il database dei comuni (una volta sola per sessione)."""
    global _db_comuni, _db_cap_provincia, _db_comuni_per_provincia
    global _db_comuni_tutti, _db_provincia_a_sigla, _db_provincia_a_regione
    
    if _db_comuni is not None:
        return  # Già inizializzato
    
    # 1. Prova cache valida
    comuni_raw = _carica_cache()
    
    # 2. Se cache scaduta/assente, scarica
    if comuni_raw is None:
        comuni_raw = _scarica_comuni()
        if comuni_raw:
            _salva_cache(comuni_raw)
    
    # 3. Se download fallito, usa cache scaduta come fallback
    if comuni_raw is None:
        comuni_raw = _carica_cache_forzata()
    
    # 4. Costruisci DB
    if comuni_raw:
        (
            _db_comuni,
            _db_cap_provincia,
            _db_comuni_per_provincia,
            _db_comuni_tutti,
            _db_provincia_a_sigla,
            _db_provincia_a_regione,
        ) = _costruisci_db(comuni_raw)
        print(f"🗄️  Database comuni pronto: {len(_db_comuni)} voci indicizzate")
    else:
        print("⚠️  Database comuni non disponibile. CAP e provincia non saranno compilati.")
        _db_comuni = {}
        _db_cap_provincia = {}
        _db_comuni_per_provincia = {}
        _db_comuni_tutti = {}
        _db_provincia_a_sigla = {}
        _db_provincia_a_regione = {}


def risolvi_sigla_provincia(provincia_o_nome=None):
    """
    Risolve il nome o la sigla di una provincia nella sua sigla a 2 lettere (es. 'PAVIA' -> 'PV').
    Se non specificato, prova a leggerlo dinamicamente da config.PROVINCIA_ATTIVA o env PROVINCIA.
    """
    _inizializza()
    cand = provincia_o_nome
    if not cand:
        try:
            from config import PROVINCIA_ATTIVA
            cand = PROVINCIA_ATTIVA
        except Exception:
            cand = os.environ.get('PROVINCIA', '')
    if not cand:
        return ''
    c_str = str(cand).strip().upper()
    if _db_provincia_a_sigla and c_str in _db_provincia_a_sigla:
        return _db_provincia_a_sigla[c_str]
    if len(c_str) == 2 and c_str.isalpha():
        return c_str
    static_map = {
        'TORINO': 'TO',
        'PAVIA': 'PV',
        'LECCO': 'LC',
        'MILANO': 'MI',
        'LODI': 'LO',
    }
    return static_map.get(c_str, c_str if len(c_str) == 2 else '')


def _cerca_comune(nome_comune, provincia=None):
    """
    Cerca un comune nel database.
    
    Args:
        nome_comune: Nome del comune o frazione (qualsiasi formato)
        provincia: Sigla o nome della provincia per disambiguare omonimie e vincolare la ricerca
    
    Returns:
        Dict con info comune, o None se non trovato
    """
    _inizializza()
    
    if not nome_comune:
        return None
    
    nome_str = str(nome_comune).strip()
    if not nome_str or nome_str.lower() in ('nan', 'null'):
        return None
    
    target_sigla = risolvi_sigla_provincia(provincia)
    nome_upper = nome_str.upper()
    nome_norm = _normalizza_nome(nome_str)
    nome_no_ap = _normalizza_senza_apostrofi(nome_str)
    nome_spazi = _normalizza_spazi(nome_str)
    
    # 1. Se abbiamo una provincia target (es. PV, TO, LC), cerca PRIMA all'interno di quella provincia
    if target_sigla and _db_comuni_per_provincia and target_sigla in _db_comuni_per_provincia:
        prov_db = _db_comuni_per_provincia[target_sigla]
        
        # 1a. Match esatto nella provincia target (priorità assoluta)
        for chiave in (nome_upper, nome_norm, nome_no_ap, nome_spazi):
            if chiave in prov_db:
                return prov_db[chiave]
        
        # 1b. Match prefisso / troncamento nella provincia target (per nomi Enel troncati)
        # es. "SAN GENESIO ED UNI" -> "SAN GENESIO ED UNITI"
        # es. "SANT ALESSIO CON V" -> "SANT'ALESSIO CON VIALONE"
        # es. "SANTA MARGHERITA D" -> "SANTA MARGHERITA DI STAFFORA"
        # Richiede almeno 4 caratteri per evitare match banali
        if len(nome_norm) >= 4:
            for k, info in prov_db.items():
                if k.startswith(nome_norm) or k.startswith(nome_no_ap) or k.startswith(nome_spazi):
                    return info
            for k, info in prov_db.items():
                if len(k) >= 4 and (
                    nome_norm.startswith(k) or nome_no_ap.startswith(k) or nome_spazi.startswith(k)
                ):
                    return info
            # Substring: l'input (lungo >= 5 caratteri) è contenuto nel nome ufficiale della provincia target
            # es. "ZENONE AL PO" in "SAN ZENONE AL PO"
            if len(nome_norm) >= 5:
                for k, info in prov_db.items():
                    if nome_norm in k or nome_no_ap in k or nome_spazi in k:
                        return info

    # 2. Cerca in tutta Italia per match ESATTO del nome
    candidati = []
    if _db_comuni_tutti:
        for chiave in (nome_upper, nome_norm, nome_no_ap, nome_spazi):
            if chiave in _db_comuni_tutti:
                candidati = _db_comuni_tutti[chiave]
                break

    if candidati:
        # Se tra i candidati c'è quello della provincia target, sceglilo! (Risoluzione omonimia)
        if target_sigla:
            for c in candidati:
                if c['provincia'] == target_sigla:
                    return c
            
            # Se target_sigla è definita ma il comune non appartiene alla provincia target:
            # Verifica se appartiene alla stessa regione (es. LODI in LO per PV, o San Colombano in MI per PV)
            target_regione = _db_provincia_a_regione.get(target_sigla, '') if _db_provincia_a_regione else ''
            if target_regione:
                stessa_regione = [
                    c for c in candidati
                    if _db_provincia_a_regione.get(c['provincia']) == target_regione
                ]
                if stessa_regione:
                    return stessa_regione[0]
            
            # Comune appartiene a regione/provincia lontana e scorrelata (es. TV per PV, CT per PV, LE per TO).
            # Rifiutiamo il match per evitare assegnazioni errate di CAP e province remote.
            return None
        
        # Se non c'era una provincia target, ritorna il primo candidato
        return candidati[0]

    # 3. Se NON c'è target_sigla, fallback compatibile per match parziale nazionale (solo se len >= 5 e prefix)
    if not target_sigla and _db_comuni and len(nome_norm) >= 5:
        for k, info in _db_comuni.items():
            if k.startswith(nome_norm) or (len(k) >= 5 and nome_norm.startswith(k)):
                return info

    return None


def cerca_cap(comune, provincia=None):
    """
    Cerca il CAP di un comune italiano.
    
    Args:
        comune: Nome del comune
        provincia: Sigla o nome della provincia per disambiguare omonimie (opzionale)
    
    Returns:
        CAP come stringa (es. '26900'), o stringa vuota se non trovato
    """
    info = _cerca_comune(comune, provincia=provincia)
    return info['cap'] if info else ''


def cerca_provincia(comune, provincia=None):
    """
    Cerca la sigla provincia di un comune italiano.
    
    Args:
        comune: Nome del comune
        provincia: Sigla o nome della provincia per disambiguare omonimie (opzionale)
    
    Returns:
        Sigla provincia (es. 'TO', 'PV', 'LO'), o la sigla della provincia target come fallback
    """
    info = _cerca_comune(comune, provincia=provincia)
    if info:
        return info['provincia']
    
    # Fallback dinamico: provincia specificata o provincia attiva da config/env
    target = risolvi_sigla_provincia(provincia)
    if target:
        return target
    
    try:
        from config import PROVINCIA_ATTIVA
        _sigla_map = {'TORINO': 'TO', 'PAVIA': 'PV', 'LECCO': 'LC'}
        return _sigla_map.get(PROVINCIA_ATTIVA.upper(), '')
    except Exception:
        return ''


def cerca_info_comune(comune, provincia=None):
    """
    Cerca tutte le info disponibili per un comune.
    
    Args:
        comune: Nome del comune
        provincia: Sigla o nome della provincia per disambiguare omonimie (opzionale)
    
    Returns:
        Dict con chiavi: cap, tutti_cap, provincia, provincia_nome, nome_originale
        Oppure None se non trovato
    """
    return _cerca_comune(comune, provincia=provincia)


def aggiorna_cache():
    """
    Forza l'aggiornamento della cache scaricando i dati freschi.
    Utile da chiamare manualmente se servono dati aggiornati.
    """
    global _db_comuni, _db_cap_provincia, _db_comuni_per_provincia
    global _db_comuni_tutti, _db_provincia_a_sigla, _db_provincia_a_regione
    
    comuni_raw = _scarica_comuni()
    if comuni_raw:
        _salva_cache(comuni_raw)
        (
            _db_comuni,
            _db_cap_provincia,
            _db_comuni_per_provincia,
            _db_comuni_tutti,
            _db_provincia_a_sigla,
            _db_provincia_a_regione,
        ) = _costruisci_db(comuni_raw)
        print(f"🗄️  Database aggiornato: {len(_db_comuni)} voci")
        return True
    else:
        print("❌ Aggiornamento fallito")
        return False
