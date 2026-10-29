# -*- coding: utf-8 -*-
"""
Manager per la Gestione dei Comuni (info, contatti, bulk email).
"""
import io
import os

import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from email.utils import formataddr

from config import PROVINCIA_ATTIVA
from moduli import db as _db
from moduli.smtp import load_smtp_settings, send_smtp_message, get_provincia_email

logger = logging.getLogger('mappa_algeco')

# Percorsi condivisi con distinte
_ROOT_DIR = os.path.join(os.path.dirname(__file__), '..')
_ALLEGATI_DIR = os.path.join(_ROOT_DIR, 'data', 'allegati')

def _style_excel_worksheet(worksheet):
    """Applica uno stile pulito e professionale a un foglio Excel aperto con openpyxl."""
    try:
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    except ImportError:
        return # Se manca qualcosa salta silenziosamente
        
    # Stili
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="00529B") # Blu aziendale
    border = Border(
        left=Side(border_style="thin", color="E0E0E0"),
        right=Side(border_style="thin", color="E0E0E0"),
        top=Side(border_style="thin", color="E0E0E0"),
        bottom=Side(border_style="thin", color="E0E0E0")
    )
    alignment = Alignment(vertical="center", wrap_text=True)
    fill_even = PatternFill("solid", fgColor="F5F7FA") # Grigio chiarissimo alternato
    
    # 1. Colora e imposta l'header (riga 1)
    for cell in worksheet[1]:
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = border
        
    # 2. Formatta tutte le altre righe
    for row_idx, row in enumerate(worksheet.iter_rows(min_row=2), start=2):
        for cell in row:
            cell.border = border
            cell.alignment = alignment
            if row_idx % 2 == 0:
                cell.fill = fill_even
                
    # 3. Auto-fit delle colonne (con limite max per evitare colonne enormi)
    for col in worksheet.columns:
        max_length = 0
        column = col[0].column_letter
        for cell in col:
            try:
                # Per calcolare l'auto-fit, spezza sui ritorni a capo
                lines = str(cell.value).split('\n')
                for line in lines:
                    if len(line) > max_length:
                        max_length = len(line)
            except:
                pass
        adjusted_width = (max_length + 2)
        worksheet.column_dimensions[column].width = min(max(adjusted_width, 10), 50)


def _normalize_string(s):
    if s is None:
        return ''
    return str(s).strip()

def get_comuni_list():
    conn = _db.get_conn()
    import config
    disabled_lcls = [str(x).strip().upper() for x in getattr(config, 'LCL_DISABILITATI', []) if str(x).strip()]
    where_extra = ""
    params_stats = []
    if disabled_lcls:
        placeholders = ','.join('?' for _ in disabled_lcls)
        where_extra = f" AND UPPER(TRIM(f.lcl)) NOT IN ({placeholders})"
        params_stats = list(disabled_lcls)
    
    # Pre-calcola le statistiche dai contatori (filone)
    stats = conn.execute(f'''
        SELECT UPPER(f.comune) as comune,
               GROUP_CONCAT(DISTINCT f.lcl) as lcls,
               COUNT(*) as num_contatori
        FROM filone f
        WHERE f.is_orphan = 0 AND f.comune IS NOT NULL AND f.comune != ''{where_extra}
        GROUP BY UPPER(f.comune)
    ''', params_stats).fetchall()
    stats_map = {r['comune']: {'lcl': r['lcls'], 'num': r['num_contatori']} for r in stats}
    
    # Recupera ztl da filone_extra per ciascun comune
    ztl_extras = conn.execute(f'''
        SELECT UPPER(f.comune) as comune, MAX(fe.value) as ztl_extra
        FROM filone_extra fe
        JOIN filone f ON f.eneltel_key = fe.eneltel_key 
             AND COALESCE(f.lcl,'') = COALESCE(fe.lcl,'')
        WHERE fe.column_name = 'ztl' AND fe.value != ''
              AND f.is_orphan = 0 AND f.comune IS NOT NULL AND f.comune != ''{where_extra}
        GROUP BY UPPER(f.comune)
    ''', params_stats).fetchall()
    ztl_map = {r['comune']: r['ztl_extra'] for r in ztl_extras}
    
    rows = conn.execute(
        "SELECT * FROM comuni_info WHERE provincia=?",
        (PROVINCIA_ATTIVA.upper(),)
    ).fetchall()
    
    result = []
    for r in rows:
        d = dict(r)
        c_upper = d['comune'].upper()
        d['lcl'] = stats_map.get(c_upper, {}).get('lcl', '')
        d['num_contatori'] = stats_map.get(c_upper, {}).get('num', 0)
        
        # ZTL è 100% letto dai contatori (filone_extra). Ignoriamo il valore in comuni_info.
        d['ztl'] = ztl_map.get(c_upper, '')
            
        result.append(d)
        
    result.sort(key=lambda x: (-x['num_contatori'], x['comune']))
    return result

def update_comune(comune, data):
    conn = _db.get_conn()
    now = _db._now_ts()
    
    allowed_keys = [
        'email_comune', 'email_polizia', 'email_uff_tecnico',
        'telefono_comune', 'telefono_polizia', 'telefono_uff_tecnico',
        'pec_comune', 'note', 'numero_ce', 'ztl', 'tipologie_zone', 'polizia', 'uff_tecnico', 'protocollo', 'fb',
        'indicazioni_ztl'
    ]
    
    set_clauses = []
    params = []
    for k, v in data.items():
        if k in allowed_keys:
            set_clauses.append(f"{k} = ?")
            params.append(v)
            
    if not set_clauses:
        return False
        
    set_clauses.append("last_updated_ts = ?")
    params.append(now)
    
    params.extend([PROVINCIA_ATTIVA.upper(), comune.upper()])
    
    query = f"UPDATE comuni_info SET {', '.join(set_clauses)} WHERE provincia=? AND comune=?"
    
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute(query, params)
        
        # Propaga ZTL a tutti i contatori (utenti) di quel comune aggiungendolo come colonna extra
        if 'ztl' in data:
            conn.execute('''
                INSERT OR REPLACE INTO filone_extra (eneltel_key, lcl, column_name, value)
                SELECT eneltel_key, lcl, 'ztl', ?
                FROM filone
                WHERE UPPER(comune) = ?
            ''', (data['ztl'], comune.upper()))
            
            # Aggiorna last_local_change_ts così che la modifica "vinca" sui futuri import Excel
            conn.execute('''
                UPDATE filone
                SET last_local_change_ts = ?
                WHERE UPPER(comune) = ?
            ''', (now, comune.upper()))
            
    return True

def format_contatti_export(email_str, tel_str):
    import re
    emails = []
    pecs = []
    if email_str:
        tokens = re.split(r'[\s,;]+', email_str.strip())
        for t in tokens:
            if not t: continue
            if 'pec' in t.lower():
                pecs.append(t)
            else:
                emails.append(t)
                
    lines = []
    if emails:
        lines.append(f"Email: {', '.join(emails)}")
    if pecs:
        lines.append(f"PEC: {', '.join(pecs)}")
    if tel_str and tel_str.strip():
        lines.append(f"Tel: {tel_str.strip()}")
        
    return "\n".join(lines)

def export_comuni_to_excel():
    """Esporta i dati dei comuni in un formato Excel pulito e strutturato.
    Le stesse colonne possono essere lette da import_comuni_clean_excel."""
    try:
        import pandas as pd
    except ImportError:
        return None
        
    comuni = get_comuni_list()
    if not comuni:
        return None
        
    df = pd.DataFrame(comuni)
    
    # Seleziona solo le colonne pulite, nell'ordine desiderato
    cols = [
        'comune', 'email_comune', 'telefono_comune',
        'email_polizia', 'telefono_polizia', 'pec_comune',
        'note', 'ztl', 'indicazioni_ztl', 'tipologie_zone', 'numero_ce'
    ]
    
    # Assicura che le colonne esistano (in caso di db vuoto)
    for c in cols:
        if c not in df.columns:
            df[c] = ''
            
    df = df[cols]
    
    # Rinomina per rendere l'header bello da leggere (ma processabile)
    df = df.rename(columns={
        'comune': 'Comune',
        'email_comune': 'Email_Ufficio_Tecnico',
        'telefono_comune': 'Telefono_Ufficio_Tecnico',
        'email_polizia': 'Email_Polizia',
        'telefono_polizia': 'Telefono_Polizia',
        'pec_comune': 'PEC',
        'note': 'Note',
        'ztl': 'ZTL',
        'indicazioni_ztl': 'Indicazioni_ZTL',
        'tipologie_zone': 'Tipologie_Zone',
        'numero_ce': 'Numero_CE'
    })
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Comuni')
        # Applica stile visivo
        _style_excel_worksheet(writer.sheets['Comuni'])
    
    return output.getvalue()

def import_comuni_clean_excel(file_path):
    """Importa l'anagrafica dei comuni da un file Excel strutturato pulito (esportato da export_comuni_to_excel).
    Logica di merge intelligente: se l'Excel ha una cella vuota ma il DB ha un valore, il valore viene conservato."""
    try:
        import pandas as pd
    except ImportError:
        return {'success': False, 'error': 'pandas non installato'}
        
    try:
        # Legge il primo foglio, o il foglio "Comuni" se esiste
        try:
            df = pd.read_excel(file_path, sheet_name='Comuni', dtype=str)
        except ValueError:
            df = pd.read_excel(file_path, dtype=str) # Fallback prima scheda
    except Exception as e:
        return {'success': False, 'error': f"Errore lettura Excel: {e}"}
        
    if df.empty:
        return {'success': False, 'error': 'Il file Excel è vuoto'}
        
    # Mappa delle colonne Excel (case insensitive, underscore tollerati) -> campo DB
    header_mapping = {
        'comune': 'comune',
        'email_ufficio_tecnico': 'email_comune',
        'telefono_ufficio_tecnico': 'telefono_comune',
        'email_polizia': 'email_polizia',
        'telefono_polizia': 'telefono_polizia',
        'pec': 'pec_comune',
        'note': 'note',
        'ztl': 'ztl',
        'indicazioni_ztl': 'indicazioni_ztl',
        'tipologie_zone': 'tipologie_zone',
        'numero_ce': 'numero_ce'
    }
    
    # Mappa le colonne effettive dell'Excel normalizzate (lowercase)
    excel_headers_normalized = {str(c).strip().lower().replace(' ', '_'): c for c in df.columns}
    
    conn = _db.get_conn()
    now = _db._now_ts()
    count_updated = 0
    
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        
        for _, row in df.iterrows():
            row_dict = {}
            for norm_key, db_key in header_mapping.items():
                orig_col = excel_headers_normalized.get(norm_key)
                if orig_col:
                    val = _normalize_string(row.get(orig_col, ''))
                    if val.lower() == 'nan': val = ''
                    row_dict[db_key] = val
                    
            comune = row_dict.get('comune', '')
            if not comune:
                continue # Salta righe senza nome comune
                
            comune = comune.upper()
            
            # Recupera il record esistente
            existing = conn.execute(
                "SELECT * FROM comuni_info WHERE provincia=? AND comune=?",
                (PROVINCIA_ATTIVA.upper(), comune)
            ).fetchone()
            
            merged_row = dict(existing) if existing else {}
            
            # Applica le regole di merge (conserva DB se Excel è vuoto)
            for db_key in header_mapping.values():
                if db_key == 'comune': continue
                
                excel_val = row_dict.get(db_key, '')
                db_val = merged_row.get(db_key, '')
                
                if excel_val:
                    merged_row[db_key] = excel_val # L'Excel ha un valore, vince l'Excel
                else:
                    merged_row[db_key] = db_val # L'Excel è vuoto, conserva il DB
            
            # Esegui UPSERT
            merged_row['provincia'] = PROVINCIA_ATTIVA.upper()
            merged_row['comune'] = comune
            merged_row['last_updated_ts'] = now
            
            # Assicurati che tutti i campi esistano nel dizionario (anche per INSERT)
            for k in ['numero_ce', 'ztl', 'indicazioni_ztl', 'tipologie_zone', 'polizia', 'uff_tecnico', 'protocollo', 'fb', 'email_comune', 'telefono_comune', 'email_polizia', 'telefono_polizia', 'pec_comune', 'note']:
                merged_row.setdefault(k, '')
            
            conn.execute('''
                INSERT INTO comuni_info (
                    provincia, comune, numero_ce, ztl, indicazioni_ztl, tipologie_zone, polizia, 
                    uff_tecnico, protocollo, fb, last_updated_ts,
                    email_comune, telefono_comune, email_polizia, telefono_polizia, pec_comune, note
                ) VALUES (
                    :provincia, :comune, :numero_ce, :ztl, :indicazioni_ztl, :tipologie_zone, :polizia,
                    :uff_tecnico, :protocollo, :fb, :last_updated_ts,
                    :email_comune, :telefono_comune, :email_polizia, :telefono_polizia, :pec_comune, :note
                )
                ON CONFLICT(provincia, comune) DO UPDATE SET
                    numero_ce = excluded.numero_ce,
                    ztl = excluded.ztl,
                    indicazioni_ztl = excluded.indicazioni_ztl,
                    tipologie_zone = excluded.tipologie_zone,
                    email_comune = excluded.email_comune,
                    telefono_comune = excluded.telefono_comune,
                    email_polizia = excluded.email_polizia,
                    telefono_polizia = excluded.telefono_polizia,
                    pec_comune = excluded.pec_comune,
                    note = excluded.note,
                    last_updated_ts = excluded.last_updated_ts
            ''', merged_row)
            
            # Propaga ZTL al filone
            if merged_row.get('ztl'):
                conn.execute('''
                    INSERT OR REPLACE INTO filone_extra (eneltel_key, lcl, column_name, value)
                    SELECT eneltel_key, lcl, 'ztl', ?
                    FROM filone
                    WHERE UPPER(comune) = ?
                ''', (merged_row['ztl'], comune))
                
            count_updated += 1
            
    logger.info(f"Importati e aggiornati {count_updated} comuni tramite template.")
    return {'success': True, 'count': count_updated}
    
def generate_excel_contatori_comune(comune):
    """
    Genera un file Excel in memoria con i contatori appartenenti a `comune`.
    Colonne richieste: tipologia del misuratore, POD, Eneltel, Matricola contatore e codice matricola, indirizzo e civico
    """
    conn = _db.get_conn()
    query = """
        SELECT tipo_misuratore, pod, eneltel_key AS eneltel, 
               matricola_ante, codice_ante, 
               indirizzo, civico, comune
        FROM filone
        WHERE is_orphan=0 AND UPPER(comune) = ?
    """
    rows = conn.execute(query, (comune.upper(),)).fetchall()
    
    try:
        import pandas as pd
    except ImportError:
        return None
        
    data = []
    for r in rows:
        data.append({
            'Tipologia Misuratore': r['tipo_misuratore'] or '',
            'POD': r['pod'] or '',
            'Eneltel': r['eneltel'] or '',
            'Codice Matricola': r['codice_ante'] or '',
            'Matricola Contatore': r['matricola_ante'] or '',
            'Indirizzo': r['indirizzo'] or '',
            'Civico': r['civico'] or ''
        })
        
    df = pd.DataFrame(data)
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Contatori')
        # Applica stile visivo
        _style_excel_worksheet(writer.sheets['Contatori'])
    
    return output.getvalue()

def _is_pec(email_str):
    if not email_str: return False
    e = email_str.lower()
    return any(p in e for p in ['@pec.', '@legalmail', '@cert.', '@postacert.', '@arubapec', '@sicurezzapostale', '.pec.it', '@pec.it'])

def generate_email_previews(comuni_ids, template_name, escludi_pec=False, filtro_tipo=None):
    import os
    comuni = get_comuni_list()
    comuni_dict = {c['comune']: c for c in comuni}
    
    if filtro_tipo is None:
        filtro_tipo = 'no_pec' if escludi_pec else 'tutti'
    
    subject_base = "SOSTITUZIONE CONTATORI E-DISTRIBUZIONE"
    pdf_attachment = f"attestazione_{PROVINCIA_ATTIVA.lower()}.pdf"
    
    previews = []
    errors = []
    
    firma_html = ""
    try:
        import json
        with open(os.path.join(os.path.dirname(__file__), '..', 'data', 'distinte_templates.json'), 'r', encoding='utf-8') as f:
            tmpl_data = json.load(f)
            raw_firma = tmpl_data.get('settings', {}).get('firma_html', '')
            if raw_firma:
                from moduli import mail_manager
                firma_html = mail_manager.resolve_signature_html(raw_firma, prov=PROVINCIA_ATTIVA)
    except Exception:
        pass
    
    for comune_name in comuni_ids:
        c = comuni_dict.get(comune_name)
        if not c:
            errors.append(f"{comune_name}: Dati non trovati")
            continue
            
        if template_name == 'polizia':
            dest_email = c.get('email_polizia')
            if not dest_email:
                import re
                raw_text = str(c.get('polizia') or '')
                emails_found = re.findall(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', raw_text)
                if emails_found:
                    dest_email = ', '.join(emails_found)
                    
            if dest_email:
                parts = [x.strip() for x in dest_email.replace(';', ',').split(',') if x.strip()]
                if filtro_tipo in ('solo_mail', 'no_pec'):
                    filtered = [x for x in parts if not _is_pec(x)]
                elif filtro_tipo in ('solo_pec', 'no_mail'):
                    filtered = [x for x in parts if _is_pec(x)]
                else:
                    filtered = parts

                if not filtered:
                    errors.append(f"{comune_name}: Esclusa Polizia dal filtro ({filtro_tipo})")
                    continue
                dest_email = ', '.join(filtered)
                
            if not dest_email:
                errors.append(f"{comune_name}: Email Polizia assente")
                continue
                
            body_html = """
            <p>Buongiorno,</p>
            <p>vi contattiamo da <b>Algeco Power Srl</b> per informarvi che la nostra società è stata incaricata da <b>E-Distribuzione</b> ad effettuare la sostituzione dei contatori dell'energia elettrica presso il vostro Comune.</p>
            <p>La sostituzione, richiesta direttamente da E-Distribuzione, è gratuita ed obbligatoria e avviene in attuazione a provvedimenti normativi che stabiliscono l'uso di sistemi di misurazione che soddisfino i requisiti richiesti dal D.Lgs. 102/2014, di recepimento della direttiva europea 2012/27/UE sull'efficienza energetica.</p>
            <p>Vi informiamo infine che i nostri tecnici lavorano dal lunedì al sabato con regolare tesserino di riconoscimento e svolgono la sostituzione a titolo totalmente gratuito.<br>
            I concittadini intestatari dei contatori da sostituire sono stati regolarmente avvisati e non dovranno firmare nessun documento che attesti la manutenzione.</p>
            <p><b><mark style="background-color: yellow;">VI CHIEDIAMO CORTESEMENTE DI RISPONDERE A QUESTA EMAIL PER COMUNICARCI SE NEL VOSTRO COMUNE È PRESENTE UNA ZTL. IN CASO AFFERMATIVO RICHIEDIAMO INDICAZIONI SUL COME PROCEDERE PER L'ACCESSO DEI MEZZI.</mark></b></p>
            <p>Alleghiamo l'aggiudicazione del bando di gara che attesta il nostro incarico e restiamo a disposizione per chiarimenti.</p>
            <p>Cordiali saluti,<br>Algeco Power Srl</p>
            """ + firma_html
            subject = f"POLIZIA MUNICIPALE COMUNE DI {comune_name} - {subject_base}"
            allegati = [pdf_attachment]
            
            previews.append({
                'comune': comune_name,
                'email': dest_email,
                'subject': subject,
                'bodyHtml': body_html,
                'allegati': allegati
            })
            
        elif template_name == 'comune':
            dest_email = c.get('email_comune') or c.get('email_uff_tecnico') or c.get('pec_comune')
            if not dest_email:
                import re
                raw_text = str(c.get('uff_tecnico') or '') + ' ' + str(c.get('protocollo') or '')
                emails_found = re.findall(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', raw_text)
                if emails_found:
                    dest_email = ', '.join(emails_found)
                    
            if dest_email:
                parts = [x.strip() for x in dest_email.replace(';', ',').split(',') if x.strip()]
                if filtro_tipo in ('solo_mail', 'no_pec'):
                    filtered = [x for x in parts if not _is_pec(x)]
                elif filtro_tipo in ('solo_pec', 'no_mail'):
                    filtered = [x for x in parts if _is_pec(x)]
                else:
                    filtered = parts

                if not filtered:
                    errors.append(f"{comune_name}: Escluso Comune dal filtro ({filtro_tipo})")
                    continue
                dest_email = ', '.join(filtered)
                
            if not dest_email:
                errors.append(f"{comune_name}: Email Comune/Uff. Tecnico assente")
                continue
                
            body_html = """
            <p>Buongiorno,</p>
            <p>vi contattiamo da <b>Algeco Power Srl</b> per informarvi che la nostra società è stata incaricata da <b>E-Distribuzione</b> ad effettuare la sostituzione dei contatori dell'energia elettrica presso il vostro Comune.</p>
            <p>La sostituzione, richiesta direttamente da E-Distribuzione, è gratuita ed obbligatoria e avviene in attuazione a provvedimenti normativi che stabiliscono l'uso di sistemi di misurazione che soddisfino i requisiti richiesti dal D.Lgs. 102/2014, di recepimento della direttiva europea 2012/27/UE sull'efficienza energetica.</p>
            <p>Alleghiamo l'aggiudicazione del bando di gara che attesta il nostro incarico e l'elenco dei contatori da sostituire che risultano intestati al vostro comune con i riferimenti per la loro individuazione.</p>
            <p><b><mark style="background-color: yellow;">PER PERMETTERCI L'ACCESSO AI CONTATORI COLLOCATI IN LOCALI CHIUSI / UFFICI, CHIEDIAMO CORTESEMENTE DI RISPONDERE A QUESTA EMAIL INDICANDO UN RIFERIMENTO DIRETTO DI UN VOSTRO INCARICATO CHE POSSA DARE SUPPORTO AI NOSTRI OPERATORI.</mark></b></p>
            <p>Restiamo a disposizione per chiarimenti e porgiamo cordiali saluti,</p>
            <p>Algeco Power Srl</p>
            """ + firma_html
            subject = f"COMUNE DI {comune_name} - {subject_base}"
            allegati = [pdf_attachment, f"Contatori_{comune_name}.xlsx"]
            
            previews.append({
                'comune': comune_name,
                'email': dest_email,
                'subject': subject,
                'bodyHtml': body_html,
                'allegati': allegati
            })

    return previews, errors

# ============================================================================
# INFRASTRUTTURA SMTP (condivisa con distinte)
# ============================================================================









def _build_comune_email_message(dest_email, subject, body_html, allegati_nomi,
                                 comune_name, smtp_settings):
    """Costruisce il messaggio MIME per un invio a comune/polizia."""
    import email.utils as email_utils

    from_email = smtp_settings.get('from_email', smtp_settings.get('username', ''))
    from_name = smtp_settings.get('from_name', 'Ufficio Tecnico Algeco Power')

    # Plain text
    from moduli.mail_manager import html_to_plain_text
    body_plain = html_to_plain_text(body_html)

    msg_alt = MIMEMultipart('alternative')
    msg_alt.attach(MIMEText(body_plain, 'plain', 'utf-8'))
    msg_alt.attach(MIMEText(body_html, 'html', 'utf-8'))

    # Se ci sono allegati, wrappa in mixed
    has_attachments = bool(allegati_nomi)
    if has_attachments:
        msg = MIMEMultipart('mixed')
        msg.attach(msg_alt)

        for nome in allegati_nomi:
            if nome.endswith('.xlsx'):
                # Excel generato al volo
                excel_bytes = generate_excel_contatori_comune(comune_name)
                if excel_bytes:
                    part = MIMEBase('application', 'octet-stream')
                    part.set_payload(excel_bytes)
                    encoders.encode_base64(part)
                    part.add_header('Content-Disposition', 'attachment', filename=nome)
                    msg.attach(part)
            else:
                # File statico dalla cartella allegati
                filepath = os.path.join(_ALLEGATI_DIR, os.path.basename(nome))
                if os.path.isfile(filepath):
                    with open(filepath, 'rb') as f:
                        part = MIMEBase('application', 'octet-stream')
                        part.set_payload(f.read())
                    encoders.encode_base64(part)
                    part.add_header('Content-Disposition', 'attachment', filename=nome)
                    msg.attach(part)
                else:
                    logger.warning(f"[COMUNI-EMAIL] Allegato non trovato: {filepath}")
    else:
        msg = msg_alt

    msg['From'] = formataddr((from_name, from_email))
    msg['To'] = dest_email
    msg['Subject'] = subject
    msg['Reply-To'] = from_email
    msg['Message-ID'] = email_utils.make_msgid(
        domain=from_email.split('@')[-1] if '@' in from_email else 'algecopower.it')
    msg['Date'] = email_utils.formatdate(localtime=True)

    # Includi email della provincia in CCn (copia nascosta), se non coincidente con il destinatario
    prov_bcc = get_provincia_email()
    if prov_bcc and prov_bcc.lower() != (dest_email or '').lower():
        msg['Bcc'] = prov_bcc

    return msg


def send_single_email(email_data, current_user):
    """Invia UNA singola email a un comune/polizia. Chiamata dal frontend in sequenza.

    Returns:
        dict con 'ok', 'comune', 'email', e opzionalmente 'errore'.
    """
    comune_name = email_data.get('comune', '')
    dest_email = email_data.get('email', '')
    subject = email_data.get('subject', '')
    body_html = email_data.get('body_override', email_data.get('bodyHtml', ''))
    allegati_nomi = email_data.get('allegati', [])

    if not comune_name or not dest_email:
        return {'ok': False, 'comune': comune_name, 'error_code': 'INVALID_DATA', 'errore': 'Dati email incompleti'}

    smtp_settings = load_smtp_settings()
    if not smtp_settings.get('host') or not smtp_settings.get('password'):
        return {'ok': False, 'comune': comune_name,
                'error_code': 'SMTP_NOT_CONFIGURED',
                'errore': 'Configurazione SMTP incompleta. Configura le credenziali SMTP.'}

    try:
        msg = _build_comune_email_message(
            dest_email, subject, body_html, allegati_nomi,
            comune_name, smtp_settings
        )

        ok_send, send_err = send_smtp_message(
            smtp_settings, msg, max_retries=3, base_delay=6, save_sent=True
        )
        if not ok_send:
            if send_err and ('autenticazione' in send_err.lower() or 'auth' in send_err.lower()):
                return {'ok': False, 'comune': comune_name, 'email': dest_email, 'error_code': 'SMTP_AUTH_FAILED', 'errore': send_err}
            return {'ok': False, 'comune': comune_name, 'email': dest_email, 'error_code': 'SEND_FAILED', 'errore': send_err or 'Errore invio email'}

        logger.info(f"[COMUNI-EMAIL] Inviata a {dest_email} per comune {comune_name} (user={current_user})")
        return {'ok': True, 'comune': comune_name, 'email': dest_email}

    except smtplib.SMTPAuthenticationError:
        err = 'Autenticazione SMTP fallita'
        logger.error(f"[COMUNI-EMAIL] {err} per {comune_name}")
        return {'ok': False, 'comune': comune_name, 'error_code': 'SMTP_AUTH_FAILED', 'errore': err}
    except Exception as e:
        logger.error(f"[COMUNI-EMAIL] Errore invio a {dest_email} per {comune_name}: {e}")
        return {'ok': False, 'comune': comune_name, 'email': dest_email, 'error_code': 'SEND_FAILED', 'errore': str(e)}


def send_bulk_emails(emails_data, current_user):
    """Invio bulk: invia tutte le email in sequenza. Usato come fallback.

    Per l'invio con progress bar, il frontend usa send_single_email in loop.
    """
    n_sent = 0
    errors = []

    for email_data in emails_data:
        result = send_single_email(email_data, current_user)
        if result.get('ok'):
            n_sent += 1
        else:
            errors.append(f"{result.get('comune', '?')}: {result.get('errore', 'Errore sconosciuto')}")

    return n_sent, errors

