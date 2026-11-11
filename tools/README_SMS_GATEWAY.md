# Guida Operativa: Invio SMS Multiplo tramite Gateway SMS

Questa guida illustra la configurazione e l'utilizzo dell'SMS Gateway integrato nel gestionale Mappa Algeco per l'invio rapido di SMS in bulk dal gestionale.

---

## Panoramica

Il sistema supporta l'invio di messaggi SMS direttamente dallo smartphone aziendale (a costo zero su SIM con piano SMS, es. 1000 SMS/mese) oppure tramite endpoint HTTP / Webhook generico.

| Caratteristica | **Android SMS Gateway (Consigliata)** | **Webhook / HTTP Generico** |
| :--- | :--- | :--- |
| **Costo** | **0€** (usa la SIM del telefono) | Dipende dal provider HTTP |
| **Requisiti Hardware** | Smartphone Android con SIM collegato in Wi-Fi o con app Capcom6 Cloud | Server SMS locale o cloud |
| **Configurazione** | Immediata (2 minuti dall'app) | Inserimento endpoint e token |
| **Ideale per** | Sostituzioni rapide, comunicazioni ai clienti dal cellulare aziendale | Integrazioni enterprise |

---

## Configurazione "SMS Gateway for Android" (App Open Source)

L'applicazione open source **SMS Gateway for Android** ([capcom6/android-sms-gateway](https://github.com/capcom6/android-sms-gateway)) trasforma lo smartphone aziendale in un gateway SMS REST, senza passare da intermediari a pagamento.

### 1. Installazione sullo smartphone aziendale
1. Scarica l'app sul telefono Android:
   - Da **GitHub Releases**: [capcom6/android-sms-gateway/releases](https://github.com/capcom6/android-sms-gateway/releases)
   - Oppure da **F-Droid** cercando `SMS Gateway` (sviluppatore capcom6 / sms-gate.app).
2. Apri l'app e concedi i permessi richiesti per l'invio degli SMS.

### 2. Modalità di utilizzo

#### Modalità A: Server Locale (Wi-Fi dell'ufficio)
1. Nelle impostazioni dell'app:
   - Abilita **Local Server**.
   - Imposta una porta (es. `8080`).
   - Imposta **Username** e **Password** (Basic Auth) per proteggere l'accesso.
2. Premi **Start**. L'app mostrerà l'indirizzo IP locale, ad esempio: `http://192.168.1.150:8080`.
3. In Mappa Algeco imposta l'URL: `http://192.168.1.150:8080/message`.

#### Modalità B: Cloud Server (sms-gate.app)
1. Registra un account gratuito su [sms-gate.app](https://sms-gate.app).
2. Nell'app del telefono accedi con le tue credenziali.
3. In Mappa Algeco imposta l'URL endpoint fornito dal servizio (es. `https://api.sms-gate.app/3rdparty/v1/message`) con Basic Auth o Bearer Token.

---

## Configurazione nel Gestionale Mappa Algeco

1. Accedi alla **Dashboard Admin**: `https://admin.<tuo-dominio>/` (ad esempio `https://admin.mappa-algeco.com/` — assicurati di aver effettuato prima il login su una provincia).
2. Nella card della provincia desiderata (es. **Torino**, **Pavia** o **Lecco**), clicca sul pulsante `📱 SMS Gateway` (oppure clicca su **`Gateway SMS`** direttamente nell'intestazione della pagina Distinte).
3. Configura:
   - **Abilita Gateway SMS**: Spuntato.
   - **Provider**: `Android SMS Gateway (App Open Source su smartphone aziendale)`.
   - **URL Endpoint**: `http://<IP_SMARTPHONE>:8080/message`.
   - **Autenticazione**: Seleziona `Basic Auth` e inserisci Username e Password definiti nell'app.
   - **Pausa tra invii (anti-flood)**: `4` secondi (raccomandato per non allarmare i filtri antispam dell'operatore telefonico).
4. Clicca su **Salva Impostazioni**.
5. Esegui un **Invio di Prova** digitando il tuo cellulare per verificare la ricezione.

---

## Utilizzo nel Gestionale (Distinte e Gestione Lettere)

La modale di invio multiplo SMS è disponibile sia nella pagina **Gestione Distinte** (`/distinte`) sia nella pagina **Gestione Lettere** (`/lettere`, sezione *Invio Multiplo SMS a Utenti Privati*):

1. Apri la pagina desiderata (**Distinte** oppure **Gestione Lettere**).
2. Clicca sul pulsante **`📱 Invio Multiplo SMS`**.
3. Configura l'invio nella modale:
   - Seleziona un template predefinito o personalizza il testo con i segnaposto automatici (`{nome}`, `{data}`, `{comune}`, ecc.).
   - Filtra o seleziona i destinatari con numero di cellulare valido (la normalizzazione in E.164 `+39...` e i preset `Tutti`, `Monofase`, `Trifase`, `Semidiretta`, `Con Note`, `Senza Note` velocizzano la selezione in Distinte).
   - Il contatore live verifica lunghezza caratteri, encoding (**GSM-7** 160 car/SMS vs **UCS-2** con emoji/caratteri speciali) e numero stimato di SMS.
   - Premi **Invia SMS Multipli**.
4. Il sistema esegue l'invio sequenziale rispettando la pausa anti-flood, mostra avanzamento ed esito per singolo destinatario e registra automaticamente la nota nel DB SQLite locale (`mandato SMS`) e nell'`audit_log`.
