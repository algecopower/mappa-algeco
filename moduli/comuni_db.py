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
import json
import time
import re
import unicodedata
import requests

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


def _scarica_comuni():
    """
    Scarica il dataset dei comuni da GitHub.
    
    Returns:
        Lista di dizionari con i dati dei comuni, o None se fallisce
    """
    try:
        print(f"📥 Download database comuni italiani...")
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
    try:
        cache_data = {
            'timestamp': time.time(),
            'comuni': comuni_raw
        }
        with open(_FILE_CACHE, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, ensure_ascii=False)
        print(f"💾 Cache salvata: {_FILE_CACHE}")
    except Exception as e:
        print(f"⚠️  Errore salvataggio cache: {e}")


def _carica_cache():
    """
    Carica i dati dalla cache locale se esistente e non scaduta.
    
    Returns:
        Lista di dizionari comuni, o None se cache assente/scaduta
    """
    if not os.path.exists(_FILE_CACHE):
        return None
    
    try:
        with open(_FILE_CACHE, 'r', encoding='utf-8') as f:
            cache_data = json.load(f)
        
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
    except Exception as e:
        print(f"⚠️  Errore lettura cache: {e}")
    
    return None


def _carica_cache_forzata():
    """Carica la cache anche se scaduta (fallback se download fallisce)."""
    if not os.path.exists(_FILE_CACHE):
        return None
    try:
        with open(_FILE_CACHE, 'r', encoding='utf-8') as f:
            cache_data = json.load(f)
        comuni = cache_data.get('comuni', [])
        if comuni:
            print(f"📦 Uso cache comuni esistente come fallback ({len(comuni)} comuni)")
            return comuni
    except Exception:
        pass
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
    db = {}         # nome_normalizzato -> info
    cap_prov = {}   # cap -> sigla_provincia
    
    for comune in comuni_raw:
        nome = comune.get('nome', '')
        provincia_info = comune.get('provincia', {})
        sigla = comune.get('sigla', '')  # Sigla a livello root (es. 'TO', 'PV', 'LO')
        cap_list = comune.get('cap', [])
        
        if not nome or not sigla:
            continue
        
        # Usa il primo CAP (per comuni con più CAP, es. Torino ha 10121-10156)
        cap_principale = cap_list[0] if cap_list else ''
        
        info = {
            'cap': cap_principale,
            'tutti_cap': cap_list,
            'provincia': sigla,
            'provincia_nome': provincia_info.get('nome', ''),
            'nome_originale': nome,
        }
        
        # Indicizza per nome normalizzato (con e senza apostrofi)
        nome_norm = _normalizza_nome(nome)
        nome_no_ap = _normalizza_senza_apostrofi(nome)
        
        db[nome_norm] = info
        if nome_no_ap != nome_norm:
            db[nome_no_ap] = info
        
        # Indicizza anche versione UPPER semplice
        nome_upper = nome.strip().upper()
        if nome_upper not in db:
            db[nome_upper] = info
        
        # Mappa CAP -> provincia
        for cap in cap_list:
            cap_prov[cap] = sigla
    
    return db, cap_prov


def _inizializza():
    """Inizializza il database dei comuni (una volta sola per sessione)."""
    global _db_comuni, _db_cap_provincia
    
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
        _db_comuni, _db_cap_provincia = _costruisci_db(comuni_raw)
        print(f"🗄️  Database comuni pronto: {len(_db_comuni)} voci indicizzate")
    else:
        print("⚠️  Database comuni non disponibile. CAP e provincia non saranno compilati.")
        _db_comuni = {}
        _db_cap_provincia = {}


def _cerca_comune(nome_comune):
    """
    Cerca un comune nel database.
    
    Args:
        nome_comune: Nome del comune (qualsiasi formato)
    
    Returns:
        Dict con info comune, o None se non trovato
    """
    _inizializza()
    
    if not nome_comune:
        return None
    
    # Prova match diretto (UPPER)
    nome_upper = str(nome_comune).strip().upper()
    if nome_upper in _db_comuni:
        return _db_comuni[nome_upper]
    
    # Prova normalizzato (senza accenti)
    nome_norm = _normalizza_nome(nome_comune)
    if nome_norm in _db_comuni:
        return _db_comuni[nome_norm]
    
    # Prova senza apostrofi
    nome_no_ap = _normalizza_senza_apostrofi(nome_comune)
    if nome_no_ap in _db_comuni:
        return _db_comuni[nome_no_ap]
    
    # Prova match parziale per nomi lunghi
    if len(nome_norm) > 5:
        for key, info in _db_comuni.items():
            if nome_norm in key or key in nome_norm:
                return info
    
    return None


def cerca_cap(comune):
    """
    Cerca il CAP di un comune italiano.
    
    Args:
        comune: Nome del comune
    
    Returns:
        CAP come stringa (es. '26900'), o stringa vuota se non trovato
    """
    info = _cerca_comune(comune)
    return info['cap'] if info else ''


def cerca_provincia(comune):
    """
    Cerca la sigla provincia di un comune italiano.
    
    Args:
        comune: Nome del comune
    
    Returns:
        Sigla provincia (es. 'TO', 'PV', 'LO'), o stringa vuota se non trovato
    """
    info = _cerca_comune(comune)
    if info:
        return info['provincia']
    
    # Fallback: provincia attiva dal config
    try:
        from config import PROVINCIA_ATTIVA
        _sigla_map = {'TORINO': 'TO', 'PAVIA': 'PV'}
        return _sigla_map.get(PROVINCIA_ATTIVA.upper(), '')
    except Exception:
        return ''


def cerca_info_comune(comune):
    """
    Cerca tutte le info disponibili per un comune.
    
    Args:
        comune: Nome del comune
    
    Returns:
        Dict con chiavi: cap, tutti_cap, provincia, provincia_nome, nome_originale
        Oppure None se non trovato
    """
    return _cerca_comune(comune)


def aggiorna_cache():
    """
    Forza l'aggiornamento della cache scaricando i dati freschi.
    Utile da chiamare manualmente se servono dati aggiornati.
    """
    global _db_comuni, _db_cap_provincia
    
    comuni_raw = _scarica_comuni()
    if comuni_raw:
        _salva_cache(comuni_raw)
        _db_comuni, _db_cap_provincia = _costruisci_db(comuni_raw)
        print(f"🗄️  Database aggiornato: {len(_db_comuni)} voci")
        return True
    else:
        print("❌ Aggiornamento fallito")
        return False
