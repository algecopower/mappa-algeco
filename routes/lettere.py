# -*- coding: utf-8 -*-
"""
Blueprint Archivio Lettere — generazioni Excel, contatti aziendali e invii bulk.
"""
import logging
import os
from flask import Blueprint, jsonify, request, send_file

from moduli.auth import login_required
from moduli.shared import _get_username
from moduli import lettere as lettere_store
from moduli.bulk_contatori_email import send_contatori_email
from config import PROVINCIA_ATTIVA

from routes.distinte import (
    _ALLEGATI_DIR,
    _ALLEGATI_DEFAULT,
    _ALLEGATO_SEMIDIRETTA,
)

logger = logging.getLogger('mappa_algeco')

bp = Blueprint('lettere', __name__)
_ROOT_DIR = os.path.join(os.path.dirname(__file__), '..')


@bp.route('/lettere')
@login_required
def serve_lettere_page():
    """Serve la pagina Archivio Lettere."""
    return send_file(os.path.join(_ROOT_DIR, 'templates', 'lettere.html'))


@bp.route('/api/lettere/generazioni', methods=['GET'])
@login_required
def api_lettere_generazioni():
    """Lista delle generazioni Excel archiviate."""
    try:
        limit = int(request.args.get('limit', 100))
        return jsonify({
            'success': True,
            'provincia': PROVINCIA_ATTIVA,
            'generazioni': lettere_store.list_generazioni(limit=limit),
        })
    except Exception as e:
        logger.exception("[Lettere] Errore lista generazioni")
        return jsonify({'error': str(e)}), 500


@bp.route('/api/lettere/generazioni/<int:generazione_id>', methods=['GET'])
@login_required
def api_lettere_generazione(generazione_id):
    """Dettaglio di una generazione con righe Excel archiviate."""
    gen = lettere_store.get_generazione(generazione_id)
    if not gen:
        return jsonify({'error': 'Generazione non trovata'}), 404
    return jsonify({'success': True, 'generazione': gen})


@bp.route('/api/lettere/generazioni/<int:generazione_id>/download')
@login_required
def api_lettere_download(generazione_id):
    """Download del file Excel associato alla generazione."""
    gen = lettere_store.get_generazione(generazione_id)
    if not gen:
        return jsonify({'error': 'Generazione non trovata'}), 404

    project_root = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
    file_name = os.path.basename(gen.get('file_name') or '')
    path = os.path.join(project_root, 'output', 'lettere', file_name)
    if file_name.lower().endswith('.xlsx') and os.path.isfile(path):
        return send_file(
            path,
            as_attachment=True,
            download_name=file_name,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
    return jsonify({'error': 'File Excel non trovato'}), 404


@bp.route('/api/lettere/aziende', methods=['GET'])
@login_required
def api_lettere_aziende():
    """Lista aziende/enti rilevati nelle lettere."""
    try:
        gen_id = request.args.get('generazione_id') or None
        q = request.args.get('q', '')
        limit = min(max(int(request.args.get('limit', 500)), 1), 5000)
        return jsonify({
            'success': True,
            'aziende': lettere_store.list_aziende(
                generazione_id=gen_id,
                search=q,
                limit=limit,
            ),
        })
    except Exception as e:
        logger.exception("[Lettere] Errore lista aziende")
        return jsonify({'error': str(e)}), 500


@bp.route('/api/lettere/privati', methods=['GET'])
@login_required
def api_lettere_privati():
    """Lista utenti privati nelle lettere categorizzati per tipo di telefono."""
    try:
        gen_id = request.args.get('generazione_id') or None
        q = request.args.get('q', '')
        limit = min(max(int(request.args.get('limit', 2000)), 1), 10000)
        risultato = lettere_store.list_privati(
            generazione_id=gen_id,
            search=q,
            limit=limit,
        )
        return jsonify({
            'success': True,
            'provincia': PROVINCIA_ATTIVA,
            **risultato,
        })
    except Exception as e:
        logger.exception("[Lettere] Errore lista privati")
        return jsonify({'error': str(e)}), 500



@bp.route('/api/lettere/contatti/<path:contact_key>', methods=['PUT'])
@login_required
def api_lettere_update_contatto(contact_key):
    """Aggiorna telefono/email del contatto e, se possibile, del filone."""
    try:
        data = request.get_json() or {}
        contatto = lettere_store.update_contatto(
            contact_key,
            telefono=data.get('telefono') if 'telefono' in data else None,
            email=data.get('email') if 'email' in data else None,
            user=_get_username(),
        )
        return jsonify({'success': True, 'contatto': contatto})
    except LookupError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        logger.exception("[Lettere] Errore update contatto")
        return jsonify({'error': str(e)}), 500


@bp.route('/api/lettere/contatti-by-name', methods=['PUT'])
@login_required
def api_lettere_update_contatti_by_name():
    """Aggiorna telefono/email per tutti i contatti con lo stesso nominativo."""
    try:
        data = request.get_json() or {}
        nominativo = data.get('nominativo', '').strip()
        if not nominativo:
            return jsonify({'error': 'nominativo mancante'}), 400
        result = lettere_store.update_contatti_by_nominativo(
            nominativo,
            comune=data.get('comune'),
            telefono=data.get('telefono') if 'telefono' in data else None,
            email=data.get('email') if 'email' in data else None,
            user=_get_username(),
        )
        return jsonify({'success': True, **result})
    except LookupError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        logger.exception("[Lettere] Errore update contatti by nominativo")
        return jsonify({'error': str(e)}), 500


@bp.route('/api/lettere/aziende/<int:azienda_id>/risposta', methods=['PUT', 'POST'])
@login_required
def api_lettere_risposta(azienda_id):
    """Aggiorna spunta di risposta / completamento e/o note per una azienda in una generazione."""
    try:
        data = request.get_json() or {}
        risposta_in = data.get('risposta')
        risposta_val = bool(risposta_in) if ('risposta' in data and risposta_in is not None) else None
        note_val = data.get('note') if 'note' in data else None
        lcl_val = data.get('lcl') if 'lcl' in data else None
        out = lettere_store.set_risposta(
            azienda_id,
            risposta_val,
            note=note_val,
            user=_get_username(),
            lcl=lcl_val,
        )
        return jsonify({'success': True, **out})
    except LookupError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        logger.exception("[Lettere] Errore update risposta")
        return jsonify({'error': str(e)}), 500


@bp.route('/api/lettere/aziende/<int:azienda_id>/contatto', methods=['POST', 'PUT'])
@login_required
def api_lettere_update_azienda_contatto(azienda_id):
    """Aggiorna telefono o email per una specifica azienda."""
    try:
        data = request.get_json() or {}
        azienda = lettere_store.get_azienda(azienda_id)
        telefono = data.get('telefono') if 'telefono' in data else None
        email = data.get('email') if 'email' in data else None

        result = lettere_store.update_contatto(
            azienda['contact_key'],
            telefono=telefono,
            email=email,
            user=_get_username(),
        )
        return jsonify({'success': True, **result})
    except LookupError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        logger.exception("[Lettere] Errore update contatto azienda")
        return jsonify({'error': str(e)}), 500


@bp.route('/api/lettere/statistiche', methods=['GET'])
@login_required
def api_lettere_statistiche():
    """Statistiche feedback aziende."""
    return jsonify({
        'success': True,
        'provincia': PROVINCIA_ATTIVA,
        'lettere': lettere_store.statistiche_feedback(),
    })


@bp.route('/api/lettere/allegati', methods=['GET'])
@login_required
def api_lettere_allegati():
    """Allegati disponibili per invii lettere."""
    disponibili = []
    if os.path.isdir(_ALLEGATI_DIR):
        disponibili = sorted(
            f for f in os.listdir(_ALLEGATI_DIR)
            if os.path.isfile(os.path.join(_ALLEGATI_DIR, f)) and not f.startswith('.')
        )
    return jsonify({
        'ok': True,
        'disponibili': disponibili,
        'default_provincia': _ALLEGATI_DEFAULT,
        'allegato_semidiretta': _ALLEGATO_SEMIDIRETTA,
    })



def _contatto_da_azienda(azienda, email_dest):
    """Adatta una riga Archivio Lettere ai template email distinte."""
    matricola = ' / '.join(
        value for value in (
            azienda.get('codice_ante') or '',
            azienda.get('matricola_ante') or '',
        )
        if value
    )
    return {
        'pod': azienda.get('pod') or '',
        'email': email_dest,
        'nome': azienda.get('nominativo') or '',
        'indirizzo': azienda.get('via') or '',
        'civico': azienda.get('civico') or '',
        'comune': azienda.get('comune') or '',
        'data_intervento': azienda.get('data_pianificazione') or '',
        'pod_filone': azienda.get('pod') or '',
        'eneltel_filone': azienda.get('eneltel') or '',
        'matricola_filone': matricola,
        'tipologia_contatore': azienda.get('tipo_misuratore') or '',
        'nome_societa': azienda.get('nominativo') or '',
        'nome_referente': '',
    }


@bp.route('/api/lettere/invia-email', methods=['POST'])
@login_required
def api_lettere_invia_email():
    """Invia una singola mail/PEC e registra l'esito su tutti i contatori associati."""
    data = request.get_json() or {}
    azienda_id = data.get('azienda_id')
    azienda_ids = data.get('azienda_ids') or ([azienda_id] if azienda_id else [])
    contatori_list = data.get('contatori') or []
    email_dest = (data.get('email') or '').strip()
    template_id = data.get('template_id') or ''
    allegati_nomi = data.get('allegati') or []
    username = _get_username()

    if not azienda_id and not azienda_ids:
        return jsonify({'error': 'azienda_id mancante'}), 400
    if not email_dest or '@' not in email_dest:
        return jsonify({'error': 'Email non valida'}), 400
    if not template_id:
        return jsonify({'error': 'Template non specificato'}), 400

    primary_id = azienda_ids[0] if azienda_ids else azienda_id
    azienda = lettere_store.get_azienda(primary_id)
    if not azienda:
        return jsonify({'error': 'Azienda non trovata'}), 404

    contatto = _contatto_da_azienda(azienda, email_dest)

    res = send_contatori_email(
        email_dest=email_dest,
        subject=data.get('subject_override'),
        body_html=data.get('body_override'),
        template_id=template_id,
        contatto=contatto,
        contatori_list=contatori_list,
        allegati_nomi=allegati_nomi,
        provincia=PROVINCIA_ATTIVA,
        username=username,
        context='lettere',
        extra_record_details={
            'azienda_ids': azienda_ids,
            'primary_id': primary_id
        }
    )

    if not res.get('ok'):
        err_msg = res.get('error') or 'Errore invio'
        for az_id in azienda_ids:
            az_item = lettere_store.get_azienda(az_id)
            if not az_item:
                continue
            lettere_store.registra_invio(
                azienda_generazione_id=az_item['id'],
                generazione_id=az_item['generazione_id'],
                contact_key_value=az_item['contact_key'],
                pod=az_item.get('pod'),
                email_dest=email_dest,
                template_id=template_id,
                subject=data.get('subject_override') or '',
                status='failed',
                error=err_msg,
                user=username,
                detail={'allegati': allegati_nomi, 'contatori_count': len(contatori_list or [contatto])},
            )
        logger.error(f"[Lettere] Errore invio a {email_dest}: {err_msg}")
        status_code = 401 if res.get('error_code') == 'SMTP_AUTH_FAILED' else 400
        return jsonify({'ok': False, 'azienda_id': primary_id, 'email': email_dest, 'errore': err_msg, 'error': err_msg, 'error_code': res.get('error_code')}), status_code

    return jsonify({
        'ok': True,
        'azienda_id': primary_id,
        'azienda_ids': azienda_ids,
        'email': email_dest,
        'pod': res.get('pod'),
        'contatori_count': res.get('contatori_count', 1)
    })
