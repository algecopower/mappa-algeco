# -*- coding: utf-8 -*-
"""
Configurazione gunicorn per Mappa Algeco (server backend per provincia).

Volutamente 1 worker:
- L'app è I/O-bound, non CPU-bound (network + DB SQLite)
- Ogni worker carica pandas + cache distanze + mappa_data (~300 MB)
- Il sync thread + worker DB locale gira nel worker: con N worker avremmo
  N sync che si pestano sui file di cache (race condition) e moltiplicano
  il carico Sheets API
- Il volume di traffico è basso (~5 utenti); gthread gestisce concorrenza

threads=32 per gestire SSE long-lived: ogni connessione SSE tiene 1 thread
bloccato in `queue.get(timeout=...)`, quindi il pool deve essere abbondante.
Con 5-10 utenti che hanno tab multipli aperti possiamo arrivare facilmente
a 15-20 SSE attive; servono thread extra per le request HTTP normali
(API, page loads). 32 è un compromesso: memoria ~150 KB/thread × 32 ≈ 5 MB.
"""

import os

bind = f"0.0.0.0:{os.environ.get('FLASK_PORT', '5001')}"
workers = 1
threads = int(os.environ.get('GUNICORN_THREADS', '32'))
worker_class = 'gthread'
worker_connections = 100

# Timeout alto per SSE (le connessioni restano aperte molti minuti)
timeout = 600
keepalive = 5
graceful_timeout = 30

# Singolo worker: preload non serve, bootstrap chiamato in wsgi.py.
preload_app = False

# Log su stdout/stderr → finiscono nel journal di systemd.
accesslog = '-'
errorlog = '-'
loglevel = 'info'

# Restituisce memoria al SO ogni N request (mitigazione leak)
max_requests = 1000
max_requests_jitter = 100
