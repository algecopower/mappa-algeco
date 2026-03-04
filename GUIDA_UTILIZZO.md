# Guida all'utilizzo — Gestionale Mappa Contatori

> Guida pratica passo-passo per l'uso quotidiano del sistema.
> Per informazioni tecniche e di installazione, vedi `DOCUMENTAZIONE.md`.

---

## Indice

1. [Accesso al sistema](#1-accesso-al-sistema)
2. [Panoramica dell&#39;interfaccia](#2-panoramica-dellinterfaccia)
3. [La mappa — Vista Zone](#3-la-mappa--vista-zone)
4. [La mappa — Vista Comuni](#4-la-mappa--vista-comuni)
5. [La mappa — Vista Dettaglio (per data)](#5-la-mappa--vista-dettaglio-per-data)
6. [La mappa — Percorso Operatore](#6-la-mappa--percorso-operatore)
7. [Filtri: data, operatore, LCL, appuntamenti](#7-filtri-data-operatore-lcl-appuntamenti)
8. [Popup contatore — Consultazione](#8-popup-contatore--consultazione)
9. [Modificare i dati di un contatore](#9-modificare-i-dati-di-un-contatore)
10. [Impostare un appuntamento](#10-impostare-un-appuntamento)
11. [Ricerca contatori](#11-ricerca-contatori)
12. [Ricerca indirizzo sulla mappa](#12-ricerca-indirizzo-sulla-mappa)
13. [Pinnare un contatore](#13-pinnare-un-contatore)
14. [Caricare file Report da Beat](#14-caricare-file-report-da-beat-contatori-installati)
15. [Inserimento sigilli](#15-inserimento-sigilli)
16. [Inserimento matricole](#16-inserimento-matricole)
17. [Flusso post-installazione](#17-flusso-post-installazione--aggiornamento-dati)
18. [Gestione Distinte](#18-gestione-distinte)
19. [Tema chiaro e scuro](#19-tema-chiaro-e-scuro)
20. [Aggiornamento dei dati](#20-aggiornamento-dei-dati)
21. [Legenda colori](#21-legenda-colori)
22. [Scorciatoie da tastiera](#22-scorciatoie-da-tastiera)

---

## 1. Accesso al sistema

### Aprire il programma

Dopo aver avviato il server (vedi `DOCUMENTAZIONE.md`), apri il browser all'indirizzo:

- **Torino:** http://localhost:5001
- **Pavia:** http://localhost:5002

Se il sistema è configurato con Cloudflare:

- **Torino:** https://torino.mappa-algeco.com
- **Pavia:** https://pavia.mappa-algeco.com

### Pagina di login

1. Inserisci il tuo **username** nel primo campo (già selezionato automaticamente)
2. Inserisci la **password** nel secondo campo
3. Clicca **Accedi** oppure premi **Invio**

**Suggerimenti:**

- Clicca l'icona 👁️ a destra del campo password per mostrare/nascondere la password
- In basso a destra appare un badge con la **provincia** attiva (es. "TORINO")
- Dopo **5 tentativi falliti**, il pulsante si blocca per 30 secondi. Appare un conto alla rovescia; attendi che si sblochi
- La sessione resta attiva per **24 ore**, poi dovrai rifare il login.

## 2. Panoramica dell'interfaccia

L'interfaccia è divisa in due aree principali:

```
┌──────────────────┬────────────────────────────────────────┐
│                  │                                        │
│    SIDEBAR       │             MAPPA                      │
│  (pannello       │         (Leaflet.js)                   │
│   sinistro)      │                                        │
│                  │                                        │
│  - Filtri        │   - Marker contatori                   │
│  - Zone/Comuni   │   - Linee percorso                     │
│  - Dettaglio     │   - Deposito                           │
│  - Percorsi      │   - Popup informativi                  │
│  - Pulsanti      │                                        │
│    gestionale    │                                        │
│                  │                                        │
├──────────────────┴────────────────────────────────────────┤
│  Barra statistiche (zone, contatori, appuntamenti)        │
└───────────────────────────────────────────────────────────┘
```

### Sidebar (pannello sinistro)

La sidebar è il pannello di controllo. Contiene:

- **Intestazione** con nome provincia e ora dell'ultimo aggiornamento dei dati;
- **Sezione pulsanti** per cambiare vista (vedi sezioni [3](#3-la-mappa--vista-zone)–[6](#6-la-mappa--percorso-operatore)), cambiare tema (vedi [sezione 19](#19-tema-chiaro-e-scuro)), effettuare ricerche (vedi sezioni [11](#11-ricerca-contatori) e [12](#12-ricerca-indirizzo-sulla-mappa)) e ricaricare i dati;
- **Sezione per il filtraggio** del contenuto;
- **Contenuto** che cambia in base alle diverse viste e i filtri selezionati;
- **Pulsanti gestionale** in fondo (BOXBEAT, Sigilli, Distinte).

Puoi **nascondere/mostrare** la sidebar con il pulsante freccia ◀/▶ sul bordo destro della sidebar.

### Mappa

La mappa occupa tutta l'area rimanente. Mostra:

- I **raggruppamenti delle zone vicine** come cerchi semitrasparenti, e/o **singoli comuni** come ulteriori segnaposti colorati
- I **contatori**, che vengono rivelati accedendo alla relativa zona/comune come ulteriori segnaposti colorati numerati
- Il **deposito** come segnaposto verde fisso

### Barra inferiore

Mostra le statistiche attuali: numero zone visibili, contatori, appuntamenti.

---

## 3. La mappa — Vista Zone

Questa è la **vista predefinita** quando apri la mappa. Mostra il territorio diviso in **zone geografiche** colorate.

### Cosa vedi sulla sidebar

Le zone appaiono come **card colorate** con:

- **Nome zona** (es. "ZONA NORD - Settimo Torinese") e pallino dello stesso colore utilizzato dalla mappa per visualizzarlo
- **Numero totale contatori** nella zona
- **Lista dei comuni** che compongono la zona
- **Indicatore LCL**
- **Distanza dal deposito** (in km) e **direzione** (Nord, Sud-Est, ecc.)
- **Date pianificate** — le prime 5 date sono visibili; se ce ne sono di più, clicca **"📋 Mostra altre N date"** per vederle tutte

### Quando usarla

La vista Zone è ideale per:

- Avere una **visione d'insieme** del territorio
- Pianificare la **distribuzione settimanale** delle zone
- Capire **quanti contatori** ci sono in ogni area
- Vedere le **date** già pianificate per ogni zona

---

## 4. La mappa — Vista Comuni

Alterna alla vista Comuni cliccando il pulsante **🏘️ Comuni** nell'header della sidebar.

### Differenze dalla vista Zone

- Anziché zone aggregate, mostra ogni **singolo comune** come entità separata
- Utile per una visione più granulare quando le zone sono troppo ampie

### Tornare alla vista Zone

Clicca il pulsante **🗺️ Zone** nell'header della sidebar.

---

## 5. La mappa — Vista Dettaglio (per data)

Quando dalla vista Zone o Comuni clicchi su una **data specifica**, entri nella vista **Dettaglio**. Qui puoi visualizzare i singoli contatori per tale data SOLO per quella specifica zona/comune.

### Come arrivarci

1. Vista Zone → clicca una data dentro una card zona
2. Oppure: usa il **dropdown Data** nella barra filtri per selezionare direttamente una data

### Cosa vedi sulla sidebar

- **Barra** in alto: mostra il nome della zona/comune e la data cliccata precedentemente (es. "ZONA NORD → 15/01/2025"). Clicca **← Indietro** per uscire
- Per ogni **operatore/tecnico** sarà visibile:
  - Nome operatore e conteggio tappe
  - Lista ordinata dei **contatori ordinati in tappe** assegnati a ciascun operatore
  - Al clic su un utente dalla lista verrà mostrata, sulla mappa a destra, una scheda informativa (sotto forma di **popup** posto immediatamente sopra il segnaposto corrispondente) con: numero progressivo, nome cliente, comune, via, eneltel, LCL, telefono, note (vedi [sezione 8](#8-popup-contatore--consultazione))
  - Indicatore **☎️ APP** se il contatore ha un appuntamento (con orario, se presente)
- **Deposito** all'inizio e alla fine del percorso

### Contatori con appuntamento

I contatori con appuntamento hanno un'**animazione pulsante** (cerchio che si espande e contrae) per renderli immediatamente riconoscibili sulla mappa.

### Filtri disponibili in questa vista

- **☐ Tutte le date** — spunta per mostrare i contatori di TUTTE le date contemporaneamente (utile per avere il quadro completo)
- **☐ Solo app.** — spunta per mostrare solo i contatori che hanno un appuntamento

### Navigazione tra le date

In vista Dettaglio, puoi navigare rapidamente tra le date.

#### Con le frecce nella sidebar

- Clicca **◀** per passare alla data **precedente**
- Clicca **▶** per passare alla data **successiva**

#### Con la tastiera

- Premi **freccia sinistra ←** per la data precedente
- Premi **freccia destra →** per la data successiva

> Funziona solo se nessun campo di testo è attivo e il mouse non è sopra la mappa.

#### Con il calendario

1. Clicca sulla **data** mostrata nel breadcrumb
2. Si apre un **Date Picker** con tutte le date disponibili
3. Ogni data mostra il **numero di contatori** pianificati
4. Clicca una data per saltare direttamente a quella

---

## 6. La mappa — Percorso Operatore

Dalla schermata iniziale, selezionando una data precisa dal **filtro data** (vedi [sezione 7](#7-filtri-data-operatore-lcl-appuntamenti)), è possibile isolare il percorso di un singolo operatore per quella giornata.

### Come arrivarci

1. Seleziona una **data** dal filtro (il cui valore iniziale è "📅 Tutte le date"). A questo punto, nella sidebar sarà visibile una lista riepilogativa del giorno in cui viene indicato:
   * la matricola di ciascun operatore
   * quanti contatori sono assegnati a ciascuno
   * i comuni visitati da ciascuno
2. Seleziona un **operatore** nel dropdown che apparirà a destra del filtro date (il cui valore iniziale è "👥 Tutti gli operatori") oppure cliccando sull'operatore desiderato direttamente nella lista riepilogativa.

### Cosa vedi in più

- **Tempi di percorrenza** tra un contatore e l'altro: ogni tappa mostra **🚗 X min (Y km)** — il tempo stimato e la distanza dal punto precedente
- Percorso completo ordinato secondo un algoritmo pre-impostato ottimizzato: **Deposito → Tappa 1 → Tappa 2 → ... → Deposito**
- **Strade tracciate** con linee colorate sulla base dei percorsi reali che gli operatori percorreranno;

### Pulsante stampa

In questa vista appare il pulsante **🖨️ Stampa** che permette di stampare il percorso (vedi [Stampare un percorso](#stampare-un-percorso) qui sotto).

### Tornare indietro

- Clicca **← Indietro** per tornare alla vista riepilogativa di tutti gli operatori
- Clicca di nuovo **← Indietro** per tornare alla vista Zone/Comuni

### Stampare un percorso

Puoi stampare una mappa con il percorso ottimizzato di un operatore insieme alle **ultime 3 cifre dell'Eneltel** per ciascun contatore, scritte in delle etichette disposte automaticamente sulla base dello spazio disponibile.

#### Prerequisito

Devi essere nella **vista Percorso Operatore** (vedi [sezione 6](#6-la-mappa--percorso-operatore)).

#### Procedura

1. Assicurati di essere nella vista del percorso di un singolo operatore
2. Clicca il pulsante **🖨️ Stampa** che appare nell'header
3. Si apre un **pannello di anteprima** a schermo intero con:
   - Una **mappa dedicata** (tema chiaro, senza scritte di attribuzione)
   - I **marker numerati** con l'ordine di visita
   - Le **etichette ENELTEL** (ultime 3 cifre) posizionate automaticamente vicino ai marker senza sovrapporsi
   - **Frecce tratteggiate** che collegano le etichette ai marker
   - Un **slider zoom** per regolare il livello di dettaglio
   - Se ci sono più tecnici, un **dropdown** per selezionare quale percorso stampare

#### Stampa effettiva

1. Regola lo zoom fino a ottenere la vista desiderata
2. Clicca **🖨️ Stampa** nel pannello anteprima
3. Il sistema genera uno screenshot ad **alta risoluzione** (2× la dimensione schermo)
4. Si apre la finestra di stampa del browser (puoi stampare su carta o salvare come PDF)

#### Contatori sovrapposti nella stampa

Se più contatori sono situati nel raggio di pochi metri, vengono **raggruppati insieme** con un indicatore di quanti contatori sono presenti in quel punto. I codici Eneltel sono elencati sottoforma di lista per ottimizzare gli spazi.

---

## 7. Filtri: data, operatore, LCL, appuntamenti

### Filtro per data

Da utilizzare per far sì che la mappa e la sidebar mostrino tutti i contatori programmati per una data specifica, indipendentemente dalle zone.

### Filtro per operatore

Dopo aver scelto una data, sarà disponibile accanto un **dropdown Operatore** da utilizzare per mostrare solo i contatori di quel tecnico e il suo percorso ottimizzato generato automaticamente.

### Filtro per LCL

Da utilizzare per mostrate solo le zone o i contatori **associati a quella LCL.**

### Filtro "Solo appuntamenti"

In vista Dettaglio, spunta la checkbox **☐ Solo app.** per mostrare **unicamente** i contatori con appuntamento.

### Filtro "Tutte le date"

In vista Dettaglio, spunta la checkbox **☐ Tutte le date** per mostrare i contatori di **tutte le date** contemporaneamente nella zona selezionata. Utile per avere il quadro completo di una zona.

### Combinare i filtri

I filtri sono cumulativi: puoi selezionare una data + un operatore + un LCL per una vista molto mirata.

---

## 8. Popup contatore — Consultazione

Il popup si apre cliccando su un **segnaposto** sulla mappa o su una **tappa** nella sidebar.

### Informazioni mostrate

Il popup contiene (dall'alto in basso):

1. **Indicatore appuntamento** (se presente): sfondo rosso con icona ☎️, testo "APP" e orario
2. **Tempo percorrenza** (se in vista percorso): 🚗 con minuti stimati dal punto precedente
3. **Numero tappa + Nome cliente** in grassetto
4. **Data pianificazione** con icona 📅
5. **Comune** con icona 🏘️
6. **Località** (solo se diversa dal comune) con icona 📍
7. **Via e civico** con icona 🏠
8. **ENELTEL** con icona 🔌
9. **POD** con icona 📋
10. **LCL** con icona 🏷️
11. **Telefono** con icona 📞
12. **Ora pianificazione** con icona ⏰ (se presente)
13. **Posizione misuratore** con icona 📐 (se presente)
14. **Note** con icona 📝 (se presenti)
15. **Pulsanti azione**:
    - **📌 Fissa questo** — pinna il contatore (vedi [sezione 13](#13-pinnare-un-contatore))
    - **✏️ Modifica** — apre la modifica (vedi [sezione 9](#9-modificare-i-dati-di-un-contatore))

### Contatori sovrapposti

Quando più contatori si trovano **entro 5 metri** l'uno dall'altro (es. stesso condominio), il sistema li raggruppa in un **segnaposto cluster** con un indicatore numerico.

- Clic sul cluster → si apre un **popup lista** con tutti i contatori sovrapposti
- Ogni contatore nella lista mostra: nome, eneltel, badge appuntamento
- Clic su un contatore nella lista → si apre il popup singolo completo

---

## 9. Modificare i dati di un contatore

Puoi modificare i dati di un contatore in **3 modi.**

### Modifica dal popup

1. Apri il popup del contatore (clic sul marker o sulla tappa in sidebar)
2. Clicca **✏️ Modifica**

### Modifica dalla sidebar

1. Nella sidebar, trova il contatore desiderato nella lista;
2. Clicca il pulsante **✏️** a destra della tappa

### Modifica dalla Ricerca Utente (vedi [sezione 11](#11-ricerca-contatori))

1. Cerca un contatore per nominativo, POD, Eneltel, telefono, ecc.
2. Clicca il pulsante **✏️** posto sulla destra del risultato corretto.

### Dati modificabili

In tutti i casi, si aprirà un **pannello a schermo** intero che permette di modificare i seguenti campi:

- **Data pianificata** — campo data
- **Note/Appuntamento** — campo testo grande
- **Nominativo** — campo testo
- **Indirizzo** — campo testo
- **Civico** — campo testo
- **Telefono** — campo testo
- **Posizione misuratore** — dropdown (Nell'appartamento, accessibile dall'esterno, ecc.)
- **Operatore** — dropdown con operatori assegnabili
- **Sezione appuntamento** dedicata (vedi [sezione 10](#10-impostare-un-appuntamento) di seguito)

### Cosa succede dopo il salvataggio

1. I dati vengono inviati a Google Sheets (il FILONE)
2. Parte una **sincronizzazione immediata** che rigenera i percorsi e la mappa
3. La sidebar e la mappa si aggiornano automaticamente senza ricaricare la pagina
4. Appare una conferma visiva

---

## 10. Impostare un appuntamento

1. Apri la **schermata di modifica** di un contatore (vedi [sezione 9](#9-modificare-i-dati-di-un-contatore) precedente)
2. Nella sezione **Appuntamento** in basso trovi:

- Selettore **Orario inizio** (es. 10:00)
- Selettore **Orario fine** (es. 12:00)

3. Clicca **☎️ Salva Appuntamento**
4. Il sistema scrive "APP 10:00-12:00" nella colonna note del FILONE

### Dalla pagina Gestione Distinte

1. Apri `/distinte`
2. Nella card di un contatto, usa il selettore data e orario posti in basso
3. Clicca **📅 Salva**

### Rimuovere un appuntamento

All'interno della modalità modifica, cancella il testo "APP..." dalla nota e salva

### Come appare sulla mappa

- I contatori con appuntamento hanno un'**animazione pulsante** (cerchio che si espande)
- Nella scheda informativa del segnaposto appare un **indicatore** con sfondo rosso ☎️ APP con l'orario (se presente)
- Anche nella sidebar, la tappa mostra **☎️ APP** con l'orario

### Effetto sull'ottimizzazione del percorso

L'appuntamento è un **vincolo temporale**: il sistema posiziona il contatore nel percorso in modo che il tecnico arrivi all'orario giusto. I contatori senza appuntamento vengono inseriti nei "buchi" tra un appuntamento e l'altro.

---

## 11. Ricerca contatori

Il sistema offre due livelli di ricerca.

### Ricerca rapida (barra di ricerca)

Nella sidebar c'è una **barra di ricerca** sempre visibile:

1. Digita il testo da cercare (nome, indirizzo, comune, ENELTEL, POD...)
2. I risultati si filtrano **in tempo reale** mentre digiti
3. La ricerca avviene sulle zone/comuni/tappe visibili nella vista corrente
4. Clicca la **✕** per cancellare la ricerca

### Ricerca avanzata (modale)

Per cercare **in tutti i contatori** (non solo quelli visibili):

1. Clicca il pulsante **🔍 Ricerca Utente** posto sotto l'intestazione della sidebar
2. Si apre un **modale** con **6 campi** di ricerca:
   - **Nominativo** — nome del cliente
   - **POD** — codice Point of Delivery
   - **Eneltel** — codice contatore
   - **Comune** — nome del comune
   - **Via** — indirizzo
   - **Telefono** — numero di telefono
3. Compila **uno o più campi** (tutti i campi devono corrispondere tra loro)
4. Premi **Invio** o clicca il pulsante di ricerca
5. Appaiono fino a **10 risultati** con nome, indirizzo, eneltel, POD e data

### Cosa puoi fare con i risultati

| Azione               | Risultato                                                                                                                             |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| Clic su un risultato | Il contatore viene fissato automaticamente: la mappa naviga alla zona/data del contatore, fa zoom e apre il popup dopo mezzo secondo |
| Clic su ✏️         | Si apre la schermata modifica del contatore, senza doverlo ricercare nella sidebar o sulla mappa                                      |

> **Nota:** i termini di ricerca vengono salvati localmente e ripristinati quando riapri il modale.

---

## 12. Ricerca indirizzo sulla mappa

Questa funzione permette di cercare un **indirizzo qualsiasi** e visualizzarlo sulla mappa.

1. Clicca il pulsante **📍 Cerca Indirizzo** nell'header della sidebar
2. Si apre un modale con un campo di testo
3. Scrivi l'indirizzo (es. "Via Roma 15, Torino")
4. Premi **Invio** o clicca Cerca
5. Appaiono fino a **5 risultati** da OpenStreetMap (Nominatim), limitati all'Italia
6. Clicca su un risultato

### Cosa succede

- Il modale si chiude automaticamente
- Sulla mappa appare un **marker viola** 📍 nel punto cercato
- La mappa fa zoom a livello 16 centrata sul punto
- Si apre un popup con l'indirizzo e le coordinate

### Rimuovere il marker

Nel popup del marker viola, clicca **🗑️ Rimuovi marker**.

### Quando usarla

- Per orientarsi sulla mappa quando non conosci la zona
- Per verificare la posizione di un indirizzo rispetto ai contatori vicini
- Per avere un riferimento visivo durante la pianificazione

---

## 13. Pinnare un contatore

Il **pin** permette di tenere un contatore evidenziato sulla mappa anche quando cambi vista, data o zoom: utile per prendere appuntamento e individuare a colpo d'occhio la potenziale giornata migliore per quel contatore

### Come pinnare

1. Apri il popup di un contatore (clic sul segnaposto)
2. Clicca **📌 Fissa questo**

### Come appare

- Il contatore pinnato diventa un **marker speciale rosa** con icona 📌
- Resta visibile a **qualsiasi livello di zoom** e in **qualsiasi vista**

### Come rimuovere il pin

1. Clicca sul marker pinnato (il rosa con 📌)
2. Nel popup, clicca **❌ Rimuovi pin**

### Quando usarlo

- Per non perdere di vista un contatore specifico mentre esplori la mappa
- Per confrontare la posizione di un contatore con altre zone
- Per tornare velocemente a un contatore quando hai cambiato vista

---

## 14. Caricare file Report da Beat (contatori installati)

Il Report è il file Excel che E-Distribuzione rilascia con gli esiti delle installazioni. Caricandolo, il sistema aggiorna automaticamente il FILONE.

### Procedura

1. Nella sezione in fondo della sidebar, clicca **📦 Carica installato**
2. Si apre un **modale** con un'area di upload
3. **Trascina** i file nell'area tratteggiata OPPURE **clicca** per selezionarli dal computer
4. Formati accettati: `.xlsx`, `.xls`, `.csv`
5. Puoi caricare **più file** contemporaneamente
6. Una volta selezionati, clicca il pulsante per avviare l'elaborazione
7. Appare una **barra di progresso** durante l'upload

### Cosa viene aggiornato

Per ogni riga del BOXBEAT con **Esito OK**, il sistema cerca il **POD** corrispondente nel FILONE e aggiorna:

| Dal BOXBEAT        | Nel FILONE         |
| ------------------ | ------------------ |
| Matricola preposto | Operatore          |
| Data esecuzione    | Data Installazione |
| (automatico)       | Esito = ESITO OK   |

### Risultati

Al termine appare un riepilogo:

- Numero **file elaborati**
- Numero **righe lette** in totale
- Numero **record con Esito OK**
- Numero **contatori aggiornati** sul FILONE
- Eventuali **errori** (file non leggibili, colonne mancanti, ecc.)

> **Nota:** i titoli delle colonne del file Report sono situati e attesi dal programma alla **riga 3, non sulla prima**

---

## 15. Inserimento sigilli

I sigilli sono necessari per i contatori **trifase semidiretti** e **fotovoltaici** (ENELTEL che termina con A o B).

### Procedura

1. Nella sezione in fondo della sidebar, clicca **🔐 Sigilli / Matricole**
2. Si apre un **pannello fullscreen**
3. Assicurati che il **tab "🔐 Sigilli"** in alto sia selezionato
4. Appare la lista dei contatori installati (**Esito OK**) che **non hanno ancora** un sigillo associato

### Filtri disponibili

- **Per tipo:** Tutti / Trifase / Fotovoltaico
- **Per operatore:** dropdown con i tecnici

### Come inserire un sigillo

1. Trova il contatore nella lista (ogni card mostra: nome, comune, via, eneltel, data installazione, operatore)
2. Scrivi il numero di sigillo nel campo **input** della card
3. Premi **Invio**
4. Il sigillo viene salvato su Google Sheets e la cella viene **evidenziata in giallo**
5. Il cursore passa automaticamente al **campo successivo** per un inserimento rapido

### Indicatori visivi

- **Indicatore ✅** — sigillo già inserito
- **Indicatore caricamento** — durante il salvataggio

> Solo le installazioni dell'**anno corrente** vengono mostrate.

---

## 16. Inserimento matricole

Le matricole sono i numeri seriali dei nuovi contatori installati. A differenza dei sigilli, le matricole riguardano **tutti** i tipi di contatori.

### Procedura

1. Nella sidebar della mappa, clicca **🔐 Sigilli / Matricole**
2. Si apre il pannello fullscreen
3. Seleziona il **tab "🔢 Matricole"**
4. Appare la lista di TUTTI i contatori installati (**Esito OK**) senza matricola

### Come inserire una matricola

1. Trova il contatore nella lista
2. Spara l'etichetta nel campo input
3. La matricola viene salvata su Google Sheets e la cella viene **evidenziata in verde**
4. Il cursore passa automaticamente al campo successivo

---

## 17. Flusso post-installazione — Aggiornamento dati

1. Ricevi i **file BOXBEAT** da Enel
2. Nella mappa, clicca **📦 Carica installato**
3. **Carica** i file BOXBEAT
4. Verifica il **riepilogo** (contatori aggiornati, errori)
5. Vai su **🔐 Sigilli / Matricole**:
   - Tab Sigilli → inserisci i sigilli per trifase e fotovoltaici
   - Tab Matricole → inserisci le matricole per tutti i contatori
6. I dati si aggiornano automaticamente su Google Sheets

---

## 18. Gestione Distinte

La pagina Distinte (`/distinte`) è uno strumento dedicato per:

- **Elaborare le** **distinte PDF** scaricate da Beat
- **Agevolare il contatto con gli utenti** via WhatsApp o telefono
- **Confrontare** i dati presenti sulla distinta con quelli del FILONE
- **Aggiornare** i dati sul FILONE direttamente dalla distinta

### Come accedervi

- Dalla sidebar della mappa: clicca **📋 Gestione Distinte**
- Oppure digita `/distinte` nell'URL (es. https://torino.mappa-algeco.com/distinte)

### Layout

```
┌───────────────────────────────────────────────────────┐
│  Intestazione: Logo + Titolo + Badge provincia        │
├───────────────────────────────────────────────────────┤
│  Area upload PDF (drag & drop)                        │
├───────────────────────────────────────────────────────┤
│  Filtri: 📱 Cellulare  ☎️ Fisso  ❌ Senza N. 	│
├───────────────────────────────────────────────────────┤
│  Card contatto 1                                      │
│  Card contatto 2                                      │
│  Card contatto 3                                      │
│  ...                                                  │
└───────────────────────────────────────────────────────┘
```

### Caricare ed elaborare i PDF

1. Apri la pagina `/distinte`
2. Nell'area di upload ci sono due opzioni:

- **Trascina** uno o più file PDF nell'area tratteggiata verde
- **Clicca** sull'area per aprire il selettore file

3. I file accettati devono essere **PDF** e idealmente avere nel nome "DistintaBase"
4. I file caricati appaiono come **chip** con il nome del file e un pulsante **✕** per rimuoverli
5. Clicca **"Elabora Distinte"** per avviare l'estrazione
6. Il sistema:
   - Estrae i dati da ogni PDF (nome, indirizzo, telefono, POD, ecc.)
   - Classifica i contatti per tipo di telefono
   - Confronta con il FILONE per trovare differenze
7. I risultati appaiono come **card** sotto i filtri

### Classificazione automatica

Il sistema divide automaticamente i contatti in tre categorie:

| Categoria                | Icona | Colore bordo | Descrizione                                           |
| ------------------------ | ----- | ------------ | ----------------------------------------------------- |
| **Cellulare**      | 📱    | Verde        | Numero mobile (prefisso 320-399), pronto per WhatsApp |
| **Fisso**          | ☎️  | Arancione    | Numero di rete fissa, da chiamare manualmente         |
| **Senza telefono** | ❌    | Grigio       | Nessun numero disponibile, da cercare                 |

### Marcare come completato

Ogni card ha una **checkbox** (☐). Spuntala per segnare che hai già contattato quel cliente. Lo stato viene salvato in localmente e persiste alla riapertura della pagina o nel caso in cui le stesse distinte venissero ricaricate in futuro.

### Inviare messaggi WhatsApp

Per i contatti con **numero di cellulare**, puoi inviare messaggi WhatsApp precompilati:

1. Trova la card di un contatto con bordo **verde** (cellulare)
2. Clicca il pulsante **📱 WhatsApp/Template**
3. Si aprirà un modale che offre la possibilità di:
   * Scegliere un template;
   * **Copiare** o **inviare direttamente su WhatsApp (web o app) il messaggio precompilato** con i dati del contatto (nome, data, indirizzo, ecc.)

### Template messaggi

Il sistema offre **6 template** di messaggio preconfigurati, ciascuno pensato per una situazione diversa.

| # | Nome                     | Scopo                                                                                                                                                                                                             |
| - | ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1 | **WA Conferma**    | Messaggio WhatsApp per confermare appuntamento già programmato con lettera                                                                                                                                       |
| 2 | **WA Sollecito**   | Messaggio WhatsApp di sollecito in seguito a una chiamata non risposta                                                                                                                                            |
| 3 | **Spiegone**       | Messaggio lungo e dettagliato con spiegazione lavoro                                                                                                                                                              |
| 4 | **Appuntamento 1** | Primo template per fissare appuntamento                                                                                                                                                                           |
| 5 | **Appuntamento 2** | Secondo template appuntamento (variante)                                                                                                                                                                          |
| 6 | **Email Società** | Template per email alla ditta/amministratore<br />(utilizza un indirizzo email specifico per provincia)<br />Cliccando **📧 Email** si apre il client email con il destinatario e l'oggetto già compilati. |

### Cercare un numero di telefono

Per i contatti **senza telefono**, puoi cercare il numero tramite servizi esterni:

1. Trova una card con bordo **grigio** (senza telefono)
2. Clicca il pulsante **🔍 Cerca telefono**
3. Si apre un **modale** con:

- Link a **Pagine Bianche** — precompilato con nome e indirizzo del cliente
- Link a **Google** — precompilato con nome e comune
- **Campo di inserimento manuale** — per digitare il numero trovato e salvarlo sul FILONE

### Segnare un numero come errato

Se un numero di telefono è sbagliato (non risponde, non esiste, ecc.):

1. Nella card del contatto, cerca l'opzione per segnare il numero errato
2. Il sistema:
   - Aggiunge **"ERRATO"** al valore del telefono nel FILONE
   - Applica la **formattazione barrato** (strikethrough) alla cella su Google Sheets

### Note e appuntamenti

Ogni contatto ha una sezione per le note:

1. Usa il **dropdown note rapide** per selezionare e inserire note predefinite:
   - "Mandato WA"
   - "Non risponde"
   - "Chiamare"
   - "Numero errato"
2. Oppure scrivi una **nota libera** nel campo testo
3. La nota si salva automaticamente premendo **Invio** o cliccando fuori dal campo
4. La nota viene:
   - Salvata sul FILONE (Google Sheets)
   - Salvata localmente

### Impostare un appuntamento dalla distinta

1. Nella card del contatto, puoi trovare anche una sezione per fissare **appuntamento** sul momento
2. Seleziona la **data** con il selettore apposito
3. Seleziona l'**orario inizio** e l'**orario fine** (se si tratta di una fascia estesa)
4. Clicca **📅 Salva**
5. Il sistema scrive la nota "APP [orario]" nel FILONE e fa partire la sincronizzazione

### Confronto tra distinte e FILONE

Quando elabori le distinte PDF, il sistema **confronta automaticamente** i dati estratti con quelli presenti nel FILONE, ovvero:

- **Data intervento** — la data nella distinta vs quella pianificata nel FILONE
- **Telefono** — il numero nella distinta vs quello nel FILONE
- **Nominativo** — il nome nella distinta vs quello nel FILONE
- **Indirizzo** — via e civico della distinta vs quelli nel FILONE
- **Comune** — il comune della distinta vs quello nel FILONE

Le differenze vengono evidenziate come **alert** con:

- Il **campo** che differisce
- Il **valore della distinta** (nuovo)
- Il **valore del FILONE** (attuale)

Per ogni differenza puoi:

- **Sovrascrivere** (aggiornare il FILONE con il valore della distinta o viceversa)
- **Ignorare** (mantenere il valore attuale del FILONE)

> Questo è particolarmente utile per aggiornare numeri di telefono, date e indirizzi quando E-Distribuzione fornisce dati più aggiornati, o, viceversa, quando mancano.

### Flusso settimanale — Contattare i clienti

1. Apri `/distinte`
2. **Carica** le distinte PDF ricevute da Enel
3. **Controlla le differenze** con il FILONE e aggiorna se necessario
4. **Contatta via WhatsApp** i clienti con cellulare (pulsante 📱)
5. **Chiama** i clienti con telefono fisso
6. Per quelli **senza telefono**: cerca su Pagine Bianche (🔍) e salva il numero
7. **Segna gli appuntamenti** fissati durante le chiamate
8. **Spunta** ogni contatto come completato (checkbox ☐)

---

## 19. Tema chiaro e scuro

L'interfaccia supporta due temi visivi.

### Come cambiare tema

- Nella sidebar della mappa: di default è impostato il tema scuro **🌙** (luna). Clicca sul pulsante **Tema** **☀️** (sole) per tema chiaro
- La pagina distinte eredita automaticamente il tema scelto dalla mappa

### Persistenza

La scelta del tema viene salvata localmente. Quando ricarichi la pagina o se dovesse essere riavviato il server, il tema scelto viene ripristinato automaticamente sul dispositivo. Il tema si riflette su tutte le pagine del programma.

---

## 20. Aggiornamento dei dati

Il sistema si aggiorna automaticamente in due modi.

### Sync in background (ogni 3 minuti)

Ogni 3 minuti il server:

1. Scarica i dati aggiornati da Google Sheets
2. Ricalcola distanze e percorsi (usando la cache per velocità)
3. Rigenera i file JSON della mappa
4. Notifica tutti i browser connessi

### Notifica al browser (SSE)

Il browser riceve una **notifica in tempo reale** quando i dati cambiano:

- L'**orario** nell'intestazione della sidebar **lampeggia in verde** brevemente
- I dati della mappa si **aggiornano automaticamente** senza ricaricare la pagina
- La sidebar mantiene la vista corrente (non ti interrompe)

Se la connessione SSE si interrompe (es. problema di rete), il sistema riprova fino a 5 volte con tempi crescenti (5s, 10s, 20s...). Dopo 5 tentativi falliti, passa a un **polling tradizionale** come backup.

### Sincronizzazione manuale

Se hai bisogno di forzare un aggiornamento immediato:

1. Clicca il pulsante **🔄** nell'intestazione della sidebar
2. Il server lancia immediatamente una sincronizzazione completa
3. La pagina si ricarica da zero con i dati aggiornati

In alternativa: dopo ogni **modifica manuale** (salvataggio contatore, upload Report, ecc.), il sync viene triggerato **automaticamente** senza dover cliccare nulla.

---

## 21. Legenda colori

Ogni **operatore/tecnico** ha un colore assegnato. I colori predefiniti sono:

**Torino:**

| Colore              | Operatore       |
| ------------------- | --------------- |
| 🔴 Rosso (#e74c3c)  | AE101392        |
| 🟡 Giallo (#f1c40f) | AG49808         |
| 🟢 Verde (#2ecc71)  | AG18966         |
| 🔵 Ciano (#00bcd4)  | SENZA OPERATORE |

**Pavia:**

| Colore              | Operatore  |
| ------------------- | ---------- |
| 🔴 Rosso (#e74c3c)  | ALESSANDRO |
| 🟡 Giallo (#f1c40f) | MELEK      |

### Colori speciali sulla mappa

| Colore         | Significato                  |
| -------------- | ---------------------------- |
| 🟢 Verde fisso | Deposito (partenza/arrivo)   |
| 🟣 Viola       | Segnaposto indirizzo cercato |
| 🩷 Rosa        | Contatore pinnato            |

---

## 22. Scorciatoie da tastiera

Riepilogo delle scorciatoie disponibili nell'interfaccia.

| Scorciatoia                   | Azione                                    | Condizione                                                             |
| ----------------------------- | ----------------------------------------- | ---------------------------------------------------------------------- |
| **← Freccia sinistra** | Vai alla data precedente                  | Vista Dettaglio, nessun campo di testo attivo, mouse fuori dalla mappa |
| **→ Freccia destra**   | Vai alla data successiva                  | Vista Dettaglio, nessun campo di testo attivo, mouse fuori dalla mappa |
| **Invio**               | Conferma ricerca, login, inserimento dati | Quando un campo di testo o un modale è attivo                         |

---

*Per assistenza tecnica e configurazione, consulta `DOCUMENTAZIONE.md`.*
