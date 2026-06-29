# -*- coding: utf-8 -*-
"""
Blueprint: Comuni — gestione informazioni sui comuni e invio comunicazioni.
"""
import logging
from flask import Blueprint, request, jsonify, render_template, session, send_file
from moduli.auth import login_required
from moduli.comuni_manager import (
    get_comuni_list,
    update_comune,
    export_comuni_to_excel,
    import_comuni_clean_excel,
    send_bulk_emails,
    send_single_email,
    generate_email_previews,
    generate_excel_contatori_comune
)

logger = logging.getLogger('mappa_algeco')

bp = Blueprint('comuni', __name__)

@bp.route('/comuni')
@login_required
def comuni_view():
    """Mostra l'interfaccia di Gestione Comuni."""
    return render_template('comuni.html')

@bp.route('/api/comuni/list', methods=['GET'])
@login_required
def api_comuni_list():
    """Ritorna la lista dei comuni con i loro dettagli."""
    try:
        comuni = get_comuni_list()
        return jsonify({'success': True, 'data': comuni})
    except Exception as e:
        logger.error(f"Errore caricamento comuni: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/comuni/update', methods=['POST'])
@login_required
def api_comuni_update():
    """Aggiorna i dettagli di un singolo comune."""
    try:
        data = request.json
        comune = data.get('comune')
        if not comune:
            return jsonify({'success': False, 'error': 'Nome comune mancante'}), 400
            
        success = update_comune(comune, data)
        if success:
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Nessun campo da aggiornare o comune non trovato'}), 400
    except Exception as e:
        logger.error(f"Errore aggiornamento comune: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/comuni/import-excel', methods=['POST'])
@login_required
def api_import_comuni():
    """Importa l'anagrafica dei comuni da un file Excel strutturato"""
    try:
        import tempfile
        import os
        
        f = request.files.get('file')
        if not f or not f.filename:
            return jsonify({'success': False, 'error': 'File mancante'}), 400
            
        filename_lower = f.filename.lower()
        if not filename_lower.endswith(('.csv', '.xlsx')):
            return jsonify({'success': False, 'error': 'Atteso file .csv o .xlsx'}), 400
            
        suffix = '.csv' if filename_lower.endswith('.csv') else '.xlsx'
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        try:
            f.save(tmp.name)
            tmp.close()
            
            result = import_comuni_clean_excel(tmp.name)
            
            if result.get('success'):
                return jsonify(result)
            else:
                return jsonify({'success': False, 'error': result.get('error', 'Errore sconosciuto')}), 400
        finally:
            try:
                os.unlink(tmp.name)
            except Exception:
                pass
    except Exception as e:
        logger.exception("Errore importazione comuni da Excel")
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/comuni/bulk-preview', methods=['POST'])
@login_required
def api_comuni_bulk_preview():
    """Genera le anteprime delle email (sia per Polizia che per Comune)."""
    try:
        data = request.json
        comuni_ids = data.get('comuni_ids', [])
        template_name = data.get('template', 'comune')
        escludi_pec = data.get('escludi_pec', False)
        
        if not comuni_ids:
            return jsonify({'success': False, 'error': 'Nessun comune specificato'}), 400
            
        previews, errors = generate_email_previews(comuni_ids, template_name, escludi_pec)
        return jsonify({
            'success': True,
            'previews': previews,
            'errors': errors
        })
    except Exception as e:
        logger.error(f"Errore anteprima bulk comuni: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/comuni/bulk-send', methods=['POST'])
@login_required
def api_comuni_bulk_send():
    """Invia le comunicazioni in bulk ai comuni, con le email modificate."""
    try:
        data = request.json
        emails_data = data.get('emails_data', [])
        
        if not emails_data:
            return jsonify({'success': False, 'error': 'Dati insufficienti'}), 400
            
        user = session.get('user', {}).get('username', 'sconosciuto') if isinstance(session.get('user'), dict) else session.get('user', 'sconosciuto')
        
        n_queued, errors = send_bulk_emails(emails_data, user)
        return jsonify({
            'success': True,
            'queued': n_queued,
            'errors': errors
        })
    except Exception as e:
        logger.error(f"Errore invio bulk comuni: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/comuni/send-single', methods=['POST'])
@login_required
def api_comuni_send_single():
    """Invia UNA singola email a un comune. Chiamato dal frontend in loop con pausa."""
    try:
        data = request.json
        if not data:
            return jsonify({'success': False, 'error': 'Dati mancanti'}), 400

        user = session.get('user', {}).get('username', 'sconosciuto') if isinstance(session.get('user'), dict) else session.get('user', 'sconosciuto')

        result = send_single_email(data, user)
        return jsonify({
            'success': result.get('ok', False),
            'comune': result.get('comune', ''),
            'email': result.get('email', ''),
            'error': result.get('errore', '')
        })
    except Exception as e:
        logger.error(f"Errore invio singolo comune: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/comuni/download-excel/<comune>', methods=['GET'])
@login_required
def api_download_comune_excel(comune):
    """Scarica il file Excel dei contatori di uno specifico comune."""
    try:
        from io import BytesIO
        excel_bytes = generate_excel_contatori_comune(comune)
        if not excel_bytes:
            return jsonify({'error': 'Nessun contatore o errore di generazione'}), 400
            
        return send_file(
            BytesIO(excel_bytes),
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=f'Contatori_{comune}.xlsx'
        )
    except Exception as e:
        logger.error(f"Errore download excel contatori comune {comune}: {e}")
        return jsonify({'error': str(e)}), 500

@bp.route('/api/comuni/export-excel', methods=['GET'])
@login_required
def api_export_comuni():
    """Esporta i dati dei comuni in Excel (simile al filone)"""
    try:
        from io import BytesIO
        excel_bytes = export_comuni_to_excel()
        if not excel_bytes:
            return jsonify({'success': False, 'error': 'Nessun dato da esportare o errore interno'}), 400
            
        return send_file(
            BytesIO(excel_bytes),
            as_attachment=True,
            download_name='info_sui_comuni.xlsx',
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    except Exception as e:
        logger.error(f"Errore esportazione comuni in excel: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
