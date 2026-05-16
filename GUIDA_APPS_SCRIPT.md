# Apps Script — Sync near-realtime da Google Sheets

> Documento per chi vorrà attivare in futuro la notifica push da Google Sheets verso il gestionale, riducendo la latenza delle modifiche sul foglio da ~60s a <5s.
>
> Stato attuale: l'endpoint server-side è già pronto, ma Apps Script **non è installato** sui fogli. Il sync funziona via polling ogni 60 secondi.

## Architettura

```
Modifica cella su Google Sheets
            │
            ▼
   Apps Script onEdit  ──POST─▶  /api/sync/webhook (server)
                                        │
                                        ▼
                          _sync_needed.set() → sync immediato
```

- **Endpoint:** `POST /api/sync/webhook` definito in [routes/sync.py](routes/sync.py) (function `sync_webhook`).
- **Autenticazione:** header `X-Sync-Secret` confrontato con env var `SYNC_WEBHOOK_SECRET`.
- **Secret in produzione:** `/etc/mappa-algeco/sync.env` (file 600, leggibile solo da root). Caricato dal systemd service via `EnvironmentFile=`.
- **Esclusione CSRF:** registrata in [server.py](server.py) — `csrf_check()` salta questo path.

## Cosa serve fare per attivarlo

### Lato server (già fatto)
- Endpoint webhook ✓
- Secret generato in `/etc/mappa-algeco/sync.env` ✓
- `SYNC_WEBHOOK_SECRET` esposto al processo via systemd ✓

### Lato Google (da fare)
Va fatto **due volte**, una per il foglio Torino e una per il foglio Pavia. L'unica differenza è l'URL del webhook.

#### 1. Recuperare il secret dal server

```bash
sudo grep SYNC_WEBHOOK_SECRET /etc/mappa-algeco/sync.env
```

Conserva il valore in modo sicuro per i prossimi passi (NON inviarlo via email/Slack pubblici).

#### 2. Aprire l'editor Apps Script

1. Aprire il foglio Google Sheets (Torino o Pavia)
2. Menu **Estensioni → Apps Script**
3. Cancellare il codice presente

#### 3. Incollare lo script

```javascript
// === CONFIGURAZIONE ===
const SYNC_SECRET = 'INSERIRE_QUI_IL_SECRET_DAL_SERVER';

// Per il foglio di TORINO:
const WEBHOOK_URL = 'https://torino.mappa-algeco.com/api/sync/webhook';
// Per il foglio di PAVIA usare invece:
// const WEBHOOK_URL = 'https://pavia.mappa-algeco.com/api/sync/webhook';

// Debounce: non chiamare il webhook più di 1 volta ogni 5 secondi
// (evita raffica di chiamate se si fanno più modifiche consecutive).
const DEBOUNCE_MS = 5000;

function onSheetEdit(e) {
  const props = PropertiesService.getScriptProperties();
  const last = parseInt(props.getProperty('last_webhook') || '0', 10);
  const now = Date.now();
  if (now - last < DEBOUNCE_MS) return;
  props.setProperty('last_webhook', String(now));

  try {
    UrlFetchApp.fetch(WEBHOOK_URL, {
      method: 'post',
      headers: { 'X-Sync-Secret': SYNC_SECRET },
      muteHttpExceptions: true,
    });
  } catch (err) {
    console.error('Webhook fallito:', err);
  }
}
```

#### 4. Salvare e installare il trigger

1. Salvare il progetto (Ctrl+S). Nome suggerito: "Mappa Algeco — sync trigger".
2. Aprire il pannello **Trigger** (icona orologio nella sidebar sinistra).
3. Cliccare **"+ Aggiungi trigger"** in basso a destra e impostare:
   - **Scegli quale funzione eseguire:** `onSheetEdit`
   - **Scegli quale distribuzione:** `Head`
   - **Seleziona origine evento:** `Dal foglio di calcolo`
   - **Seleziona tipo di evento:** `Alla modifica`
   - Cliccare **Salva**.
4. Si apre il popup di autorizzazione Google:
   - Selezionare l'account Google
   - Avviso "Google non ha verificato questa app" → **"Avanzate"** → **"Vai a Mappa Algeco — sync trigger (non sicuro)"**
   - **"Consenti"** (richiede permessi di lettura foglio e chiamate HTTP esterne).

#### 5. Test

1. Modificare una cella qualsiasi del foglio.
2. Sul server, in un'altra shell:

   ```bash
   journalctl -u mappa-algeco-router -f | grep webhook
   ```

   Entro 2-3 secondi dovresti vedere:

   ```
   [webhook] sync trigger ricevuto da <IP_GOOGLE>
   ```

3. Aprire la mappa nel browser → entro pochi secondi la modifica appare.

## Dopo aver verificato che funziona

Si può alzare il polling fallback a 10 minuti (`INTERVALLO_SYNC_SECONDI = 600` in [config.py](config.py) + nei `data/<provincia>/settings.json`). Il polling resta come ultima rete di sicurezza se Apps Script smettesse di funzionare; il webhook fornisce la latenza reale.

## Note di sicurezza

- Il secret è una **password**. Se viene compromessa, basta rigenerarne uno nuovo:
  ```bash
  NEW=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
  echo "SYNC_WEBHOOK_SECRET=$NEW" | sudo tee /etc/mappa-algeco/sync.env
  sudo chmod 600 /etc/mappa-algeco/sync.env
  sudo systemctl restart mappa-algeco-router
  ```
  Poi aggiornare la costante `SYNC_SECRET` in entrambi gli script Apps Script.
- L'endpoint accetta solo POST. È pubblicamente raggiungibile (per consentire la chiamata da IP Google), ma senza il secret risponde sempre 403.
- Non condividere il file `/etc/mappa-algeco/sync.env` né committarlo in git.

## Troubleshooting

| Sintomo | Causa probabile | Fix |
|---|---|---|
| Webhook risponde 403 sempre | Secret in Apps Script ≠ secret nel server | Riallineare `SYNC_SECRET` nello script |
| Webhook risponde 503 "webhook non configurato" | `SYNC_WEBHOOK_SECRET` non visibile al processo | Verificare `EnvironmentFile=` nel service e riavviare |
| Nessuna chiamata arriva al server modificando il foglio | Trigger non installato o autorizzazione revocata | Ripetere passi 4-5 |
| Errore "Service unavailable" in Apps Script | Server offline o Cloudflare giù | Controllare `systemctl status mappa-algeco-router` |
