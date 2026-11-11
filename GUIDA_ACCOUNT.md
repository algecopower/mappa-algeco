# Guida alla Creazione e Gestione degli Account

Questa guida spiega come creare, modificare, abilitare e gestire gli account utente del **Gestionale Mappa Contatori**.

---

## Indice

1. [Due modalità di gestione](#1-due-modalità-di-gestione)
2. [Gestione via Web UI Admin](#2-gestione-via-web-ui-admin)
3. [Prerequisiti CLI e avvio rapido](#3-prerequisiti-cli-e-avvio-rapido)
4. [Comandi CLI (`gestione_utenti.py`)](#4-comandi-cli-gestione_utentipy)
5. [Ruoli e controllo province](#5-ruoli-e-controllo-province)
6. [Sicurezza, password (Argon2id) e sessioni](#6-sicurezza-password-argon2id-e-sessioni)
7. [Risoluzione problemi](#7-risoluzione-problemi)

---

## 1. Due modalità di gestione

Gli account in [`users.json`](users.json) (file locale escluso da Git) possono essere gestiti in due modi equivalenti:

1. **Da Web UI Admin** (consigliato nell'uso quotidiano): accessibile agli utenti con ruolo `admin` sia su `https://admin.<dominio>/` (pulsante **🔑 Utenti**) sia dalla scheda **🔑 Utenti** nelle impostazioni della mappa.
2. **Da terminale via CLI** ([`gestione_utenti.py`](gestione_utenti.py)): utile in fase di primo bootstrap del server o via SSH.

---

## 2. Gestione via Web UI Admin

Dalla dashboard router (`https://admin.mappa-algeco.com/` → **🔑 Utenti**) un amministratore può eseguire tutte le operazioni senza accesso SSH:

- **Creare nuovi utenti** indicando username, password (o generazione casuale), ruolo (`user` oppure `admin`) e province autorizzate;
- **Assegnare o revocare province** dinamicamente in base a [`data/provinces.json`](data/provinces.json);
- **Cambiare il ruolo** (`admin` / `user`);
- **Resettare la password**;
- **Attivare / disattivare** temporaneamente un account;
- **Eliminare** definitivamente un account.

Le stesse funzionalità sono esposte dalle API `/api/admin/users*` in [`server.py`](server.py) e [`router.py`](router.py).

---

## 3. Prerequisiti CLI e avvio rapido

- Accesso al terminale o SSH sul server
- Ambiente virtuale Python del progetto:

```bash
cd /opt/mappa-algeco
source venv/bin/activate
```

```bash
# Menu interattivo guidato
python gestione_utenti.py

# Visualizzare tutti gli utenti (stato, ruolo, province, ultimo login)
python gestione_utenti.py --lista

# Creare un utente con password casuale generata automaticamente
python gestione_utenti.py --crea nome.cognome

# Creare un utente con password specifica (minimo 8 caratteri)
python gestione_utenti.py --crea nome.cognome --password MiaPasswordSicura1!
```

> **Nota:** la password generata è salvata solo come hash Argon2id e non è più recuperabile. I nuovi utenti creati da CLI hanno ruolo `user`; per promuovere il primo utente a `admin` (ad esempio al primo setup di un server) esegui:
> ```bash
> python -c "from moduli.auth import set_user_role; print(set_user_role('nome.cognome', 'admin'))"
> ```

---

## 4. Comandi CLI (`gestione_utenti.py`)

Tutti i flag supportati da [`gestione_utenti.py`](gestione_utenti.py):

| Comando | Alias corto | Descrizione |
|---|---|---|
| `python gestione_utenti.py` | — | Apre il menu interattivo (7 operazioni) |
| `python gestione_utenti.py --lista` | `-l` | Elenca utenti, stato, ruolo, province e ultimo login |
| `python gestione_utenti.py --crea NOME` | `-c` | Crea utente con password casuale |
| `python gestione_utenti.py --crea NOME --password PASS` | `-c ... -p ...` | Crea utente con password esplicita (min 8 caratteri) |
| `python gestione_utenti.py --elimina NOME` | `-e` | Elimina permanentemente un utente |
| `python gestione_utenti.py --reset NOME` | — | Genera una nuova password casuale per l'utente |
| `python gestione_utenti.py --toggle NOME` | `-t` | Attiva/Disattiva temporaneamente l'account |
| `python gestione_utenti.py --bulk N` | `-b` | Crea `N` account in blocco (`utente1..utenteN`, max 100) |
| `python gestione_utenti.py --province NOME PROV1,PROV2` | — | Imposta l'elenco delle province accessibili |

> **Attenzione:** `--reset NOME` da CLI genera sempre una nuova password casuale (non accetta `--password`). Per impostare una password scelta manualmente su un account esistente usa la Web UI Admin oppure elimina e ricrea l'account.

### Menu interattivo

Lanciando `python gestione_utenti.py` senza argomenti sono disponibili:
1. Lista utenti (`--lista`)
2. Crea nuovo utente
3. Crea utenti in bulk (con prefisso personalizzabile ed export opzionale su file)
4. Elimina utente
5. Reset password
6. Attiva/Disattiva utente
7. Gestione province (accetta lista separata da virgole o `tutte`)

---

## 5. Ruoli e controllo province

### Ruoli disponibili (`RUOLI_DISPONIBILI` in [`moduli/auth.py`](moduli/auth.py))

| Ruolo | Permessi |
|---|---|
| `user` (default) | Accesso operativo completo alle province assegnate (mappa, distinte, lettere, mail, calendario, report, filone). |
| `admin` | Tutti i permessi operativi più gestione configurazione provincia, impostazioni SMTP/SMS, import/outbox DB, dashboard router `admin.*` e gestione utenti/ruoli. |

### Controllo accesso per provincia

Le province disponibili non sono fisse nel codice ma vengono caricate dinamicamente da [`data/provinces.json`](data/provinces.json) tramite `get_available_provinces()` (ad esempio `TORINO`, `PAVIA`, `LECCO`).

- Alla creazione da CLI l'utente riceve accesso a **tutte le province** configurate al momento.
- Per limitare un utente a specifiche province:

```bash
python gestione_utenti.py --province tecnico.rossi TORINO
python gestione_utenti.py --province coord.nord TORINO,PAVIA,LECCO
```

Ogni backend provinciale verifica ad ogni richiesta (`@app.before_request: check_provincia_access()` in [`server.py`](server.py)) che l'utente autenticato abbia la provincia corrente nella propria lista aggiornata da `users.json`. In caso contrario la sessione viene respinta.

---

## 6. Sicurezza, password (Argon2id) e sessioni

### Hashing password (`moduli/auth.py`)

- Tutti i nuovi account e i reset/cambi password utilizzano **Argon2id** tramite `argon2-cffi` (`PasswordHasher()`).
- Per retrocompatibilità con account storici, il sistema verifica anche eventuali vecchi hash **PBKDF2-SHA256** (100.000 iterazioni + salt) ed esegue automaticamente l'**upgrade trasparente ad Argon2id** al primo login riuscito.
- Gli username sono normalizzati in minuscolo e devono rispettare i requisiti di lunghezza minima password (**8 caratteri**).

### Struttura indicativa di `users.json`

```json
{
  "admin.example": {
    "password_hash": "$argon2id$v=19$m=65536,t=3,p=4$...",
    "created_at": "2026-06-01T09:00:00.000000",
    "last_login": "2026-11-11T08:30:12.000000",
    "active": true,
    "ruolo": "admin",
    "province": ["TORINO", "PAVIA", "LECCO"]
  }
}
```

> `users.json` e `.flask_secret_key` sono elencati in [`.gitignore`](.gitignore) e non devono mai essere committati nel repository.

### Protezioni sessione, CSRF e brute-force

- **Cookie di sessione:** durata **24 ore**, attributi `HttpOnly`, `SameSite=Lax`, e `Secure` quando `FLASK_ENV=production`. Con `SESSION_COOKIE_DOMAIN=.mappa-algeco.com` la sessione autenticata è condivisa tra i sottodomini provincia e `admin.*`.
- **Protezione CSRF & Origin:** tutte le mutazioni `POST`/`PUT`/`DELETE` autenticate verificano sia gli header `Origin`/`Referer` contro i sottodomini fidati sia il doppio token (`X-CSRF-Token` confrontato in tempo costante con `session['csrf_token']`).
- **Rate limiting anti brute-force:** su `POST /api/login` il backend blocca temporaneamente l'IP per **60 secondi** dopo **5 tentativi falliti** (`429 Too Many Requests`). Inoltre l'autenticazione applica un piccolo ritardo anti-enumerazione in caso di username inesistente.

---

## 7. Risoluzione problemi

- **"Account disabilitato":** riattiva l'account da Web UI Admin o con `python gestione_utenti.py --toggle NOME`.
- **"Non hai accesso alla provincia X":** assegna la provincia mancante da Web UI Admin oppure con `python gestione_utenti.py --province NOME TORINO,PAVIA,LECCO`.
- **"Troppi tentativi":** attendi 60 secondi prima di riprovare il login.
- **Dimenticata la password di un amministratore:** accedi via SSH al server ed esegui `python gestione_utenti.py --reset NOME` (genera una nuova password casuale) oppure crea un nuovo utente e promuovilo dalla UI admin.
