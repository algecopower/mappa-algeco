"""
Caricamento dati DIRETTAMENTE da Google Sheets (FILONE).
Nessun database locale - tutto dal FILONE in tempo reale.

Colonne utilizzate dal FILONE:
- DATA PIAN          → DATA_K (data pianificata)
- NOTE APP           → NOTE_APP_RAW, IS_APPUNTAMENTO, ORA_APPUNTAMENTO
- POD                → POD_K
- Eneltel            → Eneltel_Key, ENEL_K
- NOMINATIVO         → CLI_K (cliente)
- Via Num            → VIA_K (indirizzo)
- Comune             → COMUNE
- telefono           → TEL_K
- Posizione_Mis_Ante_Sost → NOTE_K (posizione contatore)
- Latitudine         → Lat
- Longitudine        → Lon
- Operatore          → TEC_K (tecnico)
"""
import re
import logging
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials

logger = logging.getLogger('mappa_algeco')

from config import (
    ID_FOGLIO_GOOGLE,
    GID_SCHEDA,
    CREDENZIALI_JSON,
    MATRICOLE_VIP
)

# Mappatura colonne personalizzata
try:
    from config import MAPPATURA_COLONNE, MAPPATURA_COLONNE_DEFAULT
except ImportError:
    MAPPATURA_COLONNE = {}
    MAPPATURA_COLONNE_DEFAULT = {}

# Import opzionali per retrocompatibilità
try:
    from config import DATA_INIZIO, DATA_FINE
except ImportError:
    DATA_INIZIO = None
    DATA_FINE = None



# Configurazione logica appuntamenti per provincia
try:
    from config import LOGICA_APPUNTAMENTI, COLONNA_ORA_PIAN
except ImportError:
    LOGICA_APPUNTAMENTI = 'torino'
    COLONNA_ORA_PIAN = None

try:
    from config import USA_LOCALITA_COME_COMUNE
except ImportError:
    USA_LOCALITA_COME_COMUNE = False

from .percorso import normalizza_eneltel, estrai_orario
from .ai_detector import (
    rileva_appuntamenti_batch,
    rileva_appuntamenti_batch_pavia,
    estrai_orario_pavia
)


def trova_colonna(df, nomi_possibili, default=None, chiave_mapping=None):
    """Cerca una colonna tra vari nomi possibili (case insensitive).
    Se chiave_mapping è specificata, controlla prima la mappatura personalizzata."""
    colonne_lower = {c.lower().strip(): c for c in df.columns}
    
    # Prima controlla la mappatura personalizzata
    if chiave_mapping and MAPPATURA_COLONNE.get(chiave_mapping):
        nome_custom = MAPPATURA_COLONNE[chiave_mapping]
        if nome_custom.lower().strip() in colonne_lower:
            return colonne_lower[nome_custom.lower().strip()]
        # Se il nome mappato non esiste, logga un warning e continua con i default
        logger.warning(f"   ⚠️ Colonna mappata '{nome_custom}' per '{chiave_mapping}' non trovata, uso default")
    
    for nome in nomi_possibili:
        if nome.lower().strip() in colonne_lower:
            return colonne_lower[nome.lower().strip()]
    return default


def parse_data_flessibile(valore):
    """
    Converte qualsiasi formato data in YYYY-MM-DD.
    Gestisce: dd/mm/yyyy, d/m/yyyy, yyyy-mm-dd, dd-mm-yyyy, ecc.
    """
    if pd.isna(valore) or not valore:
        return None
    
    valore = str(valore).strip()
    
    # Ignora valori che sono solo un anno (es: "2026")
    if re.match(r'^\d{4}$', valore):
        return None
    
    # Ignora valori vuoti o troppo corti
    if len(valore) < 6:
        return None
    
    # Lista di formati da provare
    formati = [
        '%d/%m/%Y',      # 26/05/2025, 26/5/2025
        '%d-%m-%Y',      # 26-05-2025
        '%Y-%m-%d',      # 2025-05-26
        '%d/%m/%y',      # 26/05/25
        '%d-%m-%y',      # 26-05-25
        '%Y/%m/%d',      # 2025/05/26
        '%d.%m.%Y',      # 26.05.2025
        '%d %m %Y',      # 26 05 2025
    ]
    
    # Rimuovi eventuale orario (es: "26/05/2025 08:00")
    valore_pulito = re.split(r'\s+', valore)[0]
    
    for fmt in formati:
        try:
            dt = pd.to_datetime(valore_pulito, format=fmt)
            return dt.strftime('%Y-%m-%d')
        except Exception:
            continue
    
    # Ultimo tentativo: lascia che pandas indovini
    try:
        dt = pd.to_datetime(valore, dayfirst=True, errors='coerce')
        if pd.notna(dt):
            return dt.strftime('%Y-%m-%d')
    except Exception:
        pass
    
    return None


def carica_dati_google_sheets():
    """Carica tutti i dati direttamente dal FILONE (Google Sheet)."""

    logger.info("=" * 60)
    logger.info("📊 CARICAMENTO DATI DAL FILONE")
    logger.info("=" * 60)

    # ================================================================
    # CONNESSIONE GOOGLE SHEETS
    # ================================================================
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENZIALI_JSON, scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(ID_FOGLIO_GOOGLE).get_worksheet_by_id(GID_SCHEDA)
    values = sheet.get_all_values()

    df = pd.DataFrame(values[1:], columns=values[0])
    df.columns = df.columns.str.strip()

    logger.info(f"✅ FILONE caricato: {len(df)} righe, {len(df.columns)} colonne")

    # ================================================================
    # MAPPING COLONNE FILONE → COLONNE INTERNE
    # ================================================================
    
    # DATA PIAN / PROG → DATA_K
    col_data = trova_colonna(df, ['DATA PIAN', 'DATA_PIAN', 'Data Pian', 'DATA PIANIFICATA', 'PROG', 'Prog', 'DATA'], chiave_mapping='data_pianificata')
    if col_data:
        # Salva valore grezzo per rilevare ANNULLATO
        df['DATA_RAW'] = df[col_data].fillna('').astype(str).str.strip()
        
        # Rileva ANNULLATO dal valore grezzo (prima del parsing)
        df['IS_ANNULLATO'] = df['DATA_RAW'].str.upper().str.contains('ANNULLAT', na=False)
        annullati_count = df['IS_ANNULLATO'].sum()
        if annullati_count > 0:
            logger.info(f"   🚫 Rilevati {annullati_count} contatori ANNULLATI")
        
        # Usa la funzione flessibile per ogni valore
        df['DATA_K'] = df[col_data].apply(parse_data_flessibile)
        df['DATA_K'] = df['DATA_K'].fillna('DATA_MANCANTE')
        
        # Conta successi
        date_ok = (df['DATA_K'] != 'DATA_MANCANTE').sum()
        date_fail = (df['DATA_K'] == 'DATA_MANCANTE').sum()
        logger.info(f"   📅 Date parsate: {date_ok}/{len(df)} ({date_fail} senza data valida)")
        
        # Mostra esempi di valori non riconosciuti (per debug)
        if date_fail > 0:
            valori_non_riconosciuti = df[df['DATA_K'] == 'DATA_MANCANTE'][col_data].value_counts().head(5)
            if len(valori_non_riconosciuti) > 0:
                esempi = [f"'{v}' ({c}x)" for v, c in valori_non_riconosciuti.items()]
                logger.warning(f"      ⚠️ Valori non riconosciuti: {', '.join(esempi)}")
    else:
        logger.warning("   ⚠️ Colonna DATA PIAN non trovata!")
        df['DATA_K'] = 'DATA_MANCANTE'
        df['DATA_RAW'] = ''
        df['IS_ANNULLATO'] = False
    
    # ================================================================
    # FILTRO DATE (da config.py)
    # ================================================================
    righe_prima_filtro = len(df)
    
    if DATA_INIZIO or DATA_FINE:
        # Converti DATA_K in datetime per confronto
        df['_data_temp'] = pd.to_datetime(df['DATA_K'], errors='coerce')
        
        if DATA_INIZIO:
            data_inizio = pd.to_datetime(DATA_INIZIO)
            df = df[(df['_data_temp'] >= data_inizio) | (df['DATA_K'] == 'DATA_MANCANTE')]
        
        if DATA_FINE:
            data_fine = pd.to_datetime(DATA_FINE)
            df = df[(df['_data_temp'] <= data_fine) | (df['DATA_K'] == 'DATA_MANCANTE')]
        
        df = df.drop(columns=['_data_temp'], errors='ignore')
        
        # Log filtro
        filtro_str = ""
        if DATA_INIZIO and DATA_FINE:
            filtro_str = f"{DATA_INIZIO} → {DATA_FINE}"
        elif DATA_INIZIO:
            filtro_str = f"da {DATA_INIZIO}"
        elif DATA_FINE:
            filtro_str = f"fino a {DATA_FINE}"
        
        logger.info(f"   📅 Filtro date ({filtro_str}): {righe_prima_filtro} → {len(df)} righe")
    
    # Eneltel → Eneltel_Key, ENEL_K
    col_eneltel = trova_colonna(df, ['Eneltel', 'ENELTEL'], chiave_mapping='eneltel')
    if col_eneltel:
        df['Eneltel_Key'] = df[col_eneltel].apply(normalizza_eneltel)
        df['ENEL_K'] = df[col_eneltel]
    else:
        logger.error("   ❌ Colonna Eneltel non trovata!")
        return None
    
    # NOMINATIVO → CLI_K
    col_nominativo = trova_colonna(df, ['NOMINATIVO', 'Nominativo', 'CLIENTE', 'Cliente', 'NOME', 'Nome'], chiave_mapping='nominativo')
    if col_nominativo:
        df['CLI_K'] = df[col_nominativo].fillna('').astype(str).str.strip()
    else:
        df['CLI_K'] = 'N/A'
    
    # Comune → COMUNE
    col_comune = trova_colonna(df, ['Comune', 'COMUNE'], chiave_mapping='comune')
    if col_comune:
        df['COMUNE'] = df[col_comune].fillna('').astype(str).str.strip()
    else:
        df['COMUNE'] = ''
    
    # Località → LOCALITA (per Pavia, più dettagliata del comune)
    col_localita = trova_colonna(df, ['LOCALITA', 'Localita', 'Località', 'località'], chiave_mapping='localita')
    if col_localita:
        df['LOCALITA'] = df[col_localita].fillna('').astype(str).str.strip()
    else:
        df['LOCALITA'] = ''
    
    # Gestione Località → Comune
    if USA_LOCALITA_COME_COMUNE:
        # Per Pavia: usa LOCALITA come COMUNE quando disponibile
        mask_localita = df['LOCALITA'] != ''
        sostituiti = mask_localita.sum()
        df.loc[mask_localita, 'COMUNE'] = df.loc[mask_localita, 'LOCALITA']
        logger.info(f"   🏘️ Località → Comune: {sostituiti} righe aggiornate (usa_localita_come_comune=True)")
    else:
        # Default: usa LOCALITA solo se COMUNE è vuoto
        mask_no_comune = (df['COMUNE'] == '') & (df['LOCALITA'] != '')
        if mask_no_comune.sum() > 0:
            df.loc[mask_no_comune, 'COMUNE'] = df.loc[mask_no_comune, 'LOCALITA']
    
    # Via / Indirizzo → VIA_K (indirizzo senza civico)
    # TORINO: colonna "Indirizzo" per via, "Num" per civico
    # PAVIA: colonna "Via" per via, "civico" per civico
    col_via = trova_colonna(df, ['Indirizzo', 'Via', 'VIA', 'Via Num', 'VIA NUM'], chiave_mapping='indirizzo')
    if col_via:
        df['VIA_K'] = df[col_via].fillna('').astype(str).str.strip()
    else:
        df['VIA_K'] = ''
    
    # Civico → CIVICO_K (numero civico separato)
    # TORINO: colonna "Num"
    # PAVIA: colonna "civico"
    col_civico = trova_colonna(df, ['Num', 'NUM', 'civico', 'CIVICO', 'Numero civico', 'N. Civico'], chiave_mapping='civico')
    if col_civico:
        df['CIVICO_K'] = df[col_civico].fillna('').astype(str).str.strip()
    else:
        df['CIVICO_K'] = ''
    
    # telefono → TEL_K
    col_telefono = trova_colonna(df, ['telefono', 'TELEFONO', 'Telefono', 'TEL', 'Tel'], chiave_mapping='telefono')
    if col_telefono:
        df['TEL_K'] = df[col_telefono].fillna('').astype(str).str.strip()
    else:
        df['TEL_K'] = ''
    
    # POD → POD_K
    col_pod = trova_colonna(df, ['POD', 'Pod', 'pod'], chiave_mapping='pod')
    if col_pod:
        df['POD_K'] = df[col_pod].fillna('').astype(str).str.strip()
    else:
        df['POD_K'] = ''
    
    # Posizione_Mis_Ante_Sost / Posizione_Ante_Mis / Posizione misuratore → NOTE_K (Posizione misuratore)
    col_posizione = trova_colonna(df, ['Posizione_Mis_Ante_Sost', 'Posizione_Ante_Mis', 'Posizione misuratore', 'POSIZIONE', 'Posizione', 'Posizione Misuratore'], chiave_mapping='posizione_misuratore')
    if col_posizione:
        df['NOTE_K'] = df[col_posizione].fillna('').astype(str).str.strip()
    else:
        df['NOTE_K'] = ''
    
    # Tipo_Misuratore / TIPOLOGIA CE → TIPO_CE_K
    col_tipo_ce = trova_colonna(df, ['Tipo_Misuratore', 'TIPOLOGIA CE', 'Tipologia CE', 'TIPO CE', 'Tipo CE', 'Tipologia'], chiave_mapping='tipo_misuratore')
    if col_tipo_ce:
        df['TIPO_CE_K'] = df[col_tipo_ce].fillna('').astype(str).str.strip()
    else:
        df['TIPO_CE_K'] = ''
    
    # SMART TEST → SMART_TEST_K (solo Torino)
    col_smart_test = trova_colonna(df, ['SMART TEST', 'Smart Test', 'smart test'], chiave_mapping='smart_test')
    if col_smart_test:
        df['SMART_TEST_K'] = df[col_smart_test].fillna('').astype(str).str.strip()
    else:
        df['SMART_TEST_K'] = ''
    
    # Operatore → TEC_K
    col_operatore = trova_colonna(df, ['Operatore', 'OPERATORE', 'TECNICO', 'Tecnico', 'Matricola'], chiave_mapping='operatore')
    if col_operatore:
        df['TEC_K'] = df[col_operatore].fillna('').astype(str).str.strip()
    else:
        df['TEC_K'] = ''

    # Dove è vuoto, metti 'TUTTI'
    df.loc[df['TEC_K'] == '', 'TEC_K'] = 'TUTTI'

    # Conta operatori assegnati
    con_operatore = (df['TEC_K'] != 'TUTTI').sum()
    if con_operatore > 0:
        operatori_unici = df[df['TEC_K'] != 'TUTTI']['TEC_K'].nunique()
        logger.info(f"   👤 Operatori assegnati: {con_operatore} righe ({operatori_unici} matricole)")
    else:
        logger.info(f"   👤 Modalità GENERICA (nessun operatore assegnato)")
    
    # Latitudine, Longitudine → Lat, Lon
    col_lat = trova_colonna(df, ['Latitudine', 'LATITUDINE', 'Lat', 'LAT'], chiave_mapping='latitudine')
    col_lon = trova_colonna(df, ['Longitudine', 'LONGITUDINE', 'Lon', 'LON'], chiave_mapping='longitudine')

    if col_lat and col_lon:
        df['Lat'] = pd.to_numeric(df[col_lat].astype(str).str.replace(',', '.'), errors='coerce')
        df['Lon'] = pd.to_numeric(df[col_lon].astype(str).str.replace(',', '.'), errors='coerce')
    else:
        df['Lat'] = pd.NA
        df['Lon'] = pd.NA

    # LCL dal FILONE (Google Sheets)
    col_lcl = trova_colonna(df, ['LCL', 'lcl', 'Lcl'], chiave_mapping='lcl')
    if col_lcl:
        df['LCL'] = df[col_lcl].fillna('').astype(str).str.strip()
        # Pulisci valori non validi
        df.loc[df['LCL'].str.upper().isin(['NAN', 'NONE', 'NULL']), 'LCL'] = ''
        lcl_presenti = (df['LCL'] != '').sum()
        logger.info(f"   🏷️  LCL dal FILONE: {lcl_presenti}/{len(df)} ({lcl_presenti/len(df)*100:.1f}%)")
    else:
        df['LCL'] = ''
        logger.warning("   ⚠️  Colonna LCL non trovata nel FILONE")

    # ================================================================
    # RIEPILOGO COORDINATE (dal FILONE)
    # ================================================================
    coord_ok = df['Lat'].notna().sum()
    coord_mancanti = df['Lat'].isna().sum()
    logger.info(f"   🌍 Coordinate: {coord_ok} OK, {coord_mancanti} mancanti")

    # ================================================================
    # APPUNTAMENTI - Rilevamento in base alla provincia
    # ================================================================

    if LOGICA_APPUNTAMENTI == 'pavia':
        # ---- LOGICA PAVIA ----
        # Usa colonna "Ora pian": è appuntamento se orario NON è "dalle x alle x"
        col_ora_pian = trova_colonna(df, [COLONNA_ORA_PIAN, 'Ora pian', 'ORA PIAN', 'ora pian'], chiave_mapping='ora_pianificata') if COLONNA_ORA_PIAN else None

        if col_ora_pian:
            df['ORA_PIAN_RAW'] = df[col_ora_pian].fillna('').astype(str).str.strip()
            logger.info(f"   📋 Logica Pavia: usando colonna '{col_ora_pian}'")
        else:
            df['ORA_PIAN_RAW'] = ''
            logger.warning(f"   ⚠️ Colonna 'Ora pian' non trovata")

        # Rileva appuntamenti con logica Pavia
        ora_pian_list = df['ORA_PIAN_RAW'].tolist()
        risultati_appuntamenti = rileva_appuntamenti_batch_pavia(ora_pian_list)
        df['IS_APPUNTAMENTO'] = risultati_appuntamenti

        # Estrai orario con logica Pavia
        df['ORA_APPUNTAMENTO'] = df['ORA_PIAN_RAW'].apply(estrai_orario_pavia)
        df['ORA_PIAN'] = df['ORA_PIAN_RAW']  # Campo separato per Ora pianificazione
        df['DETTAGLIO_APP'] = df['ORA_PIAN_RAW']
        
        # Pavia: estrai orari lettere dal campo "Ora pian" quando è formato "dalle x alle x"
        pattern_dalle_alle = re.compile(
            r'dalle?\s*\d{1,2}(?:[:.]\d{2})?\s*alle?\s*\d{1,2}(?:[:.]\d{2})?',
            re.IGNORECASE
        )
        def estrai_orari_lettere_pavia(ora_pian):
            if not ora_pian or str(ora_pian).strip() == '':
                return ''
            testo = str(ora_pian).strip()
            if pattern_dalle_alle.search(testo):
                return testo
            return ''
        
        df['ORARI_LETTERE'] = df['ORA_PIAN_RAW'].apply(estrai_orari_lettere_pavia)
        orari_lettere_count = (df['ORARI_LETTERE'] != '').sum()
        if orari_lettere_count > 0:
            logger.info(f"   📬 Orari lettere (Pavia): {orari_lettere_count} contatori con fascia oraria")
        
        # Per Pavia, cerca anche una colonna Note separata
        col_note_pavia = trova_colonna(df, ['NOTE', 'Note', 'note', 'Annotazioni', 'ANNOTAZIONI'], chiave_mapping='note_appuntamento')
        if col_note_pavia:
            df['NOTE_APP_RAW'] = df[col_note_pavia].fillna('').astype(str).str.strip()
        else:
            df['NOTE_APP_RAW'] = ''  # Nessuna colonna note separata

    else:
        # ---- LOGICA TORINO (default) ----
        # Usa colonna "NOTE APP" con parola chiave "APP"
        col_note_app = trova_colonna(df, ['NOTE APP', 'NOTE_APP', 'Note App', 'NOTE APPUNTAMENTO', 'NOTE', 'Note'], chiave_mapping='note_appuntamento')

        if col_note_app:
            df['NOTE_APP_RAW'] = df[col_note_app].fillna('').astype(str).str.strip()
        else:
            df['NOTE_APP_RAW'] = ''

        # Rileva appuntamenti con regex
        note_list = df['NOTE_APP_RAW'].tolist()
        risultati_appuntamenti = rileva_appuntamenti_batch(note_list)
        df['IS_APPUNTAMENTO'] = risultati_appuntamenti

        # Estrai orario appuntamento
        def trova_orario(row):
            if not row['IS_APPUNTAMENTO']:
                return None
            return estrai_orario(row['NOTE_APP_RAW'])

        df['ORA_APPUNTAMENTO'] = df.apply(trova_orario, axis=1)
        df['DETTAGLIO_APP'] = df['NOTE_APP_RAW']
        
        # Torino: leggi colonna "Orari" per le fasce orarie delle lettere
        # Solo valori in formato "dalle x alle x" vengono considerati
        col_orari = trova_colonna(df, ['Orari', 'ORARI', 'orari', 'Orari lettere', 'ORARI LETTERE'], chiave_mapping='orari_lettere')
        if col_orari:
            orari_raw = df[col_orari].fillna('').astype(str).str.strip()
            # Pulisci valori non validi
            orari_raw[orari_raw.str.upper().isin(['NAN', 'NONE', 'NULL'])] = ''
            # Tieni solo valori nel formato "dalle x alle x"
            pattern_dalle_alle_to = re.compile(
                r'dalle?\s*\d{1,2}(?:[:.]\d{2})?\s*alle?\s*\d{1,2}(?:[:.]\d{2})?',
                re.IGNORECASE
            )
            df['ORARI_LETTERE'] = orari_raw.apply(
                lambda v: v if v and pattern_dalle_alle_to.search(v) else ''
            )
            orari_lettere_count = (df['ORARI_LETTERE'] != '').sum()
            if orari_lettere_count > 0:
                logger.info(f"   📬 Orari lettere (Torino): {orari_lettere_count} contatori con fascia oraria")
        else:
            df['ORARI_LETTERE'] = ''
            logger.warning("   ⚠️ Colonna 'Orari' non trovata nel FILONE")

    # ================================================================
    # FILTRI
    # ================================================================
    
    # Contatori senza data valida (#N/A, vuote, solo anno, ecc.)
    # NON vengono esclusi: servono per il conteggio "schedulabili" nel frontend
    contatori_senza_data = (df['DATA_K'] == 'DATA_MANCANTE').sum()
    contatori_annullati = df['IS_ANNULLATO'].sum()
    if contatori_senza_data > 0:
        schedulabili_senza_data = contatori_senza_data - contatori_annullati
        logger.info(f"   📋 {contatori_senza_data} contatori senza data pianificata (di cui {schedulabili_senza_data} schedulabili, {contatori_annullati} annullati)")
    
    # Escludi contatori già installati (ESITO OK)
    col_esito = trova_colonna(df, ['Esito', 'ESITO', 'esito'], chiave_mapping='esito')
    if col_esito:
        righe_prima = len(df)
        # Escludi righe con "ESITO OK" (case insensitive)
        df = df[~df[col_esito].astype(str).str.upper().str.contains('ESITO OK', na=False)]
        esclusi_esito = righe_prima - len(df)
        if esclusi_esito > 0:
            logger.info(f"   ✅ Esclusi {esclusi_esito} contatori già installati (ESITO OK)")
    
    # Rileva se siamo in modalità completamente generica (tutti TUTTI)
    tecnici_unici = df['TEC_K'].unique().tolist()
    modalita_generica = tecnici_unici == ['TUTTI']
    
    # Filtra per tecnici VIP + mantieni 'TUTTI' (senza operatore)
    if not modalita_generica and MATRICOLE_VIP:
        righe_prima = len(df)
        # Mantieni: matricole VIP + righe senza operatore ('TUTTI')
        df = df[df['TEC_K'].isin(MATRICOLE_VIP) | (df['TEC_K'] == 'TUTTI')]
        righe_dopo = len(df)
        if righe_prima != righe_dopo:
            logger.info(f"   👤 Filtro tecnici VIP: {righe_prima} → {righe_dopo} righe")
    
    # Rimuovi righe senza coordinate
    righe_prima = len(df)
    df = df.dropna(subset=['Lat', 'Lon'])
    righe_senza_coord = righe_prima - len(df)
    
    if righe_senza_coord > 0:
        logger.warning(f"   ⚠️ {righe_senza_coord} righe escluse (senza coordinate)")

    # ================================================================
    # RIEPILOGO
    # ================================================================
    logger.info("")
    logger.info("📊 RIEPILOGO:")
    
    # Filtro date attivo
    if DATA_INIZIO or DATA_FINE:
        filtro_str = ""
        if DATA_INIZIO and DATA_FINE:
            filtro_str = f"{DATA_INIZIO} → {DATA_FINE}"
        elif DATA_INIZIO:
            filtro_str = f"da {DATA_INIZIO} in poi"
        elif DATA_FINE:
            filtro_str = f"fino a {DATA_FINE}"
        logger.info(f"   🔍 Filtro date: {filtro_str}")
    
    # Date
    date_uniche = sorted(df['DATA_K'].unique())
    date_valide = [d for d in date_uniche if d != 'DATA_MANCANTE']
    if date_valide:
        logger.info(f"   📅 Date: {len(date_valide)} ({min(date_valide)} → {max(date_valide)})")
    else:
        logger.info(f"   📅 Date: {len(date_valide)}")
    
    # Tecnici
    tecnici_finali = df['TEC_K'].unique().tolist()
    tutti_generici = tecnici_finali == ['TUTTI']
    
    if tutti_generici:
        logger.info(f"   👤 Modalità: GENERICA (tutti i punti insieme)")
    else:
        tecnici_reali = [t for t in tecnici_finali if t != 'TUTTI']
        senza_operatore = (df['TEC_K'] == 'TUTTI').sum()
        if senza_operatore > 0:
            logger.info(f"   👤 Tecnici: {len(tecnici_reali)} ({', '.join(tecnici_reali)}) + {senza_operatore} senza operatore")
        else:
            logger.info(f"   👤 Tecnici: {len(tecnici_reali)} ({', '.join(tecnici_reali)})")
    
    # Comuni
    comuni_ok = df[df['COMUNE'] != '']['COMUNE'].nunique()
    logger.info(f"   🏘️ Comuni: {comuni_ok}")
    
    # Appuntamenti
    num_app = df['IS_APPUNTAMENTO'].sum()
    if num_app > 0:
        app_con_ora = df[df['ORA_APPUNTAMENTO'].notna()]['IS_APPUNTAMENTO'].sum()
        logger.info(f"   📞 Appuntamenti: {num_app} ({app_con_ora} con orario)")
    
    # Totale
    if modalita_generica:
        logger.info(f"   📍 TOTALE PUNTI: {len(df)} (mappa unica)")
    else:
        logger.info(f"   📍 TOTALE PUNTI: {len(df)}")
    logger.info("=" * 60)
    
    return df