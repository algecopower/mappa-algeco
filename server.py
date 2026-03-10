# -*- coding: utf-8 -*-
"""
Gestionale Mappa Contatori - Entry Point Unificato.

Server Flask + Sync automatico dei dati.
Può avviare una o più province, con supporto Cloudflare per accesso remoto.

Uso:
  python server.py               # Avvia ENTRAMBE le province (Torino + Pavia)
  python server.py --torino      # Solo Torino
  python server.py --pavia       # Solo Pavia
  python server.py --pubblico    # Entrambe + Cloudflare Tunnel (accesso Internet)
  python server.py --skip-osrm-check  # Salta verifica OSRM
"""
import os
import sys
import argparse
import subprocess
import signal
import time
import webbrowser
import urllib.request
import urllib.error
import shutil
import glob
import platform

IS_WINDOWS = platform.system() == 'Windows'
IS_LINUX = platform.system() == 'Linux'

# Configura encoding UTF-8
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except (AttributeError, Exception):
    pass  # Linux default UTF-8, Python < 3.7 non ha reconfigure

# Directory dello script
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(SCRIPT_DIR)

# URL server OSRM locale
OSRM_SERVER = "http://localhost:5000"

# File HTML delle mappe
MAPPA_TORINO = os.path.join(SCRIPT_DIR, 'templates', 'mappa_torino.html')
MAPPA_PAVIA = os.path.join(SCRIPT_DIR, 'templates', 'mappa_pavia.html')

# ============================================================================
# CONFIGURAZIONE CLOUDFLARE (per --pubblico)
# ============================================================================
TUNNEL_NAME = "mappa-algeco"
DOMINIO_BASE = "mappa-algeco.com"
SOTTODOMINI = {
    'TORINO': f"torino.{DOMINIO_BASE}",
    'PAVIA': f"pavia.{DOMINIO_BASE}",
}
# ============================================================================

# ============================================================================
# PARSING ARGOMENTI - PRIMA DI TUTTO
# ============================================================================
parser = argparse.ArgumentParser(description='Gestionale Mappa Contatori')
parser.add_argument('--torino', '-t', action='store_true', help='Avvia solo Torino')
parser.add_argument('--pavia', '-p', action='store_true', help='Avvia solo Pavia')
parser.add_argument('--pubblico', action='store_true', help='Avvia entrambe + Cloudflare Tunnel (accesso Internet)')
parser.add_argument('--skip-osrm-check', action='store_true', help='Salta la verifica OSRM')
parser.add_argument('--no-browser', action='store_true', help='Non aprire il browser automaticamente')
args = parser.parse_args()

# ============================================================================
# MODALITÀ LAUNCHER vs FLASK
# ============================================================================
# Se _SERVER_CHILD è impostato, siamo un processo figlio Flask
# Altrimenti siamo il launcher principale
IS_CHILD_PROCESS = os.environ.get('_SERVER_CHILD') == '1'

# Se siamo in launcher mode: senza argomenti = entrambe le province
# Se --torino o --pavia specifici = singola provincia
# Il child process riceve la provincia via variabile ambiente
if not IS_CHILD_PROCESS:
    # ========================================================================
    # MODALITÀ LAUNCHER (processo principale)
    # ========================================================================
    
    # Processi gestiti
    processi_flask = []
    processo_tunnel = None
    
    def trova_cloudflared():
        """Trova cloudflared. Ritorna il percorso o None. Cross-platform."""
        path = shutil.which('cloudflared')
        if path:
            return path
        
        if IS_WINDOWS:
            # Percorsi tipici Windows
            percorsi = [
                r"C:\Program Files\cloudflared\cloudflared.exe",
                r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
                os.path.expanduser(os.path.join('~', 'cloudflared', 'cloudflared.exe')),
            ]
            for p in percorsi:
                if os.path.exists(p):
                    return p
            
            # Cerca anche in WinGet
            winget_path = os.path.join(
                os.environ.get('LOCALAPPDATA', ''),
                'Microsoft', 'WinGet', 'Packages'
            )
            if os.path.exists(winget_path):
                pattern = os.path.join(winget_path, '*cloudflared*', 'cloudflared.exe')
                matches = glob.glob(pattern)
                if matches:
                    return matches[0]
        else:
            # Percorsi tipici Linux
            percorsi = [
                '/usr/local/bin/cloudflared',
                '/usr/bin/cloudflared',
                os.path.expanduser('~/cloudflared'),
                os.path.expanduser('~/.cloudflared/bin/cloudflared'),
            ]
            for p in percorsi:
                if os.path.exists(p):
                    return p
        return None
    
    CLOUDFLARED_PATH = None
    
    def verifica_cloudflared():
        global CLOUDFLARED_PATH
        CLOUDFLARED_PATH = trova_cloudflared()
        if CLOUDFLARED_PATH:
            print(f"   ✓ cloudflared: {CLOUDFLARED_PATH}")
            return True
        print("\n" + "="*60)
        print("⚠️  CLOUDFLARED NON TROVATO")
        print("="*60)
        if IS_WINDOWS:
            print("\nInstalla con: winget install Cloudflare.cloudflared")
        else:
            print("\nInstalla con:")
            print("   curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o cloudflared.deb")
            print("   sudo dpkg -i cloudflared.deb")
        print("Oppure scarica da: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/")
        print("="*60)
        return False
    
    def verifica_tunnel_configurato():
        if not CLOUDFLARED_PATH:
            return False
        try:
            result = subprocess.run(
                [CLOUDFLARED_PATH, 'tunnel', 'list'],
                capture_output=True, text=True, timeout=10
            )
            if TUNNEL_NAME in result.stdout:
                return True
            print("\n" + "="*60)
            print(f"⚠️  TUNNEL '{TUNNEL_NAME}' NON TROVATO")
            print("="*60)
            print(f'\nEsegui:\n   "{CLOUDFLARED_PATH}" tunnel login')
            print(f'   "{CLOUDFLARED_PATH}" tunnel create {TUNNEL_NAME}')
            print("\nVedi GUIDA_CLOUDFLARE.md per dettagli.")
            print("="*60)
            return False
        except Exception as e:
            print(f"⚠️ Errore verifica tunnel: {e}")
            return False
    
    def verifica_osrm():
        test_url = f"{OSRM_SERVER}/route/v1/driving/7.6869,45.0704;7.7888,45.1964"
        try:
            with urllib.request.urlopen(test_url, timeout=5) as r:
                return r.status == 200
        except Exception:
            return False
    
    def avvia_osrm_automaticamente():
        """Tenta di avviare Docker e il container osrm-server automaticamente. Cross-platform."""
        CONTAINER_NAME = "osrm-server"
        
        # 1. Verifica se Docker è disponibile
        docker_cmd = shutil.which("docker")
        if not docker_cmd:
            print("   ❌ Docker non trovato nel PATH")
            return False
        
        # 2. Verifica se Docker daemon è in esecuzione
        try:
            result = subprocess.run(
                ["docker", "info"], capture_output=True, timeout=10
            )
            docker_running = result.returncode == 0
        except Exception:
            docker_running = False
        
        if not docker_running:
            if IS_LINUX:
                # Linux: prova ad avviare Docker via systemd
                print("   🐳 Docker non in esecuzione, tentativo avvio via systemd...")
                try:
                    subprocess.run(["sudo", "systemctl", "start", "docker"], timeout=15)
                    time.sleep(3)
                    check = subprocess.run(["docker", "info"], capture_output=True, timeout=10)
                    if check.returncode == 0:
                        print("   ✅ Docker avviato via systemd")
                        docker_running = True
                    else:
                        print("   ❌ Docker non si è avviato")
                except Exception as e:
                    print(f"   ❌ Errore avvio Docker: {e}")
            else:
                # Windows: prova ad avviare Docker Desktop
                print("   🐳 Docker Desktop non in esecuzione, tentativo di avvio...")
                docker_desktop = os.path.join(
                    os.environ.get("ProgramFiles", "C:\\Program Files"),
                    "Docker", "Docker", "Docker Desktop.exe"
                )
                if not os.path.exists(docker_desktop):
                    docker_desktop = shutil.which("Docker Desktop")
                
                if docker_desktop and os.path.exists(docker_desktop):
                    try:
                        subprocess.Popen([docker_desktop], cwd=SCRIPT_DIR)
                        print("   ⏳ Attesa avvio Docker Desktop...")
                        for i in range(60):
                            time.sleep(2)
                            try:
                                check = subprocess.run(
                                    ["docker", "info"], capture_output=True, timeout=5
                                )
                                if check.returncode == 0:
                                    print(f"   ✅ Docker Desktop avviato ({(i+1)*2}s)")
                                    docker_running = True
                                    break
                            except Exception:
                                pass
                            if i > 0 and i % 10 == 0:
                                print(f"      ({(i+1)*2}s...)")
                    except Exception as e:
                        print(f"   ❌ Errore avvio Docker Desktop: {e}")
                else:
                    print("   ❌ Docker Desktop non trovato, avvialo manualmente")
            
            if not docker_running:
                print("   ❌ Docker non si è avviato in tempo")
                return False
        
        # 3. Verifica se il container esiste
        try:
            result = subprocess.run(
                ["docker", "inspect", CONTAINER_NAME],
                capture_output=True, timeout=10
            )
            container_exists = result.returncode == 0
        except Exception:
            container_exists = False
        
        if not container_exists:
            print(f"   ❌ Container '{CONTAINER_NAME}' non trovato")
            print("      Crealo prima seguendo la guida in DOCUMENTAZIONE.md")
            return False
        
        # 4. Verifica stato container
        try:
            result = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER_NAME],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0 and "true" in result.stdout.lower():
                # Container già in esecuzione, ma OSRM potrebbe essere ancora in avvio
                print(f"   🔄 Container '{CONTAINER_NAME}' già in esecuzione, attesa OSRM...")
                for i in range(30):
                    if verifica_osrm():
                        print("   ✅ OSRM pronto!")
                        return True
                    time.sleep(1)
                print("   ❌ OSRM non risponde nonostante il container sia attivo")
                return False
        except Exception:
            pass
        
        # 5. Avvia il container
        print(f"   🚀 Avvio container '{CONTAINER_NAME}'...")
        try:
            result = subprocess.run(
                ["docker", "start", CONTAINER_NAME],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode != 0:
                print(f"   ❌ Errore avvio container: {result.stderr.strip()}")
                return False
        except Exception as e:
            print(f"   ❌ Errore avvio container: {e}")
            return False
        
        # 6. Attendi che OSRM sia pronto (max 30s)
        print("   ⏳ Attesa che OSRM sia pronto...")
        for i in range(30):
            if verifica_osrm():
                print(f"   ✅ OSRM avviato e pronto! ({i+1}s)")
                return True
            time.sleep(1)
            if i > 0 and i % 10 == 0:
                print(f"      ({i+1}s...)")
        
        print("   ❌ OSRM non ha risposto in tempo")
        return False
    
    def pulisci_mappe_precedenti():
        eliminati = []
        for mappa in [MAPPA_TORINO, MAPPA_PAVIA]:
            if os.path.exists(mappa):
                try:
                    os.remove(mappa)
                    eliminati.append(os.path.basename(mappa))
                except Exception as e:
                    print(f"⚠️ Impossibile eliminare {mappa}: {e}")
        if eliminati:
            print(f"   🗑️ Mappe eliminate: {', '.join(eliminati)}")
    
    def avvia_server_flask(provincia, porta):
        env = os.environ.copy()
        env['PROVINCIA'] = provincia.upper()
        env['FLASK_PORT'] = str(porta)
        env['_SERVER_CHILD'] = '1'
        
        proc = subprocess.Popen(
            [sys.executable, __file__, '--skip-osrm-check'],
            env=env, cwd=SCRIPT_DIR,
        )
        print(f"   ✅ {provincia} avviato su http://localhost:{porta}")
        return proc
    
    def avvia_tunnel():
        global processo_tunnel
        if not CLOUDFLARED_PATH:
            return False
        print("\n🌐 Avvio Cloudflare Tunnel...")
        processo_tunnel = subprocess.Popen(
            [CLOUDFLARED_PATH, 'tunnel', 'run', TUNNEL_NAME],
            cwd=SCRIPT_DIR,
        )
        time.sleep(3)
        if processo_tunnel.poll() is None:
            print("   ✅ Tunnel attivo")
            return True
        print("   ❌ Tunnel terminato inaspettatamente")
        return False
    
    def aspetta_server(porta, timeout=30):
        url = f"http://localhost:{porta}/api/config"
        for _ in range(timeout * 2):
            try:
                with urllib.request.urlopen(url, timeout=1) as r:
                    if r.status == 200:
                        return True
            except Exception:
                pass
            time.sleep(0.5)
        return False
    
    def aspetta_mappa(provincia, timeout=120):
        mappa_file = os.path.join(SCRIPT_DIR, 'templates', f'mappa_{provincia.lower()}.html')
        print(f"   ⏳ Attesa mappa {provincia}...")
        for i in range(timeout):
            if os.path.exists(mappa_file) and os.path.getsize(mappa_file) > 1000:
                print(f"   ✅ Mappa {provincia} pronta!")
                return True
            time.sleep(1)
            if i > 0 and i % 20 == 0:
                print(f"      ({i}s)")
        return False
    
    def termina_tutti():
        global processi_flask, processo_tunnel
        print("\n🛑 Terminazione...")
        if processo_tunnel and processo_tunnel.poll() is None:
            processo_tunnel.terminate()
            try:
                processo_tunnel.wait(timeout=5)
            except Exception:
                processo_tunnel.kill()
        for proc in processi_flask:
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except Exception:
                    proc.kill()
        print("✅ Arrestato")
    
    def signal_handler(signum, frame):
        termina_tutti()
        sys.exit(0)
    
    def launcher_main():
        global processi_flask
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # Determina province
        if args.torino and not args.pavia:
            province = [('TORINO', 5001)]
        elif args.pavia and not args.torino:
            province = [('PAVIA', 5002)]
        else:
            province = [('TORINO', 5001), ('PAVIA', 5002)]
        
        titolo = "🌐 SERVER PUBBLICO" if args.pubblico else "GESTIONALE MAPPA CONTATORI"
        print("\n" + "="*60)
        print(f"  {titolo}")
        print("="*60)
        print(f"\n📍 Province: {', '.join(p[0] for p in province)}")
        
        # Verifica Cloudflare se --pubblico
        if args.pubblico:
            print("\n🔍 Verifica Cloudflare...")
            if not verifica_cloudflared():
                sys.exit(1)
            if not verifica_tunnel_configurato():
                sys.exit(1)
        
        # Pulisci mappe
        print("\n📄 Preparazione mappe...")
        pulisci_mappe_precedenti()
        
        # Verifica OSRM
        if not args.skip_osrm_check:
            print("\n🔍 Verifica OSRM...")
            if not verifica_osrm():
                print("⚠️ OSRM non raggiungibile, tentativo avvio automatico...")
                if avvia_osrm_automaticamente():
                    print("   ✅ OSRM avviato automaticamente")
                else:
                    print("\n⚠️ Impossibile avviare OSRM automaticamente")
                    print("   Docker e container OSRM in esecuzione?")
                    risposta = input("   Continuare senza OSRM? [s/N]: ").strip().lower()
                    if risposta != 's':
                        sys.exit(1)
            else:
                print("   ✅ OSRM raggiungibile")
        
        # Avvia Flask
        print("\n📦 Avvio server Flask...")
        for provincia, porta in province:
            proc = avvia_server_flask(provincia, porta)
            processi_flask.append(proc)
            time.sleep(1)
        
        # Aspetta mappe
        print("\n⏳ Generazione mappe...")
        for provincia, porta in province:
            aspetta_server(porta)
            aspetta_mappa(provincia)
        
        # Avvia tunnel se pubblico
        if args.pubblico:
            if not avvia_tunnel():
                termina_tutti()
                sys.exit(1)
        
        # Riepilogo
        print("\n" + "="*60)
        print("✅ SERVER ATTIVO!")
        print("="*60)
        
        if args.pubblico:
            print("\n🔗 URL PUBBLICI:\n")
            for provincia, porta in province:
                sottodominio = SOTTODOMINI.get(provincia)
                print(f"   {provincia}: https://{sottodominio}/")
        else:
            print("\n🔗 URL LOCALI:\n")
            for provincia, porta in province:
                print(f"   {provincia}: http://localhost:{porta}/")
        
        print("\n⏹️ Premi Ctrl+C per terminare\n")
        
        # Apri browser
        if not args.no_browser:
            for provincia, porta in province:
                if args.pubblico:
                    url = f"https://{SOTTODOMINI.get(provincia)}/"
                else:
                    url = f"http://localhost:{porta}/"
                webbrowser.open(url)
                time.sleep(0.5)
        
        # Loop principale
        try:
            while True:
                for i, proc in enumerate(processi_flask):
                    if proc.poll() is not None:
                        print(f"\n⚠️ {province[i][0]} terminato!")
                if args.pubblico and processo_tunnel and processo_tunnel.poll() is not None:
                    print("\n⚠️ Tunnel terminato!")
                    break
                time.sleep(5)
        except KeyboardInterrupt:
            pass
        
        termina_tutti()
    
    # Esegui launcher
    launcher_main()
    sys.exit(0)

# ============================================================================
# MODALITÀ FLASK (processo figlio)
# ============================================================================
# Da qui in poi: comportamento Flask normale per singola provincia

import json
import re
import time
import logging
import queue
import threading
import socket
import traceback as _traceback_mod
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory, send_file, make_response, Response, session, redirect, url_for
from flask_cors import CORS
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ---------- Logging ----------
_log_format = '%(asctime)s [%(levelname)s] %(message)s'
logging.basicConfig(level=logging.INFO, format=_log_format, datefmt='%H:%M:%S')
logger = logging.getLogger('mappa_algeco')

# Modulo autenticazione
from moduli.auth import init_auth, login_required, authenticate, load_users

# Modulo calendario condiviso
from moduli.calendario import (
    get_attivita, crea_attivita, modifica_attivita,
    completa_attivita, elimina_attivita, get_riepilogo,
    CATEGORIE_VALIDE, COLORI_CATEGORIE,
    crea_promemoria_boxbeat, get_promemoria_boxbeat_oggi,
    get_riepilogo_boxbeat, get_user_promemoria_settings,
    salva_user_promemoria_settings, registra_appuntamento_excel, genera_excel_boxbeat,
    pulisci_boxbeat_completati
)

# Porta Flask (da variabile ambiente impostata dal launcher)
FLASK_PORT = int(os.environ.get('FLASK_PORT', 5001))

# Provincia attiva (da variabile ambiente impostata dal launcher)
if 'PROVINCIA' not in os.environ:
    os.environ['PROVINCIA'] = 'TORINO'

def verifica_osrm_flask():
    """Verifica che il server OSRM sia attivo."""
    test_url = f"{OSRM_SERVER}/route/v1/driving/7.6869,45.0704;7.7888,45.1964"
    try:
        req = urllib.request.Request(test_url, method='GET')
        with urllib.request.urlopen(req, timeout=5) as response:
            if response.status == 200:
                return True
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ConnectionRefusedError):
        pass
    return False

from config import (
    ID_FOGLIO_GOOGLE,
    GID_SCHEDA,
    CREDENZIALI_JSON,
    PROVINCIA_ATTIVA,
    CAMPI_MODIFICABILI,
    CAMPI_BOXBEAT,
    COLONNE_SIGILLI,
    MATRICOLE_VIP,
    MODALITA_TEST,
)

# Colonne protette nella vista Excel (non modificabili dall'utente)
COLONNE_PROTETTE_FILONE = {
    'TORINO': [
        'LCL', 'TIPO_MISURATORE', 'MOT. RICH.', 'MID', 'TIPOLOGIA ZONA',
        'POD', 'ENELTEL', 'CODICE_ANTE_SOSTITUZIONE', 'MATRICOLA_ANTE_SOSTITUZIONE',
        'MATRICOLA CE INST', 'COD.', 'DATA INSTALLAZIONE', 'ESITO'
    ],
    'PAVIA': [
        'LCL', 'INIZIO LCL', 'FINE LCL', 'NDIS', 'TIPOLOGIA CE',
        'ENELTEL', 'POD', 'CODICE MISURATORE IN OPERA', 'MATRICOLA MISURATORE IN OPERA',
        'DATA INSTALLAZIONE', 'ESITO', 'CODICE POST', 'MATRICOLA POST'
    ]
}

app = Flask(__name__, static_folder='.')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # Limite upload: 16 MB

# CORS: limita le origini consentite (localhost + dominio Cloudflare)
_CORS_ORIGINS = [
    'http://localhost:5001', 'http://localhost:5002',
    'http://127.0.0.1:5001', 'http://127.0.0.1:5002',
    f'https://torino.{DOMINIO_BASE}', f'https://pavia.{DOMINIO_BASE}',
    f'https://{DOMINIO_BASE}',
]
CORS(app, supports_credentials=True, origins=_CORS_ORIGINS)

# Inizializza autenticazione
init_auth(app)

# ============================================================================
# BLUEPRINT REGISTRATION
# ============================================================================
from routes.contatori import bp as contatori_bp
from routes.distinte import bp as distinte_bp
from routes.calendario import bp as calendario_bp
from routes.sigilli import bp as sigilli_bp
from routes.sync import bp as sync_bp

app.register_blueprint(contatori_bp)
app.register_blueprint(distinte_bp)
app.register_blueprint(calendario_bp)
app.register_blueprint(sigilli_bp)
app.register_blueprint(sync_bp)

# ============================================================================
# CSRF PROTECTION (verifica Origin/Referer per POST/PUT/DELETE)
# ============================================================================
# Origini considerate fidate per richieste state-mutating
_TRUSTED_ORIGINS = set()
for _port in [5001, 5002]:
    _TRUSTED_ORIGINS.add(f'http://localhost:{_port}')
    _TRUSTED_ORIGINS.add(f'http://127.0.0.1:{_port}')
_TRUSTED_ORIGINS.add(f'https://torino.{DOMINIO_BASE}')
_TRUSTED_ORIGINS.add(f'https://pavia.{DOMINIO_BASE}')
_TRUSTED_ORIGINS.add(f'https://{DOMINIO_BASE}')

@app.before_request
def csrf_check():
    """Rifiuta POST/PUT/DELETE da origini non fidate (anti-CSRF)."""
    if request.method in ('POST', 'PUT', 'DELETE'):
        # Escludi login (non ha ancora sessione)
        if request.path == '/api/login':
            return None
        
        origin = request.headers.get('Origin')
        if not origin:
            # Fallback: controlla Referer
            referer = request.headers.get('Referer')
            if referer:
                from urllib.parse import urlparse
                parsed = urlparse(referer)
                origin = f"{parsed.scheme}://{parsed.netloc}"
        
        if origin and origin not in _TRUSTED_ORIGINS:
            return jsonify({'error': 'Richiesta bloccata: origine non autorizzata'}), 403
    return None

# _api_error importato da moduli.shared (vedi sotto)

# ============================================================================
# SSE (Server-Sent Events) - Notifiche in tempo reale
# ============================================================================
_sse_clients = []  # Lista di code per i client SSE connessi
_sse_lock = threading.Lock()

def sse_notify_all(event_type='update', data=None):
    """Notifica tutti i client SSE connessi."""
    if data is None:
        data = {'timestamp': datetime.now().strftime('%Y%m%d%H%M%S')}
    
    message = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
    
    with _sse_lock:
        dead_clients = []
        for client_queue in _sse_clients:
            try:
                client_queue.put_nowait(message)
            except queue.Full:
                dead_clients.append(client_queue)
        
        # Rimuovi client morti
        for dead in dead_clients:
            _sse_clients.remove(dead)
    
    logger.info(f"[SSE] 📢 Notifica '{event_type}' inviata a {len(_sse_clients)} client")


def sse_stream():
    """Generator per lo stream SSE."""
    client_queue = queue.Queue(maxsize=10)
    
    with _sse_lock:
        _sse_clients.append(client_queue)
    
    logger.info(f"[SSE] ✅ Nuovo client connesso (totale: {len(_sse_clients)})")
    
    try:
        # Invia evento iniziale di connessione
        yield f"event: connected\ndata: {json.dumps({'status': 'ok'})}\n\n"
        
        while True:
            try:
                # Attendi messaggi con timeout per keep-alive
                message = client_queue.get(timeout=30)
                yield message
            except queue.Empty:
                # Invia keep-alive per mantenere connessione
                yield ": keepalive\n\n"
    except GeneratorExit:
        pass
    finally:
        with _sse_lock:
            if client_queue in _sse_clients:
                _sse_clients.remove(client_queue)
        logger.info(f"[SSE] ❌ Client disconnesso (rimasti: {len(_sse_clients)})")


# ============================================================================
# GOOGLE SHEETS + HELPER  fonte unica: moduli/shared.py
# ============================================================================
from moduli.shared import (
    get_sheets_client, get_worksheet, get_header_map, get_headers,
    _sheets_retry, _reset_sheets_client,
    find_row_by_eneltel, find_row_by_pod,
    update_cell, update_row_fields,
    _trova_indice_colonna, _trova_indice_multi,
    _get_colonna_note, _get_username,
    _imposta_appuntamento_su_filone, _rimuovi_appuntamento_da_filone,
    _api_error,
)
import moduli.shared as _shared


# ============================================================================
# API ROUTES
# ============================================================================

# ============================================================================
# AUTENTICAZIONE ROUTES
# ============================================================================

@app.route('/favicon.svg')
def serve_favicon():
    """Serve la favicon SVG (senza autenticazione, no cache)."""
    response = send_file(os.path.join('static', 'favicon.svg'), mimetype='image/svg+xml')
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@app.route('/login')
def login_page():
    """Serve la pagina di login."""
    # Se già autenticato, redirect alla mappa
    if 'user' in session:
        return redirect(url_for('serve_index'))
    return send_file(os.path.join('templates', 'login.html'))


# Rate limiting server-side per login
_login_attempts = {}  # {ip: [(timestamp, ...)] }
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_LOCKOUT_SECONDS = 60

def _check_rate_limit(ip):
    """Controlla se l'IP ha superato il limite di tentativi."""
    now = time.time()
    attempts = _login_attempts.get(ip, [])
    # Rimuovi tentativi più vecchi del lockout
    attempts = [t for t in attempts if now - t < _LOGIN_LOCKOUT_SECONDS]
    _login_attempts[ip] = attempts
    return len(attempts) >= _LOGIN_MAX_ATTEMPTS

def _record_failed_attempt(ip):
    """Registra un tentativo fallito."""
    if ip not in _login_attempts:
        _login_attempts[ip] = []
    _login_attempts[ip].append(time.time())

def _clear_attempts(ip):
    """Pulisce i tentativi dopo login riuscito."""
    _login_attempts.pop(ip, None)


@app.route('/api/login', methods=['POST'])
def api_login():
    """Endpoint API per il login (con rate limiting server-side)."""
    client_ip = request.remote_addr or '0.0.0.0'
    
    # Controlla rate limit
    if _check_rate_limit(client_ip):
        return jsonify({
            'success': False,
            'message': f'Troppi tentativi. Riprova tra {_LOGIN_LOCKOUT_SECONDS} secondi.'
        }), 429
    
    data = request.get_json()
    
    if not data:
        return jsonify({'success': False, 'message': 'Dati mancanti'}), 400
    
    username = data.get('username', '').strip()
    password = data.get('password', '')
    
    if not username or not password:
        return jsonify({'success': False, 'message': 'Username e password richiesti'}), 400
    
    success, user_data, message = authenticate(username, password)
    
    if success:
        _clear_attempts(client_ip)
        session.permanent = True
        session['user'] = user_data
        return jsonify({
            'success': True,
            'message': message,
            'user': user_data
        })
    else:
        _record_failed_attempt(client_ip)
        return jsonify({'success': False, 'message': message}), 401


@app.route('/api/logout', methods=['POST'])
def api_logout():
    """Endpoint API per il logout."""
    session.clear()
    return jsonify({'success': True, 'message': 'Logout effettuato'})


@app.route('/logout')
def logout_redirect():
    """Logout e redirect al login."""
    session.clear()
    return redirect(url_for('login_page'))


@app.route('/api/user')
def api_current_user():
    """Ritorna l'utente corrente (se autenticato)."""
    if 'user' in session:
        return jsonify({'authenticated': True, 'user': session['user']})
    return jsonify({'authenticated': False}), 401


# ============================================================================
# ROUTES PROTETTE
# ============================================================================

@app.route('/')
@login_required
def serve_index():
    """Serve la mappa HTML (richiede autenticazione)."""
    return send_file(os.path.join('templates', f'mappa_{PROVINCIA_ATTIVA.lower()}.html'))


@app.route('/api/events')
@login_required
def sse_endpoint():
    """Endpoint SSE per notifiche in tempo reale."""
    response = Response(
        sse_stream(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no',  # Disabilita buffering nginx
        }
    )
    return response


@app.route('/api/config')
def get_config():
    """Restituisce la configurazione corrente (pubblica - nessun dato sensibile)."""
    return jsonify({
        'provincia': PROVINCIA_ATTIVA,
        'modalita_test': MODALITA_TEST,
        'campi_modificabili': CAMPI_MODIFICABILI.get(PROVINCIA_ATTIVA, {}),
        'operatori': MATRICOLE_VIP,
    })


# ============================================================================
# API IMPOSTAZIONI
# ============================================================================

@app.route('/api/settings', methods=['GET'])
@login_required
def get_settings():
    """Restituisce tutte le impostazioni configurabili + colonne filone."""
    import config
    
    # Recupera le intestazioni colonne dal foglio Google
    colonne_filone = []
    try:
        h = get_headers()
        if h:
            colonne_filone = list(h)
            logger.debug(f"📋 Colonne filone da sheets: {len(colonne_filone)}")
    except Exception as e:
        logger.warning(f"⚠️ get_headers() fallito: {e}")
    
    # Fallback: se vuoto, prova a leggere dal backup più recente
    if not colonne_filone:
        try:
            backup_dir = os.path.join('backup', PROVINCIA_ATTIVA.lower())
            if os.path.isdir(backup_dir):
                backup_files = sorted(
                    [f for f in os.listdir(backup_dir) if f.endswith('.json')],
                    reverse=True
                )
                if backup_files:
                    with open(os.path.join(backup_dir, backup_files[0]), 'r', encoding='utf-8') as bf:
                        backup_data = json.load(bf)
                    colonne_filone = backup_data.get('headers', [])
                    logger.info(f"📋 Colonne filone dal backup ({backup_files[0]}): {len(colonne_filone)}")
        except Exception as e2:
            logger.warning(f"⚠️ Fallback backup colonne fallito: {e2}")
    
    logger.info(f"📋 API settings - colonne_filone: {len(colonne_filone)} colonne")
    
    return jsonify({
        'operatori_vip': config.MATRICOLE_VIP,
        'colori_operatori': config.COLORI_TECNICI,
        'operatori_dict': config.OPERATORI_DICT,
        'deposito': config.DEPOSITO,
        'ora_inizio_giornata': config.ORA_INIZIO_GIORNATA,
        'ora_fine_giornata': config.ORA_FINE_GIORNATA,
        'velocita_media_kmh': config.VELOCITA_MEDIA_KMH,
        'tempo_intervento_min': config.TEMPO_INTERVENTO_MIN,
        'raggio_microzona_km': config.RAGGIO_MICROZONA_KM,
        'ottimizza_per_tempo': config.OTTIMIZZA_PER_TEMPO,
        'intervallo_sync_secondi': config.INTERVALLO_SYNC_SECONDI,
        'data_inizio': config.DATA_INIZIO,
        'data_fine': config.DATA_FINE,
        'mappatura_colonne': config.MAPPATURA_COLONNE,
        'mappatura_colonne_default': config.MAPPATURA_COLONNE_DEFAULT,
        'colonne_filone': colonne_filone,
    })


@app.route('/api/settings', methods=['PUT'])
@login_required
def update_settings():
    """Aggiorna le impostazioni e triggera ri-sincronizzazione."""
    import config
    from moduli import mappa_html, percorso, zone_geografiche, distanze, gestionale, data_loader
    
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Dati mancanti'}), 400
    
    try:
        # Salva su file persistente
        settings_dir = os.path.join('data', PROVINCIA_ATTIVA.lower())
        settings_file = os.path.join(settings_dir, 'settings.json')
        os.makedirs(settings_dir, exist_ok=True)
        
        with open(settings_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        # Aggiorna variabili in memoria in TUTTI i moduli che le hanno importate
        global MATRICOLE_VIP
        
        # Mapping: chiave settings -> lista di (modulo, nome_attributo)
        _settings_map = {
            'operatori_vip': [
                (config, 'MATRICOLE_VIP'),
                (mappa_html, 'MATRICOLE_VIP'),
                (distanze, 'MATRICOLE_VIP'),
                (data_loader, 'MATRICOLE_VIP'),
            ],
            'colori_operatori': [
                (config, 'COLORI_TECNICI'),
                (mappa_html, 'COLORI_TECNICI'),
            ],
            'operatori_dict': [
                (config, 'OPERATORI_DICT'),
                (gestionale, 'OPERATORI_DICT'),
            ],
            'deposito': [
                (config, 'DEPOSITO'),
                (mappa_html, 'DEPOSITO'),
                (percorso, 'DEPOSITO'),
                (zone_geografiche, 'DEPOSITO'),
            ],
            'ora_inizio_giornata': [
                (config, 'ORA_INIZIO_GIORNATA'),
                (mappa_html, 'ORA_INIZIO_GIORNATA'),
                (percorso, 'ORA_INIZIO_GIORNATA'),
            ],
            'ora_fine_giornata': [
                (config, 'ORA_FINE_GIORNATA'),
                (mappa_html, 'ORA_FINE_GIORNATA'),
                (percorso, 'ORA_FINE_GIORNATA'),
            ],
            'velocita_media_kmh': [
                (config, 'VELOCITA_MEDIA_KMH'),
                (distanze, 'VELOCITA_MEDIA_KMH'),
            ],
            'tempo_intervento_min': [
                (config, 'TEMPO_INTERVENTO_MIN'),
                (distanze, 'TEMPO_INTERVENTO_MIN'),
            ],
            'raggio_microzona_km': [
                (config, 'RAGGIO_MICROZONA_KM'),
            ],
            'ottimizza_per_tempo': [
                (config, 'OTTIMIZZA_PER_TEMPO'),
            ],
            'intervallo_sync_secondi': [
                (config, 'INTERVALLO_SYNC_SECONDI'),
                (mappa_html, 'INTERVALLO_SYNC_SECONDI'),
            ],
            'data_inizio': [
                (config, 'DATA_INIZIO'),
                (data_loader, 'DATA_INIZIO'),
            ],
            'data_fine': [
                (config, 'DATA_FINE'),
                (mappa_html, 'DATA_FINE'),
                (data_loader, 'DATA_FINE'),
            ],
            'mappatura_colonne': [
                (config, 'MAPPATURA_COLONNE'),
                (data_loader, 'MAPPATURA_COLONNE'),
            ],
        }
        
        for key, targets in _settings_map.items():
            if key in data:
                value = data[key]
                for module, attr_name in targets:
                    setattr(module, attr_name, value)
        
        # Aggiorna anche il riferimento locale di server.py
        if 'operatori_vip' in data:
            MATRICOLE_VIP = data['operatori_vip']
        
        # Aggiorna anche il dict PROVINCE in config per coerenza
        if 'operatori_vip' in data:
            config.PROVINCE[PROVINCIA_ATTIVA]['operatori_vip'] = data['operatori_vip']
        if 'colori_operatori' in data:
            config.PROVINCE[PROVINCIA_ATTIVA]['colori_operatori'] = data['colori_operatori']
        if 'operatori_dict' in data:
            config.PROVINCE[PROVINCIA_ATTIVA]['operatori_dict'] = data['operatori_dict']
        if 'deposito' in data:
            config.PROVINCE[PROVINCIA_ATTIVA]['deposito'] = data['deposito']
        if 'ora_inizio_giornata' in data:
            config.PROVINCE[PROVINCIA_ATTIVA]['ora_inizio_giornata'] = data['ora_inizio_giornata']
        if 'ora_fine_giornata' in data:
            config.PROVINCE[PROVINCIA_ATTIVA]['ora_fine_giornata'] = data['ora_fine_giornata']
        
        logger.info(f"⚙️  Impostazioni aggiornate da {session.get('user', 'unknown')}")
        
        # Ri-sincronizza in background (rigenera mappa con nuove impostazioni)
        threading.Thread(target=sincronizza_dati, daemon=True).start()
        
        return jsonify({'success': True, 'message': 'Impostazioni salvate. Ri-sincronizzazione in corso...'})
    except Exception as e:
        return _api_error(e, 'salvataggio impostazioni')


# File statici dalla cartella data/
@app.route('/data/<path:filename>')
@login_required
def serve_data_files(filename):
    """Serve file JSON dalla cartella data/ senza cache (richiede autenticazione)."""
    # Blocca path traversal
    import posixpath
    normalized = posixpath.normpath(filename)
    if normalized.startswith('..') or normalized.startswith('/'):
        return jsonify({'error': 'Accesso negato'}), 403
    
    response = make_response(send_from_directory('data', filename))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@app.route('/filone')
@login_required
def serve_filone_page():
    """Serve la pagina vista Excel del filone."""
    return send_file(os.path.join('templates', 'filone.html'))


# File/cartelle sensibili: MAI servire questi
_BLOCKED_FILES = {'.flask_secret_key', 'credenziali.json', 'users.json', 'config.py',
                   'gestione_utenti.py', 'server.py', 'genera_excel_lettere.py'}
_BLOCKED_DIRS = {'__pycache__', 'moduli', 'routes', 'backup', '.git', 'templates'}
_ALLOWED_EXTENSIONS = {'.html', '.css', '.js', '.png', '.jpg', '.jpeg', '.gif',
                        '.svg', '.ico', '.woff', '.woff2', '.ttf', '.eot', '.map'}

@app.route('/<path:filename>')
@login_required
def serve_static(filename):
    """Serve file statici (solo estensioni sicure, con autenticazione)."""
    import posixpath
    # Normalizza e blocca path traversal
    normalized = posixpath.normpath(filename)
    if normalized.startswith('..') or normalized.startswith('/'):
        return jsonify({'error': 'Accesso negato'}), 403
    
    # Blocca file sensibili
    basename = os.path.basename(normalized).lower()
    if basename in _BLOCKED_FILES or basename.startswith('.'):
        return jsonify({'error': 'Accesso negato'}), 403
    
    # Blocca directory sensibili
    parts = normalized.replace('\\', '/').split('/')
    if any(part.lower() in _BLOCKED_DIRS for part in parts):
        return jsonify({'error': 'Accesso negato'}), 403
    
    # Permetti solo estensioni sicure
    _, ext = os.path.splitext(basename)
    if ext.lower() not in _ALLOWED_EXTENSIONS:
        return jsonify({'error': 'Tipo file non consentito'}), 403
    
    return send_from_directory('.', filename)


# ============================================================================
# SYNC AUTOMATICO IN BACKGROUND
# ============================================================================
import threading
import time as time_module
import traceback

# Importa le funzioni di sincronizzazione
from moduli import (
    carica_cache,
    salva_cache,
    pulisci_cache_vecchia,
    genera_report_distanze_con_cache,
    stampa_report_distanze,
    carica_dati_google_sheets,
    genera_report_zone,
    genera_mappa_html
)
from config import (
    DEPOSITO,
    FILE_CACHE_DISTANZE,
    FILE_CACHE_HASH,
    FILE_MAPPA,
    INTERVALLO_SYNC_SECONDI
)

_sync_running = False
_sync_thread = None
_prima_sync_completata = False


def sincronizza_dati():
    """Funzione di sincronizzazione dati."""
    logger.info(f"[SYNC {time_module.strftime('%H:%M:%S')}] {'='*50}")
    logger.info(f"[SYNC {time_module.strftime('%H:%M:%S')}] Sincronizzazione - {PROVINCIA_ATTIVA}")
    logger.info(f"[SYNC {time_module.strftime('%H:%M:%S')}] {'='*50}")
    
    start_loop = time_module.time()
    
    try:
        # Carica cache
        cache_distanze, cache_hash = carica_cache()
        cache_distanze, cache_hash = pulisci_cache_vecchia(cache_distanze, cache_hash, giorni_da_mantenere=7)

        # Carica dati
        df = carica_dati_google_sheets()
        if df is None:
            logger.warning(f"[SYNC] ⚠️ Nessun dato caricato")
            return False

        # Calcolo distanze
        logger.info("🚗 Verifica distanze (con cache)...")
        report, cache_distanze, cache_hash = genera_report_distanze_con_cache(
            df, DEPOSITO, cache_distanze, cache_hash
        )

        salva_cache(cache_distanze, cache_hash)
        stampa_report_distanze(report)

        # Genera zone geografiche
        logger.info("🗺️ Analisi zone geografiche...")
        risultato_zone = genera_report_zone(df, filename=None)

        # Genera mappa HTML
        logger.info("🌐 Generazione mappa HTML...")
        genera_mappa_html(df, report, risultato_zone, filename=FILE_MAPPA)

        elapsed = time_module.time() - start_loop
        logger.info(f"✅ Sync completato in {elapsed:.1f}s")
        
        # Backup periodico automatico (giornaliero, con pulizia mensile)
        try:
            from moduli.backup import backup_periodico_auto
            backup_periodico_auto()
        except Exception as e_backup:
            logger.warning(f"[BACKUP] ⚠️ Errore nel backup periodico: {e_backup}")
        
        # Notifica tutti i client SSE che ci sono nuovi dati
        sse_notify_all('sync_complete', {'source': 'background_sync'})
        
        return True
        
    except Exception as e:
        logger.error(f"[SYNC] ❌ Errore: {e}")
        traceback.print_exc()
        return False


def background_sync_loop():
    """Loop di sincronizzazione in background."""
    global _sync_running, _prima_sync_completata
    
    logger.info(f"[SYNC] 🔄 Thread di sync avviato (intervallo: {INTERVALLO_SYNC_SECONDI}s)")
    
    # Se lanciato dal launcher, non aprire il browser (lo fa il launcher)
    lanciato_da_launcher = os.environ.get('FLASK_PORT') is not None
    
    while _sync_running:
        try:
            result = sincronizza_dati()
            
            # Apri browser dopo la prima sync completata con successo
            # (solo se NON lanciato dal launcher)
            if result and not _prima_sync_completata:
                _prima_sync_completata = True
                if not lanciato_da_launcher:
                    logger.info(f"🌐 Apertura mappa nel browser...")
                    import webbrowser
                    url = f"http://localhost:{FLASK_PORT}/"
                    webbrowser.open(url)
                
        except Exception as e:
            logger.error(f"[SYNC] ❌ Errore nel loop: {e}")
            traceback.print_exc()
        
        # Sleep interrompibile
        for _ in range(INTERVALLO_SYNC_SECONDI):
            if not _sync_running:
                break
            time_module.sleep(1)
    
    logger.info("[SYNC] 🛑 Thread di sync terminato")


def avvia_sync_background():
    """Avvia il thread di sincronizzazione in background."""
    global _sync_running, _sync_thread
    
    if _sync_running:
        return
    
    _sync_running = True
    _sync_thread = threading.Thread(target=background_sync_loop, daemon=True)
    _sync_thread.start()
    logger.info("[SYNC] ✅ Sync in background avviato")


# Lock per evitare sync multipli simultanei da modifiche
_edit_sync_lock = threading.Lock()
_edit_sync_in_progress = False
_edit_sync_pending = False  # Flag per richieste pendenti durante sync

def trigger_sync_after_edit():
    """Triggera un sync immediato dopo una modifica dalla mappa."""
    global _edit_sync_in_progress, _edit_sync_pending
    
    def do_sync():
        global _edit_sync_in_progress, _edit_sync_pending
        should_continue = True
        while should_continue:
            try:
                logger.info("[SYNC] ⚡ Sync immediato dopo modifica...")
                sincronizza_dati()
            except Exception as e:
                logger.error(f"[SYNC] ❌ Errore durante sync: {e}")
            
            # Controlla se ci sono richieste pendenti (fuori dal finally)
            with _edit_sync_lock:
                if _edit_sync_pending:
                    # C'è una richiesta pendente, fai un altro sync
                    logger.info("[SYNC] 🔄 Richiesta pendente rilevata, nuovo sync...")
                    _edit_sync_pending = False
                    # should_continue rimane True, il loop continua
                else:
                    _edit_sync_in_progress = False
                    should_continue = False  # Esci dal loop
    
    with _edit_sync_lock:
        if _edit_sync_in_progress:
            # Sync già in corso, segna come pendente
            _edit_sync_pending = True
            logger.info("[SYNC] ⏳ Sync già in corso, segnata richiesta pendente")
            return
        _edit_sync_in_progress = True
    
    # Avvia sync in thread separato per non bloccare la risposta HTTP
    threading.Thread(target=do_sync, daemon=True).start()


def _extract_contatore_data_from_row(row_num):
    """
    Helper: Estrae dati contatore (POD, nominativo, via, civico, comune) da una riga del foglio.
    Restituisce un dict con i dati trovati.
    """
    ws = get_worksheet()
    row_data = ws.row_values(row_num)
    
    headers = get_headers()
    data = {}
    for i, h in enumerate(headers):
        if i < len(row_data):
            data[h] = row_data[i]
        else:
            data[h] = ''
    
    # Mappa colonne in base alla provincia
    risultato = {
        'pod': '',
        'eneltel': '',
        'nominativo': '',
        'via': '',
        'civico': '',
        'comune': '',
        'data_pianificata': '',
    }
    
    # POD
    for col in ['POD', 'Pod', 'pod', 'Codice POD']:
        if col in data and data[col]:
            risultato['pod'] = str(data[col]).strip()
            break
    
    # ENELTEL
    for col in ['ENELTEL', 'Eneltel', 'eneltel']:
        if col in data and data[col]:
            risultato['eneltel'] = str(data[col]).strip()
            break
    
    # NOMINATIVO
    for col in ['NOMINATIVO', 'Nominativo', 'nominativo']:
        if col in data and data[col]:
            risultato['nominativo'] = str(data[col]).strip()
            break
    
    # VIA
    for col in ['Indirizzo', 'Via', 'VIA', 'via', 'indirizzo']:
        if col in data and data[col]:
            risultato['via'] = str(data[col]).strip()
            break
    
    # CIVICO
    for col in ['Num', 'civico', 'CIVICO', 'Civico', 'N.Civico']:
        if col in data and data[col]:
            risultato['civico'] = str(data[col]).strip()
            break
    
    # COMUNE
    for col in ['COMUNE', 'Comune', 'comune', 'Località', 'Localita']:
        if col in data and data[col]:
            risultato['comune'] = str(data[col]).strip()
            break
    
    # DATA PIANIFICATA
    for col in ['DATA PIAN', 'DATA_PIAN', 'Data_pianificata', 'DATA PIANIFICATA', 'PROG', 'DATA']:
        if col in data and data[col]:
            val = str(data[col]).strip()
            # Normalizza in formato YYYY-MM-DD se possibile
            from moduli.data_loader import parse_data_flessibile
            parsed = parse_data_flessibile(val)
            if parsed:
                risultato['data_pianificata'] = parsed
            else:
                risultato['data_pianificata'] = val
            break
    
    return risultato


# ============================================================================
# REGISTRAZIONE CALLBACK PER BLUEPRINT
# ============================================================================
from moduli.shared import register_callbacks
register_callbacks(
    trigger_sync_after_edit=trigger_sync_after_edit,
    sse_notify_all=sse_notify_all,
    crea_promemoria_boxbeat=crea_promemoria_boxbeat,
    registra_appuntamento_excel=registra_appuntamento_excel,
    extract_contatore_data=_extract_contatore_data_from_row,
    sincronizza_dati=sincronizza_dati,
    is_sync_running=lambda: _sync_running,
    get_sync_interval=lambda: INTERVALLO_SYNC_SECONDI,
)


# ============================================================================
# MAIN (processo Flask figlio)
# ============================================================================
if __name__ == '__main__':
    logger.info("=" * 60)
    logger.info(f"  FLASK - {os.environ.get('PROVINCIA', 'TORINO')}")
    logger.info("=" * 60)
    logger.info("")
    
    # Verifica OSRM (normalmente skippata - fatto dal launcher)
    if not args.skip_osrm_check:
        logger.info("🔍 Verifica connessione OSRM...")
        if not verifica_osrm_flask():
            logger.warning("⚠️ OSRM non raggiungibile! Continuo comunque...")
        else:
            logger.info("✅ OSRM raggiungibile")
    
    # Ottieni IP locale per accesso in rete
    def get_local_ip():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return '127.0.0.1'
    
    local_ip = get_local_ip()
    
    logger.info("")
    logger.info(f"📍 Provincia: {PROVINCIA_ATTIVA}")
    logger.info(f"📊 Modalità: {'TEST' if MODALITA_TEST else 'PRODUZIONE'}")
    logger.info(f"🌐 http://localhost:{FLASK_PORT}")
    logger.info("")
    
    # Avvia sync in background (farà subito la prima sincronizzazione)
    avvia_sync_background()
    
    # Pulizia boxbeat completati vecchi (> 7 giorni) per evitare accumulo
    try:
        rimossi = pulisci_boxbeat_completati(giorni_retention=7)
        if rimossi:
            logger.info(f"🧹 Pulizia: rimossi {rimossi} boxbeat completati")
    except Exception as e:
        logger.warning(f"⚠️ Errore pulizia boxbeat: {e}")
    
    # Avvia Flask
    app.run(host='0.0.0.0', port=FLASK_PORT, debug=False, threaded=True)
