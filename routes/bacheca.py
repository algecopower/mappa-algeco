# -*- coding: utf-8 -*-
"""
Blueprint per la Bacheca Promemoria.
Fornisce API per recuperare attività/promemoria del calendario
in base alla data, da mostrare come popup o nel pannello bacheca.
"""
import logging
from flask import Blueprint, request, jsonify

from moduli.auth import login_required
from moduli.calendario import get_attivita, CATEGORIE_VALIDE, COLORI_CATEGORIE
from config import PROVINCIA_ATTIVA

logger = logging.getLogger('mappa_algeco')

bp = Blueprint('bacheca', __name__)


@bp.route('/api/bacheca/promemoria', methods=['GET'])
@login_required
def api_promemoria_per_data():
    """
    Restituisce i promemoria/attività del calendario per una data specifica.
    Query params:
      - data: YYYY-MM-DD (obbligatorio)
    Esclude boxbeat (gestiti altrove) e attività completate.
    """
    data = request.args.get('data', '').strip()
    if not data:
        return jsonify({'ok': False, 'error': 'Parametro data obbligatorio'}), 400

    try:
        tutte = get_attivita(mostra_completate=False, provincia=PROVINCIA_ATTIVA)
        # Filtra per data richiesta, escludi boxbeat
        promemoria = [
            a for a in tutte
            if a.get('data') == data and a.get('categoria') != 'boxbeat'
        ]

        return jsonify({
            'ok': True,
            'data': data,
            'promemoria': promemoria,
            'count': len(promemoria),
        })
    except Exception as e:
        logger.error(f"[Bacheca] Errore promemoria per data {data}: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


@bp.route('/api/bacheca/tutti', methods=['GET'])
@login_required
def api_bacheca_tutti():
    """
    Restituisce TUTTI i promemoria/attività non completati del calendario
    (esclusi boxbeat). Usato per il pannello bacheca laterale.
    """
    try:
        tutte = get_attivita(mostra_completate=False, provincia=PROVINCIA_ATTIVA)
        # Escludi boxbeat
        attivita = [a for a in tutte if a.get('categoria') != 'boxbeat']

        return jsonify({
            'ok': True,
            'attivita': attivita,
            'count': len(attivita),
            'categorie': list(CATEGORIE_VALIDE),
            'colori': COLORI_CATEGORIE,
        })
    except Exception as e:
        logger.error(f"[Bacheca] Errore lista attività: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500
