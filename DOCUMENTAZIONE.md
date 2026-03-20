# Gestionale Mappa Contatori — Documentazione Completa

> **Versione:** 2.0
> **Ultimo aggiornamento:** Febbraio 2026
> **Progetto:** Mappa Algeco — Sistema di gestione e ottimizzazione percorsi per la sostituzione contatori elettrici

---

## Indice

### Parte 1 — Guida Utente

1. [Cos&#39;è questo programma](#1-cosè-questo-programma)
2. [Requisiti di sistema](#2-requisiti-di-sistema)
3. [Installazione e primo avvio](#3-installazione-e-primo-avvio)
4. [Avvio del server](#4-avvio-del-server)
5. [Login e autenticazione](#5-login-e-autenticazione)
6. [La mappa interattiva](#6-la-mappa-interattiva)
7. [Il gestionale (sidebar)](#7-il-gestionale-sidebar)
8. [Gestione distinte e WhatsApp](#8-gestione-distinte-e-whatsapp)
9. [Import BOXBEAT (Enel)](#9-import-BOXBEAT-enel)
10. [Gestione sigilli e matricole](#10-gestione-sigilli-e-matricole)
11. [Vista FILONE (Excel)](#11-vista-filone-excel)
12. [Accesso remoto (Cloudflare)](#12-accesso-remoto-cloudflare)
13. [Domande frequenti (FAQ)](#13-domande-frequenti-faq)

### Parte 2 — Guida Sviluppatore

14. [Architettura del sistema](#14-architettura-del-sistema)
15. [Struttura del progetto](#15-struttura-del-progetto)
16. [Flusso dati](#16-flusso-dati)
17. [Moduli — Riferimento completo](#17-moduli--riferimento-completo)
18. [API REST — Riferimento completo](#18-api-rest--riferimento-completo)
19. [Configurazione (`config.py`)](#19-configurazione-configpy)
20. [Sistema di cache](#20-sistema-di-cache)
21. [Algoritmo di ottimizzazione percorsi](#21-algoritmo-di-ottimizzazione-percorsi)
22. [Sistema di sincronizzazione](#22-sistema-di-sincronizzazione)
23. [Aggiungere una nuova provincia](#23-aggiungere-una-nuova-provincia)
24. [Troubleshooting per sviluppatori](#24-troubleshooting-per-sviluppatori)

---

# PARTE 1 — GUIDA UTENTE

---

## 1. Cos'è questo programma

Il **Gestionale Mappa Contatori** è un sistema web per la gestione operativa della sostituzione di contatori elettrici (Enel/e-distribuzione). È pensato per aziende come **Algeco Power** che coordinano squadre di tecnici sul campo.

### Cosa fa in pratica

- **Visualizza su mappa** tutti i contatori da sostituire, con colori diversi per tecnico e zone
- **Ottimizza automaticamente i percorsi** giornalieri per ogni tecnico, riducendo i km e il tempo di viaggio
- **Gestisce gli appuntamenti** con i clienti, calcolando fasce orarie realistiche
- **Genera messaggi WhatsApp** per avvisare i clienti della sostituzione
- **Importa i dati da BOXBEAT** per aggiornare lo stato delle installazioni
- **Traccia sigilli e matricole** dei contatori installati
- **Si sincronizza in tempo reale** con Google Sheets (il "FILONE"), aggiornandosi ogni 3 minuti

### Province supportate

| Provincia        | Porta | Deposito                      | Orario inizio |
| ---------------- | ----- | ----------------------------- | ------------- |
| **Torino** | 5001  | Volpiano (Via Brandizzo 121d) | 09:00         |
| **Pavia**  | 5002  | Pavia città                  | 08:00         |

---

## 2. Requisiti di sistema

### Software obbligatorio

| Software                  | Versione minima       | Scopo                                          |
| ------------------------- | --------------------- | ---------------------------------------------- |
| **Python**          | 3.10+                 | Linguaggio del server                          |
| **Docker Desktop**  | Qualsiasi recente     | Per il server OSRM (calcolo percorsi stradali) |
| **Browser moderno** | Chrome, Edge, Firefox | Interfaccia utente                             |

### Software opzionale

| Software                                      | Scopo                                             |
| --------------------------------------------- | ------------------------------------------------- |
| **Cloudflare Tunnel** (`cloudflared`) | Accesso remoto da Internet                        |

### File necessari

| File                 | Descrizione                                                                      |
| -------------------- | -------------------------------------------------------------------------------- |
| `credenziali.json` | Credenziali Service Account Google per accesso a Google Sheets                   |
| `users.json`       | Database utenti per il login (creato automaticamente con `gestione_utenti.py`) |

### Server OSRM (Docker)

Il sistema richiede un server **OSRM** (Open Source Routing Machine) locale per calcolare distanze e tempi di percorrenza reali sulle strade italiane. Deve essere in esecuzione come container Docker sulla porta 5000.

> **Nota:** Senza OSRM il sistema non può calcolare i percorsi ottimizzati. Il server chiederà conferma per continuare comunque, ma le funzionalità saranno limitate.

---

## 3. Installazione e primo avvio

### Passo 1 — Installa le dipendenze Python

```powershell
cd "percorso\della\cartella\mappa_algeco_nuova"
pip install -r requirements.txt
```

Le dipendenze principali sono:

- `flask` — server web
- `gspread` + `oauth2client` — collegamento a Google Sheets
- `pandas` + `openpyxl` — elaborazione dati e generazione Excel
- `requests` — comunicazione con OSRM
- `scikit-learn` — clustering geografico (DBSCAN)
- `beautifulsoup4` — parsing HTML
- `flask-cors` — gestione CORS per il browser

### Passo 2 — Configura le credenziali Google

1. Crea un **Service Account** nella Google Cloud Console
2. Abilita la **Google Sheets API** e la **Google Drive API**
3. Scarica il file JSON delle credenziali
4. Rinominalo `credenziali.json` e mettilo nella cartella del progetto
5. **Condividi** il foglio Google Sheets con l'email del Service Account (con permessi di modifica)

### Passo 3 — Crea almeno un utente

```powershell
python gestione_utenti.py
```

Scegli l'opzione **1. Crea nuovo utente** dal menu interattivo. Oppure da riga di comando:

```powershell
python gestione_utenti.py --crea --username admin --password tuapassword
```

### Passo 4 — Avvia Docker e OSRM

Assicurati che Docker Desktop sia in esecuzione e che il container OSRM sia attivo sulla porta 5000.

### Passo 5 — Avvia il server

```powershell
python server.py
```

Il server:

1. Verificherà la connessione OSRM
2. Avvierà i server Flask per le province configurate
3. Scaricherà i dati da Google Sheets
4. Calcolerà le distanze e ottimizzerà i percorsi
5. Genererà la mappa HTML interattiva
6. Aprirà automaticamente il browser

> La prima sincronizzazione può richiedere 1-2 minuti. Le successive saranno più veloci grazie alla cache.

---

## 4. Avvio del server

### Modalità di avvio

```powershell
# Avvia ENTRAMBE le province (Torino + Pavia)
python server.py

# Solo Torino
python server.py --torino

# Solo Pavia  
python server.py --pavia

# Entrambe + accesso pubblico via Cloudflare Tunnel
python server.py --pubblico

# Salta il controllo OSRM (utile per debug)
python server.py --skip-osrm-check

# Non aprire il browser automaticamente
python server.py --no-browser
```

### Cosa succede all'avvio

1. Il **launcher** (processo principale) valida la configurazione
2. Per ogni provincia, avvia un **processo Flask figlio** separato
3. Ogni processo figlio:
   - Si connette a Google Sheets
   - Scarica tutti i dati del FILONE
   - Calcola distanze e percorsi ottimizzati (OSRM)
   - Genera la mappa HTML interattiva
   - Avvia il loop di sincronizzazione automatica (ogni 3 minuti)
4. Il launcher apre il browser sugli URL locali

### URL di accesso

| Provincia | URL locale            |
| --------- | --------------------- |
| Torino    | http://localhost:5001 |
| Pavia     | http://localhost:5002 |

### Arresto

Premi **Ctrl+C** nel terminale per terminare tutti i server.

---

## 5. Login e autenticazione

All'apertura del sito, viene mostrata una **pagina di login** con tema scuro.

- Inserisci **username** e **password** configurati con `gestione_utenti.py`
- Dopo 5 tentativi falliti, il login viene bloccato per 30 secondi
- La sessione dura **24 ore**, poi è necessario rifare il login
- Per uscire: clicca il pulsante di logout o vai a `/logout`

### Gestione utenti (CLI)

```powershell
python gestione_utenti.py
```

Menu interattivo:

1. **Crea nuovo utente** — username + password
2. **Elimina utente** — rimuove un utente esistente
3. **Reset password** — cambia la password di un utente
4. **Attiva/Disattiva** — blocca/sblocca un utente senza eliminarlo
5. **Lista utenti** — mostra tutti gli utenti e il loro stato

---

## 6. La mappa interattiva

La mappa è il cuore dell'applicazione. Si apre dopo il login.

### Viste disponibili

| Vista               | Descrizione                                                                                          |
| ------------------- | ---------------------------------------------------------------------------------------------------- |
| **Zone**      | Mostra le zone geografiche come aree colorate (poligoni). Utile per la pianificazione settimanale    |
| **Dettaglio** | Mostra ogni singolo contatore per una data specifica come segnaposto numerato per ordine di percorso |

### Elementi della mappa

- **Marker colorati** — ogni tecnico ha un colore diverso (rosso, giallo, verde, ciano...)
- **Numeri nei marker** — indicano l'ordine di visita ottimizzato
- **Linee colorate** — mostrano il percorso ottimizzato che segue le strade reali
- **Segnaposto "APP"** — contatori con appuntamento, mostrati con icona speciale
- **Deposito** — punto di partenza/arrivo dei tecnici (icona casetta)

### Filtri e ricerca

- **Filtro data** — mostra solo i contatori pianificati per una data specifica
- **Filtro operatore** — mostra solo i contatori di un tecnico
- **Filtro LCL** — filtra per codice LCL
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

La mappa si aggiorna automaticamente:

- Tramite **SSE** (Server-Sent Events): il server notifica il browser quando ci sono nuovi dati
- Tramite **polling JSON**: il browser controlla periodicamente se i file JSON sono cambiati
- Il **sync in background** ricalcola tutto ogni 3 minuti

---

## 7. Il gestionale (sidebar)

La sidebar a destra della mappa contiene gli strumenti gestionali:

### Modifica contatore

Dal popup di un contatore, puoi modificare:

- **Data pianificata** — quando il tecnico deve andare
- **Note/Appuntamento** — annotazioni e orari appuntamento
- **Nominativo** — nome del cliente
- **Indirizzo e civico** — correzione indirizzo
- **Telefono** — numero di contatto
- **Posizione misuratore** — dove si trova (Nell'appartamento, Accessibile dall'esterno ecc.)
- **Operatore** — tecnico assegnato

Le modifiche vengono scritte direttamente su Google Sheets e triggherano un sync immediato.

### Pulsanti sidebar

| Pulsante                 | Funzione                                              |
| ------------------------ | ----------------------------------------------------- |
| **Import BOXBEAT** | Carica file Excel Report per aggiornare installazioni |
| **Sigilli**        | Gestisci sigilli per contatori trifase/fotovoltaici   |
| **Matricole**      | Inserisci matricole dei contatori installati          |
| **FILONE**         | Vista Excel del foglio Google Sheets completo         |
| **Sync**           | Forza sincronizzazione manuale                        |

---

## 8. Gestione distinte e WhatsApp

Accessibile da `/distinte` (o dal link nella mappa).

### Funzionalità

1. **Carica distinte PDF** — trascina o seleziona i file PDF "DistintaBase" di Enel
2. **Classifica contatti** — il sistema divide automaticamente:
   - 📱 **Cellulare** — numeri mobili (prefisso 320-399), pronti per WhatsApp
   - 📞 **Fisso** — numeri di rete fissa
   - ❌ **Senza telefono** — nessun numero disponibile
3. **Genera messaggi WhatsApp** — crea messaggi pre-compilati con data, indirizzo e fascia oraria
4. **Confronta con FILONE** — evidenzia differenze tra distinta e dati nel foglio Google
5. **Aggiorna dati** — sovrascrivi telefono, data, note direttamente dal pannello distinte
6. **Segna numeri errati** — marca numeri non validi con barrato su Google Sheets
7. **Imposta appuntamenti** — direttamente dalla vista distinte

### Flusso di lavoro tipico

1. Ricevi le distinte PDF da Enel
2. Caricale nella pagina distinte
3. Controlla le differenze con il FILONE
4. Invia i messaggi WhatsApp ai clienti con cellulare
5. Per i numeri fissi, chiama manualmente
6. Per quelli senza telefono, cerca su PagineBianche e salva

---

## 9. Import BOXBEAT (Enel)

Il **Report** è il file scaricabile da Beat per reperire i contatori installati con successo.

### Come usarlo

1. Apri la mappa → sidebar → **Import BOXBEAT**
2. Seleziona uno o più file `.xlsx`, `.xls` o `.csv`
3. Il sistema:
   - Legge il file (header alla riga 3)
   - Filtra solo le righe con **"Esito OK"**
   - Cerca il **POD** corrispondente nel FILONE
   - Aggiorna automaticamente: **Operatore**, **Data Installazione**, **Esito**
4. Tutte le modifiche vengono fatte in un unico batch per velocità

### Cosa viene aggiornato sul FILONE

| Campo BOXBEAT      | Campo FILONE       |
| ------------------ | ------------------ |
| Matricola preposto | Operatore          |
| Data esecuzione    | Data Installazione |
| Esito = "Esito OK" | Esito = "ESITO OK" |

---

## 10. Gestione sigilli e matricole

### Sigilli

I **sigilli** sono necessari per:

- Contatori **trifase semidiretti**
- Contatori **fotovoltaici** (ENELTEL che termina con A o B)

1. Sidebar → **Sigilli**
2. Vengono mostrati i contatori installati (Esito OK) senza sigillo
3. Inserisci il numero di sigillo
4. Il sistema lo salva su Google Sheets ed evidenzia la cella in giallo

### Matricole

Le **matricole** sono i numeri seriali dei contatori installati.

1. Sidebar → **Matricole**
2. Vengono mostrati TUTTI i contatori installati senza matricola
3. Inserisci la matricola del contatore nuovo
4. Il sistema la salva su Google Sheets ed evidenzia la cella in verde

> Sia sigilli che matricole mostrano solo le installazioni dell'anno corrente.

---

## 11. Accesso remoto (Cloudflare)

Per accedere al sistema da fuori la rete locale (es. dal cantiere), è possibile usare **Cloudflare Tunnel**.

### Avvio con accesso pubblico

```powershell
python server.py --pubblico
```

### URL pubblici (con dominio configurato)

| Provincia | URL                             |
| --------- | ------------------------------- |
| Torino    | https://torino.mappa-algeco.com |
| Pavia     | https://pavia.mappa-algeco.com  |

### Configurazione

Vedi il file `GUIDA_CLOUDFLARE.md` per la configurazione dettagliata del tunnel.

---

## 12. Domande frequenti (FAQ)

### Il server non si avvia, dice "OSRM non raggiungibile"

Verifica che:

1. **Docker Desktop** sia in esecuzione
2. Il **container OSRM** sia attivo (`docker ps` nel terminale)
3. La porta **5000** sia libera e raggiungibile (`http://localhost:5000`)

Puoi avviare comunque con `python server.py --skip-osrm-check`, ma i percorsi non saranno ottimizzati.

### La mappa non si aggiorna

- Verifica che il sync in background sia attivo (controlla i log nel terminale)
- Prova il **sync manuale** dalla sidebar
- Ricarica la pagina con **Ctrl+F5** (svuota cache browser)

### Non riesco a loggarmi

- Verifica che l'utente sia stato creato con `gestione_utenti.py`
- Controlla che l'utente sia **attivo** (opzione 5 nel menu gestione utenti)
- Dopo 5 tentativi falliti, attendi 30 secondi

### I dati sul foglio Google non si aggiornano

- Verifica che `credenziali.json` sia presente e valido
- Controlla che il Service Account abbia **permessi di modifica** sul foglio
- Verifica l'ID del foglio in `config.py` → `PROVINCE[...]['id_foglio']`

### Come cambio la password di un utente?

```powershell
python gestione_utenti.py --reset --username nomeutente --password nuovapassword
```

---

# PARTE 2 — GUIDA SVILUPPATORE

---

## 14. Architettura del sistema

### Diagramma ad alto livello

```
┌──────────────────────────────────────────────────────────────────┐
│                      server.py (Launcher)                        │
│  CLI args → spawn processi figlio Flask per ogni provincia       │
│  Opzionale: Cloudflare Tunnel per accesso pubblico               │
├─────────────────────────┬────────────────────────────────────────┤
│  Flask :5001 (Torino)   │  Flask :5002 (Pavia)                  │
│                         │  (stessa struttura, config diversa)    │
│                         │                                        │
│  ┌─── Sync Thread (loop 180s) ────────────────────────────┐     │
│  │ 1. carica_dati_google_sheets()  → DataFrame             │     │
│  │ 2. genera_report_distanze_con_cache()  → OSRM           │     │
│  │ 3. genera_report_zone()  → DBSCAN clusters              │     │
│  │ 4. genera_mappa_html()  → HTML + JSON                   │     │
│  │ 5. SSE notify → browser                                 │     │
│  └──────────────────────────────────────────────────────────┘     │
│                                                                    │
│  ┌─── API REST ──────────────────────────────────────────────┐   │
│  │ /api/contatore/<eneltel>      ← CRUD contatore            │   │
│  │ /api/upload-BOXBEAT           ← Import Enel               │   │
│  │ /api/sigilli, /api/matricole  ← Tracking installazioni    │   │
│  │ /api/filone                   ← Vista Excel               │   │
│  │ /api/distinte/*               ← Gestione distinte         │   │
│  │ /api/sync                     ← Trigger manuale           │   │
│  │ /api/events                   ← SSE stream                │   │
│  └────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘

Dipendenze esterne:
  • Google Sheets API (gspread + Service Account) — lettura/scrittura FILONE
  • OSRM Docker (:5000)     — calcolo distanze e percorsi stradali reali
```

### Principi architetturali

1. **Nessun database locale** — Google Sheets è l'unica fonte di verità. Il sistema legge e scrive direttamente sul foglio.
2. **OSRM obbligatorio** — tutte le distanze e i tempi sono calcolati su strade reali, mai in linea d'aria (haversine solo come fallback per errori parziali).
3. **Ottimizzazione per tempo** — l'algoritmo preferisce le autostrade anche se più lunghe in km, perché più veloci.
4. **Isolamento per provincia** — ogni provincia è un processo Flask separato con la propria configurazione, cache e output HTML.
5. **Architettura dual-JSON** — il file HTML della mappa è uno "shell" statico; i dati vengono serviti come file JSON separati (`data/{provincia}/mappa_data.json` e `data/{provincia}/config.json`), aggiornati via SSE per refresh rapidi senza ricaricare la pagina.
6. **Cache aggressiva** — le distanze OSRM vengono cachate per evitare di ricalcolare percorsi invariati. L'ordine dei percorsi viene anch'esso cachato.

---

## 15. Struttura del progetto

```
mappa_algeco_nuova/
│
├── server.py                    # Entry point: launcher + server Flask
├── config.py                    # Configurazioni globali multi-provincia
├── requirements.txt             # Dipendenze Python
├── credenziali.json             # Credenziali Google API (non in git)
├── users.json                   # Database utenti login
│
├── login.html                   # Pagina di login
├── distinte.html                # Pagina gestione distinte/WhatsApp
├── mappa_torino.html            # Mappa generata (auto, non editare)
├── mappa_pavia.html             # Mappa generata (auto, non editare)
│
├── gestione_utenti.py           # CLI per gestione utenti
├── genera_fasce_orarie.py       # CLI per Excel fasce orarie
├── genera_excel_lettere.py      # CLI per Excel lettere clienti
│
├── cache_distanze_torino.json   # Cache distanze OSRM (auto)
├── cache_distanze_pavia.json    # Cache distanze OSRM (auto)
├── cache_hash_torino.json       # Hash per invalidazione cache (auto)
├── cache_hash_pavia.json        # Hash per invalidazione cache (auto)
├── cache_ordine_percorsi_*.json # Cache ordine percorsi TSP (auto)
├── report_distanze.csv          # Report distanze ultimo sync (auto)
│
├── GUIDA_CLOUDFLARE.md          # Guida configurazione Cloudflare
├── DOCUMENTAZIONE.md            # Questo file
│
├── data/                        # Dati JSON per il frontend
│   ├── torino/
│   │   ├── config.json          # Config statica (deposito, colori, ecc.)
│   │   └── mappa_data.json      # Dati contatori (aggiornato ogni sync)
│   └── pavia/
│       ├── config.json
│       └── mappa_data.json
│
└── moduli/                      # Moduli Python del backend
    ├── __init__.py              # Re-export API pubbliche
    ├── auth.py                  # Autenticazione e gestione sessioni
    ├── cache.py                 # Cache persistente distanze
    ├── data_loader.py           # Caricamento dati da Google Sheets
    ├── distanze.py              # Calcolo distanze via OSRM
    ├── distinta_parser.py       # (vuoto — funzionalità in distinte_manager)
    ├── distinte_manager.py      # Parser PDF distinte + WhatsApp
    ├── fasce_orarie.py          # Assegnazione fasce orarie
    ├── gestionale.py            # Generazione HTML/CSS/JS gestionale
    ├── mappa_html.py            # Generazione mappa HTML completa
    ├── percorso.py              # Ottimizzazione percorso (TSP)
    ├── report_generator.py      # Generazione report Excel
    └── zone_geografiche.py      # Clustering zone geografiche
```

### File generati automaticamente (non modificare)

- `mappa_torino.html` / `mappa_pavia.html` — rigenerati ad ogni sync
- `data/*/mappa_data.json` — dati contatori aggiornati
- `cache_*.json` — cache per le performance
- `report_distanze.csv` — ultimo report distanze
- `.flask_secret_key` — chiave segreta Flask persistente

---

## 16. Flusso dati

### Ciclo di sincronizzazione (ogni 180 secondi)

```
Google Sheets (FILONE)
        │
        ▼
  1. data_loader.py
     └─ carica_dati_google_sheets()
        ├─ Scarica foglio via gspread
        ├─ Normalizza colonne (trova_colonna fuzzy match)
        ├─ Filtra: rimuove ESITO OK, date fuori range, no coordinate
        ├─ Rileva appuntamenti (regex su NOTE APP)
        └─ Risultato: pandas DataFrame pulito
        │
        ▼
  2. distanze.py
     └─ genera_report_distanze_con_cache()
        ├─ Raggruppa per tecnico + data
        ├─ Controlla hash cache (skip se dati invariati)
        ├─ Calcola matrice distanze OSRM (blocchi da 80)
        ├─ Ottimizza percorso (percorso.py → TSP)
        ├─ Assegna fasce orarie (fasce_orarie.py)
        └─ Risultato: report con ordine, km, tempo per ogni tecnico/data
        │
        ▼
  3. zone_geografiche.py
     └─ genera_report_zone()
        ├─ Calcola centroide per comune
        ├─ Clustering DBSCAN (raggio configurabile)
        ├─ Unisce zone piccole (<10-15 contatori)
        ├─ Nomina zone (direzione cardinale + comune principale)
        └─ Risultato: mappa zone con statistiche
        │
        ▼
  4. mappa_html.py
     └─ genera_mappa_html()
        ├─ Prepara dati contatori con percorsi
        ├─ Calcola rotte OSRM per le linee sulla mappa
        ├─ Genera HTML completo (Leaflet.js + gestionale.py)
        ├─ Salva data/{provincia}/mappa_data.json
        ├─ Salva data/{provincia}/config.json
        └─ Salva mappa_{provincia}.html
        │
        ▼
  5. SSE notify
     └─ Invia evento 'sync_complete' a tutti i browser connessi
```

### Flusso di modifica (utente → Google Sheets)

```
Browser (popup/sidebar)
    │
    ├─ PUT /api/contatore/<eneltel>
    │   └─ server.py → update_row_fields() → gspread.batch_update()
    │
    ├─ POST /api/upload-BOXBEAT
    │   └─ server.py → legge Excel → match POD → batch_update()
    │
    ├─ PUT /api/sigillo/<eneltel>
    │   └─ server.py → update_acell() + format() giallo
    │
    └─ POST /api/distinte/*
        └─ server.py → update_cell() / format()
    │
    ▼
Google Sheets (FILONE) aggiornato
    │
    ▼
trigger_sync_after_edit()
    └─ Sync immediato → rigenera mappa → SSE notify
```

---

## 17. Moduli — Riferimento completo

### `moduli/__init__.py`

Re-esporta le funzioni pubbliche principali per import semplificato:

```python
from moduli import carica_cache, salva_cache, carica_dati_google_sheets, genera_mappa_html, ...
```

---

### `moduli/data_loader.py`

**Scopo:** Carica tutti i dati dal foglio Google Sheets e li trasforma in un DataFrame pandas pulito.

| Funzione                        | Descrizione                                                |
| ------------------------------- | ---------------------------------------------------------- |
| `carica_dati_google_sheets()` | Entry point principale. Ritorna `pd.DataFrame`           |
| `trova_colonna(df, nomi)`     | Trova colonne con match case-insensitive                   |
| `parse_data_flessibile(val)`  | Gestisce formati data misti (dd/mm/yyyy, yyyy-mm-dd, ecc.) |

**Colonne output principali:**
`DATA_K`, `NOTE_APP_RAW`, `IS_APPUNTAMENTO`, `ORA_APPUNTAMENTO`, `Eneltel_Key`, `CLI_K`, `VIA_K`, `COMUNE`, `TEL_K`, `TEC_K`, `POD_K`, `Lat`, `Lon`, `CIVICO_K`, `TIPO_CE_K`, `LCL`, `LOCALITA`, `NOTE_K`

**Filtri applicati:**

- Rimuove righe con `ESITO OK` (già installati)
- Rimuove righe con `DATA_MANCANTE`
- Rimuove righe senza coordinate GPS valide
- Filtra per operatori VIP (`MATRICOLE_VIP`)
- Filtra per range date (`DATA_INIZIO` — `DATA_FINE`)

---

### `moduli/cache.py`

**Scopo:** Cache persistente su file JSON per evitare ricalcoli OSRM inutili.

| Funzione                                       | Descrizione                                                        |
| ---------------------------------------------- | ------------------------------------------------------------------ |
| `genera_hash_dati(df_tecnico)`               | Genera hash MD5 delle colonne rilevanti                            |
| `carica_cache()`                             | Carica `cache_distanze_{prov}.json` e `cache_hash_{prov}.json` |
| `salva_cache(cache, hash)`                   | Salva su disco                                                     |
| `pulisci_cache_vecchia(cache, hash, giorni)` | Attualmente no-op (cache permanente)                               |

**Strategia:** se l'hash dei dati di un tecnico/data non è cambiato, i percorsi non vengono ricalcolati.

---

### `moduli/distanze.py`

**Scopo:** Calcolo matrice distanze/durate tramite OSRM Docker. Modulo critico per le performance.

| Funzione                                                        | Descrizione                                          |
| --------------------------------------------------------------- | ---------------------------------------------------- |
| `calcola_matrice_distanze_osrm(punti, deposito)`              | OSRM Table API, blocchi da 80 coordinate             |
| `ottieni_matrice_distanze(punti, deposito)`                   | Wrapper, errore se OSRM offline                      |
| `calcola_distanza_haversine(lat1, lon1, lat2, lon2)`          | Fallback per errori parziali                         |
| `calcola_rotta_osrm(coords_list)`                             | Rotta singola con geometria per le linee sulla mappa |
| `calcola_distanze_percorso(percorso, deposito)`               | Distanze gamba per gamba                             |
| `genera_report_distanze_con_cache(df, deposito, cache, hash)` | **Entry point principale**                     |
| `stampa_report_distanze(report)`                              | Output console                                       |
| `esporta_report_csv(report)`                                  | Salva `report_distanze.csv`                        |

**Algoritmo blocchi:** per matrici >80 punti, divide in sotto-blocchi, calcola le sotto-matrici, e assembla la matrice completa. Haversine × `FATTORE_CORREZIONE_HAVERSINE=1.35` come fallback per singoli blocchi OSRM falliti.

**Costanti importanti:**

- `OSRM_MAX_TABLE_SIZE = 80` — dimensione massima blocco OSRM
- `OSRM_TIMEOUT = 30` — timeout richieste
- `VELOCITA_MEDIA_KMH = 35` — per stima tempi fallback
- `TEMPO_INTERVENTO_MIN = 20` — tempo medio per sostituzione contatore

---

### `moduli/percorso.py`

**Scopo:** Cuore dell'ottimizzazione — risolve il problema del commesso viaggiatore (TSP) con vincoli di appuntamento.

| Funzione                                         | Descrizione                              |
| ------------------------------------------------ | ---------------------------------------- |
| `ordina_percorso(punti, deposito, usa_osrm)`   | **Entry point principale**         |
| `raggruppa_in_microzone(punti, raggio_km)`     | Clustering spaziale per evitare "zigzag" |
| `nearest_neighbor_matrice(matrice, n, start)`  | Nearest neighbor multi-start             |
| `two_opt_improve_matrice(percorso, matrice)`   | Miglioramento 2-opt                      |
| `or_opt_improve_matrice(percorso, matrice)`    | Miglioramento Or-opt                     |
| `three_opt_improve_matrice(percorso, matrice)` | Miglioramento 3-opt                      |

**Pipeline di ottimizzazione:**

1. **Micro-zone** (raggio 500m) — raggruppa contatori vicini
2. **Nearest neighbor** — trova soluzione iniziale da più punti di partenza
3. **2-opt** — scambia coppie di archi per ridurre il tempo totale
4. **Or-opt** — sposta segmenti di 1-3 nodi in posizioni migliori
5. **3-opt** — rompe 3 archi e riconnette in modo ottimale
6. **2-opt finale** — pulizia finale

**Gestione appuntamenti:**

- Gli appuntamenti sono "ancore temporali" fisse nel percorso
- I contatori senza appuntamento vengono inseriti nei gap tra appuntamenti
- Appuntamenti serali (>17:00 TO, >15:00 PV) vengono messi a fine giornata
- `ORA_INIZIO_GIORNATA` configurabile per provincia

---

### `moduli/fasce_orarie.py`

**Scopo:** Assegna fasce orarie ai contatori in base all'ordine di visita.

| Funzione                           | Descrizione                                        |
| ---------------------------------- | -------------------------------------------------- |
| `assegna_fasce(percorso)`        | Assegna fasce sequenziali (4 contatori per fascia) |
| `formatta_fascia_oraria(fascia)` | `'8/11'` → `'dalle 8:00 alle 11:00'`          |
| `get_colore_fascia(fascia)`      | Colore hex per ogni fascia                         |
| `calcola_max_tappe_giorno()`     | 6 fasce × 4 tappe = 24                            |

**Fasce definite:** `['8/11', '10/13', '11/14', '12/15', '13/16', '14/17']`

**Logica:** 4 contatori per fascia, assegnati sequenzialmente seguendo l'ordine del percorso ottimizzato.

---

### `moduli/zone_geografiche.py`

**Scopo:** Raggruppa i comuni in zone geografiche operative tramite clustering.

| Funzione                                             | Descrizione                                               |
| ---------------------------------------------------- | --------------------------------------------------------- |
| `genera_report_zone(df, filename)`                 | **Entry point** — clustering + naming + calendario |
| `calcola_centroidi_comuni(df)`                     | Centroide GPS per ogni comune                             |
| `crea_cluster_zone(comuni_data, cella_km)`         | Clustering (DBSCAN o griglia)                             |
| `crea_cluster_zone_dbscan(comuni_data, cella_km)`  | DBSCAN con eps auto                                       |
| `crea_cluster_zone_griglia(comuni_data, cella_km)` | Griglia fallback                                          |
| `unisci_zone_piccole(zone, min, max_dist)`         | Merge zone con <10-15 contatori                           |
| `assegna_nomi_zone(zone, centroidi)`               | Nome: direzione + comune più grande                      |
| `genera_calendario_zone(df, zone, centroidi)`      | Calendario date per zona                                  |

**Algoritmo DBSCAN:** `eps = cella_km × 0.625 / 111` (conversione km → gradi), `min_samples = 1`.

---

### `moduli/mappa_html.py`

**Scopo:** Genera l'intera mappa HTML interattiva (7000+ righe) con Leaflet.js.

| Funzione                                          | Descrizione                           |
| ------------------------------------------------- | ------------------------------------- |
| `genera_mappa_html(df, report, zone, filename)` | **Entry point**                 |
| `prepara_dati_contatori(df, report)`            | Prepara dati per JSON                 |
| `prepara_dati_zone(df, zone)`                   | Prepara zone per JSON                 |
| `calcola_ordine_percorsi(df, deposito)`         | Pre-calcola ordine TSP (con cache)    |
| `prepara_rotte_per_data(df, deposito)`          | Rotte OSRM per linee mappa            |
| `salva_dati_json(dati)`                         | Salva `data/{prov}/mappa_data.json` |
| `salva_config_statica(config)`                  | Salva `data/{prov}/config.json`     |
| `genera_html_completo()`                        | Genera HTML completo                  |

**Contenuto HTML generato:**

- Mappa Leaflet.js con tile OpenStreetMap
- Marker colorati con numeri d'ordine
- Linee percorso animabili
- Popup con tutti i dati e pulsanti modifica
- Filtri (data, operatore, LCL, ricerca)
- Toggle tema chiaro/scuro
- Auto-refresh via SSE
- Sidebar gestionale (da `gestionale.py`)

---

### `moduli/gestionale.py`

**Scopo:** Genera il CSS, JavaScript e HTML per le funzionalità gestionali embeddati nella mappa.

| Funzione                      | Descrizione                                         |
| ----------------------------- | --------------------------------------------------- |
| `genera_css_gestionale()`   | CSS per modali, form, sidebar                       |
| `genera_js_gestionale()`    | JS per editing, BOXBEAT, sigilli, matricole, FILONE |
| `genera_html_gestionale()`  | HTML per modali BOXBEAT, sigilli                    |
| `genera_pulsanti_sidebar()` | Bottoni HTML della sidebar                          |

---

### `moduli/distinte_manager.py`

**Scopo:** Parsing PDF "DistintaBase" di Enel, classificazione contatti, generazione messaggi WhatsApp.

| Funzione                                    | Descrizione                                 |
| ------------------------------------------- | ------------------------------------------- |
| `parse_distinta_pdf(path)`                | Estrae lavori da PDF (pdfplumber)           |
| `parse_multiple_distinte(paths)`          | Multi-file                                  |
| `is_cellulare(numero)`                    | Verifica prefisso mobile italiano (320-399) |
| `is_numero_valido(numero)`                | Validazione numero                          |
| `formatta_numero_whatsapp(numero)`        | Formato `+39...`                          |
| `genera_messaggio_whatsapp(lavoro, prov)` | Template messaggio                          |
| `classifica_contatti(lavori, filone)`     | Divide: cellulare/fisso/senza_telefono      |
| `confronta_con_filone(lavori, filone)`    | Rileva differenze PDF ↔ FILONE             |
| `estrai_telefono_da_testo(testo)`         | Regex estrazione numero                     |

---

### `moduli/auth.py`

**Scopo:** Autenticazione Flask con sessioni.

| Funzione                              | Descrizione                     |
| ------------------------------------- | ------------------------------- |
| `init_auth(app)`                    | Configura secret key + sessioni |
| `login_required(f)`                 | Decorator di protezione route   |
| `authenticate(user, pass)`          | Verifica credenziali            |
| `hash_password(pass)`               | PBKDF2-SHA256, 100k iterazioni  |
| `verify_password(pass, hash, salt)` | Verifica hash                   |
| `create_user(user, pass)`           | Crea utente                     |
| `delete_user(user)`                 | Elimina utente                  |
| `reset_password(user, pass)`        | Cambia password                 |

**Storage:** `users.json` — `{username: {password_hash, salt, created_at, last_login, active}}`

---

### `moduli/report_generator.py`

**Scopo:** Genera report Excel completi.

| Funzione                               | Descrizione                    |
| -------------------------------------- | ------------------------------ |
| `genera_report_completo(df, report)` | Excel con 6 fogli              |
| `genera_excel_comuni_per_data(df)`   | Excel semplificato comuni/date |

**Fogli del report:**

1. **Riepilogo** — statistiche generali
2. **Per Data** — conteggi per data
3. **Per Tecnico** — statistiche per operatore
4. **Appuntamenti** — dettaglio appuntamenti
5. **Calendario Comuni** — pianificazione per comune
6. **Tappe Tecnici** — percorso ordinato con fasce orarie

---

## 18. API REST — Riferimento completo

Tutte le API (tranne `/api/config` e `/api/login`) richiedono autenticazione.

### Autenticazione

| Metodo | Endpoint        | Descrizione                                            |
| ------ | --------------- | ------------------------------------------------------ |
| GET    | `/login`      | Pagina di login HTML                                   |
| POST   | `/api/login`  | Login. Body:`{"username": "...", "password": "..."}` |
| POST   | `/api/logout` | Logout                                                 |
| GET    | `/api/user`   | Utente corrente                                        |
| GET    | `/logout`     | Logout + redirect                                      |

### Mappa e configurazione

| Metodo | Endpoint         | Descrizione                                   |
| ------ | ---------------- | --------------------------------------------- |
| GET    | `/`            | Mappa HTML della provincia attiva             |
| GET    | `/api/config`  | Configurazione provincia (non protetto)       |
| GET    | `/api/events`  | Stream SSE per notifiche real-time            |
| GET    | `/data/<path>` | File JSON dalla cartella `data/` (no cache) |

### Contatori

| Metodo | Endpoint                                          | Descrizione                                              |
| ------ | ------------------------------------------------- | -------------------------------------------------------- |
| GET    | `/api/contatore/<eneltel>`                      | Dati di un contatore specifico                           |
| PUT    | `/api/contatore/<eneltel>`                      | Aggiorna campi. Body:`{"campo": "valore", ...}`        |
| POST   | `/api/contatore/<eneltel>/appuntamento`         | Imposta appuntamento. Body:`{"orario": "10:00-12:00"}` |
| POST   | `/api/contatore/<eneltel>/rimuovi-appuntamento` | Rimuove appuntamento                                     |

### BOXBEAT

| Metodo | Endpoint                | Descrizione                                 |
| ------ | ----------------------- | ------------------------------------------- |
| POST   | `/api/upload-BOXBEAT` | Upload file Excel Enel. Multipart:`files` |

### Sigilli

| Metodo | Endpoint                   | Descrizione                                            |
| ------ | -------------------------- | ------------------------------------------------------ |
| GET    | `/api/sigilli/pending`   | Contatori (trifase/fotovolt.) installati senza sigillo |
| PUT    | `/api/sigillo/<eneltel>` | Salva sigillo. Body:`{"sigillo": "..."}`             |

### Matricole

| Metodo | Endpoint                     | Descrizione                                    |
| ------ | ---------------------------- | ---------------------------------------------- |
| GET    | `/api/matricole/pending`   | Contatori installati senza matricola           |
| PUT    | `/api/matricola/<eneltel>` | Salva matricola. Body:`{"matricola": "..."}` |

### FILONE (Vista Excel)

| Metodo | Endpoint                      | Descrizione                                        |
| ------ | ----------------------------- | -------------------------------------------------- |
| GET    | `/api/filone`               | Tutti i dati del foglio Google                     |
| PUT    | `/api/filone/row/<row_num>` | Aggiorna riga. Body:`{"colonna": "valore", ...}` |

### Distinte

| Metodo | Endpoint                                 | Descrizione                                                                         |
| ------ | ---------------------------------------- | ----------------------------------------------------------------------------------- |
| GET    | `/distinte`                            | Pagina gestione distinte HTML                                                       |
| POST   | `/api/distinte/parse-distinta`         | Upload PDF distinte. Multipart:`files`                                            |
| POST   | `/api/distinte/sovrascrivi`            | Sovrascrivi valore FILONE. Body:`{"pod": "...", "campo": "...", "valore": "..."}` |
| POST   | `/api/distinte/salva-nota`             | Salva nota. Body:`{"pod": "...", "nota": "...", "sovrascrivere": true}`           |
| POST   | `/api/distinte/strikethrough-telefono` | Barra telefono. Body:`{"pod": "...", "strikethrough": true}`                      |
| POST   | `/api/distinte/segna-errato`           | Segna numero errato. Body:`{"pod": "..."}`                                        |
| POST   | `/api/distinte/appuntamento`           | Appuntamento via POD. Body:`{"pod": "...", "orario": "..."}`                      |
| POST   | `/api/distinte/aggiorna-data`          | Aggiorna data. Body:`{"pod": "...", "data": "DD/MM/YYYY"}`                        |
| POST   | `/api/distinte/salva-telefono`         | Salva telefono. Body:`{"pod": "...", "telefono": "...", "fonte": "..."}`          |

### Sincronizzazione

| Metodo | Endpoint             | Descrizione                    |
| ------ | -------------------- | ------------------------------ |
| POST   | `/api/sync`        | Forza sincronizzazione manuale |
| GET    | `/api/sync/status` | Stato del sync in background   |

---

## 19. Configurazione (`config.py`)

### Struttura `PROVINCE`

Ogni provincia è definita come un dizionario in `PROVINCE`:

```python
PROVINCE = {
    'TORINO': {
        'nome': 'Torino',                          # Nome visualizzato
        'id_foglio': '1ZYDay6F...',                 # ID Google Sheets
        'gid_scheda': 2061618218,                   # GID del worksheet
        'deposito': {'Nome': '...', 'Lat': ..., 'Lon': ...},  # Punto partenza
        'operatori_vip': ['AE101392', 'AG49808'],   # Matricole tecnici attivi
        'colori_operatori': {'AE101392': '#e74c3c'}, # Colore mappa per tecnico
        'operatori_dict': {'Nome': 'MATRICOLA'},     # Per dropdown gestionale
        'file_report': 'report_torino.xlsx',
        'file_mappa': 'mappa_torino.html',
        'ora_inizio_giornata': 9 * 60,               # Minuti da mezzanotte
    },
    'PAVIA': { ... }
}
```

### Costanti critiche

| Costante                         | Valore                          | Descrizione                            |
| -------------------------------- | ------------------------------- | -------------------------------------- |
| `OSRM_SERVER`                  | `http://localhost:5000`       | Server OSRM Docker                     |
| `OSRM_TIMEOUT`                 | 30                              | Timeout secondi per richieste OSRM     |
| `VELOCITA_MEDIA_KMH`           | 35                              | Velocità media per stime fallback     |
| `TEMPO_INTERVENTO_MIN`         | 20                              | Minuti per sostituzione contatore      |
| `OTTIMIZZA_PER_TEMPO`          | `True`                        | `True` = preferisce autostrade       |
| `RAGGIO_MICROZONA_KM`          | 0.5                             | Raggio raggruppamento contatori vicini |
| `INTERVALLO_SYNC_SECONDI`      | 180                             | Intervallo sync automatico (3 min)     |
| `DATA_INIZIO` / `DATA_FINE`  | `2025-10-01` / `2026-03-31` | Range date filtro                      |
| `DIMENSIONE_CELLA_ZONA_KM`     | 8                               | Raggio clustering zone DBSCAN          |
| `FATTORE_CORREZIONE_HAVERSINE` | 1.35                            | Moltiplicatore distanza linea d'aria   |

### Campi modificabili per provincia

`CAMPI_MODIFICABILI` definisce quali colonne del foglio Google sono editabili dall'interfaccia web:

- **Torino:** DATA PIAN, NOTE APP, NOMINATIVO, Indirizzo, Num, telefono, Posizione_Ante_Mis, Operatore
- **Pavia:** Posizione misuratore, Nominativo, Telefono, Via, civico, PROG, Ora pian, Operatore

### Variabili d'ambiente

| Variabile         | Descrizione                                |
| ----------------- | ------------------------------------------ |
| `PROVINCIA`     | Provincia attiva (`TORINO` / `PAVIA`)  |
| `FLASK_PORT`    | Porta del server Flask (default: 5001)     |
| `_SERVER_CHILD` | Indica processo figlio Flask (`1` = sì) |

---

## 20. Sistema di cache

### File di cache

| File                                  | Contenuto                           | Chiave                 |
| ------------------------------------- | ----------------------------------- | ---------------------- |
| `cache_distanze_{prov}.json`        | Matrici distanze/durate OSRM        | `"{tecnico}_{data}"` |
| `cache_hash_{prov}.json`            | Hash MD5 dei dati per invalidazione | `"{tecnico}_{data}"` |
| `cache_ordine_percorsi_{prov}.json` | Ordine percorsi TSP pre-calcolati   | `"{tecnico}_{data}"` |

### Strategia di invalidazione

1. Per ogni coppia **tecnico + data**, viene calcolato un **hash MD5** dei dati rilevanti (coordinate, appuntamenti, fasce orarie)
2. Se l'hash è identico al sync precedente, le distanze NON vengono ricalcolate
3. Se l'hash è diverso (nuovo contatore, cambio posizione, ecc.), la cache per quella chiave viene invalidata e ricalcolata
4. La cache è **permanente** (nessuna scadenza temporale)

### Quando viene invalidata

- Aggiunta/rimozione di un contatore
- Cambio coordinate
- Cambio appuntamento
- Cambio operatore assegnato
- Cambio data pianificata

---

## 21. Algoritmo di ottimizzazione percorsi

Il modulo `percorso.py` implementa un risolutore TSP (Travelling Salesman Problem) specifico per il contesto delle sostituzioni contatori.

### Pipeline completa

```
Input: lista punti con coordinate + info appuntamenti
  │
  ▼
1. RAGGRUPPAMENTO MICROZONE (raggio 0.5 km)
   └─ I contatori entro 500m vengono raggruppati
      per evitare percorsi "zigzag" tra zone
  │
  ▼
2. SEPARAZIONE APPUNTAMENTI
   └─ Gli appuntamenti sono "ancore temporali"
      che fissano quando il tecnico deve trovarsi in un punto
  │
  ▼
3. NEAREST NEIGHBOR MULTI-START
   └─ Prova N punti di partenza diversi
      e tiene il percorso più breve
  │
  ▼
4. MIGLIORAMENTO 2-OPT
   └─ Scambia coppie di archi (A-B, C-D → A-C, B-D)
      se riduce il tempo totale
  │
  ▼
5. MIGLIORAMENTO OR-OPT
   └─ Sposta segmenti di 1-3 contatori
      in posizioni migliori del percorso
  │
  ▼
6. MIGLIORAMENTO 3-OPT
   └─ Rompe 3 archi e riconnette
      (8 possibili riconnessioni per ogni tripletta)
  │
  ▼
7. 2-OPT FINALE
   └─ Pulizia finale dopo 3-opt
  │
  ▼
Output: lista punti ordinati con tempo cumulativo
```

### Gestione appuntamenti nel percorso

- Gli appuntamenti con orario specifico vengono posizionati nel percorso considerando il tempo di viaggio
- I contatori senza appuntamento riempiono i "gap" tra un appuntamento e l'altro
- Appuntamenti serali (>17:00 Torino, >15:00 Pavia) → fine giornata
- La matrice distanze/durate è calcolata con OSRM (strade reali, autostrade incluse)

### Perché le autostrade?

Con `OTTIMIZZA_PER_TEMPO=True`, OSRM restituisce il percorso più **veloce**, non il più breve in km. Questo significa che:

- L'autostrada viene preferita quando abbatte i tempi (es. Torino → periferia)
- Il percorso evita il traffico del centro città
- I tecnici di Torino usano la tangenziale/autostrada, non attraversano il centro

---

## 22. Sistema di sincronizzazione

### Sync automatico

Un **thread daemon** esegue `sincronizza_dati()` ogni `INTERVALLO_SYNC_SECONDI` (default: 180 secondi = 3 minuti).

```python
def background_sync_loop():
    while _sync_running:
        sincronizza_dati()
        sleep(INTERVALLO_SYNC_SECONDI)
```

### Sync dopo modifica

Quando un utente modifica dati dall'interfaccia web (popup, sidebar, distinte), viene chiamato `trigger_sync_after_edit()`:

- Avvia un sync immediato in un thread separato (non blocca la risposta HTTP)
- Se un sync è già in corso, la richiesta viene messa in coda ("pending")
- Al termine del sync corrente, il sync pendente viene eseguito automaticamente
- Evita sync simultanei con un lock

### Notifiche SSE

Dopo ogni sync completato, il server invia un evento SSE a tutti i browser connessi:

```
event: sync_complete
data: {"source": "background_sync", "timestamp": "20260210143022"}
```

Il JavaScript nel browser riceve l'evento e ricarica i dati JSON dalla cartella `data/`.

---

## 23. Aggiungere una nuova provincia

### Passo 1 — Configura `config.py`

Aggiungi un nuovo blocco in `PROVINCE`:

```python
PROVINCE = {
    'TORINO': { ... },
    'PAVIA': { ... },
    'NUOVA_PROVINCIA': {
        'nome': 'Nuova Provincia',
        'id_foglio': 'ID_GOOGLE_SHEETS',
        'gid_scheda': GID_WORKSHEET,
        'deposito': {'Nome': 'DEPOSITO', 'Lat': XX.XXXX, 'Lon': YY.YYYY},
        'operatori_vip': ['OP1', 'OP2'],
        'colori_operatori': {'OP1': '#e74c3c', 'OP2': '#f1c40f'},
        'operatori_dict': {'Operatore 1': 'OP1', 'Operatore 2': 'OP2'},
        'file_report': 'report_nuova.xlsx',
        'file_mappa': 'mappa_nuova.html',
        'ora_inizio_giornata': 8 * 60,
    },
}
```

### Passo 2 — Aggiorna le validazioni

In `config.py`, aggiorna:

```python
if PROVINCIA_ATTIVA not in ['TORINO', 'PAVIA', 'NUOVA_PROVINCIA']:
```

### Passo 3 — Aggiungi campi modificabili

In `CAMPI_MODIFICABILI` e `CAMPI_BOXBEAT` e `COLONNE_SIGILLI`, aggiungi le mappature per la nuova provincia.

### Passo 4 — Crea le cartelle dati

```
data/nuova_provincia/
    config.json
    mappa_data.json
```

### Passo 5 — Aggiorna `server.py`

Nel launcher, aggiungi la porta per la nuova provincia e aggiorna la logica degli argomenti CLI.

### Passo 6 — Configura il foglio Google Sheets

Il foglio deve avere le colonne attese dal `data_loader.py`. Le colonne minime sono:

- **ENELTEL** — identificativo contatore
- **POD** — Point of Delivery
- **NOMINATIVO** — nome cliente
- **Indirizzo/Via** + **civico** — indirizzo
- **COMUNE** — comune
- **Lat**, **Lon** — coordinate GPS
- **DATA PIAN** — data pianificata
- **Operatore** — tecnico assegnato
- **Esito** — stato installazione
- Colonne opzionali: telefono, note, tipologia, sigillo, matricola

---

## 24. Troubleshooting per sviluppatori

### OSRM non raggiungibile

```powershell
# Verifica container Docker
docker ps

# Testa direttamente
curl "http://localhost:5000/route/v1/driving/7.68,45.07;7.78,45.19"

# Se il container non c'è, avvialo
docker start osrm-backend
```

### Google Sheets: "Quota exceeded"

L'API Google Sheets ha limiti di richieste. Il sistema fa una singola lettura bulk (`get_all_values()`) per sync, più scritture batch. Se superi la quota:

- Aumenta `INTERVALLO_SYNC_SECONDI`
- Le `batch_update()` contano come singola chiamata

### Cache corrotta

Se la mappa mostra percorsi sbagliati:

```powershell
# Elimina tutti i file cache
Remove-Item cache_*.json
# Riavvia il server
python server.py
```

### Mappa HTML vuota o errore

Se `mappa_{provincia}.html` è vuota o corrotta:

- Controlla i log del sync nel terminale
- Verifica che `data/{provincia}/mappa_data.json` contenga dati validi
- Riavvia il server (la mappa viene rigenerata al primo sync)

### Debug API

Tutte le API producono log dettagliati nel terminale. Per debug aggiuntivo:

```python
# In server.py, attiva debug Flask (solo sviluppo!)
app.run(host='0.0.0.0', port=FLASK_PORT, debug=True, threaded=True)
```

### Errore "Colonna X non trovata"

Il `data_loader.py` usa `trova_colonna()` con match case-insensitive. Se il nome della colonna nel foglio Google è cambiato, aggiorna i nomi possibili nella funzione.

### Permessi Google Sheets

Se le scritture falliscono:

1. Verifica che il Service Account abbia permessi di **Editore** sul foglio
2. Controlla che `credenziali.json` non sia scaduto
3. Verifica che l'ID foglio e il GID scheda siano corretti in `config.py`

### Performance lente

| Problema                | Soluzione                                                                |
| ----------------------- | ------------------------------------------------------------------------ |
| Sync lento (>2 min)     | Cache funzionante? Controlla `cache_hash_*.json`                       |
| Mappa lenta nel browser | Troppi contatori nel range date? Restringi `DATA_INIZIO`/`DATA_FINE` |
| OSRM lento              | Controlla risorse Docker (CPU/RAM)                                       |
| Google Sheets lento     | Quota API? Connessione Internet?                                         |

### Struttura log

I log nel terminale usano prefissi per agevolare il debug:

| Prefisso        | Modulo                     |
| --------------- | -------------------------- |
| `[SYNC]`      | Thread di sincronizzazione |
| `[SSE]`       | Server-Sent Events         |
| `[BOXBEAT]`   | Import BOXBEAT             |
| `[SIGILLI]`   | Gestione sigilli           |
| `[MATRICOLE]` | Gestione matricole         |
| `[DISTINTE]`  | Gestione distinte          |
| `🗺️`        | Configurazione provincia   |
| `🚗`          | Calcolo distanze           |
| `🌐`          | Generazione mappa          |

---

## Glossario

| Termine                         | Significato                                                               |
| ------------------------------- | ------------------------------------------------------------------------- |
| **FILONE**                | Il foglio Google Sheets principale con tutti i contatori da sostituire    |
| **ENELTEL**               | Codice identificativo univoco del contatore Enel                          |
| **POD**                   | Point of Delivery — codice punto di consegna energia                     |
| **BOXBEAT**               | File Excel di ritorno da Enel con gli esiti delle installazioni           |
| **Distinta/DistintaBase** | Ordine di lavoro PDF da Enel con i dati delle sostituzioni programmate    |
| **OSRM**                  | Open Source Routing Machine — calcolo percorsi stradali reali            |
| **LCL**                   | Codice località/area del contatore                                       |
| **Fascia oraria**         | Intervallo di 3 ore in cui il tecnico prevede di arrivare                 |
| **Micro-zona**            | Raggruppamento di contatori entro 500m per evitare zigzag                 |
| **TSP**                   | Travelling Salesman Problem — ottimizzazione dell'ordine di visita       |
| **SSE**                   | Server-Sent Events — tecnologia per notifiche push dal server al browser |
| **Sigillo**               | Sigillo di sicurezza applicato ai contatori trifase/fotovoltaici          |

---

*Documento generato automaticamente. Per aggiornamenti, contattare il team di sviluppo.*
