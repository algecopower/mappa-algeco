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
from urllib.parse import quote

import requests
from flask import Flask, request, Response, jsonify, redirect

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


_FORBIDDEN_HTML = """<!DOCTYPE html>
<html lang="it"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Accesso non autorizzato</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          background: #f5f6f8; color: #1f2329; display: flex; align-items: center;
          justify-content: center; min-height: 100vh; margin: 0; padding: 16px; }}
  .card {{ background: white; border-radius: 12px; padding: 40px 48px; max-width: 480px;
           width: 100%; box-shadow: 0 4px 24px rgba(0,0,0,.08); text-align: center; }}
  @media (max-width: 480px) {{ .card {{ padding: 28px 22px; }} h1 {{ font-size: 18px; }} }}
  .icon {{ font-size: 56px; margin-bottom: 8px; }}
  h1 {{ margin: 0 0 8px; font-size: 22px; }}
  p {{ color: #6b7280; font-size: 14px; line-height: 1.6; }}
  .badge {{ display: inline-block; background: #fee2e2; color: #991b1b; padding: 2px 10px;
            border-radius: 12px; font-size: 12px; font-weight: 600; margin: 0 4px; }}
  .actions {{ margin-top: 24px; }}
  a.btn {{ display: inline-block; background: #2563eb; color: white; padding: 10px 20px;
           text-decoration: none; border-radius: 6px; font-size: 14px; font-weight: 500; }}
  a.btn:hover {{ background: #1d4ed8; }}
</style>
</head><body>
<div class="card">
  <div class="icon">🔒</div>
  <h1>Accesso non autorizzato</h1>
  <p>Questa pagina è riservata agli amministratori del sistema.<br>
     Stai accedendo come <span class="badge">{username}</span>
     con ruolo <span class="badge">{ruolo}</span>.</p>
  <p>Se pensi sia un errore, chiedi a un amministratore di promuovere il tuo account.</p>
  <div class="actions">
    <a class="btn" href="{back_url}">← Torna alla mappa</a>
  </div>
</div></body></html>"""


def _require_admin_or_redirect():
    """Ritorna None se admin, altrimenti una Response (redirect o 401/403).

    UX path:
      - Non autenticato + richiesta API → 401 JSON
      - Non autenticato + richiesta HTML → redirect a login
      - Autenticato ma non admin + API → 403 JSON
      - Autenticato ma non admin + HTML → 403 con pagina HTML user-friendly
    """
    user = _validate_session()
    if not user:
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Autenticazione richiesta'}), 401
        return redirect(_login_url())
    if user.get('ruolo') != 'admin':
        if request.path.startswith('/api/'):
            return jsonify({
                'error': 'Permessi insufficienti',
                'utente': user.get('username'),
                'ruolo': user.get('ruolo'),
            }), 403
        # Pagina HTML user-friendly per non-admin
        slug, cfg = _backend_up_per_validazione()
        back_url = f"https://{cfg['sottodominio']}/" if (cfg and cfg.get('sottodominio')) else '/'
        html = _FORBIDDEN_HTML.format(
            username=(user.get('username') or '').replace('<', '&lt;'),
            ruolo=(user.get('ruolo') or 'user').replace('<', '&lt;'),
            back_url=back_url,
        )
        return Response(html, status=403, mimetype='text/html')
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

            log.info(f"[{slug}] avvio backend (gunicorn) → porta {cfg['porta']}")
            # gunicorn invece di Werkzeug dev server: thread pool gestito,
            # max_requests recycling (mitiga memory leak), graceful shutdown.
            # Vedi gunicorn.conf.py per i parametri.
            gunicorn_bin = os.path.join(os.path.dirname(sys.executable), 'gunicorn')
            proc = subprocess.Popen(
                [gunicorn_bin, '-c', 'gunicorn.conf.py', 'wsgi:app'],
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


def _filter_headers_from_backend(upstream):
    """Headers in risposta dal backend: rimuovi hop-by-hop.

    IMPORTANTE: usa `upstream.raw.headers` (urllib3 HTTPHeaderDict) invece
    di `upstream.headers` perché quest'ultimo unisce con virgola i valori
    di header duplicati — corrompendo es. multipli Set-Cookie (separati
    dal valore originale, contengono già virgole dentro `Expires=...`).
    """
    return [
        (k, v) for k, v in upstream.raw.headers.items()
        if k.lower() not in HOP_BY_HOP_HEADERS
    ]


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

    headers_out = _filter_headers_from_backend(upstream)
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
    if m and method == 'PUT':
        return _admin_check() or _admin_update_province(m.group(1))
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
    if p == 'api/health-overview' and method == 'GET':
        return _admin_check() or _admin_health_overview()
    m = re.match(r'^api/provinces/([A-Z0-9_]+)/db-action$', p)
    if m and method == 'POST':
        return _admin_check() or _admin_db_action(m.group(1))
    m = re.match(r'^api/provinces/([A-Z0-9_]+)/dump-eneltel/(.+)$', p)
    if m and method == 'GET':
        return _admin_check() or _admin_dump_eneltel(m.group(1), m.group(2))

    return jsonify({'error': 'admin endpoint non trovato', 'path': p}), 404


# ============================================================================
# ADMIN — Health overview + actions on-demand + inspector eneltel
# ============================================================================
def _proxy_admin_to_backend(slug, path, method='GET', body=None, timeout=130):
    """Forward request admin verso il backend della provincia, preservando
    cookie sessione (per @admin_required nel backend)."""
    try:
        province = carica_province()
    except Exception as e:
        return None, jsonify({'error': f'lettura provinces.json: {e}'}), 500
    slug = slug.upper()
    if slug not in province:
        return None, jsonify({'error': f'provincia {slug} non trovata'}), 404
    cfg = province[slug]
    if pm.stato().get(slug) != 'running':
        return None, jsonify({'error': f'backend {slug} non in esecuzione',
                              'stato': pm.stato().get(slug, 'stopped')}), 503
    url = f'http://127.0.0.1:{cfg["porta"]}{path}'
    headers = {'Cookie': request.headers.get('Cookie', '')}
    try:
        r = requests.request(method, url, headers=headers, json=body, timeout=timeout)
    except requests.RequestException as e:
        return None, jsonify({'error': f'backend non raggiungibile: {e}'}), 502
    return r, None, None


def _admin_health_overview():
    """Aggrega health di tutte le province up: stats + integrity + warnings."""
    try:
        province = carica_province()
    except Exception as e:
        return jsonify({'error': f'lettura provinces.json: {e}'}), 500
    out = []
    cookie = request.headers.get('Cookie', '')
    for slug, cfg in province.items():
        item = {'slug': slug, 'nome': cfg.get('nome', slug), 'stato': pm.stato().get(slug, 'stopped')}
        if item['stato'] != 'running':
            item['reachable'] = False
            out.append(item)
            continue
        try:
            r = requests.get(
                f'http://127.0.0.1:{cfg["porta"]}/api/admin/db-stats',
                headers={'Cookie': cookie}, timeout=5,
            )
            item['reachable'] = (r.status_code == 200)
            if r.status_code == 200:
                data = r.json()
                item['stats'] = data.get('stats', {})
                item['integrity'] = data.get('integrity', 'unknown')
                item['health'] = data.get('health', {})
        except requests.RequestException as e:
            item['reachable'] = False
            item['error'] = str(e)
        out.append(item)
    return jsonify({'province': out})


def _admin_db_action(slug):
    body = request.get_json(silent=True) or {}
    r, err_resp, err_code = _proxy_admin_to_backend(
        slug, '/api/admin/db-action', method='POST', body=body, timeout=130,
    )
    if r is None:
        return err_resp, err_code
    return (r.text, r.status_code, {'Content-Type': r.headers.get('Content-Type', 'application/json')})


def _admin_dump_eneltel(slug, eneltel):
    r, err_resp, err_code = _proxy_admin_to_backend(
        slug, f'/api/admin/db-dump/eneltel/{eneltel}', method='GET', timeout=15,
    )
    if r is None:
        return err_resp, err_code
    return (r.text, r.status_code, {'Content-Type': r.headers.get('Content-Type', 'application/json')})


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


def _admin_update_province(slug):
    """PUT /api/provinces/<slug> — aggiorna config strutturale di una provincia.

    Slug NON modificabile (è chiave primaria). Tutto il resto sì.
    Se il backend è running e cambia un campo strutturale (porta, id_foglio,
    gid_scheda, deposito, orari, sottodominio, file_mappa), viene auto-riavviato
    per applicare la modifica.
    """
    slug = slug.upper()
    body = request.get_json(silent=True) or {}
    try:
        province = carica_province()
    except Exception as e:
        return jsonify({'error': f'lettura provinces.json fallita: {e}'}), 500
    if slug not in province:
        return jsonify({'error': f'provincia {slug} non trovata'}), 404

    current = dict(province[slug])
    aggiornato = dict(current)

    # Campi merge-abili: copia se presenti nel body
    for k in ('nome', 'id_foglio', 'sottodominio', 'file_mappa'):
        if k in body:
            aggiornato[k] = body[k]
    for k in ('gid_scheda', 'ora_inizio_giornata', 'ora_fine_giornata', 'porta'):
        if k in body and body[k] is not None and body[k] != '':
            try:
                aggiornato[k] = int(body[k])
            except (ValueError, TypeError):
                return jsonify({'error': f"campo '{k}' deve essere intero"}), 400
    if 'usa_localita_come_comune' in body:
        if body['usa_localita_come_comune']:
            aggiornato['usa_localita_come_comune'] = True
        else:
            aggiornato.pop('usa_localita_come_comune', None)
    if 'deposito' in body and isinstance(body['deposito'], dict):
        dep = dict(current.get('deposito', {}))
        dep.update({k: v for k, v in body['deposito'].items() if v is not None})
        # normalizza Lat/Lon come float
        for k in ('Lat', 'Lon'):
            if k in dep:
                try:
                    dep[k] = float(dep[k])
                except (ValueError, TypeError):
                    return jsonify({'error': f"deposito.{k} deve essere numerico"}), 400
        aggiornato['deposito'] = dep
    for k in ('operatori_vip', 'colori_operatori', 'operatori_dict'):
        if k in body:
            aggiornato[k] = body[k]

    # Controllo conflitti porta
    if aggiornato.get('porta') != current.get('porta'):
        nuova_porta = aggiornato.get('porta')
        for altro_slug, altro_cfg in province.items():
            if altro_slug == slug:
                continue
            if int(altro_cfg.get('porta', 0)) == int(nuova_porta or 0):
                return jsonify({'error': f"porta {nuova_porta} già usata da {altro_slug}"}), 409

    # Rileva se serve riavviare il backend
    STRUTTURALI = {'porta', 'id_foglio', 'gid_scheda', 'sottodominio', 'file_mappa',
                   'deposito', 'ora_inizio_giornata', 'ora_fine_giornata',
                   'operatori_vip', 'operatori_dict', 'colori_operatori',
                   'usa_localita_come_comune'}
    cambi = [k for k in STRUTTURALI if aggiornato.get(k) != current.get(k)]

    province[slug] = aggiornato
    salva_province(province)
    log.info(f"[admin] {slug} aggiornata. Campi cambiati: {cambi or 'nessuno strutturale'}")

    backend_riavviato = False
    if cambi and pm.stato().get(slug) == 'running':
        log.info(f"[admin] {slug}: cambi strutturali → auto-restart")
        pm.termina_provincia(slug)
        porta = int(aggiornato.get('porta', 0))
        if porta:
            pm._aspetta_porta_libera(porta, timeout=15)
        pm.avvia_provincia(slug, aggiornato)
        backend_riavviato = True

    return jsonify({
        'ok': True,
        'slug': slug,
        'cfg': aggiornato,
        'campi_cambiati': cambi,
        'backend_riavviato': backend_riavviato,
    })


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
# ADMIN UI (template HTML caricato da templates_router/admin.html)
# ============================================================================
_ADMIN_TEMPLATE_PATH = os.path.join(SCRIPT_DIR, 'templates_router', 'admin.html')


def _admin_render_ui():
    try:
        with open(_ADMIN_TEMPLATE_PATH, 'r', encoding='utf-8') as f:
            html = f.read()
    except OSError as e:
        return Response(f'admin.html non trovato: {e}', status=500, mimetype='text/plain')
    return Response(html, mimetype='text/html')


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
