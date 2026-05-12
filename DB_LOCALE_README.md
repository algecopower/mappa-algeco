# Architettura sync DB locale ↔ Google Sheets

Refactor completato in aprile-maggio 2026. Il programma non scrive più
direttamente su Google Sheets ad ogni POST utente: tutte le modifiche
passano da un DB SQLite locale, e un worker asincrono le propaga su Sheets
in batch.

## Modalità di funzionamento

Controllata da `USE_DB_BACKEND` in [config.py](config.py#L364) (env var omonima):

| Valore | Comportamento |
|---|---|
| `primary` (default, attuale) | DB = source of truth. Route → DB → drainer → Sheets. |
| `shadow` | DB popolato in parallelo, ma scritture utente vanno ancora direttamente su Sheets (fallback Fase 1, conservato per emergency rollback). |
| `off` | Layer DB disabilitato. Comportamento "pre-refactor". |

Per rollback immediato in caso di problemi:

```bash
sudo systemctl edit mappa.service
# Aggiungi: Environment="USE_DB_BACKEND=shadow"
sudo systemctl restart mappa.service
```

## Flusso operativo (primary mode)

```
┌──────────────┐  POST    ┌──────────────┐
│ UI / route   │─────────▶│ DB SQLite    │  filone, pending_writes, audit_log
└──────────────┘ <100ms   └──────┬───────┘
                                 │
                       ┌─────────┴─────────┐
                       ▼                   ▼
              push_drain_worker    pull_loop (180s)
              (ogni 30s)                   │
                       │                   │
                       ▼                   ▼
                  ┌──────────────────────────┐
                  │   GOOGLE SHEETS API      │
                  │ safe_batch_update_*(...) │
                  └──────────────────────────┘
```

**Scrittura (POST utente)**

1. La route filtra i campi modificabili → chiama `update_row_fields` o equivalente.
2. `safe_batch_update_by_name` ([moduli/shared.py:344](moduli/shared.py#L344)) entra nel branch `primary`.
3. `_route_to_db_primary` ([moduli/shared.py](moduli/shared.py)):
   - Risolve `eneltel_key` (PK del DB) via anchor (Eneltel/POD) o sheets_row.
   - **Dedup**: confronta col valore corrente in DB + pending overlay. Se uguale → no-op (es. client che manda tutto il form ma utente ha cambiato un solo campo).
   - `db.record_local_change(...)` accoda in `pending_writes` (state='pending') + aggiorna `filone` (preview ottimistica) + audit.
   - `sync_engine.trigger_drain_now()` sveglia il drainer.
4. Risposta API in ~50ms.

**Patch in-place mappa_data.json (soft edit)**

`server.py:trigger_sync_after_edit()` tenta prima [moduli/mappa_data_patcher.py:try_patch_inplace](moduli/mappa_data_patcher.py). Se le pending in volo sono **tutte cosmetiche** (email, telefono, nominativo, indirizzo, NOTE APP senza pattern APP, ecc.), patcha direttamente `data/{prov}/mappa_data.json` in ~50ms e invia SSE `sync_complete`. Niente sync_loop.

Per cambi **strutturali** (DATA PIAN, Operatore, Lat/Lon, COMUNE, NOTE APP con APP, Esito) cade nel sync_loop classico (~7-10s).

**Drainer (asincrono)**

[moduli/sync_engine.py:drain_once](moduli/sync_engine.py):

1. Ogni 30s (o on-demand via `trigger_drain_now()`).
2. Prende fino a `DRAIN_BATCH_SIZE=400` pending in stato `pending`.
3. Le marca `in_flight`.
4. Costruisce `items` con `row` da `filone.sheets_row` + `anchor=eneltel`.
5. Chiama `safe_batch_update_by_name(..., _force_sheets=True)`:
   - Lock globale + `force_refresh_headers()` (anti-spanamento colonne)
   - `col_values(Eneltel)` fresh per remap righe (anti-spanamento righe)
   - Skip-with-quarantine se colonna mancante o ancora persa
   - `batch_update` su Sheets
6. Marca pending `done` su successo, `failed` (con retry) o `quarantined`.

**Pull engine**

[moduli/sync_engine.py:pull_once](moduli/sync_engine.py) gira ogni 180s (chiamato da `_do_sincronizza_dati` in `server.py`):

1. Legge l'intero foglio via `shared.get_all_values_cached` (TTL 30s, condiviso con `data_loader`).
2. Calcola hash globale → skip se invariato.
3. Per ogni riga: calcola hash riga, confronta con `filone.row_hash`.
4. Se cambiato:
   - Carica pending in volo per la riga.
   - Per ogni colonna: confronta remote/base/pending → applica diff campo-per-campo.
   - Pending in volo + `remote != base_value` → audit `conflict_local_wins` (locale vince, drainer sovrascriverà).
   - Aggiorna `filone` con i cambi remoti (skip su colonne con pending in_flight).
5. Marca orphan le righe sparite.

## Schema DB

File: `data/{provincia}/state.db` (SQLite + WAL). Schema in [moduli/db.py](moduli/db.py).

| Tabella | Contenuto |
|---|---|
| `filone` | Una riga per contatore, indicizzata per `eneltel_key`. ~30 colonne tipizzate (pod, nominativo, comune, data_pianificata, operatore, lat/lon, ecc.) + meta (row_hash, last_remote_pull_ts, sheets_row, is_orphan). |
| `filone_extra` | Colonne del foglio non mappate alle ~30 note (es. `Mot. Rich.`, `MID`, `ZTL`). Key-value `(eneltel_key, column_name) → value`. |
| `pending_writes` | Coda di scritture da pushare su Sheets. Stati: `pending` / `in_flight` / `done` / `quarantined` / `failed`. `UNIQUE INDEX` parziale su `(eneltel_key, column_name)` con `state IN ('pending','in_flight')` per coalesce. |
| `audit_log` | Storico di ogni modifica. `source` ∈ {`local`, `remote`, `bootstrap`, `conflict_local_wins`, `quarantine`}. |
| `column_registry` | Mappatura nome colonna foglio → chiave interna, aggiornata ad ogni pull. |
| `filone_anomalies` | Righe del foglio anomale (Eneltel duplicato, riga senza Eneltel). Ricalcolato ad ogni pull. |
| `sync_state` | Key/value: `schema_version`, `last_full_pull_ts`, `last_full_pull_sheet_hash`, `last_sheet_id`. |

## Strumenti CLI

Tutti in `/opt/mappa-algeco/tools/`, da eseguire con `/opt/mappa-algeco/venv/bin/python`. Per Pavia: prepostazione `PROVINCIA=PAVIA`.

```bash
PY=/opt/mappa-algeco/venv/bin/python

# Stato generale del DB
$PY tools/dump_db.py
PROVINCIA=PAVIA $PY tools/dump_db.py

# Dettagli su una riga specifica (audit, pending, ...)
$PY tools/dump_db.py --eneltel 12345

# Pending in volo (dovrebbero essere ~zero in regime normale)
$PY tools/dump_db.py --pending

# Conflitti rilevati dal pull engine
$PY tools/dump_db.py --conflicts

# Anomalie del foglio (Eneltel duplicati, righe senza Eneltel)
$PY tools/dump_db.py --anomalies

# Mappatura colonne (column_registry)
$PY tools/dump_db.py --columns

# Bootstrap iniziale (idempotente) — usare solo dopo --reset o per recovery
$PY tools/migrate_to_sqlite.py
$PY tools/migrate_to_sqlite.py --reset                # rebuild da zero
$PY tools/migrate_to_sqlite.py --from-backup PATH     # da snapshot JSON

# Validatore shadow (DB vs Sheets): dry-run a mano
$PY tools/validate_shadow.py --report-dir /tmp/shadow_check

# Cleanup pending quarantined obsolete (default: > 7 giorni)
$PY tools/cleanup_quarantined.py            # dry-run
$PY tools/cleanup_quarantined.py --apply

# Archivia audit_log > 6 mesi in backup/{prov}/audit_log_archive_YYYY-MM.jsonl.gz
$PY tools/archive_audit_log.py
$PY tools/archive_audit_log.py --apply
```

## Cron schedulati

| File | Quando | Cosa fa |
|---|---|---|
| `/etc/cron.d/mappa-algeco-shadow-validator` | Ogni notte: Torino 03:00, Pavia 03:30 | Confronta DB ↔ Sheets cella per cella, salva report JSON in `data/{prov}/shadow_reports/` |
| `/etc/cron.d/mappa-algeco-audit-archive` | 1° del mese: Torino 04:00, Pavia 04:15 | Archivia `audit_log` > 180 giorni in `backup/{prov}/audit_log_archive_YYYY-MM.jsonl.gz` |

Log:
- `/var/log/mappa-shadow-torino.log` / `/var/log/mappa-shadow-pavia.log`
- `/var/log/mappa-audit-archive-torino.log` / `/var/log/mappa-audit-archive-pavia.log`

## Endpoint admin

| Endpoint | Cosa fa |
|---|---|
| `GET /api/admin/db-stats` | Stats live del DB locale (equivalente a `dump_db.py`). |
| `GET /api/admin/db-pending?state=pending` | Lista pending in volo o per stato. Parametri: `state`, `limit`. |

## Tassonomia delle modifiche

| Tipo | Esempi | Cosa scatta |
|---|---|---|
| **Soft (in-place patch)** | email, telefono, nominativo, indirizzo, civico, posizione_mis, smart_test, sigillo, codice_*, matricola_*, NOTE APP senza pattern `APP` | Patch diretta di `mappa_data.json` (~50ms). Nessun sync_loop. |
| **Strutturali** | DATA PIAN, Operatore, Latitudine/Longitudine, Comune/Localita, NOTE APP con pattern `APP`, Esito | Sync_loop completo (~7-10s): riprocesso DataFrame, ricalcolo zone/percorsi/access points, rigenera mappa_data.json e HTML. |

Definizione "soft" in [moduli/mappa_data_patcher.py:SOFT_FIELDS_DB](moduli/mappa_data_patcher.py).
Definizione "strutturale" del DataFrame in [moduli/data_loader.py:_COLONNE_STRUTTURALI](moduli/data_loader.py#L45).

## Conflict resolution

Policy: **locale vince** (`local_wins`), configurabile in `config.py:CONFLICT_POLICY`.

Quando il pull engine trova una riga con hash cambiato E c'è una pending in volo per una cella di quella riga:

- `remote_val == pending.base_value` → nessun conflitto reale, la pending procederà (drainer la pusherà).
- `remote_val != pending.base_value` → la collega ha modificato Sheets manualmente dopo la base. Audit `source='conflict_local_wins'` con dettaglio. DB locale NON aggiornato su quella cella; il drainer sovrascriverà su Sheets.

Per ispezionare conflitti storici:
```bash
$PY tools/dump_db.py --conflicts
```

## Quota Google Sheets

Limite per service account: ~60 read/min e ~60 write/min. In regime di carico utente normale:

- POST utente: **0** chiamate Sheets (DB-only).
- Sync periodico (180s): 1 read Sheets (cache shared TTL 30s evita doppi download tra `data_loader` e `pull_once`).
- Drainer (30s, se ci sono pending): 2 read + 1 write per ciclo. Coalesce per cella: bursti di N modifiche stesso campo = 1 sola write.

In totale: **~5-7 chiamate API/min** sotto carico tipico (60 era il punto di rottura del vecchio sistema).

## File chiave del refactor

| File | Ruolo |
|---|---|
| [moduli/db.py](moduli/db.py) | Schema SQLite, connection thread-local, `record_local_change`, `shadow_record_batch`, `find_eneltel_by_pod`, `canonical_column_name`, `audit`, ecc. |
| [moduli/sync_engine.py](moduli/sync_engine.py) | `pull_once` (Sheets→DB) e `drain_once` (DB→Sheets). Worker daemon. |
| [moduli/data_loader.py](moduli/data_loader.py) | `_apply_pending_to_values` (overlay pending sui raw values), `mark_data_changed`. |
| [moduli/mappa_data_patcher.py](moduli/mappa_data_patcher.py) | Patch in-place di `mappa_data.json` per soft edit. |
| [moduli/shared.py](moduli/shared.py) | `safe_batch_update_by_name` (API canonica + branch `primary`), `_route_to_db_primary`, `find_row_by_eneltel/pod` (DB-aware in primary), hook nelle funzioni appuntamento. |
| [server.py](server.py) | `trigger_sync_after_edit` (prova patch in-place → fallback sync_loop), `_shadow_db_pull` nel `_do_sincronizza_dati`, init worker drainer, endpoint admin. |
| [tools/migrate_to_sqlite.py](tools/migrate_to_sqlite.py) | Bootstrap iniziale (idempotente). |
| [tools/validate_shadow.py](tools/validate_shadow.py) | Validatore notturno DB↔Sheets. |
| [tools/dump_db.py](tools/dump_db.py) | Diagnostica CLI. |
| [tools/cleanup_quarantined.py](tools/cleanup_quarantined.py) | Pulizia pending obsolete. |
| [tools/archive_audit_log.py](tools/archive_audit_log.py) | Archiviazione mensile audit_log. |

## Troubleshooting rapido

**Problema**: pending non vengono drenate (rimangono in `pending` per >1 min).
- Verifica `journalctl -u mappa.service | grep drain` → cerca errori del drainer.
- Verifica quota Sheets: `journalctl -u mappa.service | grep 429`.
- `$PY tools/dump_db.py --pending` mostra le pending in coda.

**Problema**: modifica utente non appare sulla UI.
- `$PY tools/dump_db.py --eneltel <ENELTEL>` → controlla `pending_writes`. Se `state='pending'` → drainer in ritardo; se `state='done'` → drainer ha pushato, controlla `mappa_data.json` con `grep <ENELTEL> data/{prov}/mappa_data.json | head -1`.
- Log del sync: `journalctl -u mappa.service | grep -E "patch|sync_complete|FILONE"` per i 30s precedenti.

**Problema**: validatore notturno segnala discrepanze.
- Apri `data/{prov}/shadow_reports/shadow_diff_YYYY-MM-DD.json`. Lista i campi divergenti.
- Se `only_in_sheets`: righe esistenti su foglio ma non nel DB → eseguire `tools/migrate_to_sqlite.py` per re-importare.
- Se `field_diffs`: indagare se sono modifiche esterne ancora pending (cache 30s) o un vero bug.

**Problema**: Sheets diventa fuori sync grave o DB corrotto.
- `cat data/{prov}/state.db | sqlite3 -batch ":memory:" 'PRAGMA integrity_check;'` (richiede sqlite3 CLI).
- Rollback in modalità shadow (vedi sopra).
- Worst case: `$PY tools/migrate_to_sqlite.py --reset` ricostruisce il DB da Sheets.
