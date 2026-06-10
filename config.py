# -*- coding: utf-8 -*-
"""
Configurazioni globali del progetto.
Supporta multiple province con switch facile.
"""
import sys
import os

# Forza encoding UTF-8 per stdout/stderr se necessario
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass
if sys.stderr.encoding != 'utf-8':
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

# ============================================================================
# SELEZIONE PROVINCIA ATTIVA
# Può essere impostata via:
# 1. Variabile d'ambiente PROVINCIA (es. set PROVINCIA=PAVIA)
# 2. Argomento --provincia (es. python main.py --provincia PAVIA)
# 3. Modificando direttamente questa variabile
# ============================================================================
_PROVINCIA_DEFAULT = 'TORINO'  # Default se non specificato altrove

# ============================================================================
# CONFIGURAZIONI PER PROVINCIA
# ============================================================================
# Le definizioni vivono in `data/provinces.json` (file tracciato in git).
# Per aggiungere/rimuovere una provincia: modificare provinces.json
# (manualmente o via UI admin di gestione province) e riavviare.
# ============================================================================
_PROVINCE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              'data', 'provinces.json')


def _load_province_da_json():
    """Carica le definizioni province da data/provinces.json.

    Ritorna dict {SLUG_UPPER: dict}. Le chiavi vengono normalizzate UPPER
    per consistenza con il resto del codice.

    Il campo 'file_mappa' viene auto-derivato dallo slug se assente
    (es. provincia 'LECCO' → 'templates/mappa_lecco.html'), così l'UI di
    aggiunta provincia non deve preoccuparsene.
    """
    import json
    if not os.path.exists(_PROVINCE_FILE):
        raise FileNotFoundError(
            f"File configurazione province mancante: {_PROVINCE_FILE}. "
            "Crearlo (es. copiando il file dal repository) prima di avviare."
        )
    with open(_PROVINCE_FILE, 'r', encoding='utf-8') as f:
        raw = json.load(f)

    out = {}
    for slug, cfg in raw.items():
        slug_upper = slug.upper()
        cfg.setdefault('file_mappa',
                       os.path.join('templates', f'mappa_{slug_upper.lower()}.html'))
        out[slug_upper] = cfg
    return out


PROVINCE = _load_province_da_json()

# Leggi da variabile d'ambiente se presente
PROVINCIA_ATTIVA = os.environ.get('PROVINCIA', _PROVINCIA_DEFAULT).upper()

# Verifica validità: la provincia richiesta deve esistere in PROVINCE.
# (Niente whitelist hardcoded: la lista è derivata da PROVINCE.)
if PROVINCIA_ATTIVA not in PROVINCE:
    province_disponibili = ', '.join(sorted(PROVINCE.keys()))
    print(f"⚠️ Provincia '{PROVINCIA_ATTIVA}' non configurata. "
          f"Disponibili: {province_disponibili}. Uso default: {_PROVINCIA_DEFAULT}")
    PROVINCIA_ATTIVA = _PROVINCIA_DEFAULT if _PROVINCIA_DEFAULT in PROVINCE else next(iter(PROVINCE))

# ============================================================================
# VARIABILI DINAMICHE (basate su PROVINCIA_ATTIVA)
# ============================================================================
_provincia = PROVINCE[PROVINCIA_ATTIVA]

DEPOSITO = _provincia['deposito']
MATRICOLE_VIP = _provincia['operatori_vip']
COLORI_TECNICI = _provincia['colori_operatori']
OPERATORI_DICT = _provincia.get('operatori_dict', {})  # Per dropdown gestionale
FILE_MAPPA = _provincia['file_mappa']
ORA_INIZIO_GIORNATA = _provincia.get('ora_inizio_giornata', 8 * 60)  # Default 8:00
ORA_FINE_GIORNATA = _provincia.get('ora_fine_giornata', 17 * 60)  # Default 17:00
USA_LOCALITA_COME_COMUNE = _provincia.get('usa_localita_come_comune', False)

# Campi modificabili (centralizzati - colonne unificate tra province)
_CAMPI_MODIFICABILI_COMUNI = {
    'data_pian': 'DATA PIAN',
    'note_app': 'NOTE APP',
    'nominativo': 'NOMINATIVO',
    'indirizzo': 'Indirizzo',
    'civico': 'Num',
    'telefono': 'telefono',
    'email': 'email',
    'posizione_mis': 'Posizione_Ante_Mis',
    'operatore': 'Operatore',
}
# Campi BOXBEAT/SIGILLI/MODIFICABILI: oggi identici tra Torino e Pavia.
# Vengono replicati per ogni provincia configurata in PROVINCE così che una
# nuova provincia aggiunta via UI eredita questi default senza dover ridichiarare.
_CAMPI_BOXBEAT_COMUNI = {
    'matricola_preposto': 'Matricola preposto',
    'data_esecuzione': 'Data esecuzione',
    'esito': 'Esito',
    'esito_ok_valore': 'Esito OK',
    'colonna_dest_operatore': 'Operatore',
    'colonna_dest_data_inst': 'Data Installazione',
    'colonna_dest_esito': 'Esito',
}
_COLONNE_SIGILLI_COMUNI = {
    'colonna_sigillo': 'SIGILLO',
    'colonna_data_inst': 'Data Installazione',
    'colonna_operatore': 'Operatore',
    'colonna_esito': 'Esito',
    'esito_ok_valore': 'Esito OK',
    'colonna_matricola': 'Cod. Installato',
}

CAMPI_MODIFICABILI = {p: _CAMPI_MODIFICABILI_COMUNI for p in PROVINCE}
CAMPI_BOXBEAT = {p: _CAMPI_BOXBEAT_COMUNI for p in PROVINCE}
COLONNE_SIGILLI = {p: _COLONNE_SIGILLI_COMUNI for p in PROVINCE}

print(f"🗺️  Provincia attiva: {_provincia['nome'].upper()}")

# ============================================================================
# MAPPATURA COLONNE FILONE → CHIAVI INTERNE
# Ogni chiave interna ha una lista di nomi colonna possibili (case insensitive).
# La prima corrispondenza trovata nel foglio viene usata.
# Può essere sovrascritta da settings.json per rimappare colonne senza toccare il codice.
# ============================================================================
MAPPATURA_COLONNE_DEFAULT = {
    'data_pianificata': {
        'label': 'Data Pianificata',
        'descrizione': 'Data programmata per l\'intervento',
        'nomi_default': ['DATA PIAN', 'DATA_PIAN', 'Data Pian', 'DATA PIANIFICATA', 'PROG', 'Prog', 'DATA'],
    },
    'eneltel': {
        'label': 'Eneltel',
        'descrizione': 'Codice identificativo contatore Enel',
        'nomi_default': ['Eneltel', 'ENELTEL'],
    },
    'nominativo': {
        'label': 'Nominativo',
        'descrizione': 'Nome del cliente',
        'nomi_default': ['NOMINATIVO', 'Nominativo', 'CLIENTE', 'Cliente', 'NOME', 'Nome'],
    },
    'comune': {
        'label': 'Comune',
        'descrizione': 'Comune di residenza',
        'nomi_default': ['Comune', 'COMUNE'],
    },
    'localita': {
        'label': 'Località',
        'descrizione': 'Località (dettaglio aggiuntivo)',
        'nomi_default': ['LOCALITA', 'Localita', 'Località', 'località'],
    },
    'indirizzo': {
        'label': 'Indirizzo',
        'descrizione': 'Via / indirizzo',
        'nomi_default': ['Indirizzo', 'Via', 'VIA', 'Via Num', 'VIA NUM'],
    },
    'civico': {
        'label': 'Civico',
        'descrizione': 'Numero civico',
        'nomi_default': ['Num', 'NUM', 'civico', 'CIVICO', 'Numero civico', 'N. Civico'],
    },
    'telefono': {
        'label': 'Telefono',
        'descrizione': 'Numero di telefono',
        'nomi_default': ['telefono', 'TELEFONO', 'Telefono', 'TEL', 'Tel'],
    },
    'email': {
        'label': 'Email',
        'descrizione': 'Indirizzo email',
        'nomi_default': ['email', 'EMAIL', 'Email', 'E-mail', 'e-mail', 'E-MAIL', 'Mail', 'MAIL'],
    },
    'pod': {
        'label': 'POD',
        'descrizione': 'Point of Delivery',
        'nomi_default': ['POD', 'Pod', 'pod'],
    },
    'posizione_misuratore': {
        'label': 'Posizione Misuratore',
        'descrizione': 'Posizione/ubicazione del misuratore',
        'nomi_default': ['Posizione_Mis_Ante_Sost', 'Posizione_Ante_Mis', 'Posizione misuratore', 'POSIZIONE', 'Posizione', 'Posizione Misuratore'],
    },
    'tipo_misuratore': {
        'label': 'Tipo Misuratore',
        'descrizione': 'Tipologia del contatore',
        'nomi_default': ['Tipo_Misuratore', 'TIPOLOGIA CE', 'Tipologia CE', 'TIPO CE', 'Tipo CE', 'Tipologia'],
    },
    'operatore': {
        'label': 'Operatore',
        'descrizione': 'Matricola tecnico assegnato',
        'nomi_default': ['Operatore', 'OPERATORE', 'TECNICO', 'Tecnico', 'Matricola'],
    },
    'latitudine': {
        'label': 'Latitudine',
        'descrizione': 'Coordinata GPS latitudine',
        'nomi_default': ['Latitudine', 'LATITUDINE', 'Lat', 'LAT'],
    },
    'longitudine': {
        'label': 'Longitudine',
        'descrizione': 'Coordinata GPS longitudine',
        'nomi_default': ['Longitudine', 'LONGITUDINE', 'Lon', 'LON'],
    },
    'lcl': {
        'label': 'LCL',
        'descrizione': 'Codice LCL',
        'nomi_default': ['LCL', 'lcl', 'Lcl'],
    },
    'note_appuntamento': {
        'label': 'Note Appuntamento',
        'descrizione': 'Colonna note / appuntamenti',
        'nomi_default': ['NOTE APP', 'NOTE_APP', 'Note App', 'NOTE APPUNTAMENTO', 'NOTE', 'Note'],
    },
    'orari_lettere': {
        'label': 'Orari Lettere',
        'descrizione': 'Fasce orarie delle lettere',
        'nomi_default': ['Orari', 'ORARI', 'orari', 'Orari lettere', 'ORARI LETTERE'],
    },
    'esito': {
        'label': 'Esito',
        'descrizione': 'Stato esito installazione',
        'nomi_default': ['Esito', 'ESITO', 'esito'],
    },
    'smart_test': {
        'label': 'Smart Test',
        'descrizione': 'Esito smart test',
        'nomi_default': ['SMART TEST', 'Smart Test', 'smart test'],
    },
    'sigillo': {
        'label': 'Sigillo',
        'descrizione': 'Numero sigillo applicato',
        'nomi_default': ['SIGILLO', 'Sigillo', 'sigillo'],
    },
    'data_installazione': {
        'label': 'Data Installazione',
        'descrizione': 'Data effettiva dell\'installazione',
        'nomi_default': ['Data Installazione', 'DATA INSTALLAZIONE', 'Data_Installazione'],
    },
    'codice_ante': {
        'label': 'Codice Misuratore Ante',
        'descrizione': 'Codice misuratore in opera (prima della sostituzione)',
        'nomi_default': ['Codice_Ante_Sostituzione', 'Codice misuratore in opera', 'CODICE ANTE'],
    },
    'matricola_ante': {
        'label': 'Matricola Misuratore Ante',
        'descrizione': 'Matricola misuratore in opera (prima della sostituzione)',
        'nomi_default': ['Matricola_Ante_Sostituzione', 'Matricola misuratore in opera', 'MATRICOLA ANTE'],
    },
    'matricola_installata': {
        'label': 'Matricola CE Installato',
        'descrizione': 'Matricola del contatore installato',
        'nomi_default': ['Cod. Installato'],
    },
    'codice_installato': {
        'label': 'Codice CE Installato',
        'descrizione': 'Codice del contatore installato',
        'nomi_default': ['Matr. Installato'],
    },
}

# Mappatura personalizzata (sovrascritta da settings.json)
# Formato: { "chiave_interna": "nome_colonna_sul_foglio" }
MAPPATURA_COLONNE = {}

# Configurazione Excel lettere. La sezione `lettere_excel` in
# data/{provincia}/settings.json può sovrascrivere questi default dalla UI admin.
LETTERE_EXCEL_CONFIG_DEFAULT = {
    'fogli': {
        'monofase': 'MONOFASE',
        'emme2': 'EMME2',
        'semidiretti': 'SEMIDIRETTI',
        'contatti_aziendali': 'Contatti Aziendali',
        'solo_monofase': 'SOLO MONOFASE',
        'solo_trifase_diretti': 'SOLO TRIFASE DIRETTI',
    },
    'nomi_colonne': {
        'nominativo': 'Nominativo_Cliente',
        'via': 'VIA',
        'civico': 'Numero_Civico',
        'comune': 'Comune',
        'provincia': 'PROVINCIA',
        'cap': 'CAP',
        'data_pianificazione': 'Data Pianificazione',
        'ora_pianificazione': 'Ora pianificazione',
        'lettera': 'LETTERA',
        'pod': 'POD',
        'operatore': 'Operatore',
        'posizione_mis': 'Posizione mis.',
        'appuntamento': 'Appuntamento',
    },
    'valori_lettera': {
        'si': "SI'",
        'no': 'NO',
    },
    'regole_no': {
        'appuntamento': True,
        'accessibile_esterno': True,
        'civico_vuoto': True,
        'civico_senza_numero': True,
    },
    'parole_chiave': {
        'lettera_no': ['COMUNE', 'PROVINCIA'],
        'contatti_aziendali_escludi': ['COMUNE', 'PROVINCIA'],
    },
    'opzioni': {
        'unisci_trifase_diretti_in_monofase': True,
        'crea_fogli_dettaglio': True,
        'escludi_enti_da_contatti_aziendali': True,
    },
}


def merge_lettere_excel_config(override=None):
    """Unisce ricorsivamente i default Excel lettere con eventuali override."""
    def _merge(default, custom):
        if isinstance(default, dict):
            out = {}
            custom = custom if isinstance(custom, dict) else {}
            for key, value in default.items():
                out[key] = _merge(value, custom.get(key))
            for key, value in custom.items():
                if key not in out:
                    out[key] = value
            return out
        return custom if custom is not None else default

    return _merge(LETTERE_EXCEL_CONFIG_DEFAULT, override or {})


LETTERE_EXCEL_CONFIG = merge_lettere_excel_config()

# ============================================================================
# CONFIGURAZIONI COMUNI (uguali per tutte le province)
# ============================================================================

# --- FILTRO DATE ---
DATA_INIZIO = _provincia.get('data_inizio') or '2025-10-01'
DATA_FINE = _provincia.get('data_fine') or ''  # Vuoto = illimitata

# --- CONFIGURAZIONE CALCOLO DISTANZE ---
# Server OSRM locale (Docker) - molto più veloce e senza limiti
# Per usare il server pubblico: "http://router.project-osrm.org"
# Per usare il server locale: "http://localhost:5000"
OSRM_SERVER = "http://localhost:5000"
OSRM_TIMEOUT = 30
# Velocità media e tempo intervento: default per provincia (override in
# provinces.json sotto la chiave del slug, eventualmente sovrascritto da
# settings.json per compatibilità con i vecchi config).
VELOCITA_MEDIA_KMH = _provincia.get('velocita_media_kmh', 35)
TEMPO_INTERVENTO_MIN = _provincia.get('tempo_intervento_min', 20)
FATTORE_CORREZIONE_HAVERSINE = 1.35
USA_OSRM = True

# Nota: l'ottimizzazione per tempo (preferenza autostrade) e' implicita nel
# profilo "driving" di OSRM, che restituisce il percorso piu' VELOCE (non il
# piu' breve in km). Non serve un toggle dedicato.
# Il vecchio raggruppamento in "micro-zone" e' stato rimosso con la
# riscrittura di moduli/percorso.py: ora il TSP e' risolto direttamente da
# OSRM Trip API sulla rete stradale reale.

# --- CONFIGURAZIONE CACHE ---
FILE_CACHE_DISTANZE = os.path.join('cache', f'cache_distanze_{PROVINCIA_ATTIVA.lower()}.json')
FILE_CACHE_HASH = os.path.join('cache', f'cache_hash_{PROVINCIA_ATTIVA.lower()}.json')

# --- CONFIGURAZIONE LOOP ---
# Post-disconnessione Sheets (21/05/2026) il loop principale è event-driven:
# le modifiche utente / import Excel risvegliano subito il sync. Questo valore
# resta solo come fallback per il polling client (refreshInterval mappa) nei
# casi rari di disconnessione SSE prolungata.
INTERVALLO_SYNC_SECONDI = 300  # 5 minuti - fallback polling client se SSE down

# --- CONFIGURAZIONE DB LOCALE (SQLite) ---
# Post-disconnessione Google Sheets (2026-05-21) il DB è l'unica fonte di
# verità. I dati esterni entrano via import Excel manuale.
DB_PATH = os.path.join('data', PROVINCIA_ATTIVA.lower(), 'state.db')

# Worker asincrono che drena pending_writes verso Sheets
# 30s = compromesso tra latenza per chi guarda Sheets direttamente e quota 429.
# Le scritture in raffica vengono coalesce per (eneltel,col) → la quota
# Read/min (60) e Write/min (60) sono protette anche con bursti utente.
DRAIN_INTERVAL_SECONDI = 30
DRAIN_BATCH_SIZE = 400  # max celle per chiamata batch_update (limite Sheets ~500)
DRAIN_THROTTLE_SECONDI = 2  # min interval tra due batch_update (rate-limit Sheets)

# Policy per conflitti rilevati durante il pull
# 'local_wins' → la modifica locale pending sovrascriverà il valore remoto (default)
# 'remote_wins'→ il valore remoto viene applicato e il pending viene scartato (audit)
# 'manual'    → blocca la riga finché un admin non risolve via UI
CONFLICT_POLICY = 'local_wins'



# ============================================================================
# SOVRASCRITTURE DA SETTINGS.JSON (impostazioni utente via interfaccia mappa)
# ============================================================================
import json as _json

_settings_file = os.path.join('data', PROVINCIA_ATTIVA.lower(), 'settings.json')
if os.path.exists(_settings_file):
    try:
        with open(_settings_file, 'r', encoding='utf-8') as _f:
            _user_settings = _json.load(_f)
        
        # Sovrascritture per provincia
        if 'operatori_vip' in _user_settings:
            MATRICOLE_VIP = _user_settings['operatori_vip']
            PROVINCE[PROVINCIA_ATTIVA]['operatori_vip'] = MATRICOLE_VIP
        if 'colori_operatori' in _user_settings:
            COLORI_TECNICI = _user_settings['colori_operatori']
            PROVINCE[PROVINCIA_ATTIVA]['colori_operatori'] = COLORI_TECNICI
        if 'operatori_dict' in _user_settings:
            OPERATORI_DICT = _user_settings['operatori_dict']
            PROVINCE[PROVINCIA_ATTIVA]['operatori_dict'] = OPERATORI_DICT
        if 'deposito' in _user_settings:
            DEPOSITO = _user_settings['deposito']
            PROVINCE[PROVINCIA_ATTIVA]['deposito'] = DEPOSITO
        if 'ora_inizio_giornata' in _user_settings:
            ORA_INIZIO_GIORNATA = _user_settings['ora_inizio_giornata']
            PROVINCE[PROVINCIA_ATTIVA]['ora_inizio_giornata'] = ORA_INIZIO_GIORNATA
        if 'ora_fine_giornata' in _user_settings:
            ORA_FINE_GIORNATA = _user_settings['ora_fine_giornata']
            PROVINCE[PROVINCIA_ATTIVA]['ora_fine_giornata'] = ORA_FINE_GIORNATA
        
        # Sovrascritture comuni
        if 'velocita_media_kmh' in _user_settings:
            VELOCITA_MEDIA_KMH = _user_settings['velocita_media_kmh']
        if 'tempo_intervento_min' in _user_settings:
            TEMPO_INTERVENTO_MIN = _user_settings['tempo_intervento_min']
        # 'raggio_microzona_km', 'ottimizza_per_tempo', 'intervallo_sync_secondi'
        # (deprecati): se presenti in vecchi settings.json vengono ignorati.
        if 'data_inizio' in _user_settings:
            DATA_INIZIO = _user_settings['data_inizio']
        if 'data_fine' in _user_settings:
            DATA_FINE = _user_settings['data_fine']
        
        # Mappatura colonne personalizzata
        if 'mappatura_colonne' in _user_settings:
            MAPPATURA_COLONNE = _user_settings['mappatura_colonne']
        if 'lettere_excel' in _user_settings:
            LETTERE_EXCEL_CONFIG = merge_lettere_excel_config(_user_settings['lettere_excel'])
            PROVINCE[PROVINCIA_ATTIVA]['lettere_excel'] = LETTERE_EXCEL_CONFIG
        
        print("✅ Impostazioni personalizzate caricate da settings.json")
    except Exception as _e:
        print(f"⚠️  Errore caricamento settings.json: {_e}")
