# -*- coding: utf-8 -*-
"""
Stato condiviso e helper per la connessione a Google Sheets.

Tutte le route Blueprint importano da qui anziché ridefinire
get_worksheet(), find_row_by_*, _header_map, ecc.
"""
import json
import os
import re
import time
import logging
import threading
import traceback as _traceback_mod
from datetime import datetime

import gspread
from gspread.exceptions import APIError
from oauth2client.service_account import ServiceAccountCredentials
from flask import session, jsonify

from config import (
    ID_FOGLIO_GOOGLE,
    GID_SCHEDA,
    CREDENZIALI_JSON,
    PROVINCIA_ATTIVA,
)

logger = logging.getLogger('mappa_algeco')

# ============================================================================
# GOOGLE SHEETS — cache, retry e connessione
# ============================================================================
_sheets_client = None
_worksheet = None
_headers = None
_header_map = None        # {col_name: 1-based index}
_worksheet_initialized = False
_headers_ts = 0           # timestamp ultima rilettura di row 1 dal foglio
# TTL CORTO per i path di sola lettura (UI). Per le SCRITTURE non ci affidiamo
# mai a questo cache: leggiamo sempre la row 1 fresca dentro al lock di scrittura.
# Senza questa precauzione, se la collega aggiunge/rimuove/sposta una colonna,
# le scritture finiscono nelle colonne sbagliate finché il TTL non scade
# (era 30s = finestra in cui i dati venivano "spanati").
_HEADERS_TTL = 5
_sheets_lock = threading.Lock()
_gsheets_write_lock = threading.RLock()  # Serializza operazioni di scrittura su Google Sheets (rientrante)

# ============================================================================
# CACHE VALORI FOGLIO (evita ri-download su ogni richiesta)
# ============================================================================
_cached_all_values = None
_cached_all_values_ts = 0
_values_cache_lock = threading.Lock()
_VALUES_CACHE_TTL = 30  # secondi — non ri-scaricare entro questo intervallo


def _sheets_retry(func, *args, max_retries=3, **kwargs):
    """
    Esegue una chiamata gspread con retry + backoff esponenziale.
    Gestisce rate-limit Google (429), errori di rete e token scaduti.
    """
    delay = 2
    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            return func(*args, **kwargs)
        except APIError as e:
            last_exc = e
            status = e.response.status_code if hasattr(e, 'response') else 0
            if status == 429:
                wait = delay * (2 ** (attempt - 1))
                logger.warning(f"[GSheets] Rate limit 429, tentativo {attempt}/{max_retries}, attesa {wait}s")
                time.sleep(wait)
            elif status in (401, 403):
                logger.warning(f"[GSheets] Auth error {status}, rinnovo client...")
                _reset_sheets_client()
                time.sleep(1)
            elif status >= 500:
                wait = delay * (2 ** (attempt - 1))
                logger.warning(f"[GSheets] Server error {status}, tentativo {attempt}/{max_retries}, attesa {wait}s")
                time.sleep(wait)
            else:
                raise
        except (ConnectionError, TimeoutError, OSError) as e:
            last_exc = e
            wait = delay * (2 ** (attempt - 1))
            logger.warning(f"[GSheets] Errore rete: {e}, tentativo {attempt}/{max_retries}, attesa {wait}s")
            time.sleep(wait)
    logger.error(f"[GSheets] Fallito dopo {max_retries} tentativi: {last_exc}")
    raise last_exc


def _reset_sheets_client():
    """Invalida il client GSheets per forzare re-autenticazione."""
    global _sheets_client, _worksheet, _worksheet_initialized, _headers_ts
    with _sheets_lock:
        _sheets_client = None
        _worksheet = None
        _worksheet_initialized = False
        _headers_ts = 0
    invalidate_values_cache()


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
    """Ritorna tutti i valori del foglio con cache in memoria (thread-safe).

    Se la cache è fresca (< _VALUES_CACHE_TTL secondi), ritorna i dati
    in cache senza contattare Google Sheets.

    Args:
        force: Se True, scarica sempre dal foglio.
    """
    global _cached_all_values, _cached_all_values_ts
    now = time.time()

    if not force:
        with _values_cache_lock:
            if _cached_all_values is not None and (now - _cached_all_values_ts) < _VALUES_CACHE_TTL:
                logger.debug("[Cache] Valori foglio serviti da cache in memoria")
                return _cached_all_values

    ws = get_worksheet()
    values = _sheets_retry(ws.get_all_values)

    with _values_cache_lock:
        _cached_all_values = values
        _cached_all_values_ts = time.time()

    return values


def get_sheets_client():
    """Ottiene o crea il client Google Sheets (thread-safe)."""
    global _sheets_client
    with _sheets_lock:
        if _sheets_client is None:
            scope = [
                'https://spreadsheets.google.com/feeds',
                'https://www.googleapis.com/auth/drive'
            ]
            creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENZIALI_JSON, scope)
            _sheets_client = gspread.authorize(creds)
    return _sheets_client


def get_worksheet(force_refresh=False):
    """Ottiene il worksheet attivo (con cache thread-safe).

    Se force_refresh è True o il worksheet non è inizializzato, ricrea tutto.
    Altrimenti delega il refresh periodico degli header a _refresh_headers_if_needed.
    """
    global _worksheet, _headers, _header_map, _worksheet_initialized, _headers_ts

    with _sheets_lock:
        if _worksheet_initialized and not force_refresh and _worksheet is not None:
            ws_cached = _worksheet

        else:
            ws_cached = None

    if ws_cached is not None and not force_refresh:
        _refresh_headers_if_needed()
        return ws_cached

    client = get_sheets_client()
    spreadsheet = _sheets_retry(client.open_by_key, ID_FOGLIO_GOOGLE)

    target_ws = None
    for ws in spreadsheet.worksheets():
        if ws.id == GID_SCHEDA:
            target_ws = ws
            break

    if target_ws is None:
        target_ws = spreadsheet.sheet1

    headers = _sheets_retry(target_ws.row_values, 1)
    header_map = {h: i + 1 for i, h in enumerate(headers)}

    with _sheets_lock:
        _worksheet = target_ws
        _headers = headers
        _header_map = header_map
        _worksheet_initialized = True
        _headers_ts = time.time()

    return _worksheet


def _refresh_headers_if_needed():
    """Rilegge la row 1 del foglio se è scaduto il TTL.

    Serve a intercettare colonne aggiunte/rimosse/rinominate senza
    dover riavviare il server: senza questo refresh, _header_map
    resterebbe congelato al primo avvio e le scritture andrebbero
    su colonne sbagliate (spanamento del filone).
    """
    global _headers, _header_map, _headers_ts
    now = time.time()

    with _sheets_lock:
        if _worksheet is None:
            return
        if (now - _headers_ts) < _HEADERS_TTL:
            return
        ws = _worksheet

    try:
        headers = _sheets_retry(ws.row_values, 1)
    except Exception as e:
        logger.warning(f"[GSheets] Refresh header fallito: {e}")
        return

    header_map = {h: i + 1 for i, h in enumerate(headers)}

    with _sheets_lock:
        if headers != _headers:
            logger.info(f"[GSheets] Header foglio cambiati: {len(_headers or [])} → {len(headers)} colonne, rimappatura applicata")
        _headers = headers
        _header_map = header_map
        _headers_ts = time.time()


def get_spreadsheet():
    """Ottiene l'oggetto Spreadsheet (per chiamate API dirette)."""
    client = get_sheets_client()
    return _sheets_retry(client.open_by_key, ID_FOGLIO_GOOGLE)


def get_header_map():
    """Restituisce la mappa header corrente (inizializza se necessario).

    Se il worksheet è già inizializzato applica il refresh periodico (TTL)
    così le colonne aggiunte al foglio vengono viste senza riavviare il server.
    """
    if not _worksheet_initialized:
        get_worksheet()
    else:
        _refresh_headers_if_needed()
    return _header_map


def get_headers():
    """Restituisce la lista header corrente (con refresh periodico)."""
    if not _worksheet_initialized:
        get_worksheet()
    else:
        _refresh_headers_if_needed()
    return _headers


def force_refresh_headers():
    """Legge SEMPRE la row 1 dal foglio (no caching) e aggiorna la mappa globale.

    DEVE essere chiamata da TUTTI i path di scrittura, dentro il
    `_gsheets_write_lock`, così la mappa nome-colonna -> indice è garantita
    fresca al momento della scrittura. Se la collega ha aggiunto/rimosso/spostato
    colonne sul foglio, questa funzione lo rileva e rigenera la mappa, evitando
    che le scritture finiscano nelle colonne sbagliate ("spanamento").

    Restituisce la nuova `header_map` (dict {nome_colonna: indice_1based}).
    """
    global _headers, _header_map, _headers_ts
    ws = get_worksheet()  # assicura inizializzazione
    headers = _sheets_retry(ws.row_values, 1)
    header_map = {h: i + 1 for i, h in enumerate(headers)}

    with _sheets_lock:
        if _headers and headers != _headers:
            # Diagnostica: log il delta per facilitare il debugging.
            old_set = set(_headers)
            new_set = set(headers)
            aggiunte = new_set - old_set
            rimosse = old_set - new_set
            riordinate = old_set & new_set
            riordinate_diff = [
                h for h in riordinate
                if (_header_map or {}).get(h) != header_map.get(h)
            ]
            logger.warning(
                f"[GSheets] ⚠️ Header foglio modificati subito prima di una scrittura "
                f"({len(_headers)} → {len(headers)} col). "
                f"Aggiunte: {sorted(aggiunte)} | Rimosse: {sorted(rimosse)} | "
                f"Riordinate: {sorted(riordinate_diff)}. Mappa rigenerata."
            )
        _headers = headers
        _header_map = header_map
        _headers_ts = time.time()

    return header_map


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
    # FASE 2: branch "primary" — DB-only.
    # Non si scrive direttamente su Sheets: si registra la modifica nel DB
    # locale (state='pending') e il drainer la pusherà su Sheets in background.
    # Ritorna immediatamente alla route con success=True, latenza ~10ms.
    # _force_sheets=True (drainer) bypassa il branch primary.
    # ────────────────────────────────────────────────────────────────────────
    if not _force_sheets:
        return _route_to_db_primary(items, anchor_col=anchor_col,
                                    anchor_normalize=anchor_normalize)

    invalid_cols = set()
    lost_anchors = []
    n_remapped = 0
    n_skipped = 0
    n_written = 0

    def _normalize(v):
        s = str(v if v is not None else '').strip()
        if anchor_normalize == 'upper':
            return s.upper()
        if anchor_normalize == 'lstrip0':
            return s.lstrip('0')
        return s

    with _gsheets_write_lock:
        # 1. Refresh atomico degli header DENTRO il lock di scrittura.
        header_map = force_refresh_headers()
        ws = get_worksheet()

        # 2. Costruisci la mappa ancore (col_values fresco) se richiesto.
        anchor_pos_to_value = {}
        anchor_value_to_pos = {}
        any_anchor_request = any(it.get('anchor') for it in items)
        if anchor_col and any_anchor_request:
            if anchor_col not in header_map:
                logger.error(
                    f"[SafeWrite] Colonna ancora '{anchor_col}' non trovata "
                    f"negli header attuali. Tutte le scritture richiedenti "
                    f"verifica sono saltate."
                )
                return {
                    'success': False, 'written': 0, 'skipped': len(items),
                    'remapped': 0, 'invalid_cols': [], 'lost_anchors': [],
                    'error': f"Anchor column '{anchor_col}' missing",
                }
            col_idx_anchor = header_map[anchor_col]
            anchor_values = _sheets_retry(ws.col_values, col_idx_anchor)
            for i, v in enumerate(anchor_values):
                pos = i + 1
                if pos == 1:
                    continue
                key = _normalize(v)
                anchor_pos_to_value[pos] = key
                if key:
                    anchor_value_to_pos[key] = pos

        # 3. Costruisci updates A1 con indici FRESCHI.
        gspread_updates = []
        for u in items:
            row = u.get('row')
            col_name = u.get('col')
            value = u.get('value')
            expected_anchor = u.get('anchor')
            if row is None or row < 2 or not col_name:
                n_skipped += 1
                continue

            col_idx = header_map.get(col_name)
            if col_idx is None:
                invalid_cols.add(col_name)
                n_skipped += 1
                continue

            # Verifica/rimappa via ancora
            if anchor_col and expected_anchor:
                expected = _normalize(expected_anchor)
                actual = anchor_pos_to_value.get(row, '')
                if actual != expected:
                    new_row = anchor_value_to_pos.get(expected)
                    if new_row is None:
                        lost_anchors.append({
                            'row': row, 'col': col_name,
                            'anchor': expected_anchor,
                        })
                        n_skipped += 1
                        continue
                    if new_row != row:
                        n_remapped += 1
                        row = new_row

            gspread_updates.append({
                'range': gspread.utils.rowcol_to_a1(row, col_idx),
                'values': [[value if value is not None else '']],
            })

        if invalid_cols:
            logger.error(
                f"[SafeWrite] ⚠️ Colonne mancanti negli header freschi: "
                f"{sorted(invalid_cols)}. Le relative scritture sono state "
                f"SALTATE per non corrompere il filone."
            )
        if lost_anchors:
            logger.error(
                f"[SafeWrite] ⚠️ {len(lost_anchors)} ancore non più "
                f"presenti nel foglio: scritture saltate."
            )
        if n_remapped:
            logger.warning(
                f"[SafeWrite] {n_remapped} righe rimappate "
                f"(foglio riordinato dalla collega)."
            )

        # 4. Esegui in chunk con retry.
        if gspread_updates:
            CHUNK = 500
            for i in range(0, len(gspread_updates), CHUNK):
                chunk = gspread_updates[i:i + CHUNK]
                _sheets_retry(ws.batch_update, chunk)
                n_written += len(chunk)

    invalidate_values_cache()

    success = (not invalid_cols) and (not lost_anchors)
    return {
        'success': success,
        'written': n_written,
        'skipped': n_skipped,
        'remapped': n_remapped,
        'invalid_cols': sorted(invalid_cols),
        'lost_anchors': lost_anchors,
    }


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
    nuova_nota = ''
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
