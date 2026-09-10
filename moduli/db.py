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

SCHEMA_VERSION_CORRENTE = 7


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

    # Schema v2: PK di filone è (eneltel_key, lcl) per supportare righe con
    # stesso Eneltel ma LCL diversi (Pavia multi-LCL).
    conn.executescript(f'''
        CREATE TABLE IF NOT EXISTS sync_state (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS filone (
            eneltel_key TEXT NOT NULL,
            lcl TEXT NOT NULL DEFAULT '',
            sheets_row INTEGER NOT NULL,
            {', '.join(c for c in cols_sql if not c.startswith('lcl '))},
            row_hash TEXT NOT NULL,
            last_remote_pull_ts INTEGER NOT NULL,
            last_local_change_ts INTEGER,
            is_orphan INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (eneltel_key, lcl)
        );
        CREATE INDEX IF NOT EXISTS idx_filone_pod ON filone(pod);
        CREATE INDEX IF NOT EXISTS idx_filone_sheets_row ON filone(sheets_row);
        CREATE INDEX IF NOT EXISTS idx_filone_data_pian ON filone(data_pianificata);
        CREATE INDEX IF NOT EXISTS idx_filone_eneltel ON filone(eneltel_key);

        CREATE TABLE IF NOT EXISTS filone_extra (
            eneltel_key TEXT NOT NULL,
            lcl TEXT NOT NULL DEFAULT '',
            column_name TEXT NOT NULL,
            value TEXT,
            PRIMARY KEY (eneltel_key, lcl, column_name),
            FOREIGN KEY (eneltel_key, lcl) REFERENCES filone(eneltel_key, lcl) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS cell_comments (
            eneltel_key TEXT NOT NULL,
            lcl TEXT NOT NULL DEFAULT '',
            column_name TEXT NOT NULL,
            comment TEXT NOT NULL,
            updated_ts INTEGER NOT NULL,
            user TEXT,
            source TEXT,
            PRIMARY KEY (eneltel_key, lcl, column_name),
            FOREIGN KEY (eneltel_key, lcl) REFERENCES filone(eneltel_key, lcl) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS pending_writes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            eneltel_key TEXT NOT NULL,
            lcl TEXT NOT NULL DEFAULT '',
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
            ON pending_writes(eneltel_key, lcl, column_name)
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

        CREATE TABLE IF NOT EXISTS comuni_info (
            provincia TEXT NOT NULL,
            comune TEXT NOT NULL,
            numero_ce TEXT,
            ztl TEXT,
            indicazioni_ztl TEXT,
            tipologie_zone TEXT,
            polizia TEXT,
            uff_tecnico TEXT,
            protocollo TEXT,
            fb TEXT,
            email_comune TEXT,
            email_polizia TEXT,
            email_uff_tecnico TEXT,
            telefono_comune TEXT,
            telefono_polizia TEXT,
            telefono_uff_tecnico TEXT,
            pec_comune TEXT,
            note TEXT,
            last_updated_ts INTEGER NOT NULL,
            PRIMARY KEY (provincia, comune)
        );
    ''')

    _create_lettere_tables(conn)
    _create_mail_tables(conn)

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

    Ogni migration è idempotente.
    """
    if da_versione < 2:
        _migrate_v1_to_v2(conn)
        da_versione = 2
    if da_versione < 3:
        _migrate_v2_to_v3(conn)
        da_versione = 3
    if da_versione < 4:
        _migrate_v3_to_v4(conn)
        da_versione = 4
    if da_versione < 5:
        _migrate_v4_to_v5(conn)
        da_versione = 5
    if da_versione < 6:
        _migrate_v5_to_v6(conn)
        da_versione = 6
    if da_versione < 7:
        _migrate_v6_to_v7(conn)
        da_versione = 7

    conn.execute(
        "UPDATE sync_state SET value=? WHERE key='schema_version'",
        (str(SCHEMA_VERSION_CORRENTE),),
    )


def _migrate_v1_to_v2(conn):
    """Schema v2: PK di `filone`/`filone_extra` diventa composta
    `(eneltel_key, lcl)` per supportare righe con stesso Eneltel ma LCL
    diverso (Pavia multi-LCL). Aggiunge `lcl` a `pending_writes`.

    Migration sicura: nessuna perdita di dati. Le righe duplicate che
    prima finivano in `filone_anomalies` verranno re-importate dal pull
    successivo (perché lo schema ora le accetta).
    """
    logger.info("[DB-migration] v1→v2: PK composta (eneltel_key, lcl)")

    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='filone'")
    if not cur.fetchone():
        return  # DB fresh, niente da migrare

    # Filone: ricrea con PK (eneltel_key, lcl)
    cols_sql = []
    for c in COLONNE_NOTE:
        if c == 'lcl':
            continue  # dichiarato esplicitamente nella PK
        tipo = 'REAL' if c in _COLONNE_REAL else 'TEXT'
        cols_sql.append(f'{c} {tipo}')

    conn.executescript(f'''
        CREATE TABLE filone_v2 (
            eneltel_key TEXT NOT NULL,
            lcl TEXT NOT NULL DEFAULT '',
            sheets_row INTEGER NOT NULL,
            {', '.join(cols_sql)},
            row_hash TEXT NOT NULL,
            last_remote_pull_ts INTEGER NOT NULL,
            last_local_change_ts INTEGER,
            is_orphan INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (eneltel_key, lcl)
        );
    ''')

    # Colonne v1 escluso 'lcl' (sarà spostata in PK con COALESCE)
    cols_select = ['eneltel_key', "COALESCE(lcl,'')", 'sheets_row']
    cols_insert = ['eneltel_key', 'lcl', 'sheets_row']
    for c in COLONNE_NOTE:
        if c == 'lcl':
            continue
        cols_select.append(c)
        cols_insert.append(c)
    cols_select += ['row_hash', 'last_remote_pull_ts', 'last_local_change_ts', 'is_orphan']
    cols_insert += ['row_hash', 'last_remote_pull_ts', 'last_local_change_ts', 'is_orphan']

    conn.execute(
        f"INSERT INTO filone_v2 ({', '.join(cols_insert)}) "
        f"SELECT {', '.join(cols_select)} FROM filone"
    )
    conn.execute('DROP TABLE filone')
    conn.execute('ALTER TABLE filone_v2 RENAME TO filone')

    conn.executescript('''
        CREATE INDEX IF NOT EXISTS idx_filone_pod ON filone(pod);
        CREATE INDEX IF NOT EXISTS idx_filone_sheets_row ON filone(sheets_row);
        CREATE INDEX IF NOT EXISTS idx_filone_data_pian ON filone(data_pianificata);
        CREATE INDEX IF NOT EXISTS idx_filone_eneltel ON filone(eneltel_key);
    ''')

    # filone_extra: ricrea con PK (eneltel_key, lcl, column_name)
    conn.executescript('''
        CREATE TABLE filone_extra_v2 (
            eneltel_key TEXT NOT NULL,
            lcl TEXT NOT NULL DEFAULT '',
            column_name TEXT NOT NULL,
            value TEXT,
            PRIMARY KEY (eneltel_key, lcl, column_name)
        );
    ''')
    # Migrazione: per ogni riga di filone_extra v1, deduciamo LCL dalla riga
    # corrispondente in filone (univoca pre-v2). Se non c'è, lcl=''.
    conn.execute('''
        INSERT OR IGNORE INTO filone_extra_v2 (eneltel_key, lcl, column_name, value)
        SELECT fe.eneltel_key, COALESCE(f.lcl, ''), fe.column_name, fe.value
        FROM filone_extra fe
        LEFT JOIN filone f ON f.eneltel_key = fe.eneltel_key
    ''')
    conn.execute('DROP TABLE filone_extra')
    conn.execute('ALTER TABLE filone_extra_v2 RENAME TO filone_extra')

    # pending_writes: aggiungi colonna lcl + ricostruisci indice UNIQUE parziale
    pending_cols = {r['name'] for r in conn.execute('PRAGMA table_info(pending_writes)')}
    if 'lcl' not in pending_cols:
        conn.execute("ALTER TABLE pending_writes ADD COLUMN lcl TEXT NOT NULL DEFAULT ''")
    # Rimuovi vecchio indice UNIQUE su (eneltel_key, column_name) e creane uno nuovo
    conn.execute('DROP INDEX IF EXISTS idx_pw_coalesce')
    conn.execute('''
        CREATE UNIQUE INDEX idx_pw_coalesce
            ON pending_writes(eneltel_key, lcl, column_name)
            WHERE state IN ('pending','in_flight')
    ''')

    logger.info("[DB-migration] v2 applicato.")


def _migrate_v2_to_v3(conn):
    """Rende canonici i nomi correnti delle colonne del contatore installato.

    Le colonne storiche:
    - ``Matricola CE inst`` diventano ``Cod. Installato``;
    - ``cod.`` diventa ``Matr. Installato``.

    Gli import Torino eseguiti prima di questa migration avevano salvato i
    nuovi header in ``filone_extra`` perché non erano ancora mappati. I valori
    vengono trasferiti nei campi tipizzati prima di eliminare gli alias.
    """
    logger.info("[DB-migration] v2→v3: nomi correnti colonne installato")

    groups = (
        (
            'Cod. Installato',
            'matricola_installata',
            ('Matricola CE inst', 'Matricola misuratore installato',
             'MATRICOLA CE INST'),
        ),
        (
            'Matr. Installato',
            'codice_installato',
            ('cod.', 'Codice misuratore installat', 'CODICE CE INST'),
        ),
    )

    for canonical, internal_key, legacy_names in groups:
        all_names = (*legacy_names, canonical)

        # Prima gli alias storici, poi il nome corrente: il dato dell'export
        # attuale vince se entrambe le versioni sono presenti.
        for source_name in all_names:
            conn.execute(
                f'''
                UPDATE filone
                SET {internal_key} = (
                    SELECT fe.value
                    FROM filone_extra fe
                    WHERE fe.eneltel_key = filone.eneltel_key
                      AND COALESCE(fe.lcl, '') = COALESCE(filone.lcl, '')
                      AND fe.column_name = ?
                )
                WHERE EXISTS (
                    SELECT 1
                    FROM filone_extra fe
                    WHERE fe.eneltel_key = filone.eneltel_key
                      AND COALESCE(fe.lcl, '') = COALESCE(filone.lcl, '')
                      AND fe.column_name = ?
                )
                ''',
                (source_name, source_name),
            )

        placeholders = ','.join('?' for _ in all_names)
        conn.execute(
            f'DELETE FROM filone_extra WHERE column_name IN ({placeholders})',
            all_names,
        )

        # Coalesce delle pending attive: per la stessa cella conserva la più
        # recente, poi rinomina anche gli stati storici.
        active = conn.execute(
            f'''
            SELECT id, eneltel_key, COALESCE(lcl, '') AS lcl, column_name
            FROM pending_writes
            WHERE state IN ('pending', 'in_flight')
              AND column_name IN ({placeholders})
            ORDER BY id DESC
            ''',
            all_names,
        ).fetchall()
        winner_by_cell = {}
        for row in active:
            cell = (row['eneltel_key'], row['lcl'])
            winner_by_cell.setdefault(cell, row['id'])
        winner_ids = set(winner_by_cell.values())
        for row in active:
            if row['id'] not in winner_ids:
                conn.execute('DELETE FROM pending_writes WHERE id=?', (row['id'],))
        for pending_id in winner_ids:
            conn.execute(
                'UPDATE pending_writes SET column_name=? WHERE id=?',
                (canonical, pending_id),
            )
        conn.execute(
            f'''
            UPDATE pending_writes
            SET column_name=?
            WHERE column_name IN ({','.join('?' for _ in legacy_names)})
            ''',
            (canonical, *legacy_names),
        )

        # Commenti cella: conserva il più recente per riga e usa il nome
        # canonico anche per gli eventuali commenti storici.
        comments = conn.execute(
            f'''
            SELECT eneltel_key, COALESCE(lcl, '') AS lcl, comment,
                   updated_ts, user, source
            FROM cell_comments
            WHERE column_name IN ({placeholders})
            ORDER BY updated_ts
            ''',
            all_names,
        ).fetchall()
        conn.execute(
            f'DELETE FROM cell_comments WHERE column_name IN ({placeholders})',
            all_names,
        )
        for row in comments:
            conn.execute(
                '''
                INSERT OR REPLACE INTO cell_comments(
                    eneltel_key, lcl, column_name, comment, updated_ts, user, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ''',
                (row['eneltel_key'], row['lcl'], canonical, row['comment'],
                 row['updated_ts'], row['user'], row['source']),
            )

        registry_rows = conn.execute(
            f'''
            SELECT column_name, last_index, last_seen_ts
            FROM column_registry
            WHERE column_name IN ({placeholders})
            ''',
            all_names,
        ).fetchall()
        if registry_rows:
            current = next(
                (row for row in registry_rows if row['column_name'] == canonical),
                None,
            )
            selected = current or max(
                registry_rows,
                key=lambda row: (
                    row['last_seen_ts'] or 0,
                    row['last_index'] or 0,
                ),
            )
            conn.execute(
                f'DELETE FROM column_registry WHERE column_name IN ({placeholders})',
                all_names,
            )
            conn.execute(
                '''
                INSERT INTO column_registry(
                    column_name, chiave_interna, last_index, last_seen_ts
                ) VALUES (?, ?, ?, ?)
                ''',
                (canonical, internal_key, selected['last_index'],
                 selected['last_seen_ts']),
            )

    logger.info("[DB-migration] v3 applicato.")


def _create_lettere_tables(conn):
    """Crea le tabelle dell'Archivio Lettere.

    Il campo email resta unico: puo' contenere uno o piu' indirizzi mail/PEC,
    coerentemente con la colonna EMAIL del filone.
    """
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS lettere_generazioni (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provincia TEXT NOT NULL,
            file_name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_hash TEXT,
            periodo_da TEXT,
            periodo_a TEXT,
            created_ts INTEGER NOT NULL,
            created_by TEXT,
            totale_righe INTEGER NOT NULL DEFAULT 0,
            totale_aziende INTEGER NOT NULL DEFAULT 0,
            note TEXT,
            UNIQUE(provincia, file_hash)
        );
        CREATE INDEX IF NOT EXISTS idx_lettere_generazioni_periodo
            ON lettere_generazioni(provincia, periodo_da, periodo_a);

        CREATE TABLE IF NOT EXISTS lettere_righe (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            generazione_id INTEGER NOT NULL,
            source_sheet TEXT,
            row_num INTEGER,
            pod TEXT,
            eneltel TEXT,
            lcl TEXT,
            nominativo TEXT,
            via TEXT,
            civico TEXT,
            comune TEXT,
            provincia_sigla TEXT,
            cap TEXT,
            data_pianificazione TEXT,
            ora_pianificazione TEXT,
            lettera TEXT,
            operatore TEXT,
            posizione_mis TEXT,
            appuntamento TEXT,
            tipo_misuratore TEXT,
            telefono TEXT,
            email TEXT,
            is_azienda INTEGER NOT NULL DEFAULT 0,
            raw_json TEXT,
            FOREIGN KEY (generazione_id)
                REFERENCES lettere_generazioni(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_lettere_righe_gen
            ON lettere_righe(generazione_id);
        CREATE INDEX IF NOT EXISTS idx_lettere_righe_pod
            ON lettere_righe(pod);
        CREATE INDEX IF NOT EXISTS idx_lettere_righe_data
            ON lettere_righe(data_pianificazione);

        CREATE TABLE IF NOT EXISTS lettere_contatti (
            provincia TEXT NOT NULL,
            contact_key TEXT NOT NULL,
            pod TEXT,
            nominativo TEXT,
            comune TEXT,
            telefono TEXT,
            email TEXT,
            created_ts INTEGER NOT NULL,
            updated_ts INTEGER NOT NULL,
            updated_by TEXT,
            PRIMARY KEY (provincia, contact_key)
        );
        CREATE INDEX IF NOT EXISTS idx_lettere_contatti_pod
            ON lettere_contatti(provincia, pod);

        CREATE TABLE IF NOT EXISTS lettere_aziende_generazione (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            generazione_id INTEGER NOT NULL,
            provincia TEXT NOT NULL,
            contact_key TEXT NOT NULL,
            pod TEXT,
            nominativo TEXT,
            comune TEXT,
            data_pianificazione TEXT,
            ora_pianificazione TEXT,
            operatore TEXT,
            tipo_misuratore TEXT,
            lettera TEXT,
            risposta INTEGER NOT NULL DEFAULT 0,
            risposta_ts INTEGER,
            risposta_by TEXT,
            note TEXT,
            created_ts INTEGER NOT NULL,
            FOREIGN KEY (generazione_id)
                REFERENCES lettere_generazioni(id) ON DELETE CASCADE,
            UNIQUE(generazione_id, contact_key)
        );
        CREATE INDEX IF NOT EXISTS idx_lettere_aziende_gen
            ON lettere_aziende_generazione(generazione_id);
        CREATE INDEX IF NOT EXISTS idx_lettere_aziende_contact
            ON lettere_aziende_generazione(provincia, contact_key);
        CREATE INDEX IF NOT EXISTS idx_lettere_aziende_risposta
            ON lettere_aziende_generazione(risposta);

        CREATE TABLE IF NOT EXISTS lettere_invii (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            azienda_generazione_id INTEGER,
            generazione_id INTEGER,
            provincia TEXT NOT NULL,
            contact_key TEXT,
            pod TEXT,
            email_dest TEXT NOT NULL,
            is_pec INTEGER NOT NULL DEFAULT 0,
            template_id TEXT,
            subject TEXT,
            sent_ts INTEGER NOT NULL,
            sent_by TEXT,
            status TEXT NOT NULL,
            error TEXT,
            detail_json TEXT,
            FOREIGN KEY (azienda_generazione_id)
                REFERENCES lettere_aziende_generazione(id) ON DELETE SET NULL,
            FOREIGN KEY (generazione_id)
                REFERENCES lettere_generazioni(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_lettere_invii_azienda
            ON lettere_invii(azienda_generazione_id, sent_ts);
        CREATE INDEX IF NOT EXISTS idx_lettere_invii_contact
            ON lettere_invii(provincia, contact_key, sent_ts);
    ''')


def _migrate_v3_to_v4(conn):
    """Schema v4: archivio lettere e rubrica aziende."""
    logger.info("[DB-migration] v3→v4: archivio lettere")
    _create_lettere_tables(conn)
    logger.info("[DB-migration] v4 applicato.")


def _migrate_v4_to_v5(conn):
    """Schema v5: GESTIONE DEI COMUNI. Tabella info e recapiti per comune."""
    logger.info("[DB-migration] v4→v5: gestione dei comuni")
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS comuni_info (
            provincia TEXT NOT NULL,
            comune TEXT NOT NULL,
            numero_ce TEXT,
            ztl TEXT,
            tipologie_zone TEXT,
            polizia TEXT,
            uff_tecnico TEXT,
            protocollo TEXT,
            fb TEXT,
            email_comune TEXT,
            email_polizia TEXT,
            email_uff_tecnico TEXT,
            telefono_comune TEXT,
            telefono_polizia TEXT,
            telefono_uff_tecnico TEXT,
            pec_comune TEXT,
            note TEXT,
            last_updated_ts INTEGER NOT NULL,
            PRIMARY KEY (provincia, comune)
        );
    ''')
    logger.info("[DB-migration] v5 applicato.")

def _migrate_v5_to_v6(conn):
    """Schema v6: aggiunta indicazioni_ztl per differenziare la colonna Excel dal toggle."""
    logger.info("[DB-migration] v5→v6: add indicazioni_ztl")
    cur = conn.execute("PRAGMA table_info(comuni_info)")
    cols = {r[1] for r in cur.fetchall()}
    if 'indicazioni_ztl' not in cols:
        conn.execute('ALTER TABLE comuni_info ADD COLUMN indicazioni_ztl TEXT')
    logger.info("[DB-migration] v6 applicato.")


def _create_mail_tables(conn):
    """Crea le tabelle del modulo Gestione Mail."""
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS mail_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT,
            thread_id TEXT,
            provincia TEXT NOT NULL,
            folder TEXT NOT NULL DEFAULT 'inbox',
            from_addr TEXT NOT NULL,
            from_name TEXT,
            to_addrs TEXT NOT NULL,
            cc_addrs TEXT,
            bcc_addrs TEXT,
            reply_to TEXT,
            subject TEXT NOT NULL,
            body_text TEXT,
            body_html TEXT,
            snippet TEXT,
            category_id TEXT,
            is_read INTEGER NOT NULL DEFAULT 0,
            is_starred INTEGER NOT NULL DEFAULT 0,
            is_archived INTEGER NOT NULL DEFAULT 0,
            is_scheduled INTEGER NOT NULL DEFAULT 0,
            scheduled_ts INTEGER,
            sent_ts INTEGER,
            received_ts INTEGER,
            created_ts INTEGER NOT NULL,
            updated_ts INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'received',
            error_msg TEXT,
            attachments_json TEXT,
            linked_pod TEXT,
            linked_comune TEXT,
            linked_azienda TEXT,
            meta_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_mail_folder_ts
            ON mail_messages(provincia, folder, received_ts);
        CREATE INDEX IF NOT EXISTS idx_mail_category
            ON mail_messages(provincia, category_id);
        CREATE INDEX IF NOT EXISTS idx_mail_read
            ON mail_messages(provincia, is_read);
        CREATE INDEX IF NOT EXISTS idx_mail_scheduled
            ON mail_messages(folder, scheduled_ts);
        CREATE INDEX IF NOT EXISTS idx_mail_linked_pod
            ON mail_messages(provincia, linked_pod);
        CREATE INDEX IF NOT EXISTS idx_mail_msg_id
            ON mail_messages(provincia, message_id);

        CREATE TABLE IF NOT EXISTS mail_categories (
            id TEXT NOT NULL,
            provincia TEXT NOT NULL,
            name TEXT NOT NULL,
            color TEXT NOT NULL,
            icon TEXT NOT NULL,
            badge_bg TEXT,
            badge_text TEXT,
            keywords_json TEXT,
            sort_order INTEGER NOT NULL DEFAULT 0,
            is_system INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (provincia, id)
        );

        CREATE TABLE IF NOT EXISTS mail_templates_snippets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provincia TEXT NOT NULL,
            title TEXT NOT NULL,
            category TEXT,
            type TEXT NOT NULL DEFAULT 'template',
            subject TEXT,
            body TEXT NOT NULL,
            variables_json TEXT,
            created_ts INTEGER NOT NULL,
            updated_ts INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_mail_templates_prov
            ON mail_templates_snippets(provincia, type);
    ''')

    # Seed categorie e template default
    _seed_default_mail_data(conn)


def _seed_default_mail_data(conn):
    """Inizializza categorie e template/snippet standard se la tabella è vuota."""
    try:
        from config import PROVINCIA_ATTIVA
        prov = PROVINCIA_ATTIVA.lower()
    except Exception:
        prov = 'torino'

    cur = conn.execute("SELECT COUNT(*) AS c FROM mail_categories WHERE provincia = ?", (prov,))
    if cur.fetchone()['c'] == 0:
        default_categories = [
            ('clienti_appuntamenti', prov, 'Clienti & Appuntamenti', '#3b82f6', 'calendar', 'rgba(59,130,246,0.15)', '#60a5fa',
             json.dumps(['appuntamento', 'appuntamenti', 'spostamento', 'cliente', 'presenza', 'fascia oraria', 'interruzione', 'disponibilita', 'orario']), 1, 1),
            ('comuni_enti', prov, 'Enti & Comuni', '#10b981', 'building-2', 'rgba(16,185,129,0.15)', '#34d399',
             json.dumps(['comune', 'polizia locale', 'polizia municipale', 'ztl', 'ordinanza', 'occupazione suolo', 'sindaco', 'ufficio tecnico', 'autorizzazione']), 2, 1),
            ('distinte_enel', prov, 'E-Distribuzione & Distinte', '#f59e0b', 'file-text', 'rgba(245,158,11,0.15)', '#fbbf24',
             json.dumps(['distinta', 'distinte', 'maxy', 'verbale', 'e-distribuzione', 'eneltel', 'semidiretto', 'trifase', 'monofase', 'misuratore']), 3, 1),
            ('tecnici_operativi', prov, 'Tecnici & Operatività', '#8b5cf6', 'wrench', 'rgba(139,92,246,0.15)', '#a78bfa',
             json.dumps(['tecnico', 'operatore', 'sigillo', 'sigilli', 'matricola', 'installato', 'posa', 'guasto', 'sostituzione', 'boxbeat']), 4, 1),
            ('solleciti', prov, 'Solleciti & Urgenze', '#ef4444', 'alert-triangle', 'rgba(239,68,68,0.15)', '#f87171',
             json.dumps(['sollecito', 'sollecita', 'urgente', 'urgenza', 'reclamo', 'diffida', 'improrogabile', 'mancata', 'ritardo']), 5, 1),
            ('amministrazione', prov, 'Amministrazione & Varie', '#06b6d4', 'folder', 'rgba(6,182,212,0.15)', '#22d3ee',
             json.dumps(['amministrazione', 'fattura', 'pagamento', 'preventivo', 'contratto', 'amministrativo']), 6, 1),
        ]
        conn.executemany(
            """INSERT INTO mail_categories (id, provincia, name, color, icon, badge_bg, badge_text, keywords_json, sort_order, is_system)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            default_categories
        )

    # I template e frasari base sono centralizzati in data/distinte_templates.json
    # La tabella mail_templates_snippets ospita eventuali template/snippet personalizzati dall'utente.
    pass


def _migrate_v6_to_v7(conn):
    """Schema v7: modulo Gestione Mail (messaggi, categorie ad HOC, template e frasario)."""
    logger.info("[DB-migration] v6→v7: modulo Gestione Mail")
    _create_mail_tables(conn)
    logger.info("[DB-migration] v7 applicato.")

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
                        origin='unknown', initial_state='pending', lcl=None):
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

        # Risolvi (eneltel_key, lcl):
        # - Se lcl è None o '' (client non lo sa) → prima riga (legacy).
        # - Se lcl è valorizzato → match esatto. Se non trovato, fallback alla
        #   prima riga (es. il client passa '' per righe senza LCL ma in DB
        #   esiste una sola riga con lcl='...').
        existing = None
        if lcl is None or lcl == '':
            cur = conn.execute(
                "SELECT * FROM filone WHERE eneltel_key=? ORDER BY sheets_row DESC LIMIT 1",
                (eneltel_key,),
            )
            existing = cur.fetchone()
        else:
            cur = conn.execute(
                "SELECT * FROM filone WHERE eneltel_key=? AND COALESCE(lcl,'')=?",
                (eneltel_key, lcl),
            )
            existing = cur.fetchone()
            if existing is None:
                # Fallback alla riga più recente dell'eneltel
                cur = conn.execute(
                    "SELECT * FROM filone WHERE eneltel_key=? ORDER BY sheets_row DESC LIMIT 1",
                    (eneltel_key,),
                )
                existing = cur.fetchone()
        resolved_lcl = (existing['lcl'] or '') if existing else (lcl or '')
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
            col_name = canonical_column_name(conn, col_name_raw)
            chiave = column_to_internal_key(conn, col_name)

            # base_value: priorità a quello fornito; fallback al DB
            if base_values and col_name in base_values:
                base_val = base_values[col_name]
            else:
                base_val = (existing_row.get(chiave) if chiave in COLONNE_NOTE
                            else _read_extra(conn, eneltel_key, resolved_lcl, col_name))

            # Coalesce per (eneltel, lcl, col): cancella eventuali pending
            # precedenti non ancora pushate per quella stessa cella.
            old_pending = conn.execute(
                "SELECT id, new_value FROM pending_writes "
                "WHERE eneltel_key=? AND COALESCE(lcl,'')=? AND column_name=? "
                "AND state IN ('pending','in_flight')",
                (eneltel_key, resolved_lcl, col_name),
            ).fetchall()
            for op in old_pending:
                audit(conn, eneltel_key=eneltel_key, column_name=col_name,
                      old_value=op['new_value'], new_value=new_val,
                      source='local', user=user,
                      detail={'origin': origin, 'replaced_pending_id': op['id'],
                              'lcl': resolved_lcl})
            conn.execute(
                "DELETE FROM pending_writes "
                "WHERE eneltel_key=? AND COALESCE(lcl,'')=? AND column_name=? "
                "AND state IN ('pending','in_flight')",
                (eneltel_key, resolved_lcl, col_name),
            )

            conn.execute(
                'INSERT INTO pending_writes(eneltel_key, lcl, column_name, new_value, base_value, '
                'created_ts, user, origin, state) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (eneltel_key, resolved_lcl, col_name,
                 None if new_val is None else str(new_val),
                 None if base_val is None else str(base_val),
                 now, user, origin, initial_state),
            )
            pending_count += 1

            # 2. Audit (modifica locale "vera")
            audit(conn, eneltel_key=eneltel_key, column_name=col_name,
                  old_value=base_val, new_value=new_val,
                  source='local', user=user,
                  detail={'origin': origin, 'lcl': resolved_lcl})

            # 3. Aggiorna riga locale (preview ottimistica)
            if chiave in COLONNE_NOTE:
                local_updates_typed[chiave] = new_val
            elif chiave == 'eneltel':
                logger.warning(f"[DB] Tentativo di modificare la colonna Eneltel su {eneltel_key}: ignorato")
            else:
                local_updates_extra[col_name] = new_val

        if existing is not None:
            if local_updates_typed:
                set_clause = ', '.join(f'{k}=?' for k in local_updates_typed)
                vals = list(local_updates_typed.values())
                vals.append(now)
                vals.append(eneltel_key)
                vals.append(resolved_lcl)
                conn.execute(
                    f"UPDATE filone SET {set_clause}, last_local_change_ts=? "
                    f"WHERE eneltel_key=? AND COALESCE(lcl,'')=?",
                    vals,
                )
            elif local_updates_extra:
                conn.execute(
                    "UPDATE filone SET last_local_change_ts=? "
                    "WHERE eneltel_key=? AND COALESCE(lcl,'')=?",
                    (now, eneltel_key, resolved_lcl),
                )
            for col_name, val in local_updates_extra.items():
                conn.execute(
                    'INSERT OR REPLACE INTO filone_extra(eneltel_key, lcl, column_name, value) '
                    'VALUES (?, ?, ?, ?)',
                    (eneltel_key, resolved_lcl, col_name,
                     None if val is None else str(val)),
                )

    return {'written_local': len(fields), 'pending_count': pending_count}


def _read_extra(conn, eneltel_key, lcl, column_name):
    """Lettura puntuale da filone_extra."""
    cur = conn.execute(
        "SELECT value FROM filone_extra WHERE eneltel_key=? AND COALESCE(lcl,'')=? AND column_name=?",
        (eneltel_key, lcl or '', column_name),
    )
    row = cur.fetchone()
    return row['value'] if row else None


# ============================================================================
# READ HELPERS
# ============================================================================
def get_filone_row(eneltel, lcl=None):
    """Ritorna la riga `filone` come dict, o None.

    Se `lcl` è None: ritorna la "prima" riga associata all'eneltel
    (backward-compat). Se è una stringa (incluso ''): match esatto.
    """
    eneltel_key = normalize_eneltel(eneltel)
    conn = get_conn()
    if lcl is None:
        cur = conn.execute(
            'SELECT * FROM filone WHERE eneltel_key=? ORDER BY sheets_row LIMIT 1',
            (eneltel_key,),
        )
    else:
        cur = conn.execute(
            "SELECT * FROM filone WHERE eneltel_key=? AND COALESCE(lcl,'')=?",
            (eneltel_key, lcl or ''),
        )
    row = cur.fetchone()
    return dict(row) if row else None


def find_eneltel_by_pod(pod):
    """Lookup POD → eneltel_key (occorrenza più recente). None se non trovato.

    Compatibilità con codice legacy: ritorna solo l'eneltel, non il LCL.
    Per disambiguare righe duplicate usare `find_filone_row_by_pod`.
    """
    if not pod:
        return None
    res = find_filone_row_by_pod(pod)
    return res[0] if res else None


def find_filone_row_by_pod(pod, lcl=None, disabled_lcls=None):
    """Lookup POD (+ opzionale LCL) → (eneltel_key, lcl).

    Se `lcl` è valorizzato: prova match esatto (POD, LCL).
    Se non trovato o `lcl` è None/'': cerca tra i record con quel POD,
    privilegiando le LCL non disabilitate e la riga più recente (sheets_row DESC).

    Ritorna `(eneltel_key, lcl_della_riga_trovata)` o None.
    """
    if not pod:
        return None
    pod_norm = str(pod).strip().upper()
    conn = get_conn()
    if lcl is not None and str(lcl).strip() != '':
        lcl_norm = str(lcl).strip().upper()
        cur = conn.execute(
            "SELECT eneltel_key, COALESCE(lcl,'') AS lcl FROM filone "
            "WHERE UPPER(TRIM(pod))=? AND UPPER(TRIM(COALESCE(lcl,'')))=? "
            "ORDER BY sheets_row DESC LIMIT 1",
            (pod_norm, lcl_norm),
        )
        row = cur.fetchone()
        if row:
            return (row['eneltel_key'], row['lcl'])
        # Fallback: nessun match esatto sulla LCL richiesta

    # Fallback 1: cerca tra le LCL non disabilitate, prendendo la più recente
    if disabled_lcls:
        dis_list = [str(x).strip().upper() for x in disabled_lcls if str(x).strip()]
        if dis_list:
            dis_placeholders = ', '.join(['?'] * len(dis_list))
            cur = conn.execute(
                f"SELECT eneltel_key, COALESCE(lcl,'') AS lcl FROM filone "
                f"WHERE UPPER(TRIM(pod))=? AND UPPER(TRIM(COALESCE(lcl,''))) NOT IN ({dis_placeholders}) "
                f"ORDER BY sheets_row DESC LIMIT 1",
                [pod_norm] + dis_list,
            )
            row = cur.fetchone()
            if row:
                return (row['eneltel_key'], row['lcl'])

    # Fallback 2: riga più recente per quel POD
    cur = conn.execute(
        "SELECT eneltel_key, COALESCE(lcl,'') AS lcl FROM filone "
        "WHERE UPPER(TRIM(pod))=? ORDER BY sheets_row DESC LIMIT 1",
        (pod_norm,),
    )
    row = cur.fetchone()
    return (row['eneltel_key'], row['lcl']) if row else None


def find_inactive_lcl_contatori(pod_list=None, eneltel_list=None, nominativo=None, disabled_lcls=None, limit=50):
    """Cerca contatori appartenenti a LCL disabilitate per i quali non esiste
    alcun duplicato (stesso POD o stesso Eneltel) in una LCL attiva.

    Ritorna una lista di dict:
    [{'pod': ..., 'eneltel_key': ..., 'nominativo': ..., 'comune': ..., 'lcl': ...}, ...]
    """
    if not disabled_lcls:
        return []

    dis_list = [str(x).strip().upper() for x in disabled_lcls if str(x).strip()]
    if not dis_list:
        return []

    dis_placeholders = ', '.join(['?'] * len(dis_list))

    conditions = []
    params = list(dis_list)

    # Token POD
    cleaned_pods = [str(p).strip().upper() for p in (pod_list or []) if str(p).strip()]
    if cleaned_pods:
        pod_clauses = []
        for p in cleaned_pods:
            if len(p) >= 4:
                pod_clauses.append("(UPPER(TRIM(f.pod)) = ? OR UPPER(TRIM(f.pod)) LIKE ?)")
                params.extend([p, f"%{p}%"])
            else:
                pod_clauses.append("UPPER(TRIM(f.pod)) = ?")
                params.append(p)
        conditions.append(f"({' OR '.join(pod_clauses)})")

    # Token Eneltel
    cleaned_eneltels = [str(e).strip().upper() for e in (eneltel_list or []) if str(e).strip()]
    if cleaned_eneltels:
        eneltel_clauses = []
        for e in cleaned_eneltels:
            if len(e) >= 4:
                eneltel_clauses.append("(UPPER(TRIM(f.eneltel_key)) = ? OR UPPER(TRIM(f.eneltel_key)) LIKE ?)")
                params.extend([e, f"%{e}%"])
            else:
                eneltel_clauses.append("UPPER(TRIM(f.eneltel_key)) = ?")
                params.append(e)
        conditions.append(f"({' OR '.join(eneltel_clauses)})")

    # Nominativo
    if nominativo and str(nominativo).strip():
        nom_clean = str(nominativo).strip().upper()
        if len(nom_clean) >= 3:
            conditions.append("UPPER(f.nominativo) LIKE ?")
            params.append(f"%{nom_clean}%")

    if not conditions:
        return []

    where_search = ' OR '.join(conditions)
    params.extend(dis_list)

    sql = f"""
        SELECT DISTINCT
            COALESCE(f.pod, '') AS pod,
            COALESCE(f.eneltel_key, '') AS eneltel_key,
            COALESCE(f.nominativo, '') AS nominativo,
            COALESCE(f.comune, '') AS comune,
            COALESCE(f.lcl, '') AS lcl
        FROM filone f
        WHERE UPPER(TRIM(COALESCE(f.lcl, ''))) IN ({dis_placeholders})
          AND ({where_search})
          AND NOT EXISTS (
              SELECT 1 FROM filone a
              WHERE (
                  (f.pod IS NOT NULL AND f.pod != '' AND UPPER(TRIM(a.pod)) = UPPER(TRIM(f.pod)))
                  OR
                  (f.eneltel_key IS NOT NULL AND f.eneltel_key != '' AND UPPER(TRIM(a.eneltel_key)) = UPPER(TRIM(f.eneltel_key)))
              )
              AND (UPPER(TRIM(COALESCE(a.lcl, ''))) NOT IN ({dis_placeholders}) OR a.lcl IS NULL)
          )
        ORDER BY f.nominativo ASC
        LIMIT ?
    """
    params.append(int(limit))

    conn = get_conn()
    cur = conn.execute(sql, params)
    rows = cur.fetchall()
    return [
        {
            'pod': row['pod'],
            'eneltel_key': row['eneltel_key'],
            'nominativo': row['nominativo'],
            'comune': row['comune'],
            'lcl': row['lcl'],
        }
        for row in rows
    ]



def find_filone_row_by_sheets_row(row_num):
    """Lookup sheets_row → (eneltel_key, lcl). None se non trovato.

    NB: sheets_row è univoco a un dato istante (un foglio ha una riga per
    posizione). Indice utile.
    """
    conn = get_conn()
    cur = conn.execute(
        "SELECT eneltel_key, COALESCE(lcl,'') AS lcl FROM filone WHERE sheets_row=? LIMIT 1",
        (int(row_num),),
    )
    row = cur.fetchone()
    return (row['eneltel_key'], row['lcl']) if row else None


def find_eneltel_by_sheets_row(row_num):
    """Compat legacy: ritorna solo eneltel_key."""
    tup = find_filone_row_by_sheets_row(row_num)
    return tup[0] if tup else None


# ============================================================================
# COMMENTI CELLA
# ============================================================================
def _parse_comment_ui_key(key):
    """Converte una chiave UI 'riga:colonna' in (sheets_row, column_name)."""
    if not key or ':' not in str(key):
        return None
    row_raw, column_name = str(key).split(':', 1)
    column_name = column_name.strip()
    if not column_name:
        return None
    try:
        row_num = int(row_raw)
    except (TypeError, ValueError):
        return None
    if row_num < 2:
        return None
    return row_num, column_name


def get_cell_comments(*, include_orphans=False):
    """Ritorna i commenti cella come dict 'sheets_row:colonna' -> testo.

    Internamente i commenti sono ancorati a `(eneltel_key, lcl, column_name)`,
    così sopravvivono meglio a reimport e riordini. La UI storica però lavora
    su chiavi riga/colonna, quindi qui li rimaterializziamo sul `sheets_row`
    corrente.
    """
    conn = get_conn()
    where = '' if include_orphans else ' AND f.is_orphan=0'
    cur = conn.execute(f'''
        SELECT f.sheets_row, cc.column_name, cc.comment
        FROM cell_comments cc
        JOIN filone f
          ON f.eneltel_key = cc.eneltel_key
         AND COALESCE(f.lcl, '') = COALESCE(cc.lcl, '')
        WHERE cc.comment <> ''{where}
        ORDER BY f.sheets_row, cc.column_name
    ''')
    return {
        f"{r['sheets_row']}:{r['column_name']}": r['comment']
        for r in cur.fetchall()
    }


def set_cell_comments_by_anchor(conn, *, eneltel_key, lcl='', comments=None,
                                user=None, source='local'):
    """Upsert/delete commenti per una riga già risolta a `(eneltel_key, lcl)`.

    Args:
        conn: connessione SQLite già aperta; può essere dentro TX esterna.
        comments: dict {column_name: testo}. Testo vuoto cancella il commento.

    Returns:
        dict con conteggi `written`/`deleted`.
    """
    comments = comments or {}
    written = 0
    deleted = 0
    ek = normalize_eneltel(eneltel_key)
    resolved_lcl = lcl or ''
    if not ek:
        return {'written': 0, 'deleted': 0}

    now = _now_ts()
    for column_name, text in comments.items():
        col = str(column_name or '').strip()
        if not col:
            continue
        clean = str(text or '').strip()
        if clean:
            conn.execute(
                'INSERT INTO cell_comments(eneltel_key, lcl, column_name, comment, '
                'updated_ts, user, source) VALUES (?, ?, ?, ?, ?, ?, ?) '
                'ON CONFLICT(eneltel_key, lcl, column_name) DO UPDATE SET '
                'comment=excluded.comment, updated_ts=excluded.updated_ts, '
                'user=excluded.user, source=excluded.source',
                (ek, resolved_lcl, col, clean, now, user, source),
            )
            written += 1
        else:
            cur = conn.execute(
                "DELETE FROM cell_comments "
                "WHERE eneltel_key=? AND COALESCE(lcl,'')=? AND column_name=?",
                (ek, resolved_lcl, col),
            )
            deleted += max(cur.rowcount, 0)
    return {'written': written, 'deleted': deleted}


def set_cell_comments_from_ui(notes=None, cleared=None, *, user=None):
    """Applica modifiche commenti arrivate dalla UI.

    `notes` e `cleared` usano chiavi legacy 'sheets_row:column_name'. Le righe
    vengono risolte al momento della scrittura per salvare su ancora stabile
    `(eneltel_key, lcl)`.
    """
    notes = notes or {}
    cleared = cleared or []
    conn = get_conn()
    invalid = []
    written = 0
    deleted = 0

    def resolve(parsed):
        row_num, column_name = parsed
        row = conn.execute(
            "SELECT eneltel_key, COALESCE(lcl,'') AS lcl "
            "FROM filone WHERE sheets_row=? LIMIT 1",
            (row_num,),
        ).fetchone()
        if not row:
            return None
        return row['eneltel_key'], row['lcl'], column_name

    with conn:
        for key, text in notes.items():
            parsed = _parse_comment_ui_key(key)
            resolved = resolve(parsed) if parsed else None
            if not resolved:
                invalid.append(str(key))
                continue
            ek, lcl, column_name = resolved
            result = set_cell_comments_by_anchor(
                conn,
                eneltel_key=ek,
                lcl=lcl,
                comments={column_name: text},
                user=user,
                source='ui',
            )
            written += result['written']
            deleted += result['deleted']

        for key in cleared:
            parsed = _parse_comment_ui_key(key)
            resolved = resolve(parsed) if parsed else None
            if not resolved:
                invalid.append(str(key))
                continue
            ek, lcl, column_name = resolved
            result = set_cell_comments_by_anchor(
                conn,
                eneltel_key=ek,
                lcl=lcl,
                comments={column_name: ''},
                user=user,
                source='ui',
            )
            written += result['written']
            deleted += result['deleted']

    return {'written': written, 'deleted': deleted, 'invalid': invalid}


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
                     row_hash, source='remote', skip_columns=None, lcl=None):
    """Upsert di una riga (chiamata in TX dal pull engine).

    Args:
        eneltel_key: già normalizzato.
        sheets_row: numero riga 1-based.
        typed_values: dict {chiave_interna: valore}.
        extra_values: dict {nome_colonna_foglio: valore}.
        row_hash: hash della riga remota.
        source: 'remote' (sync da Sheets) o 'bootstrap' (migrazione iniziale).
        skip_columns: set di chiavi_interne da NON sovrascrivere.
        lcl: LCL della riga. Se None, viene letto da typed_values.get('lcl').
    """
    skip_columns = skip_columns or set()
    now = _now_ts()

    # LCL effettivo della riga: priorità a parametro esplicito, fallback al
    # valore nei typed_values (= colonna LCL della riga del foglio).
    if lcl is None:
        lcl = typed_values.get('lcl') or ''
    lcl = str(lcl) if lcl is not None else ''

    cur = conn.execute(
        "SELECT * FROM filone WHERE eneltel_key=? AND COALESCE(lcl,'')=?",
        (eneltel_key, lcl),
    )
    existing = cur.fetchone()

    if existing is None:
        cols = ['eneltel_key', 'lcl', 'sheets_row']
        vals = [eneltel_key, lcl, sheets_row]
        for c in COLONNE_NOTE:
            if c == 'lcl':
                continue  # già dichiarato come parte della PK
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
                  detail={'kind': 'new_row', 'sheets_row': sheets_row, 'lcl': lcl})
    else:
        sets = ['sheets_row=?', 'row_hash=?', 'last_remote_pull_ts=?', 'is_orphan=0']
        vals = [sheets_row, row_hash, now]
        for c in COLONNE_NOTE:
            if c == 'lcl':
                continue
            if c in skip_columns:
                continue
            if c in typed_values:
                sets.append(f'{c}=?')
                vals.append(typed_values[c])
        vals.append(eneltel_key)
        vals.append(lcl)
        conn.execute(
            f"UPDATE filone SET {', '.join(sets)} WHERE eneltel_key=? AND COALESCE(lcl,'')=?",
            vals,
        )

    for col_name, value in extra_values.items():
        if col_name in skip_columns:
            continue
        conn.execute(
            'INSERT INTO filone_extra(eneltel_key, lcl, column_name, value) VALUES (?, ?, ?, ?) '
            'ON CONFLICT(eneltel_key, lcl, column_name) DO UPDATE SET value=excluded.value',
            (eneltel_key, lcl, col_name, None if value is None else str(value)),
        )


def mark_orphans(conn, alive_keys):
    """Marca come orphan le righe (eneltel_key, lcl) NON in `alive_keys`.

    Args:
        alive_keys: set di tuple (eneltel_key, lcl) viste nell'ultimo pull.
    """
    cur = conn.execute("SELECT eneltel_key, COALESCE(lcl,'') AS lcl FROM filone WHERE is_orphan=0")
    locali = {(r['eneltel_key'], r['lcl']) for r in cur.fetchall()}
    nuovi_orfani = locali - alive_keys
    if not nuovi_orfani:
        return 0
    for ek, lcl in nuovi_orfani:
        conn.execute(
            "UPDATE filone SET is_orphan=1 WHERE eneltel_key=? AND COALESCE(lcl,'')=?",
            (ek, lcl),
        )
        audit(conn, eneltel_key=ek, source='remote',
              detail={'kind': 'orphaned', 'lcl': lcl})
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

    # Carica extra in batch, indicizzato per (eneltel_key, lcl)
    extras = {}
    cur = conn.execute("SELECT eneltel_key, COALESCE(lcl,'') AS lcl, column_name, value FROM filone_extra")
    for r in cur.fetchall():
        extras.setdefault((r['eneltel_key'], r['lcl']), {})[r['column_name']] = r['value']

    for r in righe:
        r['extra'] = extras.get((r['eneltel_key'], r.get('lcl') or ''), {})

    return righe


def dump_filone_as_rows(*, include_orphans=False, apply_pending_overlay=True):
    """Ritorna `(headers, rows)` in formato compatibile con la risposta di
    `/api/filone`: stessa shape che il client si aspetta dalla lettura di Sheets.

      headers: list[str] nell'ordine `column_registry.last_index`.
      rows:    list[dict] con chiave `_row` (= `sheets_row`) + un campo per ogni
               header. Ordinate per `sheets_row`.

    Args:
        include_orphans: includi righe `is_orphan=1` (default False).
        apply_pending_overlay: applica `pending_writes` in volo (default True).
            In primary mode il client deve vedere subito le scritture dell'utente
            anche se il drainer non ha ancora pushato su Sheets.

    Usata da `/api/filone` in primary mode per leggere direttamente dal DB
    invece che dal cache Sheets, eliminando ogni finestra di staleness.
    """
    conn = get_conn()

    # 1. Headers nell'ordine del foglio (last_index = posizione 1-based).
    # De-duplicazione: column_registry può contenere alias stale (es. 'POD' +
    # 'Pod' con stesso last_index dopo un rename). Per ogni last_index teniamo
    # SOLO l'entry con last_seen_ts più recente.
    cur = conn.execute('''
        SELECT cr.column_name, cr.chiave_interna, cr.last_index
        FROM column_registry cr
        WHERE cr.last_seen_ts = (
            SELECT MAX(cr2.last_seen_ts)
            FROM column_registry cr2
            WHERE cr2.last_index = cr.last_index
        )
        ORDER BY cr.last_index
    ''')
    registry = cur.fetchall()
    if not registry:
        return ([], [])

    headers = [r['column_name'] for r in registry]
    # Mappa header → chiave_interna (per sapere se leggere da filone o filone_extra)
    header_to_chiave = {r['column_name']: r['chiave_interna'] for r in registry}

    # 2. Tutte le righe filone (escluse orphan se non richieste)
    where = '' if include_orphans else ' WHERE is_orphan=0'
    cur = conn.execute(f'SELECT * FROM filone{where} ORDER BY sheets_row')
    filone_rows = [dict(r) for r in cur.fetchall()]
    if not filone_rows:
        return (headers, [])

    # 3. filone_extra in batch → dict per accesso O(1)
    extras = {}
    cur = conn.execute(
        "SELECT eneltel_key, COALESCE(lcl,'') AS lcl, column_name, value "
        "FROM filone_extra"
    )
    for r in cur.fetchall():
        extras.setdefault((r['eneltel_key'], r['lcl']), {})[r['column_name']] = r['value']

    # 4. pending_writes overlay (in_flight/pending)
    pending_by_key = {}
    if apply_pending_overlay:
        cur = conn.execute(
            "SELECT eneltel_key, COALESCE(lcl,'') AS lcl, column_name, new_value "
            "FROM pending_writes WHERE state IN ('pending','in_flight')"
        )
        for r in cur.fetchall():
            pending_by_key.setdefault((r['eneltel_key'], r['lcl']), {})[r['column_name']] = r['new_value']

    # 5. Materializza ogni riga nell'ordine degli headers
    out_rows = []
    for fr in filone_rows:
        ek = fr['eneltel_key']
        lcl = fr.get('lcl') or ''
        key = (ek, lcl)
        row_extras = extras.get(key, {})
        row_pending = pending_by_key.get(key, {})

        row_dict = {'_row': fr['sheets_row']}
        for header in headers:
            chiave = header_to_chiave.get(header)
            if chiave == 'eneltel':
                val = ek
            elif chiave and chiave in fr:
                val = fr.get(chiave)
            else:
                val = row_extras.get(header)
            # pending overlay (header matching case-insensitive)
            if row_pending:
                # match esatto prima, poi case-insensitive
                if header in row_pending:
                    val = row_pending[header]
                else:
                    h_lower = header.strip().lower()
                    for pcol, pval in row_pending.items():
                        if pcol.strip().lower() == h_lower:
                            val = pval
                            break
            row_dict[header] = '' if val is None else str(val)
        out_rows.append(row_dict)

    return (headers, out_rows)


# ============================================================================
# DIAGNOSTICA
# ============================================================================
def stats():
    """Statistiche rapide sul DB (per /api/admin/db-stats e dump_db.py)."""
    conn = get_conn()
    out = {}
    for tabella in (
        'filone', 'filone_extra', 'cell_comments', 'pending_writes',
        'audit_log', 'column_registry', 'filone_anomalies',
        'lettere_generazioni', 'lettere_righe', 'lettere_contatti',
        'lettere_aziende_generazione', 'lettere_invii',
    ):
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
