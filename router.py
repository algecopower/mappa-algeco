# -*- coding: utf-8 -*-
"""
Router/supervisor multi-provincia.

Sostituisce il vecchio `--pubblico` mode di server.py:
  - Spawna un processo backend per ogni provincia configurata in
    data/provinces.json (con env vars PROVINCIA + FLASK_PORT).
  - Espone una singola porta pubblica (5050 di default) verso cui Cloudflare
    Tunnel può puntare con un'unica regola wildcard.
  - Fa reverse proxy in base all'header Host:
      torino.mappa-algeco.com → backend Torino (porta 5001)
      pavia.mappa-algeco.com  → backend Pavia (porta 5002)
      nuova.mappa-algeco.com  → backend Nuova (porta dinamica)
      admin.mappa-algeco.com  → UI di gestione province (interna al router)
  - Supporta streaming SSE (text/event-stream) per /api/events.
  - Monitora i figli e li riavvia se crashano.
  - Risponde a SIGTERM/SIGINT terminando puliamente tutti i figli.

Uso:
  python router.py                     # legge data/provinces.json
  ROUTER_PORT=5050 python router.py    # custom port

Da non confondere con server.py: server.py resta il backend per-provincia
(processo figlio spawnato dal router con _SERVER_CHILD=1).
"""
import os
import re
import sys
import json
import time
import hashlib
import signal
import socket
import logging
import subprocess
import threading
from urllib.parse import urlparse, quote

import requests
from flask import Flask, request, Response, jsonify, redirect, render_template_string

# ============================================================================
# CONFIGURAZIONE
# ============================================================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROVINCES_FILE = os.path.join(SCRIPT_DIR, 'data', 'provinces.json')
ROUTER_PORT = int(os.environ.get('ROUTER_PORT', 5050))
ROUTER_HOST = os.environ.get('ROUTER_HOST', '127.0.0.1')

# Sottodominio dedicato all'UI admin del router.
# Tutto ciò che non matcha un sottodominio di provincia ricade qui.
ADMIN_HOST_PREFIX = 'admin'

# Header che non vanno forwarded (RFC 7230 hop-by-hop)
HOP_BY_HOP_HEADERS = {
    'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
    'te', 'trailers', 'transfer-encoding', 'upgrade',
    # Lasciamo passare Host (lo riscriviamo) e Content-Length (requests lo ricalcola)
    'host', 'content-length',
}

# Tempo massimo di attesa per il primo /api/config dei backend (boot)
BACKEND_BOOT_TIMEOUT_S = 60

logging.basicConfig(
    format='[%(asctime)s] [%(levelname)s] [router] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    level=logging.INFO,
)
log = logging.getLogger('router')

app = Flask(__name__)


# ============================================================================
# REGISTRY PROVINCE
# ============================================================================
_provinces_lock = threading.Lock()


def carica_province():
    """Ritorna dict {slug_upper: cfg} dal file provinces.json."""
    with open(PROVINCES_FILE, 'r', encoding='utf-8') as f:
        raw = json.load(f)
    return {k.upper(): v for k, v in raw.items()}


def salva_province(province):
    """Scrive atomicamente provinces.json. Thread-safe."""
    with _provinces_lock:
        tmp = PROVINCES_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(province, f, indent=2, ensure_ascii=False)
        os.replace(tmp, PROVINCES_FILE)


def porta_libera_successiva(province, start=5001):
    """Trova la prima porta libera ≥ start non già usata da una provincia."""
    usate = {int(c.get('porta', 0)) for c in province.values()}
    p = start
    while p in usate:
        p += 1
    return p


# ============================================================================
# AUTH (validazione sessione via backend)
# ============================================================================
# La UI admin (admin.mappa-algeco.com) NON ha un proprio sistema di login.
# Riusa quello dei backend: l'utente fa login su torino.* o pavia.* con il
# cookie session che — grazie a SESSION_COOKIE_DOMAIN=.mappa-algeco.com — è
# visibile anche su admin.*. Il router valida l'auth chiamando /api/user su
# un backend up (cache 30s per request economici).

_auth_cache = {}            # {cookie_sha256: (expiry_ts, user_dict_or_None)}
_AUTH_CACHE_TTL_S = 30
_auth_cache_lock = threading.Lock()


def _backend_up_per_validazione():
    """Ritorna (slug, cfg) del primo backend running, o (None, None)."""
    try:
        province = carica_province()
    except Exception:
        return (None, None)
    stato = pm.stato()
    for slug, cfg in province.items():
        if stato.get(slug) == 'running':
            return (slug, cfg)
    return (None, None)


def _validate_session():
    """Ritorna user dict (con 'ruolo') se autenticato, None altrimenti.

    Cache short-TTL by cookie hash per evitare di martellare il backend
    a ogni request della UI admin.
    """
    cookie_header = request.headers.get('Cookie', '')
    if not cookie_header:
        return None

    h = hashlib.sha256(cookie_header.encode('utf-8')).hexdigest()
    now = time.time()
    with _auth_cache_lock:
        cached = _auth_cache.get(h)
        if cached and cached[0] > now:
            return cached[1]

    slug, cfg = _backend_up_per_validazione()
    if not cfg:
        log.warning("nessun backend running per validare sessione admin")
        return None

    try:
        r = requests.get(
            f'http://127.0.0.1:{cfg["porta"]}/api/user',
            headers={'Cookie': cookie_header},
            timeout=3,
        )
        user = None
        if r.status_code == 200:
            data = r.json()
            if data.get('authenticated'):
                user = data.get('user')
    except requests.RequestException as e:
        log.warning(f"errore validazione sessione: {e}")
        user = None

    with _auth_cache_lock:
        _auth_cache[h] = (now + _AUTH_CACHE_TTL_S, user)
    return user


def _is_admin():
    """True se sessione corrente è admin."""
    user = _validate_session()
    return bool(user and user.get('ruolo') == 'admin')


def _login_url():
    """URL della pagina di login del primo backend (per redirect)."""
    slug, cfg = _backend_up_per_validazione()
    if cfg and cfg.get('sottodominio'):
        next_url = request.url
        return f"https://{cfg['sottodominio']}/login?next={quote(next_url, safe='')}"
    return '/login'  # fallback


def _require_admin_or_redirect():
    """Ritorna None se admin, altrimenti una Response (redirect o 401/403)."""
    user = _validate_session()
    if not user:
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Autenticazione richiesta'}), 401
        return redirect(_login_url())
    if user.get('ruolo') != 'admin':
        return jsonify({
            'error': 'Permessi insufficienti',
            'utente': user.get('username'),
            'ruolo': user.get('ruolo'),
        }), 403
    return None


# ============================================================================
# PROCESS MANAGER
# ============================================================================
class ProcessManager:
    """Gestisce i processi backend (uno per provincia)."""

    def __init__(self):
        self._proc = {}              # {slug: subprocess.Popen}
        self._lock = threading.Lock()
        self._shutdown_requested = False

    def avvia_tutte(self, province):
        """Spawna i backend per ogni provincia + aspetta che siano pronti."""
        for slug, cfg in province.items():
            self.avvia_provincia(slug, cfg)
        for slug, cfg in province.items():
            if not self._aspetta_backend(cfg['porta'], BACKEND_BOOT_TIMEOUT_S):
                log.warning(f"[{slug}] backend non risponde dopo {BACKEND_BOOT_TIMEOUT_S}s "
                            f"(porta {cfg['porta']}). Provo a continuare comunque.")
            else:
                log.info(f"[{slug}] ✅ backend ready su porta {cfg['porta']}")

    def avvia_provincia(self, slug, cfg):
        """Spawna un singolo backend. Idempotente: non rispawna se già vivo.

        Prima di spawnare aspetta che la porta sia bindabile: dopo un restart
        sistemico (systemctl restart) i backend del vecchio router muoiono ma
        il kernel può tenere il socket in TIME_WAIT per qualche secondo. Senza
        il wait il nuovo backend fallirebbe il bind con "Address already in use".
        """
        with self._lock:
            existing = self._proc.get(slug)
            if existing and existing.poll() is None:
                log.info(f"[{slug}] backend già attivo (pid={existing.pid}), skip")
                return existing

            porta = int(cfg.get('porta', 0))
            if porta and not self._aspetta_porta_libera(porta, timeout=15):
                log.warning(f"[{slug}] porta {porta} ancora occupata dopo 15s, "
                            f"spawn tentato comunque (potrebbe fallire)")

            env = os.environ.copy()
            env['PROVINCIA'] = slug.upper()
            env['FLASK_PORT'] = str(cfg['porta'])
            env['_SERVER_CHILD'] = '1'

            log.info(f"[{slug}] avvio backend → porta {cfg['porta']}")
            proc = subprocess.Popen(
                [sys.executable, 'server.py', '--skip-osrm-check'],
                env=env, cwd=SCRIPT_DIR,
            )
            self._proc[slug] = proc
            return proc

    def termina_provincia(self, slug, timeout=10):
        """Manda SIGTERM al backend di una provincia e attende."""
        with self._lock:
            proc = self._proc.get(slug)
            if not proc:
                return
            if proc.poll() is not None:
                self._proc.pop(slug, None)
                return
            log.info(f"[{slug}] terminazione backend (pid={proc.pid})")
            proc.terminate()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                log.warning(f"[{slug}] non risponde a SIGTERM, kill")
                proc.kill()
            self._proc.pop(slug, None)

    def termina_tutte(self):
        """Shutdown completo: SIGTERM a tutti, poi kill se non rispondono."""
        self._shutdown_requested = True
        slugs = list(self._proc.keys())
        for s in slugs:
            self.termina_provincia(s, timeout=10)

    def stato(self):
        """Ritorna dict {slug: 'running'|'stopped'} per diagnostica."""
        out = {}
        with self._lock:
            for slug, proc in self._proc.items():
                out[slug] = 'running' if proc.poll() is None else f'exited:{proc.returncode}'
        return out

    def watch_loop(self):
        """Thread che monitora i backend e li riavvia se muoiono."""
        province = carica_province()  # snapshot iniziale; aggiornato nel loop
        while not self._shutdown_requested:
            time.sleep(5)
            try:
                province = carica_province()  # rilegge per pick up nuove province
            except Exception as e:
                log.warning(f"watch: errore lettura provinces.json: {e}")
                continue
            with self._lock:
                morti = []
                for slug, proc in self._proc.items():
                    if proc.poll() is not None:
                        morti.append(slug)
                        log.warning(f"[{slug}] backend morto (exit {proc.returncode})")
            for slug in morti:
                if slug in province:
                    log.info(f"[{slug}] auto-restart…")
                    self._proc.pop(slug, None)
                    porta = int(province[slug].get('porta', 0))
                    if porta:
                        self._aspetta_porta_libera(porta, timeout=10)
                    self.avvia_provincia(slug, province[slug])

    @staticmethod
    def _aspetta_backend(porta, timeout):
        """Polling TCP connect su porta locale + GET /api/config."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with socket.create_connection(('127.0.0.1', porta), timeout=1):
                    # Porta aperta; verifica anche risposta HTTP
                    try:
                        r = requests.get(f'http://127.0.0.1:{porta}/api/config', timeout=2)
                        if r.status_code < 500:
                            return True
                    except requests.RequestException:
                        pass
            except (OSError, socket.timeout):
                pass
            time.sleep(0.5)
        return False

    @staticmethod
    def _aspetta_porta_libera(porta, timeout=15):
        """Attende che la porta sia bindabile (post-terminate dei backend).

        Dopo un SIGTERM/SIGKILL del backend il kernel può tenere il socket in
        TIME_WAIT / CLOSE_WAIT per qualche secondo. Tentiamo il bind: se ci
        riusciamo la porta è davvero libera. Ritorna True se libera entro
        timeout, False altrimenti.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                # Test bind: se ci riesce, la porta è disponibile per il prossimo bind.
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(('0.0.0.0', porta))
                s.close()
                return True
            except OSError:
                pass
            time.sleep(0.3)
        return False


pm = ProcessManager()


# ============================================================================
# PROXY
# ============================================================================
def _provincia_da_host(host_header):
    """Estrae lo slug provincia dal header Host.

    Ritorna (slug, cfg) oppure (None, None) se non match.
    Casi speciali: admin.mappa-algeco.com → ('__admin__', None).
    """
    if not host_header:
        return (None, None)
    # Rimuovi eventuale :porta
    hostname = host_header.split(':', 1)[0].lower().strip()
    # Cerca subdomain.dominio
    parts = hostname.split('.')
    if len(parts) < 2:
        return (None, None)
    sub = parts[0]
    if sub == ADMIN_HOST_PREFIX:
        return ('__admin__', None)
    # Cerca corrispondenza per `sottodominio` nelle province
    try:
        province = carica_province()
    except Exception as e:
        log.warning(f"errore lettura provinces.json: {e}")
        return (None, None)
    for slug, cfg in province.items():
        sd = (cfg.get('sottodominio') or '').lower()
        if sd == hostname:
            return (slug, cfg)
        # Match più rilassato: confronta solo la prima label
        if sd.split('.', 1)[0] == sub:
            return (slug, cfg)
    return (None, None)


def _filter_headers_to_backend(headers):
    """Headers in uscita verso backend: rimuovi hop-by-hop."""
    out = {}
    for k, v in headers.items():
        if k.lower() in HOP_BY_HOP_HEADERS:
            continue
        out[k] = v
    return out


def _filter_headers_from_backend(headers):
    """Headers in risposta dal backend: rimuovi hop-by-hop."""
    return [(k, v) for k, v in headers.items() if k.lower() not in HOP_BY_HOP_HEADERS]


def _proxy_to_backend(porta, path, querystring):
    """Inoltra request corrente al backend `127.0.0.1:porta`.

    Streaming-aware (SSE: il backend invia event-stream long-lived).
    """
    target_url = f'http://127.0.0.1:{porta}{path}'
    if querystring:
        target_url += '?' + querystring

    headers = _filter_headers_to_backend(request.headers)
    # Forza Host header verso backend localhost
    headers['Host'] = f'127.0.0.1:{porta}'

    method = request.method
    body_iter = request.get_data()  # blob; abbastanza per le size tipiche

    try:
        upstream = requests.request(
            method,
            target_url,
            headers=headers,
            data=body_iter,
            cookies=request.cookies,
            stream=True,
            allow_redirects=False,
            timeout=(10, 600),  # connect, read (10min per SSE long-lived)
        )
    except requests.ConnectionError:
        return Response(f'Backend non raggiungibile su porta {porta}', status=502)
    except requests.RequestException as e:
        log.exception(f"errore proxy verso porta {porta}: {e}")
        return Response(f'Errore proxy: {e}', status=502)

    # Streaming: passa chunk-per-chunk senza accumulare in RAM.
    # chunk_size=None usa la dimensione naturale dei chunk HTTP del backend,
    # cruciale per SSE dove ogni evento è un piccolo blocco.
    def generate():
        try:
            for chunk in upstream.iter_content(chunk_size=None):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    headers_out = _filter_headers_from_backend(upstream.headers)
    return Response(generate(), status=upstream.status_code, headers=headers_out)


# ============================================================================
# ROUTES
# ============================================================================
@app.route('/', defaults={'path': ''}, methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS', 'HEAD'])
@app.route('/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS', 'HEAD'])
def catch_all(path):
    slug, cfg = _provincia_da_host(request.headers.get('Host', ''))

    if slug == '__admin__':
        return _admin_dispatch(path)

    if slug is None or cfg is None:
        return jsonify({
            'error': 'Sottodominio non riconosciuto',
            'host': request.headers.get('Host', ''),
            'hint': f'Configurare provincia in {PROVINCES_FILE} oppure usare un sottodominio valido.',
        }), 404

    return _proxy_to_backend(cfg['porta'], '/' + path, request.query_string.decode())


# ============================================================================
# ADMIN — dispatcher + endpoints (servito solo via admin.* hostname)
# ============================================================================
SLUG_RE = re.compile(r'^[A-Z][A-Z0-9_]{1,30}$')


def _admin_dispatch(path):
    """Routing manuale per il sotto-tree admin.

    Tutti gli endpoint admin richiedono ruolo=admin (validato via cookie
    sessione contro un backend up). Le rotte /api/* rispondono JSON; quelle
    HTML reindirizzano alla login del backend se non autenticate.
    """
    p = path.strip('/')
    method = request.method

    # 1. UI (HTML)
    if p == '' or p == 'index.html':
        guard = _require_admin_or_redirect()
        if guard is not None:
            return guard
        return _admin_render_ui()

    # 2. API
    if p == 'api/provinces' and method == 'GET':
        return _admin_check() or _admin_list_provinces()
    if p == 'api/provinces' and method == 'POST':
        return _admin_check() or _admin_create_province()
    m = re.match(r'^api/provinces/([A-Z0-9_]+)$', p)
    if m and method == 'DELETE':
        return _admin_check() or _admin_delete_province(m.group(1))
    m = re.match(r'^api/provinces/([A-Z0-9_]+)/(start|stop|restart)$', p)
    if m and method == 'POST':
        return _admin_check() or _admin_lifecycle(m.group(1), m.group(2))
    if p == 'api/status' and method == 'GET':
        return _admin_check() or jsonify({
            'router_port': ROUTER_PORT,
            'backends': pm.stato(),
        })
    if p == 'api/probe-sheet' and method == 'POST':
        return _admin_check() or _admin_probe_sheet()
    m = re.match(r'^api/provinces/([A-Z0-9_]+)/bootstrap$', p)
    if m and method == 'POST':
        return _admin_check() or _admin_bootstrap(m.group(1))

    return jsonify({'error': 'admin endpoint non trovato', 'path': p}), 404


# ============================================================================
# ADMIN — probe Google Sheet (validazione + suggerimento mapping colonne)
# ============================================================================
def _admin_probe_sheet():
    """Apre il foglio Google indicato e ritorna headers + mapping suggerito.

    Body: { id_foglio, gid_scheda }
    Risposta: {
      ok: bool, headers: [str], suggested_mapping: { chiave_interna: nome_colonna_o_null },
      missing: [chiave_interna], errore: str|null
    }
    """
    body = request.get_json(silent=True) or {}
    id_foglio = (body.get('id_foglio') or '').strip()
    gid_scheda = body.get('gid_scheda')
    if not id_foglio:
        return jsonify({'ok': False, 'errore': 'id_foglio mancante'}), 400

    try:
        import gspread
        from oauth2client.service_account import ServiceAccountCredentials
    except ImportError as e:
        return jsonify({'ok': False, 'errore': f'gspread non disponibile: {e}'}), 500

    creds_path = os.path.join(SCRIPT_DIR, 'credenziali.json')
    if not os.path.exists(creds_path):
        return jsonify({'ok': False, 'errore': 'credenziali.json non trovato'}), 500

    try:
        scope = [
            'https://spreadsheets.google.com/feeds',
            'https://www.googleapis.com/auth/drive',
        ]
        creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
        client = gspread.authorize(creds)
        ss = client.open_by_key(id_foglio)
        # Risolvi worksheet via gid se fornito, altrimenti primo
        ws = None
        if gid_scheda is not None and gid_scheda != '':
            try:
                gid = int(gid_scheda)
                for w in ss.worksheets():
                    if w.id == gid:
                        ws = w
                        break
            except (ValueError, TypeError):
                pass
        if ws is None:
            ws = ss.sheet1
        headers = ws.row_values(1)
    except Exception as e:
        return jsonify({'ok': False, 'errore': f'lettura foglio fallita: {e.__class__.__name__}: {e}'}), 400

    # Suggerisci mapping via MAPPATURA_COLONNE_DEFAULT
    try:
        from config import MAPPATURA_COLONNE_DEFAULT
    except ImportError:
        MAPPATURA_COLONNE_DEFAULT = {}

    headers_lower = {h.strip().lower(): h for h in headers if h}
    suggested = {}
    missing = []
    for chiave, info in MAPPATURA_COLONNE_DEFAULT.items():
        sinonimi = info.get('nomi_default', [])
        match = None
        for nome in sinonimi:
            if nome.strip().lower() in headers_lower:
                match = headers_lower[nome.strip().lower()]
                break
        suggested[chiave] = match
        if match is None and chiave in ('eneltel', 'pod', 'nominativo'):
            # campi critici
            missing.append(chiave)

    return jsonify({
        'ok': True,
        'foglio': ss.title,
        'sheet_name': ws.title,
        'headers': headers,
        'n_colonne': len(headers),
        'suggested_mapping': suggested,
        'missing_critici': missing,
    })


# ============================================================================
# ADMIN — bootstrap DB iniziale per nuova provincia
# ============================================================================
def _admin_bootstrap(slug):
    """Esegue tools/migrate_to_sqlite.py come subprocess per la provincia.

    Sincrono: aspetta il completamento + ritorna stdout/stderr. Per province
    grandi il pull iniziale può richiedere 30-60s — il client deve usare
    timeout adeguato (almeno 120s).
    """
    slug = slug.upper()
    try:
        province = carica_province()
    except Exception as e:
        return jsonify({'ok': False, 'errore': f'lettura provinces.json fallita: {e}'}), 500
    if slug not in province:
        return jsonify({'ok': False, 'errore': f'provincia {slug} non trovata'}), 404

    env = os.environ.copy()
    env['PROVINCIA'] = slug

    log.info(f"[admin] bootstrap DB per {slug}")
    try:
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPT_DIR, 'tools', 'migrate_to_sqlite.py')],
            env=env, cwd=SCRIPT_DIR,
            capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        return jsonify({'ok': False, 'errore': 'bootstrap timeout (>5min)'}), 504

    output = result.stdout + ('\n[STDERR]\n' + result.stderr if result.stderr else '')
    if result.returncode != 0:
        return jsonify({
            'ok': False,
            'errore': f'bootstrap fallito (exit {result.returncode})',
            'output': output[-4000:],
        }), 500
    return jsonify({
        'ok': True,
        'slug': slug,
        'output': output[-4000:],
        'note': 'DB inizializzato. Clicca "Avvia" per partire il backend.',
    })


def _admin_check():
    """Helper: ritorna response 401/403 se non admin, None altrimenti.
    Usato dai handler API che rispondono in JSON.
    """
    return _require_admin_or_redirect()


def _admin_list_provinces():
    try:
        province = carica_province()
    except Exception as e:
        return jsonify({'error': f'lettura provinces.json fallita: {e}'}), 500
    stato = pm.stato()
    out = []
    for slug, cfg in province.items():
        item = dict(cfg)
        item['slug'] = slug
        item['stato'] = stato.get(slug, 'stopped')
        # Heuristica: DB bootstrappato se state.db esiste e ha > 32KB
        # (uno schema vuoto pesa ~32KB, quindi >32KB significa che ci sono dati).
        db_path = os.path.join(SCRIPT_DIR, 'data', slug.lower(), 'state.db')
        item['db_exists'] = os.path.exists(db_path) and os.path.getsize(db_path) > 32 * 1024
        out.append(item)
    return jsonify({'province': out})


def _admin_create_province():
    body = request.get_json(silent=True) or {}
    slug = (body.get('slug') or '').strip().upper()
    if not SLUG_RE.match(slug):
        return jsonify({'error': "slug non valido (A-Z, 0-9, _, 2-31 chars, deve iniziare con lettera)"}), 400
    try:
        province = carica_province()
    except Exception as e:
        return jsonify({'error': f'lettura provinces.json fallita: {e}'}), 500
    if slug in province:
        return jsonify({'error': f'provincia {slug} già esistente'}), 409

    cfg = {
        'nome': body.get('nome') or slug.capitalize(),
        'id_foglio': body.get('id_foglio') or '',
        'gid_scheda': int(body.get('gid_scheda') or 0),
        'deposito': body.get('deposito') or {'Nome': '', 'Lat': 0.0, 'Lon': 0.0},
        'operatori_vip': body.get('operatori_vip') or [],
        'colori_operatori': body.get('colori_operatori') or {},
        'operatori_dict': body.get('operatori_dict') or {},
        'file_mappa': os.path.join('templates', f'mappa_{slug.lower()}.html'),
        'ora_inizio_giornata': int(body.get('ora_inizio_giornata') or 480),
        'ora_fine_giornata': int(body.get('ora_fine_giornata') or 1020),
        'sottodominio': body.get('sottodominio') or f'{slug.lower()}.mappa-algeco.com',
        'porta': int(body.get('porta') or porta_libera_successiva(province)),
    }
    if body.get('usa_localita_come_comune'):
        cfg['usa_localita_come_comune'] = True

    # Valida campi minimi
    if not cfg['id_foglio']:
        return jsonify({'error': 'id_foglio mancante (ID del Google Sheet)'}), 400

    province[slug] = cfg
    salva_province(province)
    log.info(f"[admin] provincia {slug} aggiunta (porta {cfg['porta']})")

    # Creiamo la dir locale; il bootstrap completo (DB, settings) avviene in
    # Step 4. Per ora: dir vuota + provincia spawn-abile via "Avvia".
    data_dir = os.path.join(SCRIPT_DIR, 'data', slug.lower())
    os.makedirs(data_dir, exist_ok=True)

    return jsonify({'ok': True, 'slug': slug, 'cfg': cfg}), 201


def _admin_delete_province(slug):
    slug = slug.upper()
    try:
        province = carica_province()
    except Exception as e:
        return jsonify({'error': f'lettura provinces.json fallita: {e}'}), 500
    if slug not in province:
        return jsonify({'error': f'provincia {slug} non trovata'}), 404
    # Ferma il backend prima di rimuovere
    pm.termina_provincia(slug)
    del province[slug]
    salva_province(province)
    log.info(f"[admin] provincia {slug} rimossa da provinces.json (data/{slug.lower()}/ NON eliminato per sicurezza)")
    return jsonify({'ok': True, 'slug': slug,
                    'note': f'data/{slug.lower()}/ conservato — elimina manualmente se vuoi liberare lo spazio'})


def _admin_lifecycle(slug, action):
    slug = slug.upper()
    try:
        province = carica_province()
    except Exception as e:
        return jsonify({'error': f'lettura provinces.json fallita: {e}'}), 500
    if slug not in province:
        return jsonify({'error': f'provincia {slug} non trovata'}), 404
    cfg = province[slug]

    if action == 'start':
        pm.avvia_provincia(slug, cfg)
    elif action == 'stop':
        pm.termina_provincia(slug)
    elif action == 'restart':
        pm.termina_provincia(slug)
        # Aspetta che la porta sia davvero libera (post SIGTERM/SIGKILL il
        # kernel può tenerla in TIME_WAIT per qualche secondo).
        porta = int(cfg.get('porta', 0))
        if porta and not pm._aspetta_porta_libera(porta, timeout=15):
            log.warning(f"[admin] {slug}: porta {porta} ancora occupata dopo 15s, "
                        f"tento spawn comunque")
        pm.avvia_provincia(slug, cfg)

    log.info(f"[admin] {slug}: {action}")
    return jsonify({'ok': True, 'slug': slug, 'action': action, 'stato': pm.stato().get(slug, 'unknown')})


# ============================================================================
# ADMIN UI (inline HTML — single-file admin app)
# ============================================================================
_ADMIN_HTML = r"""
<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<title>Mappa Algeco — Gestione Province</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; background: #f5f6f8; color: #1f2329; }
  header { background: #1f2937; color: white; padding: 16px 24px; display: flex; align-items: center; justify-content: space-between; box-shadow: 0 2px 4px rgba(0,0,0,.1); }
  header h1 { margin: 0; font-size: 18px; font-weight: 600; }
  header .user { font-size: 13px; opacity: .8; }
  main { max-width: 1100px; margin: 24px auto; padding: 0 16px; }
  .toolbar { display: flex; gap: 8px; margin-bottom: 16px; align-items: center; flex-wrap: wrap; }
  .btn { background: #2563eb; color: white; border: 0; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-size: 14px; font-weight: 500; }
  .btn:hover { background: #1d4ed8; }
  .btn.secondary { background: #6b7280; }
  .btn.secondary:hover { background: #4b5563; }
  .btn.danger { background: #dc2626; }
  .btn.danger:hover { background: #b91c1c; }
  .btn.success { background: #059669; }
  .btn.success:hover { background: #047857; }
  .btn:disabled { opacity: .5; cursor: not-allowed; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; }
  .card { background: white; border-radius: 8px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
  .card-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
  .card-title { font-size: 16px; font-weight: 600; }
  .badge { padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 600; text-transform: uppercase; }
  .badge.running { background: #d1fae5; color: #065f46; }
  .badge.stopped { background: #fee2e2; color: #991b1b; }
  .badge.unknown { background: #e5e7eb; color: #4b5563; }
  .field { font-size: 13px; color: #6b7280; margin: 4px 0; }
  .field strong { color: #1f2329; font-weight: 500; }
  .actions { display: flex; gap: 6px; margin-top: 12px; flex-wrap: wrap; }
  .actions .btn { padding: 5px 10px; font-size: 12px; }
  .modal { position: fixed; inset: 0; background: rgba(0,0,0,.4); display: none; align-items: center; justify-content: center; z-index: 100; }
  .modal.open { display: flex; }
  .modal-content { background: white; border-radius: 8px; padding: 24px; max-width: 600px; width: 90%; max-height: 90vh; overflow-y: auto; }
  .modal-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
  .modal-head h2 { margin: 0; font-size: 18px; }
  .close { background: transparent; border: 0; font-size: 24px; cursor: pointer; color: #6b7280; }
  .form-group { margin-bottom: 14px; }
  .form-group label { display: block; font-size: 13px; font-weight: 500; margin-bottom: 4px; color: #374151; }
  .form-group input, .form-group select { width: 100%; padding: 8px 10px; border: 1px solid #d1d5db; border-radius: 4px; font-size: 14px; box-sizing: border-box; }
  .form-group input:focus { outline: none; border-color: #2563eb; box-shadow: 0 0 0 3px rgba(37, 99, 235, .1); }
  .form-row { display: flex; gap: 12px; }
  .form-row .form-group { flex: 1; }
  .help { font-size: 11px; color: #6b7280; margin-top: 2px; }
  .toast { position: fixed; bottom: 24px; right: 24px; background: #1f2937; color: white; padding: 12px 18px; border-radius: 6px; box-shadow: 0 4px 12px rgba(0,0,0,.2); z-index: 200; transform: translateY(20px); opacity: 0; transition: all .2s; max-width: 400px; font-size: 13px; }
  .toast.show { transform: translateY(0); opacity: 1; }
  .toast.error { background: #dc2626; }
  .empty { text-align: center; padding: 60px 20px; color: #6b7280; }
  .empty h2 { margin-bottom: 8px; color: #1f2329; }
</style>
</head>
<body>
<header>
  <h1>🗺️  Mappa Algeco — Gestione Province</h1>
  <div class="user" id="user-info">…</div>
</header>
<main>
  <div class="toolbar">
    <button class="btn" onclick="apriFormAggiungi()">➕ Aggiungi provincia</button>
    <button class="btn secondary" onclick="caricaProvince()">🔄 Aggiorna</button>
  </div>
  <div id="grid" class="grid"></div>
  <div id="empty" class="empty" style="display:none">
    <h2>Nessuna provincia configurata</h2>
    <p>Clicca "Aggiungi provincia" per crearne una.</p>
  </div>
</main>

<!-- Modal Aggiungi -->
<div class="modal" id="modalAggiungi">
  <div class="modal-content">
    <div class="modal-head">
      <h2>Aggiungi provincia</h2>
      <button class="close" onclick="chiudiModal()">&times;</button>
    </div>
    <form id="formAggiungi" onsubmit="submitAggiungi(event)">
      <div class="form-row">
        <div class="form-group">
          <label>Slug (codice interno)</label>
          <input name="slug" required pattern="[A-Za-z][A-Za-z0-9_]{1,30}" placeholder="es. LECCO" oninput="onSlugChange(this)">
          <div class="help">Maiuscole, lettere+numeri+underscore. Sarà la chiave nel JSON.</div>
        </div>
        <div class="form-group">
          <label>Nome leggibile</label>
          <input name="nome" required placeholder="es. Lecco">
        </div>
      </div>

      <div class="form-group">
        <label>Sottodominio</label>
        <input name="sottodominio" required placeholder="lecco.mappa-algeco.com">
        <div class="help">Crea il record DNS corrispondente in Cloudflare.</div>
      </div>

      <div class="form-row">
        <div class="form-group">
          <label>ID Google Sheet</label>
          <input name="id_foglio" required placeholder="1ABCdef...">
          <div class="help">URL del foglio: docs.google.com/spreadsheets/d/<b>ID</b>/edit</div>
        </div>
        <div class="form-group">
          <label>GID scheda</label>
          <input name="gid_scheda" type="number" required placeholder="0">
          <div class="help">Visibile nell'URL come #gid=…</div>
        </div>
      </div>

      <div class="form-group">
        <button type="button" class="btn secondary" onclick="verificaFoglio()">🔍 Verifica foglio</button>
        <div id="probe-result" style="margin-top: 8px;"></div>
      </div>

      <div class="form-group">
        <label>Deposito — Nome</label>
        <input name="deposito_nome" required placeholder="DEPOSITO X">
      </div>
      <div class="form-row">
        <div class="form-group">
          <label>Latitudine</label>
          <input name="deposito_lat" type="number" step="any" required placeholder="45.0">
        </div>
        <div class="form-group">
          <label>Longitudine</label>
          <input name="deposito_lon" type="number" step="any" required placeholder="9.0">
        </div>
      </div>

      <div class="form-row">
        <div class="form-group">
          <label>Orario inizio (minuti da 00:00)</label>
          <input name="ora_inizio_giornata" type="number" required value="480">
          <div class="help">480 = 8:00, 540 = 9:00</div>
        </div>
        <div class="form-group">
          <label>Orario fine (minuti da 00:00)</label>
          <input name="ora_fine_giornata" type="number" required value="1020">
          <div class="help">1020 = 17:00</div>
        </div>
      </div>

      <div class="form-group">
        <label><input type="checkbox" name="usa_localita_come_comune"> Usa "Località" come Comune (raggruppamento zone)</label>
        <div class="help">Attiva se il foglio non ha la colonna Comune popolata e va usata Località.</div>
      </div>

      <div class="form-row">
        <div class="form-group">
          <label>Porta (auto se vuoto)</label>
          <input name="porta" type="number" placeholder="auto">
        </div>
      </div>

      <div style="display:flex; gap:8px; justify-content:flex-end; margin-top: 20px;">
        <button type="button" class="btn secondary" onclick="chiudiModal()">Annulla</button>
        <button type="submit" class="btn">Crea provincia</button>
      </div>
    </form>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
const toastEl = document.getElementById('toast');
function toast(msg, isError) {
  toastEl.textContent = msg;
  toastEl.classList.toggle('error', !!isError);
  toastEl.classList.add('show');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => toastEl.classList.remove('show'), 4000);
}

async function api(method, url, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin' };
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch(url, opts);
  let data;
  try { data = await r.json(); } catch { data = {}; }
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

async function caricaProvince() {
  try {
    const { province } = await api('GET', '/api/provinces');
    rendiGrid(province);
  } catch (e) {
    toast('Errore caricamento province: ' + e.message, true);
  }
}

function rendiGrid(province) {
  const grid = document.getElementById('grid');
  const empty = document.getElementById('empty');
  grid.innerHTML = '';
  if (!province.length) { empty.style.display = ''; return; }
  empty.style.display = 'none';
  for (const p of province) {
    const card = document.createElement('div');
    card.className = 'card';
    const stato = p.stato || 'unknown';
    const badgeClass = stato === 'running' ? 'running' : (stato.startsWith('exited') ? 'stopped' : 'unknown');
    card.innerHTML = `
      <div class="card-head">
        <div class="card-title">${escapeHtml(p.nome)} <small style="color:#9ca3af;font-weight:400;">(${escapeHtml(p.slug)})</small></div>
        <span class="badge ${badgeClass}">${stato}</span>
      </div>
      <div class="field"><strong>URL:</strong> ${escapeHtml(p.sottodominio || '—')}</div>
      <div class="field"><strong>Porta:</strong> ${p.porta}</div>
      <div class="field"><strong>ID Foglio:</strong> <span style="font-family:monospace;font-size:11px;">${escapeHtml((p.id_foglio || '').slice(0, 20))}…</span></div>
      <div class="field"><strong>Deposito:</strong> ${escapeHtml(p.deposito && p.deposito.Nome || '—')}</div>
      <div class="actions">
        ${!p.db_exists
          ? `<button class="btn success" onclick="bootstrap('${p.slug}')">📥 Inizializza DB</button>`
          : (stato === 'running'
              ? `<button class="btn secondary" onclick="azione('${p.slug}','restart')">🔄 Riavvia</button>
                 <button class="btn danger" onclick="azione('${p.slug}','stop')">⏸ Ferma</button>`
              : `<button class="btn success" onclick="azione('${p.slug}','start')">▶ Avvia</button>`
            )
        }
        <button class="btn danger" onclick="elimina('${p.slug}')">🗑️ Elimina</button>
      </div>
      ${!p.db_exists ? '<div class="help" style="margin-top:8px;color:#dc2626;">⚠️ DB non ancora inizializzato. Clicca "Inizializza DB" per il primo pull dal foglio.</div>' : ''}
    `;
    grid.appendChild(card);
  }
}

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

async function azione(slug, action) {
  try {
    await api('POST', `/api/provinces/${slug}/${action}`);
    toast(`${slug}: ${action} OK`);
    setTimeout(caricaProvince, 1000);
  } catch (e) {
    toast(`Errore ${action}: ${e.message}`, true);
  }
}

async function elimina(slug) {
  if (!confirm(`Eliminare la provincia ${slug}? Il processo viene fermato e l'entry rimossa da provinces.json. La cartella data/${slug.toLowerCase()}/ verrà conservata.`)) return;
  try {
    const r = await api('DELETE', `/api/provinces/${slug}`);
    toast(`${slug} eliminato. ${r.note || ''}`);
    caricaProvince();
  } catch (e) {
    toast('Errore eliminazione: ' + e.message, true);
  }
}

function apriFormAggiungi() {
  document.getElementById('modalAggiungi').classList.add('open');
}
function chiudiModal() {
  document.getElementById('modalAggiungi').classList.remove('open');
  document.getElementById('formAggiungi').reset();
}
function onSlugChange(input) {
  const slug = input.value.trim().toLowerCase();
  if (slug && !input._editedSub) {
    document.querySelector('[name=sottodominio]').value = `${slug}.mappa-algeco.com`;
  }
}
document.querySelector('[name=sottodominio]').addEventListener('input', e => e.target._editedSub = true);

async function verificaFoglio() {
  const id_foglio = document.querySelector('[name=id_foglio]').value.trim();
  const gid_scheda = document.querySelector('[name=gid_scheda]').value.trim();
  const out = document.getElementById('probe-result');
  if (!id_foglio) { out.innerHTML = '<span style="color:#dc2626">Inserisci prima l\'ID del foglio.</span>'; return; }
  out.innerHTML = '<span style="color:#6b7280">⏳ Verifica in corso...</span>';
  try {
    const r = await api('POST', '/api/probe-sheet', { id_foglio, gid_scheda: gid_scheda || null });
    let html = `<div style="background:#d1fae5;color:#065f46;padding:8px;border-radius:4px;font-size:13px;">
      ✅ Foglio trovato: <b>${escapeHtml(r.foglio)}</b> / scheda <b>${escapeHtml(r.sheet_name)}</b> (${r.n_colonne} colonne)
    </div>`;
    if (r.missing_critici && r.missing_critici.length) {
      html += `<div style="background:#fee2e2;color:#991b1b;padding:8px;border-radius:4px;font-size:13px;margin-top:6px;">
        ⚠️ Colonne critiche non trovate: <b>${r.missing_critici.join(', ')}</b><br>
        Dovrai mappare manualmente queste colonne nelle Impostazioni dopo aver avviato la provincia.
      </div>`;
    }
    // Mostra mapping dedotto compattamente
    const mapped = Object.entries(r.suggested_mapping || {}).filter(([k,v]) => v).map(([k,v]) => `${k}→${v}`);
    if (mapped.length) {
      html += `<details style="margin-top:6px;font-size:12px;color:#4b5563;"><summary>Mapping dedotto (${mapped.length})</summary>
        <div style="font-family:monospace;padding:6px 0;line-height:1.5;">${escapeHtml(mapped.join(' · '))}</div></details>`;
    }
    out.innerHTML = html;
  } catch (e) {
    out.innerHTML = `<div style="background:#fee2e2;color:#991b1b;padding:8px;border-radius:4px;font-size:13px;">❌ ${escapeHtml(e.message)}</div>`;
  }
}

async function bootstrap(slug) {
  if (!confirm(`Inizializzare il DB locale per ${slug}? Questa operazione scarica TUTTI i contatori dal foglio Google e popola data/${slug.toLowerCase()}/state.db. Può richiedere 30-60 secondi per fogli grandi.`)) return;
  toast(`⏳ Inizializzazione DB ${slug} in corso (può richiedere 30-60s)...`);
  try {
    const r = await api('POST', `/api/provinces/${slug}/bootstrap`);
    toast(`✅ DB ${slug} inizializzato. Ora clicca "Avvia".`);
    setTimeout(caricaProvince, 500);
  } catch (e) {
    toast('Errore bootstrap: ' + e.message, true);
  }
}

async function submitAggiungi(ev) {
  ev.preventDefault();
  const f = ev.target;
  const fd = new FormData(f);
  const body = {
    slug: fd.get('slug').toUpperCase(),
    nome: fd.get('nome'),
    sottodominio: fd.get('sottodominio'),
    id_foglio: fd.get('id_foglio'),
    gid_scheda: parseInt(fd.get('gid_scheda')) || 0,
    deposito: {
      Nome: fd.get('deposito_nome'),
      Lat: parseFloat(fd.get('deposito_lat')),
      Lon: parseFloat(fd.get('deposito_lon')),
    },
    ora_inizio_giornata: parseInt(fd.get('ora_inizio_giornata')),
    ora_fine_giornata: parseInt(fd.get('ora_fine_giornata')),
    usa_localita_come_comune: fd.get('usa_localita_come_comune') === 'on',
  };
  const porta = fd.get('porta');
  if (porta) body.porta = parseInt(porta);
  try {
    const r = await api('POST', '/api/provinces', body);
    toast(`Provincia ${r.slug} creata sulla porta ${r.cfg.porta}. Clicca "Avvia" per partire.`);
    chiudiModal();
    caricaProvince();
  } catch (e) {
    toast('Errore creazione: ' + e.message, true);
  }
}

// Mostra utente loggato (se la response include user-data via /api/status o altro)
fetch('/api/status', { credentials: 'same-origin' })
  .then(r => r.ok ? r.json() : null)
  .catch(() => null);
document.getElementById('user-info').textContent = 'Admin';

caricaProvince();
</script>
</body>
</html>
"""


def _admin_render_ui():
    return Response(_ADMIN_HTML, mimetype='text/html')


@app.route('/_router/health')
def router_health():
    """Endpoint di health interno del router (non proxato)."""
    return jsonify({
        'status': 'up',
        'router_port': ROUTER_PORT,
        'backends': pm.stato(),
    })


# ============================================================================
# SHUTDOWN
# ============================================================================
def _shutdown_handler(signum, frame):
    log.info(f"ricevuto segnale {signum}, shutdown…")
    pm.termina_tutte()
    sys.exit(0)


# ============================================================================
# MAIN
# ============================================================================
def main():
    signal.signal(signal.SIGINT, _shutdown_handler)
    signal.signal(signal.SIGTERM, _shutdown_handler)

    if not os.path.exists(PROVINCES_FILE):
        log.error(f"{PROVINCES_FILE} non trovato; impossibile partire.")
        sys.exit(1)
    province = carica_province()
    if not province:
        log.error(f"nessuna provincia configurata in {PROVINCES_FILE}.")
        sys.exit(1)

    log.info(f"router su {ROUTER_HOST}:{ROUTER_PORT}")
    log.info(f"province da spawnare: {list(province.keys())}")

    pm.avvia_tutte(province)

    # Watcher in thread daemon: rileva crash e riavvia
    threading.Thread(target=pm.watch_loop, name='pm-watch', daemon=True).start()

    try:
        # threaded=True permette gestione concorrente di SSE long-lived + altri request
        app.run(host=ROUTER_HOST, port=ROUTER_PORT, threaded=True, debug=False, use_reloader=False)
    finally:
        pm.termina_tutte()


if __name__ == '__main__':
    main()
