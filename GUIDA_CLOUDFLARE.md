# 🌐 Guida Cloudflare Tunnel (Multi-Tenant Wildcard)

Configura l'accesso remoto HTTPS sicuro senza esporre porte pubbliche sulla VPS, instradando tutti i sottodomini provincia e `admin.*` verso il reverse proxy [`router.py`](router.py) sulla porta `5050`.

---

## Architettura del traffico

```
Browser (https://torino.mappa-algeco.com, admin.mappa-algeco.com, ...)
   │  HTTPS (TLS gestito da Cloudflare Edge)
   ▼
Cloudflare Network  ──▶  Tunnel cifrato (outbound dalla VPS)
                              │
                              ▼
                  cloudflared-tunnel.service (localhost:5050)
                              │
                              ▼
                  router.py (mappa-algeco-router.service)
                    ├─ Host: admin.*  ──▶ UI Admin multi-provincia
                    ├─ Host: torino.* ──▶ Gunicorn :5001 (TORINO)
                    ├─ Host: pavia.*  ──▶ Gunicorn :5002 (PAVIA)
                    └─ Host: lecco.*  ──▶ Gunicorn :5003 (LECCO)
```

---

## 1. Prerequisiti dominio

1. Aggiungi il tuo dominio (es. `mappa-algeco.com`) su [Cloudflare Dashboard](https://dash.cloudflare.com) (registrazione diretta o trasferimento DNS).
2. Attendi che lo stato del dominio sia **Active**.

---

## 2. Installazione `cloudflared` (Linux VPS)

Su Ubuntu/Debian:

```bash
curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o /tmp/cloudflared.deb
sudo dpkg -i /tmp/cloudflared.deb
rm /tmp/cloudflared.deb
cloudflared --version
```

> **Sviluppo / test locale su Windows:** `cloudflared` può essere installato anche con `winget install Cloudflare.cloudflared` puntando al router locale `http://localhost:5050`.

---

## 3. Autenticazione e creazione del tunnel

```bash
# 1. Autentica il nodo (apre URL di autorizzazione Cloudflare nel browser)
cloudflared tunnel login

# 2. Crea il tunnel (annotare l'UUID restituito)
cloudflared tunnel create mappa-algeco

# Verifica tunnel e file credenziali
cloudflared tunnel list
```

Il file credenziali verrà salvato in `/root/.cloudflared/<TUNNEL_UUID>.json`.

---

## 4. Configurazione DNS su Cloudflare

### Opzione consigliata: DNS Wildcard `*.mappa-algeco.com`

Nel pannello **Cloudflare Dashboard → DNS → Records**, crea un solo record CNAME:

| Tipo | Nome | Destinazione | Proxy |
|---|---|---|---|
| `CNAME` | `*` | `<TUNNEL_UUID>.cfargotunnel.com` | **Proxied** (nuvoletta arancione) |

Con il record wildcard, quando aggiungi una nuova provincia dalla Dashboard Admin (`https://admin.mappa-algeco.com/`), il nuovo sottodominio è operativo **immediatamente** senza dover aggiungere record DNS manuali.

### Opzione alternativa: Record espliciti per sottodominio

```bash
cloudflared tunnel route dns mappa-algeco admin.mappa-algeco.com
cloudflared tunnel route dns mappa-algeco torino.mappa-algeco.com
cloudflared tunnel route dns mappa-algeco pavia.mappa-algeco.com
cloudflared tunnel route dns mappa-algeco lecco.mappa-algeco.com
```

---

## 5. File di configurazione `/etc/cloudflared/config.yml`

Usa il template versionato in [`deploy/cloudflared-config.yml.example`](deploy/cloudflared-config.yml.example):

```bash
sudo mkdir -p /etc/cloudflared
sudo cp /opt/mappa-algeco/deploy/cloudflared-config.yml.example /etc/cloudflared/config.yml
```

Sostituisci `REPLACE_WITH_TUNNEL_UUID` con l'UUID reale del tunnel:

```yaml
tunnel: mappa-algeco
credentials-file: /root/.cloudflared/<TUNNEL_UUID>.json

ingress:
  - hostname: "*.mappa-algeco.com"
    service: http://localhost:5050

  - service: http_status:404
```

> **Perché tutte le province puntano a `localhost:5050`:** il reverse proxy [`router.py`](router.py) legge l'header `Host` e inoltra la richiesta al worker Gunicorn della provincia corrispondente (o alla dashboard `admin.*`). Non è necessario modificare `config.yml` quando si aggiungono province.

---

## 6. Servizio systemd `cloudflared-tunnel.service`

Test preliminare manuale:

```bash
cloudflared tunnel --config /etc/cloudflared/config.yml run mappa-algeco
```

Installazione come servizio persistente (`/etc/systemd/system/cloudflared-tunnel.service`):

```ini
[Unit]
Description=Cloudflare Tunnel - Mappa Algeco
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/cloudflared tunnel --config /etc/cloudflared/config.yml run mappa-algeco
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Abilita e avvia il servizio:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now cloudflared-tunnel.service
sudo systemctl status cloudflared-tunnel.service
```

Assicurati che il servizio [`mappa-algeco-router.service`](deploy/mappa-algeco-router.service.example) imposti `SESSION_COOKIE_DOMAIN=.mappa-algeco.com` e `FLASK_ENV=production`: in questo modo la sessione aperta su una provincia è valida anche su `admin.mappa-algeco.com` e i cookie hanno l'attributo `Secure`.

---

## 7. Endpoint pubblici risultanti

- **Torino:** `https://torino.mappa-algeco.com/`
- **Pavia:** `https://pavia.mappa-algeco.com/`
- **Lecco:** `https://lecco.mappa-algeco.com/`
- **Admin Multi-Provincia:** `https://admin.mappa-algeco.com/`

Su ciascun sottodominio provincia sono disponibili tutte le viste operative (`/`, `/filone`, `/distinte`, `/lettere`, `/mail`, `/comuni`, `/calendario`, `/appuntamenti`, `/dashboard`, `/report`, `/templates`).

---

## 8. Troubleshooting

```bash
# Verifica stato tunnel e connettori edge
cloudflared tunnel list
cloudflared tunnel info mappa-algeco

# Log in tempo reale del servizio
sudo journalctl -u cloudflared-tunnel -f

# Smoke test locale del router prima del tunnel
curl -s -H "Host: torino.mappa-algeco.com" http://127.0.0.1:5050/login | head -n 5
curl -s http://127.0.0.1:5050/_router/health
```

- **Errore `404 Sottodominio non riconosciuto`:** verifica che il campo `"sottodominio"` in [`data/provinces.json`](data/provinces.json) corrisponda all'hostname richiesto e che il record DNS su Cloudflare sia proxied.
- **Redirect a `/login` su `admin.*` anche dopo il login su una provincia:** verifica che `SESSION_COOKIE_DOMAIN=.mappa-algeco.com` sia impostato nell'ambiente di `mappa-algeco-router.service`.
