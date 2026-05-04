# -*- coding: utf-8 -*-
"""
DB locale SQLite — fonte di verità lavorativa del programma.

Questo modulo contiene SOLO il layer di persistenza (schema, connessione,
helpers transazionali). La logica di sincronizzazione con Google Sheets vive
in moduli/sync_engine.py. Le route non chiamano direttamente sqlite3: passano
da qui (`record_local_change`, `dump_to_mappa_data_json`, ecc.).

Convenzioni:
- Una connessione SQLite per thread (thread-local), aperta lazy.
- WAL mode per concorrenza lettori/scrittore.
- PRIMARY KEY su `filone.eneltel_key` (normalizzato `lstrip('0')`).
- Colonne note (~30) in tabella tipizzata; colonne extra/sconosciute in
  `filone_extra` (key-value), così rinominare/aggiungere colonne sul foglio
  non rompe l'import.

Schema versionato in `sync_state.schema_version`; tutte le migration sono
idempotenti.
"""
import os
import time
import json
import hashlib
import logging
import sqlite3
import threading

logger = logging.getLogger('mappa_algeco')

# ============================================================================
# COLONNE NOTE (chiave_interna -> nome colonna SQL)
# ============================================================================
# Le chiavi corrispondono 1:1 a MAPPATURA_COLONNE_DEFAULT in config.py
# (escluso 'eneltel' che è la PRIMARY KEY).
COLONNE_NOTE = [
    'pod', 'nominativo', 'comune', 'localita',
    'indirizzo', 'civico', 'telefono', 'email',
    'posizione_misuratore', 'tipo_misuratore', 'operatore',
    'latitudine', 'longitudine',
    'lcl', 'note_appuntamento', 'orari_lettere',
    'esito', 'smart_test', 'sigillo',
    'data_pianificata', 'data_installazione',
    'codice_ante', 'matricola_ante',
    'matricola_installata', 'codice_installato',
]

# Colonne TEXT vs REAL (latitudine/longitudine sono numeriche)
_COLONNE_REAL = {'latitudine', 'longitudine'}

SCHEMA_VERSION_CORRENTE = 1


# ============================================================================
# CONNESSIONE (thread-local, WAL)
# ============================================================================
_thread_local = threading.local()
_db_path_lock = threading.Lock()
_db_initialized_paths = set()  # path già inizializzati (schema applicato)


def _get_db_path():
    """Path del DB per la provincia attiva."""
    try:
        from config import DB_PATH
        return DB_PATH
    except ImportError:
        # Fallback se config.py non ha ancora la chiave (shadow migration)
        from config import PROVINCIA_ATTIVA
        return os.path.join('data', PROVINCIA_ATTIVA.lower(), 'state.db')


def get_conn():
    """Ritorna la connessione SQLite per il thread corrente.

    La prima chiamata su un dato thread crea la connessione e applica le
    migrations. Connessioni successive sullo stesso thread vengono riusate.
    """
    conn = getattr(_thread_local, 'conn', None)
    if conn is not None:
        return conn

    db_path = _get_db_path()
    os.makedirs(os.path.dirname(db_path) or '.', exist_ok=True)

    conn = sqlite3.connect(
        db_path,
        timeout=30.0,
        isolation_level=None,  # autocommit; uso `with conn:` per le TX esplicite
        check_same_thread=False,  # sicurezza extra; ogni thread ha cmq la sua
    )
    conn.row_factory = sqlite3.Row

    # PRAGMA — eseguiti una volta per connessione
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA busy_timeout=30000')

    _thread_local.conn = conn

    # Schema bootstrap UNA volta per processo per ogni path
    with _db_path_lock:
        if db_path not in _db_initialized_paths:
            _bootstrap_schema(conn)
            _db_initialized_paths.add(db_path)

    return conn


def close_conn():
    """Chiude la connessione del thread corrente (utile a fine richiesta)."""
    conn = getattr(_thread_local, 'conn', None)
    if conn is not None:
        try:
            conn.close()
        finally:
            _thread_local.conn = None


# ============================================================================
# SCHEMA BOOTSTRAP & MIGRATIONS
# ============================================================================
def _bootstrap_schema(conn):
    """Crea le tabelle se non esistono e applica le migrations pending.

    Idempotente: rieseguibile senza data loss.
    """
    # CREATE TABLE IF NOT EXISTS — schema base v1
    cols_sql = []
    for c in COLONNE_NOTE:
        tipo = 'REAL' if c in _COLONNE_REAL else 'TEXT'
        cols_sql.append(f'{c} {tipo}')

    conn.executescript(f'''
        CREATE TABLE IF NOT EXISTS sync_state (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS filone (
            eneltel_key TEXT PRIMARY KEY,
            sheets_row INTEGER NOT NULL,
            {', '.join(cols_sql)},
            row_hash TEXT NOT NULL,
            last_remote_pull_ts INTEGER NOT NULL,
            last_local_change_ts INTEGER,
            is_orphan INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_filone_pod ON filone(pod);
        CREATE INDEX IF NOT EXISTS idx_filone_sheets_row ON filone(sheets_row);
        CREATE INDEX IF NOT EXISTS idx_filone_data_pian ON filone(data_pianificata);

        CREATE TABLE IF NOT EXISTS filone_extra (
            eneltel_key TEXT NOT NULL,
            column_name TEXT NOT NULL,
            value TEXT,
            PRIMARY KEY (eneltel_key, column_name),
            FOREIGN KEY (eneltel_key) REFERENCES filone(eneltel_key) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS pending_writes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            eneltel_key TEXT NOT NULL,
            column_name TEXT NOT NULL,
            new_value TEXT,
            base_value TEXT,
            created_ts INTEGER NOT NULL,
            user TEXT,
            origin TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_ts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            state TEXT NOT NULL DEFAULT 'pending'
        );
        CREATE INDEX IF NOT EXISTS idx_pw_drain ON pending_writes(state, next_attempt_ts);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_pw_coalesce
            ON pending_writes(eneltel_key, column_name)
            WHERE state IN ('pending','in_flight');

        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER NOT NULL,
            eneltel_key TEXT,
            column_name TEXT,
            old_value TEXT,
            new_value TEXT,
            source TEXT NOT NULL,
            user TEXT,
            detail TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_audit_eneltel ON audit_log(eneltel_key, ts);

        CREATE TABLE IF NOT EXISTS column_registry (
            column_name TEXT PRIMARY KEY,
            chiave_interna TEXT,
            last_index INTEGER,
            last_seen_ts INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS filone_anomalies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER NOT NULL,
            kind TEXT NOT NULL,
            eneltel_key TEXT,
            sheets_row INTEGER,
            detail TEXT
        );
    ''')

    # Versione schema
    cur = conn.execute("SELECT value FROM sync_state WHERE key='schema_version'")
    row = cur.fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO sync_state(key,value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION_CORRENTE),),
        )
    else:
        applicata = int(row['value'])
        if applicata < SCHEMA_VERSION_CORRENTE:
            _apply_migrations(conn, applicata)

    # Recovery: pending in_flight rimaste a startup (es. dopo crash)
    conn.execute(
        "UPDATE pending_writes SET state='pending' WHERE state='in_flight'"
    )


def _apply_migrations(conn, da_versione):
    """Applica migrations dalla versione `da_versione` alla corrente.

    Ogni migration è idempotente. Aggiungere qui nuovi step (alter table,
    nuovi indici, ecc.) incrementando SCHEMA_VERSION_CORRENTE.
    """
    # Per ora c'è solo la v1. Quando aggiungerai una v2, scrivi il blocco
    # `if da_versione < 2: ... ; da_versione = 2`.
    conn.execute(
        "UPDATE sync_state SET value=? WHERE key='schema_version'",
        (str(SCHEMA_VERSION_CORRENTE),),
    )


# ============================================================================
# HELPER NORMALIZZAZIONE & HASH
# ============================================================================
def normalize_eneltel(value):
    """Normalizza un Eneltel: stringa, lstrip zeri, strip spazi."""
    return str(value if value is not None else '').strip().lstrip('0')


def compute_row_hash(values):
    """Calcola hash MD5 stabile di una riga.

    Args:
        values: list[str] | tuple — valori cella nell'ordine in cui appaiono
            nel foglio (riga corrente).

    Returns:
        hex digest a 32 char.
    """
    canonical = '|'.join('' if v is None else str(v) for v in values)
    return hashlib.md5(canonical.encode('utf-8')).hexdigest()


def _now_ts():
    return int(time.time())


# ============================================================================
# COLUMN REGISTRY (mappatura nome colonna ↔ chiave interna)
# ============================================================================
def _build_internal_lookup():
    """Costruisce mappa {nome_colonna_foglio_lower: chiave_interna}.

    Usa MAPPATURA_COLONNE (override utente) + MAPPATURA_COLONNE_DEFAULT.
    """
    try:
        from config import MAPPATURA_COLONNE, MAPPATURA_COLONNE_DEFAULT
    except ImportError:
        return {}

    lookup = {}
    # Default
    for chiave, info in MAPPATURA_COLONNE_DEFAULT.items():
        for nome in info.get('nomi_default', []):
            lookup[nome.strip().lower()] = chiave
    # Eneltel non è in COLONNE_NOTE ma deve essere riconosciuto
    lookup.setdefault('eneltel', 'eneltel')

    # Override utente (vince sui default)
    for chiave, nome in (MAPPATURA_COLONNE or {}).items():
        if nome:
            lookup[str(nome).strip().lower()] = chiave
    return lookup


def update_column_registry(conn, headers):
    """Aggiorna `column_registry` con gli header letti dal foglio.

    Args:
        conn: connessione SQLite (TX esterna possibile).
        headers: list[str] — headers riga 1 nell'ordine del foglio.
    """
    lookup = _build_internal_lookup()
    now = _now_ts()
    for idx, name in enumerate(headers, start=1):
        if not name:
            continue
        chiave = lookup.get(name.strip().lower())
        conn.execute(
            'INSERT OR REPLACE INTO column_registry(column_name, chiave_interna, last_index, last_seen_ts) '
            'VALUES (?, ?, ?, ?)',
            (name, chiave, idx, now),
        )


def column_to_internal_key(conn, column_name):
    """Ritorna la chiave interna per un nome colonna del foglio (o None)."""
    cur = conn.execute(
        'SELECT chiave_interna FROM column_registry WHERE LOWER(column_name)=?',
        (column_name.strip().lower(),),
    )
    row = cur.fetchone()
    if row and row['chiave_interna']:
        return row['chiave_interna']
    # Fallback: lookup live (registry non ancora popolato in fase di bootstrap)
    return _build_internal_lookup().get(column_name.strip().lower())


def canonical_column_name(conn, column_name):
    """Normalizza un nome colonna al case esatto presente sul foglio.

    Es: client invia 'email', foglio ha 'EMAIL' → ritorna 'EMAIL'.
    Se la colonna non esiste in column_registry, ritorna il nome originale
    (sarà il drainer a quarantinare la pending).
    """
    if not column_name:
        return column_name
    cur = conn.execute(
        'SELECT column_name FROM column_registry WHERE LOWER(column_name)=? LIMIT 1',
        (column_name.strip().lower(),),
    )
    row = cur.fetchone()
    return row['column_name'] if row else column_name


# ============================================================================
# AUDIT
# ============================================================================
def audit(conn, *, eneltel_key=None, column_name=None,
          old_value=None, new_value=None, source, user=None, detail=None):
    """Inserisce una entry in audit_log.

    `source` ∈ {'local','remote','conflict_local_wins','quarantine','bootstrap'}
    `detail` può essere dict (sarà serializzato JSON).
    """
    if isinstance(detail, (dict, list)):
        detail = json.dumps(detail, ensure_ascii=False)
    conn.execute(
        'INSERT INTO audit_log(ts, eneltel_key, column_name, old_value, new_value, source, user, detail) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (_now_ts(), eneltel_key, column_name,
         None if old_value is None else str(old_value),
         None if new_value is None else str(new_value),
         source, user, detail),
    )


def record_anomaly(conn, *, kind, eneltel_key=None, sheets_row=None, detail=None):
    """Registra un'anomalia (Eneltel duplicato, riga senza Eneltel, ecc.)."""
    if isinstance(detail, (dict, list)):
        detail = json.dumps(detail, ensure_ascii=False)
    conn.execute(
        'INSERT INTO filone_anomalies(ts, kind, eneltel_key, sheets_row, detail) '
        'VALUES (?, ?, ?, ?, ?)',
        (_now_ts(), kind, eneltel_key, sheets_row, detail),
    )


# ============================================================================
# WRITE PATH (locale → DB → pending_writes)
# ============================================================================
def record_local_change(eneltel, fields, *, base_values=None, user=None,
                        origin='unknown', initial_state='pending'):
    """Registra una modifica locale: aggiorna `filone` e accoda `pending_writes`.

    Args:
        eneltel: identificativo riga (verrà normalizzato).
        fields: dict {nome_colonna_sul_foglio: nuovo_valore}.
            ATTENZIONE: il nome è quello SUL FOGLIO (es. 'NOTE APP'), non
            la chiave interna (es. 'note_appuntamento'). Il mapping a chiave
            interna avviene tramite `column_registry`.
        base_values: dict {nome_colonna: valore_noto_al_momento_della_modifica}.
            Se None, viene letto dal record corrente in `filone`. Serve a
            rilevare conflitti veri vs falsi al pull successivo.
        user: username del modificatore (per audit).
        origin: stringa libera che identifica la route (per audit/debug).
        initial_state: stato della pending_write inserita.
            - 'pending' (default, Fase 2): il drainer la pusherà su Sheets.
            - 'done' (Fase 1 shadow): la route ha già scritto su Sheets in
              modo sincrono; questa entry serve solo da audit trail e da
              snapshot per validazione DB.

    Returns:
        dict {written_local: int, pending_count: int}
    """
    eneltel_key = normalize_eneltel(eneltel)
    if not eneltel_key:
        raise ValueError('eneltel vuoto')
    if not fields:
        return {'written_local': 0, 'pending_count': 0}

    conn = get_conn()
    now = _now_ts()

    with conn:  # TX
        conn.execute('BEGIN IMMEDIATE')

        # Carica riga corrente (per base_values e per UPDATE filone)
        cur = conn.execute('SELECT * FROM filone WHERE eneltel_key=?', (eneltel_key,))
        existing = cur.fetchone()
        if existing is None:
            # Riga non ancora nel DB locale: registriamo solo le pending,
            # `filone` verrà popolato al prossimo pull. NB: questo capita solo
            # in casi limite (DB appena bootstrappato + write prima del primo pull).
            existing_row = {}
        else:
            existing_row = dict(existing)

        pending_count = 0
        local_updates_typed = {}  # {col_sql: nuovo_valore}
        local_updates_extra = {}  # {nome_colonna_foglio: nuovo_valore}

        for col_name_raw, new_val in fields.items():
            if not col_name_raw:
                continue
            # Normalizza al case esatto del foglio (es. 'email' → 'EMAIL')
            # così pending e merge_to_values trovano la colonna corretta.
            col_name = canonical_column_name(conn, col_name_raw)
            chiave = column_to_internal_key(conn, col_name)

            # base_value: priorità a quello fornito; fallback al DB
            if base_values and col_name in base_values:
                base_val = base_values[col_name]
            else:
                base_val = (existing_row.get(chiave) if chiave in COLONNE_NOTE
                            else _read_extra(conn, eneltel_key, col_name))

            # 1. Pending write — coalesce per (eneltel,col): cancello le
            # pending precedenti non ancora pushate e ne loggo la sostituzione.
            old_pending = conn.execute(
                "SELECT id, new_value FROM pending_writes "
                "WHERE eneltel_key=? AND column_name=? AND state IN ('pending','in_flight')",
                (eneltel_key, col_name),
            ).fetchall()
            for op in old_pending:
                audit(conn, eneltel_key=eneltel_key, column_name=col_name,
                      old_value=op['new_value'], new_value=new_val,
                      source='local', user=user,
                      detail={'origin': origin, 'replaced_pending_id': op['id']})
            conn.execute(
                "DELETE FROM pending_writes "
                "WHERE eneltel_key=? AND column_name=? AND state IN ('pending','in_flight')",
                (eneltel_key, col_name),
            )

            conn.execute(
                'INSERT INTO pending_writes(eneltel_key, column_name, new_value, base_value, '
                'created_ts, user, origin, state) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (eneltel_key, col_name,
                 None if new_val is None else str(new_val),
                 None if base_val is None else str(base_val),
                 now, user, origin, initial_state),
            )
            pending_count += 1

            # 2. Audit (modifica locale "vera")
            audit(conn, eneltel_key=eneltel_key, column_name=col_name,
                  old_value=base_val, new_value=new_val,
                  source='local', user=user,
                  detail={'origin': origin})

            # 3. Aggiorna riga locale (preview ottimistica)
            if chiave in COLONNE_NOTE:
                local_updates_typed[chiave] = new_val
            elif chiave == 'eneltel':
                # rinominare l'eneltel non lo facciamo qui: troppo invasivo.
                logger.warning(f"[DB] Tentativo di modificare la colonna Eneltel su {eneltel_key}: ignorato")
            else:
                local_updates_extra[col_name] = new_val

        if existing is not None:
            if local_updates_typed:
                set_clause = ', '.join(f'{k}=?' for k in local_updates_typed)
                vals = list(local_updates_typed.values())
                vals.append(now)
                vals.append(eneltel_key)
                conn.execute(
                    f'UPDATE filone SET {set_clause}, last_local_change_ts=? WHERE eneltel_key=?',
                    vals,
                )
            elif local_updates_extra:
                conn.execute(
                    'UPDATE filone SET last_local_change_ts=? WHERE eneltel_key=?',
                    (now, eneltel_key),
                )
            for col_name, val in local_updates_extra.items():
                conn.execute(
                    'INSERT OR REPLACE INTO filone_extra(eneltel_key, column_name, value) VALUES (?, ?, ?)',
                    (eneltel_key, col_name, None if val is None else str(val)),
                )

    return {'written_local': len(fields), 'pending_count': pending_count}


def _read_extra(conn, eneltel_key, column_name):
    """Lettura puntuale da filone_extra."""
    cur = conn.execute(
        'SELECT value FROM filone_extra WHERE eneltel_key=? AND column_name=?',
        (eneltel_key, column_name),
    )
    row = cur.fetchone()
    return row['value'] if row else None


# ============================================================================
# READ HELPERS
# ============================================================================
def get_filone_row(eneltel):
    """Ritorna la riga `filone` come dict, o None."""
    eneltel_key = normalize_eneltel(eneltel)
    conn = get_conn()
    cur = conn.execute('SELECT * FROM filone WHERE eneltel_key=?', (eneltel_key,))
    row = cur.fetchone()
    return dict(row) if row else None


def find_eneltel_by_pod(pod):
    """Lookup POD → eneltel_key. None se non trovato."""
    if not pod:
        return None
    pod_norm = str(pod).strip().upper()
    conn = get_conn()
    cur = conn.execute(
        'SELECT eneltel_key FROM filone WHERE UPPER(TRIM(pod))=? LIMIT 1',
        (pod_norm,),
    )
    row = cur.fetchone()
    return row['eneltel_key'] if row else None


def find_eneltel_by_sheets_row(row_num):
    """Lookup numero riga (1-based) → eneltel_key. None se non trovato.

    NB: la riga può cambiare se il foglio viene riordinato. Da usare solo
    come fallback quando POD/Eneltel non sono disponibili.
    """
    conn = get_conn()
    cur = conn.execute(
        'SELECT eneltel_key FROM filone WHERE sheets_row=? LIMIT 1',
        (int(row_num),),
    )
    row = cur.fetchone()
    return row['eneltel_key'] if row else None


# ============================================================================
# SHADOW WRITE PATH (Fase 1 della migrazione)
# ============================================================================
def shadow_record_batch(items, *, anchor_col=None, user=None, origin='unknown'):
    """Registra un batch di scritture già applicate su Sheets.

    Pensato per essere chiamato DA `shared.safe_batch_update_by_name` SUBITO
    dopo che le scritture sono andate a buon fine: il DB locale viene
    aggiornato in parallelo (Fase 1 shadow), pending_writes viene popolato
    con state='done' come audit trail.

    Args:
        items: lista di dict con almeno 'row', 'col', 'value'. Opzionale 'anchor'.
        anchor_col: nome colonna ancora ('Eneltel' / 'POD' / ...) o None.
        user: username del modificatore.
        origin: stringa identificativa (es. 'route_contatori_update').

    Errori sono assorbiti e loggati: questa funzione NON deve mai bloccare
    il flusso di scrittura principale.
    """
    if not items:
        return {'recorded': 0}

    try:
        anchor_col_low = (anchor_col or '').lower()
        # Risolvi eneltel per ogni item
        by_eneltel = {}
        unresolved = 0
        for it in items:
            row = it.get('row')
            col = it.get('col')
            value = it.get('value')
            if not col or row is None:
                continue
            anchor_val = it.get('anchor')

            if anchor_col_low == 'eneltel' and anchor_val:
                ek = normalize_eneltel(anchor_val)
            elif anchor_col_low == 'pod' and anchor_val:
                ek = find_eneltel_by_pod(anchor_val)
            else:
                # Nessuna ancora utilizzabile: prova via sheets_row
                ek = find_eneltel_by_sheets_row(row)

            if not ek:
                unresolved += 1
                continue
            by_eneltel.setdefault(ek, {})[col] = value

        recorded = 0
        for ek, fields in by_eneltel.items():
            try:
                record_local_change(
                    ek, fields, user=user, origin=origin,
                    initial_state='done',
                )
                recorded += 1
            except Exception as e:
                logger.warning(f"[shadow] record_local_change fallito per {ek}: {e}")
        return {'recorded': recorded, 'unresolved': unresolved}
    except Exception as e:
        logger.warning(f"[shadow] shadow_record_batch fallito (assorbito): {e}")
        return {'recorded': 0, 'error': str(e)}


def shadow_record_single(eneltel, fields, *, user=None, origin='unknown'):
    """Variante a singola riga di shadow_record_batch (per le 2 funzioni
    appuntamento e per le scritture cell-by-cell)."""
    if not eneltel or not fields:
        return
    try:
        record_local_change(
            eneltel, fields, user=user, origin=origin, initial_state='done',
        )
    except Exception as e:
        logger.warning(f"[shadow] shadow_record_single fallito per {eneltel}: {e}")


def list_pending_writes(state='pending', limit=400):
    """Drainer worker chiama questo: ritorna pending pronte alla scrittura."""
    conn = get_conn()
    cur = conn.execute(
        'SELECT * FROM pending_writes WHERE state=? AND next_attempt_ts <= ? '
        'ORDER BY id LIMIT ?',
        (state, _now_ts(), limit),
    )
    return [dict(r) for r in cur.fetchall()]


def mark_pending_in_flight(ids):
    """Marca un batch di pending come in_flight (lock applicativo)."""
    if not ids:
        return
    conn = get_conn()
    placeholders = ','.join('?' * len(ids))
    with conn:
        conn.execute(
            f"UPDATE pending_writes SET state='in_flight' WHERE id IN ({placeholders})",
            list(ids),
        )


def mark_pending_done(ids):
    if not ids:
        return
    conn = get_conn()
    placeholders = ','.join('?' * len(ids))
    with conn:
        conn.execute(
            f"UPDATE pending_writes SET state='done' WHERE id IN ({placeholders})",
            list(ids),
        )


def mark_pending_failed(ids, error, *, retry_in_seconds=60):
    """Riporta a 'pending' con backoff — il drainer riproverà dopo retry_in_seconds."""
    if not ids:
        return
    conn = get_conn()
    next_ts = _now_ts() + max(1, int(retry_in_seconds))
    placeholders = ','.join('?' * len(ids))
    with conn:
        conn.execute(
            f"UPDATE pending_writes SET state='pending', attempts=attempts+1, "
            f"last_error=?, next_attempt_ts=? WHERE id IN ({placeholders})",
            [str(error)[:500], next_ts] + list(ids),
        )


def quarantine_pending(ids, reason):
    """Marca pending come `quarantined` (anchor lost / colonna sparita / ecc.)."""
    if not ids:
        return
    conn = get_conn()
    placeholders = ','.join('?' * len(ids))
    with conn:
        conn.execute(
            f"UPDATE pending_writes SET state='quarantined', last_error=? WHERE id IN ({placeholders})",
            [str(reason)[:500]] + list(ids),
        )
        # Audit per ognuna
        rows = conn.execute(
            f'SELECT id, eneltel_key, column_name, new_value FROM pending_writes WHERE id IN ({placeholders})',
            list(ids),
        ).fetchall()
        for r in rows:
            audit(conn, eneltel_key=r['eneltel_key'], column_name=r['column_name'],
                  new_value=r['new_value'], source='quarantine',
                  detail={'pending_id': r['id'], 'reason': reason})


# ============================================================================
# SYNC STATE (key/value)
# ============================================================================
def sync_state_get(key, default=None):
    conn = get_conn()
    cur = conn.execute('SELECT value FROM sync_state WHERE key=?', (key,))
    row = cur.fetchone()
    return row['value'] if row else default


def sync_state_set(key, value):
    conn = get_conn()
    with conn:
        conn.execute(
            'INSERT INTO sync_state(key,value) VALUES(?,?) '
            'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
            (key, str(value)),
        )


# ============================================================================
# UPSERT RIGA (usato dal pull engine e dal bootstrap)
# ============================================================================
def upsert_filone_row(conn, *, eneltel_key, sheets_row, typed_values, extra_values,
                     row_hash, source='remote', skip_columns=None):
    """Upsert di una riga (chiamata in TX dal pull engine).

    Args:
        conn: connessione (TX esistente).
        eneltel_key: già normalizzato.
        sheets_row: numero riga 1-based.
        typed_values: dict {chiave_interna: valore} per le colonne note (può essere parziale).
        extra_values: dict {nome_colonna_foglio: valore} per le colonne extra.
        row_hash: hash della riga remota.
        source: 'remote' (sync da Sheets) o 'bootstrap' (migrazione iniziale).
        skip_columns: set di chiavi_interne da NON sovrascrivere (cells con
            pending in volo, gestite a parte dal pull).
    """
    skip_columns = skip_columns or set()
    now = _now_ts()

    cur = conn.execute('SELECT * FROM filone WHERE eneltel_key=?', (eneltel_key,))
    existing = cur.fetchone()

    if existing is None:
        cols = ['eneltel_key', 'sheets_row']
        vals = [eneltel_key, sheets_row]
        for c in COLONNE_NOTE:
            cols.append(c)
            vals.append(typed_values.get(c))
        cols.extend(['row_hash', 'last_remote_pull_ts', 'is_orphan'])
        vals.extend([row_hash, now, 0])
        placeholders = ','.join('?' * len(cols))
        conn.execute(
            f'INSERT INTO filone({",".join(cols)}) VALUES ({placeholders})',
            vals,
        )
        if source == 'remote':
            audit(conn, eneltel_key=eneltel_key, source='remote',
                  detail={'kind': 'new_row', 'sheets_row': sheets_row})
    else:
        # Aggiorna SOLO le colonne note non skippate + meta
        sets = ['sheets_row=?', 'row_hash=?', 'last_remote_pull_ts=?', 'is_orphan=0']
        vals = [sheets_row, row_hash, now]
        for c in COLONNE_NOTE:
            if c in skip_columns:
                continue
            if c in typed_values:
                sets.append(f'{c}=?')
                vals.append(typed_values[c])
        vals.append(eneltel_key)
        conn.execute(f'UPDATE filone SET {", ".join(sets)} WHERE eneltel_key=?', vals)

    # Extra values: upsert (skip se è skippata)
    for col_name, value in extra_values.items():
        if col_name in skip_columns:
            continue
        conn.execute(
            'INSERT INTO filone_extra(eneltel_key, column_name, value) VALUES (?, ?, ?) '
            'ON CONFLICT(eneltel_key, column_name) DO UPDATE SET value=excluded.value',
            (eneltel_key, col_name, None if value is None else str(value)),
        )


def mark_orphans(conn, alive_eneltel_keys):
    """Marca come orphan tutte le righe NON presenti nel set fornito.

    Args:
        alive_eneltel_keys: set[str] con tutti gli eneltel_key visti nell'ultimo pull.
    """
    cur = conn.execute('SELECT eneltel_key FROM filone WHERE is_orphan=0')
    locali = {r['eneltel_key'] for r in cur.fetchall()}
    nuovi_orfani = locali - alive_eneltel_keys
    if not nuovi_orfani:
        return 0
    placeholders = ','.join('?' * len(nuovi_orfani))
    conn.execute(
        f'UPDATE filone SET is_orphan=1 WHERE eneltel_key IN ({placeholders})',
        list(nuovi_orfani),
    )
    for ek in nuovi_orfani:
        audit(conn, eneltel_key=ek, source='remote',
              detail={'kind': 'orphaned'})
    return len(nuovi_orfani)


# ============================================================================
# DUMP (per generare mappa_data.json o per validazione shadow)
# ============================================================================
def dump_filone():
    """Ritorna lista di dict {eneltel_key, sheets_row, ..., extra: {...}}.

    Usata dal validatore notturno e (in fase 2) dal generatore di mappa_data.json.
    Esclude righe orphan.
    """
    conn = get_conn()
    cur = conn.execute('SELECT * FROM filone WHERE is_orphan=0 ORDER BY sheets_row')
    righe = [dict(r) for r in cur.fetchall()]

    if not righe:
        return []

    # Carica extra in batch
    extras = {}
    cur = conn.execute('SELECT eneltel_key, column_name, value FROM filone_extra')
    for r in cur.fetchall():
        extras.setdefault(r['eneltel_key'], {})[r['column_name']] = r['value']

    for r in righe:
        r['extra'] = extras.get(r['eneltel_key'], {})

    return righe


# ============================================================================
# DIAGNOSTICA
# ============================================================================
def stats():
    """Statistiche rapide sul DB (per /api/admin/db-stats e dump_db.py)."""
    conn = get_conn()
    out = {}
    for tabella in ('filone', 'filone_extra', 'pending_writes', 'audit_log',
                    'column_registry', 'filone_anomalies'):
        out[tabella] = conn.execute(f'SELECT COUNT(*) AS c FROM {tabella}').fetchone()['c']
    out['pending_by_state'] = {
        r['state']: r['c'] for r in conn.execute(
            'SELECT state, COUNT(*) AS c FROM pending_writes GROUP BY state'
        ).fetchall()
    }
    out['orphans'] = conn.execute(
        'SELECT COUNT(*) AS c FROM filone WHERE is_orphan=1'
    ).fetchone()['c']
    out['schema_version'] = sync_state_get('schema_version')
    out['db_path'] = _get_db_path()
    return out


def integrity_check():
    """PRAGMA integrity_check — chiamato a startup. Ritorna 'ok' o errore."""
    conn = get_conn()
    cur = conn.execute('PRAGMA integrity_check')
    row = cur.fetchone()
    return row[0] if row else 'unknown'
