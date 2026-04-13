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
_sheets_lock = threading.Lock()
_gsheets_write_lock = threading.Lock()  # Serializza operazioni di scrittura su Google Sheets


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
    global _sheets_client, _worksheet, _worksheet_initialized
    with _sheets_lock:
        _sheets_client = None
        _worksheet = None
        _worksheet_initialized = False


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
    """Ottiene il worksheet attivo (con cache thread-safe)."""
    global _worksheet, _headers, _header_map, _worksheet_initialized

    with _sheets_lock:
        if _worksheet_initialized and not force_refresh and _worksheet is not None:
            return _worksheet

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

    return _worksheet


def get_spreadsheet():
    """Ottiene l'oggetto Spreadsheet (per chiamate API dirette)."""
    client = get_sheets_client()
    return _sheets_retry(client.open_by_key, ID_FOGLIO_GOOGLE)


def get_header_map():
    """Restituisce la mappa header corrente (inizializza se necessario)."""
    if not _worksheet_initialized:
        get_worksheet()
    return _header_map


def get_headers():
    """Restituisce la lista header corrente."""
    if not _worksheet_initialized:
        get_worksheet()
    return _headers


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


# ============================================================================
# RICERCA RIGHE
# ============================================================================

def _get_col_index(col_type='pod'):
    """Restituisce l'indice 1-based della colonna POD o ENELTEL dalla header_map."""
    if col_type == 'pod':
        for name in ['POD', 'Pod', 'pod', 'Codice POD']:
            if name in _header_map:
                return _header_map[name]
    else:
        for name in ['ENELTEL', 'Eneltel', 'eneltel']:
            if name in _header_map:
                return _header_map[name]
    return None


def build_pod_row_map():
    """Costruisce una mappa fresca POD→riga (1-based) leggendo dal foglio.

    Utile per batch update: costruisci la mappa subito prima di scrivere
    per evitare problemi di righe spostate.
    """
    ws = get_worksheet()
    col = _get_col_index('pod')
    if col is None:
        return {}
    values = ws.col_values(col)
    result = {}
    for i, val in enumerate(values[1:], start=2):
        v = str(val).strip().upper()
        if v:
            result[v] = i
    return result


def verify_row_pod(row_num, expected_pod):
    """Verifica che alla riga row_num ci sia il POD atteso.

    Legge la cella POD dalla riga indicata e confronta.
    Restituisce True se corrispondono, False se la riga è stata spostata.
    """
    ws = get_worksheet()
    col = _get_col_index('pod')
    if col is None:
        return False
    actual = str(ws.cell(row_num, col).value or '').strip().upper()
    return actual == str(expected_pod).strip().upper()


def verify_row_eneltel(row_num, expected_eneltel):
    """Verifica che alla riga row_num ci sia l'ENELTEL atteso."""
    ws = get_worksheet()
    col = _get_col_index('eneltel')
    if col is None:
        return False
    actual = str(ws.cell(row_num, col).value or '').lstrip('0')
    return actual == str(expected_eneltel).lstrip('0')


def find_row_by_eneltel(eneltel):
    """Trova la riga corrispondente a un ENELTEL (1-based) o None."""
    ws = get_worksheet()
    col_eneltel = _get_col_index('eneltel')
    if col_eneltel is None:
        return None

    eneltel_norm = str(eneltel).lstrip('0')
    col_values = ws.col_values(col_eneltel)
    for i, val in enumerate(col_values[1:], start=2):
        if str(val).lstrip('0') == eneltel_norm:
            return i
    return None


def find_row_by_pod(pod):
    """Trova la riga corrispondente a un POD (1-based) o None."""
    ws = get_worksheet()
    col_pod = _get_col_index('pod')
    if col_pod is None:
        return None

    pod_norm = str(pod).strip().upper()
    col_values = ws.col_values(col_pod)
    for i, val in enumerate(col_values[1:], start=2):
        if str(val).strip().upper() == pod_norm:
            return i
    return None


# ============================================================================
# AGGIORNAMENTO CELLE
# ============================================================================

def update_cell(row, col_name, value):
    """Aggiorna una singola cella (thread-safe)."""
    with _gsheets_write_lock:
        ws = get_worksheet()
        if col_name not in _header_map:
            return False, f"Colonna '{col_name}' non trovata"
        col = _header_map[col_name]
        ws.update_cell(row, col, value)
    return True, "OK"


def update_row_fields(eneltel, fields):
    """Aggiorna più campi di una riga identificata da ENELTEL (thread-safe)."""
    logger.info(f"🔧 update_row_fields: ENELTEL={eneltel}, campi={fields}")

    row = find_row_by_eneltel(eneltel)
    if row is None:
        return False, f"ENELTEL {eneltel} non trovato"

    logger.info(f"   Trovata riga: {row}")

    updates = []
    errors = []

    for col_name, value in fields.items():
        if col_name not in _header_map:
            errors.append(f"Colonna '{col_name}' non trovata")
            logger.error(f"   ❌ Colonna '{col_name}' NON trovata in header_map")
            continue
        col = _header_map[col_name]
        logger.info(f"   ✅ Colonna '{col_name}' alla posizione {col}")
        updates.append({
            'range': gspread.utils.rowcol_to_a1(row, col),
            'values': [[value]]
        })

    if updates:
        with _gsheets_write_lock:
            ws = get_worksheet()
            logger.info(f"   📝 batch_update con {len(updates)} celle...")
            ws.batch_update(updates)
            logger.info(f"   ✅ batch_update completato!")

    if errors:
        return True, f"Aggiornato con warning: {', '.join(errors)}"
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


def _imposta_appuntamento_su_filone(row, orario, data_app='', note_extra='',
                                     origine='contatore', pod_override=None,
                                     eneltel_override=None, nota_corrente=None):
    """Logica condivisa per impostare appuntamento su una riga del filone (thread-safe)."""
    col_note = _get_colonna_note()
    if col_note not in _header_map:
        raise ValueError(f'Colonna {col_note} non trovata')

    col = _header_map[col_note]

    # Sezione critica: lettura-modifica-scrittura della cella nota
    with _gsheets_write_lock:
        ws = get_worksheet()
        nota_attuale = nota_corrente if nota_corrente is not None else (ws.cell(row, col).value or '')

        nota_app = f"APP {orario}"

        if 'APP' in nota_attuale.upper():
            nota_attuale = re.sub(
                r'APP\s*\d{1,2}[:.]\d{2}(?:\s*[-/]\s*\d{1,2}[:.]\d{2})?',
                '', nota_attuale, flags=re.IGNORECASE
            )
            nota_attuale = nota_attuale.strip(' /|-')

        nuova_nota = f"{nota_app} // {nota_attuale}" if nota_attuale else nota_app
        ws.update_cell(row, col, nuova_nota)

    # Auto-crea promemoria Appuntamento
    try:
        contatore_data = _callbacks['extract_contatore_data'](row)
        username = _get_username()
        data_bb = data_app if data_app else contatore_data.get('data_pianificata', '') or datetime.now().strftime('%Y-%m-%d')
        pod_val = pod_override or contatore_data.get('pod', '')
        eneltel_val = eneltel_override or contatore_data.get('eneltel', '')

        _callbacks['crea_promemoria_boxbeat'](
            data_appuntamento=data_bb, ora_appuntamento=orario,
            pod=pod_val, eneltel=eneltel_val,
            nominativo=contatore_data.get('nominativo', ''),
            via=contatore_data.get('via', ''), civico=contatore_data.get('civico', ''),
            comune=contatore_data.get('comune', ''), note=note_extra,
            provincia=PROVINCIA_ATTIVA, creato_da=username, origine=origine,
            data_pianificata=contatore_data.get('data_pianificata', '')
        )
        _callbacks['registra_appuntamento_excel'](
            data_appuntamento=data_bb, orario=orario,
            pod=pod_val, eneltel=eneltel_val,
            nominativo=contatore_data.get('nominativo', ''),
            via=contatore_data.get('via', ''), civico=contatore_data.get('civico', ''),
            comune=contatore_data.get('comune', ''), note=note_extra,
            origine=origine,
            data_pianificata=contatore_data.get('data_pianificata', ''),
            creato_da=username,
            provincia=PROVINCIA_ATTIVA
        )
        _callbacks['sse_notify_all']('calendario_update', {'azione': 'boxbeat_creato'})
    except Exception as bb_err:
        logger.error(f"[Appuntamento] Errore auto-creazione promemoria: {bb_err}")

    _callbacks['trigger_sync_after_edit']()
    return {'nota_app': nota_app, 'nuova_nota': nuova_nota}


def _rimuovi_appuntamento_da_filone(row, nota_corrente=None):
    """Logica condivisa per rimuovere appuntamento da una riga del filone (thread-safe)."""
    col_note = _get_colonna_note()

    if col_note not in _header_map:
        raise ValueError(f'Colonna {col_note} non trovata')

    col = _header_map[col_note]

    # Sezione critica: lettura-modifica-scrittura della cella nota
    with _gsheets_write_lock:
        ws = get_worksheet()
        nota_attuale = nota_corrente if nota_corrente is not None else (ws.cell(row, col).value or '')

        nuova_nota = re.sub(
            r'APP\s*\d{1,2}[:.]\d{2}(?:\s*[-/]\s*\d{1,2}[:.]\d{2})?',
            '', nota_attuale, flags=re.IGNORECASE
        )
        nuova_nota = re.sub(r'\s*//\s*', ' // ', nuova_nota).strip(' /|-')
        nuova_nota = re.sub(r'^//\s*|\s*//\s*$', '', nuova_nota).strip()
        ws.update_cell(row, col, nuova_nota)

    _callbacks['trigger_sync_after_edit']()
    return {'nuova_nota': nuova_nota}


# ============================================================================
# ERROR HELPER
# ============================================================================

def _api_error(e, contesto='operazione', status=500):
    """Logga l'errore completo, ritorna risposta JSON sanitizzata."""
    logger.error(f"❌ Errore in {contesto}: {type(e).__name__}: {e}")
    _traceback_mod.print_exc()
    return jsonify({'error': f'Errore interno durante {contesto}. Riprova.'}), status
