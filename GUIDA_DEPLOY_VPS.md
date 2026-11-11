# 🐧 Guida Deploy su VPS Ubuntu (Architettura Multi-Tenant)

Guida all'installazione e gestione in produzione su Ubuntu 22.04 / 24.04 LTS basata su `router.py`, Gunicorn (`wsgi:app`), OSRM nativo, Cloudflare Tunnel e `state.db` SQLite locale per provincia.

Per la topologia dettagliata del router e dei file `deploy/*.example`, consulta anche [`deploy/README.md`](deploy/README.md).

---

## Requisiti hardware consigliati

- **OS:** Ubuntu 22.04 LTS o 24.04 LTS (x86_64)
- **RAM:** 2 GB minimi (4 GB consigliati per OSRM Nord-Ovest + più worker provinciali Gunicorn)
- **Disco:** almeno 15 GB liberi (grafo OSRM Nord-Ovest + dataset + backup DB)
- **CPU:** 2 vCPU minime

---

## 1. Setup sistema, repository e Python venv

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv git curl wget jq

# Clona il repository in /opt/mappa-algeco
sudo mkdir -p /opt/mappa-algeco
cd /opt/mappa-algeco
git clone git@github.com:algecopower/mappa-algeco.git .

# Ambiente virtuale e dipendenze
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

> Nota: `requirements.txt` include già `gunicorn`, `argon2-cffi`, `flask`, `pandas`, `openpyxl`, `pdfplumber`, `scikit-learn`, `requests`, `xlrd`.

---

## 2. Compilazione e avvio OSRM nativo (`--mmap`, senza Docker)

OSRM nativo con `--algorithm mld --mmap` riduce l'uso di RAM condividendo il grafo stradale via memory mapping:

```bash
# Dipendenze di compilazione
sudo apt install -y build-essential cmake pkg-config \
    libbz2-dev libxml2-dev libzip-dev libboost-all-dev \
    lua5.4 liblua5.4-dev libtbb-dev

# Compilazione OSRM v5.27.1
git clone --branch v5.27.1 --single-branch https://github.com/Project-OSRM/osrm-backend.git /tmp/osrm-build
cd /tmp/osrm-build && mkdir build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Release
make -j"$(nproc)"
sudo make install
sudo mkdir -p /usr/local/share/osrm/profiles
sudo cp -r /tmp/osrm-build/profiles/* /usr/local/share/osrm/profiles/
sudo ldconfig
rm -rf /tmp/osrm-build

# Scarica e processa estratto Nord-Ovest Italia
sudo mkdir -p /opt/osrm-data
cd /opt/osrm-data
sudo wget https://download.geofabrik.de/europe/italy/nord-ovest-latest.osm.pbf
sudo osrm-extract -p /usr/local/share/osrm/profiles/car.lua nord-ovest-latest.osm.pbf
sudo osrm-partition nord-ovest-latest.osrm
sudo osrm-customize nord-ovest-latest.osrm
```

Crea `/etc/systemd/system/osrm-server.service`:

```ini
[Unit]
Description=OSRM Routing Engine (Mappa Algeco)
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/osrm-data
ExecStart=/usr/local/bin/osrm-routed --algorithm mld --mmap --port 5000 /opt/osrm-data/nord-ovest-latest.osrm
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Abilita e verifica OSRM:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now osrm-server.service
curl "http://localhost:5000/route/v1/driving/7.68,45.07;7.78,45.19"
```

---

## 3. Configurazione Cloudflare Tunnel

Installa `cloudflared` e configura il tunnel seguendo [`GUIDA_CLOUDFLARE.md`](GUIDA_CLOUDFLARE.md) usando [`deploy/cloudflared-config.yml.example`](deploy/cloudflared-config.yml.example) in `/etc/cloudflared/config.yml` (wildcard `*.mappa-algeco.com` → `http://localhost:5050`), poi attiva `cloudflared-tunnel.service`.

---

## 4. Installazione `mappa-algeco-router.service`

Copia il service unit d'esempio da [`deploy/mappa-algeco-router.service.example`](deploy/mappa-algeco-router.service.example):

```bash
sudo cp /opt/mappa-algeco/deploy/mappa-algeco-router.service.example /etc/systemd/system/mappa-algeco-router.service
sudo systemctl daemon-reload
sudo systemctl enable --now mappa-algeco-router.service
```

Cosa avvia questo servizio:
- `router.py` in ascolto su `127.0.0.1:5050` (`ROUTER_HOST` / `ROUTER_PORT`).
- Uno worker **Gunicorn** (`gunicorn -c gunicorn.conf.py wsgi:app` — `1` worker, `32` thread `gthread`, `max_requests=1000`) per ciascuna provincia dichiarata in [`data/provinces.json`](data/provinces.json):
  - `TORINO` → `:5001`
  - `PAVIA` → `:5002`
  - `LECCO` → `:5003`
  - Nuove province aggiunte via `admin.*` → `:500N`
- Eredita `SESSION_COOKIE_DOMAIN=.mappa-algeco.com` e `FLASK_ENV=production` per la sessione unica cross-sottodominio e i cookie `Secure`.

---

## 5. Creazione del primo account Amministratore e import iniziale dati

Su una nuova installazione `users.json` non è tracciato su Git: crea il primo utente admin prima di accedere da browser:

```bash
cd /opt/mappa-algeco
source venv/bin/activate

# 1. Crea utente
python gestione_utenti.py --crea admin.ops

# 2. Promuovilo a ruolo admin
python -c "from moduli.auth import set_user_role; print(set_user_role('admin.ops', 'admin'))"
```

Successivamente:
1. Effettua il login su una provincia (es. `https://torino.mappa-algeco.com/`).
2. Apri la dashboard amministrazione `https://admin.mappa-algeco.com/`.
3. Per ogni provincia, importa il primo Excel (`📥 Importa Excel`), usa `⚡ Da Sheets` se è configurato `google_sheet_url`, oppure avvia `🪄 Configura (wizard)`.

---

## 6. Automazioni systemd e cron di manutenzione

### Restart preventivo notturno (opzionale ma consigliato)

Libera la RAM accumulata dai thread Gunicorn/pandas ogni notte alle `04:30`:

```bash
sudo cp /opt/mappa-algeco/deploy/mappa-algeco-restart.service.example /etc/systemd/system/mappa-algeco-restart.service
sudo cp /opt/mappa-algeco/deploy/mappa-algeco-restart.timer.example /etc/systemd/system/mappa-algeco-restart.timer
sudo systemctl daemon-reload
sudo systemctl enable --now mappa-algeco-restart.timer
```

### Auto-import orario read-only da Google Sheets

Scarica l'export XLSX pubblico solo se l'hash SHA-256 è cambiato ed applica il merge protetto:

```bash
sudo cp /opt/mappa-algeco/deploy/mappa-algeco-auto-import /etc/cron.d/mappa-algeco-auto-import
sudo chmod 644 /etc/cron.d/mappa-algeco-auto-import
```

### Manutenzione mensile DB (`audit_log` e `pending_writes`)

Puoi schedulare in `/etc/cron.d/mappa-algeco-maintenance` la rotazione dell'audit (>180 giorni) e la pulizia delle pending `done` (>30 giorni) per ogni provincia:

```cron
0 4 1 * * root cd /opt/mappa-algeco && PROVINCIA=TORINO /opt/mappa-algeco/venv/bin/python tools/archive_audit_log.py --apply >> /var/log/mappa-audit-archive-torino.log 2>&1
15 4 1 * * root cd /opt/mappa-algeco && PROVINCIA=PAVIA /opt/mappa-algeco/venv/bin/python tools/archive_audit_log.py --apply >> /var/log/mappa-audit-archive-pavia.log 2>&1
0 5 1 * * root cd /opt/mappa-algeco && PROVINCIA=TORINO /opt/mappa-algeco/venv/bin/python tools/cleanup_done_pendings.py --apply >> /var/log/mappa-cleanup-pendings-torino.log 2>&1
15 5 1 * * root cd /opt/mappa-algeco && PROVINCIA=PAVIA /opt/mappa-algeco/venv/bin/python tools/cleanup_done_pendings.py --apply >> /var/log/mappa-cleanup-pendings-pavia.log 2>&1
```

---

## 7. Operazioni quotidiane e aggiornamento codice

```bash
# Stato di tutto lo stack
sudo systemctl status osrm-server cloudflared-tunnel mappa-algeco-router mappa-algeco-restart.timer

# Log in tempo reale (router + worker figli su journald)
sudo journalctl -u mappa-algeco-router -f

# Health interna router + stato worker
curl -s http://127.0.0.1:5050/_router/health | jq

# Aggiornamento codice da GitHub
cd /opt/mappa-algeco
git pull origin main
/opt/mappa-algeco/venv/bin/pip install -r requirements.txt  # se cambiate dipendenze
sudo systemctl restart mappa-algeco-router
```

> **Nota storica:** in repository sono ancora presenti [`deploy_ubuntu.sh`](deploy_ubuntu.sh) e l'avvio standalone [`server.py`](server.py) `--torino`/`--pavia` (per sviluppo locale o vecchi setup mono-istanza). Per la produzione multi-provincia usa esclusivamente `mappa-algeco-router.service`. Il vecchio file `credenziali.json` non è più usato dopo la disconnessione delle API gspread.

---

## 8. Sicurezza di base (UFW)

Il router e tutti i worker provinciali restano in ascolto solo su `127.0.0.1`; il traffico dall'esterno passa interamente attraverso Cloudflare Tunnel (`cloudflared-tunnel.service`):

```bash
sudo ufw allow ssh
sudo ufw enable
# NON aprire le porte 5000, 5001, 5002, 5003 o 5050 verso Internet
```
