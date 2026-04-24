# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Lingua

Il codebase è in italiano (commenti, variabili, log, UI, documentazione). Rispondi all'utente e scrivi commenti / log / nomi di variabili nuove in italiano per mantenere coerenza.

## Cos'è questo progetto

Gestionale web per la sostituzione contatori elettrici Enel (Algeco Power). Visualizza contatori su mappa Leaflet, ottimizza percorsi giornalieri per i tecnici (TSP su matrice OSRM), genera fasce orarie, gestisce appuntamenti/distinte/sigilli/matricole, e sincronizza tutto con un Google Sheet ("FILONE") ogni 3 minuti.

La documentazione autoritativa è in [DOCUMENTAZIONE.md](DOCUMENTAZIONE.md) (utente + sviluppatore, ~950 righe). Consultala per dettagli su API, moduli, e flusso dati.

## Comandi principali

```bash
# Ambiente: il venv è in .venv/ (non venv/ — venv/ è residuo legacy)
source .venv/bin/activate

# Avvio server (launcher → spawna un processo Flask per provincia)
python server.py                   # Torino (:5001) + Pavia (:5002)
python server.py --torino          # Solo Torino
python server.py --pavia           # Solo Pavia
python server.py --pubblico        # Entrambe + tunnel Cloudflare
python server.py --skip-osrm-check # Debug, senza OSRM
python server.py --no-browser      # Non aprire browser

# Gestione utenti login (CLI interattiva)
python gestione_utenti.py
python gestione_utenti.py --crea --username X --password Y
python gestione_utenti.py --reset --username X --password Y

# Generazione Excel da CLI
python genera_excel_lettere.py
```

### Deploy VPS (Ubuntu)

Il progetto gira in produzione su VPS Ubuntu come due servizi systemd: `osrm-server.service` e `mappa-algeco.service`. Vedi [GUIDA_DEPLOY_VPS.md](GUIDA_DEPLOY_VPS.md) e [deploy_ubuntu.sh](deploy_ubuntu.sh).

```bash
sudo systemctl status mappa-algeco
sudo systemctl restart mappa-algeco
sudo systemctl restart osrm-server
```

### OSRM

OSRM deve girare su `http://localhost:5000`. Il launcher tenta prima systemd (`osrm-server.service`, usato in produzione), poi Docker come fallback. Se OSRM è offline, il sistema chiede conferma per continuare ma i percorsi non saranno ottimizzati.

### Test

Non esistono test automatizzati. La verifica si fa avviando il server e interagendo con l'UI.

## Architettura

### Processo launcher + figli Flask

[server.py](server.py) ha due modalità nello stesso file, distinte dalla variabile d'ambiente `_SERVER_CHILD`:

1. **Launcher** (~righe 1–528): parsea CLI, verifica OSRM/Cloudflare, fa `subprocess.Popen` di sé stesso una volta per provincia settando `PROVINCIA` e `FLASK_PORT`, gestisce shutdown.
2. **Flask child** (riga ~530 in poi): legge `PROVINCIA` dall'env, carica `config.py`, registra blueprint, avvia sync loop in background.

Ogni provincia = processo separato con cache, dati JSON e HTML propri. Non c'è comunicazione inter-provincia.

### Fonte di verità: Google Sheets

**Non c'è un database locale.** Il Google Sheet ("FILONE") è l'unica fonte di verità. Tutte le mutazioni passano da `gspread` in [moduli/shared.py](moduli/shared.py) (che centralizza la connessione, il retry su 429/5xx, e il lock di scrittura `_gsheets_write_lock`). Dopo ogni scrittura si chiama `trigger_sync_after_edit()` in [server.py](server.py) per rigenerare la mappa.

Le colonne del foglio sono mappate tramite `MAPPATURA_COLONNE_DEFAULT` in [config.py](config.py) (con fuzzy-match multi-alias) e possono essere sovrascritte per provincia da `data/{provincia}/settings.json`.

### Pipeline di sync (ogni 180s, configurabile via `INTERVALLO_SYNC_SECONDI`)

```
Google Sheets
  → moduli/data_loader.py (DataFrame pulito)
  → moduli/distanze.py   (matrice OSRM blocchi da 80, cache per hash)
  → moduli/percorso.py   (TSP: nearest-neighbor + 2-opt + or-opt + 3-opt)
  → moduli/fasce_orarie.py (assegna fasce 8/11, 10/13, … 14/17)
  → moduli/zone_geografiche.py (DBSCAN su centroidi comuni)
  → moduli/mappa_html.py (HTML Leaflet + data/{prov}/mappa_data.json + config.json)
  → SSE broadcast a tutti i browser connessi
```

Il sync lavora in thread daemon (`background_sync_loop`). Le scritture utente lato sidebar triggerano sync immediato.

### Blueprint Flask

Le route sono in [routes/](routes/), registrate in [server.py:631-638](server.py#L631-L638):
- `contatori.py` — CRUD contatori, filone, bulk pianificazione, orari lettere
- `distinte.py` — parsing PDF distinte Enel, messaggi WhatsApp
- `calendario.py` — attività condivise, promemoria BoxBeat
- `sigilli.py` — sigilli e matricole installate
- `sync.py` — trigger sync manuale, backup, upload BoxBeat
- `bacheca.py`, `dashboard.py`, `report.py`, `postit.py` — feature accessorie

Tutte le route importano helper Google Sheets da [moduli/shared.py](moduli/shared.py) — non ridefinire `get_worksheet()`, `find_row_by_*`, `_header_map` nelle route, usa quelli condivisi.

### Ottimizzazione percorsi (`moduli/percorso.py`)

L'algoritmo usa **durate OSRM** (tempo), non km — quindi preferisce autostrade anche se più lunghe. Pipeline: microzone (raggio 500m per evitare zigzag) → nearest-neighbor multi-start → 2-opt → or-opt → 3-opt → 2-opt finale. Gli appuntamenti sono ancore temporali fisse; i contatori senza appuntamento vengono infilati nei gap.

`ORA_INIZIO_GIORNATA` è 9:00 a Torino, 8:00 a Pavia.

### Cache

- `cache/cache_distanze_{prov}.json` — matrici OSRM cachate per tecnico/data
- `cache/cache_hash_{prov}.json` — hash MD5 dei dati per invalidazione
- `cache/cache_ordine_percorsi_{prov}.json` — ordine TSP cachato

Se l'hash dei dati di un tecnico/data non cambia, il percorso non viene ricalcolato. Cancellare `cache/` forza ricalcolo completo al prossimo sync.

### File generati (non editare a mano, sono in `.gitignore`)

- `templates/mappa_{torino,pavia}.html` — mappa generata da `mappa_html.py` ad ogni sync
- `data/{prov}/mappa_data.json`, `data/{prov}/config.json` — payload JSON per il frontend
- `cache/*.json`, `report_distanze.csv`
- `.flask_secret_key`

### Architettura dual-JSON

Il file HTML della mappa è uno "shell" statico — i dati vivono in `data/{prov}/mappa_data.json` e `config.json`, serviti dalla route `/data/<path>` e ricaricati dal client via SSE (`/api/events`) senza ricaricare la pagina.

## Aggiungere una nuova provincia

1. Aggiungi un blocco in `PROVINCE` in [config.py](config.py) con `id_foglio`, `gid_scheda`, `deposito`, `operatori_vip`, `colori_operatori`, `ora_inizio_giornata`, `file_mappa`.
2. Aggiungi la porta in [server.py:436-439](server.py#L436-L439) e un flag CLI `--nuovaprov`.
3. Aggiungi il sottodominio Cloudflare in `SOTTODOMINI` se serve accesso pubblico.
4. Crea la cartella `data/{nuovaprov}/`.

## Convenzioni da rispettare

- **Italiano** ovunque (vedi sopra).
- **UTF-8**: tutti i file Python iniziano con `# -*- coding: utf-8 -*-`. Il launcher forza `sys.stdout.reconfigure(encoding='utf-8')` per compatibilità Windows.
- **File JSON salvati atomicamente**: usa `moduli/safe_json.py` (`json_read`, `json_write`, `json_modify`) per dati condivisi, non scrivere direttamente con `json.dump`.
- **Scritture Google Sheets**: sempre tramite `moduli/shared.py` con `_sheets_retry` e `_gsheets_write_lock`. Mai aprire una seconda connessione `gspread` in parallelo.
- **Logging**: usa `logger = logging.getLogger('mappa_algeco')`, non `print()` nelle route/moduli (il launcher usa `print()` con emoji per l'UX terminale, ma quella è l'eccezione).
- **Non editare** `templates/mappa_{torino,pavia}.html`: sono rigenerati ad ogni sync.

## File sensibili (non committare)

`credenziali.json` (Service Account Google), `users.json` (hash password PBKDF2), `.flask_secret_key`, `data/smtp_settings.json` — tutti già in `.gitignore`.
