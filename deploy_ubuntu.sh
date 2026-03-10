#!/bin/bash
# ============================================================================
# DEPLOY SU VPS UBUNTU - Mappa Algeco
# ============================================================================
# Uso: sudo bash deploy_ubuntu.sh
# 
# Questo script:
# 1. Installa Python 3, pip, venv
# 2. Installa Docker + crea container OSRM
# 3. Installa cloudflared
# 4. Configura il tunnel Cloudflare
# 5. Crea servizi systemd per avvio automatico
# ============================================================================

set -e  # Esci al primo errore

# Colori output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configurazione
APP_DIR="/opt/mappa-algeco"
APP_USER="mappa"
PYTHON_VERSION="python3"
TUNNEL_NAME="mappa-algeco"
DOMINIO_BASE="mappa-algeco.com"
OSRM_DATA_DIR="/opt/osrm-data"

echo -e "${BLUE}============================================${NC}"
echo -e "${BLUE}  DEPLOY MAPPA ALGECO - VPS Ubuntu${NC}"
echo -e "${BLUE}============================================${NC}"
echo ""

# Verifica root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Errore: esegui con sudo!${NC}"
    echo "  sudo bash deploy_ubuntu.sh"
    exit 1
fi

# ============================================================================
# STEP 1: Aggiornamento sistema e dipendenze base
# ============================================================================
echo -e "\n${YELLOW}[1/7] Aggiornamento sistema e dipendenze...${NC}"
apt update && apt upgrade -y
apt install -y \
    ${PYTHON_VERSION} \
    ${PYTHON_VERSION}-pip \
    ${PYTHON_VERSION}-venv \
    git \
    curl \
    wget \
    unzip \
    supervisor \
    apt-transport-https \
    ca-certificates \
    gnupg \
    lsb-release

echo -e "${GREEN}   ✅ Dipendenze base installate${NC}"

# ============================================================================
# STEP 2: Crea utente di sistema
# ============================================================================
echo -e "\n${YELLOW}[2/7] Configurazione utente di sistema...${NC}"
if id "$APP_USER" &>/dev/null; then
    echo "   Utente '$APP_USER' già esistente"
else
    useradd -r -m -s /bin/bash "$APP_USER"
    echo -e "${GREEN}   ✅ Utente '$APP_USER' creato${NC}"
fi

# ============================================================================
# STEP 3: Copia progetto e installa dipendenze Python
# ============================================================================
echo -e "\n${YELLOW}[3/7] Setup progetto Python...${NC}"

# Crea directory se non esiste
mkdir -p "$APP_DIR"

# Copia file (se siamo nella directory del progetto)
SCRIPT_SOURCE_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ "$SCRIPT_SOURCE_DIR" != "$APP_DIR" ]; then
    echo "   Copia file da $SCRIPT_SOURCE_DIR a $APP_DIR..."
    rsync -av --exclude='venv' --exclude='__pycache__' --exclude='.claude' \
        "$SCRIPT_SOURCE_DIR/" "$APP_DIR/"
fi

# Crea virtual environment
cd "$APP_DIR"
${PYTHON_VERSION} -m venv venv
source venv/bin/activate

# Installa dipendenze
pip install --upgrade pip
pip install -r requirements.txt

# Fix permessi
chown -R ${APP_USER}:${APP_USER} "$APP_DIR"

echo -e "${GREEN}   ✅ Progetto configurato in $APP_DIR${NC}"

# ============================================================================
# STEP 4: Installa Docker
# ============================================================================
echo -e "\n${YELLOW}[4/7] Installazione Docker...${NC}"

if command -v docker &>/dev/null; then
    echo "   Docker già installato: $(docker --version)"
else
    # Aggiungi repository Docker
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
        https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | \
        tee /etc/apt/sources.list.d/docker.list > /dev/null
    
    apt update
    apt install -y docker-ce docker-ce-cli containerd.io
    
    # Aggiungi utente al gruppo docker
    usermod -aG docker "$APP_USER"
    
    # Abilita Docker all'avvio
    systemctl enable docker
    systemctl start docker
    
    echo -e "${GREEN}   ✅ Docker installato e avviato${NC}"
fi

# ============================================================================
# STEP 5: Setup OSRM (routing engine)
# ============================================================================
echo -e "\n${YELLOW}[5/7] Setup OSRM (calcolo percorsi)...${NC}"

if docker ps -a --format '{{.Names}}' | grep -q 'osrm-server'; then
    echo "   Container osrm-server già esistente"
    docker start osrm-server 2>/dev/null || true
else
    echo "   Download dati cartografici Italia..."
    mkdir -p "$OSRM_DATA_DIR"
    cd "$OSRM_DATA_DIR"
    
    if [ ! -f "italy-latest.osm.pbf" ]; then
        wget -q --show-progress https://download.geofabrik.de/europe/italy-latest.osm.pbf
    fi
    
    echo "   Preprocessing dati OSRM (può richiedere 10-30 minuti)..."
    
    # Extract
    docker run -t -v "$OSRM_DATA_DIR:/data" osrm/osrm-backend osrm-extract \
        -p /opt/car.lua /data/italy-latest.osm.pbf || true
    
    # Partition  
    docker run -t -v "$OSRM_DATA_DIR:/data" osrm/osrm-backend osrm-partition \
        /data/italy-latest.osrm || true
    
    # Customize
    docker run -t -v "$OSRM_DATA_DIR:/data" osrm/osrm-backend osrm-customize \
        /data/italy-latest.osrm || true
    
    # Crea container
    docker run -d --name osrm-server --restart=always \
        -p 5000:5000 \
        -v "$OSRM_DATA_DIR:/data" \
        osrm/osrm-backend osrm-routed --algorithm mld /data/italy-latest.osrm
    
    echo -e "${GREEN}   ✅ OSRM configurato e avviato${NC}"
fi

cd "$APP_DIR"

# ============================================================================
# STEP 6: Installa e configura Cloudflared
# ============================================================================
echo -e "\n${YELLOW}[6/7] Installazione Cloudflared...${NC}"

if command -v cloudflared &>/dev/null; then
    echo "   cloudflared già installato: $(cloudflared --version)"
else
    # Installa cloudflared per Ubuntu/Debian
    curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb \
        -o /tmp/cloudflared.deb
    dpkg -i /tmp/cloudflared.deb
    rm /tmp/cloudflared.deb
    echo -e "${GREEN}   ✅ cloudflared installato${NC}"
fi

echo ""
echo -e "${YELLOW}   ⚠️  CONFIGURAZIONE TUNNEL MANUALE RICHIESTA:${NC}"
echo ""
echo "   Se non hai ancora configurato il tunnel, esegui questi comandi:"
echo ""
echo -e "   ${BLUE}1. Login a Cloudflare:${NC}"
echo "      cloudflared tunnel login"
echo ""
echo -e "   ${BLUE}2. Crea tunnel (se non esiste):${NC}"
echo "      cloudflared tunnel create ${TUNNEL_NAME}"
echo ""
echo -e "   ${BLUE}3. Configura DNS:${NC}"
echo "      cloudflared tunnel route dns ${TUNNEL_NAME} torino.${DOMINIO_BASE}"
echo "      cloudflared tunnel route dns ${TUNNEL_NAME} pavia.${DOMINIO_BASE}"
echo ""
echo -e "   ${BLUE}4. Crea config (/etc/cloudflared/config.yml):${NC}"
echo "      Vedi il file di esempio generato sotto."
echo ""

# Crea file config di esempio
mkdir -p /etc/cloudflared
if [ ! -f /etc/cloudflared/config.yml ]; then
    cat > /etc/cloudflared/config.yml.example << 'CLOUDFLARE_CONFIG'
# Configurazione Cloudflare Tunnel per Mappa Algeco
# Rinomina questo file in config.yml dopo averlo configurato
# Sostituisci TUNNEL_UUID con l'UUID del tuo tunnel (cloudflared tunnel list)

tunnel: mappa-algeco
# credentials-file: /root/.cloudflared/TUNNEL_UUID.json
# Oppure se usi l'utente mappa:
# credentials-file: /home/mappa/.cloudflared/TUNNEL_UUID.json

ingress:
  # Torino (porta 5001)
  - hostname: torino.mappa-algeco.com
    service: http://localhost:5001
  
  # Pavia (porta 5002)
  - hostname: pavia.mappa-algeco.com
    service: http://localhost:5002
  
  # Catch-all (obbligatorio)
  - service: http_status:404
CLOUDFLARE_CONFIG
    echo -e "${GREEN}   ✅ Config esempio creato: /etc/cloudflared/config.yml.example${NC}"
fi

# ============================================================================
# STEP 7: Crea servizi systemd
# ============================================================================
echo -e "\n${YELLOW}[7/7] Configurazione servizi systemd...${NC}"

# Servizio principale: mappa-algeco
cat > /etc/systemd/system/mappa-algeco.service << SYSTEMD_APP
[Unit]
Description=Mappa Algeco - Gestionale Contatori
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}
Environment="PATH=${APP_DIR}/venv/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=${APP_DIR}/venv/bin/python ${APP_DIR}/server.py --pubblico --no-browser --skip-osrm-check
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

# Limiti risorse
LimitNOFILE=65536
MemoryMax=2G

[Install]
WantedBy=multi-user.target
SYSTEMD_APP

# Servizio cloudflared (tunnel)
cat > /etc/systemd/system/cloudflared-tunnel.service << SYSTEMD_TUNNEL
[Unit]
Description=Cloudflare Tunnel - Mappa Algeco
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/cloudflared tunnel run ${TUNNEL_NAME}
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SYSTEMD_TUNNEL

# Servizio OSRM (assicura che il container Docker sia attivo)
cat > /etc/systemd/system/osrm-server.service << SYSTEMD_OSRM
[Unit]
Description=OSRM Routing Server (Docker)
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/docker start osrm-server
ExecStop=/usr/bin/docker stop osrm-server

[Install]
WantedBy=multi-user.target
SYSTEMD_OSRM

# Reload systemd
systemctl daemon-reload

# Abilita servizi
systemctl enable osrm-server.service
systemctl enable cloudflared-tunnel.service
systemctl enable mappa-algeco.service

echo -e "${GREEN}   ✅ Servizi systemd configurati${NC}"

# ============================================================================
# RIEPILOGO FINALE
# ============================================================================
echo ""
echo -e "${BLUE}============================================${NC}"
echo -e "${GREEN}  ✅ DEPLOY COMPLETATO!${NC}"
echo -e "${BLUE}============================================${NC}"
echo ""
echo -e "📁 Progetto:     ${APP_DIR}"
echo -e "👤 Utente:       ${APP_USER}"
echo -e "🐍 Python venv:  ${APP_DIR}/venv"
echo -e "🗄️  OSRM data:    ${OSRM_DATA_DIR}"
echo ""
echo -e "${YELLOW}PROSSIMI PASSI:${NC}"
echo ""
echo "1. Configura il tunnel Cloudflare (se non fatto):"
echo "   cloudflared tunnel login"
echo "   cloudflared tunnel create ${TUNNEL_NAME}"
echo "   cloudflared tunnel route dns ${TUNNEL_NAME} torino.${DOMINIO_BASE}"
echo "   cloudflared tunnel route dns ${TUNNEL_NAME} pavia.${DOMINIO_BASE}"
echo ""
echo "2. Configura /etc/cloudflared/config.yml"
echo "   (copia da config.yml.example e inserisci UUID tunnel)"
echo ""
echo "3. Copia le credenziali Google (credenziali.json) in ${APP_DIR}/"
echo ""
echo "4. Avvia i servizi:"
echo "   sudo systemctl start osrm-server"
echo "   sudo systemctl start cloudflared-tunnel"
echo "   sudo systemctl start mappa-algeco"
echo ""
echo "5. Verifica stato:"
echo "   sudo systemctl status mappa-algeco"
echo "   sudo systemctl status cloudflared-tunnel"
echo "   sudo journalctl -u mappa-algeco -f   # Log in tempo reale"
echo ""
echo -e "${GREEN}URL finali:${NC}"
echo "   https://torino.${DOMINIO_BASE}/"
echo "   https://pavia.${DOMINIO_BASE}/"
echo ""
