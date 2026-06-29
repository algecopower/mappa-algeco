# -*- coding: utf-8 -*-
"""
Blueprint per le API PDF E-Distribuzione — gestione distinte e moduli maxy.
"""
import os
import re
import tempfile
import logging
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from email.utils import formataddr

from flask import Blueprint, request, jsonify, send_file

from moduli.auth import login_required
from moduli.safe_json import json_read, json_write
from moduli.shared import (
    get_worksheet, get_header_map, find_row_by_pod,
    build_pod_row_map, safe_batch_update_by_name, _imposta_appuntamento_su_filone, _rimuovi_appuntamento_da_filone,
    _api_error, _get_colonna_note, _trova_indice_multi, _resolve_anchor_col_name,
    _callbacks, _get_username,
)
from moduli.smtp import load_smtp_settings, connect_smtp, save_to_sent
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


# Percorso del file JSON dei template messaggi
_TEMPLATES_JSON = os.path.join(_ROOT_DIR, 'data', 'distinte_templates.json')
_ALLEGATI_DIR = os.path.join(_ROOT_DIR, 'data', 'allegati')

# Mappa allegati di default per provincia
_ALLEGATI_DEFAULT = {
    'TORINO': ['attestazione_piemonte.pdf'],
    'PAVIA': ['attestazione_lombardia.pdf'],
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


def _resolve_template_server(body_str, fields, conditionals=None, email_tecnico=''):
    """Risolve un template sostituendo i placeholder con i valori dei campi (server-side)."""
    result = body_str
    # Risolvi condizionali (es. {distacco_text})
    if conditionals:
        for key, cond in conditionals.items():
            field_val = fields.get(cond['field'], '')
            replacement = cond.get('values', {}).get(field_val, '')
            result = re.sub(r'\{' + key + r'\}', replacement, result)
    # Risolvi {email_tecnico}
    result = result.replace('{email_tecnico}', email_tecnico)
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
    return fields


@bp.route('/api/distinte/templates', methods=['GET'])
@login_required
def get_distinte_templates():
    """Restituisce i template messaggi dal file JSON centralizzato."""
    try:
        data = json_read(_TEMPLATES_JSON, default=None)
        if data is None:
            return jsonify({'error': 'File template non trovato'}), 404
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': f'Errore lettura template: {str(e)}'}), 500


@bp.route('/api/distinte/templates', methods=['PUT'])
@login_required
def save_distinte_templates():
    """Salva i template messaggi aggiornati nel file JSON."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Nessun dato ricevuto'}), 400
        json_write(_TEMPLATES_JSON, data, indent=4)
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


def _attach_files_to_msg(msg_mixed, allegati_nomi):
    """Allega i PDF specificati al messaggio MIME. Ritorna lista di nomi effettivamente allegati."""
    allegati_ok = []
    for nome in allegati_nomi:
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


@bp.route('/api/distinte/smtp-test', methods=['POST'])
@login_required
def test_smtp_connection():
    """Testa la connessione SMTP con le impostazioni attuali."""
    try:
        settings = load_smtp_settings()
        if not settings.get('host'):
            return jsonify({'error': 'Configurazione SMTP mancante'}), 400

        server = connect_smtp(settings, timeout=10)
        server.quit()

        return jsonify({'ok': True, 'message': 'Connessione SMTP riuscita!'})
    except smtplib.SMTPAuthenticationError:
        return jsonify({'error': 'Autenticazione fallita. Controlla username e password.'}), 401
    except Exception as e:
        logger.error(f"Test SMTP fallito: {e}")
        return jsonify({'error': f'Connessione fallita: {str(e)}'}), 500


@bp.route('/api/distinte/invia-email-prova', methods=['POST'])
@login_required
def invia_email_prova():
    """Invia una email di prova con dati fittizi all'indirizzo specificato."""
    try:
        data = request.get_json()
        email_dest = (data.get('email') or '').strip()
        template_id = data.get('template_id', '')

        if not email_dest or '@' not in email_dest:
            return jsonify({'error': 'Indirizzo email non valido'}), 400
        if not template_id:
            return jsonify({'error': 'Template non specificato'}), 400

        # Carica template
        try:
            tmpl_data = json_read(_TEMPLATES_JSON, default=None)
            if tmpl_data is None:
                raise FileNotFoundError
        except Exception:
            return jsonify({'error': 'Impossibile caricare i template'}), 500

        tmpl = tmpl_data.get('templates', {}).get(template_id)
        if not tmpl or tmpl.get('type') != 'email':
            return jsonify({'error': f'Template "{template_id}" non valido'}), 404

        field_defs = tmpl_data.get('fields', {})
        tmpl_settings = tmpl_data.get('settings', {})
        email_tecnico = tmpl_settings.get(f'email_{PROVINCIA_ATTIVA.lower()}', '')

        # Dati fittizi di prova
        contatto_test = {
            'pod': 'IT001E98765432',
            'email': email_dest,
            'nome': 'AZIENDA DI PROVA SRL',
            'indirizzo': 'Via Roma',
            'civico': '1',
            'comune': 'Milano',
            'data_intervento': '2026-04-15',
            'pod_filone': 'IT001E98765432',
            'eneltel_filone': '123456789',
            'matricola_filone': 'MAT-TEST-001',
            'tipologia_contatore': 'diretto',
            'nome_societa': 'AZIENDA DI PROVA SRL',
            'nome_referente': 'Mario Rossi',
        }

        # Carica SMTP settings
        smtp_settings = load_smtp_settings()
        if not smtp_settings.get('host') or not smtp_settings.get('password'):
            return jsonify({'error': 'Configurazione SMTP incompleta. Salva prima le credenziali.'}), 400

        from_email = smtp_settings.get('from_email', smtp_settings.get('username', ''))
        from_name = smtp_settings.get('from_name', 'Ufficio Tecnico Algeco Power')

        # Costruisci campi e risolvi template
        fields = _build_fields_for_contact(contatto_test, tmpl, field_defs)
        body_html = _resolve_template_server(
            tmpl['body'], fields,
            conditionals=tmpl.get('conditionals'),
            email_tecnico=email_tecnico
        )
        subject = _resolve_template_server(
            tmpl.get('subject', ''), fields,
            email_tecnico=email_tecnico
        ) if tmpl.get('subject') else 'Sostituzione contatore - AZIENDA DI PROVA SRL'

        subject = '[TEST] ' + subject

        # Converti \n in <br> e aggiungi firma
        body_html_formatted = body_html.replace('\n', '<br>')
        firma_html = tmpl_settings.get('firma_html', '')
        if firma_html:
            firma_html = firma_html.replace('{email_tecnico}', email_tecnico)
            body_html_formatted += firma_html

        # Costruisci messaggio MIME
        import email.utils as email_utils
        msg = MIMEMultipart('alternative')
        msg['From'] = formataddr((from_name, from_email))
        msg['To'] = email_dest
        msg['Subject'] = subject
        msg['Reply-To'] = from_email
        msg['Message-ID'] = email_utils.make_msgid(
            domain=from_email.split('@')[-1] if '@' in from_email else 'algecopower.it')
        msg['Date'] = email_utils.formatdate(localtime=True)

        # Plain text
        body_plain = re.sub(r'<a[^>]*href="([^"]*?)"[^>]*>(.*?)</a>', r'\2 (\1)', body_html)
        body_plain = re.sub(r'<[^>]+>', '', body_plain)
        if firma_html:
            body_plain += f'\n\n--\nUfficio Tecnico\nTelefono: 011 025 2702\nMail: {email_tecnico}\nwww.algecopower.it'

        msg.attach(MIMEText(body_plain, 'plain', 'utf-8'))
        msg.attach(MIMEText(body_html_formatted, 'html', 'utf-8'))

        # Invio
        server = connect_smtp(smtp_settings, timeout=15)
        try:
            server.send_message(msg)
        finally:
            try:
                server.quit()
            except Exception:
                pass

        # Salva in cartella Inviati
        save_to_sent(smtp_settings, msg)

        logger.info(f"[EMAIL-TEST] Inviata email di prova a {email_dest}")
        return jsonify({'ok': True, 'email': email_dest})

    except smtplib.SMTPAuthenticationError:
        return jsonify({'ok': False, 'errore': 'Autenticazione SMTP fallita'}), 401
    except Exception as e:
        logger.error(f"[EMAIL-TEST] Errore invio prova a {email_dest}: {e}")
        return jsonify({'ok': False, 'errore': str(e)}), 500


@bp.route('/api/distinte/invia-singola-email', methods=['POST'])
@login_required
def invia_singola_email():
    """
    Invia UNA singola email usando un template personalizzato.
    Il frontend chiama questo endpoint in loop con pausa tra un invio e l'altro
    per evitare di essere marcati come spam.
    Body: {
        "template_id": "email_societa" | "email_semplificata",
        "contatto": {"pod": "IT...", "email": "...", "nome": "...", ...}
    }
    """
    try:
        data = request.get_json()
        template_id = data.get('template_id', '')
        contatto = data.get('contatto')
        subject_override = data.get('subject_override')
        body_override = data.get('body_override')
        allegati_nomi = data.get('allegati', [])

        if not template_id:
            return jsonify({'error': 'Template non specificato'}), 400
        if not contatto:
            return jsonify({'error': 'Contatto mancante'}), 400

        email_dest = (contatto.get('email') or '').strip()
        pod = contatto.get('pod', '?')

        if not email_dest:
            return jsonify({'ok': False, 'pod': pod, 'errore': 'Email mancante'}), 400

        # Carica template
        try:
            tmpl_data = json_read(_TEMPLATES_JSON, default=None)
            if tmpl_data is None:
                raise FileNotFoundError
        except Exception:
            return jsonify({'error': 'Impossibile caricare i template'}), 500

        tmpl = tmpl_data.get('templates', {}).get(template_id)
        if not tmpl or tmpl.get('type') != 'email':
            return jsonify({'error': f'Template "{template_id}" non valido'}), 404

        field_defs = tmpl_data.get('fields', {})
        tmpl_settings = tmpl_data.get('settings', {})
        email_tecnico = tmpl_settings.get(f'email_{PROVINCIA_ATTIVA.lower()}', '')

        # Carica SMTP settings
        smtp_settings = load_smtp_settings()
        if not smtp_settings.get('host') or not smtp_settings.get('password'):
            return jsonify({'error': 'Configurazione SMTP incompleta'}), 400

        from_email = smtp_settings.get('from_email', smtp_settings.get('username', ''))
        from_name = smtp_settings.get('from_name', 'Ufficio Tecnico Algeco Power')

        # Se il frontend ha fornito subject/body già risolti (anteprima modificata), usali
        if subject_override and body_override:
            subject = subject_override
            body_html = body_override
        else:
            # Costruisci i campi auto-compilati
            fields = _build_fields_for_contact(contatto, tmpl, field_defs)

            # Risolvi body e subject
            body_html = _resolve_template_server(
                tmpl['body'], fields,
                conditionals=tmpl.get('conditionals'),
                email_tecnico=email_tecnico
            )
            subject = _resolve_template_server(
                tmpl.get('subject', ''), fields,
                email_tecnico=email_tecnico
            ) if tmpl.get('subject') else f'Sostituzione contatore - {contatto.get("nome", "")}'

        # Converti \n in <br> per HTML
        body_html_formatted = body_html.replace('\n', '<br>')

        # Aggiungi firma HTML in calce
        firma_html = tmpl_settings.get('firma_html', '')
        if firma_html:
            firma_html = firma_html.replace('{email_tecnico}', email_tecnico)
            body_html_formatted += firma_html

        # Costruisci messaggio MIME con header anti-spam
        import email.utils as email_utils

        # Versione plain text
        body_plain = re.sub(r'<a[^>]*href="([^"]*?)"[^>]*>(.*?)</a>', r'\2 (\1)', body_html)
        body_plain = re.sub(r'<[^>]+>', '', body_plain)
        if firma_html:
            body_plain += f'\n\n--\nUfficio Tecnico\nTelefono: 011 025 2702\nMail: {email_tecnico}\nwww.algecopower.it'

        # Parte alternativa (text + html)
        msg_alt = MIMEMultipart('alternative')
        msg_alt.attach(MIMEText(body_plain, 'plain', 'utf-8'))
        msg_alt.attach(MIMEText(body_html_formatted, 'html', 'utf-8'))

        # Se ci sono allegati, usa mixed > alternative + attachments
        if allegati_nomi:
            msg = MIMEMultipart('mixed')
            msg.attach(msg_alt)
            allegati_effettivi = _attach_files_to_msg(msg, allegati_nomi)
            logger.info(f"[EMAIL] Allegati per {pod}: {allegati_effettivi}")
        else:
            msg = msg_alt

        msg['From'] = formataddr((from_name, from_email))
        msg['To'] = email_dest
        msg['Subject'] = subject
        msg['Reply-To'] = from_email
        msg['Message-ID'] = email_utils.make_msgid(domain=from_email.split('@')[-1] if '@' in from_email else 'algecopower.it')
        msg['Date'] = email_utils.formatdate(localtime=True)

        # Connessione e invio (una connessione per email)
        server = connect_smtp(smtp_settings, timeout=15)
        try:
            server.send_message(msg)
        finally:
            try:
                server.quit()
            except Exception:
                pass

        # Salva in cartella Inviati
        save_to_sent(smtp_settings, msg)

        # Rende visibile l'invio anche nell'Archivio Lettere.
        # Il vecchio flusso PDF non ha necessariamente una generazione associata,
        # quindi lo storico viene legato al contatto stabile (POD).
        try:
            from moduli import lettere as lettere_store

            lettere_store.registra_invio(
                contact_key_value=lettere_store.contact_key(
                    pod=pod,
                    nominativo=contatto.get('nome') or contatto.get('nome_societa') or '',
                    comune=contatto.get('comune') or '',
                ),
                pod=pod,
                email_dest=email_dest,
                template_id=template_id,
                subject=subject,
                status='sent',
                user=_get_username(),
                detail={'source': 'gestione_pdf', 'allegati': allegati_nomi},
            )
        except Exception:
            logger.exception("[EMAIL] Invio riuscito ma registrazione storico fallita")

        logger.info(f"[EMAIL] Inviata a {email_dest} (POD {pod})")
        return jsonify({'ok': True, 'pod': pod, 'email': email_dest})

    except smtplib.SMTPAuthenticationError:
        return jsonify({'ok': False, 'pod': pod, 'errore': 'Autenticazione SMTP fallita'}), 401
    except Exception as e:
        logger.error(f"[EMAIL] Errore invio a {email_dest}: {e}")
        return jsonify({'ok': False, 'pod': contatto.get('pod', '?'), 'email': email_dest, 'errore': str(e)}), 500


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

        # Carica dati filone per confronto e classificazione
        dati_filone = {}
        differenze = []
        try:
            ws = get_worksheet()
            all_data = ws.get_all_values()
            headers = all_data[0] if all_data else []

            # Trova indici colonne (usa helper condivisi + mappatura personalizzata)

            idx_pod = _trova_indice_multi(headers, 'POD', 'Pod', 'pod', chiave_mapping='pod')
            idx_eneltel = _trova_indice_multi(headers, 'ENELTEL', 'Eneltel', 'eneltel', chiave_mapping='eneltel')
            idx_data = _trova_indice_multi(headers, 'DATA PIAN', 'DATA_PIAN', 'PROG', 'Prog', chiave_mapping='data_pianificata')
            idx_telefono = _trova_indice_multi(headers, 'telefono', 'TELEFONO', 'Telefono', 'TEL', 'Tel', chiave_mapping='telefono')
            idx_email = _trova_indice_multi(headers, 'email', 'EMAIL', 'Email', 'E-mail', 'e-mail', 'Mail', 'MAIL', chiave_mapping='email')
            idx_nominativo = _trova_indice_multi(headers, 'NOMINATIVO', 'Nominativo', 'CLIENTE', 'Cliente', chiave_mapping='nominativo')
            idx_indirizzo = _trova_indice_multi(headers, 'Indirizzo', 'Via', 'VIA', chiave_mapping='indirizzo')
            idx_civico = _trova_indice_multi(headers, 'Num', 'NUM', 'civico', 'CIVICO', chiave_mapping='civico')
            idx_comune = _trova_indice_multi(headers, 'COMUNE', 'Comune', chiave_mapping='comune')
            idx_note = _trova_indice_multi(headers, 'NOTE APP', 'NOTE_APP', 'NOTE', 'Note', chiave_mapping='note_appuntamento')
            idx_codice_ante = _trova_indice_multi(headers, 'Codice_Ante_Sostituzione', 'Codice misuratore in opera', chiave_mapping='codice_ante')
            idx_matricola_ante = _trova_indice_multi(headers, 'Matricola_Ante_Sostituzione', 'Matricola misuratore in opera', chiave_mapping='matricola_ante')
            idx_tipologia = _trova_indice_multi(headers, 'Tipo_Misuratore', 'TIPOLOGIA CE', 'Tipologia CE', chiave_mapping='tipo_misuratore')
            idx_posizione = _trova_indice_multi(headers, 'Posizione_Mis_Ante_Sost', 'Posizione_Ante_Mis', 'Posizione misuratore', 'POSIZIONE', 'Posizione', chiave_mapping='posizione_misuratore')

            for row in all_data[1:]:
                if idx_pod is not None and idx_pod < len(row):
                    pod = row[idx_pod].strip().upper()
                    if pod:
                        codice_ante = (row[idx_codice_ante].strip() if idx_codice_ante is not None and idx_codice_ante < len(row) else '')
                        matricola_ante = (row[idx_matricola_ante].strip() if idx_matricola_ante is not None and idx_matricola_ante < len(row) else '')
                        # Combina codice e matricola in unico blocco
                        matricola_completa = ' / '.join(filter(None, [codice_ante, matricola_ante]))

                        dati_filone[pod] = {
                            'pod': pod,
                            'eneltel': row[idx_eneltel].strip() if idx_eneltel is not None and idx_eneltel < len(row) else '',
                            'data_pian': row[idx_data] if idx_data is not None and idx_data < len(row) else '',
                            'telefono': row[idx_telefono] if idx_telefono is not None and idx_telefono < len(row) else '',
                            'email': row[idx_email] if idx_email is not None and idx_email < len(row) else '',
                            'nominativo': row[idx_nominativo] if idx_nominativo is not None and idx_nominativo < len(row) else '',
                            'indirizzo': row[idx_indirizzo] if idx_indirizzo is not None and idx_indirizzo < len(row) else '',
                            'civico': row[idx_civico] if idx_civico is not None and idx_civico < len(row) else '',
                            'comune': row[idx_comune] if idx_comune is not None and idx_comune < len(row) else '',
                            'note': row[idx_note] if idx_note is not None and idx_note < len(row) else '',
                            'matricola': matricola_completa,
                            'tipologia': row[idx_tipologia].strip() if idx_tipologia is not None and idx_tipologia < len(row) else '',
                            'posizione': row[idx_posizione].strip() if idx_posizione is not None and idx_posizione < len(row) else '',
                        }

            # Confronta distinta con filone
            differenze = confronta_con_filone(all_lavori, dati_filone)
            logger.info(f"[DISTINTE] Rilevate {len(differenze)} differenze con il filone")

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
        logger.info(f"[DISTINTE] idx_telefono={idx_telefono}, dati_filone contiene {len(dati_filone)} PODs")
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
        data = request.json
        pod = data.get('pod', '').strip().upper()
        nota = data.get('nota', '').strip()
        sovrascrivere = data.get('sovrascrivere', True)  # Default: sovrascrive

        if not pod:
            return jsonify({'error': 'POD mancante'}), 400

        # DB-only: no chiamate Sheets nel path di scrittura. Il drainer pusha.
        from moduli import db as _db
        ek = _db.find_eneltel_by_pod(pod)
        if not ek:
            return jsonify({'error': f'POD {pod} non trovato'}), 404
        local = _db.get_filone_row(ek) or {}
        nota_attuale = local.get('note_appuntamento') or ''
        if sovrascrivere:
            nuova_nota = nota
        elif not nota:
            nuova_nota = nota_attuale
        elif not nota_attuale:
            nuova_nota = nota
        elif nota not in nota_attuale:
            nuova_nota = f"{nota_attuale} // {nota}"
        else:
            nuova_nota = nota_attuale

        col_note_name = _get_colonna_note()  # nome colonna sul foglio
        _db.record_local_change(
            ek, {col_note_name: nuova_nota},
            origin='distinte_salva_nota',
            initial_state='pending',
        )
        try:
            from moduli import sync_engine as _se
            _se.trigger_drain_now()
        except Exception:
            pass
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
@login_required
def set_appuntamento_distinte():
    """
    Imposta un appuntamento per un contatore cercato per POD.
    Body: {"pod": "IT...", "orario": "10:00-12:00"}
    """
    try:
        data = request.json
        pod = data.get('pod', '').strip().upper()
        orario = data.get('orario', '').strip()
        data_app = data.get('data_appuntamento', '')
        note_extra = data.get('note', '')

        if not pod:
            return jsonify({'error': 'POD mancante'}), 400
        if not orario:
            return jsonify({'error': 'Orario richiesto'}), 400

        row = find_row_by_pod(pod)
        if row is None:
            return jsonify({'error': f'POD {pod} non trovato'}), 404

        result = _imposta_appuntamento_su_filone(
            row, orario, data_app=data_app, note_extra=note_extra,
            origine='distinte', pod_override=pod
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
@login_required
def remove_appuntamento_distinte():
    """Rimuove l'appuntamento da un contatore cercato per POD."""
    try:
        data = request.json
        pod = data.get('pod', '').strip().upper()

        if not pod:
            return jsonify({'error': 'POD mancante'}), 400

        row = find_row_by_pod(pod)
        if row is None:
            return jsonify({'error': f'POD {pod} non trovato'}), 404

        result = _rimuovi_appuntamento_da_filone(row)
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

        if not pod:
            return jsonify({'error': 'POD mancante'}), 400

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

        if not pod:
            return jsonify({'error': 'POD mancante'}), 400

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
