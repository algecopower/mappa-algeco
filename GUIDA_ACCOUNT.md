# Guida alla Creazione e Gestione degli Account

Questa guida spiega come creare, modificare, eliminare e gestire gli account utente del **Gestionale Mappa Contatori**.

---

## Indice

1. [Prerequisiti](#1-prerequisiti)
2. [Avvio rapido](#2-avvio-rapido)
3. [Menu interattivo](#3-menu-interattivo)
4. [Comandi da riga di comando (CLI)](#4-comandi-da-riga-di-comando-cli)
5. [Creazione utente](#5-creazione-utente)
6. [Gestione province](#6-gestione-province)
7. [Reset password](#7-reset-password)
8. [Attivazione / Disattivazione utente](#8-attivazione--disattivazione-utente)
9. [Eliminazione utente](#9-eliminazione-utente)
10. [Creazione utenti in bulk](#10-creazione-utenti-in-bulk)
11. [Sicurezza e note tecniche](#11-sicurezza-e-note-tecniche)
12. [Risoluzione problemi](#12-risoluzione-problemi)

---

## 1. Prerequisiti

- Accesso SSH al server (o terminale locale)
- Ambiente virtuale Python attivato:

```bash
cd /opt/mappa-algeco
source venv/bin/activate
```

Lo script da utilizzare è `gestione_utenti.py`, che si trova nella directory principale del progetto.

---

## 2. Avvio rapido

### Creare un utente con password generata automaticamente:

```bash
python gestione_utenti.py --crea nomeutente
```

### Visualizzare tutti gli utenti:

```bash
python gestione_utenti.py --lista
```

### Aprire il menu interattivo completo:

```bash
python gestione_utenti.py
```

---

## 3. Menu interattivo

Lanciando lo script senza argomenti si apre un menu guidato con tutte le operazioni disponibili:

```bash
python gestione_utenti.py
```

```
============================================================
  GESTIONE UTENTI - Gestionale Mappa Contatori
============================================================

Operazioni disponibili:
  1. 📋 Lista utenti
  2. ➕ Crea nuovo utente
  3. 📦 Crea utenti in bulk
  4. 🗑️  Elimina utente
  5. 🔄 Reset password
  6. 🔒 Attiva/Disattiva utente
  7. 🗺️  Gestione province
  0. 🚪 Esci
```

Seleziona il numero dell'operazione desiderata e segui le istruzioni a schermo.

---

## 4. Comandi da riga di comando (CLI)

Tutti i comandi disponibili in modalità non interattiva:

| Comando | Descrizione |
|---------|-------------|
| `python gestione_utenti.py` | Menu interattivo |
| `python gestione_utenti.py --lista` | Lista tutti gli utenti |
| `python gestione_utenti.py --crea NOME` | Crea utente con password casuale |
| `python gestione_utenti.py --crea NOME --password PASS` | Crea utente con password specifica |
| `python gestione_utenti.py --elimina NOME` | Elimina un utente |
| `python gestione_utenti.py --reset NOME` | Reset password (genera nuova casuale) |
| `python gestione_utenti.py --toggle NOME` | Attiva/Disattiva un utente |
| `python gestione_utenti.py --bulk N` | Crea N utenti in blocco |
| `python gestione_utenti.py --province NOME TORINO,PAVIA` | Imposta le province accessibili |

### Alias abbreviati

Alcuni comandi hanno alias brevi:

| Lungo | Corto |
|-------|-------|
| `--crea` | `-c` |
| `--lista` | `-l` |
| `--elimina` | `-e` |
| `--toggle` | `-t` |
| `--bulk` | `-b` |
| `--password` | `-p` |

Esempio: `python gestione_utenti.py -c mario` equivale a `python gestione_utenti.py --crea mario`

---

## 5. Creazione utente

### Con password generata automaticamente

```bash
python gestione_utenti.py --crea giovanni
```

Output:
```
✅ Utente 'giovanni' creato con successo

🔐 PASSWORD GENERATA: FAqo9XdU3CtcUWzR
   ⚠️ Salva questa password, non sarà più visibile!
```

> **IMPORTANTE**: Annota subito la password generata. Non sarà possibile recuperarla in seguito (è salvata solo come hash).

### Con password personalizzata

```bash
python gestione_utenti.py --crea giovanni --password MiaPassword123!
```

### Tramite menu interattivo (opzione 2)

1. Seleziona `2` dal menu
2. Inserisci lo username
3. Scegli se generare la password automaticamente o inserirla manualmente
4. La password minima è di **8 caratteri**

### Cosa succede alla creazione

- Lo username viene **normalizzato in minuscolo** (es. "Giovanni" → "giovanni")
- La password viene hashata con **PBKDF2-SHA256** (100.000 iterazioni)
- L'utente riceve accesso a **tutte le province** di default (TORINO + PAVIA)
- L'account è **attivo** immediatamente

---

## 6. Gestione province

Ogni utente può avere accesso a una o più province. Le province disponibili nel sistema sono:

- **TORINO**
- **PAVIA**

### Come funziona il controllo province

Il server avvia istanze Flask separate per ogni provincia (Torino sulla porta 5001, Pavia sulla porta 5002). Al momento del login, il sistema verifica che l'utente abbia accesso alla provincia dell'istanza a cui si sta collegando. Se non autorizzato, il login viene rifiutato con il messaggio *"Non hai accesso alla provincia X"*.

### Impostare le province via CLI

```bash
# Accesso a entrambe le province
python gestione_utenti.py --province giovanni TORINO,PAVIA

# Solo Torino
python gestione_utenti.py --province giovanni TORINO

# Solo Pavia
python gestione_utenti.py --province giovanni PAVIA
```

### Impostare le province via menu interattivo (opzione 7)

1. Seleziona `7` dal menu
2. Verrà mostrata la lista utenti con le province attuali
3. Inserisci lo username da modificare
4. Vedrai le province correnti e quelle disponibili
5. Inserisci le nuove province separate da virgola, oppure `tutte`

Esempio di output nel menu:
```
🗺️  GESTIONE PROVINCE UTENTE
----------------------------------------

Province attuali di 'giovanni': TORINO, PAVIA
Province disponibili: TORINO, PAVIA

  1. TORINO ✅
  2. PAVIA ✅

Inserisci le province separate da virgola (es: TORINO,PAVIA)
oppure 'tutte' per assegnare tutte le province:

Province: TORINO
✅ Province di 'giovanni' aggiornate: TORINO
```

### Configurazione attuale degli utenti

| Utente | Province |
|--------|----------|
| giada | TORINO, PAVIA |
| gessica | TORINO, PAVIA |
| giovanni | TORINO, PAVIA |

---

## 7. Reset password

### Via CLI

```bash
python gestione_utenti.py --reset giovanni
```

Output:
```
✅ Password di 'giovanni' resettata

🔐 NUOVA PASSWORD: xK7mP2nQ9rLsW4vB
```

### Via menu interattivo (opzione 5)

1. Seleziona `5` dal menu
2. Inserisci lo username
3. La nuova password viene generata automaticamente

> **NOTA**: Non è possibile impostare una password specifica dal reset via CLI. Per farlo, eliminare e ricreare l'utente con `--crea NOME --password NUOVAPASSWORD`, oppure usare il menu interattivo per la creazione.

---

## 8. Attivazione / Disattivazione utente

Disattivare un utente ne impedisce il login senza eliminare l'account. Utile per sospensioni temporanee.

### Via CLI

```bash
python gestione_utenti.py --toggle giovanni
```

Output:
```
✅ Utente 'giovanni' ora è disabilitato
```

Eseguendo di nuovo lo stesso comando, l'utente viene riattivato:
```
✅ Utente 'giovanni' ora è attivo
```

### Via menu interattivo (opzione 6)

1. Seleziona `6` dal menu
2. Vedrai la lista utenti con lo stato attuale (✅ attivo / ❌ disabilitato)
3. Inserisci lo username da modificare

### Cosa succede a un utente disabilitato

- Il login viene rifiutato con il messaggio *"Account disabilitato"*
- Le sessioni attive **non** vengono invalidate automaticamente (scadranno naturalmente dopo 24 ore)
- I dati dell'utente restano intatti

---

## 9. Eliminazione utente

### Via CLI

```bash
python gestione_utenti.py --elimina giovanni
```

### Via menu interattivo (opzione 4)

1. Seleziona `4` dal menu
2. Vedrai la lista utenti
3. Inserisci lo username da eliminare
4. Conferma con `s`

> **ATTENZIONE**: L'eliminazione è **permanente e irreversibile**. L'utente e tutti i suoi dati di accesso vengono rimossi.

---

## 10. Creazione utenti in bulk

Per creare più utenti contemporaneamente:

### Via CLI

```bash
python gestione_utenti.py --bulk 5
```

Crea 5 utenti con nomi `utente1`, `utente2`, ..., `utente5` e password casuali.

### Via menu interattivo (opzione 3)

1. Seleziona `3` dal menu
2. Inserisci il numero di utenti da creare
3. Scegli un prefisso personalizzato (default: `utente`)
4. Le credenziali vengono mostrate a schermo
5. Puoi scegliere di salvarle in un file di testo

Output di esempio:
```
📋 Utenti creati:
------------------------------------------------------------
Username             Password
------------------------------------------------------------
tecnico1             aB3$kL9mNpQrS7vX
tecnico2             wE5&hJ2cDfGtY8zA
------------------------------------------------------------

✅ Creati 2/2 utenti
Salvare credenziali in file? [S/n]:
✅ Credenziali salvate in: credenziali_utenti_tecnico.txt
```

---

## 11. Sicurezza e note tecniche

### Archiviazione password

- Le password **non** vengono mai salvate in chiaro
- Viene utilizzato **PBKDF2-SHA256** con **100.000 iterazioni** e un **salt** casuale unico per ogni utente
- Il file `users.json` contiene solo hash e salt

### Sessioni

- Le sessioni hanno una durata di **24 ore**
- I cookie sono configurati con `HttpOnly` e `SameSite=Lax`
- In produzione (via Cloudflare) i cookie sono `Secure` (solo HTTPS)

### Protezione brute-force

- Il server implementa un limite ai tentativi di login per indirizzo IP
- Dopo troppi tentativi falliti, l'IP viene temporaneamente bloccato
- Un ritardo artificiale viene aggiunto per prevenire l'enumerazione degli utenti

### File coinvolti

| File | Descrizione |
|------|-------------|
| `users.json` | Database utenti (hash password, province, stato) |
| `gestione_utenti.py` | Script CLI per la gestione |
| `moduli/auth.py` | Modulo di autenticazione (funzioni core) |
| `.flask_secret_key` | Chiave segreta per le sessioni (generata automaticamente) |

### Struttura dati utente in `users.json`

```json
{
  "giovanni": {
    "password_hash": "adb2d892...",
    "salt": "e3df59ec...",
    "created_at": "2026-04-13T14:09:36.897861",
    "last_login": null,
    "active": true,
    "province": ["TORINO", "PAVIA"]
  }
}
```

---

## 12. Risoluzione problemi

### "Utente già esistente"

Lo username è già registrato. Usa `--lista` per verificare gli utenti esistenti.

### "Credenziali non valide" al login

- Verifica che lo username sia corretto (viene normalizzato in minuscolo)
- Usa `--reset NOME` per generare una nuova password
- Verifica che l'account sia attivo con `--lista`

### "Non hai accesso alla provincia X"

L'utente non ha i permessi per la provincia a cui sta tentando di accedere. Usa:
```bash
python gestione_utenti.py --province NOME TORINO,PAVIA
```

### "Account disabilitato"

L'account è stato disattivato. Riattivalo con:
```bash
python gestione_utenti.py --toggle NOME
```

### Password dimenticata

Non è possibile recuperare la password originale. Generane una nuova:
```bash
python gestione_utenti.py --reset NOME
```

### Il file `users.json` è corrotto

Se il file JSON è danneggiato, il sistema lo tratterà come vuoto. Verifica la sintassi JSON o ripristina da un backup.
