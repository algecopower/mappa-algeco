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
# 4. Compila e installa OSRM nativo (senza Docker, meno RAM)
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
# STEP 4: Compila e installa OSRM nativo (senza Docker = meno RAM)
# ============================================================================
echo -e "\n${YELLOW}[4/5] Installazione OSRM nativo (Nord-Ovest Italia)...${NC}"

OSRM_FILE="${OSRM_REGION}-latest.osm.pbf"
OSRM_BASE="${OSRM_REGION}-latest"
OSRM_INSTALL_DIR="/usr/local"

if command -v osrm-routed &>/dev/null; then
    echo "   OSRM già installato: $(osrm-routed --version 2>&1 | head -1)"
else
    echo "   Installazione dipendenze di compilazione OSRM..."
    apt install -y build-essential git cmake pkg-config \
        libbz2-dev libxml2-dev libzip-dev libboost-all-dev \
        lua5.4 liblua5.4-dev libtbb-dev

    echo "   Clonazione e compilazione OSRM backend (può richiedere 5-10 minuti)..."
    OSRM_BUILD_DIR="/tmp/osrm-build"
    rm -rf "$OSRM_BUILD_DIR"
    git clone --branch v5.27.1 --single-branch https://github.com/Project-OSRM/osrm-backend.git "$OSRM_BUILD_DIR"
    cd "$OSRM_BUILD_DIR"
    mkdir build && cd build
    cmake .. -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$OSRM_INSTALL_DIR"
    make -j$(nproc)
    make install

    # Copia profili Lua necessari
    mkdir -p /usr/local/share/osrm/profiles
    cp -r "$OSRM_BUILD_DIR/profiles/" /usr/local/share/osrm/profiles/

    # Cleanup build
    rm -rf "$OSRM_BUILD_DIR"

    # Aggiorna librerie condivise
    ldconfig

    echo -e "${GREEN}   ✅ OSRM compilato e installato${NC}"
fi

# Prepara dati cartografici
if [ -f "${OSRM_DATA_DIR}/${OSRM_BASE}.osrm" ]; then
    echo "   Dati OSRM già preprocessati"
else
    echo "   Download dati cartografici Nord-Ovest Italia..."
    mkdir -p "$OSRM_DATA_DIR"
    cd "$OSRM_DATA_DIR"

    if [ ! -f "$OSRM_FILE" ]; then
        wget -q --show-progress "https://download.geofabrik.de/europe/italy/${OSRM_FILE}"
    fi

    echo "   Preprocessing dati OSRM (può richiedere 5-15 minuti)..."

    # Extract
    osrm-extract -p /usr/local/share/osrm/profiles/car.lua \
        "${OSRM_DATA_DIR}/${OSRM_FILE}" || true

    # Partition (MLD algorithm - meno RAM di CH)
    osrm-partition "${OSRM_DATA_DIR}/${OSRM_BASE}.osrm" || true

    # Customize
    osrm-customize "${OSRM_DATA_DIR}/${OSRM_BASE}.osrm" || true

    echo -e "${GREEN}   ✅ Dati OSRM Nord-Ovest preprocessati${NC}"
fi

# Fix permessi dati OSRM
chown -R ${APP_USER}:${APP_USER} "$OSRM_DATA_DIR"

cd "$APP_DIR"

# ============================================================================
# STEP 5: Crea servizi systemd
# ============================================================================
echo -e "\n${YELLOW}[5/5] Configurazione servizi systemd...${NC}"

# Servizio principale: mappa-algeco
cat > /etc/systemd/system/mappa-algeco.service << SYSTEMD_APP
[Unit]
Description=Mappa Algeco - Gestionale Contatori
After=network.target osrm-server.service
Requires=osrm-server.service

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

# Servizio OSRM nativo (senza Docker, usa --mmap per ridurre RAM)
cat > /etc/systemd/system/osrm-server.service << SYSTEMD_OSRM
[Unit]
Description=OSRM Routing Server (nativo, --mmap)
After=network.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
Environment="LD_LIBRARY_PATH=/usr/local/lib/osrm"
ExecStart=/usr/local/bin/osrm-routed --algorithm mld --mmap --port 5000 ${OSRM_DATA_DIR}/${OSRM_BASE}.osrm
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

# Limiti risorse
LimitNOFILE=65536

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
echo -e "⚡ OSRM:         nativo + --mmap (no Docker, RAM minima)"
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
