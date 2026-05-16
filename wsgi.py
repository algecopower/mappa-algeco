# -*- coding: utf-8 -*-
"""
Entry point WSGI per gunicorn (produzione).

Avvio:
    gunicorn -c gunicorn.conf.py wsgi:app

server.py contiene anche un blocco __main__ che usa il dev server di
Werkzeug — quello resta disponibile per sviluppo locale (`python server.py`).
"""
from server import app, bootstrap

# `app` viene cercato da gunicorn via il path `wsgi:app` — re-export
# esplicito per evitare F401 e per chiarezza.
__all__ = ['app']

# Avvia sync background + warmup. Eseguito una volta all'import del modulo
# (gunicorn con preload_app=False importa wsgi.py una sola volta per worker;
# config workers=1 in gunicorn.conf.py → un solo sync thread totale).
bootstrap()
