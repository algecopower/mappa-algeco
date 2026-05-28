# -*- coding: utf-8 -*-
"""
Caricamento dati dal DB locale (FILONE).

Post-disconnessione Google Sheets (21/05/2026), il DB SQLite per provincia è
la fonte di verità. Questo modulo ricostruisce il formato tabellare storico del
FILONE e lo trasforma nel DataFrame usato da mappa, percorsi e dashboard.

Colonne utilizzate dal FILONE:
- DATA PIAN          → DATA_K (data pianificata)
- NOTE APP           → NOTE_APP_RAW, IS_APPUNTAMENTO, ORA_APPUNTAMENTO
- POD                → POD_K
- Eneltel            → Eneltel_Key, ENEL_K
- NOMINATIVO         → CLI_K (cliente)
- Via Num            → VIA_K (indirizzo)
- Comune             → COMUNE
- telefono           → TEL_K
- email              → EMAIL_K
- Posizione_Mis_Ante_Sost → NOTE_K (posizione contatore)
- Latitudine         → Lat
- Longitudine        → Lon
- Operatore          → TEC_K (tecnico)
"""
import re
import hashlib
import logging
import time as _time_module
import threading
import pandas as pd

logger = logging.getLogger('mappa_algeco')

# ============================================================================
# CACHE IN MEMORIA — evita riletture DB e re-processing inutili
# ============================================================================
_dl_cache_lock = threading.Lock()
_dl_cached_df = None          # DataFrame processato
_dl_cached_raw_hash = None    # Hash MD5 dei dati grezzi
_dl_cache_timestamp = 0       # Timestamp ultima lettura DB
_dl_data_changed = True       # Flag: ultima lettura DB ha trovato dati diversi?
_dl_structural_change = True  # Flag: cambiamento strutturale (richiede ricalcolo percorsi)?
_DL_CACHE_TTL = 30            # Secondi: non rileggere entro questo intervallo

# Colonne nel DataFrame processato che influenzano percorsi/mappa.
# Se cambiano → ricalcola distanze, zone, percorsi.
# Se NON cambiano → basta aggiornare i dati contatori nel JSON.
_COLONNE_STRUTTURALI = [
    'Eneltel_Key',        # identità riga (aggiunta/rimozione contatori)
    'DATA_K',             # data pianificata (raggruppa percorsi per giorno)
    'TEC_K',              # operatore (raggruppa percorsi per tecnico)
    'Lat', 'Lon',         # coordinate (influenza calcolo distanze)
    'IS_APPUNTAMENTO',    # appuntamento (influenza ordine tappe)
    'ORA_APPUNTAMENTO',   # orario appuntamento (influenza ordine tappe)
    'IS_ANNULLATO',       # annullamento (esclude dal percorso)
    'COMUNE',             # comune (influenza analisi zone)
]

from config import MATRICOLE_VIP

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



from config import USA_LOCALITA_COME_COMUNE

from .percorso import (
    normalizza_eneltel,
    estrai_orario,
    rileva_appuntamento,
    motivo_non_appuntamento,
)


def _hash_raw_values(values):
    """Calcola hash MD5 dei dati grezzi del foglio per rilevare modifiche."""
    data_str = str(values)
    return hashlib.md5(data_str.encode()).hexdigest()


def _apply_pending_to_values(values):
    """Sovrascrive i valori grezzi del foglio con le pending del DB locale.

    Solo in modalità 'primary' (Fase 2). Il drainer pusherà su Sheets entro
    pochi secondi, ma intanto la UI deve vedere lo stato corretto.

    Restituisce una nuova lista (non modifica `values` in-place).
    """
    try:
        from . import db as _db
    except ImportError:
        return values
    if not values or len(values) < 2:
        return values

    headers = values[0]
    # Trova colonna eneltel + lcl + indice colonne case-insensitive
    internal_lookup = _db._build_internal_lookup()
    eneltel_col_idx = None
    lcl_col_idx = None
    col_name_to_idx = {}
    for i, name in enumerate(headers):
        if not name:
            continue
        col_name_to_idx[name.strip().lower()] = i
        chiave = internal_lookup.get(name.strip().lower())
        if chiave == 'eneltel':
            eneltel_col_idx = i
        elif chiave == 'lcl':
            lcl_col_idx = i
    if eneltel_col_idx is None:
        return values

    # Carica tutte le pending in volo, indicizzate per (eneltel_key, lcl)
    conn = _db.get_conn()
    rows = conn.execute(
        "SELECT eneltel_key, COALESCE(lcl,'') AS lcl, column_name, new_value "
        "FROM pending_writes WHERE state IN ('pending','in_flight')"
    ).fetchall()
    if not rows:
        return values

    pending_by_key = {}
    for r in rows:
        key = (r['eneltel_key'], r['lcl'])
        pending_by_key.setdefault(key, {})[r['column_name']] = r['new_value']

    new_values = [list(headers)]
    n_overrides = 0
    for body_row in values[1:]:
        body_row = list(body_row)
        if eneltel_col_idx >= len(body_row):
            new_values.append(body_row)
            continue
        ek = _db.normalize_eneltel(body_row[eneltel_col_idx])
        # LCL della riga sul foglio (può essere '')
        lcl_val = ''
        if lcl_col_idx is not None and lcl_col_idx < len(body_row):
            lcl_val = str(body_row[lcl_col_idx] or '').strip()

        # Match esatto (eneltel, lcl); fallback su (eneltel, '') se non c'è LCL pending
        overrides = pending_by_key.get((ek, lcl_val))
        if not overrides:
            overrides = pending_by_key.get((ek, ''))
        if not overrides:
            new_values.append(body_row)
            continue
        while len(body_row) < len(headers):
            body_row.append('')
        for col_name, new_val in overrides.items():
            idx = col_name_to_idx.get(col_name.strip().lower())
            if idx is None:
                continue
            body_row[idx] = '' if new_val is None else str(new_val)
            n_overrides += 1
        new_values.append(body_row)

    if n_overrides:
        logger.info(f"[primary-merge] applicati {n_overrides} valori da pending_writes al DataFrame")
    return new_values


def dati_sono_cambiati():
    """Ritorna True se l'ultimo caricamento ha trovato dati modificati."""
    return _dl_data_changed


def cambiamento_e_strutturale():
    """Ritorna True se il cambiamento rilevato riguarda colonne strutturali
    (date, operatore, coordinate, appuntamenti) che richiedono ricalcolo percorsi.

    Se False, basta aggiornare i dati contatori senza ricalcolare distanze/zone.
    """
    return _dl_structural_change


def _rileva_cambiamento_strutturale(df_old, df_new):
    """Confronta due DataFrame sulle colonne strutturali.

    Ritorna True se almeno una colonna strutturale è cambiata
    (nuovo contatore, data spostata, operatore cambiato, coordinate mosse, ecc.).
    """
    if df_old is None or df_new is None:
        logger.info(f"   🔧 Strutturale: df_old={df_old is not None} df_new={df_new is not None} (primo sync dopo restart o errore)")
        return True

    # Numero righe diverso = contatori aggiunti o rimossi
    if len(df_old) != len(df_new):
        logger.info(f"   🔧 Strutturale: numero righe cambiato {len(df_old)} → {len(df_new)}")
        return True

    cols = [c for c in _COLONNE_STRUTTURALI if c in df_old.columns and c in df_new.columns]
    if not cols:
        old_missing = [c for c in _COLONNE_STRUTTURALI if c not in df_old.columns]
        new_missing = [c for c in _COLONNE_STRUTTURALI if c not in df_new.columns]
        logger.info(f"   🔧 Strutturale: nessuna colonna strutturale comune. df_old manca {old_missing}, df_new manca {new_missing}")
        return True

    try:
        # Ordina per Eneltel_Key per confronto deterministico
        key = 'Eneltel_Key'
        if key not in cols:
            logger.info(f"   🔧 Strutturale: 'Eneltel_Key' non presente in entrambi i df (cols={cols})")
            return True

        old_sorted = df_old[cols].sort_values(key).reset_index(drop=True)
        new_sorted = df_new[cols].sort_values(key).reset_index(drop=True)

        # Confronto colonna per colonna per log dettagliato
        for col in cols:
            if not old_sorted[col].equals(new_sorted[col]):
                diff_count = (old_sorted[col] != new_sorted[col]).sum()
                # Log esempi di righe diverse per capire il pattern
                try:
                    diffs = old_sorted[col] != new_sorted[col]
                    sample_idx = diffs[diffs].index[:3]
                    sample = [
                        f"key={old_sorted.loc[i, key]} old={old_sorted.loc[i, col]!r} new={new_sorted.loc[i, col]!r}"
                        for i in sample_idx
                    ]
                except Exception:
                    sample = []
                logger.info(f"   🔧 Cambiamento strutturale: colonna '{col}' ({diff_count} righe diverse). Esempi: {sample}")
                return True

        return False
    except Exception as e:
        logger.warning(f"   ⚠️ Errore confronto strutturale: {e}, assumo strutturale")
        return True


def invalidate_data_cache():
    """Invalida la cache del DataFrame (ad es. dopo scritture manuali)."""
    global _dl_cached_df, _dl_cached_raw_hash, _dl_cache_timestamp
    with _dl_cache_lock:
        _dl_cached_df = None
        _dl_cached_raw_hash = None
        _dl_cache_timestamp = 0


def mark_data_changed():
    """Forza il prossimo `carica_dati_db_locale` a riprocessare il DataFrame.

    A differenza di `invalidate_data_cache()`, NON azzera `_dl_cached_df`:
    serve a quel confronto che produce 'COSMETICO' vs 'STRUTTURALE'. Azzera
    solo l'hash dei raw values, così il check `if raw_hash == _dl_cached_raw_hash`
    fallisce e si va al processing → mappa_data.json viene rigenerato.

    Usato in primary mode dopo ogni POST utente: anche se il drainer drena
    velocemente e Sheets/raw_values "sembrano invariati" rispetto al
    precedente snapshot (perché entrambi includono già il valore modificato),
    vogliamo comunque rigenerare mappa_data.json.
    """
    global _dl_cached_raw_hash
    with _dl_cache_lock:
        _dl_cached_raw_hash = None


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


def carica_dati_db_locale(force=False):
    """Carica tutti i dati dal DB locale nel formato storico del FILONE.

    Con sistema di cache in memoria:
    - Se force=False e la cache è fresca (< _DL_CACHE_TTL secondi), ritorna
      il DataFrame in cache senza rileggere SQLite.
    - Se i dati grezzi sono identici all'ultima volta (hash MD5),
      salta tutto il processing e ritorna il DataFrame in cache.
    - Il flag dati_sono_cambiati() indica se i dati sono effettivamente diversi.

    Args:
        force: Se True, rilegge sempre il DB (usato dal sync event-driven).
    """
    global _dl_cached_df, _dl_cached_raw_hash, _dl_cache_timestamp, _dl_data_changed, _dl_structural_change

    now = _time_module.time()

    # Cache TTL: se non forzato e cache fresca, ritorna dal cache
    if not force:
        with _dl_cache_lock:
            if _dl_cached_df is not None and (now - _dl_cache_timestamp) < _DL_CACHE_TTL:
                logger.info("📊 Dati FILONE serviti da cache in memoria (nessuna rilettura DB)")
                _dl_data_changed = False
                _dl_structural_change = False
                return _dl_cached_df.copy()

    logger.info("=" * 60)
    logger.info("📊 CARICAMENTO DATI DAL DB LOCALE")
    logger.info("=" * 60)

    # ================================================================
    # CARICAMENTO DATI DAL DB LOCALE
    # ================================================================
    # Post-disconnessione Sheets (2026-05-21): la fonte di verità è il DB
    # locale. Ricostruiamo qui il formato "values" (lista di liste, con
    # header in riga 0) che il resto del processing si aspetta.
    from . import db as _db
    headers_db, rows_db = _db.dump_filone_as_rows(
        include_orphans=False,
        apply_pending_overlay=True,
    )
    if not headers_db:
        logger.error("📊 DB filone vuoto: nessun header registrato")
        return None
    values = [list(headers_db)]
    for r in rows_db:
        values.append([r.get(h, '') for h in headers_db])

    # ================================================================
    # HASH-BASED CHANGE DETECTION
    # ================================================================
    raw_hash = _hash_raw_values(values)

    with _dl_cache_lock:
        if raw_hash == _dl_cached_raw_hash and _dl_cached_df is not None:
            _dl_cache_timestamp = _time_module.time()
            _dl_data_changed = False
            _dl_structural_change = False
            logger.info("📊 Dati DB invariati (hash identico), skip processing")
            logger.info("=" * 60)
            return _dl_cached_df.copy()

    df = pd.DataFrame(values[1:], columns=values[0])
    df.columns = df.columns.str.strip()

    logger.info(f"✅ DB locale caricato: {len(df)} righe, {len(df.columns)} colonne")

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
    
    # email → EMAIL_K
    col_email = trova_colonna(df, ['email', 'EMAIL', 'Email', 'E-mail', 'e-mail', 'E-MAIL', 'Mail', 'MAIL'], chiave_mapping='email')
    if col_email:
        df['EMAIL_K'] = df[col_email].fillna('').astype(str).str.strip()
    else:
        df['EMAIL_K'] = ''
    
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
        logger.info("   👤 Modalità GENERICA (nessun operatore assegnato)")
    
    # Latitudine, Longitudine → Lat, Lon
    col_lat = trova_colonna(df, ['Latitudine', 'LATITUDINE', 'Lat', 'LAT'], chiave_mapping='latitudine')
    col_lon = trova_colonna(df, ['Longitudine', 'LONGITUDINE', 'Lon', 'LON'], chiave_mapping='longitudine')

    if col_lat and col_lon:
        df['Lat'] = pd.to_numeric(df[col_lat].astype(str).str.replace(',', '.'), errors='coerce')
        df['Lon'] = pd.to_numeric(df[col_lon].astype(str).str.replace(',', '.'), errors='coerce')
    else:
        df['Lat'] = pd.NA
        df['Lon'] = pd.NA

    # LCL dal FILONE ricostruito dal DB locale
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
    # APPUNTAMENTI - Logica unificata (colonne standardizzate)
    # ================================================================
    # Usa colonna "NOTE APP" con parola chiave "APP" per rilevare appuntamenti
    col_note_app = trova_colonna(df, ['NOTE APP', 'NOTE_APP', 'Note App', 'NOTE APPUNTAMENTO', 'NOTE', 'Note'], chiave_mapping='note_appuntamento')

    if col_note_app:
        df['NOTE_APP_RAW'] = df[col_note_app].fillna('').astype(str).str.strip()
    else:
        df['NOTE_APP_RAW'] = ''

    # Rileva appuntamenti CONFERMATI: la parola "APP" deve essere presente
    # come token, MA non deve essere preceduta/seguita da pattern di
    # negazione/cancellazione/incertezza ("no app", "app annullata",
    # "app??", "app da fissare", "richiesta app", "forse app", ecc.).
    # Vedi `rileva_appuntamento` in moduli/percorso.py per i dettagli.
    df['IS_APPUNTAMENTO'] = df['NOTE_APP_RAW'].apply(rileva_appuntamento)

    # Audit / log dei casi esclusi: NOTE che contengono "app" ma sono state
    # filtrate (utile in fase di tuning per capire i falsi negativi).
    note_con_app = df['NOTE_APP_RAW'].astype(str).str.contains(r'\bapp\b', case=False, regex=True, na=False)
    note_escluse = df.loc[note_con_app & ~df['IS_APPUNTAMENTO'], 'NOTE_APP_RAW']
    if len(note_escluse) > 0:
        # Conta per motivo
        motivi = {}
        for nota in note_escluse:
            motivo = motivo_non_appuntamento(nota) or 'altro'
            motivi[motivo] = motivi.get(motivo, 0) + 1
        riepilogo = ', '.join(f"{k}: {v}" for k, v in sorted(motivi.items(), key=lambda x: -x[1]))
        logger.info(
            f"   🔍 Falsi positivi 'APP' filtrati: {len(note_escluse)} note ({riepilogo})"
        )
        # Log dettagliato (DEBUG) dei primi 10 casi per audit
        for nota in note_escluse.head(10):
            motivo = motivo_non_appuntamento(nota) or 'altro'
            logger.debug(f"      [{motivo}] {nota[:80]}")

    # Estrai orario appuntamento
    def trova_orario(row):
        if not row['IS_APPUNTAMENTO']:
            return None
        return estrai_orario(row['NOTE_APP_RAW'])

    df['ORA_APPUNTAMENTO'] = df.apply(trova_orario, axis=1)
    df['DETTAGLIO_APP'] = df['NOTE_APP_RAW']
    
    # Leggi colonna "Orari" per le fasce orarie delle lettere
    # Solo valori in formato "dalle x alle x" vengono considerati
    col_orari = trova_colonna(df, ['Orari', 'ORARI', 'orari', 'Orari lettere', 'ORARI LETTERE'], chiave_mapping='orari_lettere')
    if col_orari:
        orari_raw = df[col_orari].fillna('').astype(str).str.strip()
        # Pulisci valori non validi
        orari_raw[orari_raw.str.upper().isin(['NAN', 'NONE', 'NULL'])] = ''
        # Tieni solo valori nel formato "dalle x alle x"
        pattern_dalle_alle = re.compile(
            r'dalle?\s*\d{1,2}(?:[:.]\d{2})?\s*alle?\s*\d{1,2}(?:[:.]\d{2})?',
            re.IGNORECASE
        )
        df['ORARI_LETTERE'] = orari_raw.apply(
            lambda v: v if v and pattern_dalle_alle.search(v) else ''
        )
        orari_lettere_count = (df['ORARI_LETTERE'] != '').sum()
        if orari_lettere_count > 0:
            logger.info(f"   📬 Orari lettere: {orari_lettere_count} contatori con fascia oraria")
    else:
        df['ORARI_LETTERE'] = ''
        logger.warning("   ⚠️ Colonna 'Orari' non trovata nel FILONE")

    # Fallback ORA_APPUNTAMENTO: per le righe con IS_APPUNTAMENTO=True che non
    # hanno un orario nelle NOTE APP, usa la fascia oraria della colonna "Orari"
    # (la stessa usata per le lettere). Evita appuntamenti senza orario, che
    # finirebbero ordinati in modo incoerente e mostrati come "nan" in UI.
    mask_app_senza_ora = df['IS_APPUNTAMENTO'] & df['ORA_APPUNTAMENTO'].isna()
    if mask_app_senza_ora.any():
        df.loc[mask_app_senza_ora, 'ORA_APPUNTAMENTO'] = (
            df.loc[mask_app_senza_ora, 'ORARI_LETTERE']
              .apply(lambda s: estrai_orario(s) if s else None)
        )
        recuperati = mask_app_senza_ora.sum() - (
            df.loc[mask_app_senza_ora, 'ORA_APPUNTAMENTO'].isna().sum()
        )
        if recuperati > 0:
            logger.info(
                f"   🕒 Appuntamenti senza orario nelle NOTE: "
                f"{recuperati} fasce recuperate dalla colonna 'Orari'"
            )

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
    # ACCESS POINTS (UPS-style snap-by-name OSRM)
    # ================================================================
    # Per ogni contatore con civico valido, calcoliamo l'access point
    # sulla strada CORRETTA (matching del nome via tramite OSRM /nearest).
    # Le coords originali Lat/Lon restano per il marker visivo;
    # Lat_access/Lon_access vengono usate solo per il routing.
    try:
        from .access_points import arricchisci_dataframe as _arricchisci_df
        logger.info("   📍 Calcolo access points (UPS-style)...")
        _arricchisci_df(df, max_workers=8, persisti_cache=True)
    except Exception as e:
        logger.warning(f"   ⚠️ Errore access points: {e} (uso Lat/Lon raw)")
        # Fallback: usa Lat/Lon originali come access point
        df['Lat_access'] = df['Lat']
        df['Lon_access'] = df['Lon']
        df['access_source'] = 'fallback'
        df['access_road'] = None

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
        logger.info("   👤 Modalità: GENERICA (tutti i punti insieme)")
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

    # ================================================================
    # OTTIMIZZAZIONE DTYPES: converte colonne string ripetitive a category.
    # Riduce sensibilmente la memoria del DataFrame e dei downstream
    # (es. groupby, copy, mappa_data.json). Sicuro: la API esposta
    # (str/iloc/loc) si comporta allo stesso modo.
    # ================================================================
    for col in df.select_dtypes(include=['object']).columns:
        try:
            nunique = df[col].nunique(dropna=False)
            # Convertiamo a category solo se ratio < 50% (alta ripetitività)
            if nunique > 0 and nunique / len(df) < 0.5:
                df[col] = df[col].astype('category')
        except Exception:
            pass  # Tipo misto/non hashable, lascia object

    # ================================================================
    # SALVA IN CACHE + RILEVA TIPO CAMBIAMENTO
    # ================================================================
    with _dl_cache_lock:
        _dl_structural_change = _rileva_cambiamento_strutturale(_dl_cached_df, df)
        _dl_cached_df = df.copy()
        _dl_cached_raw_hash = raw_hash
        _dl_cache_timestamp = _time_module.time()
        _dl_data_changed = True

    if _dl_structural_change:
        logger.info("   🔧 Tipo cambiamento: STRUTTURALE (ricalcolo percorsi necessario)")
    else:
        logger.info("   📝 Tipo cambiamento: COSMETICO (solo aggiornamento dati)")

    return df


def carica_dati_google_sheets(force=False):
    """Alias legacy: oggi i dati vengono caricati dal DB locale.

    Il nome resta per compatibilità con route e callback storici; il percorso
    effettivo non contatta Google Sheets.
    """
    return carica_dati_db_locale(force=force)
