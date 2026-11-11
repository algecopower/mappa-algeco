# Gestionale Mappa Contatori — Documentazione Tecnica e Funzionale

> **Versione:** 4.0 (Architettura Multi-Tenant SQLite-First, Outbox & Auto-Import Read-Only)  
> **Ultimo aggiornamento:** 2026-11-11  
> **Progetto:** Mappa Algeco — Piattaforma multi-provincia per pianificazione, routing OSRM e comunicazioni operative nella sostituzione contatori elettrici.

---

## Indice

### Parte 1 — Panoramica e Uso
1. [Cos'è questo programma](#1-cosè-questo-programma)
2. [Requisiti di sistema e file di configurazione](#2-requisiti-di-sistema-e-file-di-configurazione)
3. [Avvio del server](#3-avvio-del-server)
4. [Login e autenticazione](#4-login-e-autenticazione)
5. [La mappa interattiva e gli strumenti operativi](#5-la-mappa-interattiva-e-gli-strumenti-operativi)
6. [Moduli gestionali integrati](#6-moduli-gestionali-integrati)
7. [Import dati esterni, BOXBEAT e Outbox](#7-import-dati-esterni-boxbeat-e-outbox)
8. [FAQ rapide](#8-faq-rapide)

### Parte 2 — Guida Sviluppatore
9. [Architettura del sistema](#9-architettura-del-sistema)
10. [Struttura del progetto](#10-struttura-del-progetto)
11. [Flusso dati ed eventi](#11-flusso-dati-ed-eventi)
12. [Moduli (`moduli/`) — Riferimento](#12-moduli-moduli--riferimento)
13. [Blueprint e API REST (`routes/`, `server.py`, `router.py`)](#13-blueprint-e-api-rest-routes-serverpy-routerpy)
14. [Configurazione e variabili d'ambiente](#14-configurazione-e-variabili-dambiente)
15. [Cache e performance](#15-cache-e-performance)
16. [Algoritmo di ottimizzazione percorsi, Access Points e Clustering](#16-algoritmo-di-ottimizzazione-percorsi-access-points-e-clustering)
17. [Test, CI/CD e Troubleshooting](#17-test-cicd-e-troubleshooting)

---

# PARTE 1 — PANORAMICA E USO

---

## 1. Cos'è questo programma

**Mappa Algeco** è un gestionale web **multi-provincia** in Python/Flask dedicato al coordinamento operativo della sostituzione di contatori elettrici (Enel / E-Distribuzione).

### Capacità principali
- **Mappa operativa Leaflet + OSRM:** clustering geografico delle zone (DBSCAN), percorsi giornalieri ottimizzati per tecnico con ancore temporali sugli appuntamenti, snapping stradale degli Access Point, stampa ad alta risoluzione ed etichette ENELTEL anti-sovrapposizione.
- **Pianificazione massiva & Vista Excel (`/filone`):** selezione a rettangolo/lasso sulla mappa e tabella multi-tab sincronizzata in tempo reale con il database locale.
- **Database SQLite locale per provincia (`Schema v7`):** unica fonte di verità per tutte le scritture dal 21/05/2026 ([`DB_LOCALE_README.md`](DB_LOCALE_README.md)), con import Excel protetto, auto-import orario read-only da Google Sheets ed export **Outbox** compatto delle sole celle modificate.
- **Suite documentale e multicanale:** parsing PDF E-Distribuzione (`/distinte`), archivio lettere postali & invii PEC aziendali (`/lettere`), webmail completa SMTP/IMAP (`/mail`), rubrica Comuni/ZTL (`/comuni`), **SMS Gateway** integrato via smartphone Android/webhook, editor template e frasario (`/templates`).
- **Monitoraggio, agenda e multi-tenancy:** dashboard KPI (`/dashboard`), reportistica (`/report`), calendario/bacheca promemoria (`/calendario`), elenco appuntamenti (`/appuntamenti`) e pannello di amministrazione multi-provincia (`admin.*`).

### Province configurate di default ([`data/provinces.json`](data/provinces.json))

| Provincia | Porta backend | Deposito | Orario giornata | Sottodominio |
|---|---|---|---|---|
| **Torino** (`TORINO`) | `5001` | `DEPOSITO VOLPIANO` (`45.1964682, 7.7888303`) | `09:00` – `17:00` (`540`–`1020`) | `torino.mappa-algeco.com` |
| **Pavia** (`PAVIA`) | `5002` | `DEPOSITO PAVIA` (`45.2221001, 9.2254115`) | `08:00` – `17:00` (`480`–`1020`) | `pavia.mappa-algeco.com` |
| **Lecco** (`LECCO`) | `5003` | `DEPOSITO LECCO` (`45.7727912, 9.3257548`) | `08:00` – `17:00` (`480`–`1020`) | `lecco.mappa-algeco.com` |

Ulteriori province possono essere create e avviate direttamente dall'interfaccia `admin.*`.

---

## 2. Requisiti di sistema e file di configurazione

### Software
| Software | Versione / Note | Scopo |
|---|---|---|
| **Python** | `3.12` (testato su 3.12 in CI) | Backend Flask, Gunicorn, CLI e pipeline dati |
| **OSRM** | `osrm-routed` (`v5.27.1` nativo `--mmap` o Docker) su porta `5000` | Matrici distanze/durate stradali reali, `/trip`, `/route`, `/nearest` |
| **Cloudflare Tunnel** (`cloudflared`) | Opzionale in locale, attivo in produzione | Reverse tunnel TLS wildcard `*.mappa-algeco.com` → `:5050` |

### File operativi e di configurazione

| File | In Git | Descrizione |
|---|---|---|
| `data/provinces.json` | Sì | Registry province (deposito, operatori, colori, orari, LCL disabilitati, `google_sheet_url`, porte, sottodomini) |
| `users.json` | No (`.gitignore`) | Utenti, hash Argon2id, ruoli (`admin`/`user`) e province autorizzate |
| `.flask_secret_key` | No (`.gitignore`) | Chiave firma cookie di sessione (auto-generata al primo avvio se assente) |
| `data/{prov}/state.db` | No (`.gitignore`) | Database SQLite WAL della provincia (Schema v7) |
| `data/{prov}/settings.json` | No (`.gitignore`) | Override runtime salvati dalla UI admin/impostazioni |
| `data/{prov}/mappa_data.json` | No (`.gitignore`) | Payload JSON letto dal frontend Leaflet e aggiornato in-place/full-sync |
| `data/smtp_settings.json` | No (`.gitignore`) | Credenziali SMTP/IMAP per provincia |
| `data/{prov}/sms_settings.json` | No (`.gitignore`) | Configurazione SMS Gateway (Android App / Webhook) |

---

## 3. Avvio del server

```bash
PY=/opt/mappa-algeco/venv/bin/python

# Sviluppo locale (singola provincia standalone)
$PY server.py --torino --skip-osrm-check
$PY server.py --pavia
PROVINCIA=LECCO FLASK_PORT=5003 $PY server.py

# Multi-tenant completo (router + worker Gunicorn per tutte le province)
ROUTER_PORT=5050 $PY router.py
```

In produzione il stack viene gestito da `systemd` (`mappa-algeco-router.service`, `osrm-server.service`, `cloudflared-tunnel.service`, `mappa-algeco-restart.timer`). Vedi [`deploy/README.md`](deploy/README.md) e [`GUIDA_DEPLOY_VPS.md`](GUIDA_DEPLOY_VPS.md).

---

## 4. Login e autenticazione

- Modulo [`moduli/auth.py`](moduli/auth.py): hashing password **Argon2id** (`argon2-cffi`) con verifica e **upgrade trasparente automatico** degli eventuali hash legacy PBKDF2-SHA256 al login.
- **Ruoli:** `admin` e `user`.
- **Rate limiting:** dopo **5 tentativi falliti**, `POST /api/login` blocca l'IP per **60 secondi** (`429`).
- **Sessione e CSRF:** cookie `HttpOnly`, `SameSite=Lax`, durata **24 ore**, `Secure` se `FLASK_ENV=production`. Tutte le mutazioni autenticate richiedono match `Origin`/`Referer` + doppio token CSRF (`cookie csrf_token` ↔ header `X-CSRF-Token`).
- Gestione utenti completa via Web UI (`admin.*` → **🔑 Utenti**) e CLI [`gestione_utenti.py`](gestione_utenti.py). Vedi [`GUIDA_ACCOUNT.md`](GUIDA_ACCOUNT.md).

---

## 5. La mappa interattiva e gli strumenti operativi

Dettaglio passo-passo in [`GUIDA_UTILIZZO.md`](GUIDA_UTILIZZO.md):
- **Viste principali:** *Zone* (poligoni/riepiloghi DBSCAN), *Comuni*, *Dettaglio per data*, *Percorso Operatore* (singolo o **multi-operatore** con tracce stradali OSRM).
- **Modalità Pianificazione (`📋 Pianifica`):** filtri granulari su schedulabili/pianificati e selezione singola, rettangolo (`▭`) o lasso (`✂`) per assegnazione massiva via `POST /api/contatori/bulk-pianifica`.
- **Ricerca Utente Avanzata:** ricerca su tutto il DB (Nominativo, POD, Eneltel, Comune, Via, Telefono) con **ricerca multipla** a token per elenchi di POD ed Eneltel.
- **Pin multipli & Badge orari:** supporto a più contatori fissati simultaneamente (`pinnedFloatingToolbar`) e toggle **Badge orari per tutti** (`🕒`).
- **Stampa percorso:** pannello anteprima dedicato ad alta risoluzione per stampa mappa e foglio tappe.

---

## 6. Moduli gestionali integrati

Accessibili dal **Menu Gestionale & Moduli** (pulsante `⊞` sulla mappa, con ricerca e preferiti rapidi) o direttamente via URL:

| Route | Modulo | Funzionalità principali |
|---|---|---|
| `/` | Mappa operativa | Viste geografiche, routing TSP OSRM, pianificazione bulk, popup edit, stampa |
| `/filone` | Vista Excel Filone | Tabella multi-tab su `state.db`, filtri/ordinamento, editing inline, import/append |
| `/distinte` | PDF E-Distribuzione | Parsing DistintaBase & Moduli Maxy, WhatsApp, Email SMTP, **SMS Gateway** singolo/multiplo, diff/overwrite DB |
| `/lettere` | Gestione Lettere | Archivio generazioni Excel postali, rilevamento Aziende/Enti vs Privati + override, bulk email/PEC aziende, bulk SMS privati |
| `/mail` | Gestione Mail (`/webmail`) | Client SMTP/IMAP completo, sincronizzazione inbox, bozze, invii programmati, categorie automatiche, allegati standard |
| `/comuni` | Gestione Comuni | Anagrafica comuni, ZTL, PEC/email Polizia Locale & Ufficio Tecnico, invio comunicazioni in bulk |
| `/templates` | Modelli & Template | Editor centralizzato per template completi (Email/WA/SMS), frasario snippet e firma/recapiti ufficio |
| `/calendario` | Calendario Attività | Agenda condivisa per provincia, promemoria BOXBEAT e checklist giornaliera (`La mia Giornata`) |
| `/appuntamenti` | Gestione Appuntamenti | Vista tabellare appuntamenti con filtri ed export `.xlsx` |
| `/dashboard` | Dashboard Operativa | KPI avanzamento commessa, LCL, appuntamenti e km operatori |
| `/report` | Reportistica Operatori | Analisi storica multi-tab (`Panoramica`, `Dettaglio Operatori`, `Esiti`, `Confronto`) ed export Excel |
| `admin.*` | Router Admin UI | Ciclo vita worker Gunicorn, Wizard 3-step, Import/Auto-Import/Outbox Excel, CRUD colonne, SMTP/SMS, Utenti, Log `journalctl` |

---

## 7. Import dati esterni, BOXBEAT e Outbox

- **Installato E-Distribuzione (BOXBEAT):** caricabile dalla mappa via `POST /api/upload-BOXBEAT` (`.xlsx`/`.xls`/`.csv`, header riga 3); associa il `POD` e aggiorna `Operatore`, `Data Installazione` ed `Esito = ESITO OK`.
- **Import FILONE manuale (`POST /api/admin/import-excel`):** carica fogli Excel/CSV applicando il merge "vince il più recente" a granularità di riga (`last_local_change_ts` vs `excel_export_ts`).
- **Auto-Import orario read-only ([`moduli/auto_import.py`](moduli/auto_import.py)):** cron `:05` (`tools/auto_import_filone.py --all`) o pulsante `⚡ Da Sheets` in `admin.*`; scarica l'export `.xlsx` pubblico da `google_sheet_url`, confronta SHA-256 deterministico sui CRC (escludendo `docProps/`) e importa solo in caso di modifiche effettive proteggendo le righe modificate localmente.
- **Outbox modifica locale ([`moduli/outbox.py`](moduli/outbox.py)):** esporta un `.xlsx` compatto (`POD` + sole celle modificate evidenziate in giallo) tramite `GET /api/admin/outbox/excel` e marca le modifiche come gestite (`POST /api/admin/outbox/mark-delivered`).

---

## 8. FAQ rapide

- **Reset password da CLI:**
  ```bash
  /opt/mappa-algeco/venv/bin/python gestione_utenti.py --reset nomeutente
  ```
  *(Genera automaticamente una nuova password casuale; per impostare una password scelta manualmente o cambiare ruolo usa la scheda `🔑 Utenti` su `https://admin.<dominio>/`).*
- **OSRM non raggiungibile in dev:** verifica `curl "http://localhost:5000/route/v1/driving/7.68,45.07;7.78,45.19"` oppure avvia in locale con `--skip-osrm-check` (fallback su stima Haversine/sweep angolare).
- **Rate limit login:** attendi **60 secondi** dopo 5 tentativi errati consecutivi.

---

# PARTE 2 — GUIDA SVILUPPATORE

---

## 9. Architettura del sistema

```
                        Cloudflare Tunnel (*.mappa-algeco.com → :5050)
                                              │
                                              ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│  router.py (:5050, Processo supervisore mappa-algeco-router.service)             │
│  • Reverse proxy streaming-aware per Host header (supporto SSE `/api/events`)    │
│  • Validazione auth delegata (/api/user con cache 30s) + CSRF guard              │
│  • ProcessManager + Watchdog + UI Admin (`templates_router/admin.html`)          │
└───────────┬─────────────────────────────┬────────────────────────────┬───────────┘
            │                             │                            │
            ▼                             ▼                            ▼
  Gunicorn wsgi:app             Gunicorn wsgi:app            Gunicorn wsgi:app
  PROVINCIA=TORINO :5001        PROVINCIA=PAVIA :5002        PROVINCIA=LECCO :5003
  (1 worker × 32 gthread)       (1 worker × 32 gthread)      (1 worker × 32 gthread)
            │
            ├─ 14 Blueprint Flask (`routes/*.py`) + Routes core/admin (`server.py`)
            ├─ Loop background (ogni 5s):
            │    • Risveglio immediato per `sync_loop` o `try_patch_inplace()` (~50ms)
            │    • Scheduler ogni 15s: `mail_manager.process_scheduled_queue()`
            │    • Snapshot DB giornaliero + backup JSON (`backup_periodico_auto()`)
            ├─ SQLite WAL `data/{prov}/state.db` (Schema v7, PK `(eneltel_key, lcl)`)
            └─ OSRM `:5000` (Table, Trip, Route, Nearest) + Cache SQLite `cache_distanze_{prov}.db`
```

### Principi architetturali
1. **Isolamento multi-tenant per processo:** ogni provincia ha il proprio processo Gunicorn (`PROVINCIA=<SLUG>`), database `state.db`, cache distanze, settings e archivi `data/{prov}/`, `backup/{prov}/`.
2. **DB-first + API legacy compatibile:** `moduli/shared.py` espone l'interfaccia storica compatibile con i blueprint originali instradando tutte le letture e scritture al DB SQLite locale (`moduli/db.py`). `moduli/sync_engine.py` è uno stub post-disconnessione Sheets.
3. **Dual-speed UI sync:** le modifiche non strutturali (`SOFT_FIELDS_DB`) applicano patch JSON in-place su `mappa_data.json` (~50 ms) e inviano `sync_complete` via SSE; le modifiche strutturali scatenano la rigenerazione completa di DataFrame, cluster DBSCAN, rotte OSRM e `mappa_data.json`.
4. **HTML rendering on-demand + JSON statici:** `mappa_html.py` non scrive più `mappa_{prov}.html` su disco; salva `config.json` e `mappa_data.json`, mentre `GET /` renderizza `templates/mappa.html` da cache leggera in memoria.

---

## 10. Struttura del progetto

```
/opt/mappa-algeco/
├── server.py                        # App Flask per provincia, sicurezza CSRF/auth, SSE, loop sync, admin DB API
├── router.py                        # Reverse proxy by-Host, ProcessManager, admin.* API & dashboard
├── wsgi.py                          # Entry point WSGI per Gunicorn
├── gunicorn.conf.py                 # 1 worker gthread × 32 thread (GUNICORN_THREADS), timeout 600s, max_requests 1000
├── config.py                        # Caricamento data/provinces.json, settings.json override, mapping colonne, costanti
├── gestione_utenti.py               # CLI gestione utenti in users.json
├── genera_excel_lettere.py          # CLI generazione Excel multi-foglio lettere (MONOFASE, EMME2, SEMIDIRETTI, ...)
├── pyproject.toml                   # Configurazione Ruff (F, E9, W6; py312; max line 100) e Pytest
├── requirements.txt                 # Dipendenze runtime
├── requirements-dev.txt             # Dipendenze sviluppo/CI (pytest, ruff)
├── .github/workflows/test.yml       # CI GitHub Actions (ruff check . + pytest -v su push/PR main)
│
├── moduli/                          # Logica di dominio (31 moduli)
│   ├── __init__.py
│   ├── access_points.py             # Risoluzione access point stradali via OSRM /nearest + override/undo
│   ├── appuntamenti_core.py         # Parsing unificato rilevazione appuntamenti (^APP)
│   ├── auth.py                      # Hashing Argon2id + fallback PBKDF2-SHA256, ruoli, ACL per provincia
│   ├── auto_import.py               # Pull read-only XLSX da Google Sheets con dedup ZIP CRC SHA-256
│   ├── backup.py                    # Backup giornaliero JSON + snapshot DB SQLite (VACUUM INTO, ultimi 7)
│   ├── bulk_contatori_email.py      # Raggruppamento contatori aziendali, allegati Excel dinamici e note invio
│   ├── cache.py                     # Cache lazy SQLite (cache/cache_distanze_{prov}.db) distanze & hash MD5
│   ├── calendario.py                # Attività calendario, promemoria BOXBEAT, registry appuntamenti
│   ├── comuni_db.py                 # Lookup CAP/Provincia su comuni-json con cache 30gg
│   ├── comuni_manager.py            # CRUD rubrica comuni_info, ZTL e bulk mail/PEC ai Comuni
│   ├── data_loader.py               # Lettura DB SQLite → DataFrame normalizzato con filtri e cache TTL
│   ├── db.py                        # Core SQLite WAL v7 (18 tabelle), migrations, record_local_change, audit
│   ├── distanze.py                  # Calcolo matrici OSRM /table, rotte /route e fallback Haversine × 1.35
│   ├── distinte_manager.py          # Parser PDF E-Distribuzione (DistintaBase & Modulo Maxy) + telefoni/WA
│   ├── excel_importer.py            # Import Excel/CSV filone con merge sicuro "vince il più recente"
│   ├── fasce_orarie.py              # 7 fasce orarie ('8/11'..'14/17'), consolidamento indirizzi
│   ├── gestionale.py                # CSS/JS/HTML embedded per popup editing e upload BOXBEAT
│   ├── lettere.py                   # Archivio lettere, rubrica contatti, aziende/privati, storico invii
│   ├── mail_manager.py              # Storage webmail SQLite, sincronizzazione IMAP, categorie, queue schedulata
│   ├── mappa_data_patcher.py        # Patch JSON in-place per soft edit (~50 ms)
│   ├── mappa_html.py                # Preparazione mappa_data.json, config.json e render template mappa
│   ├── outbox.py                    # Export modifiche pending (xlsx giallo compatto) + mark_delivered
│   ├── percorso.py                  # TSP via OSRM /trip con ancore temporali e fallback sweep angolare
│   ├── rilevamento_aziende.py       # Heuristica aziende/condomini/enti pubblici vs privati
│   ├── safe_json.py                 # Letture/scritture JSON thread-safe e atomiche (temp + os.replace)
│   ├── shared.py                    # Layer compatibilità API legacy → SQLite DB + callback registry
│   ├── sms_gateway.py               # Client Android SMS Gateway (locale/cloud) + webhook + contatore GSM-7/UCS-2
│   ├── smtp.py                      # Invio SMTP (465 SSL / 587 STARTTLS, retry), salvataggio IMAP Sent, CCN provincia
│   ├── sync_engine.py               # Stub no-op post-disconnessione Sheets (check integrità DB al boot)
│   ├── workflow.py                  # Configurazione checklist "La mia Giornata" per provincia
│   └── zone_geografiche.py          # Clustering geografico DBSCAN dei comuni e calendario zone
│
├── routes/                          # 14 Blueprint Flask montati da server.py
│   ├── access_points.py             # GET/POST/DELETE/undo/snap /api/access-point/<eneltel>
│   ├── appuntamenti.py              # /appuntamenti + JSON + export Excel
│   ├── bacheca.py                   # /api/bacheca/{promemoria,tutti}
│   ├── calendario.py                # /calendario, /api/calendario*, /api/workflow*, /api/boxbeat/*
│   ├── comuni.py                    # /comuni + rubrica + import/export + bulk email
│   ├── contatori.py                 # /api/contatore/<eneltel>*, /api/contatori/*, /api/filone*, orari lettere
│   ├── dashboard.py                 # /dashboard, /api/dashboard
│   ├── distinte.py                  # /distinte, /whatsapp, parse PDF, WA/Email/SMS bulk, appuntamenti/note
│   ├── lettere.py                   # /lettere, generazioni, aziende/privati, riclassifica, bulk email
│   ├── mail.py                      # /mail (/webmail, /gestione-mail), IMAP sync, send/draft, categorie, template
│   ├── report.py                    # /report, /api/report, /api/report/export
│   ├── sigilli.py                   # /api/sigilli/* e /api/matricole/*
│   ├── sync.py                      # /api/sync*, /api/filone/sync, /api/backup/*, /api/upload-BOXBEAT
│   └── templates.py                 # /templates + API CRUD modelli/snippet/impostazioni firma
│
├── tools/                           # 9 CLI di servizio e documentazione SMS
│   ├── README_SMS_GATEWAY.md
│   ├── archive_audit_log.py         # Archivia audit_log > N giorni in .jsonl.gz
│   ├── auto_import_filone.py        # CLI auto-import cron (--all / --provincia / --force / --dry-run)
│   ├── cleanup_cross_province_pollution.py
│   ├── cleanup_done_pendings.py     # Pulizia pending_writes in stato done > 30 giorni
│   ├── confronta_percorsi.py        # Benchmark A/B offline ordinamento percorsi su backup JSON
│   ├── dump_db.py                   # Ispezione CLI state.db (--eneltel, --pending, --conflicts, --anomalies, --columns)
│   ├── import_invii_email_storici.py
│   ├── import_lettere_storiche.py
│   └── riclassifica_aziende_lettere.py
│
├── deploy/                          # Template systemd, cloudflared, cron e guida deploy
├── templates/                       # 12 template HTML Jinja2 per provincia
├── templates_router/                # admin.html (dashboard multi-provincia del router)
├── static/                          # CSS/JS/asset condivisi (mappa, mail, lettere, comuni, csrf, tour, modali)
├── data/                            # provinces.json + sottocartelle isolate data/{prov}/
└── tests/                           # Suite pytest (conftest.py + 32 file test_*.py)
```

---

## 11. Flusso dati ed eventi

### Ciclo modifica → UI in tempo reale

1. Il client invia una mutazione (`POST`/`PUT`/`DELETE`) con header `X-CSRF-Token`.
2. La route valida i parametri ed esegue la scrittura sul DB SQLite locale (`moduli/db.py:record_local_change`), aggiornando `filone`/`filone_extra`, `last_local_change_ts`, `audit_log` e la coda `pending_writes` (Outbox).
3. `server.py:trigger_sync_after_edit()` verifica i campi toccati tramite [`moduli/mappa_data_patcher.py`](moduli/mappa_data_patcher.py):
   - **Se tutti i campi appartengono a `SOFT_FIELDS_DB`**: patcha `data/{prov}/mappa_data.json` in ~50 ms e chiama `sse_notify_all('sync_complete', {'source': 'inplace_patch', ...})`.
   - **Se almeno un campo è in `STRUCTURAL_FIELDS_DB`** (es. `data_pianificata`, `operatore`, coordinate, `comune`, `esito`, note `APP`): imposta `_sync_needed` per risvegliare immediatamente il `background_sync_loop`.
4. Durante il `sync_loop`: `data_loader.py` legge SQLite → `access_points.py` risolve eventuali access point mancanti → `distanze.py` + `percorso.py` aggiornano percorsi con cache hash MD5 → `zone_geografiche.py` calcola le zone → `mappa_html.py` scrive `mappa_data.json` e `config.json` → `sse_notify_all('sync_complete')` → `backup_periodico_auto()`.

---

## 12. Moduli (`moduli/`) — Riferimento

| Modulo | Responsabilità tecnica |
|---|---|
| `db.py` | Schema SQLite WAL **v7** (18 tabelle, PK `(eneltel_key, lcl)`), pool connessioni thread-local, migrazioni v1→v7, `record_local_change`, `upsert_filone_row`, `dump_filone_as_rows`, `canonical_column_name`. |
| `shared.py` | Bridge di compatibilità legacy gspread → DB SQLite, cache letture con invalidazione, helper lookup anchor (`eneltel`, `pod`, `sheets_row`), registry callback con `server.py`. |
| `data_loader.py` | Query unificata `filone` + `filone_extra`, normalizzazione fuzzy colonne (`trova_colonna`), esclusione `ESITO OK` / coordinate invalide / LCL e operatori disabilitati / date fuori finestra. |
| `excel_importer.py` | Parser `.xlsx`/`.xls`/`.csv`, creazione automatica colonne mancanti in `column_registry`, merge cella-per-cella protetto per riga (`last_local_change_ts` vs `excel_export_ts`). |
| `auto_import.py` | Scaricamento XLSX da link Google Sheets (`export?format=xlsx&gid=...`), calcolo hash SHA-256 deterministico sui CRC dello ZIP (escluso `docProps/`), invocazione sicura importer. |
| `outbox.py` | Aggregazione (`_list_pending_coalesced`) delle modifiche `pending`, export Excel compatto giallo (`build_outbox_xlsx`), export completo (`build_full_xlsx`), marca consegnate (`mark_delivered`). |
| `mappa_data_patcher.py` | Patch atomica su `mappa_data.json` per campi non strutturali (`SOFT_FIELDS_DB`). |
| `access_points.py` | Calcolo access point stradali via OSRM `/nearest` (`OSRM_NEAREST_NUMBER=15`, `SOGLIA_SIMILARITY=0.85`, `DISTANZA_MAX_SNAP_M=80.0m`), override manuali in `data/{prov}/access_overrides.json` con undo stack. |
| `distanze.py` | Client HTTP OSRM `/table` (blocchi fino a 80 punti) e `/route`, fallback Haversine × `1.35`, orchestrazione cache con `genera_report_distanze_con_cache`. |
| `percorso.py` | Ordinamento tappe per giornata/operatore: `/trip` roundtrip oppure segmentazione a **ancore temporali** tra appuntamenti con fallback **sweep angolare**. |
| `appuntamenti_core.py` | Matching rigoroso `^\W*app\b` (case-insensitive) condiviso tra mappa, dashboard, report e lettere. |
| `fasce_orarie.py` | 7 fasce (`'8/11'`, `'9/12'`, `'10/13'`, `'11/14'`, `'12/15'`, `'13/16'`, `'14/17'`), assegnazione fissa (`TAPPE_PER_FASCIA=4`) o bilanciata e consolidamento indirizzi aziendali. |
| `zone_geografiche.py` | Centroidi comunali, clustering DBSCAN con matrice distanze km precomputata (`dimensione_cella_km=8`, `eps_km = dimensione_cella_km * 0.625`), split zone estese e merge zone piccole (`min_contatori_zona=15`), calendario per zona. |
| `cache.py` | Wrapper dict-like sopra SQLite (`cache/cache_distanze_{prov}.db`, tabelle `distanze` e `hashes`) con migrazione automatica da eventuali vecchi `.json`. |
| `mappa_html.py` | Costruzione payload `mappa_data.json` + `config.json`, memorizzazione inputs leggeri per il render Jinja dinamico (`render_mappa_corrente()`). |
| `gestionale.py` | Componenti UI embedded nella mappa per pannello modifica popup/full, BOXBEAT upload, sigilli/matricole. |
| `distinte_manager.py` | Estrazione dati da PDF **DistintaBase** e **Modulo Maxy** (`pdfplumber`), classificazione numeri cellulari italiani (`320–399`), confronto numeri tollerando lo `0` iniziale e helper WhatsApp. |
| `bulk_contatori_email.py` | Raggruppamento contatori per destinatario/azienda, pluralizzazione testi, allegati Excel dinamici e scrittura automatica note (`mandata mail` / `mandato SMS` / `mandato wa`) su `state.db`. |
| `lettere.py` | Gestione archivio generazioni lettere, chiavi contatto (`POD:`/`NOPOD:`), rubrica contatti/PEC, aziende per generazione, statistiche e invii email. |
| `rilevamento_aziende.py` | Classificazione `azienda` / `condominio` / `ente_pubblico` / `privato` tramite regex suffissi societari, prefissi ente e analisi contestuale della preposizione `'DI'` nelle note. |
| `mail_manager.py` | Webmail SQLite (`mail_messages`, `mail_categories`, `mail_templates_snippets`), fetch IMAP, invio programmato in coda (`process_scheduled_queue`), categorizzazione automatica, rubrica unificata. |
| `smtp.py` | Client SMTP centralizzato (SSL `465` / STARTTLS `587`, retry errori 4xx/transitori), salvataggio automatico in `Sent` via IMAP, supporto CCN automatico email provincia. |
| `sms_gateway.py` | Invio SMS via Android SMS Gateway (Server Locale o Cloud Capcom6) o HTTP Webhook, normalizzazione `+39` E.164, conteggio caratteri/segmenti GSM-7 (`160`) vs UCS-2 (`70`/`67`). |
| `comuni_db.py` | Lookup locale CAP/Provincia italiana basato su dataset `comuni-json` (`matteocontrini/comuni-json`), cache in `cache/cache_comuni_italiani.json` (TTL 30 giorni). |
| `comuni_manager.py` | Gestione tabella `comuni_info`, import/export `.xlsx`, anteprima e invio bulk email/PEC a Comuni, Polizia Locale e Uffici Tecnici. |
| `calendario.py` | CRUD attività giornaliere (`data/{prov}/calendario.json`), promemoria BOXBEAT e pulizia automatica completati. |
| `workflow.py` | Gestione checklist operativa personalizzata (`data/{prov}/user_workflows.json`). |
| `backup.py` | Dump giornaliero `filone_{prov}_YYYY-MM-DD.json` e snapshot SQLite binario `state_YYYY-MM-DD.db` via `VACUUM INTO` con rotazione ultimi 7 giorni. |
| `auth.py` | Autenticazione Argon2id + rehash PBKDF2 legacy, ruoli `['admin', 'user']`, controllo province da `data/provinces.json`, sicurezza cookie. |
| `safe_json.py` | I/O JSON thread-safe per-file con scrittura atomica (`NamedTemporaryFile` + `os.replace`). |
| `sync_engine.py` | Stub post-disconnessione Sheets (nessuna connessione Google API; verifica `integrity_check()` all'avvio). |

---

## 13. Blueprint e API REST (`routes/`, `server.py`, `router.py`)

Salvo diversa indicazione (`POST /api/login`, `GET /api/config`), tutte le API richiedono sessione autenticata (`@login_required`) e tutte le mutazioni richiedono header `X-CSRF-Token`.

### Auth, Mappa, Impostazioni & SSE (`server.py`)
| Metodo | Endpoint | Descrizione |
|---|---|---|
| `GET` / `POST` | `/login`, `/api/login`, `/api/logout`, `/logout`, `/api/user` | Autenticazione, sessione corrente (username, ruolo, province) |
| `GET` | `/`, `/filone`, `/api/config`, `/data/<path>` | Mappa HTML, vista Filone, config provincia, JSON protetti |
| `GET` | `/api/events` | Stream Server-Sent Events (`connected`, `sync_complete`, keepalive 10s) |
| `POST` | `/api/route_geometry` | Calcolo geometria percorso stradale OSRM tra coordinate |
| `GET` / `PUT` | `/api/settings` | Lettura/salvataggio override in `data/{prov}/settings.json` (`@admin_required` per `PUT`) |
| `POST` / `GET` | `/api/internal/trigger-sync`, `/api/internal/auto-import` | Endpoint interni solo localhost usati da CLI/cron |

### Contatori, Filone & Access Points (`routes/contatori.py`, `routes/access_points.py`, `routes/sigilli.py`)
| Metodo | Endpoint | Descrizione |
|---|---|---|
| `GET` / `PUT` | `/api/contatore/<eneltel>` | Dettaglio e modifica singolo contatore su `state.db` |
| `POST` | `/api/contatori/bulk-pianifica` | Pianificazione massiva data/operatore dalla mappa |
| `POST` | `/api/contatori/excel-assegnati`, `/api/contatori/check-inactive-lcl` | Export Excel assegnazioni e verifica LCL disabilitate |
| `POST` | `/api/contatore/<eneltel>/{appuntamento,rimuovi-appuntamento}` | Fissazione/rimozione slot appuntamento |
| `GET` | `/api/filone`, `/api/filone/log` | Dati completi vista Excel e log variazioni |
| `GET`/`PUT` | `/api/filone/notes`, `/api/filone/row/<row_num>` | Commenti celle e aggiornamento riga |
| `POST` | `/api/filone/bulk`, `/api/filone/append` | Modifiche massive ed aggiunta nuova riga |
| `POST` / `GET` | `/api/genera-orari-lettere`, `/api/download-excel-lettere/<filename>` | Calcolo fasce e download Excel generato |
| `GET`/`POST`/`DELETE` | `/api/access-point/<eneltel>`, `/undo`, `/snap` | Letture, override manuali, undo e snap OSRM dell'access point |
| `GET` / `PUT` | `/api/sigilli/pending`, `/api/sigillo/<eneltel>`, `/api/matricole/pending`, `/api/matricola/<eneltel>` | Monitoraggio ed inserimento sigilli e matricole installati |

### Sync, Backup & BOXBEAT (`routes/sync.py`)
| Metodo | Endpoint | Descrizione |
|---|---|---|
| `POST` / `GET` | `/api/filone/sync`, `/api/sync`, `/api/sync/status` | Trigger patch/sync immediato e stato sync loop |
| `GET`/`POST`/`DELETE` | `/api/backup/{lista,esegui,ripristina,elimina,scarica_excel}` | Gestione backup JSON ed export Excel |
| `POST` | `/api/upload-BOXBEAT` | Upload singolo/multiplo report installazioni E-Distribuzione (Beat) |

### Distinte, Lettere, Mail, Comuni, Template, Agenda & Analytics
| Area | Endpoint principali |
|---|---|
| **Distinte (`routes/distinte.py`)** | `/distinte`, `/whatsapp`, `/api/distinte/parse-distinta`, `/sovrascrivi`, `/aggiorna-operatori`, `/aggiorna-data`, `/salva-{nota,telefono,email}`, `/strikethrough-telefono`, `/segna-errato`, `/appuntamento`, `/rimuovi-appuntamento`, `/templates`, `/allegati`, `/invia-{singola-email,email-prova,singolo-sms}`, `/smtp-*`, `/sms-*` |
| **Lettere (`routes/lettere.py`)** | `/lettere`, `/api/lettere/generazioni*`, `/aziende*`, `/privati`, `/riclassifica`, `/contatti*`, `/statistiche`, `/allegati`, `/invia-email` |
| **Mail (`routes/mail.py`)** | `/mail` (`/webmail`, `/gestione-mail`), `/api/mail/{stats,messages*,send,save-draft,batch-action,toggle-starred,mark-read,set-category,move-folder,sync-imap,categories*,templates*,template-settings,resolve-template,contacts-search,standard-attachments,upload-attachment,attachment/...}` |
| **Comuni (`routes/comuni.py`)** | `/comuni`, `/api/comuni/{list,update,import-excel,bulk-preview,bulk-send,send-single,download-excel/<comune>,export-excel}`, `/api/admin/export-comuni-excel` |
| **Template (`routes/templates.py`)** | `/templates`, `/api/templates/{all,save,delete/<id>,settings,preview}` |
| **Calendario & Bacheca (`routes/calendario.py`, `routes/bacheca.py`, `routes/appuntamenti.py`)** | `/calendario`, `/api/calendario*`, `/api/workflow*`, `/api/boxbeat/*`, `/api/bacheca/{promemoria,tutti}`, `/appuntamenti`, `/api/appuntamenti`, `/api/appuntamenti/excel` |
| **Dashboard & Report (`routes/dashboard.py`, `routes/report.py`)** | `/dashboard`, `/api/dashboard`, `/report`, `/api/report`, `/api/report/export` |

### Admin Backend & Router Multi-Tenant (`server.py`, `router.py`)
- **Backend provincia (`@admin_required`):** `/api/admin/users*`, `/api/admin/lcls`, `/api/admin/{smtp,sms}-{settings,test*}`, `/api/admin/db-{stats,pending,action,dump/...}`, `/api/admin/outbox*`, `/api/admin/export-full-excel`, `/api/admin/import-excel`, `/api/admin/auto-import-{sheets,status}`, `/api/admin/columns*`, `/api/admin/column-mapping`, `/api/admin/lettere-excel-config`.
- **Router `admin.*` (`router.py`):** `/api/provinces*` (CRUD, lifecycle `start`/`stop`/`restart`), `/api/status`, `/api/logs` (`journalctl`), `/api/health-overview`, `/_router/health`, oltre al proxy admin trasparente verso `/api/provinces/<slug>/{db-action,dump-eneltel,smtp-*,sms-*,auto-import*,import-excel,outbox*,export-full-excel,export-comuni-excel,column-mapping,lettere-excel-config,lcls,columns*}` e gestione `/api/admin/users*`.

---

## 14. Configurazione e variabili d'ambiente

### Schema [`data/provinces.json`](data/provinces.json)

```json
{
  "TORINO": {
    "nome": "Torino",
    "deposito": { "Nome": "DEPOSITO VOLPIANO", "Lat": 45.1964682, "Lon": 7.7888303 },
    "operatori_vip": ["AE101392", "..."],
    "colori_operatori": { "AE101392": "#e74c3c" },
    "operatori_dict": { "PAUL": "AE101392" },
    "ora_inizio_giornata": 540,
    "ora_fine_giornata": 1020,
    "velocita_media_kmh": 35,
    "tempo_intervento_min": 20,
    "data_inizio": "2025-10-01",
    "data_fine": "",
    "usa_localita_come_comune": false,
    "sottodominio": "torino.mappa-algeco.com",
    "porta": 5001,
    "operatori_disabilitati": [],
    "lcl_disabilitati": ["6600101316"],
    "google_sheet_url": "https://docs.google.com/spreadsheets/d/...",
    "auto_import_enabled": true
  }
}
```

### Costanti principali (`config.py`) e Override (`data/{prov}/settings.json`)

| Costante (`config.py`) | Default / Origine | Descrizione |
|---|---|---|
| `OSRM_SERVER` / `OSRM_TIMEOUT` | `http://localhost:5000` / `30` | Server e timeout chiamate routing OSRM |
| `VELOCITA_MEDIA_KMH` / `TEMPO_INTERVENTO_MIN` | `35` / `20` (override per provincia) | Parametri stima tempi di viaggio/sostituzione |
| `FATTORE_CORREZIONE_HAVERSINE` | `1.35` | Moltiplicatore fallback geometrico su assenza OSRM |
| `INTERVALLO_SYNC_SECONDI` | `300` | Solo fallback client in caso di assenza SSE |
| `DB_PATH` | `data/{prov}/state.db` | Database SQLite WAL della provincia |
| `CARTO_API_KEY` | `os.environ.get('CARTO_API_KEY')` o default repo | Chiave basemap CARTO |

`data/{prov}/settings.json` può sovrascrivere a runtime `operatori_vip`, `colori_operatori`, `operatori_dict`, `operatori_disabilitati`, `lcl_disabilitati`, `deposito`, `ora_inizio_giornata`, `ora_fine_giornata`, `velocita_media_kmh`, `tempo_intervento_min`, `data_inizio`, `data_fine`, `mappatura_colonne`, `lettere_excel`, `carto_api_key`, `google_sheet_url`, `auto_import_enabled`.

### Variabili d'ambiente principali

| Variabile | Default | Ruolo |
|---|---|---|
| `PROVINCIA` | `TORINO` | Seleziona la provincia del processo backend |
| `FLASK_PORT` | `5001` | Porta ascolto backend provinciale |
| `ROUTER_HOST` / `ROUTER_PORT` | `127.0.0.1` / `5050` | Bind del reverse proxy `router.py` |
| `GUNICORN_THREADS` | `32` | Thread per worker in `gunicorn.conf.py` (`workers=1`, `gthread`) |
| `FLASK_ENV` | — | Se `production` attiva `SESSION_COOKIE_SECURE=True` |
| `SESSION_COOKIE_DOMAIN` | — | Esempio `.mappa-algeco.com` per auth cross-subdomain (incluso `admin.*`) |
| `DOMINIO_BASE` | `mappa-algeco.com` | Dominio base usato per origin/CORS fidate |
| `ADMIN_HOST_PREFIX` | `admin` | Prefisso hostname per servire la dashboard admin del router |
| `CARTO_API_KEY` | vedi `config.py` | Override basemap CARTO |

---

## 15. Cache e performance

| Risorsa / File | Tecnologia | Scopo |
|---|---|---|
| `cache/cache_distanze_{prov}.db` | **SQLite** lazy (`moduli/cache.py`) | Memorizza le tabelle `distanze` e `hashes` on-demand per coppia `"{tecnico}_{data}"` evitando il caricamento in RAM dell'intero albero storico. Migra automaticamente eventuali vecchi `.json`. |
| `cache/auto_import_{prov}.json` & `last_sheet_{prov}.xlsx` | JSON / XLSX | Salva ultimo hash SHA-256 ZIP CRC dello Sheet ed ultima istantanea scaricata. |
| `cache/cache_comuni_italiani.json` | JSON (TTL 30 gg) | Cache locale del database comuni ISTAT/CAP da `comuni-json`. |
| `cache/cache_appuntamenti_visti_{prov}.json` | JSON | Tracciamento chiavi promemoria già elaborate. |
| `gunicorn.conf.py` | `workers=1`, `threads=32`, `gthread` | Mantiene un solo stato in RAM per provincia (~data + dataframe) gestendo richieste e stream SSE long-lived su 32 thread con `max_requests=1000`. |

---

## 16. Algoritmo di ottimizzazione percorsi, Access Points e Clustering

1. **Access Point stradali (`moduli/access_points.py`):** per ogni contatore con civico valido interroga OSRM `/nearest?number=15` confrontando il nome strada normalizzato (prefissi rimossi) con soglia di similarità `0.85` ed entro `80.0 m`. La coordinata originale rimane il marker visivo del fabbricato, mentre `Lat_access`/`Lon_access` viene usata per il routing OSRM. Gli operatori/admin possono applicare override manuali e undo tramite `/api/access-point/<eneltel>`.
2. **Routing TSP con ancore temporali (`moduli/percorso.py`):**
   - `IS_APPUNTAMENTO = True` sse la nota inizia con token `^\W*app\b` ([`moduli/appuntamenti_core.py`](moduli/appuntamenti_core.py)); l'orario/fascia viene estratto **solo dalla colonna Orari**.
   - Se non ci sono appuntamenti: `OSRM /trip?roundtrip=true&source=first&destination=last`.
   - Con appuntamenti: gli appuntamenti normali diventano **ancore cronologiche** ordinate per `ora_min`; ogni intervallo tra due ancore seleziona i punti base a minor deviazione entro il budget temporale e li ordina con OSRM `/trip` non-roundtrip (`start → end`), accodando infine residui e appuntamenti serali. Fallback geografico tramite sweep angolare dal deposito se OSRM non risponde.
3. **Fasce orarie lettere (`moduli/fasce_orarie.py`):** 7 fasce (`'8/11'`, `'9/12'`, `'10/13'`, `'11/14'`, `'12/15'`, `'13/16'`, `'14/17'`), modalità fissa (`4` tappe/fascia) o bilanciata, con consolidamento automatico degli indirizzi aziendali ([`moduli/rilevamento_aziende.py`](moduli/rilevamento_aziende.py)).
4. **Clustering geografico (`moduli/zone_geografiche.py`):** DBSCAN (`sklearn.cluster.DBSCAN`, `metric='precomputed'`) sui centroidi comunali in km (`dimensione_cella_km=8`, `eps_km = dimensione_cella_km * 0.625`, `min_samples=1`), seguito da suddivisione ricorsiva di zone troppo estese e fusione delle zone piccole (`min_contatori_zona=15`).

---

## 17. Test, CI/CD e Troubleshooting

### Suite di test e Lint (CI su GitHub Actions)

Configurazione in [`.github/workflows/test.yml`](.github/workflows/test.yml) e [`pyproject.toml`](pyproject.toml):

```bash
PY=/opt/mappa-algeco/venv/bin/python

# Lint Ruff (py312, line-length=100, F/E9/W6)
$PY -m ruff check .

# Suite completa Pytest (usa DB temporanei tramite fixture db_isolato in tests/conftest.py)
$PY -m pytest -v
```

### Log e diagnostica rapida

```bash
# Log centralizzati router + worker Gunicorn
sudo journalctl -u mappa-algeco-router -f

# Verifica integrità, conflitti, outbox e anomalie di una provincia
/opt/mappa-algeco/venv/bin/python tools/dump_db.py
PROVINCIA=PAVIA /opt/mappa-algeco/venv/bin/python tools/dump_db.py --pending --anomalies
```

In caso di anomalie sulla cache distanze è sufficiente rimuovere `cache/cache_distanze_{prov}.db` (verrà rigenerata al sync successivo). Per restore di `state.db` vedere [`DB_LOCALE_README.md`](DB_LOCALE_README.md#troubleshooting-rapido).
