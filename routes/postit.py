# -*- coding: utf-8 -*-
"""
Blueprint per le route dei Post-it (promemoria sempre visibili).
"""
import logging
from flask import Blueprint, request, jsonify

from moduli.auth import login_required
from moduli.shared import _get_username, _api_error
from moduli.postit import (
    get_postit, crea_postit, modifica_postit, elimina_postit,
    get_conteggio, COLORI_POSTIT, CATEGORIE_POSTIT,
    crea_postit_da_attivita, sync_postit_a_calendario,
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
        return _api_error(e, 'get post-it')


@bp.route('/api/postit', methods=['POST'])
@login_required
def api_crea_postit():
    """Crea un nuovo post-it."""
    try:
        body = request.get_json(force=True)
        testo = body.get('testo', '').strip()
        if not testo:
            return jsonify({'error': 'Testo obbligatorio'}), 400

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
            return jsonify({'error': 'Errore creazione post-it'}), 500

        logger.info(f"[PostIt] ✅ Creato: '{testo[:40]}' da {_get_username()}")
        return jsonify({'ok': True, 'nota': nota}), 201
    except Exception as e:
        logger.error(f"[PostIt] Errore creazione: {e}")
        return _api_error(e, 'creazione post-it')


@bp.route('/api/postit/<note_id>', methods=['PUT'])
@login_required
def api_modifica_postit(note_id):
    """Modifica un post-it esistente."""
    try:
        body = request.get_json(force=True)
        nota = modifica_postit(note_id, **body)
        if not nota:
            return jsonify({'error': 'Post-it non trovato'}), 404

        logger.info(f"[PostIt] ✏️ Modificato: {note_id} da {_get_username()}")
        return jsonify({'ok': True, 'nota': nota})
    except Exception as e:
        logger.error(f"[PostIt] Errore modifica: {e}")
        return _api_error(e, 'modifica post-it')


@bp.route('/api/postit/<note_id>', methods=['DELETE'])
@login_required
def api_elimina_postit(note_id):
    """Elimina un post-it."""
    try:
        if elimina_postit(note_id):
            logger.info(f"[PostIt] 🗑️ Eliminato: {note_id} da {_get_username()}")
            return jsonify({'ok': True})
        return jsonify({'error': 'Post-it non trovato'}), 404
    except Exception as e:
        logger.error(f"[PostIt] Errore eliminazione: {e}")
        return _api_error(e, 'eliminazione post-it')


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


@bp.route('/api/postit/<note_id>/to-calendario', methods=['POST'])
@login_required
def api_postit_to_calendario(note_id):
    """Sincronizza un post-it nel calendario come attività."""
    try:
        notes = get_postit()
        nota = next((n for n in notes if n['id'] == note_id), None)
        if not nota:
            return jsonify({'error': 'Post-it non trovato'}), 404

        attivita = sync_postit_a_calendario(nota)
        logger.info(f"[PostIt] 📅 Post-it {note_id} → calendario da {_get_username()}")
        return jsonify({'ok': True, 'attivita': attivita})
    except Exception as e:
        logger.error(f"[PostIt] Errore sync→calendario: {e}")
        return _api_error(e, 'sync post-it a calendario')


@bp.route('/api/postit/from-calendario', methods=['POST'])
@login_required
def api_postit_from_calendario():
    """Crea un post-it a partire da un'attività del calendario."""
    try:
        body = request.get_json(force=True)
        attivita_id = body.get('attivita_id', '').strip()
        if not attivita_id:
            return jsonify({'error': 'attivita_id obbligatorio'}), 400

        from moduli.calendario import get_attivita
        tutte = get_attivita(provincia=PROVINCIA_ATTIVA)
        attivita = next((a for a in tutte if a['id'] == attivita_id), None)
        if not attivita:
            return jsonify({'error': 'Attività non trovata'}), 404

        nota = crea_postit_da_attivita(attivita, creato_da=_get_username())
        if not nota:
            return jsonify({'error': 'Errore creazione post-it'}), 500

        logger.info(f"[PostIt] 📌 Calendario {attivita_id} → post-it da {_get_username()}")
        return jsonify({'ok': True, 'nota': nota}), 201
    except Exception as e:
        logger.error(f"[PostIt] Errore from-calendario: {e}")
        return _api_error(e, 'creazione post-it da calendario')


@bp.route('/api/postit/calendario-attivita', methods=['GET'])
@login_required
def api_postit_lista_calendario():
    """Restituisce le attività del calendario (non completate) per importazione rapida."""
    try:
        from moduli.calendario import get_attivita
        tutte = get_attivita(mostra_completate=False, provincia=PROVINCIA_ATTIVA)
        # Restituisci solo campi essenziali
        lista = [{
            'id': a['id'],
            'titolo': a.get('titolo', ''),
            'data': a.get('data', ''),
            'ora': a.get('ora', ''),
            'categoria': a.get('categoria', ''),
        } for a in tutte[:50]]
        return jsonify({'ok': True, 'attivita': lista})
    except Exception as e:
        logger.error(f"[PostIt] Errore lista calendario: {e}")
        return _api_error(str(e), 500)
