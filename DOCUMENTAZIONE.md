# Gestionale Mappa Contatori — Documentazione

> **Versione:** 3.0 (post-disconnessione Google Sheets)
> **Ultimo aggiornamento:** 2026-05-25
> **Progetto:** Mappa Algeco — Sistema di gestione e ottimizzazione percorsi per la sostituzione contatori elettrici

---

## Indice

### Parte 1 — Guida Utente

1. [Cos'è questo programma](#1-cosè-questo-programma)
2. [Requisiti di sistema](#2-requisiti-di-sistema)
3. [Avvio del server](#3-avvio-del-server)
4. [Login e autenticazione](#4-login-e-autenticazione)
5. [La mappa interattiva](#5-la-mappa-interattiva)
6. [Il gestionale (sidebar)](#6-il-gestionale-sidebar)
7. [Gestione distinte e WhatsApp](#7-gestione-distinte-e-whatsapp)
8. [Import dati esterni](#8-import-dati-esterni)
9. [Gestione sigilli e matricole](#9-gestione-sigilli-e-matricole)
10. [Vista FILONE (Excel)](#10-vista-filone-excel)
11. [Accesso remoto (Cloudflare)](#11-accesso-remoto-cloudflare)
12. [FAQ](#12-faq)

### Parte 2 — Guida Sviluppatore

13. [Architettura del sistema](#13-architettura-del-sistema)
14. [Struttura del progetto](#14-struttura-del-progetto)
15. [Flusso dati](#15-flusso-dati)
16. [Moduli — Riferimento](#16-moduli--riferimento)
17. [API REST](#17-api-rest)
18. [Configurazione](#18-configurazione)
19. [Cache e performance](#19-cache-e-performance)
20. [Algoritmo di ottimizzazione percorsi](#20-algoritmo-di-ottimizzazione-percorsi)
21. [Sistema di refresh mappa](#21-sistema-di-refresh-mappa)
22. [Aggiungere una nuova provincia](#22-aggiungere-una-nuova-provincia)
23. [Troubleshooting](#23-troubleshooting)

---

# PARTE 1 — GUIDA UTENTE

---

## 1. Cos'è questo programma

Il **Gestionale Mappa Contatori** è un sistema web per la gestione operativa della sostituzione di contatori elettrici (Enel / e-distribuzione). È pensato per aziende come **Algeco Power** che coordinano squadre di tecnici sul campo.

### Cosa fa in pratica

- **Visualizza su mappa** tutti i contatori da sostituire, con colori diversi per tecnico e zone
- **Ottimizza automaticamente i percorsi** giornalieri per ogni tecnico, riducendo km e tempo di viaggio
- **Gestisce gli appuntamenti** con i clienti, calcolando fasce orarie realistiche
- **Genera messaggi WhatsApp** per avvisare i clienti della sostituzione
- **Importa dati esterni** (Excel/CSV del foglio commesse, file di ritorno installazioni) con merge sicuro
- **Traccia sigilli e matricole** dei contatori installati
- **Mantiene la fonte di verità in un DB SQLite locale** (post-disconnessione Google Sheets del 21/05/2026, vedi [DB_LOCALE_README.md](DB_LOCALE_README.md))

### Province supportate

Le province sono definite in `data/provinces.json` e aggiungibili a runtime dalla UI admin. Default storici:

| Provincia | Porta backend | Deposito | Orario inizio |
| --- | --- | --- | --- |
| **Torino** | 5001 | Volpiano (Via Brandizzo 121d) | 09:00 |
| **Pavia**  | 5002 | Pavia città                  | 08:00 |

In produzione il **router** espone una porta unica (5050) e instrada per Host alle province via Cloudflare Tunnel.

---

## 2. Requisiti di sistema

### Software obbligatorio

| Software | Versione minima | Scopo |
| --- | --- | --- |
| **Python** | 3.10+ (consigliato 3.12) | Linguaggio del server |
| **OSRM** (nativo o Docker) | qualsiasi | Calcolo percorsi stradali reali |
| **Browser moderno** | Chrome, Edge, Firefox | Interfaccia utente |

### Software opzionale

| Software | Scopo |
| --- | --- |
| **Cloudflare Tunnel** (`cloudflared`) | Accesso remoto da Internet |

### File necessari

| File | Descrizione |
| --- | --- |
| `users.json` | Database utenti per il login (creato con `gestione_utenti.py`). Non in git. |
| `data/provinces.json` | Configurazione province (deposito, operatori, colori). In git. |
| `data/{prov}/state.db` | DB SQLite per provincia. Generato al primo avvio o via import Excel. |
| `data/{prov}/settings.json` | Override impostazioni provincia (da UI admin). Opzionale. |

> **Nota**: `credenziali.json` (vecchio Service Account Google) **non serve più**. È rimasto in `.gitignore` per precauzione storica ma il programma non lo legge.

### Server OSRM

Il sistema richiede un server **OSRM** (Open Source Routing Machine) per calcolare distanze e tempi di percorrenza reali. In produzione gira nativo come servizio systemd (`osrm-routed.service`); in locale può girare anche su Docker (porta 5000).

Senza OSRM le distanze sono calcolate in linea d'aria con correzione 1.35× (fallback haversine) e i percorsi NON sono ottimizzati sulla rete stradale reale.

---

## 3. Avvio del server

### Modalità di avvio

```bash
PY=/opt/mappa-algeco/venv/bin/python

# Dev: singola provincia (no router)
$PY server.py --torino
$PY server.py --pavia

# Produzione: router multi-tenant
$PY router.py

# Oppure via systemd (in produzione)
sudo systemctl start mappa-algeco-router
sudo systemctl restart mappa-algeco-router
```

### Cosa succede all'avvio

1. Il **router** (in produzione) o `server.py` standalone valida la configurazione.
2. Per ogni provincia spawna un processo Flask figlio sulla sua porta.
3. Ogni processo figlio:
   - Apre il DB SQLite (`data/{prov}/state.db`)
   - Esegue `PRAGMA integrity_check` (logga warning se KO)
   - Carica il DataFrame dei contatori dal DB
   - Calcola distanze e percorsi ottimizzati (OSRM)
   - Genera la mappa HTML interattiva
   - Avvia il loop event-driven (svegliato da modifiche utente o import Excel)
4. Il router monta i backend dietro routing per Host.

### URL di accesso

| Provincia | URL locale | URL pubblico (con Cloudflare) |
| --- | --- | --- |
| Torino | http://localhost:5001 | https://torino.mappa-algeco.com |
| Pavia | http://localhost:5002 | https://pavia.mappa-algeco.com |
| Admin | http://localhost:5050/ (Host: `admin.*`) | https://admin.mappa-algeco.com |

### Arresto

In dev: **Ctrl+C** nel terminale. In produzione: `sudo systemctl stop mappa-algeco-router`.

---

## 4. Login e autenticazione

All'apertura del sito viene mostrata la pagina di login.

- Inserisci **username** e **password** configurati con `gestione_utenti.py` (vedi [GUIDA_ACCOUNT.md](GUIDA_ACCOUNT.md))
- Dopo 5 tentativi falliti il login viene bloccato per 30 secondi
- La sessione dura **24 ore**, poi è necessario rifare il login
- Per uscire: pulsante di logout o `/logout`

---

## 5. La mappa interattiva

La mappa è il cuore dell'applicazione. Si apre dopo il login.

### Viste disponibili

| Vista | Descrizione |
| --- | --- |
| **Zone** | Mostra le zone geografiche come aree colorate (poligoni). Utile per la pianificazione settimanale. |
| **Comuni** | Aggregazione per comune con conteggi e calendario. |
| **Dettaglio** | Mostra ogni singolo contatore per una data specifica come segnaposto numerato per ordine di percorso. |
| **Percorso operatore** | Mostra solo il percorso di un tecnico in una data. |

### Elementi della mappa

- **Marker colorati** — un colore per tecnico (rosso, giallo, verde, ciano…)
- **Numeri nei marker** — indicano l'ordine di visita ottimizzato
- **Linee colorate** — mostrano il percorso ottimizzato che segue le strade reali
- **Segnaposto "APP"** — contatori con appuntamento, mostrati con icona speciale
- **Deposito** — punto di partenza/arrivo dei tecnici (icona casetta)

### Filtri e ricerca

- **Filtro data** — mostra solo i contatori pianificati per una data specifica
- **Filtro operatore** — mostra solo i contatori di un tecnico
- **Filtro LCL** — filtra per codice LCL
- **Filtro appuntamenti** — solo contatori con/senza appuntamento
- **Barra di ricerca** — cerca per nome cliente, indirizzo, POD, ENELTEL o comune
- **Temi** — alterna tra tema chiaro e scuro

### Popup contatore

Cliccando su un contatore si apre un popup con:

- Dati del cliente (nome, indirizzo, telefono, comune)
- Codici identificativi (ENELTEL, POD)
- Data di pianificazione
- Note e appuntamenti
- Fascia oraria stimata
- Pulsanti per modificare i dati o impostare appuntamenti

### Aggiornamento automatico

La mappa si aggiorna automaticamente tramite **SSE** (Server-Sent Events): dopo ogni modifica utente o import, il server invia un evento `sync_complete` e il browser ricarica i JSON. Quando la connessione SSE è giù, fallback su polling JSON ogni `INTERVALLO_SYNC_SECONDI` (default 300s).

---

## 6. Il gestionale (sidebar)

La sidebar contiene gli strumenti gestionali.

### Modifica contatore

Dal popup di un contatore puoi modificare i campi configurati in `CAMPI_MODIFICABILI` (vedi [config.py](config.py)):

- **Data pianificata** — quando il tecnico deve andare
- **Note/Appuntamento** — annotazioni e orari appuntamento
- **Nominativo** — nome del cliente
- **Indirizzo e civico** — correzione indirizzo
- **Telefono / Email** — contatti
- **Posizione misuratore** — dove si trova
- **Operatore** — tecnico assegnato

Le modifiche vengono scritte nel DB locale e triggherano un refresh immediato della mappa (patch in-place per soft edit, sync_loop classico per cambi strutturali).

### Pulsanti sidebar (variabili per provincia)

| Pulsante | Funzione |
| --- | --- |
| **Import Report** | Carica file Excel di ritorno installazioni (BOXBEAT/Beat) |
| **Sigilli** | Gestisci sigilli per contatori trifase/fotovoltaici |
| **Matricole** | Inserisci matricole dei contatori installati |
| **Vista FILONE** | Vista Excel del DB completo |
| **Sync** | Forza refresh manuale della mappa |

---

## 7. Gestione distinte e WhatsApp

Accessibile da `/distinte` (link nella mappa).

### Funzionalità

1. **Carica distinte PDF** — trascina o seleziona i file PDF "DistintaBase" di Enel
2. **Classifica contatti** — il sistema divide automaticamente:
   - 📱 **Cellulare** — numeri mobili (prefisso 320-399), pronti per WhatsApp
   - 📞 **Fisso** — numeri di rete fissa
   - ❌ **Senza telefono** — nessun numero disponibile
3. **Genera messaggi WhatsApp** — messaggi pre-compilati con data, indirizzo e fascia oraria
4. **Confronta con FILONE** — evidenzia differenze tra distinta e dati nel DB
5. **Aggiorna dati** — sovrascrivi telefono, data, note direttamente dal pannello distinte
6. **Segna numeri errati** — marca numeri non validi
7. **Imposta appuntamenti** — direttamente dalla vista distinte

### Flusso tipico

1. Ricevi le distinte PDF da Enel
2. Caricale nella pagina distinte
3. Controlla le differenze con il FILONE
4. Invia i messaggi WhatsApp ai clienti con cellulare
5. Per i numeri fissi, chiama manualmente
6. Per quelli senza telefono, cerca su PagineBianche e salva

---

## 8. Import dati esterni

Il sistema importa due tipi di file Excel/CSV:

### A. Import FILONE (foglio commesse)

Sostituisce il vecchio pull periodico da Google Sheets (rimosso il 21/05/2026).

1. L'admin scarica il foglio Sheets come `.xlsx` o `.csv`
2. UI admin → **Import FILONE** → seleziona file
3. Inserisci la data/ora di export (per la politica di merge)
4. Il sistema:
   - Legge il file e risolve il mapping colonne
   - Esegue **merge cella-per-cella** col DB locale
   - Politica: **"vince il più recente"** a granularità RIGA — se una riga è stata modificata nel programma dopo l'export Excel, il DB protegge l'intera riga
5. Riepilogo: righe nuove, aggiornate, ignorate (per protezione locale), anomalie

Vedi [moduli/excel_importer.py](moduli/excel_importer.py) e [DB_LOCALE_README.md](DB_LOCALE_README.md).

### B. Import Report installazioni (BOXBEAT/Beat)

Il **Report** è il file scaricabile da Beat con i contatori installati con successo.

1. Apri la mappa → sidebar → **Import Report**
2. Seleziona uno o più file `.xlsx`, `.xls` o `.csv`
3. Il sistema:
   - Legge il file (header alla riga 3)
   - Filtra solo le righe con **"Esito OK"**
   - Cerca il **POD** corrispondente nel DB
   - Aggiorna automaticamente: **Operatore**, **Data Installazione**, **Esito**

### Cosa viene aggiornato dal Report

| Campo Report | Campo DB |
| --- | --- |
| Matricola preposto | Operatore |
| Data esecuzione | Data Installazione |
| Esito = "Esito OK" | Esito = "ESITO OK" |

---

## 9. Gestione sigilli e matricole

### Sigilli

Necessari per contatori **trifase semidiretti** e **fotovoltaici** (ENELTEL che termina con A o B).

1. Sidebar → **Sigilli**
2. Vengono mostrati i contatori installati (Esito OK) senza sigillo
3. Inserisci il numero di sigillo
4. Il sistema lo salva nel DB

### Matricole

Numeri seriali dei contatori installati.

1. Sidebar → **Matricole**
2. Vengono mostrati TUTTI i contatori installati senza matricola
3. Inserisci la matricola del contatore nuovo
4. Il sistema la salva nel DB

> Sia sigilli che matricole mostrano solo le installazioni dell'anno corrente.

---

## 10. Vista FILONE (Excel)

Vista tabellare di tutti i contatori del DB, equivalente alla vista del foglio Excel originale. Permette:

- Filtri rapidi per colonna
- Editing inline dei campi modificabili
- Aggiunta manuale di una nuova riga
- Export Excel del filone corrente

---

## 11. Accesso remoto (Cloudflare)

Per accedere al sistema da fuori la rete locale è disponibile **Cloudflare Tunnel** come servizio systemd indipendente (`cloudflared-tunnel.service`).

### URL pubblici (con dominio configurato)

| Servizio | URL |
| --- | --- |
| Torino | https://torino.mappa-algeco.com |
| Pavia  | https://pavia.mappa-algeco.com |
| Admin  | https://admin.mappa-algeco.com |

Vedi [GUIDA_CLOUDFLARE.md](GUIDA_CLOUDFLARE.md) per la configurazione dettagliata del tunnel.

---

## 12. FAQ

### Il server non si avvia, dice "OSRM non raggiungibile"

In produzione (servizio nativo): `sudo systemctl status osrm-routed`.
In dev locale (Docker): `docker ps` e verifica porta 5000.
Test diretto: `curl "http://localhost:5000/route/v1/driving/7.68,45.07;7.78,45.19"`.

Si può avviare comunque con `python server.py --skip-osrm-check`, ma i percorsi non saranno ottimizzati.

### La mappa non si aggiorna

- Verifica che l'SSE sia connessa (in dev tools del browser, scheda Network → `/api/events`)
- Prova il **sync manuale** dalla sidebar
- Ricarica la pagina con **Ctrl+F5** (svuota cache browser)

### Non riesco a loggarmi

- Verifica che l'utente sia stato creato con `gestione_utenti.py` (vedi [GUIDA_ACCOUNT.md](GUIDA_ACCOUNT.md))
- Controlla che l'utente sia **attivo**
- Dopo 5 tentativi falliti, attendi 30 secondi

### Una modifica fatta in Excel non appare

Da quando Google Sheets è scollegato (21/05/2026), il sistema NON legge più da Sheets in automatico. Per aggiornare il DB con i dati del foglio devi fare un **import Excel manuale** dalla UI admin (sezione [8](#8-import-dati-esterni)).

### Come cambio la password di un utente?

```bash
/opt/mappa-algeco/venv/bin/python gestione_utenti.py --reset --username nomeutente --password nuovapassword
```

---

# PARTE 2 — GUIDA SVILUPPATORE

---

## 13. Architettura del sistema

### Diagramma ad alto livello

```
┌──────────────────────────────────────────────────────────────────────┐
│                         router.py (entry produzione)                 │
│  Reverse proxy per Host → backend per provincia                      │
│  + UI admin (templates_router/) su admin.*                           │
├─────────────────────────────┬────────────────────────────────────────┤
│  server.py :5001 (Torino)   │  server.py :5002 (Pavia)               │
│                             │  (stessa struttura, config diversa)    │
│                             │                                        │
│  ┌─── Loop event-driven ───────────────────────────────────────┐    │
│  │  Risvegliato da: POST utente, import Excel, sync manuale    │    │
│  │   1. Legge DB → DataFrame pandas (con cache)                │    │
│  │   2. Calcola distanze OSRM (con cache hash)                 │    │
│  │   3. Genera zone (DBSCAN)                                   │    │
│  │   4. Genera mappa_data.json + HTML                          │    │
│  │   5. SSE notify → browser                                   │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                       │
│  ┌─── API REST (blueprint in routes/) ───────────────────────────┐   │
│  │  /api/contatore/<eneltel>    ← CRUD contatore                 │   │
│  │  /api/upload-report          ← Import installazioni Beat      │   │
│  │  /api/admin/import-excel     ← Import FILONE                  │   │
│  │  /api/sigilli, /api/matricole← Tracking installazioni         │   │
│  │  /api/filone                 ← Vista Excel                    │   │
│  │  /api/distinte/*             ← Gestione distinte              │   │
│  │  /api/sync                   ← Trigger manuale                │   │
│  │  /api/events                 ← SSE stream                     │   │
│  └───────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────┘

Dipendenze esterne:
  • OSRM (:5000) — calcolo distanze e percorsi stradali reali
  • Cloudflare Tunnel — accesso pubblico (opzionale)
```

### Principi architetturali

1. **DB SQLite locale = unica fonte di verità** (dal 21/05/2026). I dati esterni entrano via import Excel manuale. Vedi [DB_LOCALE_README.md](DB_LOCALE_README.md) per il dettaglio.
2. **OSRM consigliato** — distanze e tempi calcolati su strade reali. Fallback haversine × 1.35 in caso di errore parziale.
3. **Ottimizzazione per tempo** — l'algoritmo preferisce le autostrade anche se più lunghe in km. Implicito nel profilo `driving` di OSRM.
4. **Isolamento per provincia** — ogni provincia è un processo Flask separato, con il proprio DB, cache, output HTML.
5. **Architettura dual-JSON** — il file HTML della mappa è uno shell statico; i dati arrivano come JSON separati (`data/{prov}/mappa_data.json` e `data/{prov}/config.json`), aggiornati via SSE per refresh rapidi.
6. **Refresh event-driven** — niente sync periodico. La mappa viene rigenerata solo quando serve (modifica utente o import). Soft edit applicano una patch in-place a `mappa_data.json` in ~50ms; cambi strutturali fanno girare l'intero sync_loop (~7-10s).
7. **Cache aggressiva** — distanze OSRM e ordine percorsi vengono cachate per evitare ricalcoli inutili.

---

## 14. Struttura del progetto

```
/opt/mappa-algeco/
│
├── server.py                    # Entry point per-provincia (Flask)
├── router.py                    # Reverse proxy multi-tenant + admin UI
├── wsgi.py                      # Bootstrap per gunicorn
├── gunicorn.conf.py             # Config gunicorn (1 worker, 32 thread, gthread)
├── config.py                    # Configurazione (carica provinces.json)
├── requirements.txt             # Dipendenze runtime
├── requirements-dev.txt         # Dipendenze test
├── pyproject.toml
│
├── users.json                   # DB utenti (non in git)
├── gestione_utenti.py           # CLI gestione utenti
├── genera_excel_lettere.py      # CLI export lettere clienti
│
├── data/
│   ├── provinces.json           # Definizioni province (in git)
│   ├── torino/
│   │   ├── state.db             # DB SQLite (WAL)
│   │   ├── mappa_data.json      # JSON per la mappa (aggiornato a ogni refresh)
│   │   ├── config.json          # Config statica per il frontend
│   │   ├── settings.json        # Override settings (da UI admin)
│   │   ├── cell_log.json
│   │   ├── shadow_reports/      # (legacy, vuota post-disconnessione)
│   │   └── span_reports/        # Report anomalie span (Torino)
│   └── pavia/
│       └── (idem)
│
├── moduli/                      # Logica di dominio
│   ├── __init__.py
│   ├── auth.py                  # Auth Flask + sessioni
│   ├── backup.py                # Backup periodico DB + JSON
│   ├── cache.py                 # Cache persistente distanze OSRM
│   ├── calendario.py            # Calendario zone/comuni
│   ├── comuni_db.py             # DB normalizzato comuni
│   ├── data_loader.py           # DB → DataFrame (con cache + hash)
│   ├── db.py                    # Schema SQLite, record_local_change, upsert
│   ├── distanze.py              # OSRM Table/Route
│   ├── distinte_manager.py      # Parser PDF distinte + WhatsApp
│   ├── excel_importer.py        # Import FILONE Excel/CSV (merge sicuro)
│   ├── fasce_orarie.py          # Assegnazione fasce
│   ├── fasce_orarie_legacy.py   # Compat (vecchia API)
│   ├── gestionale.py            # CSS/JS/HTML del gestionale embeddato
│   ├── mappa_data_patcher.py    # Patch in-place mappa_data.json (soft edit)
│   ├── mappa_html.py            # Generazione mappa HTML completa
│   ├── outbox.py                # Coda email
│   ├── percorso.py              # TSP via OSRM Trip
│   ├── postit.py                # Note utente (post-it sulla mappa)
│   ├── rilevamento_aziende.py   # Detect aziende/condomini dagli indirizzi
│   ├── safe_json.py             # Read/write JSON robusto
│   ├── shared.py                # API legacy gspread → DB
│   ├── sync_engine.py           # STUB no-op (post-disconnessione)
│   ├── verifica_indirizzi.py    # Geocoding + normalizzazione
│   ├── verifica_indirizzi_ui.py
│   └── zone_geografiche.py      # Clustering DBSCAN delle zone
│
├── routes/                      # Blueprint Flask
│   ├── access_points.py         # Access points / punti di interesse
│   ├── bacheca.py               # Bacheca avvisi
│   ├── calendario.py            # Calendario zone
│   ├── contatori.py             # CRUD contatori + sigilli + matricole
│   ├── dashboard.py             # Dashboard admin
│   ├── distinte.py              # Gestione distinte
│   ├── postit.py                # Post-it
│   ├── report.py                # Generazione report Excel
│   ├── sigilli.py               # API sigilli
│   ├── sync.py                  # Trigger sync manuale + SSE
│   └── verifica_indirizzi.py
│
├── tools/                       # CLI di servizio
│   ├── dump_db.py               # Diagnostica DB
│   ├── archive_audit_log.py     # Archiviazione mensile audit
│   ├── cleanup_done_pendings.py # Cleanup mensile pending done
│   ├── build_indir_db.py        # Costruzione DB indirizzi
│   └── confronta_percorsi.py    # Confronto offline percorsi
│
├── templates/                   # HTML per-provincia (Jinja2)
├── templates_router/            # HTML admin del router
├── static/                      # CSS/JS/asset frontend
├── cache/                       # Cache OSRM + hash (auto, non in git)
├── output/                      # Excel/PDF generati (auto)
├── backup/                      # Snapshot DB + audit archive
├── deploy/                      # Script deploy e systemd unit
├── tests/                       # pytest
│
├── AGENTS.md                    # Linee guida per agent
├── CLAUDE.md                    # (se presente) istruzioni Claude per il repo
├── DB_LOCALE_README.md          # Architettura DB-only
├── DOCUMENTAZIONE.md            # Questo file
├── GUIDA_UTILIZZO.md            # Guida utente
├── GUIDA_ACCOUNT.md             # Gestione utenti
├── GUIDA_CLOUDFLARE.md          # Setup tunnel
└── GUIDA_DEPLOY_VPS.md          # Deploy su VPS
```

### File generati automaticamente

- `data/{prov}/mappa_data.json` — rigenerato a ogni refresh
- `templates/mappa_{prov}.html` — generato dalla pipeline (non editare a mano)
- `cache/cache_*.json` — cache distanze OSRM
- `output/*.xlsx`, `output/*.pdf` — export utente
- `backup/{prov}/state_YYYY-MM-DD.db` — snapshot DB giornaliero
- `.flask_secret_key` — chiave Flask persistente

---

## 15. Flusso dati

### Refresh della mappa (event-driven)

```
POST utente / Import Excel / Trigger manuale
        │
        ▼
  trigger_sync_after_edit()  ← server.py
        │
        ├── soft edit? ──→ try_patch_inplace()  → patch JSON  → SSE
        │   (~50 ms)        moduli/mappa_data_patcher.py
        │
        └── cambio strutturale? ──→ sync_loop classico (~7-10 s)
            │
            ▼
  1. data_loader.py
     └─ carica_dati_google_sheets()  ← nome legacy: legge dal DB
        ├─ Apre state.db, applica join filone + filone_extra
        ├─ Normalizza colonne (trova_colonna fuzzy match)
        ├─ Filtra: rimuove ESITO OK, date fuori range, no coordinate
        ├─ Rileva appuntamenti (regex su NOTE APP + esclusioni)
        └─ Risultato: pandas DataFrame
        │
        ▼
  2. distanze.py
     └─ genera_report_distanze_con_cache()
        ├─ Raggruppa per tecnico + data
        ├─ Controlla hash cache (skip se dati invariati)
        ├─ Calcola matrice distanze OSRM (blocchi da 80)
        ├─ Ottimizza percorso (percorso.py → TSP via OSRM Trip)
        ├─ Assegna fasce orarie (fasce_orarie.py)
        └─ Risultato: report con ordine, km, tempo per ogni tecnico/data
        │
        ▼
  3. zone_geografiche.py
     └─ genera_report_zone()
        ├─ Calcola centroide per comune
        ├─ Clustering DBSCAN (raggio configurabile)
        ├─ Unisce zone piccole (<10-15 contatori)
        └─ Nomina zone (direzione cardinale + comune principale)
        │
        ▼
  4. mappa_html.py
     └─ genera_mappa_html()
        ├─ Prepara dati contatori con percorsi
        ├─ Calcola rotte OSRM per le linee sulla mappa
        ├─ Genera HTML completo (Leaflet.js + gestionale.py)
        ├─ Salva data/{prov}/mappa_data.json
        └─ Salva templates/mappa_{prov}.html
        │
        ▼
  5. SSE notify
     └─ Invia 'sync_complete' a tutti i browser connessi
```

### Flusso di modifica (utente → DB)

```
Browser (popup / sidebar / FILONE)
    │
    ├── PUT /api/contatore/<eneltel>
    │      → routes/contatori.py
    │      → safe_batch_update_by_name (legacy name, routes al DB)
    │      → moduli/db.py:record_local_change()
    │
    ├── POST /api/upload-report           (Beat)
    │      → routes/contatori.py
    │      → match POD → db.record_local_change()
    │
    ├── POST /api/admin/import-excel      (FILONE)
    │      → moduli/excel_importer.py
    │      → merge "vince il più recente" cella per cella
    │
    └── PUT /api/sigillo/<eneltel>, /api/matricola/<eneltel>
           → routes/sigilli.py, routes/contatori.py
    │
    ▼
DB SQLite (state.db) aggiornato + audit_log popolato
    │
    ▼
trigger_sync_after_edit()
    └─ Patch in-place o sync_loop classico → SSE notify
```

---

## 16. Moduli — Riferimento

### `moduli/db.py`

Schema SQLite v2 + accessor thread-local. Funzioni principali:

| Funzione | Descrizione |
| --- | --- |
| `record_local_change(eneltel, fields, ...)` | Update filone + audit (entry point per le scritture utente). |
| `upsert_filone_row(...)` | Insert/update da import Excel. |
| `find_eneltel_by_pod(pod)` | Lookup secondario. |
| `canonical_column_name(name)` | Risolve alias colonna → chiave interna. |
| `dump_filone_as_rows(...)` | Export DataFrame-like dal DB. |
| `stats()` | Statistiche DB (count, integrity, ecc.). |
| `integrity_check()` | PRAGMA integrity_check. |

Schema: `filone`, `filone_extra`, `pending_writes` (legacy), `audit_log`, `column_registry`, `filone_anomalies`, `sync_state`. Vedi [DB_LOCALE_README.md](DB_LOCALE_README.md).

### `moduli/excel_importer.py`

Import del FILONE da Excel/CSV. Politica di merge: "vince il più recente" a granularità RIGA (`last_local_change_ts` vs `excel_export_ts`).

### `moduli/data_loader.py`

Legge dal DB e produce il DataFrame pandas. Mantiene una cache TTL in memoria invalidata da `mark_data_changed()` dopo ogni scrittura.

**Colonne output principali:**
`DATA_K`, `NOTE_APP_RAW`, `IS_APPUNTAMENTO`, `ORA_APPUNTAMENTO`, `Eneltel_Key`, `CLI_K`, `VIA_K`, `COMUNE`, `TEL_K`, `TEC_K`, `POD_K`, `Lat`, `Lon`, `CIVICO_K`, `TIPO_CE_K`, `LCL`, `LOCALITA`, `NOTE_K`.

**Filtri applicati:**
- Rimuove righe con `ESITO OK` (già installate)
- Rimuove righe senza coordinate GPS valide
- Filtra per operatori VIP (`MATRICOLE_VIP`)
- Filtra per range date (`DATA_INIZIO` — `DATA_FINE`)

### `moduli/shared.py`

API legacy gspread (`safe_batch_update_by_name`, `get_worksheet`, `get_header_map`, `force_refresh_headers`, ecc.) re-implementata sopra il DB. Tutte le chiamate gspread effettive (es. `ws.batch_update`) sollevano eccezioni esplicite. Mantenuta per non toccare le route Blueprint.

### `moduli/sync_engine.py`

**Stub no-op**. `pull_once`, `drain_once`, `trigger_drain_now` ritornano risultati success-like senza effetti. `start_workers` esegue solo `PRAGMA integrity_check` al boot. Vedi commento nel modulo per la cronologia.

### `moduli/mappa_data_patcher.py`

Patch in-place di `mappa_data.json` per soft edit (~50ms invece di rigenerare tutto). Definisce `SOFT_FIELDS_DB` e `STRUCTURAL_FIELDS_DB`.

### `moduli/distanze.py`

| Funzione | Descrizione |
| --- | --- |
| `calcola_matrice_distanze_osrm(...)` | OSRM Table API, blocchi da 80 coordinate |
| `calcola_rotta_osrm(...)` | Rotta singola con geometria per le linee sulla mappa |
| `genera_report_distanze_con_cache(...)` | Entry point principale del calcolo distanze |

**Costanti:** `OSRM_MAX_TABLE_SIZE=80`, `OSRM_TIMEOUT=30`, `VELOCITA_MEDIA_KMH=35` (override per provincia), `TEMPO_INTERVENTO_MIN=20`, `FATTORE_CORREZIONE_HAVERSINE=1.35`.

### `moduli/percorso.py`

TSP via **OSRM Trip API**. Gestisce vincoli di appuntamento con strategia ad ancore temporali. Fallback geografico (sweep angolare) se OSRM cade. Vedi sezione [20](#20-algoritmo-di-ottimizzazione-percorsi).

### `moduli/fasce_orarie.py`

| Funzione | Descrizione |
| --- | --- |
| `assegna_fasce(percorso)` | 4 contatori per fascia, ordine del percorso ottimizzato |
| `formatta_fascia_oraria(fascia)` | `'8/11'` → `'dalle 8:00 alle 11:00'` |

**Fasce definite:** `['8/11', '10/13', '11/14', '12/15', '13/16', '14/17']`.

### `moduli/zone_geografiche.py`

Clustering DBSCAN dei comuni in zone operative. `eps = cella_km × 0.625 / 111`, `min_samples = 1`. Naming: direzione cardinale + comune principale.

### `moduli/mappa_html.py`

Generazione mappa HTML completa con Leaflet.js. Inietta CSS/JS dal modulo `gestionale.py`. Output: `templates/mappa_{prov}.html` + `data/{prov}/mappa_data.json`.

### `moduli/distinte_manager.py`

Parsing PDF "DistintaBase" Enel (pdfplumber), classificazione contatti per prefisso (320-399 → cellulare), generazione messaggi WhatsApp.

### `moduli/auth.py`

Auth Flask con sessioni. Password PBKDF2-SHA256 (100k iterazioni). Storage: `users.json`.

### `moduli/backup.py`

Backup periodico del DB (`VACUUM INTO backup/{prov}/state_YYYY-MM-DD.db`, rotation ultimi 7).

### `moduli/verifica_indirizzi.py`

Geocoding + normalizzazione indirizzi. Usa un DB locale di indirizzi pre-costruito da `tools/build_indir_db.py`.

### `moduli/rilevamento_aziende.py`

Detect di indirizzi aziendali/condomini per il raggruppamento marker (stessa azienda/indirizzo → fascia unica in lettera, pin separati sulla mappa).

---

## 17. API REST

Tutte le API (tranne `/api/config` e `/api/login`) richiedono autenticazione.

### Auth

| Metodo | Endpoint | Descrizione |
| --- | --- | --- |
| GET | `/login` | Pagina di login HTML |
| POST | `/api/login` | Login. Body: `{"username": "...", "password": "..."}` |
| POST | `/api/logout` | Logout |
| GET | `/api/user` | Utente corrente |
| GET | `/logout` | Logout + redirect |

### Mappa e config

| Metodo | Endpoint | Descrizione |
| --- | --- | --- |
| GET | `/` | Mappa HTML della provincia attiva |
| GET | `/api/config` | Configurazione provincia (non protetto) |
| GET | `/api/events` | Stream SSE per notifiche real-time |
| GET | `/data/<path>` | File JSON dalla cartella `data/` (no cache) |

### Contatori

| Metodo | Endpoint | Descrizione |
| --- | --- | --- |
| GET | `/api/contatore/<eneltel>` | Dati di un contatore |
| PUT | `/api/contatore/<eneltel>` | Aggiorna campi |
| POST | `/api/contatore/<eneltel>/appuntamento` | Imposta appuntamento |
| POST | `/api/contatore/<eneltel>/rimuovi-appuntamento` | Rimuove appuntamento |

### Import dati esterni

| Metodo | Endpoint | Descrizione |
| --- | --- | --- |
| POST | `/api/upload-report` | Upload file Excel di ritorno installazioni (Beat) |
| POST | `/api/admin/import-excel` | Upload FILONE Excel/CSV con merge sicuro |

### Sigilli / Matricole

| Metodo | Endpoint | Descrizione |
| --- | --- | --- |
| GET | `/api/sigilli/pending` | Contatori (trifase/fotovolt.) installati senza sigillo |
| PUT | `/api/sigillo/<eneltel>` | Salva sigillo |
| GET | `/api/matricole/pending` | Contatori installati senza matricola |
| PUT | `/api/matricola/<eneltel>` | Salva matricola |

### FILONE (vista Excel)

| Metodo | Endpoint | Descrizione |
| --- | --- | --- |
| GET | `/api/filone` | Tutti i contatori del DB |
| PUT | `/api/filone/row/<row_num>` | Aggiorna riga |
| POST | `/api/filone/row` | Aggiunge nuova riga manualmente |

### Distinte

| Metodo | Endpoint | Descrizione |
| --- | --- | --- |
| GET | `/distinte` | Pagina gestione distinte |
| POST | `/api/distinte/parse-distinta` | Upload PDF distinte |
| POST | `/api/distinte/sovrascrivi` | Sovrascrivi campo via POD |
| POST | `/api/distinte/salva-nota` | Salva nota |
| POST | `/api/distinte/appuntamento` | Appuntamento via POD |
| POST | `/api/distinte/salva-telefono` | Salva telefono |

### Sync / Admin

| Metodo | Endpoint | Descrizione |
| --- | --- | --- |
| POST | `/api/sync` | Forza refresh manuale |
| GET | `/api/sync/status` | Stato del refresh |
| GET | `/api/admin/db-stats` | Stats DB locale |
| GET | `/api/admin/db-pending` | Pending residue per stato |

---

## 18. Configurazione

### `data/provinces.json` — definizioni province

```json
{
  "TORINO": {
    "nome": "Torino",
    "deposito": {"Nome": "Volpiano", "Lat": 45.32, "Lon": 7.78},
    "operatori_vip": ["AE101392", "AG49808"],
    "colori_operatori": {"AE101392": "#e74c3c"},
    "operatori_dict": {"Mario Rossi": "AE101392"},
    "ora_inizio_giornata": 540,
    "ora_fine_giornata": 1020,
    "data_inizio": "2025-10-01",
    "data_fine": ""
  }
}
```

Aggiungibile/modificabile dall'UI admin (`admin.*` o `/admin/*`).

### `config.py` — costanti globali

| Costante | Valore default | Descrizione |
| --- | --- | --- |
| `OSRM_SERVER` | `http://localhost:5000` | Server OSRM |
| `OSRM_TIMEOUT` | 30 | Timeout in secondi |
| `VELOCITA_MEDIA_KMH` | 35 | Stima fallback (override per provincia) |
| `TEMPO_INTERVENTO_MIN` | 20 | Minuti per sostituzione |
| `INTERVALLO_SYNC_SECONDI` | 300 | Solo fallback polling client se SSE down |
| `DATA_INIZIO` / `DATA_FINE` | provincia-specifico | Range filtro date |
| `DIMENSIONE_CELLA_ZONA_KM` | 8 | Raggio clustering DBSCAN |
| `FATTORE_CORREZIONE_HAVERSINE` | 1.35 | Moltiplicatore fallback haversine |
| `DB_PATH` | `data/{prov}/state.db` | Path DB SQLite |
| `USE_DB_BACKEND` | `primary` | `primary` (default) o `off` (kill-switch) |

### `data/{prov}/settings.json` — override da UI admin

Può sovrascrivere: `operatori_vip`, `colori_operatori`, `operatori_dict`, `deposito`, `ora_inizio_giornata`, `ora_fine_giornata`, `velocita_media_kmh`, `tempo_intervento_min`, `data_inizio`, `data_fine`, `mappatura_colonne`.

### Variabili d'ambiente

| Variabile | Descrizione |
| --- | --- |
| `PROVINCIA` | Provincia attiva (`TORINO` / `PAVIA` / ...) |
| `FLASK_PORT` | Porta del server Flask (default 5001) |
| `ROUTER_PORT` | Porta del router (default 5050) |
| `GUNICORN_THREADS` | Override threads gunicorn (default 32) |
| `USE_DB_BACKEND` | Override modalità DB (`primary` / `off`) |

---

## 19. Cache e performance

### File di cache

| File | Contenuto | Chiave |
| --- | --- | --- |
| `cache/cache_distanze_{prov}.json` | Matrici distanze/durate OSRM | `"{tecnico}_{data}"` |
| `cache/cache_hash_{prov}.json` | Hash MD5 dei dati per invalidazione | `"{tecnico}_{data}"` |
| `cache/cache_ordine_percorsi_{prov}.json` | Ordine TSP pre-calcolato | `"{tecnico}_{data}"` |

### Strategia di invalidazione

1. Per ogni coppia **tecnico + data** viene calcolato un **hash MD5** dei dati rilevanti (coordinate, appuntamenti, fasce orarie).
2. Se l'hash è identico al sync precedente, le distanze NON vengono ricalcolate.
3. Se l'hash è diverso (nuovo contatore, cambio posizione, ecc.), la cache per quella chiave viene invalidata.
4. La cache è **permanente** (nessuna scadenza temporale).

### Quando viene invalidata

- Aggiunta/rimozione di un contatore
- Cambio coordinate
- Cambio appuntamento
- Cambio operatore assegnato
- Cambio data pianificata

### Memoria

Singolo worker gunicorn (`workers=1`, `threads=32`, `worker_class=gthread`). Il DataFrame + cache + `mappa_data` occupa ~300 MB in memoria. Con N worker la memoria si moltiplicherebbe: si è scelto **1 worker** perché il pattern è I/O-bound (DB + OSRM) e il volume di traffico basso (~5 utenti). I thread gthread gestiscono la concorrenza, inclusi SSE long-lived.

---

## 20. Algoritmo di ottimizzazione percorsi

Il modulo `percorso.py` delega il TSP a **OSRM Trip API**, che lo risolve nativamente sulla rete stradale reale (sensi unici, svincoli, autostrade).

### Pipeline

```
Input: lista punti con coordinate (Lat/Lon o Lat_access/Lon_access)
  │
  ▼
1. CATEGORIZZAZIONE
   ├─ app_normali: appuntamenti del giorno con orario parsato
   ├─ app_serali : appuntamenti dopo soglia (17:00 TO / 15:00 PA)
   └─ punti_base : tutto il resto
  │
  ▼
2. SE NESSUN APPUNTAMENTO → OSRM Trip roundtrip
   GET /trip/v1/driving/{deposito;p1;p2;...;pn;deposito}
       ?roundtrip=true&source=first&destination=last
  │
  ▼
3. SE CI SONO APPUNTAMENTI → strategia "ancore temporali"
   ├─ Ordina app_normali per ora_min crescente.
   ├─ Per ogni segmento (pos → prossimo_app):
   │    budget = ora_min(app) - orario_corrente - tempo_diretto(pos, app)
   │    n_max  = floor(budget / TEMPO_TAPPA_MEDIO)
   │    Sceglie i n_max punti base con minor "deviazione" dal segmento
   │    e li ordina con OSRM Trip non-roundtrip (start=pos, end=app).
   ├─ Aggiunge l'appuntamento (se arriva prima di ora_min, "aspetta").
   ├─ Punti base rimanenti: OSRM Trip pos → deposito.
   └─ Appuntamenti serali in coda, in ordine cronologico.
  │
  ▼
4. FALLBACK (OSRM down): sweep angolare dal deposito
  │
  ▼
Output: lista punti ordinata
```

### Rilevamento appuntamenti

`IS_APPUNTAMENTO=True` se la nota contiene `\bapp\b` come token e NON matcha pattern di esclusione (`no app`, `app annullata`, `app da fissare`, ecc.). Vedi `rileva_appuntamento` in `percorso.py`.

### Parsing orario

`estrai_orario_e_fascia` riconosce:

- `dalle 8 alle 12` → `(08:00, 12:00)`
- `dalle 09:00 alle 12:30` → `(09:00, 12:30)`
- `ORE 10:30/12:00` → `(10:30, 12:00)`
- `10/12`, `10-12` → `(10:00, 12:00)`
- `10:30` → `(10:30, 10:30)`
- `alle 14`, `ore 14` → `(14:00, 14:00)`

Se NOTE APP non contiene un orario parsabile, fallback su `ORARI_LETTERE` (la fascia delle lettere già spedite ai clienti).

### Perché le autostrade

OSRM con `profile=driving` restituisce il percorso **più veloce** (non il più breve in km). Per Torino i tecnici usano la tangenziale/autostrada anziché attraversare il centro.

---

## 21. Sistema di refresh mappa

### Refresh event-driven

Il sistema NON gira più un sync periodico ogni 3 minuti. La mappa viene rigenerata solo quando serve:

- Dopo un POST utente (modifica contatore, sigillo, matricola, appuntamento, ecc.)
- Dopo un import (FILONE Excel o Report Beat)
- Su trigger manuale (`/api/sync` o pulsante "Sync" in sidebar)

`server.py:trigger_sync_after_edit()` decide se applicare una **patch in-place** o eseguire il **sync_loop classico**.

### Patch in-place (soft edit, ~50ms)

Per modifiche cosmetiche (email, telefono, nominativo, indirizzo, sigillo, matricola, NOTE APP senza pattern `APP`, ecc.) `moduli/mappa_data_patcher.py` patcha direttamente `data/{prov}/mappa_data.json` e invia SSE `sync_complete`. Niente sync_loop.

### Sync_loop classico (cambi strutturali, ~7-10s)

Per cambi strutturali (DATA PIAN, Operatore, Lat/Lon, Comune, NOTE APP con pattern `APP`, Esito) si esegue l'intera pipeline: DB → DataFrame → distanze → zone → mappa.

### Notifiche SSE

```
event: sync_complete
data: {"source": "inplace_patch" | "sync_loop", "timestamp": "..."}
```

Il JS nel browser riceve l'evento e ricarica i JSON da `data/`. Quando l'SSE è giù, fallback su polling JSON ogni `INTERVALLO_SYNC_SECONDI` (default 300s).

---

## 22. Aggiungere una nuova provincia

### Passo 1 — Wizard UI admin (consigliato)

Dalla UI admin (`admin.*`) c'è un wizard che chiede nome, deposito, operatori e genera automaticamente la voce in `data/provinces.json` + cartella `data/{slug}/` + DB iniziale vuoto. L'utente che crea la provincia ottiene auto-grant di accesso.

### Passo 2 — Aggiunta manuale (per dev)

1. Modifica `data/provinces.json`:
   ```json
   "NUOVA_PROVINCIA": {
     "nome": "Nuova Provincia",
     "deposito": {"Nome": "DEPOSITO", "Lat": 45.0, "Lon": 9.0},
     "operatori_vip": ["OP1"],
     "colori_operatori": {"OP1": "#e74c3c"},
     "operatori_dict": {"Operatore 1": "OP1"},
     "ora_inizio_giornata": 480
   }
   ```
2. Crea la cartella `data/nuova_provincia/`.
3. Importa un Excel iniziale per popolare il DB (UI admin → Import FILONE).
4. Riavvia il router: `systemctl restart mappa-algeco-router`.

---

## 23. Troubleshooting

### OSRM non raggiungibile

```bash
# Produzione (nativo)
sudo systemctl status osrm-routed

# Dev (Docker)
docker ps
curl "http://localhost:5000/route/v1/driving/7.68,45.07;7.78,45.19"
```

### Mappa HTML vuota o errore

- Controlla `journalctl -u mappa-algeco-router` per i log del sync
- Verifica che `data/{prov}/mappa_data.json` contenga dati validi
- Forza un sync manuale dalla sidebar

### Cache corrotta

```bash
rm /opt/mappa-algeco/cache/cache_*.json
sudo systemctl restart mappa-algeco-router
```

### DB corrotto

```bash
PY=/opt/mappa-algeco/venv/bin/python
$PY tools/dump_db.py        # mostra anche PRAGMA integrity_check

# Restore da snapshot
cp /opt/mappa-algeco/backup/torino/state_YYYY-MM-DD.db \
   /opt/mappa-algeco/data/torino/state.db
sudo systemctl restart mappa-algeco-router
```

### Import Excel non aggiorna alcune righe

Verifica il timestamp di export inserito al momento dell'upload: se è precedente alle ultime modifiche locali, la politica "vince il più recente" protegge il DB. Vedi [DB_LOCALE_README.md](DB_LOCALE_README.md#troubleshooting-rapido).

### Debug API

I log nel journal sono prefissati per area:

| Prefisso | Modulo |
| --- | --- |
| `[SYNC]` | Refresh mappa |
| `[SSE]` | Server-Sent Events |
| `[REPORT]` | Import installazioni Beat |
| `[IMPORT]` | Import FILONE Excel |
| `[SIGILLI]` | Gestione sigilli |
| `[MATRICOLE]` | Gestione matricole |
| `[DISTINTE]` | Gestione distinte |

```bash
sudo journalctl -u mappa-algeco-router -f
```

---

## Glossario

| Termine | Significato |
| --- | --- |
| **FILONE** | Il foglio principale con tutti i contatori da sostituire. Storicamente Google Sheets, oggi DB SQLite locale. |
| **ENELTEL** | Codice identificativo univoco del contatore Enel |
| **POD** | Point of Delivery — codice punto di consegna energia |
| **Report / BOXBEAT** | File Excel di ritorno da Beat con gli esiti delle installazioni |
| **Distinta / DistintaBase** | Ordine di lavoro PDF da Enel con dati delle sostituzioni programmate |
| **OSRM** | Open Source Routing Machine — calcolo percorsi stradali reali |
| **LCL** | Codice località/area del contatore |
| **Fascia oraria** | Intervallo di 3 ore in cui il tecnico prevede di arrivare |
| **TSP** | Travelling Salesman Problem — ottimizzazione dell'ordine di visita |
| **SSE** | Server-Sent Events — notifiche push dal server al browser |
| **Sigillo** | Sigillo di sicurezza applicato ai contatori trifase/fotovoltaici |
| **Sync_loop** | Pipeline completa di rigenerazione mappa (DB → DataFrame → distanze → zone → JSON + HTML) |
| **Patch in-place** | Aggiornamento diretto di `mappa_data.json` per soft edit (no sync_loop) |

---
