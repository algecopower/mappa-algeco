# Apps Script — Sync near-realtime da Google Sheets

> **⚠️ DOCUMENTO OBSOLETO — 21/05/2026**
>
> Google Sheets è stato **scollegato** dal gestionale. La fonte di verità è ora il **DB locale SQLite** per provincia, alimentato da:
>
> - scritture dirette dalla UI (mappa, distinte, sigilli/matricole)
> - import Excel manuale del FILONE (`moduli/excel_importer.py`)
> - report Beat caricati da `📦 Carica installato`
>
> Per i dettagli del flusso attuale vedi [DB_LOCALE_README.md](DB_LOCALE_README.md) e la sezione "Import dati esterni" di [DOCUMENTAZIONE.md](DOCUMENTAZIONE.md).
>
> Il presente file resta come **riferimento storico** del trigger Apps Script che era stato progettato (ma mai installato) per ridurre la latenza del sync. **Non eseguire i passi descritti sotto**: il webhook server-side è ancora montato in [routes/sync.py](routes/sync.py) ma `moduli/sync_engine.py` è uno stub no-op, quindi una chiamata non produrrebbe alcun effetto reale.

---

## Storia (perché era stato pensato)

L'idea era ridurre la latenza delle modifiche fatte su Google Sheets verso la mappa: il polling girava a ~60 s e un onEdit Apps Script avrebbe portato l'aggiornamento sotto i 5 s, postando a `POST /api/sync/webhook` con un secret in header.

Il 21/05/2026 il sistema automatico di dispatch ha causato uno span massivo (numeri di telefono spostati nella colonna SIGILLO perché matchavano il pattern `\d{8,14}`). Per evitare di ripetere l'incidente è stato deciso di:

1. scollegare completamente Sheets come fonte di verità
2. spostare tutto sul DB locale SQLite
3. far entrare i dati esterni solo via import Excel **manuale** del FILONE (vedi `moduli/excel_importer.py`)

A quel punto il trigger Apps Script ha perso ogni scopo.

## Se in futuro si volesse ricollegare Sheets

Sconsigliato — la decisione di scollegare era motivata da un incidente reale. Se proprio fosse necessario:

1. Rimuovere lo stub `moduli/sync_engine.py` e ripristinare il vero motore di sync (l'ultimo commit funzionante è precedente a `b97890f`)
2. Riallineare `moduli/shared.py` (oggi instrada tutto al DB)
3. Solo allora reinstallare l'Apps Script come da istruzioni archiviate nella history di questo file (`git log --follow GUIDA_APPS_SCRIPT.md`)

## Cleanup eseguito (25/05/2026)

Rimossi: endpoint `POST /api/sync/webhook` da `routes/sync.py`, esclusione CSRF da `server.py`, `EnvironmentFile=/etc/mappa-algeco/sync.env` dal systemd unit, il file `sync.env` e il vecchio `credenziali.json`. Resta solo il riferimento storico in questo documento.
