# -*- coding: utf-8 -*-
"""
Stato condiviso e helper per l'accesso al filone.

Post-disconnessione Google Sheets (2026-05-21): la fonte di verità è il DB
locale SQLite. Le funzioni storicamente legate a gspread (get_worksheet,
get_headers, force_refresh_headers, ecc.) sono state riconvertite per
leggere dalla tabella `filone` / `column_registry`. L'API esposta resta
invariata così le route Blueprint non hanno bisogno di cambiare.
"""
import re
import time
import logging
import threading
import traceback as _traceback_mod

from flask import session, jsonify

from config import PROVINCIA_ATTIVA

logger = logging.getLogger('mappa_algeco')

# ============================================================================
# HEADER REGISTRY — cache locale degli header colonna
# ============================================================================
# Mantenuto in memoria per compatibilità API: le route leggono `_header_map`
# come dict {nome_colonna: indice_1based}. Il backing store è
# `column_registry` nel DB SQLite, popolato dall'importer Excel.
_headers = None
_header_map = None        # {col_name: 1-based index}
_worksheet_initialized = False
_headers_ts = 0
_HEADERS_TTL = 60         # secondi: refresh periodico (le colonne cambiano via import)
_sheets_lock = threading.Lock()
_gsheets_write_lock = threading.RLock()  # Serializza operazioni di scrittura (rientrante)

# ============================================================================
# CACHE VALORI FILONE
# ============================================================================
_cached_all_values = None
_cached_all_values_ts = 0
_values_cache_lock = threading.Lock()
_VALUES_CACHE_TTL = 30


def _sheets_retry(func, *args, max_retries=3, **kwargs):
    """Passthrough storico: ora chiama solo `func(*args, **kwargs)`.

    Mantenuto perché tante route lo importano. Senza Google Sheets, niente
    rate-limit / 5xx da gestire, quindi nessuna logica di retry. Lasciato
    come funzione invece che alias così se serve ri-aggiungere logica di
    retry attorno a operazioni DB lente, c'è già il punto.
    """
    return func(*args, **kwargs)


def invalidate_values_cache():
    """Invalida la cache dei valori del foglio (dopo scritture).

      - Cache shared INVALIDATA → il prossimo `get_all_values_cached`
        scaricherà Sheets fresh (dove il drainer ha appena pushato).
      - `_dl_cached_df` NON invalidato → evita falso positivo "primo sync"
        in `_rileva_cambiamento_strutturale` → resta light sync.
      - `_dl_cached_raw_hash` azzerato → forza il sync a riprocessare il
        DataFrame e rigenerare `mappa_data.json` anche se i raw values
        risultassero "uguali" al precedente snapshot.
    """
    global _cached_all_values, _cached_all_values_ts
    with _values_cache_lock:
        _cached_all_values = None
        _cached_all_values_ts = 0
    try:
        from .data_loader import mark_data_changed
        mark_data_changed()
    except ImportError:
        pass


def get_all_values_cached(force=False):
    """Ritorna tutti i valori del filone come lista di liste (headers + righe).

    Post-disconnessione Sheets (2026-05-21): legge SOLO dal DB locale
    tramite `db.dump_filone_as_rows`. Mantiene la cache breve in memoria
    per non rifare il dump a ogni chiamata.

    Args:
        force: Se True, invalida la cache e rilegge subito dal DB.
    """
    global _cached_all_values, _cached_all_values_ts
    now = time.time()

    if not force:
        with _values_cache_lock:
            if _cached_all_values is not None and (now - _cached_all_values_ts) < _VALUES_CACHE_TTL:
                logger.debug("[Cache] Filone servito da cache in memoria")
                return _cached_all_values

    from . import db as _db
    headers, rows = _db.dump_filone_as_rows(
        include_orphans=False,
        apply_pending_overlay=True,
    )
    values = [list(headers)] + [[r.get(h, '') for h in headers] for r in rows]

    with _values_cache_lock:
        _cached_all_values = values
        _cached_all_values_ts = time.time()

    return values


def _load_headers_from_db():
    """Carica gli header dal `column_registry` del DB SQLite.

    De-duplica per `last_index` mantenendo l'entry con `last_seen_ts` più
    recente (può capitare di avere alias stale dopo rename di colonne).
    Ritorna (headers_list, header_map_dict).
    """
    from . import db as _db
    conn = _db.get_conn()
    cur = conn.execute('''
        SELECT cr.column_name, cr.last_index
        FROM column_registry cr
        WHERE cr.last_seen_ts = (
            SELECT MAX(cr2.last_seen_ts)
            FROM column_registry cr2
            WHERE cr2.last_index = cr.last_index
        )
        ORDER BY cr.last_index
    ''')
    rows = cur.fetchall()
    headers = [r['column_name'] for r in rows]
    header_map = {r['column_name']: r['last_index'] for r in rows}
    return headers, header_map


def _ensure_headers_loaded():
    """Inizializza/refresha la cache header dal DB."""
    global _headers, _header_map, _headers_ts, _worksheet_initialized
    now = time.time()
    if _worksheet_initialized and (now - _headers_ts) < _HEADERS_TTL:
        return
    with _sheets_lock:
        try:
            headers, header_map = _load_headers_from_db()
        except Exception as e:
            logger.warning(f"[Headers] Caricamento dal DB fallito: {e}")
            return
        _headers = headers
        _header_map = header_map
        _headers_ts = now
        _worksheet_initialized = True


class _DBWorksheetProxy:
    """Imita la parte read-only di `gspread.Worksheet`, leggendo dal DB.

    Le route che storicamente facevano `ws = get_worksheet()` e poi
    `ws.row_values(N)` / `ws.col_values(N)` / `ws.acell('B5')` continuano
    a funzionare senza modifiche. Le scritture dirette su `ws.*` (update_acell,
    format, batch_update, append_rows, clear, resize) sollevano
    NotImplementedError esplicito: chi voleva scrivere DEVE passare da
    safe_batch_update_by_name() che instrada al DB.

    Attributi `id`, `spreadsheet_id`, `client` sono stub.
    """

    @property
    def id(self):
        return 0

    @property
    def spreadsheet_id(self):
        return ''

    @property
    def client(self):
        raise NotImplementedError(
            "ws.client.batch_update() non è più supportato (Sheets disconnesso). "
            "Le formattazioni/note cella vivono solo lato UI (cell_log.json)."
        )

    def _all_rows(self):
        """Cache rows + headers in un'unica chiamata (TTL via get_all_values_cached)."""
        return get_all_values_cached()

    def row_values(self, row_num):
        """Ritorna i valori della riga 1-based. row_num=1 → headers."""
        rows = self._all_rows()
        if row_num < 1 or row_num > len(rows):
            return []
        return list(rows[row_num - 1])

    def col_values(self, col_idx):
        """Ritorna i valori della colonna 1-based, inclusa la cella header."""
        rows = self._all_rows()
        out = []
        for r in rows:
            if col_idx - 1 < len(r):
                out.append(r[col_idx - 1])
            else:
                out.append('')
        return out

    def get_all_values(self):
        return self._all_rows()

    def acell(self, cell_ref):
        """Singola cella in notazione A1 ('B5'). Ritorna proxy con .value."""
        import re as _re
        m = _re.match(r'^([A-Z]+)(\d+)$', cell_ref.strip().upper())
        if not m:
            raise ValueError(f"Cell ref non valido: {cell_ref}")
        col_letters = m.group(1)
        row_num = int(m.group(2))
        col_idx = 0
        for ch in col_letters:
            col_idx = col_idx * 26 + (ord(ch) - ord('A') + 1)
        rows = self._all_rows()
        if row_num < 1 or row_num > len(rows):
            value = ''
        else:
            r = rows[row_num - 1]
            value = r[col_idx - 1] if col_idx - 1 < len(r) else ''
        return type('Cell', (), {'value': value})()

    # ── Scritture dirette: NON supportate ────────────────────────────────
    def _write_disabled(self, method_name, *_, **__):
        raise NotImplementedError(
            f"ws.{method_name}() non è più supportato (Sheets disconnesso). "
            "Usa safe_batch_update_by_name() che instrada al DB locale."
        )

    def update_acell(self, *a, **k):    return self._write_disabled('update_acell')
    def update_cell(self, *a, **k):     return self._write_disabled('update_cell')
    def update(self, *a, **k):          return self._write_disabled('update')
    def batch_update(self, *a, **k):    return self._write_disabled('batch_update')
    def format(self, *a, **k):          return self._write_disabled('format')
    def append_rows(self, *a, **k):     return self._write_disabled('append_rows')
    def clear(self, *a, **k):           return self._write_disabled('clear')
    def resize(self, *a, **k):          return self._write_disabled('resize')


_ws_proxy_singleton = None


def get_worksheet(force_refresh=False):
    """Ritorna il proxy DB. La firma resta compatibile con la chiamata storica.

    Lettura cella/riga/colonna funzionano (leggono dal DB). Le scritture
    sollevano NotImplementedError: chi voleva scrivere deve usare
    safe_batch_update_by_name().
    """
    global _ws_proxy_singleton
    _ensure_headers_loaded()
    if _ws_proxy_singleton is None:
        _ws_proxy_singleton = _DBWorksheetProxy()
    return _ws_proxy_singleton


def get_spreadsheet():
    raise NotImplementedError("Google Sheets disconnesso (2026-05-21).")


def get_sheets_client():
    raise NotImplementedError("Google Sheets disconnesso (2026-05-21).")


def get_header_map():
    """Mappa header → indice 1-based, dal DB locale."""
    _ensure_headers_loaded()
    return _header_map or {}


def get_headers():
    """Lista degli header del filone, dal DB locale."""
    _ensure_headers_loaded()
    return list(_headers or [])


def force_refresh_headers():
    """No-op semantico: forza il reload dal DB (TTL ignorato).

    Storicamente rileggeva la row 1 del foglio per intercettare le
    modifiche fatte dai colleghi. Ora le colonne entrano via import Excel,
    che aggiorna direttamente `column_registry`.
    """
    global _headers_ts
    _headers_ts = 0
    _ensure_headers_loaded()
    return _header_map or {}


# ============================================================================
# SAFE BATCH UPDATE — l'API canonica per OGNI scrittura sul filone
# ============================================================================
#
# Tutti i path di scrittura DEVONO passare da safe_batch_update_by_name (o
# safe_update_cell). Questa è l'unica protezione robusta contro lo
# "spanamento" del filone, perché:
#
#   1. Acquisisce _gsheets_write_lock (serializza tutte le scritture nel
#      processo Flask, evitando interleaving).
#   2. RILEGGE row 1 dentro al lock (force_refresh_headers): l'indice di
#      ogni colonna è quello CORRENTE sul foglio, non quello cached.
#   3. (Opzionale) verifica via "ancora" POD/Eneltel che la riga su cui sta
#      per scrivere contenga davvero il record atteso. Se la riga è stata
#      spostata (sort/inserimento/cancellazione), la rimappa al volo.
#   4. Skip-with-log delle scritture verso colonne che non esistono più
#      (es. la collega ha rinominato/cancellato la colonna): meglio non
#      scrivere che scrivere nella colonna sbagliata.
#   5. Esegue le scritture in chunk via batch_update con retry.
# ============================================================================

def _resolve_anchor_col_name(anchor_type='pod'):
    """Ritorna il primo nome colonna ancora disponibile in `_header_map`."""
    candidates = {
        'pod': ['POD', 'Pod', 'pod', 'Codice POD'],
        'eneltel': ['Eneltel', 'ENELTEL', 'eneltel'],
    }.get(anchor_type, [])
    if not _header_map:
        return None
    for name in candidates:
        if name in _header_map:
            return name
    return None


def safe_batch_update_by_name(items, *, anchor_col=None, anchor_normalize='upper',
                              _force_sheets=False):
    """Esegue un batch_update VERIFICANDO atomicamente la mappa colonne e
    (opzionalmente) l'identità di ogni riga via "ancora" POD/Eneltel.

    Note:
        _force_sheets=True salta il branch primary (Fase 2) e scrive
        direttamente su Sheets. È riservato al drainer del DB locale, che
        DEVE pushare le pending su Sheets — se passasse dal branch primary
        creerebbe un loop infinito (DB → drain → DB).

    Args:
        items: lista di dict, ciascuno con:
            - 'row': int riga 1-based sul foglio
            - 'col': str nome colonna (case-sensitive — deve corrispondere
              esattamente all'header attuale del foglio)
            - 'value': valore da scrivere (verrà convertito in str se non lo è)
            - 'anchor' (opzionale): valore atteso nella colonna `anchor_col`
              alla riga `row`. Se diverso, la riga viene re-cercata; se non
              trovata, la cella viene saltata (NON sovrascritta).
        anchor_col: str | None — nome colonna ancora ('POD' o 'Eneltel').
            Se fornita E almeno un item ha `anchor`, viene letta la colonna
            fresca dal foglio per verificare le righe.
        anchor_normalize: 'upper' (POD-like) o 'lstrip0' (Eneltel-like).

    Returns:
        dict con:
            'success': bool — True se nessuna colonna mancante e nessuna
                ancora persa
            'written': int — celle effettivamente scritte
            'skipped': int — celle saltate (col mancante o ancora persa)
            'remapped': int — righe rimappate (sheet riordinato)
            'invalid_cols': list[str] — colonne richieste ma non presenti
                nei header freschi (i loro write sono stati saltati)
            'lost_anchors': list[dict] — ancore non più nel foglio
    """
    if not items:
        return {
            'success': True, 'written': 0, 'skipped': 0, 'remapped': 0,
            'invalid_cols': [], 'lost_anchors': [],
        }

    # ────────────────────────────────────────────────────────────────────────
    # POST-DISCONNESSIONE SHEETS (2026-05-21):
    # ogni scrittura va SOLO al DB locale. Il parametro `_force_sheets`,
    # storicamente usato dal drainer per saltare il branch DB e scrivere
    # direttamente su Sheets, è ignorato: il drainer non esiste più.
    # Le route continuano a chiamare questa funzione invariate.
    # ────────────────────────────────────────────────────────────────────────
    return _route_to_db_primary(items, anchor_col=anchor_col,
                                anchor_normalize=anchor_normalize)


def _route_to_db_primary(items, *, anchor_col=None, anchor_normalize='upper'):
    """Branch FASE 2 di safe_batch_update_by_name.

    Scrive solo nel DB locale. Risposta in ~10-50ms; il drainer asincrono
    pusherà su Sheets entro pochi secondi.

    DEDUP: il client invia spesso TUTTO il form (es. 9 campi) anche se
    l'utente ne ha modificato 1 solo. Filtriamo qui le scritture cui valore
    coincide con lo stato corrente (filone + pending in volo): evita scrivere
    9 celle quando ne basta 1, riduce il carico sul drainer e impedisce
    false detection di "cambio strutturale" lato data_loader.

    Mantiene la stessa interfaccia di ritorno di safe_batch_update_by_name
    così le route NON devono cambiare nulla. Le invalid_cols e lost_anchors
    vengono rilevate al drain (non qui), e i pending corrispondenti finiranno
    in stato 'quarantined' visibile via /api/admin/db-pending.
    """
    from . import db as _db

    n_written = 0
    n_skipped = 0
    n_unchanged = 0  # dedup: stessi valori, niente da scrivere
    # Mappa (eneltel_key, lcl) -> {col: value}. LCL distingue righe con
    # stesso Eneltel ma LCL diverso (capita con multi-LCL su Pavia).
    by_row = {}
    unresolved = 0

    anchor_col_low = (anchor_col or '').lower()

    # Mappa (eneltel_key, lcl) → stato corrente. Lazy.
    state_cache = {}

    def _current_value(ek, lcl, col_name):
        key = (ek, lcl or '')
        if key not in state_cache:
            state_cache[key] = _build_current_state(ek, lcl or '')
        return state_cache[key].get(col_name, '')

    def _build_current_state(ek, lcl):
        """Carica filone + filone_extra + pending in volo per (eneltel, lcl)."""
        out = {}
        conn = _db.get_conn()
        local = conn.execute(
            'SELECT * FROM filone WHERE eneltel_key=? AND COALESCE(lcl,\"\")=?',
            (ek, lcl),
        ).fetchone()
        if local:
            for cr in conn.execute(
                'SELECT column_name, chiave_interna FROM column_registry'
            ).fetchall():
                chiave = cr['chiave_interna']
                if chiave and chiave in _db.COLONNE_NOTE:
                    out[cr['column_name']] = local[chiave] if chiave in local.keys() else None
        for r in conn.execute(
            'SELECT column_name, value FROM filone_extra '
            'WHERE eneltel_key=? AND COALESCE(lcl,\"\")=?',
            (ek, lcl),
        ).fetchall():
            out[r['column_name']] = r['value']
        for r in conn.execute(
            "SELECT column_name, new_value FROM pending_writes "
            "WHERE eneltel_key=? AND COALESCE(lcl,'')=? AND state IN ('pending','in_flight')",
            (ek, lcl),
        ).fetchall():
            out[r['column_name']] = r['new_value']
        return out

    for it in items:
        row = it.get('row')
        col = it.get('col')
        value = it.get('value')
        if not col or row is None or row < 2:
            n_skipped += 1
            continue
        anchor_val = it.get('anchor')
        # Per i duplicate_eneltel su Pavia (riga con stesso ENELTEL ma LCL
        # diverso) il client passa anche `anchor_lcl` come secondary anchor.
        # Se assente, il DB ritorna la "prima" riga associata all'eneltel.
        anchor_lcl = it.get('anchor_lcl') or ''

        # Risolvi (eneltel_key, lcl) — la chiave composta del DB
        ek = None
        resolved_lcl = anchor_lcl
        if anchor_col_low == 'eneltel' and anchor_val:
            ek = _db.normalize_eneltel(anchor_val)
            # Risolvi anche LCL dal DB: se anchor_lcl vuoto (caso comune,
            # client non lo manda), usa il LCL della prima riga di quell'eneltel.
            # Altrimenti match esatto su (ek, lcl) e fallback alla prima.
            if not anchor_lcl:
                local = _db.get_filone_row(ek)
                if local:
                    resolved_lcl = local.get('lcl') or ''
            else:
                local = _db.get_filone_row(ek, lcl=anchor_lcl)
                if local is None:
                    local = _db.get_filone_row(ek)  # fallback prima riga
                resolved_lcl = (local.get('lcl') or '') if local else anchor_lcl
        elif anchor_col_low == 'pod' and anchor_val:
            tup = _db.find_filone_row_by_pod(anchor_val, lcl=anchor_lcl)
            if tup:
                ek, resolved_lcl = tup
        else:
            tup = _db.find_filone_row_by_sheets_row(row)
            if tup:
                ek, resolved_lcl = tup

        if not ek:
            unresolved += 1
            n_skipped += 1
            continue

        # DEDUP: confronta col valore corrente "visibile" per (eneltel, lcl).
        col_canonical = col
        try:
            col_canonical = _db.canonical_column_name(_db.get_conn(), col)
        except Exception:
            pass
        new_str = '' if value is None else str(value)
        cur_val = _current_value(ek, resolved_lcl, col_canonical)
        cur_str = '' if cur_val is None else str(cur_val)
        if new_str == cur_str:
            n_unchanged += 1
            continue

        by_row.setdefault((ek, resolved_lcl), {})[col] = value
        n_written += 1

    # Una record_local_change per riga (state='pending', drainer pusherà).
    # Passiamo lcl per identificare la riga specifica (per i duplicate_eneltel).
    user = _safe_username()
    for (ek, lcl), fields in by_row.items():
        try:
            _db.record_local_change(
                ek, fields, user=user, origin='primary_route',
                initial_state='pending', lcl=lcl,
            )
        except Exception as e:
            logger.warning(f"[primary] record_local_change fallito per {ek}/{lcl!r}: {e}")
            n_written -= len(fields)
            n_skipped += len(fields)

    # Risveglia il drainer per scrivere su Sheets ~ASAP
    try:
        from . import sync_engine as _se
        _se.trigger_drain_now()
    except Exception:
        pass

    # Forza il prossimo sync_loop a riprocessare il DataFrame e rigenerare
    # mappa_data.json. Necessario perché c'è una race fra drainer e sync_loop:
    # se il drainer drena prima del sync, le pending sono già 'done' e il
    # raw_hash dei values fresh può coincidere con quello del sync precedente
    # (entrambi con valore 'modificato'), causando uno skip e mappa_data.json
    # rimasto al snapshot precedente.
    if n_written > 0:
        try:
            from . import data_loader as _dl
            _dl.mark_data_changed()
        except Exception:
            pass

    if unresolved:
        logger.warning(
            f"[primary] {unresolved} item(s) con eneltel non risolto "
            f"(anchor_col={anchor_col}). Saltati."
        )
    if n_unchanged:
        logger.info(
            f"[primary] dedup: {n_unchanged} item(s) già al valore "
            f"corrente, no pending creata"
        )

    return {
        'success': unresolved == 0,
        'written': n_written,
        'skipped': n_skipped,
        'unchanged': n_unchanged,
        'remapped': 0,  # remapping avviene al drain
        'invalid_cols': [],
        'lost_anchors': [],
        'primary_routed': True,
    }


def safe_update_cell(row, col_name, value, *, anchor_col=None, anchor_value=None):
    """Versione "single-cell" di safe_batch_update_by_name.

    Args:
        row: int riga 1-based
        col_name: str nome colonna (deve esistere nei header attuali del foglio)
        value: valore da scrivere
        anchor_col: opzionale, nome colonna ancora ('POD' o 'Eneltel')
        anchor_value: opzionale, valore atteso nella colonna ancora alla riga row

    Returns:
        (success: bool, message: str)
    """
    item = {'row': row, 'col': col_name, 'value': value}
    if anchor_col and anchor_value:
        item['anchor'] = anchor_value
    norm = 'lstrip0' if (anchor_col or '').lower() in ('eneltel',) else 'upper'
    res = safe_batch_update_by_name(
        [item], anchor_col=anchor_col, anchor_normalize=norm,
    )
    if res.get('error'):
        return False, res['error']
    if not res['success']:
        if res['invalid_cols']:
            return False, f"Colonna '{col_name}' non trovata sul foglio"
        if res['lost_anchors']:
            return False, f"Riga non più trovata via ancora {anchor_col}={anchor_value}"
        return False, "Scrittura non completata"
    return True, "OK"


# ============================================================================
# HELPER COLONNE
# ============================================================================

def _trova_indice_colonna(headers, nome):
    """Trova l'indice (0-based) di una colonna per nome (case-insensitive)."""
    for i, h in enumerate(headers):
        if h.strip().upper() == nome.upper():
            return i
    return None


def _trova_indice_multi(headers, *nomi, chiave_mapping=None):
    """Cerca tra più nomi colonna, restituisce il primo indice trovato (0-based).
    Se chiave_mapping è specificata, controlla prima la mappatura personalizzata."""
    from config import MAPPATURA_COLONNE
    if chiave_mapping and MAPPATURA_COLONNE.get(chiave_mapping):
        nome_custom = MAPPATURA_COLONNE[chiave_mapping]
        r = _trova_indice_colonna(headers, nome_custom)
        if r is not None:
            return r
    for nome in nomi:
        r = _trova_indice_colonna(headers, nome)
        if r is not None:
            return r
    return None


def _get_colonna_note():
    """Restituisce il nome della colonna note, cercando tra i nomi possibili."""
    if not _worksheet_initialized:
        get_worksheet()
    from config import MAPPATURA_COLONNE
    # Se c'è una mappatura personalizzata, usala
    nome_custom = MAPPATURA_COLONNE.get('note_appuntamento')
    if nome_custom and nome_custom in _header_map:
        return nome_custom
    # Cerca tra i nomi noti
    for nome in ['NOTE APP', 'NOTE_APP', 'NOTE APPUNTAMENTO', 'NOTE', 'Note']:
        if nome in _header_map:
            return nome
    return 'NOTE APP'  # fallback


def _get_username():
    """Estrai username dalla sessione corrente."""
    user = session.get('user')
    if isinstance(user, dict):
        return user.get('username', 'sconosciuto')
    return user or 'sconosciuto'


def _safe_username():
    """Variante che non lancia eccezioni se chiamata fuori dal contesto Flask
    (es. dai worker daemon, dai test, dai tools CLI)."""
    try:
        return _get_username()
    except Exception:
        return None


# ============================================================================
# RICERCA RIGHE
# ============================================================================

def _get_col_index(col_type='pod', *, fresh=False):
    """Restituisce l'indice 1-based della colonna POD o ENELTEL.

    Args:
        fresh: se True, ricarica gli header dal foglio prima di restituire
            l'indice. DA USARE in tutti i path di scrittura (e nelle ricerche
            riga subito prima di una scrittura) per evitare di leggere/scrivere
            la colonna sbagliata se la collega ha modificato il foglio.
    """
    if fresh:
        force_refresh_headers()
    if not _header_map:
        return None
    if col_type == 'pod':
        for name in ['POD', 'Pod', 'pod', 'Codice POD']:
            if name in _header_map:
                return _header_map[name]
    else:
        for name in ['ENELTEL', 'Eneltel', 'eneltel']:
            if name in _header_map:
                return _header_map[name]
    return None


def build_pod_row_map(*, fresh=True):
    """Costruisce una mappa fresca POD→riga (1-based) leggendo dal foglio.

    Args:
        fresh: se True (default), ricarica gli header per essere certi che
            l'indice della colonna POD sia quello attuale sul foglio. È
            VITALE per le scritture: se la collega ha aggiunto colonne, il
            POD potrebbe essere su un indice diverso da quello cached.

    Utile per batch update: costruisci la mappa subito prima di scrivere
    per evitare problemi di righe spostate.
    """
    if fresh:
        force_refresh_headers()
    ws = get_worksheet()
    col = _get_col_index('pod')
    if col is None:
        return {}
    values = _sheets_retry(ws.col_values, col)
    result = {}
    for i, val in enumerate(values[1:], start=2):
        v = str(val).strip().upper()
        if v:
            result[v] = i
    return result


def verify_row_pod(row_num, expected_pod):
    """Verifica che alla riga row_num ci sia il POD atteso.

    Legge la cella POD dalla riga indicata e confronta. La colonna POD viene
    risolta con header FRESCHI (così la verifica è valida anche se la collega
    ha appena modificato la struttura del foglio).
    """
    ws = get_worksheet()
    col = _get_col_index('pod', fresh=True)
    if col is None:
        return False
    actual = str(_sheets_retry(ws.cell, row_num, col).value or '').strip().upper()
    return actual == str(expected_pod).strip().upper()


def verify_row_eneltel(row_num, expected_eneltel):
    """Verifica che alla riga row_num ci sia l'ENELTEL atteso (con header freschi)."""
    ws = get_worksheet()
    col = _get_col_index('eneltel', fresh=True)
    if col is None:
        return False
    actual = str(_sheets_retry(ws.cell, row_num, col).value or '').lstrip('0')
    return actual == str(expected_eneltel).lstrip('0')


def find_row_by_eneltel(eneltel):
    """Trova la riga corrispondente a un ENELTEL (1-based) o None.

    Legge da DB locale: zero chiamate Sheets.
    """
    from . import db as _db
    local = _db.get_filone_row(eneltel)
    return local.get('sheets_row') if local else None


def find_row_by_pod(pod):
    """Trova la riga corrispondente a un POD (1-based) o None.

    Legge da DB locale: zero chiamate Sheets.
    """
    from . import db as _db
    ek = _db.find_eneltel_by_pod(pod)
    if not ek:
        return None
    local = _db.get_filone_row(ek)
    return local.get('sheets_row') if local else None


# ============================================================================
# AGGIORNAMENTO CELLE
# ============================================================================

def update_cell(row, col_name, value):
    """Aggiorna una singola cella (thread-safe, con header refresh atomico).

    Usa internamente safe_batch_update_by_name per garantire che la colonna
    target sia quella attuale sul foglio (no staleness).
    """
    return safe_update_cell(row, col_name, value)


def update_row_fields(eneltel, fields, *, lcl=None):
    """Aggiorna più campi di una riga identificata da ENELTEL (+ LCL opzionale).

    `lcl` è l'ancora secondaria che disambigua le righe duplicate (Pavia
    multi-LCL). Se None, scrive sulla "prima riga" dell'eneltel (legacy).
    """
    logger.info(f"🔧 update_row_fields: ENELTEL={eneltel}, lcl={lcl!r}, campi={fields}")

    row = find_row_by_eneltel(eneltel)
    if row is None:
        return False, f"ENELTEL {eneltel} non trovato"

    logger.info(f"   Trovata riga: {row}")

    eneltel_col_name = _resolve_anchor_col_name('eneltel') or 'Eneltel'
    items = []
    for col_name, value in fields.items():
        item = {
            'row': row,
            'col': col_name,
            'value': value,
            'anchor': str(eneltel),
        }
        if lcl is not None:
            item['anchor_lcl'] = str(lcl)
        items.append(item)

    if not items:
        return True, "OK"

    res = safe_batch_update_by_name(
        items, anchor_col=eneltel_col_name, anchor_normalize='lstrip0',
    )
    logger.info(
        f"   📝 safe write: written={res['written']} skipped={res['skipped']} "
        f"remapped={res['remapped']} invalid_cols={res['invalid_cols']}"
    )

    if res.get('error'):
        return False, res['error']

    warnings = []
    if res['invalid_cols']:
        warnings.append(f"colonne non trovate: {res['invalid_cols']}")
    if res['lost_anchors']:
        warnings.append(f"ENELTEL non più presente: {len(res['lost_anchors'])} celle")

    if warnings:
        return True, "Aggiornato con warning: " + "; ".join(warnings)
    return True, "OK"


# ============================================================================
# APPUNTAMENTI (logica condivisa)
# ============================================================================
# Nota: _imposta_appuntamento_su_filone e _rimuovi_appuntamento_da_filone
# dipendono da trigger_sync_after_edit e funzioni calendario che vengono
# registrate dopo l'import. Usiamo late-binding via _callbacks.

_callbacks = {
    'trigger_sync_after_edit': lambda: None,
    'sse_notify_all': lambda *a, **kw: None,
    'crea_promemoria_boxbeat': lambda **kw: None,
    'registra_appuntamento_excel': lambda **kw: None,
    'extract_contatore_data': lambda row: {},
}


def register_callbacks(**kw):
    """Registra le callback effettive (chiamato da server.py dopo import)."""
    _callbacks.update(kw)


def _resolve_eneltel_for_appuntamento(row, *, pod_override=None, eneltel_override=None):
    """Risolve l'eneltel_key per una riga (usato dalle helper primary)."""
    from . import db as _db
    if eneltel_override:
        return _db.normalize_eneltel(eneltel_override)
    try:
        cd = _callbacks['extract_contatore_data'](row)
        ek = cd.get('eneltel') or cd.get('Eneltel') or ''
        if ek:
            return _db.normalize_eneltel(ek)
    except Exception:
        pass
    if pod_override:
        ek = _db.find_eneltel_by_pod(pod_override)
        if ek:
            return ek
    return _db.find_eneltel_by_sheets_row(row)


def _calcola_nuova_nota_app(nota_attuale, orario_app):
    """Inserisce 'APP HH:MM' nella nota, rimuovendo eventuali APP precedenti.

    Logica identica a quella di _imposta_appuntamento_su_filone (path Fase 1).
    """
    nota_app = f"APP {orario_app}"
    if 'APP' in (nota_attuale or '').upper():
        nota_attuale = re.sub(
            r'APP\s*\d{1,2}[:.]\d{2}(?:\s*[-/]\s*\d{1,2}[:.]\d{2})?',
            '', nota_attuale, flags=re.IGNORECASE
        ).strip(' /|-')
    return f"{nota_app} // {nota_attuale}" if nota_attuale else nota_app


def _calcola_rimozione_app(nota_attuale):
    """Rimuove 'APP HH:MM' dalla nota. Logica identica al path Fase 1."""
    nuova = re.sub(
        r'APP\s*\d{1,2}[:.]\d{2}(?:\s*[-/]\s*\d{1,2}[:.]\d{2})?',
        '', nota_attuale or '', flags=re.IGNORECASE,
    )
    nuova = re.sub(r'\s*//\s*', ' // ', nuova).strip(' /|-')
    nuova = re.sub(r'^//\s*|\s*//\s*$', '', nuova).strip()
    return nuova


def _imposta_appuntamento_db_primary(row, orario, nota_app, *, data_app='',
                                     note_extra='', origine='contatore',
                                     pod_override=None, eneltel_override=None,
                                     nota_corrente=None, lcl=None):
    """Variante FASE 2 di _imposta_appuntamento_su_filone — solo DB."""
    from . import db as _db

    ek = _resolve_eneltel_for_appuntamento(
        row, pod_override=pod_override, eneltel_override=eneltel_override,
    )
    if not ek:
        raise ValueError(f'Eneltel non risolto per riga {row}')

    # Nota corrente: priorità a quella passata, altrimenti dal DB.
    if nota_corrente is None:
        local = _db.get_filone_row(ek) or {}
        nota_corrente = local.get('note_appuntamento') or ''

    # Risolvi nome colonna note dal column_registry / fallback
    col_note = _get_colonna_note()  # questa accede a _header_map; in Fase 2
    # potremmo voler bypassare get_worksheet, ma per ora lo lasciamo: header è
    # cached e già affidabile.

    nuova_nota = _calcola_nuova_nota_app(nota_corrente, orario)

    _db.record_local_change(
        ek, {col_note: nuova_nota},
        user=_safe_username(),
        origin=f'imposta_app_primary_{origine}',
        initial_state='pending',
        lcl=lcl,
    )

    # Risveglia drainer
    try:
        from . import sync_engine as _se
        _se.trigger_drain_now()
    except Exception:
        pass

    # Auto-crea promemoria boxbeat (riusa logica esistente)
    try:
        contatore_data = _callbacks['extract_contatore_data'](row)
        username = _safe_username() or 'sconosciuto'
        from datetime import datetime
        data_bb = data_app if data_app else (
            contatore_data.get('data_pianificata', '')
            or datetime.now().strftime('%Y-%m-%d')
        )
        pod_val = pod_override or contatore_data.get('pod', '')
        eneltel_val = eneltel_override or ek

        _callbacks['crea_promemoria_boxbeat'](
            data_appuntamento=data_bb, ora_appuntamento=orario,
            pod=pod_val, eneltel=eneltel_val,
            nominativo=contatore_data.get('nominativo', ''),
            via=contatore_data.get('via', ''),
            civico=contatore_data.get('civico', ''),
            comune=contatore_data.get('comune', ''),
            note=note_extra,
            provincia=PROVINCIA_ATTIVA,
            creato_da=username, origine=origine,
            data_pianificata=contatore_data.get('data_pianificata', ''),
        )
        _callbacks['registra_appuntamento_excel'](
            data_appuntamento=data_bb, orario=orario,
            pod=pod_val, eneltel=eneltel_val,
            nominativo=contatore_data.get('nominativo', ''),
            via=contatore_data.get('via', ''),
            civico=contatore_data.get('civico', ''),
            comune=contatore_data.get('comune', ''),
            note=note_extra,
            origine=origine,
            data_pianificata=contatore_data.get('data_pianificata', ''),
            creato_da=username, provincia=PROVINCIA_ATTIVA,
        )
        _callbacks['sse_notify_all']('calendario_update', {'azione': 'boxbeat_creato'})
    except Exception as bb_err:
        logger.error(f"[primary] auto-creazione promemoria fallita: {bb_err}")

    _callbacks['trigger_sync_after_edit']()
    return {'nota_app': nota_app, 'nuova_nota': nuova_nota}


def _rimuovi_appuntamento_db_primary(row, *, nota_corrente=None, lcl=None):
    """Variante FASE 2 di _rimuovi_appuntamento_da_filone — solo DB."""
    from . import db as _db

    ek = _resolve_eneltel_for_appuntamento(row)
    if not ek:
        raise ValueError(f'Eneltel non risolto per riga {row}')

    if nota_corrente is None:
        local = _db.get_filone_row(ek, lcl=lcl) or {}
        nota_corrente = local.get('note_appuntamento') or ''

    col_note = _get_colonna_note()
    nuova_nota = _calcola_rimozione_app(nota_corrente)

    _db.record_local_change(
        ek, {col_note: nuova_nota},
        user=_safe_username(),
        origin='rimuovi_app_primary',
        initial_state='pending',
        lcl=lcl,
    )

    try:
        from . import sync_engine as _se
        _se.trigger_drain_now()
    except Exception:
        pass

    _callbacks['trigger_sync_after_edit']()
    return {'nuova_nota': nuova_nota}


def _imposta_appuntamento_su_filone(row, orario, data_app='', note_extra='',
                                     origine='contatore', pod_override=None,
                                     eneltel_override=None, nota_corrente=None,
                                     lcl=None):
    """Logica condivisa per impostare appuntamento su una riga del filone.

    In modalità 'primary' (Fase 2): legge nota corrente dal DB locale,
    calcola la nuova nota, scrive via record_local_change. Drainer pusha.
    In modalità 'shadow' (Fase 1): path originale via ws.update_cell + hook
    shadow al DB.
    """
    nota_app = f"APP {orario}"
    return _imposta_appuntamento_db_primary(
        row, orario, nota_app, data_app=data_app, note_extra=note_extra,
        origine=origine, pod_override=pod_override,
        eneltel_override=eneltel_override, nota_corrente=nota_corrente,
        lcl=lcl,
    )


def _rimuovi_appuntamento_da_filone(row, nota_corrente=None, lcl=None):
    """Logica condivisa per rimuovere appuntamento da una riga del filone."""
    return _rimuovi_appuntamento_db_primary(row, nota_corrente=nota_corrente, lcl=lcl)


# ============================================================================
# ERROR HELPER
# ============================================================================

def _api_error(e, contesto='operazione', status=500):
    """Logga l'errore completo, ritorna risposta JSON sanitizzata."""
    logger.error(f"❌ Errore in {contesto}: {type(e).__name__}: {e}")
    _traceback_mod.print_exc()
    return jsonify({'error': f'Errore interno durante {contesto}. Riprova.'}), status
