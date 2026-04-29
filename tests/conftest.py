# -*- coding: utf-8 -*-
"""
Fixtures condivise per i test del DB locale.

Ogni test riceve un DB SQLite isolato in tmp_path: niente lockaggio sul
DB di produzione, niente effetti collaterali tra test.
"""
import os
import sys
import importlib

import pytest

# Rendi importabili i moduli del progetto
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def db_isolato(tmp_path, monkeypatch):
    """Patcha config.DB_PATH a un file temporaneo e ricarica il modulo db.

    Yields: il modulo db pronto all'uso. Alla fine chiude la connessione.
    """
    db_path = str(tmp_path / 'state.db')

    # Setup config: monkeypatcha DB_PATH PRIMA di importare db
    import config
    monkeypatch.setattr(config, 'DB_PATH', db_path, raising=False)

    # Importa db con il path patchato
    if 'moduli.db' in sys.modules:
        importlib.reload(sys.modules['moduli.db'])
    from moduli import db as db_module

    # Reset stato globale del modulo db (set di path già inizializzati)
    db_module._db_initialized_paths.clear()
    if hasattr(db_module._thread_local, 'conn'):
        try:
            db_module._thread_local.conn.close()
        except Exception:
            pass
        del db_module._thread_local.conn

    db_module.get_conn()  # bootstrap schema

    yield db_module

    # Cleanup
    try:
        db_module.close_conn()
    except Exception:
        pass


@pytest.fixture
def sync_engine_isolato(db_isolato):
    """Carica sync_engine con il db_isolato attivo."""
    import importlib
    if 'moduli.sync_engine' in sys.modules:
        importlib.reload(sys.modules['moduli.sync_engine'])
    from moduli import sync_engine
    return sync_engine
