# Architettura DB locale (post-disconnessione Google Sheets)

## Storia

Fino al 21/05/2026 il sistema era a doppia fonte di verità: DB SQLite locale + Google Sheets, con un sync engine bidirezionale (pull periodico Sheets→DB, drainer asincrono DB→Sheets). Il 21/05/2026 il dispatch automatico cross-colonna ha causato uno **span massivo** (numeri di telefono finiti nella colonna SIGILLO perché matchavano il pattern `\d{8,14}`).

A seguito di quell'incidente Google Sheets è stato **scollegato completamente**. Da allora:

- Il **DB SQLite locale** è l'unica fonte di verità.
- I dati esterni entrano via **import Excel manuale** (admin scarica il foglio come `.xlsx`/`.csv` e lo carica dall'UI admin).
- `moduli/sync_engine.py` è oggi uno **stub no-op** ([moduli/sync_engine.py:1-17](moduli/sync_engine.py)) che mantiene la firma delle funzioni storiche (`pull_once`, `drain_once`, `trigger_drain_now`, `start_workers`, `stop_workers`) per non rompere gli import residui.
- I nomi gspread (`safe_batch_update_by_name`, `get_worksheet`, `carica_dati_google_sheets`, ecc.) sopravvivono come **alias legacy**: le route Blueprint non sono state toccate, ma internamente tutto legge/scrive sul DB. Le chiamate gspread reali sollevano eccezioni ([moduli/shared.py:179-277](moduli/shared.py)).

## Modalità di funzionamento

Controllata da `USE_DB_BACKEND` in [config.py](config.py) (env var omonima). Nel nuovo regime esistono due soli valori utili:

| Valore | Comportamento |
|---|---|
| `primary` (default) | DB = source of truth. Route → DB → invalidate cache → mappa rigenerata. |
| `off` | Kill-switch d'emergenza: le route scrivono comunque nel DB (Sheets era già off, quindi è equivalente in pratica). |

Per rollback emergenziale al comportamento pre-disconnessione (sconsigliato, riapre l'incident del 21/05) occorre `git checkout` di un commit precedente a `5d24775`.

## Flusso operativo

```
┌──────────────────┐  POST   ┌──────────────┐
│  UI / route      │────────▶│  DB SQLite   │  filone, audit_log
└──────────────────┘ <50ms   └──────┬───────┘
                                    │
                              ┌─────┴───────────────┐
                              ▼                     ▼
                      patch in-place        sync_loop classico
                      mappa_data.json       (rigenera DataFrame +
                      (~50ms, soft edit)    percorsi + mappa, ~7-10s,
                              │             cambi strutturali)
                              ▼
                       SSE 'sync_complete' → browser
```

**Scrittura (POST utente)**

1. La route filtra i campi modificabili → chiama `safe_batch_update_by_name` ([moduli/shared.py:366](moduli/shared.py)) o helper di alto livello.
2. La funzione entra nel branch `primary` (l'unico oggi attivo) e instrada a `_route_to_db_primary`:
   - Risolve `eneltel_key` (PK del DB) via anchor (Eneltel/POD) o `sheets_row`.
   - **Dedup**: confronta col valore corrente in DB. Se uguale → no-op.
   - `db.record_local_change(...)` ([moduli/db.py:490](moduli/db.py)) aggiorna `filone` + scrive entry in `audit_log`.
3. Risposta API in ~50ms.

Nota: la coda `pending_writes` (storicamente usata dal drainer verso Sheets) viene ancora popolata da alcuni path per audit/storico, ma non viene mai drenata verso un sistema esterno. Il cron mensile `cleanup_done_pendings.py` la mantiene piccola.

**Patch in-place mappa_data.json (soft edit)**

`server.py:trigger_sync_after_edit()` tenta prima [moduli/mappa_data_patcher.py:try_patch_inplace](moduli/mappa_data_patcher.py). Se le modifiche in volo sono **tutte cosmetiche** (email, telefono, nominativo, indirizzo, posizione_misuratore, sigillo, codice_*, matricola_*, NOTE APP senza pattern `APP`, ecc.), patcha direttamente `data/{prov}/mappa_data.json` in ~50ms e invia SSE `sync_complete`. Niente sync_loop.

Per cambi **strutturali** (DATA PIAN, Operatore, Lat/Lon, COMUNE, NOTE APP con pattern `APP`, Esito) cade nel sync_loop classico (~7-10s): riprocesso DataFrame, ricalcolo zone/percorsi/access points, rigenera `mappa_data.json` e HTML.

**Import Excel manuale**

[moduli/excel_importer.py](moduli/excel_importer.py) sostituisce il vecchio pull periodico. Flusso:

1. L'admin scarica il foglio Sheets come `.xlsx` o `.csv` e lo carica dall'endpoint admin.
2. L'importer legge il file, risolve il mapping colonne (header alla riga 1 o configurato), e fa **merge cella-per-cella** col DB.
3. Politica di merge: **"vince il più recente"** a granularità RIGA. Per ogni `eneltel` confronta `last_local_change_ts` del DB con `excel_export_ts` (fornito dall'utente nel form). Se la riga è stata modificata nel programma DOPO l'export Excel, il DB vince e Excel viene ignorato per quella riga; altrimenti vincono i valori Excel, cella per cella.

Granularità a livello riga: una sola modifica locale post-export protegge l'intera riga dall'import. È la scelta più sicura per evitare di sovrascrivere lavoro recente.

## Schema DB

File: `data/{provincia}/state.db` (SQLite + WAL). Schema in [moduli/db.py](moduli/db.py).

| Tabella | Contenuto |
|---|---|
| `filone` | Una riga per contatore. **PK composta `(eneltel_key, lcl)`** (schema v2, per supportare i casi multi-LCL di Pavia). ~30 colonne tipizzate (pod, nominativo, comune, data_pianificata, operatore, lat/lon, ecc.) + meta (row_hash, last_local_change_ts, sheets_row, is_orphan). |
| `filone_extra` | Colonne del foglio non mappate alle ~30 note (es. `Mot. Rich.`, `MID`, `ZTL`). Key-value `(eneltel_key, lcl, column_name) → value`. |
| `pending_writes` | Coda storica delle scritture. Oggi rimane popolata per audit ma **non viene drenata** verso sistemi esterni. Stati: `pending` / `in_flight` / `done` / `quarantined` / `failed`. Pulita dal cron mensile. |
| `audit_log` | Storico di ogni modifica. `source` ∈ {`local`, `import_excel`, `bootstrap`, `quarantine`}. |
| `column_registry` | Mappatura nome colonna → chiave interna, aggiornata ad ogni import Excel. |
| `filone_anomalies` | Righe Excel anomale (Eneltel duplicato, riga senza Eneltel). Ricalcolato ad ogni import. |
| `sync_state` | Key/value: `schema_version`, `last_import_ts`, `last_import_filename`, ecc. |

## Strumenti CLI

Tutti in `/opt/mappa-algeco/tools/`, da eseguire con `/opt/mappa-algeco/venv/bin/python`. Per Pavia: prepostazione `PROVINCIA=PAVIA`.

```bash
PY=/opt/mappa-algeco/venv/bin/python

# Stato generale del DB
$PY tools/dump_db.py
PROVINCIA=PAVIA $PY tools/dump_db.py

# Dettagli su una riga specifica (audit, pending, ...)
$PY tools/dump_db.py --eneltel 12345

# Pending residue (dovrebbero essere ~zero in regime normale)
$PY tools/dump_db.py --pending

# Anomalie rilevate dall'ultimo import
$PY tools/dump_db.py --anomalies

# Mappatura colonne (column_registry)
$PY tools/dump_db.py --columns

# Cleanup pending in stato `done` > 30 giorni (info conservata in audit_log)
$PY tools/cleanup_done_pendings.py            # dry-run
$PY tools/cleanup_done_pendings.py --apply

# Archivia audit_log > 6 mesi in backup/{prov}/audit_log_archive_YYYY-MM.jsonl.gz
$PY tools/archive_audit_log.py
$PY tools/archive_audit_log.py --apply

# Costruisce il DB di indirizzi normalizzati (usato da verifica_indirizzi)
$PY tools/build_indir_db.py

# Confronto offline tra due percorsi pre-calcolati (debug)
$PY tools/confronta_percorsi.py
```

> **Tool rimossi post-disconnessione**: `migrate_to_sqlite.py`, `validate_shadow.py`, `cleanup_quarantined.py` non esistono più (servivano per bootstrap da Sheets e validazione DB↔Sheets).

## Cron schedulati

| File | Quando | Cosa fa |
|---|---|---|
| `/etc/cron.d/mappa-algeco-audit-archive` | 1° del mese: Torino 04:00, Pavia 04:15 | Archivia `audit_log` > 180 giorni in `backup/{prov}/audit_log_archive_YYYY-MM.jsonl.gz` |
| `/etc/cron.d/mappa-algeco-cleanup-pendings` | 1° del mese: Torino 05:00, Pavia 05:15 | Cancella `pending_writes` in stato `done` > 30 giorni |

Backup giornaliero del DB:
- `backup/{prov}/state_YYYY-MM-DD.db` (snapshot via `VACUUM INTO`, rotation: ultimi 7)
- Eseguito da `backup_periodico_auto()` (vedi [moduli/backup.py](moduli/backup.py))

Log:
- `/var/log/mappa-audit-archive-{torino,pavia}.log`
- `/var/log/mappa-cleanup-pendings-{torino,pavia}.log`

## Endpoint admin

| Endpoint | Cosa fa |
|---|---|
| `GET /api/admin/db-stats` | Stats live del DB locale + `health.warnings` se backlog ≥ 500 o `max_attempts` ≥ 10. |
| `GET /api/admin/db-pending?state=pending` | Lista pending per stato. Parametri: `state`, `limit`. |
| `POST /api/admin/import-excel` | Upload foglio Sheets esportato come Excel/CSV. Form data: file + `excel_export_ts`. |

## Tassonomia delle modifiche

| Tipo | Esempi | Cosa scatta |
|---|---|---|
| **Soft (in-place patch)** | email, telefono, nominativo, indirizzo, civico, posizione_mis, smart_test, sigillo, codice_*, matricola_*, NOTE APP senza pattern `APP` | Patch diretta di `mappa_data.json` (~50ms). Nessun sync_loop. |
| **Strutturali** | DATA PIAN, Operatore, Latitudine/Longitudine, Comune/Localita, NOTE APP con pattern `APP`, Esito | Sync_loop completo (~7-10s): riprocesso DataFrame, ricalcolo zone/percorsi/access points, rigenera `mappa_data.json` e HTML. |

Definizione "soft" in [moduli/mappa_data_patcher.py:SOFT_FIELDS_DB](moduli/mappa_data_patcher.py).
Definizione "strutturale" del DataFrame in [moduli/data_loader.py:_COLONNE_STRUTTURALI](moduli/data_loader.py).

## File chiave

| File | Ruolo |
|---|---|
| [moduli/db.py](moduli/db.py) | Schema SQLite v2, connection thread-local, `record_local_change`, `find_eneltel_by_pod`, `canonical_column_name`, `dump_filone_as_rows`, `audit`, migrations. |
| [moduli/excel_importer.py](moduli/excel_importer.py) | Import Excel/CSV con merge "vince il più recente" per riga. |
| [moduli/sync_engine.py](moduli/sync_engine.py) | **Stub post-disconnessione**. `pull_once`/`drain_once` no-op; `start_workers` esegue solo PRAGMA integrity_check al boot. |
| [moduli/data_loader.py](moduli/data_loader.py) | Legge dal DB e produce il DataFrame pandas usato dalla mappa. `mark_data_changed` invalida l'hash per forzare il riprocessing. |
| [moduli/mappa_data_patcher.py](moduli/mappa_data_patcher.py) | Patch in-place di `mappa_data.json` per soft edit. |
| [moduli/shared.py](moduli/shared.py) | API legacy compatibile gspread che instrada al DB. Helper di lookup row leggono solo dal DB. |
| [moduli/backup.py](moduli/backup.py) | Backup giornaliero JSON + snapshot DB rotativo (`esegui_backup_db`). |
| [server.py](server.py) | `trigger_sync_after_edit` (prova patch in-place → fallback sync_loop), endpoint admin (`db-stats`, `db-pending`, `import-excel`). |
| [tools/dump_db.py](tools/dump_db.py) | Diagnostica CLI. |
| [tools/cleanup_done_pendings.py](tools/cleanup_done_pendings.py) | Cancellazione mensile delle pending `done` vecchie. |
| [tools/archive_audit_log.py](tools/archive_audit_log.py) | Archiviazione mensile audit_log. |

## Troubleshooting rapido

**Problema**: modifica utente non appare sulla UI.
- `$PY tools/dump_db.py --eneltel <ENELTEL>` → controlla che il `filone` mostri il nuovo valore.
- Se il DB è aggiornato ma la mappa no: controlla `mappa_data.json` con `grep <ENELTEL> data/{prov}/mappa_data.json | head -1`. Se manca, il sync_loop / patch in-place ha fallito.
- Log: `journalctl -u mappa-algeco-router.service | grep -E "patch|sync_complete|FILONE"` per i 30s precedenti.

**Problema**: import Excel non aggiorna alcune righe.
- Verifica `excel_export_ts` inserito al momento dell'upload: se è precedente alle ultime modifiche locali, la politica "vince il più recente" protegge il DB e quelle righe vengono ignorate.
- `$PY tools/dump_db.py --eneltel <ENELTEL>` → controlla `last_local_change_ts`.
- Anomalie (Eneltel duplicati, righe senza Eneltel): `$PY tools/dump_db.py --anomalies`.

**Problema**: DB corrotto.
- `PRAGMA integrity_check` via `$PY tools/dump_db.py` (mostra anche `integrity` nella sezione stats).
- Restore da snapshot: `cp backup/{prov}/state_YYYY-MM-DD.db data/{prov}/state.db` (poi `systemctl restart mappa-algeco-router`).

**Problema**: pending residue non vengono pulite (rimangono nello stato `done` per > 30 giorni).
- Verifica che il cron `cleanup-pendings` sia attivo: `cat /etc/cron.d/mappa-algeco-cleanup-pendings`.
- Esegui manualmente: `$PY tools/cleanup_done_pendings.py --apply`.

