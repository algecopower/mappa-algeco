# Architettura DB locale e sincronizzazione dati

## Storia

Fino al 21/05/2026 il sistema manteneva una doppia fonte di verità: DB SQLite locale + Google Sheets con sync engine bidirezionale. A seguito di un incidente di scrittura cross-colonna, Google Sheets è stato **scollegato dal canale di scrittura**.

Da allora:

- Il **DB SQLite locale** (`data/{provincia}/state.db`, modalità WAL, **Schema v7**) è l'**unica fonte di verità operativa**.
- Le scritture dell'app non toccano mai Google Sheets: `moduli/sync_engine.py` è uno **stub no-op** ([moduli/sync_engine.py](moduli/sync_engine.py)) che al boot esegue solo `PRAGMA integrity_check`, e i nomi legacy in [`moduli/shared.py`](moduli/shared.py) (`safe_batch_update_by_name`, `get_worksheet`, ...) instradano ogni lettura/scrittura direttamente su SQLite.
- I dati esterni entrano nel DB tramite:
  1. **Import Excel/CSV manuale** dalla UI admin (`POST /api/admin/import-excel`), gestito da [`moduli/excel_importer.py`](moduli/excel_importer.py).
  2. **Auto-import orario in sola lettura** da Google Sheets (download dell'export `.xlsx` pubblico senza credenziali OAuth/gspread), gestito da [`moduli/auto_import.py`](moduli/auto_import.py) e [`tools/auto_import_filone.py`](tools/auto_import_filone.py).
- Le modifiche effettuate localmente dagli operatori sono tracciate nella coda Outbox (`pending_writes`) tramite [`moduli/outbox.py`](moduli/outbox.py) ed esportabili in formato `.xlsx` compatto per l'eventuale riallineamento verso sistemi esterni.

> **Nota storica:** la vecchia variabile `USE_DB_BACKEND` è stata rimossa dal codice; il DB SQLite locale è sempre l'unico backend attivo.

---

## Flusso operativo

```
┌──────────────────────┐  POST/PUT  ┌───────────────────┐  filone, audit_log,
│ UI Web / API Routes  │───────────▶│ DB SQLite (WAL)   │  pending_writes (Outbox)
└──────────────────────┘   <50ms    └─────────┬─────────┘
                                              │
                         ┌────────────────────┴────────────────────┐
                         ▼                                         ▼
               try_patch_inplace()                        sync_loop classico
               mappa_data.json                            (riprocessa DataFrame,
               (~50ms, soft edit)                         zone DBSCAN, percorsi OSRM,
                         │                                cache access point, ~7-10s)
                         └────────────────────┬────────────────────┘
                                              ▼
                                 SSE 'sync_complete' → Browser
```

### 1. Scrittura (utente / API)

1. La route valida i campi modificabili e chiama `safe_batch_update_by_name` ([`moduli/shared.py`](moduli/shared.py)) o gli helper diretti su [`moduli/db.py`](moduli/db.py).
2. `db.record_local_change(...)` risolve la chiave primaria `(eneltel_key, lcl)`:
   - **Dedup:** confronta il nuovo valore con il DB; se invariato, non esegue scritture.
   - Aggiorna la tabella `filone` (o `filone_extra` per colonne custom) e `last_local_change_ts`.
   - Registra una riga immutabile in `audit_log` (`source='local'`).
   - Accoda/coalesce il campo modificato in `pending_writes` (`state='pending'`) per l'Outbox.
3. `server.py:trigger_sync_after_edit()` tenta prima [`moduli/mappa_data_patcher.py`](moduli/mappa_data_patcher.py) (`try_patch_inplace`):
   - Se le modifiche toccano solo campi non strutturali (`SOFT_FIELDS_DB`: telefono, email, nominativo, indirizzo, sigillo, matricola, note senza appuntamento), patcha `data/{prov}/mappa_data.json` in ~50 ms e invia l'evento SSE `sync_complete`.
   - Per modifiche strutturali (`DATA PIAN`, `Operatore`, coordinate, `COMUNE`, note con pattern `APP`, `Esito`), risveglia il `sync_loop` completo.

### 2. Import Excel e Auto-Import orario (Sheets → DB)

[`moduli/excel_importer.py`](moduli/excel_importer.py) implementa un merge sicuro con politica **"vince il più recente" a granularità di RIGA**:

- Confronta `last_local_change_ts` (DB) con `excel_export_ts` (fornito dall'admin oppure timestamp corrente in caso di download live).
- Se una riga è stata modificata localmente **dopo** `excel_export_ts`, l'intera riga nel DB viene preservata per evitare sovrascritture accidentali del lavoro d'ufficio.
- Altrimenti applica gli aggiornamenti cella per cella, registrandoli in `audit_log` (`source='import_excel'`) e segnando come gestite eventuali `pending_writes` antecedenti all'export.

[`moduli/auto_import.py`](moduli/auto_import.py) automatizza il pull in sola lettura:

1. Scarica `https://docs.google.com/spreadsheets/d/<ID>/export?format=xlsx&gid=<GID>` per le province con `google_sheet_url` valorizzato e `auto_import_enabled: true`.
2. Calcola un **hash SHA-256 deterministico dei CRC dei file interni dello ZIP XLSX** (escludendo `docProps/`, i cui timestamp variano a ogni download Google).
3. Se l'hash coincide con l'ultima importazione salvata in `cache/auto_import_{prov}.json`, salta l'import senza toccare il DB.
4. Se il foglio è cambiato, salva lo snapshot in `cache/last_sheet_{prov}.xlsx`, invoca `import_excel(..., protect_pending_rows=True)` e risveglia la sincronizzazione della mappa (`/api/internal/trigger-sync`).

### 3. Outbox modifiche locali (DB → Excel)

[`moduli/outbox.py`](moduli/outbox.py) utilizza `pending_writes` (`state = 'pending'`) come coda delle variazioni prodotte dal gestionale:

- Coalesce per `(eneltel_key, lcl, column_name)` mantenendo il valore più recente (`_list_pending_coalesced`).
- **Scarica cambiamenti** (`GET /api/admin/outbox/excel`): genera un file `.xlsx` compatto con la colonna identificativa `POD` + le sole colonne effettivamente modificate e le celle variate evidenziate in giallo.
- **Segna gestite** (`POST /api/admin/outbox/mark-delivered`): porta le righe `pending` allo stato `done` (azzerando i badge nella UI admin).
- **Scarica completo** (`GET /api/admin/export-full-excel`): esporta l'intero snapshot del DB (`build_full_xlsx`) nel formato standard del filone.

---

## Schema DB (SQLite WAL — v7)

File per provincia: `data/{provincia}/state.db`. Definito in [`moduli/db.py`](moduli/db.py) (`SCHEMA_VERSION_CORRENTE = 7`).

### Core Filone & Audit

| Tabella | Contenuto |
|---|---|
| `filone` | Una riga per utenza/ordine. **PK composta `(eneltel_key, lcl)`** (introdotta in v2 per ordini multi-LCL). ~30 colonne tipizzate + metadati (`row_hash`, `last_local_change_ts`, `sheets_row`, `is_orphan`). |
| `filone_extra` | Colonne non standard del foglio (es. campi custom creati da admin o import Excel). Key-value `(eneltel_key, lcl, column_name) → value`. |
| `cell_comments` | Commenti/annotazioni associati a singole celle `(eneltel_key, lcl, column_name)`. |
| `pending_writes` | Coda **Outbox** delle modifiche locali (`pending`, `in_flight`, `done`, `quarantined`, `failed`). Coalesce univoco su `(eneltel_key, lcl, column_name)` per record attivi. |
| `audit_log` | Storico cronologico di ogni variazione (`ts`, `eneltel_key`, `column_name`, `old_value`, `new_value`, `source`, `user`, `detail`). |
| `column_registry` | Registro delle colonne del filone (`column_name`, `chiave_interna`, `last_index`, `last_seen_ts`). |
| `filone_anomalies` | Righe anomale rilevate in fase di import (es. Eneltel duplicato o mancante). |
| `sync_state` | Coppie chiave/valore di stato DB (`schema_version`, `last_import_ts`, `last_import_filename`, ...). |

### Moduli Territoriali, Lettere & Mail

| Tabella | Contenuto |
|---|---|
| `comuni_info` | Anagrafica comuni per provincia: ZTL, indicazioni operative, tipologie zone, PEC/email e telefoni di Comune, Polizia Locale e Ufficio Tecnico. |
| `lettere_generazioni` | Registro degli export Excel generati per spedizioni postali (`output/lettere/*.xlsx`), con hash SHA-256 e conteggi righe. |
| `lettere_righe` | Dettaglio singole righe associate a ciascuna generazione lettere. |
| `lettere_contatti` | Rubrica persistente dei destinatari (`contact_key`), inclusi recapiti aziendali, privati, PEC ed esiti contatto. |
| `lettere_aziende_generazione` | Raggruppamento per azienda/ente di una generazione, stato lavorazione ed esito risposta. |
| `lettere_invii` | Storico delle email/PEC inviate alle aziende dal modulo Gestione Lettere. |
| `lettere_classificazione_override` | Override manuali della classificazione automatica `azienda`/`privato`/`condominio`/`ente_pubblico`. |
| `mail_messages` | Archivio webmail integrata (cartelle `inbox`, `sent`, `drafts`, `scheduled`, `trash`, `archive`), allegati, thread e categorie. |
| `mail_categories` | Regole di categorizzazione intelligente email (Comuni, Aziende, Appuntamenti, ecc.). |
| `mail_templates_snippets` | Modelli completi Email/SMS/WhatsApp e frasario/snippet riutilizzabili. |

> Oltre a `state.db`, le distanze stradali OSRM per provincia sono memorizzate nella cache SQLite lazy `cache/cache_distanze_{provincia}.db` ([`moduli/cache.py`](moduli/cache.py)).

---

## Strumenti CLI

Tutti in `/opt/mappa-algeco/tools/`, da eseguire con il venv Python del progetto (`/opt/mappa-algeco/venv/bin/python`). Per selezionare una provincia diversa dal default (`TORINO`), anteporre `PROVINCIA=PAVIA` o `PROVINCIA=LECCO`.

```bash
PY=/opt/mappa-algeco/venv/bin/python

# Stato generale e PRAGMA integrity_check del DB
$PY tools/dump_db.py
PROVINCIA=PAVIA $PY tools/dump_db.py

# Dettagli su una riga specifica (campi filone, audit, pending, anomalie)
$PY tools/dump_db.py --eneltel 12345

# Diagnostica Outbox, conflitti, anomalie di import, mappatura colonne
$PY tools/dump_db.py --pending
$PY tools/dump_db.py --conflicts
$PY tools/dump_db.py --anomalies
$PY tools/dump_db.py --columns

# Auto-import read-only da Google Sheets (usato anche dal cron orario)
$PY tools/auto_import_filone.py --all
$PY tools/auto_import_filone.py --provincia TORINO --dry-run
$PY tools/auto_import_filone.py --provincia PAVIA --force

# Cleanup pending in stato `done` > 30 giorni (lo storico resta in audit_log)
$PY tools/cleanup_done_pendings.py            # dry-run
$PY tools/cleanup_done_pendings.py --apply

# Archiviazione audit_log > 180 giorni in backup/{prov}/audit_log_archive_YYYY-MM.jsonl.gz
$PY tools/archive_audit_log.py
$PY tools/archive_audit_log.py --apply

# Manutenzione e backport storici
$PY tools/cleanup_cross_province_pollution.py # pulizia righe cross-provincia (Torino)
$PY tools/import_lettere_storiche.py          # import archivio Excel lettere storici nel DB
$PY tools/import_invii_email_storici.py       # import storico invii email lettere
$PY tools/riclassifica_aziende_lettere.py     # ricalcolo euristico aziende/privati su rubrica

# Confronto A/B offline tra algoritmi di ordinamento percorsi su backup JSON
$PY tools/confronta_percorsi.py --provincia torino --max-giornate 5
```

---

## Automazioni e backup schedulati

| Job / File | Frequenza | Cosa fa |
|---|---|---|
| [`deploy/mappa-algeco-auto-import`](deploy/mappa-algeco-auto-import) (`/etc/cron.d/mappa-algeco-auto-import`) | Ogni ora al minuto `:05` | Esegue `tools/auto_import_filone.py --all` per le province con `google_sheet_url` configurato e `auto_import_enabled: true`. Log: `/var/log/mappa-auto-import.log`. |
| Audit Archive cron | 1° del mese (Torino 04:00, Pavia 04:15) | Archivia voci `audit_log` più vecchie di 180 giorni in `.jsonl.gz`. |
| Cleanup Pendings cron | 1° del mese (Torino 05:00, Pavia 05:15) | Rimuove record `pending_writes` in stato `done` da oltre 30 giorni. |
| `backup_periodico_auto()` ([`moduli/backup.py`](moduli/backup.py)) | Al primo `full_sync` del giorno | Produce sia il dump JSON giornaliero (`backup/{prov}/filone_{prov}_YYYY-MM-DD.json`) sia lo snapshot binario SQLite via `VACUUM INTO` (`backup/{prov}/state_YYYY-MM-DD.db`, rotazione ultimi 7 giorni). |
| [`deploy/mappa-algeco-restart.timer.example`](deploy/mappa-algeco-restart.timer.example) | Ogni notte alle `04:30` (+ jitter 5m) | Riavvio preventivo di `mappa-algeco-router.service` per liberare memoria accumulata dai worker. |

---

## Endpoint admin (DB, Outbox, Auto-Import, Colonne)

Tutte le API richiedono autenticazione con ruolo `admin` (e sul router `admin.*` sono esposte sotto `/api/provinces/<slug>/...`):

| Endpoint backend | Cosa fa |
|---|---|
| `GET /api/admin/db-stats` | Statistiche live del DB locale, integrità, numero righe/orfani/anomalie. |
| `GET /api/admin/db-pending` | Lista record `pending_writes` filtrabile per `state` e `limit`. |
| `GET /api/admin/db-dump/eneltel/<eneltel>` | Ispezione dettagliata di un contatore (campi `filone`, `extra`, `audit_log`, `pending_writes`). |
| `GET /api/admin/db-dump/{conflicts,columns,anomalies}` | Conflitti, registro colonne e anomalie dall'ultimo import. |
| `POST /api/admin/db-action` | Azioni manuali (`sync` per risvegliare il loop, `snapshot-db` per backup immediato via `VACUUM INTO`). |
| `GET /api/admin/outbox` | Conteggio celle/contatori in attesa di export nella coda Outbox. |
| `GET /api/admin/outbox/excel` | Scarica `.xlsx` compatto dei soli cambiamenti pendenti (evidenziati in giallo). |
| `POST /api/admin/outbox/mark-delivered` | Marca tutte le modifiche `pending` correnti come `done`. |
| `GET /api/admin/export-full-excel` | Esporta l'intero filone dal DB come file `.xlsx`. |
| `POST /api/admin/import-excel` | Upload manuale `.xlsx`/`.csv` con parametro `excel_export_ts` per il merge sicuro. |
| `POST /api/admin/auto-import-sheets` | Trigger manuale dell'import read-only dal Google Sheet configurato. |
| `GET /api/admin/auto-import-status` | Stato dell'ultimo check auto-import (`hash`, `skipped`, righe modificate) e config URL. |
| `GET/POST/PUT/DELETE /api/admin/columns*` | CRUD colonne custom del filone, rinomina sicura e bootstrap schema standard. |
| `GET/PUT /api/admin/column-mapping` | Lettura e personalizzazione della mappatura intestazioni Excel → chiavi interne. |

---

## Tassonomia delle modifiche e file chiave

| Tipo | Campi | Azione post-scrittura |
|---|---|---|
| **Soft (in-place patch)** | `email`, `telefono`, `nominativo`, `indirizzo`, `civico`, `posizione_mis`, `smart_test`, `sigillo`, `codice_*`, `matricola_*`, `note_appuntamento` senza prefisso `APP`, ecc. (`SOFT_FIELDS_DB` in [`moduli/mappa_data_patcher.py`](moduli/mappa_data_patcher.py)) | Patch diretta di `mappa_data.json` (~50 ms) + SSE `sync_complete`. |
| **Strutturali** | `data_pianificata`, `operatore`, `latitudine`/`longitudine`, `comune`/`localita`, `note_appuntamento` con prefisso `APP`, `esito` (`STRUCTURAL_FIELDS_DB`) | Riprocesso completo DataFrame, ricalcolo zone DBSCAN, percorsi OSRM e rigenerazione `mappa_data.json` (~7–10 s). |

---

## Troubleshooting rapido

**Modifica utente salvata ma non visibile sulla mappa**
- `$PY tools/dump_db.py --eneltel <ENELTEL>` → verifica il valore in `filone` e lo storico `audit_log`.
- Se il DB è corretto ma la mappa no, controlla `journalctl -u mappa-algeco-router -n 100 | grep -E "patch|sync_complete|FILONE"`.

**L'import Excel non aggiorna alcune righe**
- Controlla `excel_export_ts`: se il DB ha un `last_local_change_ts` più recente del file importato, la politica "vince il più recente" protegge le modifiche locali.
- Controlla le anomalie dell'import: `$PY tools/dump_db.py --anomalies`.

**Ripristino del DB da snapshot binario**
```bash
systemctl stop mappa-algeco-router
cp backup/torino/state_YYYY-MM-DD.db data/torino/state.db
systemctl start mappa-algeco-router
```
