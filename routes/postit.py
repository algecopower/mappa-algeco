# -*- coding: utf-8 -*-
"""
Blueprint per le route dei Post-it (promemoria sempre visibili).
"""
import logging
from flask import Blueprint, request, jsonify, session

from moduli.auth import login_required
from moduli.shared import _get_username, _api_error
from moduli.postit import (
    get_postit, crea_postit, modifica_postit, elimina_postit,
    get_conteggio, COLORI_POSTIT, CATEGORIE_POSTIT,
)
from config import PROVINCIA_ATTIVA

logger = logging.getLogger('mappa_algeco')

bp = Blueprint('postit', __name__)


@bp.route('/api/postit', methods=['GET'])
@login_required
def api_get_postit():
    """Restituisce tutti i post-it attivi."""
    try:
        notes = get_postit(provincia=PROVINCIA_ATTIVA)
        return jsonify({'ok': True, 'notes': notes, 'count': len(notes)})
    except Exception as e:
        logger.error(f"[PostIt] Errore get: {e}")
        return _api_error(str(e), 500)


@bp.route('/api/postit', methods=['POST'])
@login_required
def api_crea_postit():
    """Crea un nuovo post-it."""
    try:
        body = request.get_json(force=True)
        testo = body.get('testo', '').strip()
        if not testo:
            return _api_error('Testo obbligatorio', 400)

        nota = crea_postit(
            testo=testo,
            categoria=body.get('categoria', 'generico'),
            colore=body.get('colore', 'giallo'),
            priorita=body.get('priorita', 'media'),
            provincia=body.get('provincia', PROVINCIA_ATTIVA),
            scadenza=body.get('scadenza'),
            creato_da=_get_username(),
        )
        if not nota:
            return _api_error('Errore creazione post-it', 500)

        logger.info(f"[PostIt] ✅ Creato: '{testo[:40]}' da {_get_username()}")
        return jsonify({'ok': True, 'nota': nota}), 201
    except Exception as e:
        logger.error(f"[PostIt] Errore creazione: {e}")
        return _api_error(str(e), 500)


@bp.route('/api/postit/<note_id>', methods=['PUT'])
@login_required
def api_modifica_postit(note_id):
    """Modifica un post-it esistente."""
    try:
        body = request.get_json(force=True)
        nota = modifica_postit(note_id, **body)
        if not nota:
            return _api_error('Post-it non trovato', 404)

        logger.info(f"[PostIt] ✏️ Modificato: {note_id} da {_get_username()}")
        return jsonify({'ok': True, 'nota': nota})
    except Exception as e:
        logger.error(f"[PostIt] Errore modifica: {e}")
        return _api_error(str(e), 500)


@bp.route('/api/postit/<note_id>', methods=['DELETE'])
@login_required
def api_elimina_postit(note_id):
    """Elimina un post-it."""
    try:
        if elimina_postit(note_id):
            logger.info(f"[PostIt] 🗑️ Eliminato: {note_id} da {_get_username()}")
            return jsonify({'ok': True})
        return _api_error('Post-it non trovato', 404)
    except Exception as e:
        logger.error(f"[PostIt] Errore eliminazione: {e}")
        return _api_error(str(e), 500)


@bp.route('/api/postit/meta', methods=['GET'])
@login_required
def api_postit_meta():
    """Restituisce categorie, colori e conteggio."""
    return jsonify({
        'ok': True,
        'categorie': CATEGORIE_POSTIT,
        'colori': COLORI_POSTIT,
        'count': get_conteggio(),
    })
