# Deploy multi-tenant — Mappa Algeco

Architettura del deploy in produzione dopo la migrazione al router.

## Componenti

```
Cloudflare wildcard *.mappa-algeco.com
         │
         ▼
cloudflared-tunnel.service    (systemd, indipendente)
         │
         ▼
mappa-algeco-router.service   (systemd, supervisor)
   │
   ├─ router.py (porta 5050)                  ← reverse proxy by Host header + UI admin
   ├─ gunicorn wsgi:app PROVINCIA=TORINO :5001 ← backend Torino (1 worker, 32 thread)
   ├─ gunicorn wsgi:app PROVINCIA=PAVIA  :5002 ← backend Pavia
   ├─ gunicorn wsgi:app PROVINCIA=LECCO  :5003 ← backend Lecco
   └─ gunicorn wsgi:app PROVINCIA=...    :500N ← altre province (spawnate dinamicamente)
```

## File rilevanti in questa cartella

| File | Destinazione | Note |
|---|---|---|
| `cloudflared-config.yml.example` | `/etc/cloudflared/config.yml` | Wildcard ingress `*.mappa-algeco.com` su `localhost:5050` |
| `mappa-algeco-router.service.example` | `/etc/systemd/system/mappa-algeco-router.service` | Supervisor router + backend Gunicorn per provincia |
| `mappa-algeco-restart.service.example` | `/etc/systemd/system/mappa-algeco-restart.service` | Unit oneshot per restart preventivo di `mappa-algeco-router.service` |
| `mappa-algeco-restart.timer.example` | `/etc/systemd/system/mappa-algeco-restart.timer` | Timer systemd notturno (04:30 con jitter fino a 5 min) |
| `mappa-algeco-auto-import` | `/etc/cron.d/mappa-algeco-auto-import` | Cron orario (`:05`) per auto-import read-only da Google Sheets |

Il file `cloudflared-tunnel.service` (in `/etc/systemd/system/`) gira indipendentemente — `cloudflared tunnel run mappa-algeco` (`Restart=always`).

## Aggiungere una nuova provincia

**Operazione "self-serve" via UI:**

1. Login con un account di ruolo `admin` su una provincia esistente (es. `torino.mappa-algeco.com`) — la sessione viene riusata su `admin.*` grazie a `SESSION_COOKIE_DOMAIN=.mappa-algeco.com`.
2. Vai su `https://admin.mappa-algeco.com/`.
3. Clicca **➕ Aggiungi provincia**, compila codice slug, nome, sottodominio, porta, deposito, orari ed eventualmente l'URL pubblico di Google Sheets (per l'auto-import in sola lettura).
4. Sulla card della nuova provincia, clicca **▶ Avvia**: viene inizializzato un `state.db` SQLite vuoto e avviato il worker Gunicorn.
5. Inizializza struttura e dati scegliendo una di queste strade:
   - **🪄 Configura (wizard)**: carica le colonne standard del filone, configura operatori e abilita gli utenti senza partire da un file esistente.
   - **📥 Importa Excel**: carica direttamente un `.xlsx`/`.csv` del filone (creando automaticamente le colonne rilevate).
   - **⚡ Da Sheets**: se hai impostato `google_sheet_url`, scarica ed importa subito l'export XLSX pubblico.
6. Configura opzionalmente **📧 SMTP**, **📱 SMS Gateway**, **📬 Excel lettere** e **🏷️ LCL e Zone** direttamente dai pulsanti della card.

**Operazione manuale che resta:** aggiungere il record DNS su Cloudflare per il nuovo sottodominio.

- Vai su Cloudflare dashboard → mappa-algeco.com → DNS.
- Aggiungi un CNAME: `{slug}.mappa-algeco.com` → `<TUNNEL_UUID>.cfargotunnel.com` (proxied).
- Oppure (raccomandato): aggiungi una volta sola il wildcard `*.mappa-algeco.com` → `<TUNNEL_UUID>.cfargotunnel.com`, così le nuove province non richiedono più nuovi record DNS.

## Comandi utili

```bash
# Stato servizi
systemctl status mappa-algeco-router cloudflared-tunnel

# Logs
journalctl -u mappa-algeco-router -f
journalctl -u cloudflared-tunnel -f

# Restart router (i backend ripartono in cascata)
systemctl restart mappa-algeco-router

# Health interna router
curl -s http://127.0.0.1:5050/_router/health | jq

# Smoke test proxy
curl -s -H "Host: torino.mappa-algeco.com" http://127.0.0.1:5050/login | head -3
```

## Servizi systemd e schedulazioni attive (post-migrazione)

Unit systemd principali in produzione:
- `mappa-algeco-router.service` — supervisor router + backend Gunicorn per provincia
- `cloudflared-tunnel.service` — tunnel Cloudflare
- `osrm-server.service` — routing engine stradale OSRM (porta 5000)
- `mappa-algeco-restart.timer` (+ `mappa-algeco-restart.service`) — riavvio notturno preventivo alle 04:30
- `/etc/cron.d/mappa-algeco-auto-import` — controllo orario (`:05`) dell'export XLSX Google Sheets in sola lettura

```bash
systemctl status mappa-algeco-router cloudflared-tunnel osrm-server mappa-algeco-restart.timer
systemctl restart mappa-algeco-router    # comando "normale" di restart
tail -f /var/log/mappa-auto-import.log   # log auto-import orario
```

⚠️ **NON usare** `systemctl restart mappa` né `systemctl restart mappa-algeco`:
quei due unit file esistono ancora sotto `/etc/systemd/system/` ma sono
**rinominati con suffisso `.disabled`** così systemd non li vede:
- `mappa.service.disabled` (vecchio launcher monolitico)
- `mappa-algeco.service.disabled` (variante mai usata)

I file restano come reference per eventuale rollback (vedi sezione sotto).

## Rollback (se qualcosa va storto)

```bash
# 1. Stop nuovo stack
systemctl stop mappa-algeco-router cloudflared-tunnel
systemctl disable mappa-algeco-router cloudflared-tunnel

# 2. Ripristina cloudflared config legacy (path-per-host)
cp /etc/cloudflared/config.yml.pre-router-bak /etc/cloudflared/config.yml

# 3. Riattiva il vecchio unit
mv /etc/systemd/system/mappa.service.disabled /etc/systemd/system/mappa.service
systemctl daemon-reload
systemctl enable mappa.service
systemctl start mappa.service
```

## Storia

- Step 1: `data/provinces.json` come fonte di verità della config province (commit `8c711df`)
- Step 2: `router.py` base con proxy by Host + ProcessManager (commit `0809e3c`)
- Step 3: UI admin nel router (commit `b233de6`)
- Step 4: probe foglio + bootstrap DB (commit `d3dc33f`)
- Step 5: cutover sistemico — cloudflared wildcard + systemd unit (questo deploy)
