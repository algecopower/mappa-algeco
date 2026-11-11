# Guida all'utilizzo — Gestionale Mappa Contatori

> Guida operativa completa per l'uso quotidiano della piattaforma **Mappa Algeco**.  
> Per architettura tecnica, API e deploy consulta [`DOCUMENTAZIONE.md`](DOCUMENTAZIONE.md), [`DB_LOCALE_README.md`](DB_LOCALE_README.md) e [`deploy/README.md`](deploy/README.md).

---

## Indice

1. [Accesso al sistema e Login](#1-accesso-al-sistema-e-login)
2. [Panoramica dell'interfaccia e Menu Gestionale](#2-panoramica-dellinterfaccia-e-menu-gestionale)
3. [Viste Mappa: Zone, Comuni, Dettaglio e Percorso Operatore](#3-viste-mappa-zone-comuni-dettaglio-e-percorso-operatore)
4. [Modalità Pianificazione (Assegnazione massiva sulla mappa)](#4-modalità-pianificazione-assegnazione-massiva-sulla-mappa)
5. [Filtri, Badge Orari, Ricerca Avanzata (Multi-POD/Eneltel) e Pin Multipli](#5-filtri-badge-orari-ricerca-avanzata-multi-podeneltel-e-pin-multipli)
6. [Popup, Modifica Contatori e Appuntamenti](#6-popup-modifica-contatori-e-appuntamenti)
7. [Caricamento BOXBEAT, Sigilli e Matricole](#7-caricamento-boxbeat-sigilli-e-matricole)
8. [Vista Excel Filone (`/filone`)](#8-vista-excel-filone-filone)
9. [PDF E-Distribuzione — Gestione Distinte, WhatsApp, Email e SMS (`/distinte`)](#9-pdf-e-distribuzione--gestione-distinte-whatsapp-email-e-sms-distinte)
10. [Gestione Lettere — Archivio, Aziende/PEC e SMS Privati (`/lettere`)](#10-gestione-lettere--archivio-aziendepec-e-sms-privati-lettere)
11. [Gestione Mail — Webmail SMTP/IMAP (`/mail`)](#11-gestione-mail--webmail-smtpimap-mail)
12. [Gestione Comuni e ZTL (`/comuni`)](#12-gestione-comuni-e-ztl-comuni)
13. [Modelli, Template & Frasario Centralizzato (`/templates`)](#13-modelli-template--frasario-centralizzato-templates)
14. [Calendario Attività, Bacheca Promemoria e Appuntamenti (`/calendario`, `/appuntamenti`)](#14-calendario-attività-bacheca-promemoria-e-appuntamenti-calendario-appuntamenti)
15. [Dashboard Operativa, Reportistica e Backup (`/dashboard`, `/report`)](#15-dashboard-operativa-reportistica-e-backup-dashboard-report)
16. [Pannello Admin Multi-Provincia (`admin.*`)](#16-pannello-admin-multi-provincia-admin)
17. [Aggiornamento dati in tempo reale (DB locale + SSE) e Tema](#17-aggiornamento-dati-in-tempo-reale-db-locale--sse-e-tema)
18. [Legenda colori operatori e Scorciatoie](#18-legenda-colori-operatori-e-scorciatoie)

---

## 1. Accesso al sistema e Login

### URL di accesso

In produzione tramite router multi-tenant + Cloudflare Tunnel:
- **Torino:** `https://torino.mappa-algeco.com/`
- **Pavia:** `https://pavia.mappa-algeco.com/`
- **Lecco:** `https://lecco.mappa-algeco.com/`
- **Pannello Admin Multi-Provincia (solo ruolo `admin`):** `https://admin.mappa-algeco.com/`

In sviluppo locale standalone: `http://localhost:5001/` (Torino), `http://localhost:5002/` (Pavia), `http://localhost:5003/` (Lecco).

### Login e sicurezza sessione

1. Inserisci **username** (case-insensitive) e **password** (puoi usare l'icona 👁️ per verificarla).
2. Premi **Accedi** o `Invio`.
3. La sessione resta attiva per **24 ore** ed è condivisa tra le province autorizzate e `admin.*` quando `SESSION_COOKIE_DOMAIN` è configurato.
4. Dopo **5 tentativi falliti**, l'accesso viene temporaneamente bloccato per **60 secondi** (`429 Too Many Requests`).

---

## 2. Panoramica dell'interfaccia e Menu Gestionale

La schermata principale (`/`) è composta da:
- **Sidebar sinistra** (comprimibile con il pulsante `◀` sul bordo destro): intestazione con provincia e timestamp ultimo aggiornamento, controlli vista/ricerca/pianificazione, filtri cumulativi, elenco zone/comuni/tappe e barra statistiche (`Zone`, `Contatori`, `Appuntamenti`).
- **Mappa interattiva Leaflet** sulla destra: marker per zona/comune o singolo contatore, percorsi stradali OSRM, deposito, slider zoom (`−`/`+`), pulsante `🎯 Adatta vista`, `🖨️ Stampa Mappa` e `📋 Stampa Tappe`.
- **Pulsanti floating in basso a destra:**
  - **🔔 Bacheca Promemoria** (`bacheca.js`): mostra promemoria, scadenze calendario e avvisi BOXBEAT del giorno.
  - **⊞ Menu Gestionale & Moduli** (`#gestionaleFab`): apre il lanciatore categorizzato con barra di ricerca rapida, stella per **Preferiti Rapidi** e filtro per categoria (`Operatività`, `Documenti`, `Pianificazione`, `Sistema & Admin`):
    - *Operatività:* **Orari Lettere**, **Carica Installato (BOXBEAT)**, **Sigilli & Matricole**, **Vista Excel Filone**
    - *Documenti & Comunicazioni:* **Gestione Mail**, **Gestione Lettere**, **PDF E-Distribuzione**, **Gestione Comuni**, **Modelli & Template**
    - *Pianificazione & Analisi:* **Calendario Attività**, **Gestione Appuntamenti**, **Dashboard & Statistiche**, **Reportistica Operatori**
    - *Sistema & Admin:* **Backup / Ripristino**, **Pannello Admin / Impostazioni** (Operatori, LCL e Zone, Date, Utenti per `admin`), **Disconnetti**

---

## 3. Viste Mappa: Zone, Comuni, Dettaglio e Percorso Operatore

### Vista Zone e Vista Comuni

- Il pulsante **🗺️ Zone / Comuni** nell'header della sidebar alterna il raggruppamento per **macro-zone geografiche** (generate via DBSCAN) oppure per **singolo comune**.
- Ogni card mostra riepilogo contatori, date pianificate e contatti utili.

### Vista Dettaglio (per zona/comune e data)

1. Clicca una **data** dentro una card Zona/Comune oppure seleziona una data dal filtro **Tutte le date**.
2. La barra superiore (breadcrumb) mostra zona/comune, data corrente, frecce **◀ / ▶** per cambiare giorno e il **Date Picker** cliccando sulla data centrale.
3. I contatori con appuntamento (`APP ...`) sono evidenziati sulla mappa da un'animazione pulsante e dal badge **☎️ APP**.

### Vista Percorso Operatore & Multi-Operatore

1. Selezionando una **data** dal filtro principale, compare la lista degli operatori impegnati nella giornata e il dropdown **👥 Tutti gli operatori** (con selezione singola o **multi-operatore**).
2. Per ciascun operatore il percorso viene ordinato da **Deposito → Tappa 1 → Tappa 2 → ... → Deposito** rispettando gli orari degli appuntamenti come ancore temporali.
3. Ogni tappa mostra distanza e tempo di guida stimato da OSRM (`🚗 X min (Y km)`).
4. **Stampa percorso:** usa **🖨️ Stampa Mappa** per generare la mappa di stampa con zoom regolabile e label ENELTEL anti-sovrapposizione (ultime 3 cifre) e **📋 Stampa Tappe** per il foglio tappe ordinato.

---

## 4. Modalità Pianificazione (Assegnazione massiva sulla mappa)

Clicca **📋 Pianifica** nell'header della sidebar per entrare in modalità pianificazione:

1. **Filtra** i contatori per:
   - Stato: `📋 Schedulabili` e/o `📅 Pianificati` (con ulteriore filtro per Data e Operatore sui pianificati);
   - `Nome`, `Zona`, `Comune`, `Telefono` (`Tutti` / `Con ☎️` / `Senza`), `Tipologia`, `Posizione`, `LCL`.
2. **Seleziona** i marker sulla mappa con la barra dedicata:
   - `👆` Selezione singola (clic sui marker)
   - `▭` Selezione a rettangolo
   - `✂` Selezione libera (lasso)
   - `☑` Seleziona tutti i visibili / `☐` Deseleziona tutto
3. Clicca **📅 Assegna**, scegli **Data Pianificata** e/o **Operatore**, quindi conferma: il sistema salva in bulk su SQLite (`POST /api/contatori/bulk-pianifica`) e aggiorna la mappa via SSE.

---

## 5. Filtri, Badge Orari, Ricerca Avanzata (Multi-POD/Eneltel) e Pin Multipli

### Filtri nella sidebar
- Barra di testo rapida per filtrare in tempo reale le card visibili.
- Filtro **LCL** (le LCL disabilitate dall'admin vengono nascoste automaticamente).
- Checkbox **☎️ Solo appuntamenti** (con contatore live).
- Checkbox **🕒 Badge orari per tutti** (visibile nelle viste a marker singolo e nella zoom bar) per mostrare le fasce orarie `orari_lettere` / `ora_appuntamento` su tutte le tappe con decluttering anti-collisione.

### Ricerca Utente Avanzata & Ricerca Multipla POD / Eneltel

Clicca **🔍 Ricerca Utente** per cercare in **tutto il database** della provincia tramite 6 criteri combinabili (`Nominativo`, `POD`, `Eneltel`, `Comune`, `Via`, `Telefono`):
- **Ricerca multipla attiva di default su POD ed Eneltel:** incolla elenchi di POD o Eneltel separati da spazio, virgola o a capo. Il sistema mostra il badge con il numero di token riconosciuti ed evidenzia eventuali codici non trovati nel DB.
- Spunta opzionale **Apri in Vista Zona** per isolare la zona specifica anziché la giornata provinciale.

### Ricerca Indirizzo & Pin Multipli (`📌`)

- **📍 Cerca Indirizzo:** interroga OpenStreetMap/Nominatim e posiziona un marker viola temporaneo per verifiche logistiche.
- **Pin multipli:** da popup o ricerca puoi fissare **più contatori contemporaneamente** (`📌`). Quando almeno un contatore è pinnato compare la **Floating Pinned Toolbar** con le azioni rapide: **🎯 Centra** (inquadra tutti i pin), **🔍 Cerca** e **🗑️ Rimuovi tutti**.

---

## 6. Popup, Modifica Contatori e Appuntamenti

- **Contatori sovrapposti:** più utenze alle stesse coordinate (~5 m) vengono raggruppate in un popup multiplo con lista cliccabile.
- **Modifica:** dal popup (`✏️ Modifica`), dalla sidebar (`✏️`) o dai risultati di ricerca puoi aggiornare Data Pianificata, Note/Appuntamento, Nominativo, Via, Civico, Telefono, Posizione Misuratore e Operatore.
- **Appuntamenti:** nella sezione dedicata imposta **orario inizio** e **orario fine** e salva (**☎️ Salva Appuntamento**): viene scritta l'etichetta `APP HH:MM-HH:MM` nelle note e salvato lo slot, vincolando il routing OSRM a quell'orario.
- **Aggiornamento ~50 ms:** le modifiche ai campi non strutturali applicano una patch in-place immediata e aggiornano tutti i client connessi via SSE senza ricaricare la pagina.

---

## 7. Caricamento BOXBEAT, Sigilli e Matricole

Apri dal **Menu Gestionale (`⊞`)**:

1. **📦 Carica Installato (BOXBEAT):**
   - Trascina uno o più export Excel/CSV E-Distribuzione (`.xlsx`, `.xls`, `.csv`; intestazioni attese alla riga 3).
   - Per i record con `Esito OK`, cerca il `POD` sul DB e aggiorna `Operatore`, `Data Installazione` ed `Esito = ESITO OK`.
2. **🔐 Sigilli & Matricole:**
   - **Tab Sigilli:** elenca gli installati dell'anno corrente di tipo **trifase semidiretto** e **fotovoltaico** (Eneltel terminante con `A` o `B`) privi di sigillo; inserimento rapido con `Invio` e avanzamento automatico al campo successivo.
   - **Tab Matricole:** elenca tutti gli installati dell'anno corrente privi di matricola nuovo contatore (supporta lettura barcode + salvataggio istantaneo su DB).

---

## 8. Vista Excel Filone (`/filone`)

Accessibile dal Menu Gestionale (`⊞`) → **Vista Excel Filone** oppure all'URL `/filone`:
- Tabella stile foglio di calcolo sincronizzata con SQLite `state.db` (`filone` + `filone_extra`).
- **Multi-Tab indipendenti (`➕`):** apri più schede nella stessa pagina, ciascuna con i propri filtri, ricerca e ordinamento separati.
- **Editing inline** delle celle consentite con salvataggio diretto su DB e notifica SSE alla mappa.
- **Aggiunta dati (`➕ Singolo Contatore` / `📥 Importa Excel / Nuova LCL`):** inserisci singole utenze o importa nuove LCL/fogli direttamente dal modale dedicato.
- Export dei dati visualizzati e pannello audit/commenti.

---

## 9. PDF E-Distribuzione — Gestione Distinte, WhatsApp, Email e SMS (`/distinte`)

Accessibile dal Menu Gestionale (`⊞`) → **PDF E-Distribuzione** (`/distinte`):

1. **Upload PDF:** trascina uno o più file PDF **DistintaBase** o **Moduli Maxy** E-Distribuzione ed elabora.
2. **Classificazione & Confronto con Filone:**
   - Suddivide i contatti in **Cellulare** (verde), **Fisso** (arancione) e **Senza telefono** (grigio).
   - Confronta Data, Telefono, Nominativo, Indirizzo e Comune rispetto al DB locale evidenziando le discrepanze (con azione per sovrascrivere il DB).
3. **Azioni rapide su ogni card contatto:**
   - **📱 WhatsApp / Template:** genera messaggi precompilati usando i modelli centralizzati.
   - **📧 Email:** invia email dirette via SMTP aziendale (con anteprima e allegati configurabili).
   - **💬 SMS Gateway (singolo o `📱 Invio Multiplo SMS`):** invia SMS tramite smartphone aziendale (app open source SMS Gateway for Android) o webhook HTTP con preset (`Tutti`, `Monofase`, `Trifase`, `Semidiretta`, `Con Note`, `Senza Note`), contatore GSM-7 / UCS-2 e pausa anti-flood configurabile. Ogni invio aggiorna automaticamente le note sul DB (`mandato wa` / `mandata mail` / `mandato SMS`).
   - **🔍 Cerca telefono**, **Segna errato** (barrato `ERRATO`), note rapide e fissazione appuntamento inline con aggiornamento data/orari.

---

## 10. Gestione Lettere — Archivio, Aziende/PEC e SMS Privati (`/lettere`)

Accessibile dal Menu Gestionale (`⊞`) → **Gestione Lettere** (`/lettere`) insieme alla modale **Orari Lettere** sulla mappa:

- **Orari Lettere (mappa) & Generazioni:** calcola le fasce orarie ottimizzate su percorso (`8/11`, `9/12`, `10/13`, `11/14`, `12/15`, `13/16`, `14/17`) e genera i fogli Excel multi-tab (`MONOFASE`, `EMME2`, `SEMIDIRETTI`, `SOLO MONOFASE`, `SOLO TRIFASE DIRETTI`) salvati e scaricabili dall'archivio `/lettere`.
- **Rilevamento automatico Aziende / Enti vs Privati** (`moduli/rilevamento_aziende.py`): distingue SRL/SPA/ditte individuali/condomini/enti pubblici dalle persone fisiche, con possibilità di **riclassificazione manuale** (override persistente in DB).
- **Aziende e Contatti / Invio Mail & PEC in bulk:** gestisce rubrica aziendale, allegati personalizzati per azienda (elenco contatori Excel generato dinamicamente) e tracciamento dello stato di risposta (`lettere_invii`).
- **Privati & Invio Multiplo SMS:** invia avvisi SMS in bulk agli utenti privati della generazione tramite `SMS Gateway`.

---

## 11. Gestione Mail — Webmail SMTP/IMAP (`/mail`)

Accessibile dal Menu Gestionale (`⊞`) → **Gestione Mail** (`/mail`, alias `/webmail`, `/gestione-mail`):

- **Client Email integrato** con cartelle `Posta in arrivo`, `Inviate`, `Bozze`, `Programmate`, `Archivio` e `Cestino`, sincronizzazione IMAP manuale e automatica.
- **Invio immediato e programmato:** i messaggi schedulati vengono inviati automaticamente in background (`mail_manager.process_scheduled_queue()`).
- **Categorizzazione intelligente (`Gestione Categorie Intelligenti`):** associa colori e regole automatiche basate su mittente, rubriche Comuni/Aziende e parole chiave.
- **Composizione avanzata:** autocompletamento contatti unificato (Filone, Comuni, Aziende, Storico), inserimento rapido di **Template Completi** e **Frasario / Snippet**, allegati custom e **Allegati Standard Aziendali** (`data/allegati/`).

---

## 12. Gestione Comuni e ZTL (`/comuni`)

Accessibile dal Menu Gestionale (`⊞`) → **Gestione Comuni** (`/comuni`):

- **Rubrica Comuni** salvata in `comuni_info`: stato **ZTL** e relative indicazioni operative, numero CE, tipologie zone, PEC/email e telefoni di Comune, Polizia Locale e Ufficio Tecnico.
- **Import/Export Excel** massivo della rubrica comunale.
- **Invio Mail/PEC in bulk ai Comuni:** anteprima personalizzata e invio verso Comune / Polizia Locale / Ufficio Tecnico con elenco Excel dei contatori del territorio allegato automaticamente.
- **Ricerca rapida contatti** integrata.

---

## 13. Modelli, Template & Frasario Centralizzato (`/templates`)

Accessibile dal Menu Gestionale (`⊞`) → **Modelli & Template** (`/templates`) o direttamente dall'editor dentro Gestione Mail:

- **Template Completi (Email / WhatsApp / SMS):** gestione centralizzata di oggetti e corpi con segnaposto automatici (`{nome}`, `{data}`, `{comune}`, `{indirizzo}`, `{pod}`, ecc.) condivisi tra Mappa, Distinte, Lettere, Comuni e Mail.
- **Frasario & Snippet Rapidi:** blocchi testuali riutilizzabili con un clic in fase di composizione email.
- **Firma & Recapiti Ufficio:** impostazione di firma standard, contatti telefonici ed email/PEC di riferimento per provincia (usata anche in CCN automatico SMTP quando configurata).

---

## 14. Calendario Attività, Bacheca Promemoria e Appuntamenti (`/calendario`, `/appuntamenti`)

### Calendario Attività & Workflow Giornaliero (`/calendario`)
- Vista **Lista** e vista **Calendario mensile** condivise a livello provinciale (`data/{prov}/calendario.json`).
- **Promemoria Appuntamento (BOXBEAT):** promemoria automatici o manuali generati in relazione agli esiti e alle note appuntamento.
- **Configura La mia Giornata (Workflow):** checklist giornaliera interattiva per operatore/ufficio.
- **Bacheca Promemoria (FAB sulla mappa):** mostra all'apertura o al cambio data i promemoria pertinenti alla giornata selezionata.

### Gestione Appuntamenti (`/appuntamenti`)
- Tabella operativa dedicata a tutti gli appuntamenti pianificati nel filone locale, filtrabile per periodo/operatore/comune/esito e scaricabile come file Excel formattato (`GET /api/appuntamenti/excel`).

---

## 15. Dashboard Operativa, Reportistica e Backup (`/dashboard`, `/report`)

- **Dashboard Operativa (`/dashboard`):** KPI in tempo reale sul filone locale, riepilogo contatori installati/rimanenti/appuntamenti, avanzamento per LCL e stima km percorsi dagli operatori.
- **Reportistica Operatori (`/report`):** analisi storica per intervallo di date divisa in 4 schede (**Panoramica**, **Dettaglio Operatori**, **Esiti**, **Confronto**) con export report in `.xlsx`.
- **Backup / Ripristino (Menu Gestionale → Backup / Ripristino):** lista backup JSON giornalieri generati automaticamente dal DB locale, creazione backup manuale, download come `.xlsx` ed eventuale ripristino controllato (gli snapshot binari SQLite `.db` sono gestiti anche lato admin).

---

## 16. Pannello Admin Multi-Provincia (`admin.*`)

Riservato agli utenti con ruolo `admin` su `https://admin.<tuo-dominio>/`:

- **Stato worker e Health overview:** controllo stato processi Gunicorn per provincia, statistiche DB, anomali e badge celle Outbox da esportare.
- **Gestione ciclo di vita:** `▶ Avvia`, `🔄 Riavvia`, `⏸ Ferma`, `➕ Aggiungi provincia`, `✏️ Modifica`, `🗑️ Elimina` (con opzione wipe completo).
- **Dati e sincronizzazione Excel/Sheets:**
  - **🪄 Configura (wizard):** configurazione in 3 step (colonne standard, operatori, accessi utenti) per nuove province.
  - **📥 Importa Excel:** upload `.xlsx`/`.csv` con timestamp di export per merge protetto "vince il più recente".
  - **⚡ Da Sheets:** trigger immediato auto-import in sola lettura dall'URL Google Sheet configurato.
  - **📤 Scarica cambiamenti / ✓ Segna gestite:** Outbox Excel compatto (`POD` + celle modificate in giallo) e reset badge `pending`.
  - **📋 Scarica completo** e **💾 Backup (`snapshot-db`)**.
- **Configurazione per provincia:** **🗂️ Colonne** (CRUD colonne + mapping intestazioni), **📬 Excel lettere**, **👥 Operatori**, **🏷️ LCL e Zone**, **📧 SMTP**, **📱 SMS Gateway**.
- **Strumenti trasversali:** **🔑 Utenti** (CRUD account, ruoli, province, reset password), **🔍 Ricerca Eneltel** globale e **📋 Log in tempo reale** filtrabili per provincia/router e livello (`journalctl`).

---

## 17. Aggiornamento dati in tempo reale (DB locale + SSE) e Tema

### Flusso DB-first & Notifiche SSE
- Ogni modifica viene salvata prima su SQLite (`data/{prov}/state.db`).
- Le modifiche cosmetiche/puntuali aggiornano `mappa_data.json` in ~50 ms; quelle strutturali rilanciano il `sync_loop` (~7–10 s).
- Al termine, il backend notifica i browser via **Server-Sent Events** (`/api/events`): l'orario nell'intestazione della sidebar lampeggia e la vista si aggiorna preservando lo stato corrente. In caso di disconnessioni persistenti entra in gioco un fallback di polling automatico.

### Tema chiaro e scuro
- Switch tema 🌙 / ☀️ dalla sidebar (mappa) o dall'header delle viste secondarie e admin: la preferenza è salvata in `localStorage` e applicata coerentemente su tutto il gestionale.

---

## 18. Legenda colori operatori e Scorciatoie

Le configurazioni predefinite in [`data/provinces.json`](data/provinces.json) (personalizzabili da Admin → `👥 Operatori`):

### Torino (`DEPOSITO VOLPIANO`)
| Colore | Matricola | Operatore |
|---|---|---|
| 🔴 `#e74c3c` | `AE101392` | PAUL |
| 🟡 `#f1c40f` | `AG49808` | MIHAI |
| 🟢 `#2ecc71` | `AG18966` | SIDI |
| 🟣 `#db33a6` | `AE94428` | ALECCI |
| 🟢 `#a0db33` | `AG05928` | GUINDO |
| 🔵 `#3498db` | `AG46824` | FENOCCHIO |

### Pavia (`DEPOSITO PAVIA`)
| Colore | Matricola | Operatore |
|---|---|---|
| 🔵 `#3498db` | `AG49808` | CIOBANU |
| 🟢 `#60db33` | `AG46824` | FENOCCHIO |
| 🟠 `#db7f33` | `AE101392` | PAUL |
| 🩵 `#33dbbf` | `AF09076` | SIMONE |
| 🟣 `#d833db` | `AG18966` | SIDI |
| 🟡 `#bfdb33` | `AG69238` | CLAUDIO |
| 🟪 `#6633db` | `AF34814` | ALESSANDRO |

### Lecco (`DEPOSITO LECCO`)
| Colore | Matricola | Operatore |
|---|---|---|
| 🟣 `#8d31bf` | `AG29392` | MARCO |
| 🔵 `#3498db` | `AG12345` | PIPPO |

**Marker speciali:** 🟢 Deposito (partenza/arrivo) • 🟣 Indirizzo cercato • 🩷 Contatore pinnato (`📌`) • 🔴 Cerchio pulsante (`☎️ APP`).

### Scorciatoie da tastiera

| Scorciatoia | Azione | Contesto |
|---|---|---|
| `←` / `→` | Giorno precedente / successivo | Vista Dettaglio con nessun input attivo e mouse fuori dalla mappa |
| `Invio` | Conferma ricerca, login, inserimento rapido sigillo/matricola | Campo attivo / modale |
