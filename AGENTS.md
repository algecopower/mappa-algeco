# Repository Guidelines

Gestionale Flask multi-provincia per la sostituzione contatori (Algeco). Backend Python + sync DB locale ↔ Google Sheets.

## Project Structure & Module Organization

Due entry point convivono:

- `server.py` — backend Flask **per singola provincia** (env `PROVINCIA=TORINO|PAVIA|...`, porta da `FLASK_PORT`). Avviabile standalone con `--torino`/`--pavia` per dev locale.
- `router.py` — reverse proxy by-Host che spawna un `server.py` per provincia leggendo `data/provinces.json`, espone porta unica (`ROUTER_PORT=5050`) e ospita la UI admin su `admin.*`. È l'entry point in produzione.

Codice condiviso:

- `moduli/` — logica di dominio (sync engine, DB, mappa, percorsi, auth, calendario…). `moduli/shared.py` e `moduli/sync_engine.py` sono il cuore del sync DB↔Sheets descritto in [DB_LOCALE_README.md](DB_LOCALE_README.md).
- `routes/` — blueprint Flask montati da `server.py` (un file per area: distinte, calendario, dashboard, sync, ecc.).
- `tools/` — CLI di servizio (dump DB, archive audit, migrate, validate shadow…). Tutti girano con il venv: `/opt/mappa-algeco/venv/bin/python tools/<x>.py`.
- `config.py` — switch provincia + tutti i path. Carica `data/provinces.json` (con fallback hardcoded per bootstrap).
- `data/{provincia}/state.db` — SQLite (WAL) source of truth in `primary` mode. Schema v2 con PK composta `(eneltel_key, lcl)`.
- `templates/`, `static/` — UI per-provincia; `templates_router/` — UI admin del router.

## Build, Test, and Development Commands

```bash
PY=/opt/mappa-algeco/venv/bin/python

# Dev locale (singola provincia, no router)
$PY server.py --torino
$PY server.py --pavia

# Multi-tenant come in produzione
$PY router.py

# Test
$PY -m pytest                              # tutta la suite
$PY -m pytest tests/test_sync_engine.py    # un singolo file
$PY -m pytest tests/test_db.py::test_bootstrap_schema_idempotente

# CLI ricorrenti
$PY tools/dump_db.py                       # stato DB Torino
PROVINCIA=PAVIA $PY tools/dump_db.py       # stato DB Pavia
$PY tools/validate_shadow.py               # sanity DB vs Sheets
```

Produzione: `systemctl restart mappa-algeco-router` (i backend per provincia ripartono in cascata). Cloudflare è il service `cloudflared-tunnel.service`, indipendente. Vedi [deploy/README.md](deploy/README.md).

## Coding Style & Naming Conventions

- Python 3.12, 4-space indent, header `# -*- coding: utf-8 -*-` su ogni `.py` (commenti e docstring in italiano).
- Nessun linter/formatter configurato — segui lo stile dei file vicini.
- File e simboli in `snake_case`; moduli sotto `moduli/` raggruppati per dominio (`sync_engine`, `verifica_indirizzi`, …), non per layer.
- Nessuna nuova dipendenza senza aggiornare [requirements.txt](requirements.txt).
- File con segreti sono già in `.gitignore` (`credenziali.json`, `users.json`, `.flask_secret_key`, `data/smtp_settings.json`) — non committarli mai.

## Testing Guidelines

- Framework: `pytest`. La fixture `db_isolato` in [tests/conftest.py](tests/conftest.py) monkeypatcha `config.DB_PATH` su un file temporaneo e ricarica `moduli.db` — usala per qualunque test che tocchi lo stato. **Non puntare al DB di produzione.**
- Nomi: `test_*.py`, funzioni `test_*`. Raggruppa per modulo testato (`test_db.py`, `test_sync_engine.py`, `test_inplace_patch.py`…).
- Non c'è gate di coverage; aggiungi test quando tocchi sync engine, schema DB o resolution di conflitti.

## Commit & Pull Request Guidelines

- Messaggi in italiano, prima riga ~70 char. Pattern ricorrenti dal log: `fix(scope): ...`, `docs(scope): ...`, `admin UI: ...`, `DB locale: ...`, `Step N multi-tenant: ...`. Usa lo scope quando l'area è netta (`pull`, `shadow`, `auth`, `deploy`).
- Un commit = un cambio logico; descrivi il *perché* nel body se non ovvio dal diff.
- Non c'è template PR. In assenza di CI, prima del merge: lancia `pytest` e — per cambi su sync, schema o route — fai un giro su `server.py --torino` in locale.
