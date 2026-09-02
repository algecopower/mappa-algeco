# -*- coding: utf-8 -*-
"""
Blueprint Archivio Lettere — generazioni Excel, contatti aziendali e invii bulk.
"""
import logging
import os
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

from flask import Blueprint, jsonify, request, send_file

from moduli.auth import login_required
from moduli.safe_json import json_read
from moduli.shared import _get_username
from moduli import lettere as lettere_store
from moduli import mail_manager
from moduli.bulk_contatori_email import (
    generate_contatori_excel,
    sanitize_filename,
    build_nota_invio,
    registra_nota_su_db,
)
from config import PROVINCIA_ATTIVA

from moduli.smtp import send_smtp_message, load_smtp_settings
from routes.distinte import (
    _ALLEGATI_DIR,
    _ALLEGATI_DEFAULT,
    _ALLEGATO_SEMIDIRETTA,
    _TEMPLATES_JSON,
    _attach_files_to_msg,
    _build_fields_for_contact,
    _resolve_template_server,
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


@bp.route('/api/lettere/aziende/<int:azienda_id>/risposta', methods=['PUT'])
@login_required
def api_lettere_risposta(azienda_id):
    """Aggiorna spunta di risposta per una azienda in una generazione."""
    try:
        data = request.get_json() or {}
        out = lettere_store.set_risposta(
            azienda_id,
            bool(data.get('risposta')),
            note=data.get('note') if 'note' in data else None,
            user=_get_username(),
        )
        return jsonify({'success': True, **out})
    except LookupError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        logger.exception("[Lettere] Errore update risposta")
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


def _plain_from_html(html):
    text = re.sub(r'<a[^>]*href="([^"]*?)"[^>]*>(.*?)</a>', r'\2 (\1)', html or '')
    return re.sub(r'<[^>]+>', '', text)


def _build_email_message(
    *,
    template_id,
    contatto,
    email_dest,
    subject_override=None,
    body_override=None,
    allegati_nomi=None,
    dynamic_files=None,
):
    tmpl_data = json_read(_TEMPLATES_JSON, default=None)
    if tmpl_data is None:
        raise RuntimeError('Impossibile caricare i template')

    tmpl = tmpl_data.get('templates', {}).get(template_id)
    if not tmpl or tmpl.get('type') != 'email':
        raise ValueError(f'Template "{template_id}" non valido')

    field_defs = tmpl_data.get('fields', {})
    tmpl_settings = tmpl_data.get('settings', {})
    email_tecnico = tmpl_settings.get(f'email_{PROVINCIA_ATTIVA.lower()}', '')

    if subject_override and body_override:
        subject = subject_override
        body_html = body_override
    else:
        fields = _build_fields_for_contact(contatto, tmpl, field_defs)
        body_html = _resolve_template_server(
            tmpl['body'], fields,
            conditionals=tmpl.get('conditionals'),
            email_tecnico=email_tecnico,
        )
        subject = _resolve_template_server(
            tmpl.get('subject', ''), fields, email_tecnico=email_tecnico,
        ) if tmpl.get('subject') else f"Sostituzione contatore - {contatto.get('nome', '')}"

    smtp_settings = load_smtp_settings()
    if not smtp_settings.get('host') or not smtp_settings.get('password'):
        raise RuntimeError('Configurazione SMTP incompleta')

    from_email = smtp_settings.get('from_email', smtp_settings.get('username', ''))
    from_name = smtp_settings.get('from_name', 'Ufficio Tecnico Algeco Power')

    body_html_formatted = (body_html or '').replace('\n', '<br>')
    firma_html = tmpl_settings.get('firma_html', '')
    prov_settings = tmpl_settings.get('province', {}).get(PROVINCIA_ATTIVA.upper(), {})
    tel_tecnico = prov_settings.get('telefono') or '011 025 2702'
    if firma_html:
        firma_resolved = mail_manager.resolve_signature_html(
            firma_html, prov=PROVINCIA_ATTIVA, email_tecnico=email_tecnico, telefono_tecnico=tel_tecnico
        )
        body_html_formatted += firma_resolved

    body_plain = _plain_from_html(body_html)
    if firma_html:
        body_plain += (
            f'\n\n--\nUfficio Tecnico\nTelefono: {tel_tecnico}\n'
            f'Mail: {email_tecnico}\nwww.algecopower.it'
        )

    msg_alt = MIMEMultipart('alternative')
    msg_alt.attach(MIMEText(body_plain, 'plain', 'utf-8'))
    msg_alt.attach(MIMEText(body_html_formatted, 'html', 'utf-8'))

    allegati_nomi = allegati_nomi or []
    if allegati_nomi:
        msg = MIMEMultipart('mixed')
        msg.attach(msg_alt)
        _attach_files_to_msg(msg, allegati_nomi, dynamic_files=dynamic_files)
    else:
        msg = msg_alt

    import email.utils as email_utils
    msg['From'] = formataddr((from_name, from_email))
    msg['To'] = email_dest
    msg['Subject'] = subject
    msg['Reply-To'] = from_email
    msg['Message-ID'] = email_utils.make_msgid(
        domain=from_email.split('@')[-1] if '@' in from_email else 'algecopower.it'
    )
    msg['Date'] = email_utils.formatdate(localtime=True)
    return msg, smtp_settings, subject


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
    nome_azienda = azienda.get('nominativo') or contatto.get('nome') or 'Azienda'

    # Genera allegati dinamici (es. Excel con elenco contatori se N > 2)
    dynamic_files = {}
    effective_contatori = contatori_list or [contatto]

    for nom in (allegati_nomi or []):
        if nom.lower().endswith('.xlsx'):
            excel_bytes = generate_contatori_excel(effective_contatori, nome_azienda)
            dynamic_files[nom] = excel_bytes

    if not dynamic_files and len(effective_contatori) > 2:
        safe_nome = sanitize_filename(nome_azienda)
        auto_excel_name = f"Elenco_Contatori_{safe_nome}.xlsx"
        excel_bytes = generate_contatori_excel(effective_contatori, nome_azienda)
        dynamic_files[auto_excel_name] = excel_bytes
        if auto_excel_name not in allegati_nomi:
            allegati_nomi.append(auto_excel_name)

    subject = data.get('subject_override') or ''

    try:
        msg, smtp_settings, subject = _build_email_message(
            template_id=template_id,
            contatto=contatto,
            email_dest=email_dest,
            subject_override=data.get('subject_override'),
            body_override=data.get('body_override'),
            allegati_nomi=allegati_nomi,
            dynamic_files=dynamic_files,
        )

        ok_send, send_err = send_smtp_message(
            smtp_settings, msg, max_retries=3, base_delay=6, save_sent=True
        )
        if not ok_send:
            raise RuntimeError(send_err or 'Errore invio email')

        # Registra l'invio su tutte le aziende/contatori coinvolti e scrivi la nota sul DB
        for az_id in azienda_ids:
            az_item = lettere_store.get_azienda(az_id)
            if not az_item:
                continue
            pod_val = (az_item.get('pod') or '').strip()
            data_prevista = az_item.get('data_pianificazione') or contatto.get('data_intervento') or ''

            lettere_store.registra_invio(
                azienda_generazione_id=az_item['id'],
                generazione_id=az_item['generazione_id'],
                contact_key_value=az_item['contact_key'],
                pod=pod_val,
                email_dest=email_dest,
                template_id=template_id,
                subject=subject,
                status='sent',
                user=username,
                detail={'allegati': allegati_nomi, 'contatori_count': len(effective_contatori)},
            )

            # Scrivi nota sul DB locale (es. "18/08 mandata mail per 25/08")
            if pod_val:
                nota_invio = build_nota_invio('mail', data_intervento=data_prevista)
                registra_nota_su_db(pod_val, nota_invio, user=username, origin='lettere_invia_email')

        return jsonify({'ok': True, 'azienda_id': primary_id, 'azienda_ids': azienda_ids, 'email': email_dest})

    except smtplib.SMTPAuthenticationError:
        err = 'Autenticazione SMTP fallita'
    except Exception as e:
        err = str(e)

    for az_id in azienda_ids:
        az_item = lettere_store.get_azienda(az_id)
        if not az_item:
            continue
        lettere_store.registra_invio(
            azienda_generazione_id=az_item['id'],
            generazione_id=az_item['generazione_id'],
            contact_key_value=az_item['contact_key'],
            pod=az_item.get('pod') or '',
            email_dest=email_dest,
            template_id=template_id,
            subject=subject,
            status='failed',
            error=err,
            user=username,
            detail={'allegati': allegati_nomi, 'contatori_count': len(effective_contatori)},
        )
    logger.error(f"[Lettere] Errore invio a {email_dest}: {err}")
    return jsonify({'ok': False, 'azienda_id': primary_id, 'email': email_dest, 'errore': err}), 500
