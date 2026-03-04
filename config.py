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

# Leggi da variabile d'ambiente se presente
PROVINCIA_ATTIVA = os.environ.get('PROVINCIA', _PROVINCIA_DEFAULT).upper()

# Verifica validità
if PROVINCIA_ATTIVA not in ['TORINO', 'PAVIA']:
    print(f"⚠️ Provincia '{PROVINCIA_ATTIVA}' non valida. Uso default: {_PROVINCIA_DEFAULT}")
    PROVINCIA_ATTIVA = _PROVINCIA_DEFAULT

# ============================================================================
# MODALITÀ TEST (usa foglio di prova invece di produzione)
# Imposta MODALITA_TEST = True per lavorare su una copia del FILONE
# ============================================================================
MODALITA_TEST = False

# Fogli di TEST (copie per sviluppo sicuro)
FOGLI_TEST = {
    'TORINO': '1sdvUFF7zxU77OcJeuZ41i8Cgv9N1chQIwb6ZmWD9jdw',
    'PAVIA': None,  # Aggiungi ID quando crei la copia per Pavia
}

# ============================================================================
# CONFIGURAZIONI PER PROVINCIA
# ============================================================================
PROVINCE = {
    'TORINO': {
        'nome': 'Torino',
        'id_foglio': '1ZYDay6FgMZqArPxTInumGzPpZ1aMJ3XxTF_vmxPgwXw',
        'gid_scheda': 2061618218,
        'deposito': {'Nome': 'DEPOSITO VOLPIANO', 'Lat': 45.1964682, 'Lon': 7.7888303},  # Via Brandizzo 121d, 10088 Volpiano
        'operatori_vip': ['AE101392', 'AG49808', 'AG18966'],
        'colori_operatori': {
            'AE101392': '#e74c3c',      # Rosso
            'AG49808': '#f1c40f',       # Giallo
            'AG18966': '#2ecc71',       # Verde lime
        },
        # Dizionario nomi -> matricole per dropdown gestionale
        'operatori_dict': {
            'Operatore 1 (AE101392)': 'AE101392',
            'Operatore 2 (AG49808)': 'AG49808',
            'Operatore 3 (AG18966)': 'AG18966',
        },
        'file_mappa': os.path.join('templates', 'mappa_torino.html'),
        # Logica appuntamenti: cerca "APP" nella colonna NOTE APP
        'logica_appuntamenti': 'torino',
        # Orario inizio giornata tecnici (9:00 per Torino)
        'ora_inizio_giornata': 9 * 60,  # 9:00 in minuti
        # Orario fine giornata tecnici (17:00 per Torino)
        'ora_fine_giornata': 17 * 60,  # 17:00 in minuti
    },
    'PAVIA': {
        'nome': 'Pavia',
        'id_foglio': '1vyEooPBbmWf3D44NC-zRtZGjYMoUITOlGWX7fZf4wm0',
        'gid_scheda': 445238966,
        'deposito': {'Nome': 'DEPOSITO PAVIA', 'Lat': 45.2221001, 'Lon': 9.2254115},
        'operatori_vip': ['ALESSANDRO', 'MELEK', 'BRAHIM'],
        'colori_operatori': {
            'ALESSANDRO': '#e74c3c',    # Rosso
            'MELEK': '#f1c40f',         # Giallo
            'BRAHIM': '#2ecc71',        # Verde lime
        },
        # Dizionario nomi -> matricole per dropdown gestionale
        'operatori_dict': {
            'Alessandro': 'ALESSANDRO',
            'Melek': 'MELEK',
            'Brahim': 'BRAHIM',
        },
        'file_mappa': os.path.join('templates', 'mappa_pavia.html'),
        # Logica appuntamenti: colonna "Ora pian" con orario NON in formato "dalle x alle x"
        'logica_appuntamenti': 'pavia',
        'colonna_ora_pian': 'Ora pian',
        # Usa la colonna Località come Comune per raggruppamento zone
        'usa_localita_come_comune': True,
        # Orario inizio giornata tecnici (8:00 per Pavia)
        'ora_inizio_giornata': 8 * 60,  # 8:00 in minuti
        # Orario fine giornata tecnici (17:00 per Pavia)
        'ora_fine_giornata': 17 * 60,  # 17:00 in minuti
    },
}

# ============================================================================
# VARIABILI DINAMICHE (basate su PROVINCIA_ATTIVA)
# ============================================================================
_provincia = PROVINCE[PROVINCIA_ATTIVA]

# Usa foglio TEST se modalità test attiva
if MODALITA_TEST and FOGLI_TEST.get(PROVINCIA_ATTIVA):
    ID_FOGLIO_GOOGLE = FOGLI_TEST[PROVINCIA_ATTIVA]
    print(f"⚠️  MODALITÀ TEST ATTIVA - Usando foglio di prova")
else:
    ID_FOGLIO_GOOGLE = _provincia['id_foglio']
    if MODALITA_TEST:
        print(f"⚠️  Foglio test non configurato per {PROVINCIA_ATTIVA}, uso produzione")

GID_SCHEDA = _provincia['gid_scheda']
DEPOSITO = _provincia['deposito']
MATRICOLE_VIP = _provincia['operatori_vip']
COLORI_TECNICI = _provincia['colori_operatori']
OPERATORI_DICT = _provincia.get('operatori_dict', {})  # Per dropdown gestionale
FILE_MAPPA = _provincia['file_mappa']
LOGICA_APPUNTAMENTI = _provincia.get('logica_appuntamenti', 'torino')
COLONNA_ORA_PIAN = _provincia.get('colonna_ora_pian', None)
ORA_INIZIO_GIORNATA = _provincia.get('ora_inizio_giornata', 8 * 60)  # Default 8:00
ORA_FINE_GIORNATA = _provincia.get('ora_fine_giornata', 17 * 60)  # Default 17:00
USA_LOCALITA_COME_COMUNE = _provincia.get('usa_localita_come_comune', False)

# Campi modificabili per provincia (usati dal gestionale)
CAMPI_MODIFICABILI = {
    'TORINO': {
        'data_pian': 'DATA PIAN',
        'note_app': 'NOTE APP',
        'nominativo': 'NOMINATIVO',
        'indirizzo': 'Indirizzo',
        'civico': 'Num',
        'telefono': 'telefono',
        'posizione_mis': 'Posizione_Ante_Mis',
        'operatore': 'Operatore',
    },
    'PAVIA': {
        'note_app': 'NOTE',
        'nominativo': 'Nominativo',
        'indirizzo': 'Via',
        'civico': 'civico',
        'telefono': 'Telefono',
        'posizione_mis': 'Posizione misuratore',
        'operatore': 'Operatore',
    }
}

# Campi per import BOXBEAT (Enel)
CAMPI_BOXBEAT = {
    'TORINO': {
        'matricola_preposto': 'Matricola preposto',  # -> Operatore
        'data_esecuzione': 'Data esecuzione',        # -> Data Installazione (solo data)
        'esito': 'Esito',                            # -> Esito
        'esito_ok_valore': 'Esito OK',               # Valore che indica installazione riuscita
        'colonna_dest_operatore': 'Operatore',
        'colonna_dest_data_inst': 'Data Installazione',
        'colonna_dest_esito': 'Esito',
    },
    'PAVIA': {
        'matricola_preposto': 'Matricola preposto',
        'data_esecuzione': 'Data esecuzione',
        'esito': 'Esito',
        'esito_ok_valore': 'Esito OK',
        'colonna_dest_operatore': 'Operatore',
        'colonna_dest_data_inst': 'Data Installazione',
        'colonna_dest_esito': 'Esito',
    }
}

# Configurazione per gestione sigilli
COLONNE_SIGILLI = {
    'TORINO': {
        'colonna_sigillo': 'SIGILLO',
        'colonna_data_inst': 'Data Installazione',
        'colonna_operatore': 'Operatore',
        'colonna_esito': 'Esito',
        'esito_ok_valore': 'Esito OK',
        'colonna_matricola': 'Matricola CE inst',
    },
    'PAVIA': {
        'colonna_sigillo': 'SIGILLO',
        'colonna_data_inst': 'Data Installazione',
        'colonna_operatore': 'Operatore',
        'colonna_esito': 'Esito',
        'esito_ok_valore': 'Esito OK',
        'colonna_matricola': 'Matricola CE inst',  
    }
}

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
        'descrizione': 'Note / appuntamento (Torino: NOTE APP, Pavia: NOTE)',
        'nomi_default': ['NOTE APP', 'NOTE_APP', 'Note App', 'NOTE APPUNTAMENTO', 'NOTE', 'Note'],
    },
    'ora_pianificata': {
        'label': 'Ora Pianificata',
        'descrizione': 'Orario pianificato (usato da Pavia)',
        'nomi_default': ['Ora pian', 'ORA PIAN', 'ora pian'],
    },
    'orari_lettere': {
        'label': 'Orari Lettere',
        'descrizione': 'Fasce orarie delle lettere (usato da Torino)',
        'nomi_default': ['Orari', 'ORARI', 'orari', 'Orari lettere', 'ORARI LETTERE'],
    },
    'esito': {
        'label': 'Esito',
        'descrizione': 'Stato esito installazione',
        'nomi_default': ['Esito', 'ESITO', 'esito'],
    },
    'smart_test': {
        'label': 'Smart Test',
        'descrizione': 'Esito smart test (solo Torino)',
        'nomi_default': ['SMART TEST', 'Smart Test', 'smart test'],
    },
}

# Mappatura personalizzata (sovrascritta da settings.json)
# Formato: { "chiave_interna": "nome_colonna_sul_foglio" }
MAPPATURA_COLONNE = {}

# ============================================================================
# CONFIGURAZIONI COMUNI (uguali per tutte le province)
# ============================================================================
CREDENZIALI_JSON = 'credenziali.json'

# --- FILTRO DATE ---
DATA_INIZIO = '2025-10-01'
DATA_FINE = '2026-03-31'

# --- CONFIGURAZIONE CALCOLO DISTANZE ---
# Server OSRM locale (Docker) - molto più veloce e senza limiti
# Per usare il server pubblico: "http://router.project-osrm.org"
# Per usare il server locale: "http://localhost:5000"
OSRM_SERVER = "http://localhost:5000"
OSRM_TIMEOUT = 30
VELOCITA_MEDIA_KMH = 35
TEMPO_INTERVENTO_MIN = 20
FATTORE_CORREZIONE_HAVERSINE = 1.35
USA_OSRM = True

# --- CONFIGURAZIONE OTTIMIZZAZIONE PERCORSO ---
# L'algoritmo usa le DURATE (tempo) da OSRM, non le distanze in km.
# Questo significa che:
# - Le AUTOSTRADE vengono preferite quando sono più veloci (anche se più lunghe in km)
# - Il percorso evita automaticamente il traffico del centro città
# - Per Torino: i tecnici usano l'autostrada, non passano per Torino città
# OSRM calcola il percorso più veloce considerando tipo strada e velocità consentite.
OTTIMIZZA_PER_TEMPO = True  # True = usa tempo (preferisce autostrade), False = usa km

# --- CONFIGURAZIONE RAGGRUPPAMENTO ZONE (anti-saltelli) ---
# I contatori vengono raggruppati in "micro-zone" geografiche.
# L'algoritmo completa tutti i contatori di una zona prima di passare alla successiva.
# Questo evita percorsi che saltano avanti e indietro tra aree diverse.
# Le zone vengono poi ordinate usando OSRM (quindi preferendo autostrade tra zone).
RAGGIO_MICROZONA_KM = 0.5  # Raggio in km per raggruppare contatori vicini (default 500m)

# --- CONFIGURAZIONE CACHE ---
FILE_CACHE_DISTANZE = os.path.join('cache', f'cache_distanze_{PROVINCIA_ATTIVA.lower()}.json')
FILE_CACHE_HASH = os.path.join('cache', f'cache_hash_{PROVINCIA_ATTIVA.lower()}.json')

# --- CONFIGURAZIONE LOOP ---
INTERVALLO_SYNC_SECONDI = 180  # 3 minuti - check periodico per modifiche sul foglio



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
        if 'raggio_microzona_km' in _user_settings:
            RAGGIO_MICROZONA_KM = _user_settings['raggio_microzona_km']
        if 'ottimizza_per_tempo' in _user_settings:
            OTTIMIZZA_PER_TEMPO = _user_settings['ottimizza_per_tempo']
        if 'intervallo_sync_secondi' in _user_settings:
            INTERVALLO_SYNC_SECONDI = _user_settings['intervallo_sync_secondi']
        if 'data_inizio' in _user_settings:
            DATA_INIZIO = _user_settings['data_inizio']
        if 'data_fine' in _user_settings:
            DATA_FINE = _user_settings['data_fine']
        
        # Mappatura colonne personalizzata
        if 'mappatura_colonne' in _user_settings:
            MAPPATURA_COLONNE = _user_settings['mappatura_colonne']
        
        print(f"✅ Impostazioni personalizzate caricate da settings.json")
    except Exception as _e:
        print(f"⚠️  Errore caricamento settings.json: {_e}")