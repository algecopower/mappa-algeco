# Repository Guidelines

Gestionale Flask multi-provincia per la sostituzione contatori (Algeco). Backend Python + DB SQLite locale per provincia. Google Sheets è stato **scollegato il 21/05/2026**: la fonte di verità è esclusivamente il DB locale, i dati esterni entrano via import Excel manuale (vedi [DB_LOCALE_README.md](DB_LOCALE_README.md)).

## Project Structure & Module Organization

Due entry point convivono:

- `server.py` — backend Flask **per singola provincia** (env `PROVINCIA=TORINO|PAVIA|...`, porta da `FLASK_PORT`). Avviabile standalone con `--torino`/`--pavia` per dev locale.
- `router.py` — reverse proxy by-Host che spawna un `server.py` per provincia leggendo `data/provinces.json`, espone porta unica (`ROUTER_PORT=5050`) e ospita la UI admin su `admin.*`. È l'entry point in produzione.

Codice condiviso:

- `moduli/` — logica di dominio (DB, mappa, percorsi, auth, calendario, importer Excel, ecc.). `moduli/db.py` è il cuore del DB locale; `moduli/shared.py` mantiene l'API storica delle scritture (nomi legacy `safe_batch_update_by_name`, `get_worksheet`, ...) instradando tutto al DB. `moduli/sync_engine.py` è uno stub post-disconnessione.
- `routes/` — blueprint Flask montati da `server.py` (un file per area: contatori, distinte, calendario, dashboard, sync, ecc.).
- `tools/` — CLI di servizio (`dump_db.py`, `archive_audit_log.py`, `cleanup_done_pendings.py`, `build_indir_db.py`, `confronta_percorsi.py`). Tutti girano con il venv: `/opt/mappa-algeco/venv/bin/python tools/<x>.py`.
- `config.py` — switch provincia + tutti i path. Carica `data/provinces.json` (sovrascrivibile da `data/{prov}/settings.json` per i parametri esposti in UI admin).
- `data/{provincia}/state.db` — SQLite (WAL), unica fonte di verità. Schema v2 con PK composta `(eneltel_key, lcl)`.
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
```

Produzione: `systemctl restart mappa-algeco-router` (i backend per provincia ripartono in cascata). Cloudflare è il service `cloudflared-tunnel.service`, indipendente. Vedi [deploy/README.md](deploy/README.md).

## Coding Style & Naming Conventions

- Python 3.12, 4-space indent, header `# -*- coding: utf-8 -*-` su ogni `.py` (commenti e docstring in italiano).
- Nessun linter/formatter configurato — segui lo stile dei file vicini.
- File e simboli in `snake_case`; moduli sotto `moduli/` raggruppati per dominio (`excel_importer`, `verifica_indirizzi`, …), non per layer.
- Nessuna nuova dipendenza senza aggiornare [requirements.txt](requirements.txt).
- File con segreti sono già in `.gitignore` (`users.json`, `.flask_secret_key`, `data/smtp_settings.json`) — non committarli mai. `credenziali.json` è obsoleto (era per gspread) ma resta in `.gitignore` per precauzione.

## Testing Guidelines

- Framework: `pytest`. La fixture `db_isolato` in [tests/conftest.py](tests/conftest.py) monkeypatcha `config.DB_PATH` su un file temporaneo e ricarica `moduli.db` — usala per qualunque test che tocchi lo stato. **Non puntare al DB di produzione.**
- Nomi: `test_*.py`, funzioni `test_*`. Raggruppa per modulo testato (`test_db.py`, `test_inplace_patch.py`…).
- Non c'è gate di coverage; aggiungi test quando tocchi schema DB, importer Excel o route di scrittura.

## Commit & Pull Request Guidelines

- Messaggi in italiano, prima riga ~70 char. Pattern ricorrenti dal log: `fix(scope): ...`, `docs(scope): ...`, `admin UI: ...`, `import: ...`. Usa lo scope quando l'area è netta (`import`, `auth`, `deploy`, `tema`).
- Un commit = un cambio logico; descrivi il *perché* nel body se non ovvio dal diff.
- Non c'è template PR. In assenza di CI, prima del merge: lancia `pytest` e — per cambi su schema, importer o route di scrittura — fai un giro su `server.py --torino` in locale.
