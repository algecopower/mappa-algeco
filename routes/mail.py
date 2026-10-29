# -*- coding: utf-8 -*-
"""
routes/mail.py — Blueprint per il modulo Gestione Mail.
"""

import logging
import mimetypes
import os
import time
import urllib.parse
import uuid
from flask import Blueprint, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename

from config import PROVINCIA_ATTIVA
from moduli.auth import login_required
from moduli.shared import _get_username
from moduli import mail_manager

logger = logging.getLogger('mappa_algeco')

bp = Blueprint('mail', __name__)
_ROOT_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))


# ============================================================================
# VISTE HTML
# ============================================================================

@bp.route('/mail')
@bp.route('/webmail')
@bp.route('/gestione-mail')
@login_required
def mail_view():
    """Renderizza la pagina principale del client di posta Gestione Mail."""
    return render_template('mail.html', provincia=PROVINCIA_ATTIVA)


# ============================================================================
# API STATISTICHE & LISTING
# ============================================================================

@bp.route('/api/mail/stats', methods=['GET'])
@login_required
def api_mail_stats():
    """Ritorna conteggi e statistiche sulle cartelle e categorie."""
    try:
        stats = mail_manager.get_mail_stats()
        return jsonify({'success': True, 'stats': stats})
    except Exception as e:
        logger.exception("[MAIL-API] Errore stats email")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/mail/messages', methods=['GET'])
@login_required
def api_mail_list():
    """Ritorna la lista paginata dei messaggi con filtri."""
    try:
        folder = request.args.get('folder', 'inbox')
        category_id = request.args.get('category_id')
        query = request.args.get('q')
        unread_only = request.args.get('unread') in ('1', 'true', 'yes')
        starred_only = request.args.get('starred') in ('1', 'true', 'yes')
        has_attachments = request.args.get('has_attachments') in ('1', 'true', 'yes')
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 40))

        result = mail_manager.list_messages(
            folder=folder,
            category_id=category_id,
            query=query,
            unread_only=unread_only,
            starred_only=starred_only,
            has_attachments=has_attachments,
            page=page,
            per_page=per_page
        )
        result['success'] = True
        return jsonify(result)
    except Exception as e:
        logger.exception("[MAIL-API] Errore listing messaggi")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/mail/messages/<int:msg_id>', methods=['GET'])
@login_required
def api_mail_detail(msg_id):
    """Ritorna il dettaglio di un messaggio singolo."""
    try:
        mark_read = request.args.get('mark_read', '1') in ('1', 'true', 'yes')
        msg = mail_manager.get_message(msg_id, mark_read=mark_read)
        if not msg:
            return jsonify({'success': False, 'error': 'Messaggio non trovato'}), 404
        return jsonify({'success': True, 'message': msg})
    except Exception as e:
        logger.exception(f"[MAIL-API] Errore dettaglio messaggio {msg_id}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================================
# API INVIO, BOZZE & AZIONI
# ============================================================================

@bp.route('/api/mail/send', methods=['POST'])
@login_required
def api_mail_send():
    """Invia o programma l'invio di un'email."""
    try:
        data = request.get_json() or {}
        user = _get_username()
        result = mail_manager.send_or_schedule_mail(data, user=user)
        return jsonify(result)
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        logger.exception("[MAIL-API] Errore invio email")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/mail/save-draft', methods=['POST'])
@login_required
def api_mail_save_draft():
    """Salva o aggiorna una bozza."""
    try:
        data = request.get_json() or {}
        draft_id = data.get('draft_id')
        result = mail_manager.save_draft(data, draft_id=draft_id)
        return jsonify(result)
    except Exception as e:
        logger.exception("[MAIL-API] Errore salvataggio bozza")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/mail/batch-action', methods=['POST'])
@login_required
def api_mail_batch_action():
    """Esegue un'azione cumulativa (mark_read, move, star, delete, change_category)."""
    try:
        data = request.get_json() or {}
        msg_ids = data.get('ids', [])
        action = data.get('action')
        value = data.get('value')
        
        if not action or not msg_ids:
            return jsonify({'success': False, 'error': 'Parametri mancanti'}), 400
            
        result = mail_manager.batch_actions(msg_ids, action, value=value)
        return jsonify(result)
    except Exception as e:
        logger.exception("[MAIL-API] Errore azione batch")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/mail/toggle-starred', methods=['POST'])
@login_required
def api_mail_toggle_starred():
    """Aggiunge/rimuove dai preferiti con stella."""
    try:
        data = request.get_json() or {}
        msg_id = data.get('id')
        if not msg_id:
            return jsonify({'success': False, 'error': 'ID mancante'}), 400
        mail_manager.toggle_message_starred(msg_id, is_starred=data.get('starred'))
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/mail/mark-read', methods=['POST'])
@login_required
def api_mail_mark_read():
    """Segna come letta o non letta."""
    try:
        data = request.get_json() or {}
        msg_id = data.get('id')
        is_read = data.get('is_read', True)
        if not msg_id:
            return jsonify({'success': False, 'error': 'ID mancante'}), 400
        mail_manager.mark_message_read(msg_id, is_read=is_read)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/mail/set-category', methods=['POST'])
@login_required
def api_mail_set_category():
    """Assegna una categoria a un messaggio."""
    try:
        data = request.get_json() or {}
        msg_id = data.get('id')
        cat_id = data.get('category_id')
        if not msg_id:
            return jsonify({'success': False, 'error': 'ID mancante'}), 400
        mail_manager.set_message_category(msg_id, category_id=cat_id)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/mail/move-folder', methods=['POST'])
@login_required
def api_mail_move_folder():
    """Sposta un messaggio in una cartella specificata."""
    try:
        data = request.get_json() or {}
        msg_id = data.get('id')
        folder = data.get('folder', 'inbox')
        if not msg_id:
            return jsonify({'success': False, 'error': 'ID mancante'}), 400
        mail_manager.move_message(msg_id, destination_folder=folder)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/mail/messages/<int:msg_id>', methods=['DELETE'])
@login_required
def api_mail_delete(msg_id):
    """Elimina o sposta nel cestino."""
    try:
        permanent = request.args.get('permanent') in ('1', 'true', 'yes')
        ok = mail_manager.delete_message(msg_id, permanent=permanent)
        if not ok:
            return jsonify({'success': False, 'error': 'Messaggio non trovato'}), 404
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================================
# API SINCRONIZZAZIONE IMAP
# ============================================================================

@bp.route('/api/mail/sync-imap', methods=['POST'])
@login_required
def api_mail_sync_imap():
    """Esegue la sincronizzazione manuale con la casella IMAP."""
    try:
        data = request.get_json(silent=True) or {}
        raw_limit = request.args.get('limit')
        if raw_limit is None and 'limit' in data:
            raw_limit = data.get('limit')
        max_msgs = int(raw_limit) if raw_limit is not None else 150
        result = mail_manager.sync_imap_inbox(max_messages=max_msgs)
        return jsonify(result)
    except Exception as e:
        logger.exception("[MAIL-API] Errore sync IMAP")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================================
# API CATEGORIE
# ============================================================================

@bp.route('/api/mail/categories', methods=['GET'])
@login_required
def api_mail_categories_list():
    """Lista delle categorie configurate."""
    try:
        cats = mail_manager.list_categories()
        return jsonify({'success': True, 'categories': cats})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/mail/categories', methods=['POST'])
@login_required
def api_mail_category_save():
    """Crea o modifica una categoria."""
    try:
        data = request.get_json() or {}
        result = mail_manager.save_category(data)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@bp.route('/api/mail/categories/<cat_id>', methods=['DELETE'])
@login_required
def api_mail_category_delete(cat_id):
    """Elimina una categoria non di sistema."""
    try:
        mail_manager.delete_category(cat_id)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


# ============================================================================
# API TEMPLATE & FRASARIO ("SPIACCICA TEMPLATE")
# ============================================================================

@bp.route('/api/mail/templates', methods=['GET'])
@login_required
def api_mail_templates_list():
    """Lista dei template e frasari disponibili."""
    try:
        items = mail_manager.list_templates_and_snippets()
        return jsonify({'success': True, 'templates': items})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/mail/templates', methods=['POST'])
@login_required
def api_mail_template_save():
    """Salva o aggiorna un template o una frase rapida."""
    try:
        data = request.get_json() or {}
        result = mail_manager.save_template_or_snippet(data)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@bp.route('/api/mail/templates/<string:item_id>', methods=['DELETE'])
@login_required
def api_mail_template_delete(item_id):
    """Elimina un template/snippet."""
    try:
        mail_manager.delete_template_or_snippet(item_id, prov=PROVINCIA_ATTIVA)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@bp.route('/api/mail/template-settings', methods=['GET'])
@login_required
def api_mail_template_settings_get():
    """Ritorna le impostazioni globali dei template e firma aziendale."""
    try:
        st = mail_manager.get_template_settings()
        return jsonify({'success': True, 'settings': st})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/mail/template-settings', methods=['POST'])
@login_required
def api_mail_template_settings_save():
    """Salva le impostazioni globali dei template e firma aziendale."""
    try:
        data = request.get_json() or {}
        res = mail_manager.save_template_settings(data)
        return jsonify(res)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@bp.route('/api/mail/resolve-template', methods=['POST'])
@login_required
def api_mail_resolve_template():
    """Risolve i segnaposto in un testo con i valori forniti."""
    try:
        data = request.get_json() or {}
        text = data.get('text', '')
        variables = data.get('variables', {})
        resolved = mail_manager.resolve_template_placeholders(text, variables)
        return jsonify({'success': True, 'resolved': resolved})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================================
# AUTOCOMPLETAMENTO CONTATTI & ALLEGATI
# ============================================================================

@bp.route('/api/mail/contacts-search', methods=['GET'])
@login_required
def api_mail_contacts_search():
    """Cerca contatti per autocompletamento in rubrica Comuni, Aziende e Filone."""
    try:
        q = request.args.get('q', '').strip()
        results = mail_manager.search_contacts_autocomplete(q)
        return jsonify({'success': True, 'contacts': results})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/mail/standard-attachments', methods=['GET'])
@login_required
def api_mail_standard_attachments():
    """Ritorna la lista degli allegati standard aziendali disponibili."""
    try:
        files = mail_manager.list_standard_attachments()
        return jsonify({'success': True, 'attachments': files})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/mail/upload-attachment', methods=['POST'])
@login_required
def api_mail_upload_attachment():
    """Carica un file temporaneo come allegato per la composizione."""
    try:
        f = request.files.get('file')
        if not f or not f.filename:
            return jsonify({'success': False, 'error': 'File mancante'}), 400
            
        original_fn = f.filename
        safe_fn = secure_filename(original_fn) or 'allegato'
        prov_att_dir = mail_manager._get_attachments_dir()
        
        stored_name = f"draft_{int(time.time())}_{uuid.uuid4().hex[:6]}_{safe_fn}"
        stored_path = os.path.join(prov_att_dir, stored_name)
        f.save(stored_path)
        
        size = os.path.getsize(stored_path)
        return jsonify({
            'success': True,
            'attachment': {
                'filename': original_fn,
                'file_path': stored_path,
                'size_bytes': size,
                'size_formatted': f"{round(size / 1024, 1)} KB" if size < 1024*1024 else f"{round(size / (1024*1024), 2)} MB"
            }
        })
    except Exception as e:
        logger.exception("[MAIL-API] Errore upload allegato")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/mail/attachment/<int:msg_id>/<path:filename>', methods=['GET'])
@login_required
def api_mail_download_attachment(msg_id, filename):
    """Download sicuro o visualizzazione inline di un allegato associato al messaggio."""
    try:
        msg = mail_manager.get_message(msg_id)
        if not msg:
            return jsonify({'error': 'Messaggio non trovato'}), 404
            
        target_path = None
        target_att = None
        decoded_fn = urllib.parse.unquote(filename)
        
        for a in msg.get('attachments', []):
            fn = a.get('filename') or ''
            fpath = a.get('file_path') or ''
            cid = a.get('content_id') or ''
            if (fn == decoded_fn or os.path.basename(fpath) == decoded_fn or 
                cid == decoded_fn or fn == filename or os.path.basename(fpath) == filename):
                target_path = fpath
                target_att = a
                break
                
        # Fallback: se non trovato ma c'è un solo allegato e si richiede inline
        if not target_path and msg.get('attachments'):
            target_path = msg['attachments'][0].get('file_path')
            target_att = msg['attachments'][0]
                
        if target_path and os.path.isfile(target_path):
            is_inline = request.args.get('inline') in ('1', 'true', 'yes')
            mime, _ = mimetypes.guess_type(target_path)
            if not mime and target_att:
                mime = target_att.get('content_type')
            mime = mime or 'application/octet-stream'
            
            return send_file(
                target_path,
                mimetype=mime,
                as_attachment=not is_inline,
                download_name=target_att.get('filename', filename) if target_att else filename
            )
            
        return jsonify({'error': 'Allegato non trovato'}), 404
    except Exception as e:
        logger.exception(f"[MAIL-API] Errore download allegato {filename}")
        return jsonify({'error': str(e)}), 500
