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


def _trova_indice_multi(headers, *nomi):
    """Cerca tra più nomi colonna, restituisce il primo indice trovato (0-based)."""
    for nome in nomi:
        r = _trova_indice_colonna(headers, nome)
        if r is not None:
            return r
    return None


def _get_colonna_note():
    """Restituisce il nome della colonna note in base alla provincia."""
    return 'NOTE APP' if PROVINCIA_ATTIVA == 'TORINO' else 'NOTE'


def _get_username():
    """Estrai username dalla sessione corrente."""
    user = session.get('user')
    if isinstance(user, dict):
        return user.get('username', 'sconosciuto')
    return user or 'sconosciuto'


# ============================================================================
# RICERCA RIGHE
# ============================================================================

def find_row_by_eneltel(eneltel):
    """Trova la riga corrispondente a un ENELTEL (1-based) o None."""
    ws = get_worksheet()

    col_eneltel = None
    for col_name in ['ENELTEL', 'Eneltel', 'eneltel']:
        if col_name in _header_map:
            col_eneltel = _header_map[col_name]
            break

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

    col_pod = None
    for col_name in ['POD', 'Pod', 'pod', 'Codice POD']:
        if col_name in _header_map:
            col_pod = _header_map[col_name]
            break

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
    """Aggiorna una singola cella."""
    ws = get_worksheet()
    if col_name not in _header_map:
        return False, f"Colonna '{col_name}' non trovata"
    col = _header_map[col_name]
    ws.update_cell(row, col, value)
    return True, "OK"


def update_row_fields(eneltel, fields):
    """Aggiorna più campi di una riga identificata da ENELTEL."""
    logger.info(f"🔧 update_row_fields: ENELTEL={eneltel}, campi={fields}")

    row = find_row_by_eneltel(eneltel)
    if row is None:
        return False, f"ENELTEL {eneltel} non trovato"

    logger.info(f"   Trovata riga: {row}")
    ws = get_worksheet()

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
    """Logica condivisa per impostare appuntamento su una riga del filone."""
    col_note = _get_colonna_note()
    ws = get_worksheet()
    if col_note not in _header_map:
        raise ValueError(f'Colonna {col_note} non trovata')

    col = _header_map[col_note]
    # Se il frontend ha inviato il contenuto corrente della textarea, usalo
    # per non perdere eventuali modifiche non ancora salvate
    nota_attuale = nota_corrente if nota_corrente is not None else (ws.cell(row, col).value or '')

    ora_pian_value = ''

    if PROVINCIA_ATTIVA == 'PAVIA':
        # Pavia: scrive solo 'APP' nelle note e l'orario in 'Ora pian'
        nota_app = 'APP'
        ora_pian_value = orario

        # Rimuovi eventuale APP precedente (con o senza orario)
        if 'APP' in nota_attuale.upper():
            nota_attuale = re.sub(
                r'APP\s*(?:\d{1,2}[:.]\d{2}(?:\s*[-/]\s*\d{1,2}[:.]\d{2})?)?',
                '', nota_attuale, flags=re.IGNORECASE
            )
            nota_attuale = nota_attuale.strip(' /|-')

        nuova_nota = f"{nota_app} // {nota_attuale}" if nota_attuale else nota_app
        ws.update_cell(row, col, nuova_nota)

        # Scrivi orario in colonna 'Ora pian'
        col_ora_pian = None
        for nome in ['Ora pian', 'ORA PIAN', 'ORA_PIAN']:
            if nome in _header_map:
                col_ora_pian = _header_map[nome]
                break
        if col_ora_pian:
            ws.update_cell(row, col_ora_pian, orario)
    else:
        # Torino: comportamento originale (APP + orario nelle note)
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
    return {'nota_app': nota_app, 'nuova_nota': nuova_nota, 'ora_pian': ora_pian_value}


def _rimuovi_appuntamento_da_filone(row, nota_corrente=None):
    """Logica condivisa per rimuovere appuntamento da una riga del filone."""
    col_note = _get_colonna_note()
    ws = get_worksheet()

    if col_note not in _header_map:
        raise ValueError(f'Colonna {col_note} non trovata')

    col = _header_map[col_note]
    nota_attuale = nota_corrente if nota_corrente is not None else (ws.cell(row, col).value or '')

    if PROVINCIA_ATTIVA == 'PAVIA':
        # Pavia: rimuovi 'APP' (con o senza orario) e pulisci 'Ora pian'
        nuova_nota = re.sub(
            r'APP\s*(?:\d{1,2}[:.]\d{2}(?:\s*[-/]\s*\d{1,2}[:.]\d{2})?)?',
            '', nota_attuale, flags=re.IGNORECASE
        )
        nuova_nota = re.sub(r'\s*//\s*', ' // ', nuova_nota).strip(' /|-')
        nuova_nota = re.sub(r'^//\s*|\s*//\s*$', '', nuova_nota).strip()
        ws.update_cell(row, col, nuova_nota)

        # Pulisci colonna 'Ora pian'
        col_ora_pian = None
        for nome in ['Ora pian', 'ORA PIAN', 'ORA_PIAN']:
            if nome in _header_map:
                col_ora_pian = _header_map[nome]
                break
        if col_ora_pian:
            ws.update_cell(row, col_ora_pian, '')
    else:
        # Torino: rimuovi 'APP orario' dalle note
        nuova_nota = re.sub(
            r'APP\s*\d{1,2}[:.]\d{2}(?:\s*[-/]\s*\d{1,2}[:.]\d{2})?',
            '', nota_attuale, flags=re.IGNORECASE
        )
        nuova_nota = re.sub(r'\s*//\s*', ' // ', nuova_nota).strip(' /|-')
        nuova_nota = re.sub(r'^//\s*|\s*//\s*$', '', nuova_nota).strip()
        ws.update_cell(row, col, nuova_nota)

    _callbacks['trigger_sync_after_edit']()
    return {'nuova_nota': nuova_nota, 'ora_pian': ''}


# ============================================================================
# ERROR HELPER
# ============================================================================

def _api_error(e, contesto='operazione', status=500):
    """Logga l'errore completo, ritorna risposta JSON sanitizzata."""
    logger.error(f"❌ Errore in {contesto}: {type(e).__name__}: {e}")
    _traceback_mod.print_exc()
    return jsonify({'error': f'Errore interno durante {contesto}. Riprova.'}), status
