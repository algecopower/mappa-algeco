# 🐧 Guida Deploy su VPS Ubuntu (Aruba)

## Requisiti VPS
- **OS**: Ubuntu 22.04 LTS o 24.04 LTS
- **RAM**: minimo 2 GB (4 GB consigliati per OSRM)
- **Disco**: minimo 15 GB (OSRM Italia occupa ~5 GB processati)
- **CPU**: 2 vCPU minimo

---

## 🚀 Deploy rapido (automatico)

### 1. Connettiti alla VPS
```bash
ssh root@IL_TUO_IP_ARUBA
```

### 2. Carica il progetto sulla VPS
Dal tuo PC Windows, apri PowerShell:
```powershell
# Comprimi il progetto (escludi venv e cache pesanti)
Compress-Archive -Path "C:\Users\algec\Desktop\mappa_algeco_nuova\*" -DestinationPath "mappa_algeco.zip" -Force

# Carica sulla VPS
scp mappa_algeco.zip root@IL_TUO_IP_ARUBA:/tmp/
```

Oppure usa FileZilla/WinSCP per caricare la cartella.

### 3. Sulla VPS, estrai e lancia il deploy
```bash
# Estrai
mkdir -p /opt/mappa-algeco
cd /opt/mappa-algeco
apt install -y unzip
unzip /tmp/mappa_algeco.zip -d /opt/mappa-algeco/

# Lancia lo script di deploy
sudo bash deploy_ubuntu.sh
```

Lo script installa tutto automaticamente:
- Python 3 + virtual environment
- Docker + container OSRM (routing percorsi)
- Cloudflared
- Servizi systemd per avvio automatico

---

## ⚙️ Deploy manuale (passo-passo)

### 1. Aggiorna il sistema
```bash
sudo apt update && sudo apt upgrade -y
```

### 2. Installa Python e dipendenze
```bash
sudo apt install -y python3 python3-pip python3-venv git curl wget
```

### 3. Setup progetto
```bash
# Crea directory
sudo mkdir -p /opt/mappa-algeco
sudo chown $USER:$USER /opt/mappa-algeco

# Copia/clona il progetto in /opt/mappa-algeco

# Virtual environment
cd /opt/mappa-algeco
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 4. Installa Docker + OSRM
```bash
# Docker
curl -fsSL https://get.docker.com | sh
sudo systemctl enable docker
sudo systemctl start docker

# Dati OSRM (Italia)
sudo mkdir -p /opt/osrm-data
cd /opt/osrm-data
wget https://download.geofabrik.de/europe/italy-latest.osm.pbf

# Preprocessing (10-30 minuti)
docker run -t -v /opt/osrm-data:/data osrm/osrm-backend osrm-extract -p /opt/car.lua /data/italy-latest.osm.pbf
docker run -t -v /opt/osrm-data:/data osrm/osrm-backend osrm-partition /data/italy-latest.osrm
docker run -t -v /opt/osrm-data:/data osrm/osrm-backend osrm-customize /data/italy-latest.osrm

# Avvia OSRM
docker run -d --name osrm-server --restart=always \
    -p 5000:5000 \
    -v /opt/osrm-data:/data \
    osrm/osrm-backend osrm-routed --algorithm mld /data/italy-latest.osrm

# Verifica
curl "http://localhost:5000/route/v1/driving/7.68,45.07;7.78,45.19"
```

### 5. Installa e configura Cloudflared

```bash
# Installa
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o cloudflared.deb
sudo dpkg -i cloudflared.deb
rm cloudflared.deb

# Verifica
cloudflared --version

# Login (si aprirà un URL da visitare nel browser)
cloudflared tunnel login

# Crea tunnel
cloudflared tunnel create mappa-algeco

# Configura DNS (sottodomini)
cloudflared tunnel route dns mappa-algeco torino.mappa-algeco.com
cloudflared tunnel route dns mappa-algeco pavia.mappa-algeco.com
```

### 6. Configura il tunnel

Trova l'UUID del tunnel:
```bash
cloudflared tunnel list
```

Crea il file di configurazione:
```bash
sudo mkdir -p /etc/cloudflared
sudo nano /etc/cloudflared/config.yml
```

Contenuto:
```yaml
tunnel: mappa-algeco
credentials-file: /root/.cloudflared/XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX.json

ingress:
  - hostname: torino.mappa-algeco.com
    service: http://localhost:5001
  
  - hostname: pavia.mappa-algeco.com
    service: http://localhost:5002
  
  - service: http_status:404
```

> ⚠️ Sostituisci `XXXXXXXX-...` con l'UUID reale del tunnel.

Testa il tunnel:
```bash
cloudflared tunnel run mappa-algeco
```

### 7. Configura servizi systemd

Crea `/etc/systemd/system/mappa-algeco.service`:
```ini
[Unit]
Description=Mappa Algeco - Gestionale Contatori
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/mappa-algeco
Environment="PATH=/opt/mappa-algeco/venv/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=/opt/mappa-algeco/venv/bin/python /opt/mappa-algeco/server.py --pubblico --no-browser --skip-osrm-check
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Crea `/etc/systemd/system/cloudflared-tunnel.service`:
```ini
[Unit]
Description=Cloudflare Tunnel
After=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/cloudflared tunnel run mappa-algeco
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Attiva i servizi:
```bash
sudo systemctl daemon-reload
sudo systemctl enable mappa-algeco cloudflared-tunnel
sudo systemctl start mappa-algeco cloudflared-tunnel
```

---

## 📋 Comandi utili quotidiani

### Stato dei servizi
```bash
sudo systemctl status mappa-algeco
sudo systemctl status cloudflared-tunnel
```

### Log in tempo reale
```bash
# Log applicazione
sudo journalctl -u mappa-algeco -f

# Log tunnel
sudo journalctl -u cloudflared-tunnel -f

# Log ultimi 100 righe
sudo journalctl -u mappa-algeco -n 100
```

### Riavvia servizi
```bash
sudo systemctl restart mappa-algeco
sudo systemctl restart cloudflared-tunnel
```

### Aggiorna il progetto
```bash
cd /opt/mappa-algeco
# Carica i file aggiornati (scp, rsync, git pull, ecc.)
source venv/bin/activate
pip install -r requirements.txt  # Solo se cambiate dipendenze
sudo systemctl restart mappa-algeco
```

---

## 🔒 Sicurezza (consigliato)

### Firewall (UFW)
```bash
sudo ufw allow ssh
sudo ufw allow 80/tcp     # HTTP (Cloudflare)
sudo ufw allow 443/tcp    # HTTPS (Cloudflare)
# NON aprire 5001/5002 — Cloudflare tunnel gestisce tutto
sudo ufw enable
```

### Aggiornamenti automatici
```bash
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades
```

---

## 🐛 Troubleshooting

### "cloudflared: command not found"
```bash
# Reinstalla
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o cloudflared.deb
sudo dpkg -i cloudflared.deb
```

### OSRM non risponde
```bash
# Verifica container
docker ps -a | grep osrm
docker logs osrm-server

# Riavvia
docker restart osrm-server
```

### "Permission denied" su credenziali.json
```bash
sudo chown mappa:mappa /opt/mappa-algeco/credenziali.json
chmod 600 /opt/mappa-algeco/credenziali.json
```

### Il tunnel non si collega
```bash
# Verifica config
cloudflared tunnel list
cat /etc/cloudflared/config.yml

# Testa manualmente
cloudflared tunnel run mappa-algeco
```

### Memoria insufficiente (OSRM pesante)
```bash
# Aggiungi swap
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```
