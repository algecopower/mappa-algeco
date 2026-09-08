# -*- coding: utf-8 -*-
"""
moduli/bulk_contatori_email.py — Modulo per raggruppamento contatori multipli,
pluralizzazione testi email, generazione dinamica dell'elenco contatori in Excel
e registrazione automatica delle note di invio sul DB.
"""

import datetime
import io
import logging
import os
import re
import smtplib
import unicodedata
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email.mime.application import MIMEApplication
from email import encoders
from email.utils import formataddr, formatdate, make_msgid

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from config import PROVINCIA_ATTIVA
from moduli.safe_json import json_read
from moduli.smtp import load_smtp_settings, send_smtp_message

logger = logging.getLogger('mappa_algeco')

_ROOT_DIR = os.path.join(os.path.dirname(__file__), '..')
_ALLEGATI_DIR = os.path.join(_ROOT_DIR, 'data', 'allegati')


def sanitize_filename(name: str) -> str:
    """Pulisce una stringa per usarla in modo sicuro come nome file."""
    if not name:
        return 'Azienda'
    name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
    name = re.sub(r'[^\w\s-]', '', name).strip()
    name = re.sub(r'[-\s]+', '_', name)
    return name[:50] or 'Azienda'


def format_data_breve(date_str: str | None) -> str:
    """Formatta una data in GG/MM (es. 25/08/2026 -> 25/08, 2026-08-25 -> 25/08)."""
    if not date_str:
        return ''
    val = str(date_str).strip()
    m = re.search(r'(\d{1,2})/(\d{1,2})(?:/\d{2,4})?', val)
    if m:
        return f"{int(m.group(1)):02d}/{int(m.group(2)):02d}"
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})', val)
    if m:
        return f"{int(m.group(3)):02d}/{int(m.group(2)):02d}"
    return val[:5] if len(val) >= 5 else val


def build_nota_invio(tipo: str, data_intervento: str | None = None, data_oggi: datetime.date | None = None) -> str:
    """
    Costruisce la nota standard da scrivere sul DB.
    Es:
    - tipo='mail' o 'email': "18/08 mandata mail per 25/08" (o "18/08 mandata mail")
    - tipo='sms': "18/08 mandato SMS per 25/08" (o "18/08 mandato SMS")
    - tipo='wa' o 'whatsapp': "18/08 mandato WA per 25/08" (o "18/08 mandato WA")
    """
    if data_oggi is None:
        data_oggi = datetime.date.today()
    oggi_str = f"{data_oggi.day:02d}/{data_oggi.month:02d}"
    
    tipo_str = str(tipo).lower().strip()
    if tipo_str in ('sms', 'mandato sms', 'sms gateway'):
        azione = "mandato SMS"
    elif tipo_str in ('wa', 'whatsapp', 'msg wa', 'mandato msg wa'):
        azione = "mandato WA"
    else:
        azione = "mandata mail"

    data_breve = format_data_breve(data_intervento)
    
    if data_breve:
        return f"{oggi_str} {azione} per {data_breve}"
    return f"{oggi_str} {azione}"


def registra_nota_su_db(pod: str, nota: str, user: str = '', origin: str = 'bulk_send') -> bool:
    """
    Scrive/premette la nota nel campo note_appuntamento del filone sul DB locale.
    Se la nota non è già presente, la concatena all'inizio con ' // '.
    """
    if not pod or not nota:
        return False
    try:
        from moduli import db as _db
        from moduli.shared import _get_colonna_note
        ek = _db.find_eneltel_by_pod(pod)
        if not ek:
            return False
        local = _db.get_filone_row(ek) or {}
        nota_attuale = (local.get('note_appuntamento') or '').strip()
        if nota in nota_attuale:
            return True  # già presente
        
        nuova_nota = f"{nota} // {nota_attuale}" if nota_attuale else nota
        col_note = _get_colonna_note()
        _db.record_local_change(
            ek, {col_note: nuova_nota},
            origin=origin,
            user=user,
            initial_state='pending',
        )
        return True
    except Exception as e:
        logger.warning(f"[NOTA_DB] Errore salvataggio nota per {pod}: {e}")
        return False


def generate_contatori_excel(contatori: list[dict], nome_azienda: str = '') -> bytes:
    """
    Genera un file Excel formattato professionalmente con l'elenco dei contatori
    associati all'azienda per l'invio in allegato.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Elenco Contatori"

    # Stili
    brand_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    title_font = Font(name="Calibri", size=13, bold=True, color="1F4E78")
    subtitle_font = Font(name="Calibri", size=10, italic=True, color="555555")
    cell_font = Font(name="Calibri", size=10)
    bold_cell_font = Font(name="Calibri", size=10, bold=True)

    thin_border = Border(
        left=Side(style='thin', color='D9D9D9'),
        right=Side(style='thin', color='D9D9D9'),
        top=Side(style='thin', color='D9D9D9'),
        bottom=Side(style='thin', color='D9D9D9')
    )
    zebra_fill = PatternFill(start_color="F2F5F9", end_color="F2F5F9", fill_type="solid")

    # Intestazione documento
    title_text = "ELENCO CONTATORI IN SOSTITUZIONE"
    if nome_azienda:
        title_text += f" — {nome_azienda.upper()}"
    ws.cell(row=1, column=1, value=title_text).font = title_font
    ws.cell(row=2, column=1, value="Incarico ufficiale E-Distribuzione — Algeco Power Srl").font = subtitle_font

    headers = [
        ("N°", 6, 'center'),
        ("POD", 20, 'center'),
        ("Tipologia Misuratore", 22, 'left'),
        ("Indirizzo", 28, 'left'),
        ("Civico", 10, 'center'),
        ("Comune", 20, 'left'),
        ("Matricola / Codice", 22, 'center'),
        ("Data Prevista", 16, 'center'),
    ]

    header_row = 4
    for col_idx, (head, width, align) in enumerate(headers, start=1):
        cell = ws.cell(row=header_row, column=col_idx, value=head)
        cell.fill = brand_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal=align, vertical='center', wrap_text=True)
        cell.border = thin_border
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    ws.row_dimensions[header_row].height = 24

    for i, c in enumerate(contatori, start=1):
        r = header_row + i
        ws.row_dimensions[r].height = 20
        row_fill = zebra_fill if (i % 2 == 0) else None

        pod = str(c.get('pod') or c.get('pod_filone') or '').strip()
        tipo = str(c.get('tipologia_contatore') or c.get('tipo_misuratore') or 'N/D').strip()
        indirizzo = str(c.get('indirizzo') or c.get('via') or '').strip()
        civico = str(c.get('civico') or '').strip()
        comune = str(c.get('comune') or '').strip()

        # Recupero matricola e codice
        matr_val = str(c.get('matricola_ante') or c.get('matricola') or c.get('matricola_filone') or c.get('matricola_installata') or '').strip()
        cod_val = str(c.get('codice_ante') or c.get('codice') or c.get('codice_filone') or c.get('codice_installato') or '').strip()

        # Se non trovati nei campi diretti, prova query DB su filone tramite POD
        if (not matr_val and not cod_val) and pod:
            try:
                from moduli import db
                conn = db.get_conn()
                db_row = conn.execute(
                    "SELECT codice_ante, matricola_ante FROM filone WHERE UPPER(TRIM(pod)) = UPPER(TRIM(?)) LIMIT 1",
                    (pod,)
                ).fetchone()
                if db_row:
                    cod_val = str(db_row['codice_ante'] or '').strip()
                    matr_val = str(db_row['matricola_ante'] or '').strip()
            except Exception:
                pass

        if c.get('matricola_filone') and ' / ' in str(c.get('matricola_filone')):
            matricola = str(c.get('matricola_filone')).strip()
        elif cod_val and matr_val and cod_val != matr_val:
            matricola = f"{cod_val} / {matr_val}"
        else:
            matricola = matr_val or cod_val or ''

        data_int = str(c.get('data_intervento') or c.get('data_pianificazione') or c.get('data') or '').strip()

        row_data = [
            (i, 'center', bold_cell_font),
            (pod, 'center', bold_cell_font),
            (tipo, 'left', cell_font),
            (indirizzo, 'left', cell_font),
            (civico, 'center', cell_font),
            (comune, 'left', cell_font),
            (matricola or '-', 'center', cell_font),
            (data_int or '-', 'center', cell_font),
        ]

        for col_idx, (val, align, font_style) in enumerate(row_data, start=1):
            cell = ws.cell(row=r, column=col_idx, value=val)
            cell.font = font_style
            cell.alignment = Alignment(horizontal=align, vertical='center')
            cell.border = thin_border
            if row_fill:
                cell.fill = row_fill

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def pluralize_template_text(text: str) -> str:
    """
    Adatta un testo template standard dal singolare al plurale
    per la comunicazione a società con più contatori.
    """
    if not text:
        return text

    replacements = [
        (r'\bil vostro contatore\b', 'i vostri contatori'),
        (r'\bdel vostro contatore\b', 'dei vostri contatori'),
        (r'\bal vostro contatore\b', 'ai vostri contatori'),
        (r'\bil contatore\b', 'i contatori'),
        (r'\bdel contatore\b', 'dei contatori'),
        (r'\bal contatore\b', 'ai contatori'),
        (r'\bsul contatore\b', 'sui contatori'),
        (r'\bdel dispositivo\b', 'dei dispositivi'),
        (r'\bil dispositivo\b', 'i dispositivi'),
        (r'\bassociato al POD in oggetto\b', 'associati ai POD indicati'),
        (r'\bassociato al POD\b', 'associati ai POD'),
        (r'\boggetto dell’intervento\b', 'oggetto degli interventi'),
        (r'\boggetto dell\'intervento\b', 'oggetto degli interventi'),
        (r'\bLa data prevista per l\'intervento è\b', 'Le date previste per gli interventi sono'),
    ]

    result = text
    for pattern, repl in replacements:
        result = re.sub(pattern, repl, result, flags=re.IGNORECASE)
    return result


def build_plural_email(
    template_body: str,
    template_subject: str,
    contatori: list[dict],
    fields: dict,
    email_tecnico: str = '',
) -> tuple[str, str, str | None]:
    """
    Costruisce subject, body e l'eventuale nome file Excel dinamico
    in base al numero di contatori dell'azienda:
    - N == 1: Singolare standard
    - N == 2: Plurale con entrambi i POD nell'oggetto e lista nel corpo
    - N > 2: Plurale con conteggio nell'oggetto e riferimento all'allegato Excel
    """
    n = len(contatori) if contatori else 1
    nome_azienda = (
        (contatori[0].get('nome_societa') or contatori[0].get('nome') or contatori[0].get('nominativo') or '')
        if contatori else ''
    ).strip()

    if n <= 1:
        # Singolare standard
        return template_subject, template_body, None

    # Pluralizzazione
    plural_body = pluralize_template_text(template_body)

    if n == 2:
        pod1 = contatori[0].get('pod') or contatori[0].get('pod_filone') or ''
        pod2 = contatori[1].get('pod') or contatori[1].get('pod_filone') or ''

        # Oggetto con entrambi i POD
        if template_subject:
            subj = template_subject
            subj = re.sub(r'SOSTITUZIONE CONTATORE.*', f'SOSTITUZIONE CONTATORI E-DISTRIBUZIONE POD {pod1}, {pod2}', subj, flags=re.IGNORECASE)
            if '{pod}' in subj or '{nome}' in subj or '{nome_societa}' in subj:
                subj = subj.replace('{pod}', f"{pod1}, {pod2}")
        else:
            subj = f"{nome_azienda}: SOSTITUZIONE CONTATORI E-DISTRIBUZIONE POD {pod1}, {pod2}"

        # Corpo con dettaglio dei 2 contatori
        c1 = contatori[0]
        c2 = contatori[1]
        dettagli_contatori = (
            f"\n\nRiepilogo contatori oggetto della sostituzione:\n"
            f"1) POD: {pod1} — {c1.get('indirizzo', '')} {c1.get('civico', '')} ({c1.get('comune', '')}) "
            f"[Tipo: {c1.get('tipologia_contatore') or c1.get('tipo_misuratore') or 'N/D'}"
            + (f", Matricola: {c1.get('matricola_filone') or c1.get('matricola') or c1.get('matricola_ante')}" if (c1.get('matricola_filone') or c1.get('matricola') or c1.get('matricola_ante')) else "")
            + "]\n"
            f"2) POD: {pod2} — {c2.get('indirizzo', '')} {c2.get('civico', '')} ({c2.get('comune', '')}) "
            f"[Tipo: {c2.get('tipologia_contatore') or c2.get('tipo_misuratore') or 'N/D'}"
            + (f", Matricola: {c2.get('matricola_filone') or c2.get('matricola') or c2.get('matricola_ante')}" if (c2.get('matricola_filone') or c2.get('matricola') or c2.get('matricola_ante')) else "")
            + "]"
        )

        # Sostituisci il blocco singolo POD nel corpo con il riepilogo
        if "POD (presente in fattura):" in plural_body:
            plural_body = re.sub(
                r'POD \(presente in fattura\):.*?(?=Precisiamo che|\n\nPrecisiamo)',
                dettagli_contatori.strip() + "\n\n",
                plural_body,
                flags=re.DOTALL
            )
        else:
            plural_body += dettagli_contatori

        return subj, plural_body, None

    else:
        # N > 2: Plurale con conteggio e allegato Excel
        safe_nome = sanitize_filename(nome_azienda)
        excel_filename = f"Elenco_Contatori_{safe_nome}.xlsx"

        if template_subject:
            subj = template_subject
            subj = re.sub(r'SOSTITUZIONE CONTATORE.*', f'SOSTITUZIONE CONTATORI E-DISTRIBUZIONE ({n} contatori)', subj, flags=re.IGNORECASE)
            subj = subj.replace('{pod}', f"({n} contatori)")
        else:
            subj = f"{nome_azienda}: SOSTITUZIONE CONTATORI E-DISTRIBUZIONE ({n} contatori)"

        nota_excel = (
            f"\n\nIn allegato alla presente trasmettiamo il file Excel riepilogativo ({excel_filename}) "
            f"con l'elenco completo dei {n} contatori oggetto dell’intervento (comprensivo di POD, tipologia misuratore, indirizzo e civico)."
        )

        if "POD (presente in fattura):" in plural_body:
            plural_body = re.sub(
                r'POD \(presente in fattura\):.*?(?=Precisiamo che|\n\nPrecisiamo)',
                nota_excel.strip() + "\n\n",
                plural_body,
                flags=re.DOTALL
            )
        else:
            plural_body += nota_excel

        return subj, plural_body, excel_filename


def load_distinte_templates_data(prov: str | None = None) -> dict | None:
    """Carica i template: prima cerca data/{provincia}/distinte_templates.json, fallback su default."""
    prov_name = (prov or PROVINCIA_ATTIVA or 'TORINO').lower()
    prov_file = os.path.join(_ROOT_DIR, 'data', prov_name, 'distinte_templates.json')
    if os.path.exists(prov_file):
        d = json_read(prov_file, default=None)
        if d:
            return d
    default_file = os.path.join(_ROOT_DIR, 'data', 'distinte_templates.json')
    return json_read(default_file, default=None)


def attach_files_to_message(msg_mixed, allegati_nomi: list[str], dynamic_files: dict | None = None, allegati_dir: str | None = None) -> list[str]:
    """Allega i PDF/file specificati al messaggio MIME, inclusi file generati dinamicamente (es. Excel)."""
    dynamic_files = dynamic_files or {}
    allegati_dir = allegati_dir or _ALLEGATI_DIR
    allegati_ok = []
    for nome in (allegati_nomi or []):
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

        filepath = os.path.join(allegati_dir, os.path.basename(nome))
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


def send_contatori_email(
    email_dest: str,
    subject: str | None = None,
    body_html: str | None = None,
    template_id: str = '',
    contatto: dict | None = None,
    contatori_list: list[dict] | None = None,
    allegati_nomi: list[str] | None = None,
    dynamic_files: dict | None = None,
    provincia: str | None = None,
    username: str = '',
    context: str = 'distinte',
    extra_record_details: dict | None = None,
) -> dict:
    """Funzione centralizzata di dominio per invio email/PEC e tracciamento note DB + storico lettere.
    
    Ritorna dict:
      - Successo: {'ok': True, 'pod': ..., 'email': ..., 'contatori_count': ..., ...}
      - Errore: {'ok': False, 'pod': ..., 'email': ..., 'error': ..., 'error_code': 'SMTP_NOT_CONFIGURED' | 'SMTP_AUTH_FAILED' | ...}
    """
    contatto = contatto or {}
    contatori_list = contatori_list or contatto.get('contatori') or ([contatto] if contatto else [])
    primary_pod = contatto.get('pod') or (contatori_list[0].get('pod') if contatori_list else '?') or '?'
    nome_azienda = (contatto.get('nome_societa') or contatto.get('nome') or (contatori_list[0].get('nome_societa') if contatori_list else '') or 'Cliente').strip()
    allegati_nomi = list(allegati_nomi or [])
    dynamic_files = dict(dynamic_files or {})

    email_dest = (email_dest or contatto.get('email') or '').strip()
    if not email_dest or '@' not in email_dest:
        return {
            'ok': False,
            'pod': primary_pod,
            'email': email_dest,
            'error_code': 'INVALID_EMAIL',
            'error': 'Indirizzo email mancante o non valido'
        }

    prov = (provincia or PROVINCIA_ATTIVA or 'TORINO').upper()
    smtp_settings = load_smtp_settings(prov=prov.lower())
    if not smtp_settings.get('host') or not smtp_settings.get('password'):
        return {
            'ok': False,
            'pod': primary_pod,
            'email': email_dest,
            'error_code': 'SMTP_NOT_CONFIGURED',
            'error': 'Configurazione SMTP incompleta. Configura le credenziali SMTP.'
        }

    from_email = smtp_settings.get('from_email', smtp_settings.get('username', ''))
    from_name = smtp_settings.get('from_name', 'Ufficio Tecnico Algeco Power')

    # Carica impostazioni template per firma e recapiti
    tmpl_data = load_distinte_templates_data(prov.lower()) or {}
    tmpl_settings = tmpl_data.get('settings', {})
    prov_settings = tmpl_settings.get('province', {}).get(prov, {})
    email_tecnico = prov_settings.get('email') or tmpl_settings.get(f'email_{prov.lower()}') or tmpl_settings.get('email_tecnico', from_email)
    tel_tecnico = prov_settings.get('telefono') or tmpl_settings.get('telefono_tecnico') or '011 025 2702'

    # Se subject o body_html non sono stati passati già compilati, prova a risolverli dal template
    if not subject or not body_html:
        if template_id:
            tmpl = tmpl_data.get('templates', {}).get(template_id)
            if not tmpl:
                return {
                    'ok': False,
                    'pod': primary_pod,
                    'email': email_dest,
                    'error_code': 'INVALID_TEMPLATE',
                    'error': f'Template "{template_id}" non trovato'
                }
            sub_res, bdy_res, auto_excel = build_plural_email(
                tmpl['body'], tmpl.get('subject', ''), contatori_list, {}, email_tecnico=email_tecnico
            )
            subject = subject or sub_res
            body_html = body_html or bdy_res
            if auto_excel and auto_excel not in allegati_nomi:
                allegati_nomi.append(auto_excel)
        else:
            subject = subject or f'Sostituzione contatore - {nome_azienda}'
            body_html = body_html or ''

    # Converti \n in <br> per l'HTML
    body_html_formatted = body_html.replace('\n', '<br>')

    # Aggiungi firma HTML
    firma_html = tmpl_settings.get('firma_html', '')
    if firma_html:
        try:
            from moduli.mail_manager import resolve_signature_html
            firma_resolved = resolve_signature_html(
                firma_html, prov=prov, email_tecnico=email_tecnico, telefono_tecnico=tel_tecnico
            )
            body_html_formatted += firma_resolved
        except Exception as e:
            logger.warning(f"[EMAIL] Errore risoluzione firma HTML: {e}")

    # Plain text version
    try:
        from moduli.mail_manager import html_to_plain_text
        body_plain = html_to_plain_text(body_html)
    except Exception:
        body_plain = re.sub(r'<[^>]+>', '', body_html)

    if firma_html:
        body_plain += f'\n\n--\nUfficio Tecnico\nTelefono: {tel_tecnico}\nMail: {email_tecnico}\nwww.algecopower.it'

    msg_alt = MIMEMultipart('alternative')
    msg_alt.attach(MIMEText(body_plain, 'plain', 'utf-8'))
    msg_alt.attach(MIMEText(body_html_formatted, 'html', 'utf-8'))

    # Se ci sono allegati Excel dinamici
    for nom in allegati_nomi:
        if nom.lower().endswith('.xlsx') and nom not in dynamic_files:
            try:
                excel_bytes = generate_contatori_excel(contatori_list, nome_azienda)
                dynamic_files[nom] = excel_bytes
            except Exception as e:
                logger.warning(f"[EMAIL] Errore generazione Excel per {nom}: {e}")

    if not dynamic_files and len(contatori_list) > 2:
        safe_nome = sanitize_filename(nome_azienda)
        auto_excel_name = f"Elenco_Contatori_{safe_nome}.xlsx"
        excel_bytes = generate_contatori_excel(contatori_list, nome_azienda)
        dynamic_files[auto_excel_name] = excel_bytes
        if auto_excel_name not in allegati_nomi:
            allegati_nomi.append(auto_excel_name)

    if allegati_nomi:
        msg = MIMEMultipart('mixed')
        msg.attach(msg_alt)
        allegati_effettivi = attach_files_to_message(msg, allegati_nomi, dynamic_files=dynamic_files)
        logger.info(f"[EMAIL] Allegati inclusi per {primary_pod}: {allegati_effettivi}")
    else:
        msg = msg_alt

    msg['From'] = formataddr((from_name, from_email))
    msg['To'] = email_dest
    msg['Subject'] = subject
    msg['Reply-To'] = from_email
    msg['Message-ID'] = make_msgid(domain=from_email.split('@')[-1] if '@' in from_email else 'algecopower.it')
    msg['Date'] = formatdate(localtime=True)

    try:
        ok_send, send_err = send_smtp_message(
            smtp_settings, msg, max_retries=3, base_delay=6, save_sent=True
        )
        if not ok_send:
            if send_err and ('autenticazione' in send_err.lower() or 'auth' in send_err.lower()):
                return {
                    'ok': False,
                    'pod': primary_pod,
                    'email': email_dest,
                    'error_code': 'SMTP_AUTH_FAILED',
                    'error': send_err or 'Autenticazione SMTP fallita'
                }
            return {
                'ok': False,
                'pod': primary_pod,
                'email': email_dest,
                'error_code': 'SEND_FAILED',
                'error': send_err or 'Errore invio email'
            }
    except smtplib.SMTPAuthenticationError:
        return {
            'ok': False,
            'pod': primary_pod,
            'email': email_dest,
            'error_code': 'SMTP_AUTH_FAILED',
            'error': 'Autenticazione SMTP fallita: verifica username e password'
        }
    except Exception as e:
        return {
            'ok': False,
            'pod': primary_pod,
            'email': email_dest,
            'error_code': 'SEND_FAILED',
            'error': str(e)
        }

    # Registra esito nello storico lettere per tutti i contatori coinvolti
    try:
        from moduli import lettere as lettere_store
        pods_to_record = [c.get('pod') or c.get('pod_filone') for c in contatori_list if (c.get('pod') or c.get('pod_filone'))] or [primary_pod]
        record_detail = {
            'source': context,
            'allegati': allegati_nomi,
            'contatori_count': len(contatori_list),
        }
        if extra_record_details:
            record_detail.update(extra_record_details)

        for p_pod in pods_to_record:
            lettere_store.registra_invio(
                contact_key_value=lettere_store.contact_key(
                    pod=p_pod,
                    nominativo=nome_azienda,
                    comune=contatto.get('comune') or '',
                ),
                pod=p_pod,
                email_dest=email_dest,
                template_id=template_id or 'custom_email',
                subject=subject,
                status='sent',
                user=username,
                detail=record_detail,
            )
    except Exception:
        logger.exception("[EMAIL] Invio riuscito ma registrazione storico lettere fallita")

    # Scrivi nota automatica sul DB filone per ciascun contatore (es. "18/08 mandata mail per 25/08")
    for c_item in contatori_list:
        c_pod = c_item.get('pod') or c_item.get('pod_filone') or primary_pod
        c_data = c_item.get('data_intervento') or c_item.get('data_pian_filone') or c_item.get('data_pianificazione') or contatto.get('data_intervento') or contatto.get('data_pian_filone')
        if c_pod and c_pod != '?':
            nota_invio = build_nota_invio('mail', data_intervento=c_data)
            registra_nota_su_db(c_pod, nota_invio, user=username, origin=f'{context}_invia_email')

    # Registra in Audit Log
    try:
        from moduli import db as _db
        _db.record_audit_log(
            action='send_email',
            table_name='filone',
            record_id=primary_pod,
            user=username,
            details={
                'email': email_dest,
                'contatori_count': len(contatori_list),
                'template_id': template_id,
                'context': context,
                'allegati': allegati_nomi,
            }
        )
    except Exception:
        pass

    logger.info(f"[EMAIL] Inviata con successo a {email_dest} ({len(contatori_list)} contatori, context={context})")
    return {
        'ok': True,
        'pod': primary_pod,
        'email': email_dest,
        'contatori_count': len(contatori_list),
        'allegati': allegati_nomi,
        'subject': subject
    }

