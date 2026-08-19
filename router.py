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
import hmac
import signal
import socket
import logging
import subprocess
import threading
from urllib.parse import quote, urlparse

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


_SETTINGS_OVERRIDE_FIELDS = {
    'operatori_vip',
    'colori_operatori',
    'operatori_dict',
    'operatori_disabilitati',
    'lcl_disabilitati',
    'deposito',
    'ora_inizio_giornata',
    'ora_fine_giornata',
    'velocita_media_kmh',
    'tempo_intervento_min',
    'data_inizio',
    'data_fine',
}


def sincronizza_settings_provincia(slug, cfg, campi):
    """Allinea gli override esistenti di settings.json alla dashboard admin.

    Il backend carica settings.json dopo provinces.json: senza questo passaggio
    un valore storico nel file per-provincia annullerebbe la modifica al riavvio.
    """
    settings_file = os.path.join(SCRIPT_DIR, 'data', slug.lower(), 'settings.json')
    if not os.path.exists(settings_file):
        return []

    da_aggiornare = _SETTINGS_OVERRIDE_FIELDS.intersection(campi)
    if not da_aggiornare:
        return []

    with open(settings_file, 'r', encoding='utf-8') as f:
        settings = json.load(f)

    aggiornati = []
    for campo in sorted(da_aggiornare):
        if campo in cfg:
            settings[campo] = cfg[campo]
            aggiornati.append(campo)

    if aggiornati:
        tmp = settings_file + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
        os.replace(tmp, settings_file)

    return aggiornati


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
    backends = _backends_running_per_validazione()
    return backends[0] if backends else (None, None)


def _backends_running_per_validazione():
    """Ritorna tutti i backend running candidati alla validazione sessione."""
    try:
        province = carica_province()
    except Exception:
        return []
    stato = pm.stato()
    out = []
    for slug, cfg in province.items():
        if stato.get(slug) == 'running':
            out.append((slug, cfg))
    return out


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

    backends = _backends_running_per_validazione()
    if not backends:
        log.warning("nessun backend running per validare sessione admin")
        return None

    user = None
    for slug, cfg in backends:
        try:
            r = requests.get(
                f'http://127.0.0.1:{cfg["porta"]}/api/user',
                headers={'Cookie': cookie_header},
                timeout=3,
            )
            if r.status_code == 200:
                data = r.json()
                if data.get('authenticated'):
                    user = data.get('user')
                    break
        except requests.RequestException as e:
            log.warning(f"[{slug}] errore validazione sessione: {e}")

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
HOST_LABEL_RE = re.compile(r'^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$')
RESERVED_SLUGS = {'ADMIN', 'ROUTER', 'WWW'}


def _admin_csrf_check():
    """Protegge le API admin native del router da CSRF.

    I backend provinciali fanno gia' un controllo equivalente; qui serve per
    le mutazioni gestite direttamente dal router (province, lifecycle, wipe).
    """
    origin = request.headers.get('Origin')
    if not origin:
        referer = request.headers.get('Referer', '')
        if referer:
            p = urlparse(referer)
            if p.scheme and p.netloc:
                origin = f'{p.scheme}://{p.netloc}'

    if origin:
        p = urlparse(origin)
        req_host = (request.headers.get('Host') or '').split(':', 1)[0].lower()
        if not p.hostname or p.hostname.lower() != req_host:
            return jsonify({'error': 'Richiesta bloccata: origine non autorizzata'}), 403

    header_token = request.headers.get('X-CSRF-Token', '')
    cookie_token = request.cookies.get('csrf_token', '')
    if not header_token or not cookie_token or not hmac.compare_digest(header_token, cookie_token):
        return jsonify({'error': 'Token CSRF mancante o non valido'}), 403
    return None


def _normalize_sottodominio(value, slug):
    """Normalizza il sottodominio configurato per una provincia."""
    host = (value or '').strip().lower()
    if not host:
        return f'{slug.lower()}.mappa-algeco.com'
    if '://' in host:
        parsed = urlparse(host)
        host = parsed.netloc
    host = host.split('/', 1)[0].split(':', 1)[0].strip()
    return host


def _validate_sottodominio(slug, host, province, current_slug=None):
    """Valida host pubblico evitando collisioni con admin.* e altre province."""
    labels = [p for p in (host or '').split('.') if p]
    if len(labels) < 2 or any(not HOST_LABEL_RE.match(label) for label in labels):
        return "sottodominio non valido"
    if labels[0] == ADMIN_HOST_PREFIX:
        return "sottodominio riservato all'admin"
    current_slug = (current_slug or '').upper()
    for other_slug, other_cfg in province.items():
        if other_slug.upper() == current_slug:
            continue
        other_host = _normalize_sottodominio(other_cfg.get('sottodominio'), other_slug)
        if other_host == host or other_host.split('.', 1)[0] == labels[0]:
            return f"sottodominio gia' usato da {other_slug}"
    return None


def _parse_port(value, province, current_slug=None):
    """Valida una porta backend dichiarata dall'admin."""
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None, 'porta non valida'
    if port <= 0 or port > 65535:
        return None, 'porta fuori range (1-65535)'
    if port == ROUTER_PORT:
        return None, f'porta {port} riservata al router'
    current_slug = (current_slug or '').upper()
    for other_slug, other_cfg in province.items():
        if other_slug.upper() == current_slug:
            continue
        if int(other_cfg.get('porta', 0) or 0) == port:
            return None, f'porta {port} gia\' usata da {other_slug}'
    return port, None


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
    if method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        guard = _admin_check()
        if guard is not None:
            return guard
        csrf_guard = _admin_csrf_check()
        if csrf_guard is not None:
            return csrf_guard

    if p == 'api/user' and method == 'GET':
        return _admin_check() or _admin_current_user()
    if p == 'api/provinces' and method == 'GET':
        return _admin_check() or _admin_list_provinces()
    if p == 'api/provinces' and method == 'POST':
        return _admin_check() or _admin_create_province()
    m = re.match(r'^api/provinces/([a-zA-Z0-9_]+)$', p)
    if m and method == 'DELETE':
        return _admin_check() or _admin_delete_province(m.group(1))
    if m and method == 'PUT':
        return _admin_check() or _admin_update_province(m.group(1))
    m = re.match(r'^api/provinces/([a-zA-Z0-9_]+)/(start|stop|restart)$', p)
    if m and method == 'POST':
        return _admin_check() or _admin_lifecycle(m.group(1), m.group(2))
    if p == 'api/status' and method == 'GET':
        return _admin_check() or jsonify({
            'router_port': ROUTER_PORT,
            'backends': pm.stato(),
        })
    if p == 'api/logs' and method == 'GET':
        return _admin_check() or _admin_logs()
    if p == 'api/health-overview' and method == 'GET':
        return _admin_check() or _admin_health_overview()
    m = re.match(r'^api/provinces/([a-zA-Z0-9_]+)/db-action$', p)
    if m and method == 'POST':
        return _admin_check() or _admin_db_action(m.group(1))
    m = re.match(r'^api/provinces/([a-zA-Z0-9_]+)/dump-eneltel/(.+)$', p)
    if m and method == 'GET':
        return _admin_check() or _admin_dump_eneltel(m.group(1), m.group(2))

    
    m = re.match(r'^api/provinces/([a-zA-Z0-9_]+)/smtp-settings$', p)
    if m and method == 'GET':
        return _admin_check() or _admin_proxy_simple(m.group(1), '/api/admin/smtp-settings', 'GET')
    if m and method == 'PUT':
        return _admin_check() or _admin_proxy_simple(m.group(1), '/api/admin/smtp-settings', 'PUT')

    m = re.match(r'^api/provinces/([a-zA-Z0-9_]+)/import-excel$', p)
    if m and method == 'POST':
        return _admin_check() or _admin_import_excel(m.group(1))

    m = re.match(r'^api/provinces/([a-zA-Z0-9_]+)/outbox$', p)
    if m and method == 'GET':
        return _admin_check() or _admin_outbox_stats(m.group(1))
    m = re.match(r'^api/provinces/([a-zA-Z0-9_]+)/outbox/excel$', p)
    if m and method == 'GET':
        return _admin_check() or _admin_outbox_excel(m.group(1))
    m = re.match(r'^api/provinces/([a-zA-Z0-9_]+)/outbox/mark-delivered$', p)
    if m and method == 'POST':
        return _admin_check() or _admin_outbox_mark_delivered(m.group(1))
    m = re.match(r'^api/provinces/([a-zA-Z0-9_]+)/export-full-excel$', p)
    if m and method == 'GET':
        return _admin_check() or _admin_export_full_excel(m.group(1))

    m = re.match(r'^api/provinces/([a-zA-Z0-9_]+)/export-comuni-excel$', p)
    if m and method == 'GET':
        return _admin_check() or _admin_export_comuni_excel(m.group(1))

    m = re.match(r'^api/provinces/([a-zA-Z0-9_]+)/column-mapping$', p)
    if m and method == 'GET':
        return _admin_check() or _admin_proxy_simple(m.group(1), '/api/admin/column-mapping', 'GET')
    if m and method == 'PUT':
        return _admin_check() or _admin_proxy_simple(m.group(1), '/api/admin/column-mapping', 'PUT')

    m = re.match(r'^api/provinces/([a-zA-Z0-9_]+)/lettere-excel-config$', p)
    if m and method == 'GET':
        return _admin_check() or _admin_proxy_simple(
            m.group(1), '/api/admin/lettere-excel-config', 'GET'
        )
    if m and method == 'PUT':
        return _admin_check() or _admin_proxy_simple(
            m.group(1), '/api/admin/lettere-excel-config', 'PUT'
        )

    m = re.match(r'^api/provinces/([a-zA-Z0-9_]+)/lcls$', p)
    if m and method == 'GET':
        return _admin_check() or _admin_proxy_simple(m.group(1), '/api/admin/lcls', 'GET')
    if m and method == 'PUT':
        return _admin_check() or _admin_proxy_simple(m.group(1), '/api/admin/lcls', 'PUT')

    m = re.match(r'^api/provinces/([a-zA-Z0-9_]+)/columns$', p)
    if m and method == 'GET':
        return _admin_check() or _admin_proxy_simple(m.group(1), '/api/admin/columns', 'GET')
    if m and method == 'POST':
        return _admin_check() or _admin_proxy_simple(m.group(1), '/api/admin/columns', 'POST')

    m = re.match(r'^api/provinces/([a-zA-Z0-9_]+)/columns/bootstrap-standard$', p)
    if m and method == 'POST':
        return _admin_check() or _admin_proxy_simple(
            m.group(1), '/api/admin/columns/bootstrap-standard', 'POST'
        )

    m = re.match(r'^api/provinces/([a-zA-Z0-9_]+)/columns/(.+)/rename$', p)
    if m and method == 'PUT':
        return _admin_check() or _admin_proxy_simple(
            m.group(1), f'/api/admin/columns/{m.group(2)}/rename', 'PUT'
        )
    m = re.match(r'^api/provinces/([a-zA-Z0-9_]+)/columns/(.+)$', p)
    if m and method == 'DELETE':
        return _admin_check() or _admin_proxy_simple(
            m.group(1), f'/api/admin/columns/{m.group(2)}', 'DELETE'
        )

    # Utenti (users.json globale, proxy a backend running qualsiasi)
    if p == 'api/admin/users' and method in ('GET', 'POST'):
        return _admin_check() or _admin_users_proxy('/api/admin/users', method)
    m = re.match(r'^api/admin/users/([^/]+)$', p)
    if m and method == 'DELETE':
        return _admin_check() or _admin_users_proxy(f'/api/admin/users/{m.group(1)}', 'DELETE')
    m = re.match(r'^api/admin/users/([^/]+)/(toggle|reset-password)$', p)
    if m and method == 'POST':
        return _admin_check() or _admin_users_proxy(
            f'/api/admin/users/{m.group(1)}/{m.group(2)}', 'POST'
        )
    m = re.match(r'^api/admin/users/([^/]+)/province$', p)
    if m and method == 'PUT':
        return _admin_check() or _admin_users_proxy(
            f'/api/admin/users/{m.group(1)}/province', 'PUT'
        )

    return jsonify({'error': 'admin endpoint non trovato', 'path': p}), 404


# ============================================================================
# ADMIN — Health overview + actions on-demand + inspector eneltel
# ============================================================================
def _proxy_admin_to_backend(slug, path, method='GET', body=None, timeout=130):
    """Forward request admin verso il backend della provincia, preservando
    cookie sessione + CSRF + Origin (per @admin_required e csrf_check)."""
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
    headers = {
        'Cookie': request.headers.get('Cookie', ''),
        # Inoltra CSRF + Origin (richiesti dal backend su POST/PUT/DELETE)
        'X-CSRF-Token': request.headers.get('X-CSRF-Token', ''),
        'Origin': request.headers.get('Origin', '') or request.headers.get('Referer', ''),
    }
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
                item['outbox'] = data.get('outbox', {})
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


def _admin_outbox_stats(slug):
    r, err_resp, err_code = _proxy_admin_to_backend(
        slug, '/api/admin/outbox', method='GET', timeout=10,
    )
    if r is None:
        return err_resp, err_code
    return (r.text, r.status_code, {'Content-Type': r.headers.get('Content-Type', 'application/json')})


def _admin_outbox_excel(slug):
    """Forward del download Excel preservando binary content + filename."""
    try:
        province = carica_province()
    except Exception as e:
        return jsonify({'error': f'lettura provinces.json: {e}'}), 500
    slug = slug.upper()
    if slug not in province:
        return jsonify({'error': f'provincia {slug} non trovata'}), 404
    cfg = province[slug]
    if pm.stato().get(slug) != 'running':
        return jsonify({'error': f'backend {slug} non in esecuzione',
                        'stato': pm.stato().get(slug, 'stopped')}), 503
    url = f'http://127.0.0.1:{cfg["porta"]}/api/admin/outbox/excel'
    headers = {'Cookie': request.headers.get('Cookie', '')}
    try:
        r = requests.get(url, headers=headers, timeout=60, stream=True)
    except requests.RequestException as e:
        return jsonify({'error': f'backend non raggiungibile: {e}'}), 502
    # Preserva content-type, content-disposition, e custom headers
    fwd_headers = {}
    for h in ('Content-Type', 'Content-Disposition', 'X-Outbox-Rows', 'X-Outbox-Cells'):
        v = r.headers.get(h)
        if v:
            fwd_headers[h] = v
    return (r.content, r.status_code, fwd_headers)


def _admin_users_proxy(backend_path, method):
    """Proxy degli endpoint /api/admin/users* verso un backend running.
    users.json è globale (sotto SCRIPT_DIR), quindi qualsiasi provincia
    può servire l'endpoint."""
    slug, _cfg = _backend_up_per_validazione()
    if not slug:
        return jsonify({
            'error': 'Nessuna provincia in esecuzione. Avvia almeno una provincia '
                     'per poter gestire gli utenti.',
        }), 503
    return _admin_proxy_simple(slug, backend_path, method)


def _admin_proxy_simple(slug, backend_path, method, timeout=30):
    """Proxy admin generico per endpoint JSON (no upload/download binari)."""
    body = request.get_json(silent=True) if method in ('POST', 'PUT', 'PATCH') else None
    r, err_resp, err_code = _proxy_admin_to_backend(
        slug, backend_path, method=method, body=body, timeout=timeout,
    )
    if r is None:
        return err_resp, err_code
    return (r.text, r.status_code, {'Content-Type': r.headers.get('Content-Type', 'application/json')})


def _admin_export_full_excel(slug):
    """Forward download dell'Excel completo dal backend (binary)."""
    try:
        province = carica_province()
    except Exception as e:
        return jsonify({'error': f'lettura provinces.json: {e}'}), 500
    slug = slug.upper()
    if slug not in province:
        return jsonify({'error': f'provincia {slug} non trovata'}), 404
    cfg = province[slug]
    if pm.stato().get(slug) != 'running':
        return jsonify({'error': f'backend {slug} non in esecuzione',
                        'stato': pm.stato().get(slug, 'stopped')}), 503
    url = f'http://127.0.0.1:{cfg["porta"]}/api/admin/export-full-excel'
    headers = {'Cookie': request.headers.get('Cookie', '')}
    try:
        r = requests.get(url, headers=headers, timeout=120, stream=True)
    except requests.RequestException as e:
        return jsonify({'error': f'backend non raggiungibile: {e}'}), 502
    fwd_headers = {}
    for h in ('Content-Type', 'Content-Disposition', 'X-Filone-Rows', 'X-Filone-Cols'):
        v = r.headers.get(h)
        if v:
            fwd_headers[h] = v
    return (r.content, r.status_code, fwd_headers)


def _admin_export_comuni_excel(slug):
    """Forward download dell'Excel comuni dal backend (binary)."""
    try:
        province = carica_province()
    except Exception as e:
        return jsonify({'error': f'lettura provinces.json: {e}'}), 500
    slug = slug.upper()
    if slug not in province:
        return jsonify({'error': f'provincia {slug} non trovata'}), 404
    cfg = province[slug]
    if pm.stato().get(slug) != 'running':
        return jsonify({'error': f'backend {slug} non in esecuzione',
                        'stato': pm.stato().get(slug, 'stopped')}), 503
    url = f'http://127.0.0.1:{cfg["porta"]}/api/admin/export-comuni-excel'
    headers = {'Cookie': request.headers.get('Cookie', '')}
    try:
        r = requests.get(url, headers=headers, timeout=120, stream=True)
    except requests.RequestException as e:
        return jsonify({'error': f'backend non raggiungibile: {e}'}), 502
    fwd_headers = {}
    for h in ('Content-Type', 'Content-Disposition'):
        v = r.headers.get(h)
        if v:
            fwd_headers[h] = v
    return (r.content, r.status_code, fwd_headers)


def _admin_outbox_mark_delivered(slug):
    body = request.get_json(silent=True) or {}
    r, err_resp, err_code = _proxy_admin_to_backend(
        slug, '/api/admin/outbox/mark-delivered', method='POST', body=body, timeout=15,
    )
    if r is None:
        return err_resp, err_code
    return (r.text, r.status_code, {'Content-Type': r.headers.get('Content-Type', 'application/json')})


def _admin_import_excel(slug):
    """Forward del file Excel + form-data verso il backend della provincia."""
    try:
        province = carica_province()
    except Exception as e:
        return jsonify({'error': f'lettura provinces.json: {e}'}), 500
    slug = slug.upper()
    if slug not in province:
        return jsonify({'error': f'provincia {slug} non trovata'}), 404
    cfg = province[slug]
    if pm.stato().get(slug) != 'running':
        return jsonify({'error': f'backend {slug} non in esecuzione',
                        'stato': pm.stato().get(slug, 'stopped')}), 503

    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'success': False, 'message': 'File mancante'}), 400

    # Inoltra il multipart al backend
    files = {'file': (f.filename, f.stream, f.mimetype or 'application/octet-stream')}
    data = {k: v for k, v in request.form.items()}
    url = f'http://127.0.0.1:{cfg["porta"]}/api/admin/import-excel'
    headers = {
        'Cookie': request.headers.get('Cookie', ''),
        # Inoltra anche CSRF e Origin: il backend li richiede su POST/PUT/DELETE
        # (vedi `csrf_check` in server.py)
        'X-CSRF-Token': request.headers.get('X-CSRF-Token', ''),
        'Origin': request.headers.get('Origin', '') or request.headers.get('Referer', ''),
    }
    try:
        # Timeout largo: parse Excel + merge possono richiedere 30-60s
        r = requests.post(url, headers=headers, files=files, data=data, timeout=300)
    except requests.RequestException as e:
        return jsonify({'error': f'backend non raggiungibile: {e}'}), 502
    return (r.text, r.status_code,
            {'Content-Type': r.headers.get('Content-Type', 'application/json')})


# ============================================================================
# ADMIN — Live log viewer
# ============================================================================
# Priorità syslog: 0=emerg .. 7=debug. Mappa per filtro UI:
_LOG_LEVEL_THRESHOLD = {
    'error': 3,    # err + warning escluso
    'warn': 4,     # warning e sopra (err, crit, alert, emerg)
    'info': 6,     # info e sopra (warning, err, ecc.)
    'all': 7,      # tutto, incluso debug
}


def _pid_to_slug_map():
    """Ritorna mappa {pid_str: 'TORINO'|'PAVIA'|...} per identificare i log
    in base al PID. Il PID del router stesso viene marcato come '__router__'."""
    out = {}
    with pm._lock:
        for slug, proc in pm._proc.items():
            if proc and proc.poll() is None:
                out[str(proc.pid)] = slug
    out[str(os.getpid())] = '__router__'
    return out


def _admin_logs():
    """Ritorna gli ultimi N log dal systemd journal del servizio.

    Query params:
      - level: error|warn|info|all (default warn)
      - limit: max righe (default 200, max 2000)
      - source: tutti|router|TORINO|PAVIA|... (default tutti)
      - since: timestamp Unix opzionale per delta updates
    """
    level = request.args.get('level', 'warn').lower()
    limit = min(int(request.args.get('limit', 200)), 2000)
    source = request.args.get('source', 'tutti')
    since = request.args.get('since', '').strip()

    priority_max = _LOG_LEVEL_THRESHOLD.get(level, _LOG_LEVEL_THRESHOLD['warn'])

    cmd = [
        '/usr/bin/journalctl',
        '-u', 'mappa-algeco-router.service',
        '--output', 'json',
        '--no-pager',
        '--lines', str(limit * 3),  # raccogliamo extra per compensare i filtri
        '-r',  # reverse: più recenti prima
    ]
    if since:
        try:
            since_ts = int(float(since))
            cmd.extend(['--since', f'@{since_ts}'])
        except (ValueError, TypeError):
            pass

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'journalctl timeout'}), 504
    if result.returncode != 0:
        return jsonify({'error': f'journalctl exit {result.returncode}', 'stderr': result.stderr[-500:]}), 500

    pid_map = _pid_to_slug_map()
    logs = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            pri = int(entry.get('PRIORITY', 6))
        except (TypeError, ValueError):
            pri = 6
        if pri > priority_max:
            continue
        msg = entry.get('MESSAGE', '')
        pid = entry.get('_PID', '')
        slug = pid_map.get(pid, '?')
        if source != 'tutti':
            if source == 'router' and slug != '__router__':
                continue
            elif source not in ('router',) and slug != source:
                continue
        ts_us = int(entry.get('__REALTIME_TIMESTAMP', '0'))
        ts = ts_us / 1_000_000 if ts_us else 0
        logs.append({
            'ts': ts,
            'priority': pri,
            'source': 'router' if slug == '__router__' else slug,
            'pid': pid,
            'message': msg[:1000],
        })
        if len(logs) >= limit:
            break

    return jsonify({
        'logs': logs,
        'count': len(logs),
        'pid_map': pid_map,
        'sources_disponibili': ['tutti', 'router'] + sorted(set(pid_map.values()) - {'__router__'}),
    })


def _admin_check():
    """Helper: ritorna response 401/403 se non admin, None altrimenti.
    Usato dai handler API che rispondono in JSON.
    """
    return _require_admin_or_redirect()


def _admin_current_user():
    """Ritorna l'utente admin corrente per l'header della dashboard."""
    user = _validate_session()
    if not user:
        return jsonify({'authenticated': False}), 401
    return jsonify({'authenticated': True, 'user': user})


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
    if slug in RESERVED_SLUGS:
        return jsonify({'error': f"slug '{slug}' riservato"}), 400
    try:
        province = carica_province()
    except Exception as e:
        return jsonify({'error': f'lettura provinces.json fallita: {e}'}), 500
    if slug in province:
        return jsonify({'error': f'provincia {slug} già esistente'}), 409

    sottodominio = _normalize_sottodominio(body.get('sottodominio'), slug)
    err = _validate_sottodominio(slug, sottodominio, province)
    if err:
        return jsonify({'error': err}), 400
    porta_raw = body.get('porta') or porta_libera_successiva(province)
    porta, err = _parse_port(porta_raw, province)
    if err:
        return jsonify({'error': err}), 400

    cfg = {
        'nome': body.get('nome') or slug.capitalize(),
        'deposito': body.get('deposito') or {'Nome': '', 'Lat': 0.0, 'Lon': 0.0},
        'operatori_vip': body.get('operatori_vip') or [],
        'colori_operatori': body.get('colori_operatori') or {},
        'operatori_dict': body.get('operatori_dict') or {},
        'file_mappa': os.path.join('templates', f'mappa_{slug.lower()}.html'),
        'ora_inizio_giornata': int(body.get('ora_inizio_giornata') or 480),
        'ora_fine_giornata': int(body.get('ora_fine_giornata') or 1020),
        'velocita_media_kmh': int(body.get('velocita_media_kmh') or 35),
        'tempo_intervento_min': int(body.get('tempo_intervento_min') or 20),
        'data_inizio': body.get('data_inizio') or '',
        'data_fine': body.get('data_fine') or '',
        'sottodominio': sottodominio,
        'porta': porta,
    }
    if body.get('usa_localita_come_comune'):
        cfg['usa_localita_come_comune'] = True

    province[slug] = cfg
    salva_province(province)
    log.info(f"[admin] provincia {slug} aggiunta (porta {cfg['porta']})")

    # Crea la cartella dati. Il DB SQLite verrà inizializzato vuoto al
    # primo avvio del backend (db.py crea lo schema on-demand). I dati
    # entrano successivamente via import Excel o tramite il wizard.
    data_dir = os.path.join(SCRIPT_DIR, 'data', slug.lower())
    os.makedirs(data_dir, exist_ok=True)

    # Auto-grant accesso all'admin che ha creato la provincia (così non
    # deve modificare users.json a mano dopo ogni creazione).
    granted_to = _grant_provincia_a_utente_corrente(slug)

    return jsonify({
        'ok': True, 'slug': slug, 'cfg': cfg,
        'auto_granted_to': granted_to,
    }), 201


def _grant_provincia_a_utente_corrente(slug):
    """Aggiunge `slug` alla lista 'province' dell'utente loggato.

    Ritorna l'username modificato, o None se non possibile.
    """
    try:
        user = _validate_session()
        if not user:
            return None
        username = (user.get('username') or '').strip().lower()
        if not username:
            return None
        import json as _json
        users_file = os.path.join(SCRIPT_DIR, 'users.json')
        if not os.path.exists(users_file):
            return None
        with open(users_file, 'r', encoding='utf-8') as f:
            users = _json.load(f)
        if username not in users:
            return None
        provs = users[username].get('province') or []
        if slug not in provs:
            provs.append(slug)
            users[username]['province'] = provs
            tmp = users_file + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                _json.dump(users, f, indent=2, ensure_ascii=False)
            os.replace(tmp, users_file)
            log.info(f"[admin] auto-grant: aggiunto {slug} a {username}")
        return username
    except Exception as e:
        log.warning(f"[admin] auto-grant {slug} fallito: {e}")
        return None


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
    for k in ('nome', 'file_mappa', 'data_inizio', 'data_fine'):
        if k in body:
            aggiornato[k] = body[k]
    if 'sottodominio' in body:
        aggiornato['sottodominio'] = _normalize_sottodominio(body.get('sottodominio'), slug)
    for k in ('ora_inizio_giornata', 'ora_fine_giornata', 'porta',
              'velocita_media_kmh', 'tempo_intervento_min'):
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
    for k in ('operatori_vip', 'colori_operatori', 'operatori_dict', 'operatori_disabilitati', 'lcl_disabilitati'):
        if k in body:
            aggiornato[k] = body[k]

    # Controlli su campi che impattano il routing pubblico/backend.
    if aggiornato.get('porta') != current.get('porta'):
        porta, err = _parse_port(aggiornato.get('porta'), province, current_slug=slug)
        if err:
            return jsonify({'error': err}), 400
        aggiornato['porta'] = porta
    if aggiornato.get('sottodominio') != current.get('sottodominio'):
        err = _validate_sottodominio(
            slug, aggiornato.get('sottodominio'), province, current_slug=slug,
        )
        if err:
            return jsonify({'error': err}), 400

    # Rileva se serve riavviare il backend
    STRUTTURALI = {'porta', 'sottodominio', 'file_mappa',
                   'deposito', 'ora_inizio_giornata', 'ora_fine_giornata',
                   'velocita_media_kmh', 'tempo_intervento_min',
                   'data_inizio', 'data_fine',
                   'operatori_vip', 'operatori_dict', 'colori_operatori',
                   'operatori_disabilitati', 'lcl_disabilitati',
                   'usa_localita_come_comune'}
    cambi = [k for k in STRUTTURALI if aggiornato.get(k) != current.get(k)]

    province[slug] = aggiornato
    salva_province(province)
    try:
        settings_aggiornati = sincronizza_settings_provincia(slug, aggiornato, body)
    except (OSError, ValueError, TypeError) as e:
        log.exception(f"[admin] {slug}: sincronizzazione settings.json fallita")
        return jsonify({
            'error': (
                'provinces.json aggiornato, ma settings.json non è stato allineato: '
                f'{e}'
            ),
        }), 500
    log.info(f"[admin] {slug} aggiornata. Campi cambiati: {cambi or 'nessuno strutturale'}")
    if settings_aggiornati:
        log.info(f"[admin] {slug}: override settings allineati: {settings_aggiornati}")

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
    """Elimina una provincia. Query param ?wipe_data=1 cancella anche tutta
    la cartella data/{slug}/ (DB, backup, settings). Senza ?wipe_data, la
    cartella resta come precauzione."""
    slug = slug.upper()
    try:
        province = carica_province()
    except Exception as e:
        return jsonify({'error': f'lettura provinces.json fallita: {e}'}), 500
    if slug not in province:
        return jsonify({'error': f'provincia {slug} non trovata'}), 404
    wipe_data = (request.args.get('wipe_data') or '').strip().lower() in ('1', 'true', 'yes')
    # Ferma il backend prima di rimuovere
    pm.termina_provincia(slug)
    del province[slug]
    salva_province(province)

    deleted_paths = []
    if wipe_data:
        import shutil
        # Cancella TUTTI gli artefatti su disco relativi al slug
        candidates = [
            os.path.join(SCRIPT_DIR, 'data', slug.lower()),
            os.path.join(SCRIPT_DIR, 'backup', slug.lower()),
            os.path.join(SCRIPT_DIR, 'cache', f'cache_distanze_{slug.lower()}.json'),
            os.path.join(SCRIPT_DIR, 'cache', f'cache_hash_{slug.lower()}.json'),
            os.path.join(SCRIPT_DIR, 'templates', f'mappa_{slug.lower()}.html'),
        ]
        for p in candidates:
            try:
                if os.path.isdir(p):
                    shutil.rmtree(p)
                    deleted_paths.append(p)
                elif os.path.isfile(p):
                    os.remove(p)
                    deleted_paths.append(p)
            except Exception as e:
                log.warning(f"[admin] wipe {p} fallito: {e}")
        log.warning(
            f"[admin] provincia {slug} ELIMINATA + dati rimossi "
            f"({len(deleted_paths)} percorsi su disco)"
        )
        return jsonify({
            'ok': True, 'slug': slug,
            'wiped': True,
            'deleted_paths': deleted_paths,
            'note': f'Provincia {slug} eliminata completamente, inclusi tutti i dati.',
        })

    log.info(f"[admin] provincia {slug} rimossa da provinces.json (data/{slug.lower()}/ NON eliminato per sicurezza)")
    return jsonify({
        'ok': True, 'slug': slug,
        'wiped': False,
        'note': f'data/{slug.lower()}/ conservato — elimina manualmente se vuoi liberare lo spazio',
    })


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
