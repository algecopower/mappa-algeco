# -*- coding: utf-8 -*-
"""Modulo per l'auto-import periodico del filone da Google Sheets.

Scarica l'export XLSX direttamente dal Google Sheet configurato per ciascuna
provincia, verifica se ci sono novità tramite hash SHA-256 (evitando import
inutili se il foglio non è cambiato), ed esegue l'import con `excel_importer.import_excel()`.
"""
import hashlib
import io
import json
import logging
import os
import re
import tempfile
import time
import zipfile
from typing import Optional, Tuple

import requests

from . import excel_importer

logger = logging.getLogger('mappa_algeco')

_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GOOGLE_SHEETS_ID_RE = re.compile(r'/spreadsheets/d/([a-zA-Z0-9-_]+)')
_GID_RE = re.compile(r'[?&#]gid=(\d+)')


def extract_sheet_id_and_gid(url_or_id: str) -> Tuple[str, Optional[str]]:
    """Estrae sheet_id e gid da un URL di Google Sheets o da un ID diretto.

    Esempi supportati:
      - https://docs.google.com/spreadsheets/d/1SXRlFDtagj.../edit?gid=229428995
      - https://docs.google.com/spreadsheets/d/1SXRlFDtagj.../edit#gid=229428995
      - https://docs.google.com/spreadsheets/d/1SXRlFDtagj...
      - 1SXRlFDtagj...
    """
    s = (url_or_id or '').strip()
    if not s:
        raise ValueError("URL o ID foglio mancante")

    m_id = _GOOGLE_SHEETS_ID_RE.search(s)
    sheet_id = m_id.group(1) if m_id else s.split('/')[0].split('?')[0].split('#')[0].strip()
    if not sheet_id or not re.match(r'^[a-zA-Z0-9-_]+$', sheet_id):
        raise ValueError(f"ID foglio Google non valido: {url_or_id!r}")

    m_gid = _GID_RE.search(s)
    gid = m_gid.group(1) if m_gid else None

    return sheet_id, gid


def build_export_url(sheet_id: str, gid: Optional[str] = None) -> str:
    """Costruisce l'URL di esportazione Excel (.xlsx) per Google Sheets."""
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=xlsx"
    if gid:
        url += f"&gid={gid}"
    return url


def download_sheet_xlsx(url_or_id: str, gid: Optional[str] = None, timeout: int = 120) -> bytes:
    """Scarica il file .xlsx dal foglio Google Sheets.

    Ritorna i byte del file .xlsx o solleva eccezione in caso di errore.
    """
    sheet_id, extracted_gid = extract_sheet_id_and_gid(url_or_id)
    final_gid = gid if gid is not None else extracted_gid
    export_url = build_export_url(sheet_id, final_gid)

    headers = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/124.0.0.0 Safari/537.36 MappaAlgecoAutoImport/1.0'
        ),
    }

    try:
        r = requests.get(export_url, headers=headers, allow_redirects=True, timeout=timeout)
    except requests.RequestException as e:
        raise ConnectionError(f"Impossibile contattare Google Sheets: {e}")

    if r.status_code == 404:
        raise FileNotFoundError(f"Foglio Google non trovato (ID: {sheet_id}). Verifica l'URL.")
    if r.status_code in (401, 403):
        raise PermissionError(
            "Accesso negato al foglio Google. Assicurati che sia condiviso con "
            "'Chiunque abbia il link può visualizzare'."
        )
    if r.status_code != 200:
        raise RuntimeError(f"Errore download Google Sheets: HTTP {r.status_code}")

    content = r.content
    # Verifica che sia un archivio zip valido (formato .xlsx inizia per 'PK\x03\x04')
    if len(content) < 500 or not content.startswith(b'PK\x03\x04'):
        snippet = content[:200].decode('utf-8', errors='replace')
        raise ValueError(
            f"La risposta da Google non è un file Excel (.xlsx) valido. Risposta: {snippet}"
        )

    return content


def compute_sha256(content: bytes) -> str:
    """Calcola l'hash deterministico del contenuto del foglio.

    I file .xlsx esportati dinamicamente da Google Sheets contengono timestamp
    aggiornati ad ogni richiesta all'interno degli header dell'archivio ZIP.
    Per evitare falsi positivi (file rilevato come 'modificato' anche a parità
    di dati), questa funzione calcola l'hash dei CRC e dei nomi delle entry
    interne dell'archivio zip (escluse proprietà temporali effimere in docProps/).
    """
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            h = hashlib.sha256()
            for name in sorted(z.namelist()):
                if name.startswith('docProps/'):
                    continue
                info = z.getinfo(name)
                h.update(f"{name}:{info.CRC}".encode('utf-8'))
            return h.hexdigest()
    except Exception:
        # Fallback nel caso in cui non sia un file zip/xlsx (es. CSV grezzo)
        return hashlib.sha256(content).hexdigest()


def get_status_file_path(provincia: str) -> str:
    """Restituisce il path al file di stato dell'auto-import per la provincia."""
    cache_dir = os.path.join(_ROOT_DIR, 'cache')
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, f'auto_import_{provincia.lower()}.json')


def get_last_auto_import_status(provincia: str) -> dict:
    """Legge lo stato dell'ultimo auto-import per la provincia specificata."""
    status_path = get_status_file_path(provincia)
    if not os.path.exists(status_path):
        return {
            'last_import_ts': None,
            'last_check_ts': None,
            'hash': None,
            'success': None,
            'skipped': False,
            'message': 'Nessun import automatico eseguito finora',
        }
    try:
        with open(status_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"[{provincia}] Errore lettura stato auto-import: {e}")
        return {
            'last_import_ts': None,
            'last_check_ts': None,
            'hash': None,
            'success': None,
            'skipped': False,
            'message': f'Errore lettura stato: {e}',
        }


def save_auto_import_status(provincia: str, status_dict: dict) -> None:
    """Salva atomicamente lo stato dell'ultimo auto-import."""
    status_path = get_status_file_path(provincia)
    tmp_path = f"{status_path}.tmp"
    try:
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(status_dict, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, status_path)
    except Exception as e:
        logger.error(f"[{provincia}] Errore salvataggio stato auto-import: {e}")
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


def get_google_sheet_config(provincia: str) -> dict:
    """Recupera la configurazione del foglio Google per la provincia.

    Legge prima da data/{provincia}/settings.json se presente, altrimenti da
    data/provinces.json.
    """
    prov_upper = provincia.upper()
    provinces_path = os.path.join(_ROOT_DIR, 'data', 'provinces.json')
    prov_cfg = {}
    if os.path.exists(provinces_path):
        try:
            with open(provinces_path, 'r', encoding='utf-8') as f:
                all_provs = json.load(f)
                prov_cfg = all_provs.get(prov_upper, {})
        except Exception as e:
            logger.warning(f"Errore lettura provinces.json per auto-import: {e}")

    # Override da data/{provincia}/settings.json se esiste
    settings_path = os.path.join(_ROOT_DIR, 'data', prov_upper.lower(), 'settings.json')
    if os.path.exists(settings_path):
        try:
            with open(settings_path, 'r', encoding='utf-8') as f:
                settings = json.load(f)
                if 'google_sheet_url' in settings:
                    prov_cfg['google_sheet_url'] = settings['google_sheet_url']
                if 'auto_import_enabled' in settings:
                    prov_cfg['auto_import_enabled'] = settings['auto_import_enabled']
        except Exception as e:
            logger.warning(f"Errore lettura settings.json di {prov_upper}: {e}")

    return {
        'google_sheet_url': prov_cfg.get('google_sheet_url', '').strip(),
        'auto_import_enabled': prov_cfg.get('auto_import_enabled', True),
        'porta': prov_cfg.get('porta'),
        'nome': prov_cfg.get('nome', prov_upper),
    }


def esegui_auto_import(
    provincia: str,
    url: Optional[str] = None,
    force: bool = False,
    dry_run: bool = False,
    user: str = 'auto_import_sheets',
) -> dict:
    """Esegue l'auto-import del filone per una provincia.

    Passaggi:
      1. Recupera l'URL (passato come parametro o da configurazione).
      2. Scarica il file .xlsx da Google Sheets.
      3. Calcola l'hash SHA-256 dei byte: se non modificato rispetto all'ultimo
         import riuscito e non 'force', salta l'elaborazione.
      4. Salva temporaneamente il file ed esegue `excel_importer.import_excel()`.
      5. Se ha successo e non è dry-run, aggiorna lo stato persistente.

    Returns:
        dict con esito, statistiche righe e flag 'skipped'.
    """
    prov_upper = provincia.upper()
    cfg = get_google_sheet_config(prov_upper)
    sheet_url = url or cfg.get('google_sheet_url')

    if not sheet_url:
        msg = f"Nessun URL Google Sheets configurato per la provincia {prov_upper}"
        logger.warning(f"[AUTO-IMPORT] [{prov_upper}] {msg}")
        return {
            'success': False,
            'provincia': prov_upper,
            'error': msg,
            'message': msg,
        }

    logger.info(f"[AUTO-IMPORT] [{prov_upper}] Inizio download da Google Sheets...")
    t0 = time.time()
    try:
        content = download_sheet_xlsx(sheet_url)
    except Exception as e:
        msg = f"Errore durante lo scaricamento da Google Sheets: {e}"
        logger.error(f"[AUTO-IMPORT] [{prov_upper}] ❌ {msg}")
        # Salva lo stato di errore
        current_status = get_last_auto_import_status(prov_upper)
        current_status['last_check_ts'] = int(time.time())
        current_status['success'] = False
        current_status['message'] = msg
        save_auto_import_status(prov_upper, current_status)
        return {
            'success': False,
            'provincia': prov_upper,
            'error': msg,
            'message': msg,
        }

    sha = compute_sha256(content)
    download_elapsed = time.time() - t0
    logger.info(
        f"[AUTO-IMPORT] [{prov_upper}] Download completato in {download_elapsed:.1f}s "
        f"({len(content):,} bytes, SHA-256: {sha[:12]}...)"
    )

    last_status = get_last_auto_import_status(prov_upper)
    if not force and last_status.get('hash') == sha and last_status.get('success'):
        logger.info(
            f"[AUTO-IMPORT] [{prov_upper}] ⏭️ Foglio invariato (SHA-256 identico). "
            f"Nessuna importazione necessaria."
        )
        last_status['last_check_ts'] = int(time.time())
        last_status['skipped'] = True
        save_auto_import_status(prov_upper, last_status)
        return {
            'success': True,
            'provincia': prov_upper,
            'skipped': True,
            'hash': sha,
            'message': 'Nessuna modifica rilevata sul foglio Google (invariato)',
            'last_import_ts': last_status.get('last_import_ts'),
            'last_check_ts': last_status['last_check_ts'],
        }

    # Salva il file scaricato in una copia di snapshot e in un file temporaneo per l'import
    cache_dir = os.path.join(_ROOT_DIR, 'cache')
    os.makedirs(cache_dir, exist_ok=True)
    snapshot_path = os.path.join(cache_dir, f'last_sheet_{prov_upper.lower()}.xlsx')
    try:
        with open(snapshot_path, 'wb') as f:
            f.write(content)
    except Exception as e:
        logger.warning(f"[{prov_upper}] Impossibile salvare snapshot cache: {e}")

    with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        export_ts = int(time.time())
        logger.info(f"[AUTO-IMPORT] [{prov_upper}] Avvio importazione con merge...")
        t_import = time.time()
        res = excel_importer.import_excel(
            tmp_path,
            export_ts=export_ts,
            dry_run=dry_run,
            user=user,
            auto_mark_delivered=False,
            protect_pending_rows=True,
        )
        import_elapsed = time.time() - t_import

        success = res.get('success', False)
        if success:
            n_agg = res.get('rows_aggiornate', 0)
            n_nuove = res.get('rows_nuove', 0)
            n_celle = res.get('celle_modificate', 0)
            msg = (
                f"Import completato in {import_elapsed:.1f}s: "
                f"{n_agg} righe aggiornate, {n_nuove} nuove, {n_celle} celle modificate"
            )
            logger.info(f"[AUTO-IMPORT] [{prov_upper}] ✅ {msg}")

            if not dry_run:
                status_dict = {
                    'last_import_ts': export_ts,
                    'last_check_ts': export_ts,
                    'hash': sha,
                    'success': True,
                    'skipped': False,
                    'rows_nuove': n_nuove,
                    'rows_aggiornate': n_agg,
                    'celle_modificate': n_celle,
                    'rows_excel': res.get('rows_excel', 0),
                    'rows_skip_invalid_key': res.get('rows_skip_invalid_key', 0),
                    'rows_skip_local_newer': res.get('rows_skip_local_newer', 0),
                    'message': msg,
                }
                save_auto_import_status(prov_upper, status_dict)

                # Notifica il backend se siamo in un processo CLI esterno
                porta = cfg.get('porta')
                if porta:
                    try:
                        requests.post(
                            f"http://127.0.0.1:{porta}/api/internal/trigger-sync",
                            timeout=5,
                        )
                    except Exception:
                        pass
        else:
            err_msg = (
                res.get('error')
                or res.get('message')
                or ' / '.join(res.get('errori') or ['Errore importazione'])
            )
            logger.error(f"[AUTO-IMPORT] [{prov_upper}] ❌ Fallito: {err_msg}")
            if not dry_run:
                save_auto_import_status(prov_upper, {
                    'last_import_ts': last_status.get('last_import_ts'),
                    'last_check_ts': int(time.time()),
                    'hash': sha,
                    'success': False,
                    'skipped': False,
                    'message': f"Errore importazione: {err_msg}",
                })

        # Prepara risposta
        res['provincia'] = prov_upper
        res['skipped'] = False
        res['hash'] = sha
        return res
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
