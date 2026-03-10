#!/bin/bash
# ============================================================================
# DEPLOY SU VPS UBUNTU - Mappa Algeco
# ============================================================================
# Uso: sudo bash deploy_ubuntu.sh
# 
# Questo script:
# 1. Installa Python 3, pip, venv
# 2. Crea utente di sistema
# 3. Setup progetto Python
# 4. Installa Docker + crea container OSRM (Nord-Ovest Italia)
# 5. Crea servizi systemd per avvio automatico
#
# Nota: cloudflared è gestito internamente dal server.py (--pubblico)
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
OSRM_DATA_DIR="/opt/osrm-data"
OSRM_REGION="nord-ovest"  # Solo Nord-Ovest Italia (Piemonte, Lombardia, ecc.)

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
echo -e "\n${YELLOW}[1/5] Aggiornamento sistema e dipendenze...${NC}"
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
echo -e "\n${YELLOW}[2/5] Configurazione utente di sistema...${NC}"
if id "$APP_USER" &>/dev/null; then
    echo "   Utente '$APP_USER' già esistente"
else
    useradd -r -m -s /bin/bash "$APP_USER"
    echo -e "${GREEN}   ✅ Utente '$APP_USER' creato${NC}"
fi

# ============================================================================
# STEP 3: Copia progetto e installa dipendenze Python
# ============================================================================
echo -e "\n${YELLOW}[3/5] Setup progetto Python...${NC}"

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
# STEP 4: Installa Docker + OSRM
# ============================================================================
echo -e "\n${YELLOW}[4/5] Installazione Docker e OSRM (Nord-Ovest Italia)...${NC}"

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

# Setup OSRM (routing engine) - dentro lo step 4
OSRM_FILE="${OSRM_REGION}-latest.osm.pbf"
OSRM_BASE="${OSRM_REGION}-latest"

if docker ps -a --format '{{.Names}}' | grep -q 'osrm-server'; then
    echo "   Container osrm-server già esistente"
    docker start osrm-server 2>/dev/null || true
else
    echo "   Download dati cartografici Nord-Ovest Italia..."
    mkdir -p "$OSRM_DATA_DIR"
    cd "$OSRM_DATA_DIR"
    
    if [ ! -f "$OSRM_FILE" ]; then
        wget -q --show-progress "https://download.geofabrik.de/europe/italy/${OSRM_FILE}"
    fi
    
    echo "   Preprocessing dati OSRM (può richiedere 5-15 minuti)..."
    
    # Extract
    docker run -t -v "$OSRM_DATA_DIR:/data" osrm/osrm-backend osrm-extract \
        -p /opt/car.lua "/data/${OSRM_FILE}" || true
    
    # Partition  
    docker run -t -v "$OSRM_DATA_DIR:/data" osrm/osrm-backend osrm-partition \
        "/data/${OSRM_BASE}.osrm" || true
    
    # Customize
    docker run -t -v "$OSRM_DATA_DIR:/data" osrm/osrm-backend osrm-customize \
        "/data/${OSRM_BASE}.osrm" || true
    
    # Crea container
    docker run -d --name osrm-server --restart=always \
        -p 5000:5000 \
        -v "$OSRM_DATA_DIR:/data" \
        osrm/osrm-backend osrm-routed --algorithm mld "/data/${OSRM_BASE}.osrm"
    
    echo -e "${GREEN}   ✅ OSRM Nord-Ovest configurato e avviato${NC}"
fi

cd "$APP_DIR"

# ============================================================================
# STEP 5: Crea servizi systemd
# ============================================================================
echo -e "\n${YELLOW}[5/5] Configurazione servizi systemd...${NC}"

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
ExecStart=${APP_DIR}/venv/bin/python ${APP_DIR}/server.py --pubblico --no-browser
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

# Nota: cloudflared NON ha un servizio systemd separato.
# È gestito internamente da server.py quando avviato con --pubblico.

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
echo -e "🗄️  OSRM data:    ${OSRM_DATA_DIR} (${OSRM_REGION})"
echo ""
echo -e "${YELLOW}PROSSIMI PASSI:${NC}"
echo ""
echo "1. Copia le credenziali Google (credenziali.json) in ${APP_DIR}/"
echo ""
echo "2. Avvia i servizi:"
echo "   sudo systemctl start osrm-server"
echo "   sudo systemctl start mappa-algeco"
echo ""
echo "3. Verifica stato:"
echo "   sudo systemctl status mappa-algeco"
echo "   sudo journalctl -u mappa-algeco -f   # Log in tempo reale"
echo ""
echo -e "${YELLOW}Nota: cloudflared è gestito internamente da server.py (--pubblico)${NC}"
echo ""
