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
        """Tenta di avviare OSRM automaticamente. Supporta sia nativo (systemd) che Docker."""
        
        # --- Tentativo 1: OSRM nativo via systemd (preferito, meno RAM) ---
        if IS_LINUX:
            try:
                # Verifica se il servizio systemd esiste
                result = subprocess.run(
                    ["systemctl", "is-enabled", "osrm-server"],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    print("   🚀 Avvio OSRM nativo via systemd...")
                    subprocess.run(["sudo", "systemctl", "start", "osrm-server"], timeout=15)
                    time.sleep(2)
                    
                    # Attendi che OSRM sia pronto (max 30s)
                    print("   ⏳ Attesa che OSRM sia pronto...")
                    for i in range(30):
                        if verifica_osrm():
                            print(f"   ✅ OSRM nativo avviato e pronto! ({i+1}s)")
                            return True
                        time.sleep(1)
                        if i > 0 and i % 10 == 0:
                            print(f"      ({i+1}s...)")
                    print("   ❌ OSRM nativo non ha risposto in tempo")
                    return False
            except Exception as e:
                print(f"   ⚠️ Servizio systemd OSRM non disponibile: {e}")
        
        # --- Tentativo 2: Fallback Docker (retrocompatibilità) ---
        CONTAINER_NAME = "osrm-server"
        
        # Verifica se Docker è disponibile
        docker_cmd = shutil.which("docker")
        if not docker_cmd:
            print("   ❌ Né OSRM nativo né Docker trovati")
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
                    print("   Verifica: sudo systemctl status osrm-server")
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
import logging
import queue
import threading
import socket
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory, send_file, make_response, Response, session, redirect, url_for
from flask_cors import CORS

# ---------- Logging ----------
_log_format = '%(asctime)s [%(levelname)s] %(message)s'
logging.basicConfig(level=logging.INFO, format=_log_format, datefmt='%H:%M:%S')
logger = logging.getLogger('mappa_algeco')

# Modulo autenticazione
from moduli.auth import (
    init_auth, login_required, admin_required, authenticate, load_users,
    create_user, delete_user, reset_password, toggle_user_active,
    list_users, set_user_province, set_user_role,
    PROVINCE_DISPONIBILI, RUOLI_DISPONIBILI
)

# Modulo calendario condiviso
from moduli.calendario import (
    crea_promemoria_boxbeat, registra_appuntamento_excel, pulisci_boxbeat_completati
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
    PROVINCIA_ATTIVA,
    CAMPI_MODIFICABILI,
    MATRICOLE_VIP,
)

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
from routes.bacheca import bp as bacheca_bp
from routes.dashboard import bp as dashboard_bp
from routes.report import bp as report_bp
from routes.access_points import bp as access_points_bp
from routes.verifica_indirizzi import bp as verifica_indirizzi_bp

app.register_blueprint(contatori_bp)
app.register_blueprint(distinte_bp)
app.register_blueprint(calendario_bp)
app.register_blueprint(sigilli_bp)
app.register_blueprint(sync_bp)
app.register_blueprint(bacheca_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(report_bp)
app.register_blueprint(access_points_bp)
app.register_blueprint(verifica_indirizzi_bp)

# ============================================================================
# CSRF PROTECTION
# ============================================================================
# Doppia barriera:
#   1) Origin/Referer check (sotto): blocca POST/PUT/DELETE da origini esterne.
#   2) CSRF token (double-cookie): token random in sessione, riflesso come
#      cookie 'csrf_token' (non-HttpOnly) leggibile da JS. Il frontend lo
#      invia nell'header X-CSRF-Token su ogni POST/PUT/DELETE; il server
#      verifica match con il valore in sessione (immune a CSRF perché un
#      attaccante cross-site non può leggere il cookie csrf_token grazie a
#      SameSite=Lax).
import secrets as _secrets

_TRUSTED_ORIGINS = set()
for _port in [5001, 5002]:
    _TRUSTED_ORIGINS.add(f'http://localhost:{_port}')
    _TRUSTED_ORIGINS.add(f'http://127.0.0.1:{_port}')
_TRUSTED_ORIGINS.add(f'https://torino.{DOMINIO_BASE}')
_TRUSTED_ORIGINS.add(f'https://pavia.{DOMINIO_BASE}')
_TRUSTED_ORIGINS.add(f'https://{DOMINIO_BASE}')


def _get_or_create_csrf_token():
    """Restituisce il CSRF token in sessione (lo crea se mancante)."""
    if 'csrf_token' not in session:
        session['csrf_token'] = _secrets.token_urlsafe(32)
    return session['csrf_token']


@app.after_request
def set_csrf_cookie(response):
    """Riflette il CSRF token come cookie leggibile da JS (per il frontend).

    Genera il token se manca, così è disponibile dal primo request autenticato.
    """
    if 'user' in session:
        token = _get_or_create_csrf_token()
        response.set_cookie(
            'csrf_token',
            token,
            httponly=False,  # JS deve poterlo leggere
            samesite='Lax',
            secure=app.config.get('SESSION_COOKIE_SECURE', False),
            max_age=24 * 60 * 60,
        )
    return response


@app.before_request
def csrf_check():
    """Verifica Origin + CSRF token per POST/PUT/DELETE."""
    if request.method not in ('POST', 'PUT', 'DELETE'):
        return None
    # Endpoint esclusi dal CSRF check:
    # - /api/login: nessuna sessione ancora, niente token in sessione.
    # - /api/logout: forzare un logout non causa danno; e se fallisse qui
    #   l'utente resterebbe loggato silenziosamente.
    # - /api/sync/webhook: server-to-server (Apps Script di Google), già
    #   autenticato via header X-Sync-Secret.
    if request.path in ('/api/login', '/api/logout', '/api/sync/webhook'):
        return None

    # 1) Origin/Referer check
    origin = request.headers.get('Origin')
    if not origin:
        referer = request.headers.get('Referer')
        if referer:
            from urllib.parse import urlparse
            parsed = urlparse(referer)
            origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin and origin not in _TRUSTED_ORIGINS:
        return jsonify({'error': 'Richiesta bloccata: origine non autorizzata'}), 403

    # 2) CSRF token check (solo per utenti già autenticati)
    if 'user' in session:
        header_token = request.headers.get('X-CSRF-Token', '')
        session_token = session.get('csrf_token', '')
        if not header_token or not session_token or not _secrets.compare_digest(
            header_token, session_token
        ):
            return jsonify({'error': 'CSRF token mancante o non valido'}), 403
    return None


@app.before_request
def check_provincia_access():
    """Verifica che l'utente loggato abbia accesso alla provincia di questa istanza."""
    # Escludi pagine pubbliche
    if request.path in ('/login', '/api/login', '/api/logout', '/logout'):
        return None
    # Escludi risorse statiche
    if request.path.startswith('/static/'):
        return None

    if 'user' in session:
        # Rileggi province aggiornate da users.json
        username = session['user'].get('username', '')
        users = load_users()
        if username in users:
            province_utente = users[username].get('province', list(PROVINCE_DISPONIBILI))
            session['user']['province'] = province_utente
        else:
            province_utente = session['user'].get('province', [])

        if province_utente and PROVINCIA_ATTIVA.upper() not in province_utente:
            session.clear()
            if request.path.startswith('/api/'):
                return jsonify({'error': f'Non hai accesso alla provincia {PROVINCIA_ATTIVA}'}), 403
            return redirect(url_for('login_page'))

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
    get_worksheet, get_headers,
    _api_error,
)


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
    
    success, user_data, message = authenticate(username, password, PROVINCIA_ATTIVA)
    
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


def _delete_all_session_cookies(resp):
    """Cancella esplicitamente il cookie sessione su TUTTI i possibili domini.

    Necessario dopo l'introduzione di SESSION_COOKIE_DOMAIN=.mappa-algeco.com:
    chi era loggato prima del cambio ha un cookie host-scoped (es.
    Domain=torino.mappa-algeco.com) che `session.clear()` non rimuove perché
    Flask scrive solo sul Domain configurato corrente.

    Mandiamo Set-Cookie con expires=0 sia su host che su parent domain così il
    browser scarta entrambe le varianti.
    """
    cookie_name = app.config.get('SESSION_COOKIE_NAME', 'session')
    # Variante host-scoped (cookie storici, senza Domain attribute)
    resp.delete_cookie(cookie_name, path='/')
    # Variante con Domain configurato (cookie nuovi, cross-subdomain)
    cfg_domain = app.config.get('SESSION_COOKIE_DOMAIN')
    if cfg_domain:
        resp.delete_cookie(cookie_name, path='/', domain=cfg_domain)
    return resp


@app.route('/api/logout', methods=['POST'])
def api_logout():
    """Endpoint API per il logout."""
    session.clear()
    return _delete_all_session_cookies(
        jsonify({'success': True, 'message': 'Logout effettuato'})
    )


@app.route('/logout')
def logout_redirect():
    """Logout e redirect al login."""
    session.clear()
    return _delete_all_session_cookies(redirect(url_for('login_page')))


@app.route('/api/user')
def api_current_user():
    """Ritorna l'utente corrente (se autenticato)."""
    if 'user' in session:
        user_data = dict(session['user'])
        # Aggiorna ruolo dal file utenti (potrebbe essere cambiato)
        users = load_users()
        username = user_data.get('username', '')
        if username in users:
            user_data['ruolo'] = users[username].get('ruolo', 'user')
            user_data['province'] = users[username].get('province', list(PROVINCE_DISPONIBILI))
            # Aggiorna anche la sessione
            session['user'] = user_data
        return jsonify({'authenticated': True, 'user': user_data})
    return jsonify({'authenticated': False}), 401


# ============================================================================
# ROUTES PROTETTE
# ============================================================================

@app.route('/')
@login_required
def serve_index():
    """Serve la mappa HTML renderizzata al volo dall'ultima cache di sync."""
    from moduli.mappa_html import render_mappa_corrente
    return Response(render_mappa_corrente(), mimetype='text/html; charset=utf-8')


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
        'modalita_test': False,
        'campi_modificabili': CAMPI_MODIFICABILI.get(PROVINCIA_ATTIVA, {}),
        'operatori': MATRICOLE_VIP,
    })


@app.route('/api/route_geometry', methods=['POST'])
@login_required
def api_route_geometry():
    """
    Geometria stradale OSRM per una sequenza arbitraria di waypoint (lon, lat).

    Usato dalla mappa in modalita' ordinamento per orario: l'ordine delle tappe
    non coincide con il round-trip pre-calcolato nel JSON sync.
    """
    payload = request.get_json(silent=True) or {}
    coords_raw = payload.get('coordinates')

    if not isinstance(coords_raw, list) or len(coords_raw) < 2:
        return jsonify({'success': False, 'error': 'coordinates deve essere un array di almeno 2 punti'}), 400

    if len(coords_raw) > 200:
        return jsonify({'success': False, 'error': 'Troppi punti nella richiesta (max 200)'}), 400

    coords_list = []
    for pair in coords_raw:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            return jsonify({'success': False, 'error': 'Ogni punto deve essere [lon, lat]'}), 400
        try:
            lon_f = float(pair[0])
            lat_f = float(pair[1])
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'Coordinate non numeriche'}), 400

        if not (-180.0 <= lon_f <= 180.0 and -90.0 <= lat_f <= 90.0):
            return jsonify({'success': False, 'error': 'Coordinate fuori range'}), 400
        # Area operative Italia nord / vicinanze (stesso ordine di grandezza del data loader)
        if not (35.0 <= lat_f <= 48.5 and 6.0 <= lon_f <= 20.0):
            return jsonify({'success': False, 'error': 'Coordinate fuori area operative'}), 400

        coords_list.append((lon_f, lat_f))

    from moduli.distanze import calcola_geometria_percorso_waypoints

    geometria, err = calcola_geometria_percorso_waypoints(coords_list)
    if geometria is None:
        logger.warning(f"⚠️  route_geometry fallito: {err}")
        return jsonify({'success': False, 'error': err or 'Calcolo rotta fallito'}), 502

    return jsonify({'success': True, 'geometry': geometria})


# ============================================================================
# API IMPOSTAZIONI
# ============================================================================

@app.route('/api/settings', methods=['GET'])
@login_required
def get_settings():
    """Restituisce tutte le impostazioni configurabili + colonne filone."""
    import config
    
    # Recupera le intestazioni colonne fresche dal foglio Google
    colonne_filone = []
    try:
        # Forza refresh per avere sempre gli header aggiornati
        get_worksheet(force_refresh=True)
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
                    from moduli.safe_json import json_read as _jr
                    backup_data = _jr(os.path.join(backup_dir, backup_files[0]), default={})
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
        from moduli.safe_json import json_write as _jw
        settings_dir = os.path.join('data', PROVINCIA_ATTIVA.lower())
        settings_file = os.path.join(settings_dir, 'settings.json')
        os.makedirs(settings_dir, exist_ok=True)
        
        _jw(settings_file, data, indent=2)
        
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
                (mappa_html, 'OPERATORI_DICT'),
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

        # Rigenera SUBITO config.json e (se possibile) la mappa HTML usando i dati in
        # cache. Senza questo, il loop di sync salta tutto se i dati Google Sheets
        # non sono cambiati (Livello 1: "dati invariati") e le nuove impostazioni
        # non vengono riflesse nei file serviti al frontend finche' i dati non
        # cambiano (o finche' il server non viene riavviato).
        from moduli.mappa_html import salva_config_statica, genera_mappa_html
        try:
            salva_config_statica()
            logger.info("[SETTINGS] config.json rigenerato sincronicamente")
        except Exception as e_cfg:
            logger.warning(f"[SETTINGS] Rigenerazione config.json fallita: {e_cfg}")

        html_regen_ok = False
        if _last_report is not None and _last_risultato_zone is not None:
            try:
                from moduli.data_loader import carica_dati_google_sheets
                df_cached = carica_dati_google_sheets(force=False)
                if df_cached is not None:
                    genera_mappa_html(df_cached, _last_report, _last_risultato_zone)
                    html_regen_ok = True
                    logger.info("[SETTINGS] HTML rigenerato sincronicamente con dati in cache")
            except Exception as e_html:
                logger.warning(f"[SETTINGS] Rigenerazione HTML fallita: {e_html}")

        # Notifica i client connessi cosi' ricaricano subito config + dati.
        sse_notify_all('sync_complete', {'source': 'settings_change'})

        # Se non avevamo cache (server appena avviato), forza un full sync in
        # background per generare la mappa con i dati piu' recenti.
        if not html_regen_ok:
            threading.Thread(target=sincronizza_dati, daemon=True).start()
            msg = 'Impostazioni salvate. Ri-sincronizzazione in corso...'
        else:
            msg = 'Impostazioni salvate e applicate.'

        return jsonify({'success': True, 'message': msg})
    except Exception as e:
        return _api_error(e, 'salvataggio impostazioni')


# ============================================================================
# API GESTIONE UTENTI (solo admin)
# ============================================================================

@app.route('/api/admin/users', methods=['GET'])
@admin_required
def api_admin_list_users():
    """Lista tutti gli utenti (solo admin)."""
    utenti = list_users()
    return jsonify({'success': True, 'users': utenti, 'province_disponibili': PROVINCE_DISPONIBILI, 'ruoli_disponibili': RUOLI_DISPONIBILI})


@app.route('/api/admin/users', methods=['POST'])
@admin_required
def api_admin_create_user():
    """Crea un nuovo utente (solo admin)."""
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': 'Dati mancanti'}), 400

    username = (data.get('username') or '').strip()
    password = (data.get('password') or '').strip() or None
    province = data.get('province')
    ruolo = data.get('ruolo', 'user')

    if not username:
        return jsonify({'success': False, 'message': 'Username richiesto'}), 400

    if password and len(password) < 8:
        return jsonify({'success': False, 'message': 'Password troppo corta (min 8 caratteri)'}), 400

    success, msg, generated_pass = create_user(username, password, province, ruolo)
    if success:
        return jsonify({'success': True, 'message': msg, 'password_generata': generated_pass})
    return jsonify({'success': False, 'message': msg}), 400


@app.route('/api/admin/users/<username>', methods=['DELETE'])
@admin_required
def api_admin_delete_user(username):
    """Elimina un utente (solo admin)."""
    current_user = session.get('user', {}).get('username', '')
    if username.lower() == current_user.lower():
        return jsonify({'success': False, 'message': 'Non puoi eliminare il tuo stesso account'}), 400

    success, msg = delete_user(username)
    if success:
        return jsonify({'success': True, 'message': msg})
    return jsonify({'success': False, 'message': msg}), 404


@app.route('/api/admin/users/<username>/reset-password', methods=['POST'])
@admin_required
def api_admin_reset_password(username):
    """Reset password di un utente (solo admin)."""
    success, msg, new_pass = reset_password(username)
    if success:
        return jsonify({'success': True, 'message': msg, 'new_password': new_pass})
    return jsonify({'success': False, 'message': msg}), 404


@app.route('/api/admin/users/<username>/toggle', methods=['POST'])
@admin_required
def api_admin_toggle_user(username):
    """Attiva/disattiva un utente (solo admin)."""
    current_user = session.get('user', {}).get('username', '')
    if username.lower() == current_user.lower():
        return jsonify({'success': False, 'message': 'Non puoi disattivare il tuo stesso account'}), 400

    success, msg, new_state = toggle_user_active(username)
    if success:
        return jsonify({'success': True, 'message': msg, 'active': new_state})
    return jsonify({'success': False, 'message': msg}), 404


@app.route('/api/admin/users/<username>/province', methods=['PUT'])
@admin_required
def api_admin_set_province(username):
    """Imposta le province di un utente (solo admin)."""
    data = request.get_json()
    if not data or 'province' not in data:
        return jsonify({'success': False, 'message': 'Province richieste'}), 400

    success, msg = set_user_province(username, data['province'])
    if success:
        return jsonify({'success': True, 'message': msg})
    return jsonify({'success': False, 'message': msg}), 400


@app.route('/api/admin/users/<username>/ruolo', methods=['PUT'])
@admin_required
def api_admin_set_role(username):
    """Imposta il ruolo di un utente (solo admin)."""
    data = request.get_json()
    if not data or 'ruolo' not in data:
        return jsonify({'success': False, 'message': 'Ruolo richiesto'}), 400

    current_user = session.get('user', {}).get('username', '')
    if username.lower() == current_user.lower():
        return jsonify({'success': False, 'message': 'Non puoi modificare il tuo stesso ruolo'}), 400

    success, msg = set_user_role(username, data['ruolo'])
    if success:
        return jsonify({'success': True, 'message': msg})
    return jsonify({'success': False, 'message': msg}), 400


@app.route('/api/admin/db-stats', methods=['GET'])
@admin_required
def api_admin_db_stats():
    """Statistiche del DB locale SQLite + valutazione health del backlog."""
    try:
        from moduli import db as _db
        stats = _db.stats()
        # Health: rileva backlog anormale e max_attempts elevato
        conn = _db.get_conn()
        row = conn.execute(
            "SELECT COUNT(*) AS n, MAX(attempts) AS max_att "
            "FROM pending_writes WHERE state IN ('pending','in_flight')"
        ).fetchone()
        backlog = row['n'] or 0
        max_attempts = row['max_att'] or 0
        warnings = []
        if backlog >= 500:
            warnings.append(f'Backlog pending anomalo: {backlog} (soglia 500)')
        if max_attempts >= 10:
            warnings.append(f'Pending con {max_attempts} tentativi falliti (soglia 10)')
        return jsonify({
            'success': True,
            'use_db_backend': USE_DB_BACKEND,
            'stats': stats,
            'integrity': _db.integrity_check(),
            'health': {
                'backlog': backlog,
                'max_attempts': max_attempts,
                'warnings': warnings,
                'ok': not warnings,
            },
        })
    except Exception as e:
        logger.exception('[admin] errore /api/admin/db-stats')
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/admin/db-pending', methods=['GET'])
@admin_required
def api_admin_db_pending():
    """Lista pending_writes (in volo o quarantined) per ispezione."""
    try:
        from moduli import db as _db
        conn = _db.get_conn()
        state_filter = request.args.get('state', 'pending')
        limit = min(int(request.args.get('limit', 100)), 1000)
        rows = conn.execute(
            'SELECT * FROM pending_writes WHERE state=? ORDER BY id DESC LIMIT ?',
            (state_filter, limit),
        ).fetchall()
        return jsonify({
            'success': True,
            'state': state_filter,
            'count': len(rows),
            'pending': [dict(r) for r in rows],
        })
    except Exception as e:
        logger.exception('[admin] errore /api/admin/db-pending')
        return jsonify({'success': False, 'message': str(e)}), 500


# ============================================================================
# ADMIN — Dump DB (equivalenti web dei flag CLI di tools/dump_db.py)
# ============================================================================
@app.route('/api/admin/db-dump/eneltel/<eneltel>', methods=['GET'])
@admin_required
def api_admin_db_dump_eneltel(eneltel):
    """Dettagli completi su una riga: filone + filone_extra + pending + audit."""
    try:
        from moduli import db as _db
        conn = _db.get_conn()
        ek = _db.normalize_eneltel(eneltel)
        # Tutte le righe di filone per quell'eneltel (multi-LCL su Pavia)
        rows_filone = [dict(r) for r in conn.execute(
            'SELECT * FROM filone WHERE eneltel_key=? ORDER BY sheets_row',
            (ek,),
        ).fetchall()]
        rows_extra = [dict(r) for r in conn.execute(
            'SELECT * FROM filone_extra WHERE eneltel_key=? ORDER BY lcl, column_name',
            (ek,),
        ).fetchall()]
        rows_pending = [dict(r) for r in conn.execute(
            'SELECT * FROM pending_writes WHERE eneltel_key=? ORDER BY id DESC LIMIT 100',
            (ek,),
        ).fetchall()]
        rows_audit = [dict(r) for r in conn.execute(
            'SELECT * FROM audit_log WHERE eneltel_key=? ORDER BY ts DESC LIMIT 50',
            (ek,),
        ).fetchall()]
        return jsonify({
            'success': True,
            'eneltel_key': ek,
            'filone': rows_filone,
            'filone_extra': rows_extra,
            'pending_writes_recenti': rows_pending,
            'audit_log_recenti': rows_audit,
        })
    except Exception as e:
        logger.exception('[admin] errore /api/admin/db-dump/eneltel')
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/admin/db-dump/conflicts', methods=['GET'])
@admin_required
def api_admin_db_dump_conflicts():
    """Ultimi conflitti `local_wins` rilevati dal pull engine."""
    try:
        from moduli import db as _db
        conn = _db.get_conn()
        limit = min(int(request.args.get('limit', 50)), 500)
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM audit_log WHERE source='conflict_local_wins' "
            "ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()]
        return jsonify({'success': True, 'count': len(rows), 'conflicts': rows})
    except Exception as e:
        logger.exception('[admin] errore /api/admin/db-dump/conflicts')
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/admin/db-dump/columns', methods=['GET'])
@admin_required
def api_admin_db_dump_columns():
    """Column registry: mappatura nome colonna foglio → chiave interna."""
    try:
        from moduli import db as _db
        conn = _db.get_conn()
        rows = [dict(r) for r in conn.execute(
            'SELECT * FROM column_registry ORDER BY last_index'
        ).fetchall()]
        return jsonify({'success': True, 'count': len(rows), 'columns': rows})
    except Exception as e:
        logger.exception('[admin] errore /api/admin/db-dump/columns')
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/admin/db-action', methods=['POST'])
@admin_required
def api_admin_db_action():
    """Esegue una action manuale sulla provincia corrente.

    Body: { "action": "drain" | "sync" | "snapshot-db" | "validate" }
    """
    try:
        body = request.get_json(silent=True) or {}
        action = (body.get('action') or '').strip()

        if action == 'drain':
            from moduli import sync_engine as _se
            _se.trigger_drain_now()
            return jsonify({'success': True, 'action': action,
                            'message': 'Drainer risvegliato; controlla i log nei prossimi 30s.'})

        if action == 'sync':
            # Risveglia il sync_loop completo
            try:
                _sync_needed.set()
                return jsonify({'success': True, 'action': action,
                                'message': 'Sync_loop risvegliato; aggiornamento entro pochi secondi.'})
            except NameError:
                return jsonify({'success': False, 'message': '_sync_needed non disponibile'}), 500

        if action == 'snapshot-db':
            from moduli.backup import esegui_backup_db
            res = esegui_backup_db(forza=True)
            return jsonify({'success': res.get('success', False), 'action': action, **res})

        if action == 'validate':
            import subprocess
            try:
                r = subprocess.run(
                    [sys.executable, os.path.join(SCRIPT_DIR, 'tools', 'validate_shadow.py'),
                     '--quiet', '--report-dir',
                     os.path.join(SCRIPT_DIR, 'data', PROVINCIA_ATTIVA.lower(), 'shadow_reports')],
                    cwd=SCRIPT_DIR, env={**os.environ, 'PROVINCIA': PROVINCIA_ATTIVA},
                    capture_output=True, text=True, timeout=120,
                )
                return jsonify({
                    'success': r.returncode == 0,
                    'action': action,
                    'returncode': r.returncode,
                    'output': (r.stdout + r.stderr)[-2000:],
                })
            except subprocess.TimeoutExpired:
                return jsonify({'success': False, 'message': 'validator timeout (>2min)'}), 504

        return jsonify({'success': False, 'message': f"action sconosciuta: {action!r}"}), 400
    except Exception as e:
        logger.exception('[admin] errore /api/admin/db-action')
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/admin/db-dump/anomalies', methods=['GET'])
@admin_required
def api_admin_db_dump_anomalies():
    """Anomalie rilevate dal pull engine (righe senza eneltel, ecc.)."""
    try:
        from moduli import db as _db
        conn = _db.get_conn()
        limit = min(int(request.args.get('limit', 100)), 500)
        rows = [dict(r) for r in conn.execute(
            'SELECT * FROM filone_anomalies ORDER BY ts DESC LIMIT ?',
            (limit,),
        ).fetchall()]
        return jsonify({'success': True, 'count': len(rows), 'anomalies': rows})
    except Exception as e:
        logger.exception('[admin] errore /api/admin/db-dump/anomalies')
        return jsonify({'success': False, 'message': str(e)}), 500


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
    """Serve la pagina vista Excel del filone.

    Cache disabilitata: il template contiene tutto il JS della vista, e
    quando viene aggiornato vogliamo che i client ne ricarichino sempre
    la versione fresca (senza dover fare Ctrl+Shift+R)."""
    resp = send_file(os.path.join('templates', 'filone.html'))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


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
    dati_sono_cambiati,
    cambiamento_e_strutturale,
    genera_report_zone,
    genera_mappa_html
)
from config import (
    DEPOSITO,
    INTERVALLO_SYNC_SECONDI,
    USE_DB_BACKEND,
)

_sync_running = False
_sync_thread = None
_sync_lock = threading.Lock()      # Impedisce esecuzioni concorrenti di sincronizza_dati
_sync_needed = threading.Event()   # Segnala che serve un sync ASAP (dopo modifica utente)


def _release_memory_to_os():
    """Forza gc + restituzione dell'heap glibc al sistema operativo.

    Senza questo, dopo un full sync l'RSS resta alto anche dopo che gli
    oggetti pandas/numpy transitori sono stati garbage-collected: glibc
    tiene la memoria libera nell'heap pronta per riuso, ma Linux non la
    vede come 'available'. malloc_trim(0) la restituisce.
    """
    import gc
    import ctypes
    gc.collect()
    try:
        # glibc only; no-op su altri allocator (musl, jemalloc...)
        libc = ctypes.CDLL('libc.so.6')
        libc.malloc_trim(0)
    except (OSError, AttributeError):
        pass

# Cache risultati ultimo sync riuscito (per light sync su modifiche cosmetiche)
_last_report = None
_last_risultato_zone = None


def _shadow_db_pull():
    """Aggiorna il DB locale leggendo da Sheets (Fase 1 shadow).

    Idempotente, non bloccante: errori qui non devono mai bloccare il sync
    principale o sse_notify. In modalità 'off' è un no-op.
    """
    if USE_DB_BACKEND == 'off':
        return
    try:
        from moduli import sync_engine as _se
        res = _se.pull_once()
        if res.get('changed') or res.get('new') or res.get('conflicts') or res.get('orphans'):
            logger.info(f"[DB-{USE_DB_BACKEND}] pull: {res}")
    except Exception as e:
        logger.warning(f"[DB-{USE_DB_BACKEND}] pull fallito (non bloccante): {e}")


def _do_sincronizza_dati():
    """Logica effettiva di sincronizzazione (chiamare solo con _sync_lock acquisito).

    Tre livelli di ottimizzazione:
    1. Dati invariati (hash identico) → skip completo
    2. Solo modifiche cosmetiche (telefono, email, note...) → light sync:
       aggiorna solo i dati contatori nel JSON, riusa percorsi/zone in cache
    3. Modifiche strutturali (data, operatore, coordinate, appuntamenti) → full sync:
       ricalcola distanze, zone, percorsi, rigenera mappa
    """
    global _last_report, _last_risultato_zone

    logger.info(f"[SYNC {time_module.strftime('%H:%M:%S')}] {'='*50}")
    logger.info(f"[SYNC {time_module.strftime('%H:%M:%S')}] Sincronizzazione - {PROVINCIA_ATTIVA}")
    logger.info(f"[SYNC {time_module.strftime('%H:%M:%S')}] {'='*50}")
    
    start_loop = time_module.time()
    
    try:
        # Carica dati (force=True → scarica sempre dal foglio nel sync periodico)
        df = carica_dati_google_sheets(force=True)
        if df is None:
            logger.warning("[SYNC] ⚠️ Nessun dato caricato")
            sse_notify_all('sync_complete', {'source': 'sync_failed'})
            return False

        # LIVELLO 1: Se i dati non sono cambiati affatto, salta tutto
        if not dati_sono_cambiati():
            elapsed = time_module.time() - start_loop
            logger.info(f"[SYNC] ⚡ Dati invariati, skip elaborazione ({elapsed:.1f}s)")
            # Anche su no-change: pull_once aggiorna last_remote_pull_ts e
            # riconcilia eventuali cambi locali pending arrivati nel frattempo.
            _shadow_db_pull()
            sse_notify_all('sync_complete', {'source': 'no_changes'})
            return True

        # LIVELLO 2: Modifiche cosmetiche — light sync
        if not cambiamento_e_strutturale() and _last_report is not None:
            logger.info("[SYNC] 📝 Modifiche cosmetiche — light sync (riuso percorsi/zone in cache)")

            # Rigenera solo i dati contatori nel JSON + HTML, riusando percorsi e zone
            genera_mappa_html(df, _last_report, _last_risultato_zone)

            elapsed = time_module.time() - start_loop
            logger.info(f"✅ Light sync completato in {elapsed:.1f}s")

            _shadow_db_pull()
            sse_notify_all('sync_complete', {'source': 'light_sync'})
            return True

        # LIVELLO 3: Modifiche strutturali — full sync
        logger.info("[SYNC] 🔧 Modifiche strutturali — full sync (ricalcolo percorsi)")

        # Carica cache distanze
        cache_distanze, cache_hash = carica_cache()
        cache_distanze, cache_hash = pulisci_cache_vecchia(cache_distanze, cache_hash, giorni_da_mantenere=7)

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
        genera_mappa_html(df, report, risultato_zone)

        # Salva per riuso nel light sync
        _last_report = report
        _last_risultato_zone = risultato_zone

        elapsed = time_module.time() - start_loop
        logger.info(f"✅ Full sync completato in {elapsed:.1f}s")

        # Backup periodico automatico (giornaliero, con pulizia mensile)
        try:
            from moduli.backup import backup_periodico_auto
            backup_periodico_auto()
        except Exception as e_backup:
            logger.warning(f"[BACKUP] ⚠️ Errore nel backup periodico: {e_backup}")

        # Sync DB locale (Fase 1 shadow): popola/aggiorna SQLite in parallelo
        # al flusso principale. Errori qui NON devono bloccare il sync.
        _shadow_db_pull()

        # Notifica tutti i client SSE che ci sono nuovi dati
        sse_notify_all('sync_complete', {'source': 'full_sync'})

        return True

    except Exception as e:
        logger.error(f"[SYNC] ❌ Errore: {e}")
        traceback.print_exc()
        sse_notify_all('sync_complete', {'source': 'sync_error'})
        return False
    finally:
        # Restituisce al SO la memoria liberata da pandas/numpy nel sync
        _release_memory_to_os()


def sincronizza_dati():
    """Sincronizza dati con protezione da esecuzione concorrente.
    
    Blocca fino a quando il lock è disponibile, poi esegue la sync.
    Per uso non-bloccante, usare direttamente _sync_lock.acquire(blocking=False).
    """
    with _sync_lock:
        return _do_sincronizza_dati()


def background_sync_loop():
    """Loop di sincronizzazione in background.
    
    Esegue sync periodiche ogni INTERVALLO_SYNC_SECONDI.
    Si risveglia anticipatamente se _sync_needed viene segnalato (dopo modifica utente).
    Salta l'iterazione se un altro sync è già in corso.
    """
    global _sync_running
    
    logger.info(f"[SYNC] 🔄 Thread di sync avviato (intervallo: {INTERVALLO_SYNC_SECONDI}s)")
    
    while _sync_running:
        # Prova ad acquisire il lock senza bloccare
        acquired = _sync_lock.acquire(blocking=False)
        if acquired:
            try:
                _do_sincronizza_dati()
            except Exception as e:
                logger.error(f"[SYNC] ❌ Errore nel loop: {e}")
                traceback.print_exc()
            finally:
                _sync_lock.release()
        else:
            logger.info("[SYNC] ⏳ Sync periodico saltato (altro sync in corso)")
        
        # Sleep interrompibile — si risveglia se c'è un sync pendente
        _sync_needed.clear()
        for _ in range(INTERVALLO_SYNC_SECONDI):
            if not _sync_running:
                break
            if _sync_needed.wait(timeout=1.0):
                _sync_needed.clear()
                logger.info("[SYNC] ⚡ Risveglio anticipato per modifica dati")
                break
    
    logger.info("[SYNC] 🛑 Thread di sync terminato")


def avvia_sync_background():
    """Avvia il thread di sincronizzazione in background + worker DB locale."""
    global _sync_running, _sync_thread

    if _sync_running:
        return

    _sync_running = True
    _sync_thread = threading.Thread(target=background_sync_loop, daemon=True)
    _sync_thread.start()
    logger.info("[SYNC] ✅ Sync in background avviato")

    # Avvia worker DB locale (drainer pending_writes → Sheets).
    # In Fase 1 shadow è idle ma utile averlo già running per testing /
    # eventuali pending_writes inseriti manualmente.
    if USE_DB_BACKEND != 'off':
        try:
            from moduli import sync_engine as _se
            _se.start_workers()
        except Exception as e:
            logger.warning(f"[DB] start_workers fallito (non bloccante): {e}")


def trigger_sync_after_edit():
    """Triggera un sync il prima possibile dopo una modifica utente.

    In primary mode (Fase 2), prima di risvegliare il sync_loop completo
    proviamo una "patch in-place" di mappa_data.json: se la modifica utente
    riguarda SOLO campi cosmetici (email, telefono, nominativo, indirizzo,
    posizione_mis, ecc.) non c'è motivo di ricalcolare percorsi/zone/HTML.
    Aggiorniamo direttamente il JSON sul disco (~50ms) e inviamo subito SSE
    'sync_complete' al frontend.

    Per cambi STRUTTURALI (data, operatore, lat/lon, comune, note APP, esito)
    si va sul flusso classico: SSE 'data_changing' + wake del background loop.
    """
    if USE_DB_BACKEND == 'primary':
        try:
            from moduli.mappa_data_patcher import try_patch_inplace
            if try_patch_inplace():
                # Patch riuscita: niente full sync, niente flicker UI
                sse_notify_all('sync_complete', {'source': 'inplace_patch'})
                logger.info("[SYNC] ⚡ Patch in-place applicata (no full sync)")
                return
        except Exception as e:
            logger.warning(f"[SYNC] patch in-place fallita ({e}), fallback al sync_loop")

    # Path classico: notifica e wake del background loop
    sse_notify_all('data_changing', {'status': 'sync_started'})
    _sync_needed.set()
    logger.info("[SYNC] ⚡ Sync richiesto dopo modifica")


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
    is_sync_running=lambda: _sync_lock.locked(),
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
    logger.info("📊 Modalità: PRODUZIONE")
    logger.info(f"🌐 http://localhost:{FLASK_PORT}")
    logger.info("")
    
    # Avvia sync in background (farà subito la prima sincronizzazione)
    avvia_sync_background()

    # Warmup cache formattazione filone (lettura non-bloccante da Sheets)
    try:
        from routes.contatori import warmup_format_cache
        warmup_format_cache()
    except Exception as e:
        logger.warning(f"⚠️ Warmup cache formattazione fallito: {e}")

    # Warmup cache note filone
    try:
        from routes.contatori import warmup_notes_cache
        warmup_notes_cache()
    except Exception as e:
        logger.warning(f"⚠️ Warmup cache note fallito: {e}")
    
    # Pulizia boxbeat completati vecchi (> 7 giorni) per evitare accumulo
    try:
        rimossi = pulisci_boxbeat_completati(giorni_retention=7)
        if rimossi:
            logger.info(f"🧹 Pulizia: rimossi {rimossi} boxbeat completati")
    except Exception as e:
        logger.warning(f"⚠️ Errore pulizia boxbeat: {e}")
    
    # Avvia Flask
    app.run(host='0.0.0.0', port=FLASK_PORT, debug=False, threaded=True)
