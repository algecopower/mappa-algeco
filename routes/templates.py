# -*- coding: utf-8 -*-
"""
routes/templates.py — Blueprint per la pagina dedicata e gestione centralizzata
dei Modelli, Template Email/WhatsApp, Frasario Rapido e Firme Aziendali.
"""

import logging
from flask import Blueprint, render_template, request, jsonify
from moduli.auth import login_required
from moduli import mail_manager
from config import PROVINCIA_ATTIVA

logger = logging.getLogger('mappa_algeco')

bp = Blueprint('templates_view', __name__)


@bp.route('/templates')
@login_required
def templates_page():
    """Mostra la pagina di gestione centralizzata dei modelli e template."""
    return render_template('templates.html', provincia=PROVINCIA_ATTIVA)


@bp.route('/api/templates/all', methods=['GET'])
@login_required
def api_templates_all():
    """Ritorna tutti i template, snippet, impostazioni e definizioni campi."""
    try:
        items = mail_manager.list_templates_and_snippets(prov=PROVINCIA_ATTIVA)
        settings = mail_manager.get_template_settings(prov=PROVINCIA_ATTIVA)
        
        # Definizioni campi disponibili per l'inserimento
        fields = [
            {'key': 'nome', 'label': 'Nome Cliente / Intestatario', 'sample': 'Mario Rossi'},
            {'key': 'nome_societa', 'label': 'Ragione Sociale Azienda', 'sample': 'Acme S.r.l.'},
            {'key': 'indirizzo', 'label': 'Indirizzo e Civico', 'sample': 'Via Roma 10'},
            {'key': 'comune', 'label': 'Comune', 'sample': 'Torino'},
            {'key': 'pod', 'label': 'Codice POD', 'sample': 'IT001E12345678'},
            {'key': 'eneltel', 'label': 'Codice Eneltel', 'sample': '123456789'},
            {'key': 'matricola', 'label': 'Matricola Contatore', 'sample': '1234567890'},
            {'key': 'data', 'label': 'Data Intervento', 'sample': '25/08/2026'},
            {'key': 'nome_operatore', 'label': 'Nome Operatore Tecnico', 'sample': 'Marco Bianchi'},
            {'key': 'telefono_operatore', 'label': 'Telefono Operatore', 'sample': '333 1234567'},
            {'key': 'email_tecnico', 'label': 'Email Ufficio Tecnico', 'sample': f'tecnico_{PROVINCIA_ATTIVA.lower()}@algecopower.it'},
            {'key': 'frase_distacco', 'label': 'Clausola Distacco Automatica', 'sample': 'Precisiamo inoltre che durante l’intervento NON è previsto il distacco della fornitura elettrica.'},
            {'key': 'lista_date', 'label': 'Elenco Date Proposte', 'sample': '- Lunedì 01/09\n- Mercoledì 03/09'},
        ]
        
        return jsonify({
            'success': True,
            'provincia': PROVINCIA_ATTIVA,
            'items': items,
            'settings': settings,
            'available_fields': fields
        })
    except Exception as e:
        logger.exception("[TEMPLATES-API] Errore caricamento template")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/templates/save', methods=['POST'])
@login_required
def api_templates_save():
    """Salva o aggiorna un template o uno snippet."""
    try:
        data = request.get_json() or {}
        res = mail_manager.save_template_or_snippet(data, prov=PROVINCIA_ATTIVA)
        return jsonify(res)
    except Exception as e:
        logger.exception("[TEMPLATES-API] Errore salvataggio template")
        return jsonify({'success': False, 'error': str(e)}), 400


@bp.route('/api/templates/delete/<path:item_id>', methods=['DELETE'])
@login_required
def api_templates_delete(item_id):
    """Elimina un template/snippet."""
    try:
        mail_manager.delete_template_or_snippet(item_id, prov=PROVINCIA_ATTIVA)
        return jsonify({'success': True})
    except Exception as e:
        logger.exception("[TEMPLATES-API] Errore eliminazione template")
        return jsonify({'success': False, 'error': str(e)}), 400


@bp.route('/api/templates/settings', methods=['POST'])
@login_required
def api_templates_settings_save():
    """Salva le impostazioni della firma e recapiti."""
    try:
        data = request.get_json() or {}
        res = mail_manager.save_template_settings(data, prov=PROVINCIA_ATTIVA)
        return jsonify(res)
    except Exception as e:
        logger.exception("[TEMPLATES-API] Errore salvataggio impostazioni template")
        return jsonify({'success': False, 'error': str(e)}), 400


@bp.route('/api/templates/preview', methods=['POST'])
@login_required
def api_templates_preview():
    """Genera l'anteprima risolta di un template dato un set di variabili di test."""
    try:
        data = request.get_json() or {}
        subject_raw = data.get('subject', '')
        body_raw = data.get('body', '')
        variables = data.get('variables', {})
        include_signature = data.get('include_signature', False)

        resolved_subject = mail_manager.resolve_template_placeholders(subject_raw, variables)
        resolved_body = mail_manager.resolve_template_placeholders(body_raw, variables)

        signature_html = ""
        if include_signature:
            settings = mail_manager.get_template_settings()
            raw_sig = settings.get('firma_html', '')
            signature_html = mail_manager.resolve_signature_html(
                raw_sig,
                prov=PROVINCIA_ATTIVA,
                email_tecnico=variables.get('email_tecnico'),
                telefono_tecnico=variables.get('telefono_tecnico') or variables.get('telefono_ufficio')
            )

        return jsonify({
            'success': True,
            'subject': resolved_subject,
            'body': resolved_body,
            'signature': signature_html
        })
    except Exception as e:
        logger.exception("[TEMPLATES-API] Errore anteprima template")
        return jsonify({'success': False, 'error': str(e)}), 500
