# -*- coding: utf-8 -*-
"""
Safe JSON I/O — Scritture atomiche e lock per-file.

Tutte le operazioni su file JSON dovrebbero passare da qui per garantire:
1. Scritture atomiche (temp file + os.rename, atomico su Linux)
2. Lock thread-safe per-file (evita race condition tra thread)
3. Ciclo read-modify-write protetto da lock esclusivo
"""
import os
import json
import tempfile
import threading
import logging

logger = logging.getLogger('mappa_algeco')

# Lock per-file: un Lock per ogni percorso assoluto
_file_locks: dict[str, threading.Lock] = {}
_meta_lock = threading.Lock()  # protegge l'accesso a _file_locks


def _get_lock(filepath: str) -> threading.Lock:
    """Restituisce (o crea) il Lock associato a un filepath."""
    key = os.path.abspath(filepath)
    with _meta_lock:
        if key not in _file_locks:
            _file_locks[key] = threading.Lock()
        return _file_locks[key]


def json_read(filepath: str, default=None):
    """
    Legge un file JSON in modo thread-safe.

    Args:
        filepath: percorso del file JSON
        default: valore restituito se il file non esiste o è corrotto

    Returns:
        Contenuto deserializzato, oppure *default*.
    """
    lock = _get_lock(filepath)
    with lock:
        return _json_read_unlocked(filepath, default)


def _json_read_unlocked(filepath: str, default=None):
    """Lettura interna senza acquisire il lock (usare dentro un lock già acquisito)."""
    if not os.path.exists(filepath):
        return default if default is not None else {}
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError, OSError) as e:
        logger.warning(f"[safe_json] Errore lettura {filepath}: {e}")
        return default if default is not None else {}


def json_write(filepath: str, data, **dump_kwargs) -> bool:
    """
    Scrive un file JSON in modo atomico e thread-safe.

    Procedura:
    1. Scrive in un file temporaneo nella stessa directory
    2. Chiama os.replace() (atomico su Linux, sovrascrive il target)

    Args:
        filepath: percorso del file JSON
        data: oggetto serializzabile
        **dump_kwargs: argomenti extra per json.dump (indent, ensure_ascii, …)

    Returns:
        True se OK, False se errore.
    """
    lock = _get_lock(filepath)
    with lock:
        return _json_write_unlocked(filepath, data, **dump_kwargs)


def _json_write_unlocked(filepath: str, data, **dump_kwargs) -> bool:
    """Scrittura interna senza acquisire il lock."""
    dirpath = os.path.dirname(filepath) or '.'
    os.makedirs(dirpath, exist_ok=True)

    # Default per dump_kwargs
    dump_kwargs.setdefault('ensure_ascii', False)

    fd = None
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=dirpath,
            prefix='.tmp_',
            suffix='.json'
        )
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            fd = None  # os.fdopen prende ownership del fd
            json.dump(data, f, **dump_kwargs)
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp_path, filepath)
        tmp_path = None  # rename riuscito, niente da pulire
        return True

    except Exception as e:
        logger.error(f"[safe_json] Errore scrittura {filepath}: {e}")
        return False

    finally:
        # Chiudi fd se ancora aperto (errore prima di os.fdopen)
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        # Rimuovi file temporaneo se rimasto (errore dopo mkstemp ma prima di replace)
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def json_modify(filepath: str, modifier, default=None, **dump_kwargs):
    """
    Ciclo atomico read-modify-write protetto da lock esclusivo.

    Args:
        filepath: percorso del file JSON
        modifier: callable(data) -> data_modificato.
                  Riceve il contenuto attuale e deve restituire il nuovo contenuto.
                  Se restituisce None, il file NON viene sovrascritto.
        default: valore iniziale se il file non esiste
        **dump_kwargs: argomenti extra per json.dump

    Returns:
        (success: bool, data: risultato del modifier)
    """
    lock = _get_lock(filepath)
    with lock:
        current = _json_read_unlocked(filepath, default)
        new_data = modifier(current)
        if new_data is None:
            return True, current  # Nessuna modifica richiesta
        ok = _json_write_unlocked(filepath, new_data, **dump_kwargs)
        return ok, new_data
