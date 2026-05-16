"""
Sistema di cache per ottimizzare i ricalcoli.

Storicamente la cache distanze era un JSON da 30+ MB caricato interamente
in RAM (~130 MB parsed). Ora è uno SQLite con accesso lazy on-demand:
ogni read è una query con index, ogni write è un INSERT OR REPLACE.

Per il caller l'API è la stessa dict-like di prima: `cache[key]`,
`cache.get(key)`, `cache[key] = value`, `key in cache`.

Migrazione dal vecchio JSON: al primo avvio in cui FILE_CACHE_DISTANZE.json
esiste ma il DB no, le voci vengono importate in batch e poi il .json viene
rinominato in .json.migrated.
"""
import hashlib
import json
import os
import sqlite3
import threading

from config import FILE_CACHE_DISTANZE, FILE_CACHE_HASH


# Path del DB SQLite (un singolo file per provincia). Deriva dal path
# storico cambiando .json → .db.
def _db_path():
    base = FILE_CACHE_DISTANZE
    if base.endswith('.json'):
        return base[:-5] + '.db'
    return base + '.db'


def genera_hash_dati(df_tecnico):
    """Genera un hash univoco basato sui dati rilevanti del tecnico."""
    if df_tecnico.empty:
        return "EMPTY"

    colonne_rilevanti = ['Eneltel_Key', 'Lat', 'Lon', 'IS_APPUNTAMENTO',
                         'ORA_APPUNTAMENTO', 'Fascia_Oraria', 'CLI_K']

    colonne_esistenti = [c for c in colonne_rilevanti if c in df_tecnico.columns]

    df_subset = df_tecnico[colonne_esistenti].copy()
    df_subset = df_subset.sort_values('Eneltel_Key')

    data_string = df_subset.to_json(orient='records', default_handler=str)
    hash_obj = hashlib.md5(data_string.encode())

    return hash_obj.hexdigest()


class _DistanzeCacheStore:
    """Backend SQLite condiviso tra le due view (distanze + hash)."""

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS distanze (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS hashes (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.execute('PRAGMA synchronous=NORMAL')
        self.conn.executescript(self._SCHEMA)

    def get_distanza(self, key):
        row = self.conn.execute('SELECT value FROM distanze WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def set_distanza(self, key, value):
        payload = json.dumps(value, ensure_ascii=False)
        with self._lock:
            self.conn.execute(
                'INSERT OR REPLACE INTO distanze(key, value) VALUES (?, ?)',
                (key, payload),
            )

    def get_hash(self, key):
        row = self.conn.execute('SELECT value FROM hashes WHERE key=?', (key,)).fetchone()
        return row[0] if row else None

    def set_hash(self, key, value):
        with self._lock:
            self.conn.execute(
                'INSERT OR REPLACE INTO hashes(key, value) VALUES (?, ?)',
                (key, value),
            )

    def has_distanza(self, key):
        row = self.conn.execute('SELECT 1 FROM distanze WHERE key=?', (key,)).fetchone()
        return row is not None

    def count(self):
        return self.conn.execute('SELECT COUNT(*) FROM distanze').fetchone()[0]


class _DistanzeView:
    """Dict-like view sulla tabella `distanze`."""
    def __init__(self, store): self._store = store
    def get(self, key, default=None):
        v = self._store.get_distanza(key)
        return v if v is not None else default
    def __getitem__(self, key):
        v = self._store.get_distanza(key)
        if v is None:
            raise KeyError(key)
        return v
    def __setitem__(self, key, value): self._store.set_distanza(key, value)
    def __contains__(self, key): return self._store.has_distanza(key)
    def __len__(self): return self._store.count()


class _HashView:
    """Dict-like view sulla tabella `hashes`."""
    def __init__(self, store): self._store = store
    def get(self, key, default=None):
        v = self._store.get_hash(key)
        return v if v is not None else default
    def __getitem__(self, key):
        v = self._store.get_hash(key)
        if v is None:
            raise KeyError(key)
        return v
    def __setitem__(self, key, value): self._store.set_hash(key, value)
    def __contains__(self, key): return self._store.get_hash(key) is not None


_store_singleton = None
_store_lock = threading.Lock()


def _get_store():
    global _store_singleton
    if _store_singleton is None:
        with _store_lock:
            if _store_singleton is None:
                path = _db_path()
                _migrate_json_if_present(path)
                _store_singleton = _DistanzeCacheStore(path)
    return _store_singleton


def _migrate_json_if_present(db_path):
    """Migrazione one-shot dal vecchio JSON al nuovo SQLite.

    Eseguita solo se il DB non esiste ancora e il JSON sì. Dopo la migrazione
    il JSON viene rinominato in .migrated per non ricaricarlo.
    """
    if os.path.exists(db_path):
        return  # DB già presente, nessuna migrazione
    if not os.path.exists(FILE_CACHE_DISTANZE):
        return  # nessun JSON da migrare (clone fresco)

    print(f"📦 Migrazione cache distanze: {FILE_CACHE_DISTANZE} → SQLite")
    try:
        with open(FILE_CACHE_DISTANZE, 'r', encoding='utf-8') as f:
            distanze = json.load(f)
        hashes = {}
        if os.path.exists(FILE_CACHE_HASH):
            with open(FILE_CACHE_HASH, 'r', encoding='utf-8') as f:
                hashes = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"⚠️  Errore lettura cache JSON ({e}), inizio con DB vuoto")
        return

    # Scrittura batch
    os.makedirs(os.path.dirname(db_path) or '.', exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.executescript(_DistanzeCacheStore._SCHEMA)
    conn.execute('BEGIN')
    for k, v in distanze.items():
        conn.execute(
            'INSERT OR REPLACE INTO distanze(key, value) VALUES (?, ?)',
            (k, json.dumps(v, ensure_ascii=False)),
        )
    for k, v in hashes.items():
        conn.execute(
            'INSERT OR REPLACE INTO hashes(key, value) VALUES (?, ?)',
            (k, v),
        )
    conn.execute('COMMIT')
    conn.close()
    print(f"   ✅ Migrate {len(distanze)} voci distanze + {len(hashes)} hash")

    # Rinomina i JSON vecchi per non ricaricarli (mantiene backup utile)
    for p in (FILE_CACHE_DISTANZE, FILE_CACHE_HASH):
        if os.path.exists(p):
            try:
                os.rename(p, p + '.migrated')
            except OSError:
                pass


def carica_cache():
    """Carica cache distanze e hash (lazy via SQLite, niente full-load in RAM)."""
    store = _get_store()
    if store.count() > 0:
        print(f"📂 Cache distanze pronta ({store.count()} voci, lazy SQLite)")
    return _DistanzeView(store), _HashView(store)


def salva_cache(cache_distanze, cache_hash):
    """No-op: le scritture sono già persistite riga-per-riga via SQLite."""
    pass


def pulisci_cache_vecchia(cache_distanze, cache_hash, giorni_da_mantenere=None):
    """No-op: la cache è permanente (compat per chiamate esistenti)."""
    return cache_distanze, cache_hash
