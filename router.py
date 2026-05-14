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
import sys
import time
import signal
import socket
import logging
import subprocess
import threading
from urllib.parse import urlparse

import requests
from flask import Flask, request, Response, jsonify

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
def carica_province():
    """Ritorna dict {slug_upper: cfg} dal file provinces.json."""
    import json
    with open(PROVINCES_FILE, 'r', encoding='utf-8') as f:
        raw = json.load(f)
    return {k.upper(): v for k, v in raw.items()}


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
        """Spawna un singolo backend. Idempotente: non rispawna se già vivo."""
        with self._lock:
            existing = self._proc.get(slug)
            if existing and existing.poll() is None:
                log.info(f"[{slug}] backend già attivo (pid={existing.pid}), skip")
                return existing

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
        # Placeholder Step 3 (UI admin). Per ora un endpoint minimo.
        return jsonify({
            'router': 'mappa-algeco',
            'province': pm.stato(),
            'provinces_file': PROVINCES_FILE,
            'message': 'Admin UI not yet implemented (Step 3).',
        })

    if slug is None or cfg is None:
        return jsonify({
            'error': 'Sottodominio non riconosciuto',
            'host': request.headers.get('Host', ''),
            'hint': f'Configurare provincia in {PROVINCES_FILE} oppure usare un sottodominio valido.',
        }), 404

    return _proxy_to_backend(cfg['porta'], '/' + path, request.query_string.decode())


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
