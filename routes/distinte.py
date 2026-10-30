# -*- coding: utf-8 -*-
"""
Blueprint per le API PDF E-Distribuzione — gestione distinte e moduli maxy.
"""
import io
import os
import re
import tempfile
import logging
from email.mime.base import MIMEBase
from email.mime.application import MIMEApplication
from email import encoders

from flask import Blueprint, request, jsonify, send_file

from moduli.auth import login_required
from moduli.safe_json import json_read, json_write
from moduli.bulk_contatori_email import (
    generate_contatori_excel,
    sanitize_filename,
    build_nota_invio,
    registra_nota_su_db,
    send_contatori_email,
)
from moduli.shared import (
    get_header_map, find_row_by_pod,
    build_pod_row_map, safe_batch_update_by_name, _imposta_appuntamento_su_filone, _rimuovi_appuntamento_da_filone,
    _api_error, _resolve_anchor_col_name,
    _callbacks, _get_username,
)
from moduli.smtp import (
    load_smtp_settings,
    save_smtp_settings,
    get_safe_smtp_settings,
    test_smtp_connection as core_test_smtp,
    test_send_email as core_test_send_email,
)
from moduli.sms_gateway import (
    load_sms_settings,
    save_sms_settings,
    get_safe_sms_settings,
    send_sms,
    test_sms_gateway,
)
from moduli.distinte_manager import (
    parse_pdf_auto,
    classifica_contatti,
    confronta_con_filone,
)
from moduli.rilevamento_aziende import (
    arricchisci_contatti,
)
from config import PROVINCIA_ATTIVA

logger = logging.getLogger('mappa_algeco')

bp = Blueprint('distinte', __name__)

# Percorso root del progetto (una directory sopra moduli/)
_ROOT_DIR = os.path.join(os.path.dirname(__file__), '..')


@bp.route('/distinte')
@login_required
def serve_distinte_page():
    """Serve la pagina di gestione distinte."""
    return send_file(os.path.join(_ROOT_DIR, 'templates', 'distinte.html'))


@bp.route('/whatsapp')
@login_required
def serve_whatsapp_redirect():
    """Redirect alla nuova pagina distinte (retrocompatibilità)."""
    return send_file(os.path.join(_ROOT_DIR, 'templates', 'distinte.html'))


# Percorso del file JSON dei template messaggi (default globale + override per-provincia)
_TEMPLATES_JSON = os.path.join(_ROOT_DIR, 'data', 'distinte_templates.json')
_TEMPLATES_JSON_DEFAULT = _TEMPLATES_JSON
_ALLEGATI_DIR = os.path.join(_ROOT_DIR, 'data', 'allegati')


def _load_templates_data():
    """Carica i template: base da data/distinte_templates.json unita con override data/{provincia}/distinte_templates.json."""
    default_file = os.path.join(_ROOT_DIR, 'data', 'distinte_templates.json')
    base_data = json_read(default_file, default=None) or {}
    prov_file = os.path.join(_ROOT_DIR, 'data', PROVINCIA_ATTIVA.lower(), 'distinte_templates.json')
    if os.path.exists(prov_file):
        prov_data = json_read(prov_file, default=None) or {}
        merged = dict(base_data)
        merged.update(prov_data)
        for subkey in ('templates', 'snippets'):
            if subkey in base_data or subkey in prov_data:
                m_sub = dict(base_data.get(subkey, {}))
                m_sub.update(prov_data.get(subkey, {}))
                merged[subkey] = m_sub
        return merged
    return base_data


def _save_templates_data(data):
    """Salva i template modificati nella cartella della provincia attiva."""
    prov_file = os.path.join(_ROOT_DIR, 'data', PROVINCIA_ATTIVA.lower(), 'distinte_templates.json')
    os.makedirs(os.path.dirname(prov_file), exist_ok=True)
    existing = _load_templates_data() or {}
    merged = dict(existing)
    merged.update(data)
    for key in ('settings', 'fields'):
        if key in data and isinstance(data[key], dict):
            merged[key] = dict(existing.get(key, {}))
            merged[key].update(data[key])

    # Per templates e snippets: se presenti nel payload, sostituiscono l'insieme esistente.
    # Eventuali template o snippet eliminati vengono rimossi anche a livello globale.
    from moduli import mail_manager
    for subkey, prefix in (('templates', 'sys_'), ('snippets', 'snip_')):
        if subkey in data and isinstance(data[subkey], dict):
            merged[subkey] = dict(data[subkey])
            old_keys = set(existing.get(subkey, {}).keys())
            new_keys = set(data[subkey].keys())
            deleted_keys = old_keys - new_keys
            for del_k in deleted_keys:
                mail_manager.delete_template_or_snippet(f"{prefix}{del_k}", prov=PROVINCIA_ATTIVA)

    return json_write(prov_file, merged, indent=4)


# Mappa allegati di default per provincia
_ALLEGATI_DEFAULT = {
    'TORINO': ['attestazione_piemonte.pdf'],
    'PAVIA': ['attestazione_lombardia.pdf'],
    'LECCO': ['attestazione_lombardia.pdf'],
}
# Allegato aggiuntivo per contatori semidiretti
_ALLEGATO_SEMIDIRETTA = 'informativa_semidiretta.pdf'





def _format_data_estesa(date_str):
    """Converte una data in formato esteso italiano (es. 'mercoledì 15 aprile')."""
    if not date_str:
        return date_str
    giorni = ['lunedì', 'martedì', 'mercoledì', 'giovedì', 'venerdì', 'sabato', 'domenica']
    mesi = ['gennaio', 'febbraio', 'marzo', 'aprile', 'maggio', 'giugno',
            'luglio', 'agosto', 'settembre', 'ottobre', 'novembre', 'dicembre']
    from datetime import datetime
    for fmt in ('%d/%m/%Y', '%Y-%m-%d'):
        try:
            d = datetime.strptime(date_str.strip(), fmt)
            return f"{giorni[d.weekday()]} {d.day} {mesi[d.month - 1]}".upper()
        except ValueError:
            continue
    return date_str


def _resolve_template_server(body_str, fields, conditionals=None, email_tecnico='', telefono_tecnico=''):
    """Risolve un template sostituendo i placeholder con i valori dei campi (server-side)."""
    result = body_str or ''
    fields = fields or {}

    distacco_val = str(fields.get('distacco') or 'no').strip().lower()
    fallback_frase = (
        "Facciamo presente che durante l’intervento è previsto un breve distacco della fornitura elettrica."
        if distacco_val in ('si', 'sì') else
        "Precisiamo inoltre che durante l’intervento NON è previsto il distacco della fornitura elettrica (in allegato la specifica tecnica di riferimento)."
    )

    # Risolvi condizionali (es. {distacco_text})
    if conditionals:
        for key, cond in conditionals.items():
            cond_field = cond.get('field') or ('distacco' if 'distacco' in key else key)
            field_val = fields.get(cond_field, distacco_val if cond_field == 'distacco' else '')
            replacement = cond.get('values', {}).get(field_val, fallback_frase if 'distacco' in key else '')
            result = re.sub(r'\{' + key + r'\}', replacement, result)

    custom_frase = fields.get('frase_distacco') or fields.get('distacco_text')
    final_frase = custom_frase if custom_frase and custom_frase.strip() else fallback_frase

    result = result.replace('{frase_distacco}', final_frase)
    result = result.replace('{distacco_text}', final_frase)

    # Risolvi {email_tecnico}
    result = result.replace('{email_tecnico}', email_tecnico)
    # Risolvi {telefono_tecnico}, {telefono_ufficio}, {telefono_tecnico_clean}
    tel = telefono_tecnico
    if not tel:
        from moduli import mail_manager
        st = mail_manager.get_template_settings(PROVINCIA_ATTIVA)
        pv = st.get('province', {}).get(PROVINCIA_ATTIVA.upper(), {})
        tel = pv.get('telefono') or '011 025 2702'
    tel_clean = re.sub(r'[^0-9+]', '', str(tel))
    result = result.replace('{telefono_tecnico}', str(tel))
    result = result.replace('{telefono_ufficio}', str(tel))
    result = result.replace('{telefono_tecnico_clean}', tel_clean)
    # Risolvi tutti i {campo} — formatta date in formato esteso
    date_fields = {'data', 'nuova_data'}
    for k, v in fields.items():
        val = _format_data_estesa(v) if k in date_fields else (v or '')
        result = result.replace('{' + k + '}', val)
    return result


def _build_fields_for_contact(contatto, template_def, field_defs):
    """Costruisce i valori dei campi auto-compilati per un contatto, come fa il frontend."""
    fields = {}
    for field_id in template_def.get('fields', []):
        fdef = field_defs.get(field_id, {})
        value = ''
        # autoFillRule (es. distacco)
        if 'autoFillRule' in fdef:
            rule = fdef['autoFillRule']
            src_val = (contatto.get(rule['field']) or '').lower()
            value = rule['then'] if rule.get('contains') and rule['contains'] in src_val else rule['else']
        elif fdef.get('autoFillFrom') is None:
            value = ''
        elif isinstance(fdef.get('autoFillFrom'), list):
            value = ' '.join(contatto.get(k, '') or '' for k in fdef['autoFillFrom']).strip()
        elif fdef.get('autoFillFrom'):
            value = contatto.get(fdef['autoFillFrom'], '') or ''
        fields[field_id] = value

    # Se il template usa il distacco ma il campo non è in template_def.fields, auto-compilalo
    body_str = template_def.get('body') or ''
    needs_distacco = (
        'distacco' in template_def.get('fields', []) or
        '{frase_distacco}' in body_str or
        '{distacco_text}' in body_str or
        any(c.get('field') == 'distacco' for c in (template_def.get('conditionals') or {}).values())
    )
    if needs_distacco and 'distacco' not in fields:
        fdef = field_defs.get('distacco', {})
        rule = fdef.get('autoFillRule')
        if rule:
            src_val = (contatto.get(rule['field']) or contatto.get('tipologia_contatore') or contatto.get('tipo_misuratore') or '').lower()
            fields['distacco'] = rule['then'] if rule.get('contains') and rule['contains'] in src_val else rule['else']
        else:
            tipo_mis = str(contatto.get('tipologia_contatore') or contatto.get('tipo_misuratore') or '').lower()
            fields['distacco'] = 'no' if 'semidiretto' in tipo_mis else 'si'

    return fields


@bp.route('/api/distinte/templates', methods=['GET'])
@bp.route('/api/lettere/templates', methods=['GET'])
@login_required
def get_distinte_templates():
    """Restituisce i template messaggi dal file JSON (per provincia o default)."""
    try:
        data = _load_templates_data()
        if data is None:
            return jsonify({'error': 'File template non trovato'}), 404
        from moduli import mail_manager
        data['settings'] = mail_manager.get_template_settings(PROVINCIA_ATTIVA)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': f'Errore lettura template: {str(e)}'}), 500


@bp.route('/api/distinte/templates', methods=['PUT'])
@login_required
def save_distinte_templates():
    """Salva i template messaggi aggiornati nel file JSON della provincia attiva."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Nessun dato ricevuto'}), 400
        # Normalizza i template WhatsApp e SMS a plain text senza tag HTML, e normalizza i fields per il distacco
        from moduli.mail_manager import html_to_plain_text
        for tk, tv in (data.get('templates') or {}).items():
            if isinstance(tv, dict):
                if tv.get('type') in ('whatsapp', 'sms') or str(tk).startswith('wa_'):
                    if tv.get('body') and ('<' in tv['body'] and '>' in tv['body']):
                        tv['body'] = html_to_plain_text(tv['body'])
                # Normalizza i campi: frase_distacco o distacco_text nei fields vanno rimossi e sostituiti con distacco
                if 'fields' in tv and isinstance(tv['fields'], list):
                    normalized_fields = []
                    needs_distacco = (
                        (tv.get('body') and ('{frase_distacco}' in tv['body'] or '{distacco_text}' in tv['body'])) or
                        any(c.get('field') == 'distacco' for c in (tv.get('conditionals') or {}).values())
                    )
                    for f in tv['fields']:
                        if f in ('frase_distacco', 'distacco_text'):
                            needs_distacco = True
                        elif f not in normalized_fields:
                            normalized_fields.append(f)
                    if needs_distacco and 'distacco' not in normalized_fields:
                        normalized_fields.append('distacco')
                    tv['fields'] = normalized_fields
        _save_templates_data(data)
        return jsonify({'ok': True})
    except Exception as e:
        logger.error(f"Errore salvataggio template: {e}")
        return jsonify({'error': str(e)}), 500


def _get_default_allegati(provincia, tipologia_contatore=''):
    """Restituisce la lista di nomi file allegati di default per provincia e tipo contatore."""
    allegati = list(_ALLEGATI_DEFAULT.get(provincia.upper(), []))
    if 'semidirett' in (tipologia_contatore or '').lower():
        allegati.append(_ALLEGATO_SEMIDIRETTA)
    return allegati


def _get_available_allegati():
    """Restituisce la lista di file disponibili nella cartella allegati."""
    if not os.path.isdir(_ALLEGATI_DIR):
        return []
    return sorted(f for f in os.listdir(_ALLEGATI_DIR)
                  if os.path.isfile(os.path.join(_ALLEGATI_DIR, f)) and not f.startswith('.'))


def _attach_files_to_msg(msg_mixed, allegati_nomi, dynamic_files=None):
    """Allega i PDF/file specificati al messaggio MIME, inclusi file generati dinamicamente (es. Excel)."""
    dynamic_files = dynamic_files or {}
    allegati_ok = []
    for nome in allegati_nomi:
        if nome in dynamic_files:
            try:
                data = dynamic_files[nome]
                part = MIMEApplication(data, _subtype='vnd.openxmlformats-officedocument.spreadsheetml.sheet')
                part.add_header('Content-Disposition', 'attachment', filename=nome)
                msg_mixed.attach(part)
                allegati_ok.append(nome)
                continue
            except Exception as e:
                logger.warning(f"[ALLEGATI] Errore allegato dinamico {nome}: {e}")
                continue
        filepath = os.path.join(_ALLEGATI_DIR, os.path.basename(nome))  # sanitize
        if not os.path.isfile(filepath):
            logger.warning(f"[ALLEGATI] File non trovato: {filepath}")
            continue
        try:
            with open(filepath, 'rb') as f:
                part = MIMEBase('application', 'octet-stream')
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', 'attachment', filename=nome)
            msg_mixed.attach(part)
            allegati_ok.append(nome)
        except Exception as e:
            logger.warning(f"[ALLEGATI] Errore lettura {nome}: {e}")
    return allegati_ok


@bp.route('/api/distinte/allegati', methods=['GET'])
@login_required
def get_allegati_disponibili():
    """Restituisce la lista di allegati disponibili e i default per provincia/tipo."""
    disponibili = _get_available_allegati()
    return jsonify({
        'ok': True,
        'disponibili': disponibili,
        'default_provincia': _ALLEGATI_DEFAULT,
        'allegato_semidiretta': _ALLEGATO_SEMIDIRETTA,
    })


@bp.route('/api/allegati/file/<path:filename>', methods=['GET'])
@bp.route('/api/distinte/allegato/<path:filename>', methods=['GET'])
@bp.route('/api/lettere/allegato/<path:filename>', methods=['GET'])
@login_required
def download_allegato_file(filename):
    """Serve un file statico dalla cartella data/allegati per anteprima e download, con fallback per Excel dinamici."""
    safe_name = os.path.basename(filename)
    filepath = os.path.join(_ALLEGATI_DIR, safe_name)
    if not os.path.isfile(filepath):
        # Fallback 1: File Excel Comuni (Contatori_<Comune>.xlsx)
        if safe_name.startswith('Contatori_') and safe_name.lower().endswith('.xlsx'):
            comune_name = safe_name[len('Contatori_'):-len('.xlsx')]
            try:
                from moduli.comuni_manager import generate_excel_contatori_comune
                excel_bytes = generate_excel_contatori_comune(comune_name)
                if excel_bytes:
                    return send_file(
                        io.BytesIO(excel_bytes),
                        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        as_attachment=True,
                        download_name=safe_name,
                    )
            except Exception as e:
                logger.warning(f"Errore generazione fallback Excel comune {comune_name}: {e}")

        # Fallback 2: File Excel Elenco Contatori (Elenco_Contatori_<Nome>.xlsx)
        if safe_name.startswith('Elenco_Contatori_') and safe_name.lower().endswith('.xlsx'):
            nome_az = safe_name[len('Elenco_Contatori_'):-len('.xlsx')].replace('_', ' ')
            excel_bytes = generate_contatori_excel([], nome_azienda=nome_az)
            return send_file(
                io.BytesIO(excel_bytes),
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                as_attachment=True,
                download_name=safe_name,
            )

        return jsonify({'error': 'File allegato non trovato'}), 404

    mimetype = 'application/pdf' if safe_name.lower().endswith('.pdf') else None
    return send_file(
        filepath,
        mimetype=mimetype,
        as_attachment=False,
        download_name=safe_name,
    )


@bp.route('/api/allegati/anteprima-excel-contatori', methods=['GET', 'POST'])
@bp.route('/api/distinte/anteprima-excel-contatori', methods=['GET', 'POST'])
@bp.route('/api/lettere/anteprima-excel-contatori', methods=['GET', 'POST'])
@login_required
def api_anteprima_excel_contatori():
    """Genera al volo il file Excel con l'elenco contatori associato per l'anteprima/download."""
    if request.method == 'GET':
        data = request.args
        contatori = []
    else:
        data = request.get_json() or {}
        contatori = data.get('contatori') or []

    nome_azienda = data.get('nome_azienda') or 'Azienda'
    safe_nome = sanitize_filename(nome_azienda)
    filename = data.get('filename') or f"Elenco_Contatori_{safe_nome}.xlsx"
    if not filename.lower().endswith('.xlsx'):
        filename += '.xlsx'

    if not contatori:
        contatori = [{
            'pod': '',
            'tipologia_contatore': '',
            'indirizzo': '',
            'civico': '',
            'comune': '',
            'matricola': '',
            'data_intervento': '',
        }]

    excel_bytes = generate_contatori_excel(contatori, nome_azienda=nome_azienda)
    return send_file(
        io.BytesIO(excel_bytes),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=filename,
    )


ALLOWED_UPLOAD_EXTENSIONS = {
    '.pdf', '.xlsx', '.xls', '.docx', '.doc', '.png', '.jpg', '.jpeg', '.txt', '.zip'
}


@bp.route('/api/allegati/upload', methods=['POST'])
@bp.route('/api/distinte/allegati/upload', methods=['POST'])
@bp.route('/api/lettere/allegati/upload', methods=['POST'])
@login_required
def api_upload_allegato():
    """Carica un nuovo file allegato nella cartella data/allegati per usarlo negli invii."""
    if 'file' not in request.files:
        return jsonify({'ok': False, 'error': 'Nessun file inviato'}), 400

    file = request.files['file']
    if not file or not file.filename:
        return jsonify({'ok': False, 'error': 'Nome file non valido'}), 400

    original_name = os.path.basename(file.filename)
    base_name, ext = os.path.splitext(original_name)
    clean_ext = ext.lower().strip()

    if clean_ext not in ALLOWED_UPLOAD_EXTENSIONS:
        return jsonify({
            'ok': False,
            'error': f'Estensione non consentita ({clean_ext}). Ammessi: PDF, Excel, Word, Immagini, ZIP.'
        }), 400

    safe_base = sanitize_filename(base_name)
    safe_name = f"{safe_base}{clean_ext}"

    os.makedirs(_ALLEGATI_DIR, exist_ok=True)
    target_path = os.path.join(_ALLEGATI_DIR, safe_name)
    file.save(target_path)

    disponibili = _get_available_allegati()
    return jsonify({
        'ok': True,
        'filename': safe_name,
        'disponibili': disponibili,
        'message': f'File "{safe_name}" caricato con successo'
    })


@bp.route('/api/distinte/smtp-settings', methods=['GET'])
@login_required
def api_distinte_get_smtp_settings():
    """Restituisce le impostazioni SMTP per la provincia corrente."""
    try:
        return jsonify(get_safe_smtp_settings(PROVINCIA_ATTIVA))
    except Exception as e:
        logger.exception("[distinte] Errore lettura smtp-settings")
        return jsonify({'error': str(e)}), 500


@bp.route('/api/distinte/smtp-settings', methods=['PUT'])
@login_required
def api_distinte_save_smtp_settings():
    """Salva le impostazioni SMTP per la provincia corrente."""
    try:
        data = request.get_json() or {}
        save_smtp_settings(data, PROVINCIA_ATTIVA)
        return jsonify({'ok': True, 'message': 'Impostazioni SMTP salvate con successo'})
    except Exception as e:
        logger.exception("[distinte] Errore salvataggio smtp-settings")
        return jsonify({'error': str(e)}), 500


@bp.route('/api/distinte/smtp-test', methods=['POST'])
@login_required
def test_smtp_connection():
    """Testa la connessione e login SMTP con le impostazioni passate o correnti."""
    try:
        data = request.get_json() or {}
        settings = data.get('settings') or data
        if not settings or not settings.get('host'):
            settings = load_smtp_settings(PROVINCIA_ATTIVA)
        ok, msg = core_test_smtp(settings)
        if ok:
            return jsonify({'ok': True, 'message': msg})
        return jsonify({'ok': False, 'error': msg, 'errore': msg}), 400
    except Exception as e:
        logger.error(f"[distinte] Test SMTP fallito: {e}")
        return jsonify({'ok': False, 'error': str(e), 'errore': str(e)}), 500


@bp.route('/api/distinte/invia-email-prova', methods=['POST'])
@login_required
def invia_email_prova():
    """Invia un'email di prova all'indirizzo specificato."""
    try:
        data = request.get_json() or {}
        email_dest = (data.get('email') or data.get('recipient') or '').strip()
        settings = data.get('settings') or load_smtp_settings(PROVINCIA_ATTIVA)
        ok, msg = core_test_send_email(settings, email_dest, prov=PROVINCIA_ATTIVA)
        if ok:
            return jsonify({'ok': True, 'message': msg, 'email': email_dest})
        return jsonify({'ok': False, 'error': msg, 'errore': msg}), 400
    except Exception as e:
        logger.error(f"[EMAIL-TEST] Errore invio prova: {e}")
        return jsonify({'ok': False, 'error': str(e), 'errore': str(e)}), 500


@bp.route('/api/distinte/invia-singola-email', methods=['POST'])
@bp.route('/api/lettere/invia-singola-email', methods=['POST'])
@login_required
def invia_singola_email():
    """
    Invia UNA singola email usando il motore di dominio unificato send_contatori_email.
    Supporta contatori singoli o multipli per azienda, allegati dinamici e note automatiche.
    """
    try:
        data = request.get_json() or {}
        template_id = data.get('template_id', '')
        contatto = data.get('contatto')
        subject_override = data.get('subject_override')
        body_override = data.get('body_override')
        allegati_nomi = data.get('allegati', [])
        contatori = data.get('contatori')
        context = data.get('context') or 'distinte'

        if not contatto and not data.get('email'):
            return jsonify({'error': 'Contatto mancante'}), 400

        email_dest = ((contatto.get('email') if contatto else '') or data.get('email') or '').strip()
        username = _get_username()

        res = send_contatori_email(
            email_dest=email_dest,
            subject=subject_override,
            body_html=body_override,
            template_id=template_id,
            contatto=contatto,
            contatori_list=contatori,
            allegati_nomi=allegati_nomi,
            provincia=PROVINCIA_ATTIVA,
            username=username,
            context=context
        )

        status_code = 200 if res.get('ok') else (401 if res.get('error_code') == 'SMTP_AUTH_FAILED' else 400)
        return jsonify(res), status_code
    except Exception as e:
        logger.exception(f"[EMAIL] Errore invio singola email distinte: {e}")
        return jsonify({'ok': False, 'error': str(e), 'errore': str(e)}), 500


@bp.route('/api/distinte/sms-settings', methods=['GET'])
@bp.route('/api/lettere/sms-settings', methods=['GET'])
@login_required
def api_distinte_sms_settings():
    """Restituisce la configurazione completa del gateway SMS per la provincia corrente."""
    try:
        return jsonify(get_safe_sms_settings(PROVINCIA_ATTIVA))
    except Exception as e:
        logger.error(f"[SMS] Errore lettura config distinte: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


@bp.route('/api/distinte/sms-settings', methods=['PUT'])
@bp.route('/api/lettere/sms-settings', methods=['PUT'])
@login_required
def api_distinte_save_sms_settings():
    """Salva le impostazioni SMS Gateway per la provincia corrente direttamente dalla gestione distinte."""
    try:
        data = request.get_json() or {}
        save_sms_settings(data, PROVINCIA_ATTIVA)
        return jsonify({'ok': True, 'message': 'Impostazioni salvate con successo'})
    except Exception as e:
        logger.exception('[distinte] errore salvataggio sms-settings')
        return jsonify({'ok': False, 'error': str(e)}), 500


@bp.route('/api/distinte/sms-test', methods=['POST'])
@bp.route('/api/lettere/sms-test', methods=['POST'])
@login_required
def api_distinte_test_sms():
    """Esegue un invio di prova SMS direttamente dalla gestione distinte."""
    try:
        data = request.get_json() or {}
        test_phone = (data.get('test_phone') or data.get('phone') or '').strip()
        if not test_phone:
            return jsonify({'ok': False, 'error': 'Numero di telefono test mancante'}), 400

        settings = data.get('settings')
        if not settings:
            if 'provider' in data or 'endpoint_url' in data:
                settings = data
            else:
                settings = load_sms_settings(PROVINCIA_ATTIVA)

        ok, msg = test_sms_gateway(settings, test_phone, prov=PROVINCIA_ATTIVA)
        return jsonify({'ok': ok, 'message': msg if ok else None, 'error': None if ok else msg})
    except Exception as e:
        logger.exception('[distinte] errore /api/distinte/sms-test')
        return jsonify({'ok': False, 'error': str(e)}), 500


@bp.route('/api/distinte/invia-singolo-sms', methods=['POST'])
@bp.route('/api/lettere/invia-singolo-sms', methods=['POST'])
@login_required
def invia_singolo_sms():
    """
    Invia un singolo SMS tramite SMS Gateway configurato.
    Body: {
        "template_id": str,
        "contatto": {"pod": str, "telefono": str, "nome": str, ...},
        "testo_sms": str
    }
    """
    try:
        data = request.get_json() or {}
        contatto = data.get('contatto')
        testo_sms = (data.get('testo_sms') or data.get('body_override') or data.get('testo') or '').strip()

        if not contatto:
            if data.get('telefono') or data.get('pod'):
                contatto = {
                    'pod': data.get('pod'),
                    'telefono': data.get('telefono'),
                    'nome': data.get('nome'),
                    'data_pian_filone': data.get('data_pian_filone') or data.get('data'),
                }
            else:
                return jsonify({'ok': False, 'errore': 'Contatto mancante', 'error': 'Contatto mancante'}), 400

        if not testo_sms:
            return jsonify({'ok': False, 'errore': 'Testo SMS vuoto', 'error': 'Testo SMS vuoto'}), 400

        telefono = (contatto.get('telefono') or contatto.get('cellulare') or contatto.get('dest_telefono') or '').strip()
        pod = contatto.get('pod', '?')

        if not telefono:
            return jsonify({'ok': False, 'pod': pod, 'errore': 'Numero di telefono mancante', 'error': 'Numero di telefono mancante'}), 400

        settings = load_sms_settings(PROVINCIA_ATTIVA)
        if not settings.get('enabled'):
            return jsonify({'ok': False, 'pod': pod, 'errore': 'SMS Gateway disabilitato nelle impostazioni', 'error': 'SMS Gateway disabilitato nelle impostazioni'}), 400
        if not settings.get('endpoint_url'):
            return jsonify({'ok': False, 'pod': pod, 'errore': 'SMS Gateway non configurato', 'error': 'SMS Gateway non configurato'}), 400

        ok, info, err = send_sms(telefono, testo_sms, settings=settings, prov=PROVINCIA_ATTIVA)
        if not ok:
            return jsonify({'ok': False, 'pod': pod, 'telefono': telefono, 'errore': err, 'error': err}), 500

        username = _get_username()
        c_data = contatto.get('data_intervento') or contatto.get('data_pian_filone')

        c_lcl = (contatto.get('lcl') or data.get('lcl') or '').strip()
        c_eneltel = (contatto.get('eneltel') or data.get('eneltel') or '').strip()

        # Registra nota sul DB locale (es. "18/08 mandato SMS per 25/08")
        if (pod and pod != '?') or c_eneltel:
            nota_invio = build_nota_invio('sms', data_intervento=c_data)
            registra_nota_su_db(pod, nota_invio, user=username, origin='distinte_invia_sms', lcl=c_lcl, eneltel=c_eneltel)

        # Audit log su db locale se disponibile
        try:
            from moduli import db as _db
            _db.record_audit_log(
                action='send_sms',
                table_name='filone',
                record_id=pod,
                user=username,
                details={
                    'telefono': telefono,
                    'parts': info.get('parts', 1),
                    'gateway_id': info.get('gateway_id', ''),
                    'provider': info.get('provider', '')
                }
            )
        except Exception:
            pass

        logger.info(f"[SMS] Inviato a {telefono} (POD {pod})")
        return jsonify({
            'ok': True,
            'pod': pod,
            'telefono': telefono,
            'parts': info.get('parts', 1),
            'parti_sms': info.get('parts', 1),
            'gateway_id': info.get('gateway_id', ''),
        })

    except Exception as e:
        logger.exception(f"[SMS] Errore invio: {e}")
        return jsonify({'ok': False, 'pod': contatto.get('pod', '?') if isinstance(contatto, dict) else '?', 'errore': str(e)}), 500


@bp.route('/api/distinte/parse-distinta', methods=['POST'])
@login_required
def parse_distinta_endpoint():
    """
    Riceve file PDF (DistintaBase o Modulo Maxy), li elabora e restituisce i contatti.
    Confronta anche con i dati del filone per rilevare differenze.
    
    Form fields:
        files: file PDF da elaborare
        tipo_documento: 'auto', 'distinta' o 'modulo_maxy' (default: 'auto')
    """
    try:
        if 'files' not in request.files:
            return jsonify({'error': 'Nessun file caricato'}), 400

        files = request.files.getlist('files')
        if not files or all(f.filename == '' for f in files):
            return jsonify({'error': 'Nessun file selezionato'}), 400

        # Tipo documento: auto, distinta, modulo_maxy
        tipo_documento = request.form.get('tipo_documento', 'auto').strip().lower()
        if tipo_documento not in ('auto', 'distinta', 'modulo_maxy'):
            tipo_documento = 'auto'

        # Salva temporaneamente i file e elaborali
        temp_paths = []
        all_lavori = []
        tipo_rilevato = None

        try:
            for file in files:
                if file and file.filename:
                    # Crea file temporaneo
                    temp_fd, temp_path = tempfile.mkstemp(suffix='.pdf')
                    os.close(temp_fd)
                    file.save(temp_path)
                    temp_paths.append(temp_path)

                    # Parsa il PDF con auto-detect o tipo forzato
                    lavori, tipo_file = parse_pdf_auto(temp_path, tipo_documento)
                    if tipo_rilevato is None:
                        tipo_rilevato = tipo_file
                    logger.info(f"[DISTINTE] Estratti {len(lavori)} lavori da {file.filename} (tipo: {tipo_file})")
                    all_lavori.extend(lavori)
        finally:
            # Pulisci file temporanei
            for path in temp_paths:
                try:
                    os.unlink(path)
                except Exception:
                    pass

        if not all_lavori:
            return jsonify({
                'error': 'Nessun lavoro estratto dai PDF',
                'contatti': [],
                'differenze': []
            }), 400

        # Carica dati filone per confronto e classificazione (prioritizza LCL attivi e note popolate)
        dati_filone = {}
        differenze = []
        try:
            from moduli import db as _db
            pods_distinta = [l.get('pod') for l in all_lavori if l.get('pod')]
            dati_filone = _db.get_dati_filone_dict(pods=pods_distinta)

            # Confronta distinta con filone
            differenze = confronta_con_filone(all_lavori, dati_filone)
            logger.info(f"[DISTINTE] Rilevate {len(differenze)} differenze con il filone (su {len(dati_filone)} contatori caricati)")

        except Exception as e:
            logger.error(f"[DISTINTE] Errore caricamento filone: {e}")

        # Classifica i contatti per tipo di telefono (passa dati filone per trovare numeri alternativi)
        classificati = classifica_contatti(all_lavori, dati_filone)

        # Prepara lista contatti con tipo
        contatti = []
        for lavoro in classificati['cellulare']:
            lavoro['tipo'] = 'cellulare'
            contatti.append(lavoro)
        for lavoro in classificati['fisso']:
            lavoro['tipo'] = 'fisso'
            contatti.append(lavoro)
        for lavoro in classificati['senza_telefono']:
            lavoro['tipo'] = 'senza_telefono'
            contatti.append(lavoro)

        # Ordina per numero lavoro
        contatti.sort(key=lambda x: x.get('lavoro', 999))

        # Aggiungi le note, la data e il telefono del filone ai contatti
        logger.info(f"[DISTINTE] dati_filone contiene {len(dati_filone)} PODs")
        for contatto in contatti:
            pod = contatto.get('pod', '').strip().upper()
            # Inizializza defaults per tutti
            contatto.setdefault('note_filone', '')
            contatto.setdefault('data_pian_filone', '')
            contatto.setdefault('telefono_filone', '')
            contatto.setdefault('email_filone', '')
            contatto.setdefault('matricola_filone', '')
            contatto.setdefault('matricola_operatore', '')
            contatto.setdefault('pod_filone', '')
            contatto.setdefault('eneltel_filone', '')
            contatto.setdefault('lcl_filone', '')
            contatto.setdefault('tipologia_contatore', '')
            contatto.setdefault('posizione_contatore', '')
            if pod in dati_filone:
                contatto['note_filone'] = dati_filone[pod].get('note', '')
                contatto['data_pian_filone'] = dati_filone[pod].get('data_pian', '')
                contatto['telefono_filone'] = dati_filone[pod].get('telefono', '')
                contatto['email_filone'] = dati_filone[pod].get('email', '')
                contatto['matricola_filone'] = dati_filone[pod].get('matricola', '')
                contatto['pod_filone'] = dati_filone[pod].get('pod', '')
                contatto['eneltel_filone'] = dati_filone[pod].get('eneltel', '')
                contatto['lcl_filone'] = dati_filone[pod].get('lcl', '')
                if not contatto.get('lcl') and dati_filone[pod].get('lcl'):
                    contatto['lcl'] = dati_filone[pod]['lcl']
                if not contatto.get('eneltel') and dati_filone[pod].get('eneltel'):
                    contatto['eneltel'] = dati_filone[pod]['eneltel']
                contatto['tipologia_contatore'] = dati_filone[pod].get('tipologia', '')
                contatto['posizione_contatore'] = dati_filone[pod].get('posizione', '')
                # Se il nome dalla distinta è vuoto, usa il nominativo dal filone
                if not contatto.get('nome', '').strip():
                    nominativo_filone = dati_filone[pod].get('nominativo', '').strip()
                    if nominativo_filone:
                        # Rimuovi eventuali numeri di telefono dal nominativo
                        nome_pulito = re.sub(r'\b\d{9,13}\b', '', nominativo_filone).strip()
                        nome_pulito = re.sub(r'\s{2,}', ' ', nome_pulito).strip()
                        if nome_pulito:
                            contatto['nome'] = nome_pulito
                            contatto['nome_da_filone'] = True
                # Se il comune dalla distinta è vuoto, usa il comune dal filone
                if not contatto.get('comune', '').strip():
                    comune_filone = dati_filone[pod].get('comune', '').strip()
                    if comune_filone:
                        contatto['comune'] = comune_filone
                # Se l'indirizzo dalla distinta è vuoto, usa quello dal filone
                if not contatto.get('indirizzo', '').strip():
                    indirizzo_filone = dati_filone[pod].get('indirizzo', '').strip()
                    if indirizzo_filone:
                        contatto['indirizzo'] = indirizzo_filone
                if not contatto.get('civico', '').strip():
                    civico_filone = dati_filone[pod].get('civico', '').strip()
                    if civico_filone:
                        contatto['civico'] = civico_filone
                if contatto['telefono_filone']:
                    logger.info(f"[DISTINTE] POD {pod}: telefono_filone='{contatto['telefono_filone']}'")
            else:
                if pod:
                    logger.info(f"[DISTINTE] POD {pod}: NON trovato in dati_filone")

        # Arricchisci con info tipo contatto (azienda/privato)
        try:
            contatti = arricchisci_contatti(contatti)
            n_aziende = sum(1 for c in contatti if c.get('is_azienda'))
            logger.info(f"[DISTINTE] Rilevate {n_aziende} aziende/enti")
        except Exception as e:
            logger.error(f"[DISTINTE] Errore rilevamento aziende: {e}")

        return jsonify({
            'success': True,
            'contatti': contatti,
            'differenze': differenze,
            'totale': len(contatti),
            'cellulare': len(classificati['cellulare']),
            'fisso': len(classificati['fisso']),
            'senza_telefono': len(classificati['senza_telefono']),
            'tipo_documento': tipo_rilevato or 'distinta',
        })

    except Exception as e:
        return _api_error(e, 'parsing distinta')


@bp.route('/api/distinte/sovrascrivi', methods=['POST'])
@login_required
def sovrascrivi_valore_filone():
    """
    Sovrascrive un valore sul filone con quello della distinta.
    """
    try:
        data = request.json
        pod = data.get('pod', '').strip().upper()
        campo = data.get('campo', '')
        valore = data.get('valore', '')

        if not pod:
            return jsonify({'error': 'POD mancante'}), 400

        # Mappa campo UI -> colonna filone
        mappa_campi = {
            'Data intervento': 'DATA PIAN' if PROVINCIA_ATTIVA == 'TORINO' else 'Data_pianificata',
            'Telefono': 'telefono' if PROVINCIA_ATTIVA == 'TORINO' else 'Telefono',
            'Nominativo': 'NOMINATIVO' if PROVINCIA_ATTIVA == 'TORINO' else 'Nominativo',
            'Indirizzo': 'Indirizzo' if PROVINCIA_ATTIVA == 'TORINO' else 'Via',
            'Civico': 'Num' if PROVINCIA_ATTIVA == 'TORINO' else 'civico',
            'Comune': 'COMUNE' if PROVINCIA_ATTIVA == 'TORINO' else 'Comune',
        }

        col_name = mappa_campi.get(campo)
        if not col_name:
            return jsonify({'error': f'Campo "{campo}" non mappabile'}), 400

        row = find_row_by_pod(pod)
        if row is None:
            return jsonify({'error': f'POD {pod} non trovato'}), 404

        # Scrittura SAFE: header refresh atomico + ancora POD per remap riga
        pod_anchor_col = _resolve_anchor_col_name('pod') or 'POD'
        res = safe_batch_update_by_name(
            [{'row': row, 'col': col_name, 'value': valore, 'anchor': pod}],
            anchor_col=pod_anchor_col, anchor_normalize='upper',
        )
        if not res['success']:
            if res.get('invalid_cols'):
                return jsonify({'error': f'Colonna {col_name} non trovata sul foglio'}), 400
            if res.get('lost_anchors'):
                return jsonify({'error': f'POD {pod} non più presente nel foglio'}), 404
            return jsonify({'error': 'Scrittura non completata'}), 500

        _callbacks['trigger_sync_after_edit']()
        return jsonify({
            'success': True,
            'message': f'{campo} aggiornato per POD {pod}',
            'remapped': res['remapped'] > 0,
        })

    except Exception as e:
        return _api_error(e, 'sovrascrittura valore filone')


@bp.route('/api/distinte/aggiorna-operatori', methods=['POST'])
@login_required
def aggiorna_operatori_filone():
    """
    Aggiorna in blocco la colonna Operatore sul filone usando la matricola
    operatore estratta dalle distinte.
    Body: {"contatori": [{"pod": "IT...", "matricola_operatore": "AG18966"}, ...]}

    Anti-spanamento: usa safe_batch_update_by_name con ancora POD; se la
    collega ha modificato struttura del foglio, la scrittura va comunque
    nelle colonne corrette.
    """
    try:
        data = request.json
        contatori = data.get('contatori', [])

        if not contatori:
            return jsonify({'error': 'Nessun contatore da aggiornare'}), 400

        # Trova nome colonna Operatore
        header_map = get_header_map()
        col_operatore_name = None
        for col_name in ['Operatore', 'OPERATORE', 'operatore']:
            if col_name in header_map:
                col_operatore_name = col_name
                break
        if col_operatore_name is None:
            return jsonify({'error': 'Colonna Operatore non trovata nel filone'}), 400

        # Mappa POD→riga (build_pod_row_map ora rilegge gli header freschi)
        pod_map = build_pod_row_map()

        items = []
        errori = []
        for item in contatori:
            pod = (item.get('pod') or '').strip().upper()
            matricola = (item.get('matricola_operatore') or '').strip()
            if not pod or not matricola:
                continue
            row = pod_map.get(pod)
            if row is None:
                errori.append(f'POD {pod} non trovato')
                continue
            items.append({
                'row': row,
                'col': col_operatore_name,
                'value': matricola,
                'anchor': pod,
            })

        res = {'written': 0, 'remapped': 0, 'lost_anchors': [], 'invalid_cols': []}
        if items:
            pod_anchor_col = _resolve_anchor_col_name('pod') or 'POD'
            res = safe_batch_update_by_name(
                items, anchor_col=pod_anchor_col, anchor_normalize='upper',
            )
            for la in res['lost_anchors']:
                errori.append(f"POD {la['anchor']} non più nel foglio")
            _callbacks['trigger_sync_after_edit']()

        return jsonify({
            'success': True,
            'aggiornati': res['written'],
            'remapped': res['remapped'],
            'errori': errori,
            'message': f"Operatore aggiornato per {res['written']} contatori"
                       + (f' ({len(errori)} errori)' if errori else '')
        })

    except Exception as e:
        return _api_error(e, 'aggiornamento operatori')


@bp.route('/api/distinte/salva-nota', methods=['POST'])
@bp.route('/api/lettere/salva-nota', methods=['POST'])
@login_required
def salva_nota_distinte():
    """
    Salva una nota nel campo note del filone.
    Concatena con // se c'è già qualcosa.

    Anti-spanamento: la lettura della nota corrente E la scrittura della
    nuova avvengono nello stesso blocco lockato con header FRESCHI, così
    indice colonna è sempre quello attuale e (via ancora POD) la riga è
    quella giusta.
    """
    try:
        data = request.json or {}
        pod = data.get('pod', '').strip().upper()
        eneltel = data.get('eneltel', '').strip()
        nota = data.get('nota', '').strip()
        sovrascrivere = data.get('sovrascrivere', True)  # Default: sovrascrive
        lcl = data.get('lcl', '').strip()

        if not pod and not eneltel:
            return jsonify({'error': 'POD o Eneltel mancante'}), 400

        # DB-only: no chiamate Sheets nel path di scrittura. Il drainer pusha.
        from moduli import db as _db
        from moduli.lettere import _get_disabled_lcls, _column_name_for_internal
        from moduli.bulk_contatori_email import format_data_breve
        disabled = _get_disabled_lcls()
        found = None
        if pod:
            found = _db.find_filone_row_by_pod(pod, lcl=lcl, disabled_lcls=disabled)
        if not found and eneltel:
            found = _db.find_filone_row_by_eneltel(eneltel, lcl=lcl, disabled_lcls=disabled)
        if not found:
            return jsonify({'error': f'Contatore {pod or eneltel} non trovato'}), 404
        ek, resolved_lcl = found
        local = _db.get_filone_row(ek, lcl=resolved_lcl) or {}
        nota_attuale = local.get('note_appuntamento') or ''

        # Se la nota è di invio e manca " per GG/MM", arricchiscila con la data di pianificazione se disponibile
        nota_lower = nota.lower()
        if ' per ' not in nota_lower:
            dt_target = local.get('data_pianificata') or local.get('data_intervento')
            dt_breve = format_data_breve(dt_target)
            if dt_breve:
                import datetime as _dt
                oggi_str = _dt.date.today().strftime('%d/%m')
                if 'mandata mail' in nota_lower or 'mandata email' in nota_lower:
                    m = re.match(r'^(\d{1,2}/\d{1,2})\s+', nota)
                    pref = m.group(1) if m else oggi_str
                    nota = f"{pref} mandata mail per {dt_breve}"
                elif 'mandato wa' in nota_lower or 'mandato msg wa' in nota_lower or 'whatsapp' in nota_lower:
                    m = re.match(r'^(\d{1,2}/\d{1,2})\s+', nota)
                    pref = m.group(1) if m else oggi_str
                    nota = f"{pref} mandato wa per {dt_breve}"
                elif 'mandato sms' in nota_lower:
                    m = re.match(r'^(\d{1,2}/\d{1,2})\s+', nota)
                    pref = m.group(1) if m else oggi_str
                    nota = f"{pref} mandato SMS per {dt_breve}"

        if sovrascrivere:
            nuova_nota = nota
        elif not nota:
            nuova_nota = nota_attuale
        elif not nota_attuale:
            nuova_nota = nota
        elif nota not in nota_attuale:
            nuova_nota = f"{nota} // {nota_attuale}"
        else:
            nuova_nota = nota_attuale

        conn = _db.get_conn()
        col_note_name = _column_name_for_internal(conn, 'note_appuntamento', None)
        _db.record_local_change(
            ek, {col_note_name: nuova_nota},
            origin='distinte_salva_nota',
            initial_state='pending',
            lcl=resolved_lcl,
        )
        if pod:
            with conn:
                conn.execute(
                    '''
                    UPDATE lettere_aziende_generazione
                    SET note=?
                    WHERE UPPER(TRIM(pod))=UPPER(TRIM(?))
                    ''',
                    (nuova_nota, pod),
                )
        return jsonify({'success': True, 'nota': nuova_nota})

    except Exception as e:
        return _api_error(e, 'salvataggio nota distinte')


@bp.route('/api/distinte/strikethrough-telefono', methods=['POST'])
@login_required
def strikethrough_telefono():
    """
    Applica o rimuove il barrato (strikethrough) sulla cella telefono nel filone.
    Body: {"pod": "IT...", "strikethrough": true/false}

    Anti-spanamento: risolve sia la colonna TELEFONO sia la riga (via ancora
    POD) con header freschi sotto write-lock.
    """
    return jsonify({
        'success': False,
        'unsupported': True,
        'error': 'Funzione non più disponibile (Google Sheets disconnesso il 21/05/2026).',
    }), 410


@bp.route('/api/distinte/segna-errato', methods=['POST'])
@login_required
def segna_numero_errato():
    """
    Segna un numero come errato nella colonna telefono del filone:
    - Aggiunge " ERRATO" al valore esistente
    - Applica formattazione barrata (strikethrough)
    Body: {"pod": "IT..."}

    Anti-spanamento: lettura+scrittura della cella telefono dentro lo stesso
    write-lock con header freschi e ancora POD, così non scrive in colonne
    sbagliate se la collega ha modificato il foglio.
    """
    return jsonify({
        'success': False,
        'unsupported': True,
        'error': 'Funzione non più disponibile (Google Sheets disconnesso il 21/05/2026).',
    }), 410



@bp.route('/api/distinte/appuntamento', methods=['POST'])
@bp.route('/api/lettere/appuntamento', methods=['POST'])
@login_required
def set_appuntamento_distinte():
    """
    Imposta un appuntamento per un contatore cercato per POD.
    Body: {"pod": "IT...", "orario": "10:00-12:00", "nota_corrente": "...", "data_appuntamento": "..."}
    """
    try:
        data = request.json or {}
        pod = data.get('pod', '').strip().upper()
        orario = data.get('orario', '').strip()
        data_app = data.get('data_appuntamento', '')
        note_extra = data.get('note', '')
        nota_corrente = data.get('nota_corrente')
        lcl = data.get('lcl', '').strip() or None

        if not pod:
            return jsonify({'error': 'POD mancante'}), 400
        if not orario:
            return jsonify({'error': 'Orario richiesto'}), 400

        from moduli import db as _db
        from moduli.lettere import _get_disabled_lcls
        disabled = _get_disabled_lcls()
        found = _db.find_filone_row_by_pod(pod, lcl=lcl, disabled_lcls=disabled)
        if not found:
            found = _db.find_filone_row_by_pod(pod, lcl=lcl)
        if not found:
            return jsonify({'error': f'POD {pod} non trovato'}), 404

        ek, resolved_lcl = found
        local = _db.get_filone_row(ek, lcl=resolved_lcl) or {}
        row = local.get('sheets_row') or 1

        result = _imposta_appuntamento_su_filone(
            row, orario, data_app=data_app, note_extra=note_extra,
            origine='distinte', pod_override=pod, eneltel_override=ek,
            nota_corrente=nota_corrente, lcl=resolved_lcl,
        )
        return jsonify({
            'success': True,
            'message': f"Appuntamento impostato: {result['nota_app']}",
            'nota': result['nuova_nota']
        })

    except ValueError as ve:
        return jsonify({'error': str(ve)}), 400
    except Exception as e:
        return _api_error(e, 'appuntamento distinte')


@bp.route('/api/distinte/rimuovi-appuntamento', methods=['POST'])
@bp.route('/api/lettere/rimuovi-appuntamento', methods=['POST'])
@login_required
def remove_appuntamento_distinte():
    """Rimuove l'appuntamento da un contatore cercato per POD."""
    try:
        data = request.json or {}
        pod = data.get('pod', '').strip().upper()
        nota_corrente = data.get('nota_corrente')
        lcl = data.get('lcl', '').strip() or None

        if not pod:
            return jsonify({'error': 'POD mancante'}), 400

        from moduli import db as _db
        from moduli.lettere import _get_disabled_lcls
        disabled = _get_disabled_lcls()
        found = _db.find_filone_row_by_pod(pod, lcl=lcl, disabled_lcls=disabled)
        if not found:
            found = _db.find_filone_row_by_pod(pod, lcl=lcl)
        if not found:
            return jsonify({'error': f'POD {pod} non trovato'}), 404

        ek, resolved_lcl = found
        local = _db.get_filone_row(ek, lcl=resolved_lcl) or {}
        row = local.get('sheets_row') or 1

        result = _rimuovi_appuntamento_da_filone(
            row, nota_corrente=nota_corrente, lcl=resolved_lcl,
            pod_override=pod, eneltel_override=ek,
        )
        return jsonify({
            'success': True,
            'message': 'Appuntamento rimosso',
            'nota': result['nuova_nota']
        })

    except ValueError as ve:
        return jsonify({'error': str(ve)}), 400
    except Exception as e:
        return _api_error(e, 'rimozione appuntamento distinte')


@bp.route('/api/distinte/aggiorna-data', methods=['POST'])
@bp.route('/api/lettere/aggiorna-data', methods=['POST'])
@login_required
def aggiorna_data_distinte():
    """
    Aggiorna la data di pianificazione per un contatore cercato per POD.
    Body: {"pod": "IT...", "data": "DD/MM/YYYY"}
    """
    try:
        data = request.json
        pod = data.get('pod', '').strip().upper()
        nuova_data = data.get('data', '').strip()

        if not pod:
            return jsonify({'error': 'POD mancante'}), 400
        if not nuova_data:
            return jsonify({'error': 'Data richiesta'}), 400

        col_data = 'DATA PIAN' if PROVINCIA_ATTIVA == 'TORINO' else 'Data_pianificata'

        row = find_row_by_pod(pod)
        if row is None:
            return jsonify({'error': f'POD {pod} non trovato'}), 404

        pod_anchor_col = _resolve_anchor_col_name('pod') or 'POD'
        res = safe_batch_update_by_name(
            [{'row': row, 'col': col_data, 'value': nuova_data, 'anchor': pod}],
            anchor_col=pod_anchor_col, anchor_normalize='upper',
        )
        if not res['success']:
            if res.get('invalid_cols'):
                return jsonify({'error': f'Colonna {col_data} non trovata sul foglio'}), 400
            if res.get('lost_anchors'):
                return jsonify({'error': f'POD {pod} non più presente nel foglio'}), 404
            return jsonify({'error': 'Scrittura non completata'}), 500

        _callbacks['trigger_sync_after_edit']()
        return jsonify({
            'success': True,
            'message': f'Data aggiornata: {nuova_data}',
            'remapped': res['remapped'] > 0,
        })

    except Exception as e:
        return _api_error(e, 'aggiornamento data distinte')


@bp.route('/api/distinte/salva-telefono', methods=['POST'])
@bp.route('/api/lettere/salva-telefono', methods=['POST'])
@login_required
def salva_telefono_filone():
    """
    Salva un numero di telefono nel filone per un POD.
    Body: {"pod": "IT...", "telefono": "...", "fonte": "PagineBianche"}
    Se telefono è vuoto o "non trovato", scrive "non trovato" nel campo.
    """
    try:
        data = request.json
        pod = data.get('pod', '').strip().upper()
        telefono = data.get('telefono', '').strip()
        lcl = data.get('lcl', '').strip()

        if not pod:
            return jsonify({'error': 'POD mancante'}), 400

        from moduli import db as _db
        from moduli.lettere import _get_disabled_lcls
        disabled = _get_disabled_lcls()
        found = _db.find_filone_row_by_pod(pod, lcl=lcl, disabled_lcls=disabled)
        row = None
        if found:
            loc = _db.get_filone_row(found[0], lcl=found[1])
            if loc:
                row = loc.get('sheets_row')
        if row is None:
            row = find_row_by_pod(pod)
        if row is None:
            return jsonify({'error': f'POD {pod} non trovato'}), 404

        # Trova nome canonico colonna TELEFONO sul foglio attuale
        header_map = get_header_map()
        col_tel_name = None
        for col_name in ['TELEFONO', 'Telefono', 'telefono', 'TEL', 'Tel']:
            if col_name in header_map:
                col_tel_name = col_name
                break
        if col_tel_name is None:
            return jsonify({'error': 'Colonna TELEFONO non trovata nel filone'}), 400

        valore = 'non trovato' if (not telefono or telefono.lower() == 'non trovato') else telefono

        pod_anchor_col = _resolve_anchor_col_name('pod') or 'POD'
        res = safe_batch_update_by_name(
            [{'row': row, 'col': col_tel_name, 'value': valore, 'anchor': pod}],
            anchor_col=pod_anchor_col, anchor_normalize='upper',
        )
        if not res['success']:
            if res.get('invalid_cols'):
                return jsonify({'error': 'Colonna TELEFONO non trovata sul foglio'}), 400
            if res.get('lost_anchors'):
                return jsonify({'error': f'POD {pod} non più presente nel foglio'}), 404
            return jsonify({'error': 'Scrittura non completata'}), 500

        _callbacks['trigger_sync_after_edit']()
        return jsonify({
            'success': True,
            'telefono': valore,
            'message': f'Telefono salvato: {valore}'
        })

    except Exception as e:
        return _api_error(e, 'salvataggio telefono')


@bp.route('/api/distinte/salva-email', methods=['POST'])
@bp.route('/api/lettere/salva-email', methods=['POST'])
@login_required
def salva_email_filone():
    """
    Salva un indirizzo email nel filone per un POD.
    Body: {"pod": "IT...", "email": "..."}
    """
    try:
        data = request.json
        pod = data.get('pod', '').strip().upper()
        email = data.get('email', '').strip()
        lcl = data.get('lcl', '').strip()

        if not pod:
            return jsonify({'error': 'POD mancante'}), 400

        from moduli import db as _db
        from moduli.lettere import _get_disabled_lcls
        disabled = _get_disabled_lcls()
        found = _db.find_filone_row_by_pod(pod, lcl=lcl, disabled_lcls=disabled)
        row = None
        if found:
            loc = _db.get_filone_row(found[0], lcl=found[1])
            if loc:
                row = loc.get('sheets_row')
        if row is None:
            row = find_row_by_pod(pod)
        if row is None:
            return jsonify({'error': f'POD {pod} non trovato'}), 404

        header_map = get_header_map()
        col_email_name = None
        for col_name in ['email', 'EMAIL', 'Email', 'E-mail', 'e-mail', 'Mail', 'MAIL']:
            if col_name in header_map:
                col_email_name = col_name
                break
        if col_email_name is None:
            return jsonify({'error': 'Colonna EMAIL non trovata nel filone'}), 400

        pod_anchor_col = _resolve_anchor_col_name('pod') or 'POD'
        res = safe_batch_update_by_name(
            [{'row': row, 'col': col_email_name, 'value': email, 'anchor': pod}],
            anchor_col=pod_anchor_col, anchor_normalize='upper',
        )
        if not res['success']:
            if res.get('invalid_cols'):
                return jsonify({'error': 'Colonna EMAIL non trovata sul foglio'}), 400
            if res.get('lost_anchors'):
                return jsonify({'error': f'POD {pod} non più presente nel foglio'}), 404
            return jsonify({'error': 'Scrittura non completata'}), 500

        _callbacks['trigger_sync_after_edit']()
        return jsonify({
            'success': True,
            'email': email,
            'message': f'Email salvata: {email}'
        })

    except Exception as e:
        return _api_error(e, 'salvataggio email')
