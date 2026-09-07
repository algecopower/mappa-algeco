# -*- coding: utf-8 -*-
"""
moduli/mail_manager.py — Gestione Mail avanzata per Mappa Algeco.

Funzionalità:
- Storage e indicizzazione email in SQLite (inbox, sent, drafts, scheduled, trash, archive).
- Invio SMTP immediato e programmato (con scheduling worker).
- Sincronizzazione IMAP automatica e manuale della posta in arrivo.
- Categorizzazione intelligente ad HOC basata su mittente, rubrica Comuni, Aziende e NLP/keyword.
- Template dinamici con risoluzione variabili e Frasario rapido ("Spiaccica Template").
- Autocompletamento contatti unificato (Comuni, Aziende, Filone Contatori, Storico).
- Gestione allegati (upload custom e allegati standard aziendali).
"""

import email
import email.header
import email.utils
import imaplib
import json
import logging
import mimetypes
import os
import re
import sqlite3
import time
import urllib.parse
import uuid
from datetime import datetime
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, parseaddr

from config import PROVINCIA_ATTIVA
from moduli import db
from moduli.bulk_contatori_email import registra_nota_su_db
from moduli.safe_json import json_read, json_write
from moduli.smtp import load_smtp_settings, send_smtp_message

logger = logging.getLogger('mappa_algeco')

_ROOT_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
_ALLEGATI_DIR = os.path.join(_ROOT_DIR, 'data', 'allegati')
_ATTACHMENTS_STORAGE_DIR = os.path.join(_ROOT_DIR, 'data')


def _get_prov(prov=None):
    """Ritorna la provincia attiva in minuscolo."""
    return (prov or PROVINCIA_ATTIVA).lower()


def _get_attachments_dir(prov=None):
    """Ritorna la cartella di archiviazione allegati per la provincia."""
    p = _get_prov(prov)
    d = os.path.join(_ATTACHMENTS_STORAGE_DIR, p, 'mail_attachments')
    os.makedirs(d, exist_ok=True)
    return d


def _now_ts():
    return int(time.time())


def _decode_header_val(val):
    """Decodifica un header RFC2047 in stringa Python UTF-8 leggibile."""
    if not val:
        return ''
    parts = email.header.decode_header(val)
    res = []
    for text, enc in parts:
        if isinstance(text, bytes):
            try:
                res.append(text.decode(enc or 'utf-8', errors='replace'))
            except Exception:
                res.append(text.decode('utf-8', errors='replace'))
        else:
            res.append(str(text))
    return ''.join(res).strip()


def _extract_plain_snippet(text, max_len=160):
    """Estrae uno snippet pulito di testo da HTML o plain text."""
    if not text:
        return ''
    clean = re.sub(r'<style.*?>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r'<script.*?>.*?</script>', '', clean, flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r'<[^>]+>', ' ', clean)
    clean = re.sub(r'&[a-zA-Z0-9#]+;', ' ', clean)
    clean = re.sub(r'\s+', ' ', clean).strip()
    return clean[:max_len] + ('...' if len(clean) > max_len else '')


def html_to_plain_text(html):
    """
    Converte codice HTML in testo puro leggibile, preservando a capi e liste.
    Converte <br>, <p>, <div> in ritorni a capo, tag <a> in 'testo (url)',
    grassetto/corsivo in markdown (*, _) e decodifica entità HTML.
    """
    if not html:
        return ''
    text = str(html)
    # Link: <a href="url">testo</a> -> testo (url) oppure url se testo vuoto o identico
    def _link_repl(m):
        href = (m.group(1) or '').strip()
        label = (m.group(2) or '').strip()
        if not label or label == href:
            return href
        return f"{label} ({href})"
    text = re.sub(r'<a[^>]*href="([^"]*?)"[^>]*>\s*(.*?)\s*</a>', _link_repl, text, flags=re.IGNORECASE | re.DOTALL)

    # Interruzioni di riga e blocchi
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</p\s*>', '\n\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</div>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<li[^>]*>', '• ', text, flags=re.IGNORECASE)
    text = re.sub(r'</li>', '\n', text, flags=re.IGNORECASE)

    # Formattazione per WhatsApp / markdown
    text = re.sub(r'<(?:b|strong)[^>]*>(.*?)</(?:b|strong)>', r'*\1*', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<(?:i|em)[^>]*>(.*?)</(?:i|em)>', r'_\1_', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<(?:s|strike|del)[^>]*>(.*?)</(?:s|strike|del)>', r'~\1~', text, flags=re.IGNORECASE | re.DOTALL)

    # Rimuovi tutti i restanti tag HTML
    text = re.sub(r'<[^>]+>', '', text)

    # Decodifica entità HTML (&nbsp;, &amp;, ecc.)
    import html as html_lib
    text = html_lib.unescape(text)
    text = text.replace('\xa0', ' ')

    # Normalizza ritorni a capo
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _detect_email_province(from_addr, to_addrs, cc_addrs=None, default_prov='torino'):
    """
    Determina dinamicamente la provincia corretta di destinazione/competenza analizzando:
    1. Tutte le configurazioni SMTP registrate in data/smtp_settings.json.
    2. Tutte le province registrate dinamicamente in data/provinces.json.
    3. Mittente (from_addr), Destinatari (to_addrs) e Destinatari in copia (cc_addrs).
    """
    from_str = (from_addr or '').lower().strip()
    to_str = (' '.join(to_addrs) if isinstance(to_addrs, list) else str(to_addrs or '')).lower().strip()
    cc_str = (' '.join(cc_addrs) if isinstance(cc_addrs, list) else str(cc_addrs or '')).lower().strip()
    def_slug = (default_prov or 'torino').lower()

    # 1. Mappatura da configurazioni SMTP registrate per ogni provincia
    all_smtp = json_read(os.path.join(_ROOT_DIR, 'data', 'smtp_settings.json'), default={})
    if isinstance(all_smtp, dict):
        for prov_key, settings in all_smtp.items():
            if isinstance(settings, dict):
                addr = (settings.get('from_email') or settings.get('username') or '').lower().strip()
                if addr:
                    # Se il mittente è l'indirizzo della provincia prov_key
                    if addr in from_str:
                        # Se è inviata direttamente all'altra provincia default_prov, appartiene alla inbox di default_prov
                        def_settings = all_smtp.get(def_slug, {})
                        def_addr = (def_settings.get('from_email') or def_settings.get('username') or '').lower().strip()
                        if def_addr and (def_addr in to_str or def_addr in cc_str):
                            return def_slug
                        return prov_key.lower()

                    # Se è indirizzata specificamente a prov_key e non a default_prov
                    if addr in to_str or addr in cc_str:
                        def_settings = all_smtp.get(def_slug, {})
                        def_addr = (def_settings.get('from_email') or def_settings.get('username') or '').lower().strip()
                        if def_addr and def_addr not in to_str and def_addr not in cc_str:
                            return prov_key.lower()
                    
    # 2. Caricamento dinamico di tutte le province presenti in data/provinces.json
    provs_path = os.path.join(_ROOT_DIR, 'data', 'provinces.json')
    sys_provs = json_read(provs_path, default={}) or {}
    
    for p_key in sys_provs.keys():
        slug = p_key.lower()
        if slug == def_slug:
            continue
            
        patterns = [
            f"tecnico_{slug[:2]}", # es. tecnico_pv, tecnico_to, tecnico_lc, tecnico_cn, tecnico_al, tecnico_no
            f"@{slug}",           # es. @pavia, @torino, @lecco, @cuneo
            f"_{slug}"
        ]
        
        # Se from_str corrisponde a una provincia specifica diversa da default
        if any(pat in from_str for pat in patterns):
            if not any(f"tecnico_{def_slug[:2]}" in x for x in (to_str, cc_str)):
                return slug
                
        # Se to_str o cc_str corrisponde a una provincia specifica diversa da default
        if any(pat in to_str or pat in cc_str for pat in patterns):
            if not any(f"tecnico_{def_slug[:2]}" in x for x in (to_str, cc_str)):
                return slug
        
    return def_slug


# ============================================================================
# STATISTICHE & CONTEGGI
# ============================================================================

def get_mail_stats(prov=None):
    """Ritorna statistiche su email per cartella e per categoria."""
    p = _get_prov(prov)
    conn = db.get_conn()
    
    folders = ['inbox', 'sent', 'drafts', 'scheduled', 'starred', 'trash', 'archive']
    stats = {
        'folders': {f: {'total': 0, 'unread': 0} for f in folders},
        'categories': {},
        'total_unread': 0
    }
    
    rows = conn.execute(
        """SELECT folder, is_read, COUNT(*) as count 
           FROM mail_messages 
           WHERE provincia = ? 
           GROUP BY folder, is_read""",
        (p,)
    ).fetchall()
    
    for r in rows:
        f = r['folder']
        is_read = bool(r['is_read'])
        c = r['count']
        if f in stats['folders']:
            stats['folders'][f]['total'] += c
            if not is_read:
                stats['folders'][f]['unread'] += c
                if f == 'inbox':
                    stats['total_unread'] += c
                    
    starred_row = conn.execute(
        """SELECT COUNT(*) as total, SUM(CASE WHEN is_read = 0 THEN 1 ELSE 0 END) as unread 
           FROM mail_messages 
           WHERE provincia = ? AND is_starred = 1 AND folder != 'trash'""",
        (p,)
    ).fetchone()
    if starred_row:
        stats['folders']['starred']['total'] = starred_row['total'] or 0
        stats['folders']['starred']['unread'] = starred_row['unread'] or 0

    cat_rows = conn.execute(
        """SELECT category_id, COUNT(*) as total, 
                  SUM(CASE WHEN is_read = 0 THEN 1 ELSE 0 END) as unread 
           FROM mail_messages 
           WHERE provincia = ? AND folder != 'trash' AND category_id IS NOT NULL AND category_id != ''
           GROUP BY category_id""",
        (p,)
    ).fetchall()
    
    for r in cat_rows:
        cid = r['category_id']
        stats['categories'][cid] = {
            'total': r['total'] or 0,
            'unread': r['unread'] or 0
        }
        
    return stats


# ============================================================================
# QUERY & LISTING MESSAGGI
# ============================================================================

def list_messages(folder='inbox', category_id=None, query=None, 
                  unread_only=False, starred_only=False, has_attachments=False,
                  page=1, per_page=40, prov=None):
    """
    Ritorna la lista paginata dei messaggi con filtri e metadati essenziali.
    """
    p = _get_prov(prov)
    conn = db.get_conn()
    
    page = max(int(page or 1), 1)
    per_page = min(max(int(per_page or 40), 5), 100)
    offset = (page - 1) * per_page
    
    where_clauses = ["provincia = ?"]
    params = [p]
    
    if folder == 'starred':
        where_clauses.append("is_starred = 1")
        where_clauses.append("folder != 'trash'")
    elif folder:
        where_clauses.append("folder = ?")
        params.append(folder)
        
    if category_id:
        where_clauses.append("category_id = ?")
        params.append(category_id)
        
    if unread_only:
        where_clauses.append("is_read = 0")
        
    if starred_only and folder != 'starred':
        where_clauses.append("is_starred = 1")
        
    if has_attachments:
        where_clauses.append("attachments_json IS NOT NULL AND attachments_json != '[]' AND attachments_json != ''")
        
    if query and query.strip():
        q = f"%{query.strip().lower()}%"
        where_clauses.append("""
            (LOWER(subject) LIKE ? 
             OR LOWER(from_addr) LIKE ? 
             OR LOWER(from_name) LIKE ? 
             OR LOWER(to_addrs) LIKE ? 
             OR LOWER(snippet) LIKE ? 
             OR LOWER(COALESCE(linked_pod, '')) LIKE ? 
             OR LOWER(COALESCE(linked_comune, '')) LIKE ? 
             OR LOWER(COALESCE(linked_azienda, '')) LIKE ?)
        """)
        params.extend([q, q, q, q, q, q, q, q])
        
    where_sql = " AND ".join(where_clauses)
    
    count_cur = conn.execute(f"SELECT COUNT(*) as cnt FROM mail_messages WHERE {where_sql}", params)
    total_count = count_cur.fetchone()['cnt']
    
    order_col = "scheduled_ts" if folder == 'scheduled' else "COALESCE(received_ts, sent_ts, created_ts)"
    
    sql = f"""
        SELECT id, message_id, thread_id, folder, from_addr, from_name,
               to_addrs, cc_addrs, subject, snippet, category_id,
               is_read, is_starred, is_archived, is_scheduled,
               scheduled_ts, sent_ts, received_ts, created_ts, status, error_msg,
               attachments_json, linked_pod, linked_comune, linked_azienda
        FROM mail_messages
        WHERE {where_sql}
        ORDER BY {order_col} DESC
        LIMIT ? OFFSET ?
    """
    
    rows = conn.execute(sql, params + [per_page, offset]).fetchall()
    
    messages = []
    for r in rows:
        item = dict(r)
        try:
            item['to_addrs'] = json.loads(item['to_addrs']) if item['to_addrs'] else []
        except Exception:
            item['to_addrs'] = [item['to_addrs']] if item['to_addrs'] else []
            
        try:
            item['attachments'] = json.loads(item['attachments_json']) if item['attachments_json'] else []
        except Exception:
            item['attachments'] = []
            
        item['has_attachments'] = len(item['attachments']) > 0
        
        ts = item['scheduled_ts'] if folder == 'scheduled' else (item['received_ts'] or item['sent_ts'] or item['created_ts'])
        item['display_date'] = _format_display_date(ts)
        item['timestamp'] = ts
        
        messages.append(item)
        
    return {
        'messages': messages,
        'total': total_count,
        'page': page,
        'per_page': per_page,
        'total_pages': max((total_count + per_page - 1) // per_page, 1)
    }


def _format_display_date(ts):
    """Formatta un timestamp UNIX in modo leggibile (es: '14:35', 'Ieri', '18 Ago', '12/03/2026')."""
    if not ts:
        return ''
    try:
        dt = datetime.fromtimestamp(ts)
        now = datetime.now()
        
        if dt.date() == now.date():
            return dt.strftime('%H:%M')
        elif (now.date() - dt.date()).days == 1:
            return f"Ieri {dt.strftime('%H:%M')}"
        elif dt.year == now.year:
            mesi = ['Gen', 'Feb', 'Mar', 'Apr', 'Mag', 'Giu', 'Lug', 'Ago', 'Set', 'Ott', 'Nov', 'Dic']
            return f"{dt.day} {mesi[dt.month - 1]}"
        else:
            return dt.strftime('%d/%m/%Y')
    except Exception:
        return ''


def _resolve_cid_images(html_content, attachments, msg_id):
    """
    Sostituisce i riferimenti inline cid:xxx nelle immagini HTML con l'endpoint
    di visualizzazione diretta /api/mail/attachment/<msg_id>/<filename>?inline=1
    """
    if not html_content or 'cid:' not in html_content:
        return html_content

    cid_map = {}
    image_atts = []
    
    for att in (attachments or []):
        fn = att.get('filename') or ''
        fpath = att.get('file_path') or ''
        cid = (att.get('content_id') or '').strip('<> ')
        is_img = (att.get('content_type') or '').startswith('image/') or any(fn.lower().endswith(ext) for ext in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.bmp'))
        
        if is_img:
            image_atts.append(att)
            
        url_name = urllib.parse.quote(fn or os.path.basename(fpath))
        url = f"/api/mail/attachment/{msg_id}/{url_name}?inline=1"
        if cid:
            cid_map[cid.lower()] = url
        if fn:
            cid_map[fn.lower()] = url
            
    def _replace_cid(match):
        raw_cid = match.group(1).strip('<> ')
        cid_lower = raw_cid.lower()
        
        if cid_lower in cid_map:
            return f'src="{cid_map[cid_lower]}"'
        for k, url in cid_map.items():
            if k in cid_lower or cid_lower in k:
                return f'src="{url}"'
                
        if len(image_atts) == 1:
            att = image_atts[0]
            fn = att.get('filename') or os.path.basename(att.get('file_path', ''))
            return f'src="/api/mail/attachment/{msg_id}/{urllib.parse.quote(fn)}?inline=1"'
            
        if image_atts:
            att = image_atts[0]
            fn = att.get('filename') or os.path.basename(att.get('file_path', ''))
            return f'src="/api/mail/attachment/{msg_id}/{urllib.parse.quote(fn)}?inline=1"'

        return match.group(0)

    return re.sub(r'src=["\']cid:([^"\']+)["\']', _replace_cid, html_content, flags=re.IGNORECASE)


# ============================================================================
# DETTAGLIO MESSAGGIO
# ============================================================================

def get_message(msg_id, mark_read=False, prov=None):
    """Ritorna il dettaglio completo di una singola email."""
    p = _get_prov(prov)
    conn = db.get_conn()
    
    row = conn.execute(
        "SELECT * FROM mail_messages WHERE id = ? AND provincia = ?",
        (int(msg_id), p)
    ).fetchone()
    
    if not row:
        return None
        
    msg = dict(row)
    
    try:
        msg['to_addrs'] = json.loads(msg['to_addrs']) if msg['to_addrs'] else []
    except Exception:
        msg['to_addrs'] = [msg['to_addrs']] if msg['to_addrs'] else []
        
    try:
        msg['cc_addrs'] = json.loads(msg['cc_addrs']) if msg['cc_addrs'] else []
    except Exception:
        msg['cc_addrs'] = [msg['cc_addrs']] if msg['cc_addrs'] else []
        
    try:
        msg['bcc_addrs'] = json.loads(msg['bcc_addrs']) if msg['bcc_addrs'] else []
    except Exception:
        msg['bcc_addrs'] = [msg['bcc_addrs']] if msg['bcc_addrs'] else []
        
    try:
        msg['attachments'] = json.loads(msg['attachments_json']) if msg['attachments_json'] else []
    except Exception:
        msg['attachments'] = []
        
    try:
        msg['meta'] = json.loads(msg['meta_json']) if msg['meta_json'] else {}
    except Exception:
        msg['meta'] = {}

    # Risolvi inline CID images nel corpo HTML
    if msg.get('body_html') and 'cid:' in msg['body_html']:
        msg['body_html'] = _resolve_cid_images(msg['body_html'], msg.get('attachments', []), msg['id'])
        
    ts = msg['scheduled_ts'] if msg['folder'] == 'scheduled' else (msg['received_ts'] or msg['sent_ts'] or msg['created_ts'])
    msg['display_date_full'] = datetime.fromtimestamp(ts).strftime('%d/%m/%Y %H:%M:%S') if ts else ''
    msg['display_date'] = _format_display_date(ts)
    
    if mark_read and not msg['is_read']:
        conn.execute(
            "UPDATE mail_messages SET is_read = 1, updated_ts = ? WHERE id = ?",
            (_now_ts(), int(msg_id))
        )
        msg['is_read'] = 1

    if msg.get('linked_pod'):
        ek = db.find_eneltel_by_pod(msg['linked_pod'])
        if ek:
            filone_row = db.get_filone_row(ek)
            if filone_row:
                msg['pod_detail'] = {
                    'eneltel': ek,
                    'nominativo': filone_row.get('nominativo', ''),
                    'indirizzo': f"{filone_row.get('indirizzo', '')} {filone_row.get('civico', '')}".strip(),
                    'comune': filone_row.get('comune', ''),
                    'telefono': filone_row.get('telefono', ''),
                    'operatore': filone_row.get('operatore', ''),
                    'data_pianificata': filone_row.get('data_pianificata', ''),
                    'esito': filone_row.get('esito', '')
                }
                
    if msg.get('linked_comune'):
        com_cur = conn.execute(
            "SELECT * FROM comuni_info WHERE provincia = ? AND LOWER(comune) = LOWER(?)",
            (p, msg['linked_comune'])
        ).fetchone()
        if com_cur:
            msg['comune_detail'] = dict(com_cur)

    return msg


# ============================================================================
# AZIONI SINGOLE E BATCH
# ============================================================================

def mark_message_read(msg_id, is_read=True, prov=None):
    p = _get_prov(prov)
    conn = db.get_conn()
    conn.execute(
        "UPDATE mail_messages SET is_read = ?, updated_ts = ? WHERE id = ? AND provincia = ?",
        (1 if is_read else 0, _now_ts(), int(msg_id), p)
    )
    return True


def toggle_message_starred(msg_id, is_starred=None, prov=None):
    p = _get_prov(prov)
    conn = db.get_conn()
    if is_starred is None:
        conn.execute(
            """UPDATE mail_messages 
               SET is_starred = CASE WHEN is_starred = 1 THEN 0 ELSE 1 END, updated_ts = ? 
               WHERE id = ? AND provincia = ?""",
            (_now_ts(), int(msg_id), p)
        )
    else:
        conn.execute(
            "UPDATE mail_messages SET is_starred = ?, updated_ts = ? WHERE id = ? AND provincia = ?",
            (1 if is_starred else 0, _now_ts(), int(msg_id), p)
        )
    return True


def set_message_category(msg_id, category_id, prov=None):
    p = _get_prov(prov)
    conn = db.get_conn()
    conn.execute(
        "UPDATE mail_messages SET category_id = ?, updated_ts = ? WHERE id = ? AND provincia = ?",
        (category_id or None, _now_ts(), int(msg_id), p)
    )
    return True


def move_message(msg_id, destination_folder, prov=None):
    p = _get_prov(prov)
    conn = db.get_conn()
    conn.execute(
        "UPDATE mail_messages SET folder = ?, updated_ts = ? WHERE id = ? AND provincia = ?",
        (destination_folder, _now_ts(), int(msg_id), p)
    )
    return True


def delete_message(msg_id, permanent=False, prov=None):
    """
    Sposta nel cestino o elimina definitivamente.
    """
    p = _get_prov(prov)
    conn = db.get_conn()
    
    cur = conn.execute(
        "SELECT folder, attachments_json FROM mail_messages WHERE id = ? AND provincia = ?",
        (int(msg_id), p)
    )
    row = cur.fetchone()
    if not row:
        return False
        
    if row['folder'] == 'trash' or permanent:
        try:
            if row['attachments_json']:
                att_list = json.loads(row['attachments_json'])
                for a in att_list:
                    fp = a.get('file_path')
                    if fp and os.path.exists(fp) and 'mail_attachments' in fp:
                        os.remove(fp)
        except Exception as e:
            logger.warning(f"[MAIL] Errore pulizia allegati cancellazione {msg_id}: {e}")
            
        conn.execute("DELETE FROM mail_messages WHERE id = ? AND provincia = ?", (int(msg_id), p))
        return True
    else:
        conn.execute(
            "UPDATE mail_messages SET folder = 'trash', updated_ts = ? WHERE id = ? AND provincia = ?",
            (_now_ts(), int(msg_id), p)
        )
        return True


def batch_actions(msg_ids, action, value=None, prov=None):
    """Esegue un'azione cumulativa su una lista di ID messaggi."""
    if not msg_ids:
        return {'success': True, 'count': 0}
        
    p = _get_prov(prov)
    conn = db.get_conn()
    clean_ids = [int(i) for i in msg_ids if str(i).isdigit()]
    if not clean_ids:
        return {'success': True, 'count': 0}
        
    placeholders = ', '.join(['?'] * len(clean_ids))
    now = _now_ts()
    
    if action == 'mark_read':
        conn.execute(
            f"UPDATE mail_messages SET is_read = 1, updated_ts = ? WHERE id IN ({placeholders}) AND provincia = ?",
            [now] + clean_ids + [p]
        )
    elif action == 'mark_unread':
        conn.execute(
            f"UPDATE mail_messages SET is_read = 0, updated_ts = ? WHERE id IN ({placeholders}) AND provincia = ?",
            [now] + clean_ids + [p]
        )
    elif action == 'star':
        conn.execute(
            f"UPDATE mail_messages SET is_starred = 1, updated_ts = ? WHERE id IN ({placeholders}) AND provincia = ?",
            [now] + clean_ids + [p]
        )
    elif action == 'unstar':
        conn.execute(
            f"UPDATE mail_messages SET is_starred = 0, updated_ts = ? WHERE id IN ({placeholders}) AND provincia = ?",
            [now] + clean_ids + [p]
        )
    elif action == 'move':
        folder = value or 'inbox'
        conn.execute(
            f"UPDATE mail_messages SET folder = ?, updated_ts = ? WHERE id IN ({placeholders}) AND provincia = ?",
            [folder, now] + clean_ids + [p]
        )
    elif action == 'change_category':
        cat_id = value or None
        conn.execute(
            f"UPDATE mail_messages SET category_id = ?, updated_ts = ? WHERE id IN ({placeholders}) AND provincia = ?",
            [cat_id, now] + clean_ids + [p]
        )
    elif action == 'delete':
        for mid in clean_ids:
            delete_message(mid, permanent=bool(value), prov=p)
            
    return {'success': True, 'count': len(clean_ids)}


# ============================================================================
# CLASSIFICAZIONE INTELLIGENTE & AUTO-TAGGING
# ============================================================================

def auto_categorize_email(from_addr, to_addrs, subject, body_text='', prov=None):
    """
    Analizza i metadati e il contenuto di un'email per:
    1. Riconoscere la categoria ad HOC più appropriata.
    2. Riconoscere POD, Comune e Azienda collegati.
    """
    p = _get_prov(prov)
    conn = db.get_conn()
    
    combined_text = f"{subject} {body_text}".lower()
    from_lower = (from_addr or '').lower()
    to_str = (' '.join(to_addrs) if isinstance(to_addrs, list) else str(to_addrs)).lower()
    
    linked_pod = None
    linked_comune = None
    linked_azienda = None
    category_id = None
    
    # 1. Riconoscimento POD
    pod_match = re.search(r'\b(IT\d{3}E\d{8}[A-Z0-9]?)\b', combined_text, re.IGNORECASE)
    if not pod_match:
        pod_match = re.search(r'\b(IT[0-9A-Z]{14,15})\b', combined_text, re.IGNORECASE)
    if pod_match:
        cand_pod = pod_match.group(1).upper()
        ek = db.find_eneltel_by_pod(cand_pod)
        if ek:
            linked_pod = cand_pod
            
    # 2. Riconoscimento Comune da comuni_info
    comuni_rows = conn.execute("SELECT comune, email_comune, email_uff_tecnico, email_polizia, pec_comune FROM comuni_info WHERE provincia = ?", (p,)).fetchall()
    for crow in comuni_rows:
        c_name = crow['comune']
        com_emails = [
            (crow['email_comune'] or '').lower(),
            (crow['email_uff_tecnico'] or '').lower(),
            (crow['email_polizia'] or '').lower(),
            (crow['pec_comune'] or '').lower(),
        ]
        if any(ce and (ce in from_lower or ce in to_str) for ce in com_emails):
            linked_comune = c_name
            category_id = 'comuni_enti'
            break
        if len(c_name) >= 4 and re.search(rf'\b{re.escape(c_name.lower())}\b', combined_text):
            if not linked_comune:
                linked_comune = c_name

    # 3. Riconoscimento Aziende da lettere_contatti
    if not category_id:
        az_row = conn.execute(
            """SELECT contact_key, nominativo, pod, comune FROM lettere_contatti 
               WHERE provincia = ? AND (LOWER(email) = ? OR LOWER(email) LIKE ?) LIMIT 1""",
            (p, from_lower, f"%{from_lower}%")
        ).fetchone()
        if az_row:
            linked_azienda = az_row['nominativo']
            if not linked_pod and az_row['pod']:
                linked_pod = az_row['pod']
            if not linked_comune and az_row['comune']:
                linked_comune = az_row['comune']
            category_id = 'clienti_appuntamenti'

    # 4. Matching con keywords categorie in DB
    if not category_id:
        categories = conn.execute("SELECT id, keywords_json FROM mail_categories WHERE provincia = ? ORDER BY sort_order ASC", (p,)).fetchall()
        best_cat = None
        max_score = 0
        
        for cat in categories:
            cid = cat['id']
            kw_list = []
            if cat['keywords_json']:
                try:
                    kw_list = json.loads(cat['keywords_json'])
                except Exception:
                    pass
            score = 0
            for kw in kw_list:
                kw_clean = kw.strip().lower()
                if kw_clean in combined_text:
                    weight = 2 if kw_clean in subject.lower() else 1
                    score += weight
                    
            if score > max_score:
                max_score = score
                best_cat = cid
                
        if best_cat and max_score >= 1:
            category_id = best_cat
        else:
            if linked_pod:
                category_id = 'clienti_appuntamenti'
            elif linked_comune:
                category_id = 'comuni_enti'
            else:
                category_id = 'amministrazione'

    return {
        'category_id': category_id,
        'linked_pod': linked_pod,
        'linked_comune': linked_comune,
        'linked_azienda': linked_azienda
    }


# ============================================================================
# COMPOSIZIONE, INVIO & SCHEDULAZIONE
# ============================================================================

def save_draft(data, draft_id=None, prov=None):
    """Salva una bozza nella cartella 'drafts'."""
    p = _get_prov(prov)
    conn = db.get_conn()
    now = _now_ts()
    
    to_addrs = data.get('to', [])
    if isinstance(to_addrs, str):
        to_addrs = [a.strip() for a in re.split(r'[,;]', to_addrs) if a.strip()]
        
    cc_addrs = data.get('cc', [])
    if isinstance(cc_addrs, str):
        cc_addrs = [a.strip() for a in re.split(r'[,;]', cc_addrs) if a.strip()]
        
    bcc_addrs = data.get('bcc', [])
    if isinstance(bcc_addrs, str):
        bcc_addrs = [a.strip() for a in re.split(r'[,;]', bcc_addrs) if a.strip()]
        
    subject = data.get('subject', '') or '(Nessun oggetto)'
    body_html = data.get('body_html', '')
    body_text = data.get('body_text', '') or _extract_plain_snippet(body_html, 1000)
    snippet = _extract_plain_snippet(body_text or body_html)
    category_id = data.get('category_id')
    attachments = data.get('attachments', [])
    
    smtp_settings = load_smtp_settings(p)
    from_addr = smtp_settings.get('from_email') or smtp_settings.get('username') or f"tecnico_{p}@algecopower.it"
    from_name = smtp_settings.get('from_name') or "Algeco Power"
    
    if not category_id:
        auto_info = auto_categorize_email(from_addr, to_addrs, subject, body_text, prov=p)
        category_id = auto_info['category_id']
        linked_pod = data.get('linked_pod') or auto_info['linked_pod']
        linked_comune = data.get('linked_comune') or auto_info['linked_comune']
        linked_azienda = data.get('linked_azienda') or auto_info['linked_azienda']
    else:
        linked_pod = data.get('linked_pod')
        linked_comune = data.get('linked_comune')
        linked_azienda = data.get('linked_azienda')

    if draft_id:
        conn.execute(
            """UPDATE mail_messages SET
               subject = ?, body_text = ?, body_html = ?, snippet = ?,
               to_addrs = ?, cc_addrs = ?, bcc_addrs = ?, category_id = ?,
               attachments_json = ?, linked_pod = ?, linked_comune = ?, linked_azienda = ?,
               updated_ts = ?
               WHERE id = ? AND provincia = ?""",
            (
                subject, body_text, body_html, snippet,
                json.dumps(to_addrs), json.dumps(cc_addrs), json.dumps(bcc_addrs),
                category_id, json.dumps(attachments),
                linked_pod, linked_comune, linked_azienda,
                now, int(draft_id), p
            )
        )
        return {'success': True, 'id': int(draft_id), 'is_draft': True}
    else:
        cur = conn.execute(
            """INSERT INTO mail_messages (
                provincia, folder, from_addr, from_name, to_addrs, cc_addrs, bcc_addrs,
                subject, body_text, body_html, snippet, category_id, is_read,
                status, attachments_json, linked_pod, linked_comune, linked_azienda,
                created_ts, updated_ts
            ) VALUES (?, 'drafts', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'draft', ?, ?, ?, ?, ?, ?)""",
            (
                p, from_addr, from_name,
                json.dumps(to_addrs), json.dumps(cc_addrs), json.dumps(bcc_addrs),
                subject, body_text, body_html, snippet, category_id,
                json.dumps(attachments), linked_pod, linked_comune, linked_azienda,
                now, now
            )
        )
        return {'success': True, 'id': cur.lastrowid, 'is_draft': True}


def build_mime_message(from_addr, from_name, to_addrs, cc_addrs=None, bcc_addrs=None,
                       subject='', body_html='', body_text='', attachments=None,
                       reply_to=None, signature_html=None):
    """
    Costruisce un oggetto email.mime.multipart.MIMEMultipart standard e compatibile.
    """
    msg = MIMEMultipart('mixed')
    
    if from_name:
        msg['From'] = formataddr((from_name, from_addr))
    else:
        msg['From'] = from_addr
        
    msg['To'] = ', '.join(to_addrs) if isinstance(to_addrs, list) else str(to_addrs)
    if cc_addrs:
        msg['Cc'] = ', '.join(cc_addrs) if isinstance(cc_addrs, list) else str(cc_addrs)
    if reply_to:
        msg['Reply-To'] = reply_to
    elif from_addr:
        msg['Reply-To'] = from_addr
        
    msg['Subject'] = subject
    msg['Date'] = email.utils.formatdate(localtime=True)
    msg['Message-ID'] = email.utils.make_msgid(domain='algecopower.it')
    
    body_alt = MIMEMultipart('alternative')
    
    full_html = body_html or ''
    if signature_html and signature_html not in full_html:
        full_html = f"{full_html}<br><br>{signature_html}"
        
    plain_text = body_text or _extract_plain_snippet(full_html, 5000)
    
    part_text = MIMEText(plain_text, 'plain', 'utf-8')
    part_html = MIMEText(full_html, 'html', 'utf-8')
    body_alt.attach(part_text)
    body_alt.attach(part_html)
    
    msg.attach(body_alt)
    
    if attachments:
        for att in attachments:
            file_path = att.get('file_path')
            file_name = att.get('filename') or (os.path.basename(file_path) if file_path else 'allegato')
            
            if not file_path and att.get('standard_name'):
                std_path = os.path.join(_ALLEGATI_DIR, att['standard_name'])
                if os.path.isfile(std_path):
                    file_path = std_path
                    file_name = att.get('filename') or att['standard_name']
                    
            if file_path and os.path.isfile(file_path):
                ctype, encoding = mimetypes.guess_type(file_path)
                if ctype is None or encoding is not None:
                    ctype = 'application/octet-stream'
                maintype, subtype = ctype.split('/', 1)
                
                with open(file_path, 'rb') as f:
                    file_data = f.read()
                    
                part = MIMEApplication(file_data, _subtype=subtype)
                part.add_header('Content-Disposition', 'attachment', filename=file_name)
                msg.attach(part)
                
    return msg


def send_or_schedule_mail(data, user='', prov=None):
    """
    Invia subito o programma l'invio di un'email.
    """
    p = _get_prov(prov)
    conn = db.get_conn()
    now = _now_ts()
    
    to_addrs = data.get('to', [])
    if isinstance(to_addrs, str):
        to_addrs = [a.strip() for a in re.split(r'[,;]', to_addrs) if a.strip()]
        
    if not to_addrs:
        raise ValueError("Specificare almeno un destinatario valido.")
        
    cc_addrs = data.get('cc', [])
    if isinstance(cc_addrs, str):
        cc_addrs = [a.strip() for a in re.split(r'[,;]', cc_addrs) if a.strip()]
        
    bcc_addrs = data.get('bcc', [])
    if isinstance(bcc_addrs, str):
        bcc_addrs = [a.strip() for a in re.split(r'[,;]', bcc_addrs) if a.strip()]
        
    subject = (data.get('subject') or '').strip() or '(Nessun oggetto)'
    body_html = data.get('body_html', '')
    body_text = data.get('body_text', '') or _extract_plain_snippet(body_html, 2000)
    snippet = _extract_plain_snippet(body_text or body_html)
    category_id = data.get('category_id')
    attachments = data.get('attachments', [])
    scheduled_at = data.get('scheduled_at')
    linked_pod = data.get('linked_pod')
    linked_comune = data.get('linked_comune')
    linked_azienda = data.get('linked_azienda')
    add_filone_note = bool(data.get('add_filone_note'))
    draft_id = data.get('draft_id')
    
    if not category_id:
        auto_info = auto_categorize_email('', to_addrs, subject, body_text, prov=p)
        category_id = auto_info['category_id']
        if not linked_pod:
            linked_pod = auto_info['linked_pod']
        if not linked_comune:
            linked_comune = auto_info['linked_comune']
        if not linked_azienda:
            linked_azienda = auto_info['linked_azienda']
            
    smtp_settings = load_smtp_settings(p)
    from_addr = smtp_settings.get('from_email') or smtp_settings.get('username') or f"tecnico_{p}@algecopower.it"
    from_name = smtp_settings.get('from_name') or "Algeco Power"
    
    is_scheduled = False
    scheduled_ts = None
    if scheduled_at:
        if isinstance(scheduled_at, (int, float)):
            scheduled_ts = int(scheduled_at)
        else:
            try:
                dt = datetime.fromisoformat(str(scheduled_at).replace('Z', '+00:00'))
                scheduled_ts = int(dt.timestamp())
            except Exception:
                try:
                    dt = datetime.strptime(str(scheduled_at), '%Y-%m-%d %H:%M')
                    scheduled_ts = int(dt.timestamp())
                except Exception:
                    pass
                    
        if scheduled_ts and scheduled_ts > now + 30:
            is_scheduled = True

    if is_scheduled:
        if draft_id:
            conn.execute(
                """UPDATE mail_messages SET
                   folder = 'scheduled', status = 'scheduled',
                   from_addr = ?, from_name = ?, to_addrs = ?, cc_addrs = ?, bcc_addrs = ?,
                   subject = ?, body_text = ?, body_html = ?, snippet = ?, category_id = ?,
                   is_scheduled = 1, scheduled_ts = ?,
                   attachments_json = ?, linked_pod = ?, linked_comune = ?, linked_azienda = ?,
                   updated_ts = ?
                   WHERE id = ? AND provincia = ?""",
                (
                    from_addr, from_name, json.dumps(to_addrs), json.dumps(cc_addrs), json.dumps(bcc_addrs),
                    subject, body_text, body_html, snippet, category_id,
                    scheduled_ts, json.dumps(attachments),
                    linked_pod, linked_comune, linked_azienda,
                    now, int(draft_id), p
                )
            )
            mail_id = int(draft_id)
        else:
            cur = conn.execute(
                """INSERT INTO mail_messages (
                    provincia, folder, from_addr, from_name, to_addrs, cc_addrs, bcc_addrs,
                    subject, body_text, body_html, snippet, category_id, is_read,
                    is_scheduled, scheduled_ts, status, attachments_json,
                    linked_pod, linked_comune, linked_azienda,
                    created_ts, updated_ts
                ) VALUES (?, 'scheduled', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?, 'scheduled', ?, ?, ?, ?, ?, ?)""",
                (
                    p, from_addr, from_name, json.dumps(to_addrs), json.dumps(cc_addrs), json.dumps(bcc_addrs),
                    subject, body_text, body_html, snippet, category_id,
                    scheduled_ts, json.dumps(attachments),
                    linked_pod, linked_comune, linked_azienda,
                    now, now
                )
            )
            mail_id = cur.lastrowid
            
        logger.info(f"[MAIL] Email programmata con successo (ID: {mail_id}, timestamp: {scheduled_ts})")
        return {
            'success': True,
            'mail_id': mail_id,
            'scheduled': True,
            'scheduled_ts': scheduled_ts,
            'display_date': _format_display_date(scheduled_ts)
        }

    signature_html = None
    try:
        tpl_data = json_read(os.path.join(_ROOT_DIR, 'data', 'distinte_templates.json'), default={})
        raw_sig = tpl_data.get('settings', {}).get('firma_html')
        if raw_sig:
            signature_html = resolve_signature_html(raw_sig, prov=p, email_tecnico=from_addr)
    except Exception:
        pass
        
    mime_msg = build_mime_message(
        from_addr=from_addr,
        from_name=from_name,
        to_addrs=to_addrs,
        cc_addrs=cc_addrs,
        bcc_addrs=bcc_addrs,
        subject=subject,
        body_html=body_html,
        body_text=body_text,
        attachments=attachments,
        signature_html=signature_html
    )
    
    ok, err = send_smtp_message(smtp_settings, mime_msg, max_retries=3, save_sent=True)
    if not ok:
        logger.error(f"[MAIL] Invio SMTP fallito: {err}")
        if draft_id:
            conn.execute(
                """UPDATE mail_messages SET
                   status = 'failed', error_msg = ?, updated_ts = ?
                   WHERE id = ? AND provincia = ?""",
                (err, now, int(draft_id), p)
            )
        raise RuntimeError(f"Invio email fallito: {err}")
        
    msg_id_header = mime_msg.get('Message-ID', '')
    if draft_id:
        conn.execute(
            """UPDATE mail_messages SET
               folder = 'sent', status = 'sent', message_id = ?,
               from_addr = ?, from_name = ?, to_addrs = ?, cc_addrs = ?, bcc_addrs = ?,
               subject = ?, body_text = ?, body_html = ?, snippet = ?, category_id = ?,
               sent_ts = ?, error_msg = NULL,
               attachments_json = ?, linked_pod = ?, linked_comune = ?, linked_azienda = ?,
               updated_ts = ?
               WHERE id = ? AND provincia = ?""",
            (
                msg_id_header, from_addr, from_name,
                json.dumps(to_addrs), json.dumps(cc_addrs), json.dumps(bcc_addrs),
                subject, body_text, body_html, snippet, category_id,
                now, json.dumps(attachments),
                linked_pod, linked_comune, linked_azienda,
                now, int(draft_id), p
            )
        )
        mail_id = int(draft_id)
    else:
        cur = conn.execute(
            """INSERT INTO mail_messages (
                provincia, folder, message_id, from_addr, from_name, to_addrs, cc_addrs, bcc_addrs,
                subject, body_text, body_html, snippet, category_id, is_read,
                sent_ts, status, attachments_json, linked_pod, linked_comune, linked_azienda,
                created_ts, updated_ts
            ) VALUES (?, 'sent', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, 'sent', ?, ?, ?, ?, ?, ?)""",
            (
                p, msg_id_header, from_addr, from_name,
                json.dumps(to_addrs), json.dumps(cc_addrs), json.dumps(bcc_addrs),
                subject, body_text, body_html, snippet, category_id,
                now, json.dumps(attachments),
                linked_pod, linked_comune, linked_azienda,
                now, now
            )
        )
        mail_id = cur.lastrowid

    if add_filone_note and linked_pod:
        try:
            today_str = datetime.now().strftime('%d/%m')
            nota = f"{today_str} inviata mail: {subject[:40]}"
            registra_nota_su_db(linked_pod, nota, user=user or 'webmail', origin='gestione_mail')
        except Exception as e:
            logger.warning(f"[MAIL] Errore salvataggio nota su filone: {e}")

    logger.info(f"[MAIL] ✅ Email inviata con successo (ID: {mail_id}) a {to_addrs}")
    return {'success': True, 'mail_id': mail_id, 'status': 'sent'}


def process_scheduled_queue(prov=None):
    """
    Worker per processare e inviare le email programmate il cui orario è scaduto.
    """
    p = _get_prov(prov)
    conn = db.get_conn()
    now = _now_ts()
    
    rows = conn.execute(
        """SELECT * FROM mail_messages 
           WHERE provincia = ? AND folder = 'scheduled' AND status = 'scheduled' AND scheduled_ts <= ?""",
        (p, now)
    ).fetchall()
    
    if not rows:
        return {'processed': 0, 'sent': 0, 'failed': 0}
        
    logger.info(f"[MAIL-WORKER] Trovate {len(rows)} email programmate da inviare per {p}")
    smtp_settings = load_smtp_settings(p)
    sent_count = 0
    fail_count = 0
    
    for r in rows:
        mid = r['id']
        try:
            to_addrs = json.loads(r['to_addrs']) if r['to_addrs'] else []
            cc_addrs = json.loads(r['cc_addrs']) if r['cc_addrs'] else []
            bcc_addrs = json.loads(r['bcc_addrs']) if r['bcc_addrs'] else []
            attachments = json.loads(r['attachments_json']) if r['attachments_json'] else []
            
            mime_msg = build_mime_message(
                from_addr=r['from_addr'],
                from_name=r['from_name'],
                to_addrs=to_addrs,
                cc_addrs=cc_addrs,
                bcc_addrs=bcc_addrs,
                subject=r['subject'],
                body_html=r['body_html'] or '',
                body_text=r['body_text'] or '',
                attachments=attachments
            )
            
            ok, err = send_smtp_message(smtp_settings, mime_msg, max_retries=2, save_sent=True)
            if ok:
                conn.execute(
                    """UPDATE mail_messages 
                       SET folder = 'sent', status = 'sent', sent_ts = ?, message_id = ?, updated_ts = ? 
                       WHERE id = ?""",
                    (now, mime_msg.get('Message-ID', ''), now, mid)
                )
                sent_count += 1
                logger.info(f"[MAIL-WORKER] ✅ Email programmata {mid} inviata con successo")
            else:
                conn.execute(
                    """UPDATE mail_messages 
                       SET status = 'failed', error_msg = ?, updated_ts = ? 
                       WHERE id = ?""",
                    (err or 'Errore invio', now, mid)
                )
                fail_count += 1
                logger.error(f"[MAIL-WORKER] ❌ Invio email programmata {mid} fallito: {err}")
        except Exception as e:
            conn.execute(
                "UPDATE mail_messages SET status = 'failed', error_msg = ?, updated_ts = ? WHERE id = ?",
                (str(e), now, mid)
            )
            fail_count += 1
            logger.exception(f"[MAIL-WORKER] Eccezione processando email programmata {mid}")
            
    return {'processed': len(rows), 'sent': sent_count, 'failed': fail_count}


# ============================================================================
# SINCRONIZZAZIONE IMAP (RICEZIONE POSTA)
# ============================================================================

def sync_imap_inbox(max_messages=150, prov=None):
    """
    Sincronizza i messaggi dalla casella IMAP collegata e li importa in SQLite in modo ottimizzato.
    Se max_messages == 0, sincronizza l'intero storico della casella.
    Esegue prima un fetch leggero degli header (Message-ID) per saltare istantaneamente
    le email già archiviate in locale senza riscaricare corpi o allegati pesanti.
    """
    p = _get_prov(prov)
    smtp_settings = load_smtp_settings(p)
    
    host = smtp_settings.get('host', '')
    username = smtp_settings.get('username', '')
    password = smtp_settings.get('password', '')
    
    if not host or not username or not password:
        return {'success': False, 'error': f'Credenziali email non configurate per la provincia {p}.'}
        
    imap_host = smtp_settings.get('imap_host', '')
    if not imap_host:
        imap_host = host.replace('smtps.', 'imaps.').replace('smtp.', 'imap.')
    imap_port = int(smtp_settings.get('imap_port', 993))
    
    conn = db.get_conn()
    new_count = 0
    
    # Pre-carica tutti i Message-ID già presenti nel DB per questa provincia
    existing_msg_ids = set()
    for row in conn.execute("SELECT message_id FROM mail_messages WHERE provincia = ? AND message_id IS NOT NULL", (p,)).fetchall():
        if row[0]:
            existing_msg_ids.add(row[0].strip())
            
    try:
        imap = imaplib.IMAP4_SSL(imap_host, imap_port, timeout=40)
        imap.login(username, password)
        status, _ = imap.select('INBOX', readonly=True)
        if status != 'OK':
            imap.logout()
            return {'success': False, 'error': "Impossibile aprire la cartella INBOX."}
            
        status, search_data = imap.search(None, 'ALL')
        if status != 'OK' or not search_data or not search_data[0]:
            imap.logout()
            return {'success': True, 'new_messages': 0, 'total_inbox': 0, 'message': 'Nessun messaggio presente nella casella.'}
            
        msg_ids = search_data[0].split()
        total_inbox = len(msg_ids)
        
        if max_messages and max_messages > 0:
            limit_to_fetch = min(max_messages, total_inbox)
            target_ids = msg_ids[-limit_to_fetch:]
        else:
            # max_messages == 0 -> Sincronizza tutto lo storico
            target_ids = msg_ids[:]
            
        target_ids.reverse()
        att_base_dir = _get_attachments_dir(p)
        
        for mid in target_ids:
            try:
                # 1. Fetch ultrarapido dei soli header essenziali per verificare duplicati
                try:
                    res_hdr, data_hdr = imap.fetch(mid, '(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID DATE FROM SUBJECT)])')
                    if res_hdr == 'OK' and data_hdr and data_hdr[0] and isinstance(data_hdr[0], tuple):
                        hdr_msg = email.message_from_bytes(data_hdr[0][1])
                        mid_header = _decode_header_val(hdr_msg.get('Message-ID', '')).strip()
                        if mid_header and mid_header in existing_msg_ids:
                            # Già archiviato in SQLite: salta download corpo/allegati
                            continue
                except Exception:
                    pass

                # 2. Download completo del messaggio solo per i nuovi
                res, data = imap.fetch(mid, '(BODY.PEEK[])')
                if res != 'OK' or not data or not data[0] or not isinstance(data[0], tuple):
                    res, data = imap.fetch(mid, '(RFC822)')
                    if res != 'OK' or not data or not data[0] or not isinstance(data[0], tuple):
                        continue
                    
                raw_bytes = data[0][1]
                parsed_msg = email.message_from_bytes(raw_bytes)
                
                message_id = _decode_header_val(parsed_msg.get('Message-ID', '')).strip()
                subject = _decode_header_val(parsed_msg.get('Subject', '')) or '(Nessun oggetto)'
                from_raw = _decode_header_val(parsed_msg.get('From', ''))
                from_name, from_addr = parseaddr(from_raw)
                to_raw = _decode_header_val(parsed_msg.get('To', ''))
                to_addrs = [parseaddr(a)[1] for a in re.split(r'[,;]', to_raw) if parseaddr(a)[1]]
                cc_raw = _decode_header_val(parsed_msg.get('Cc', ''))
                cc_addrs = [parseaddr(a)[1] for a in re.split(r'[,;]', cc_raw) if parseaddr(a)[1]]
                
                date_raw = parsed_msg.get('Date')
                received_ts = _now_ts()
                if date_raw:
                    try:
                        dt = email.utils.parsedate_to_datetime(date_raw)
                        received_ts = int(dt.timestamp())
                    except Exception:
                        pass
                        
                if message_id and message_id in existing_msg_ids:
                    continue
                elif not message_id:
                    existing = conn.execute(
                        "SELECT id FROM mail_messages WHERE provincia = ? AND from_addr = ? AND subject = ? AND received_ts = ?",
                        (p, from_addr, subject, received_ts)
                    ).fetchone()
                    if existing:
                        continue

                body_html = ''
                body_text = ''
                attachments = []
                
                if parsed_msg.is_multipart():
                    for part in parsed_msg.walk():
                        content_type = part.get_content_type()
                        disposition = str(part.get('Content-Disposition') or '')
                        content_id = str(part.get('Content-ID') or '').strip('<> ')
                        
                        if 'attachment' in disposition or part.get_filename() or content_id:
                            fn = _decode_header_val(part.get_filename() or f"allegato_{len(attachments)+1}")
                            payload = part.get_payload(decode=True)
                            if payload:
                                safe_fn = re.sub(r'[^\w\.\-]', '_', fn)
                                stored_name = f"{int(time.time())}_{uuid.uuid4().hex[:6]}_{safe_fn}"
                                stored_path = os.path.join(att_base_dir, stored_name)
                                with open(stored_path, 'wb') as af:
                                    af.write(payload)
                                attachments.append({
                                    'filename': fn,
                                    'file_path': stored_path,
                                    'size_bytes': len(payload),
                                    'content_type': content_type,
                                    'content_id': content_id
                                })
                        elif content_type == 'text/html' and not body_html:
                            payload = part.get_payload(decode=True)
                            if payload:
                                charset = part.get_content_charset() or 'utf-8'
                                try:
                                    body_html = payload.decode(charset, errors='replace')
                                except Exception:
                                    body_html = payload.decode('utf-8', errors='replace')
                        elif content_type == 'text/plain' and not body_text:
                            payload = part.get_payload(decode=True)
                            if payload:
                                charset = part.get_content_charset() or 'utf-8'
                                try:
                                    body_text = payload.decode(charset, errors='replace')
                                except Exception:
                                    body_text = payload.decode('utf-8', errors='replace')
                else:
                    content_type = parsed_msg.get_content_type()
                    payload = parsed_msg.get_payload(decode=True)
                    if payload:
                        charset = parsed_msg.get_content_charset() or 'utf-8'
                        text = payload.decode(charset, errors='replace')
                        if content_type == 'text/html':
                            body_html = text
                        else:
                            body_text = text

                snippet = _extract_plain_snippet(body_text or body_html)
                auto_info = auto_categorize_email(from_addr, to_addrs, subject, body_text or snippet, prov=p)
                
                target_prov = _detect_email_province(from_addr, to_addrs, cc_addrs, default_prov=p)
                
                # Se l'email è indirizzata alla casella di un'altra provincia, salvala nel rispettivo DB
                if target_prov != p:
                    target_db_path = os.path.join(_ROOT_DIR, 'data', target_prov, 'state.db')
                    if os.path.exists(target_db_path):
                        target_conn = sqlite3.connect(target_db_path)
                    else:
                        target_conn = conn
                        target_prov = p
                else:
                    target_conn = conn

                mid_key = message_id or f"imap-{mid}-{received_ts}"
                now = _now_ts()
                with target_conn:
                    target_conn.execute(
                        """INSERT INTO mail_messages (
                            provincia, folder, message_id, from_addr, from_name, to_addrs, cc_addrs,
                            subject, body_text, body_html, snippet, category_id, is_read,
                            received_ts, status, attachments_json, linked_pod, linked_comune, linked_azienda,
                            created_ts, updated_ts
                        ) VALUES (?, 'inbox', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'received', ?, ?, ?, ?, ?, ?)""",
                        (
                            target_prov, mid_key,
                            from_addr, from_name, json.dumps(to_addrs), json.dumps(cc_addrs),
                            subject, body_text, body_html, snippet, auto_info['category_id'],
                            received_ts, json.dumps(attachments),
                            auto_info['linked_pod'], auto_info['linked_comune'], auto_info['linked_azienda'],
                            now, now
                        )
                    )
                if target_conn != conn:
                    target_conn.close()

                if message_id:
                    existing_msg_ids.add(message_id)
                new_count += 1
            except Exception as e:
                logger.warning(f"[IMAP] Errore parsing messaggio {mid}: {e}")
                continue
                
        imap.logout()
        logger.info(f"[IMAP] Sincronizzazione completata: {new_count} nuove email importate (su {total_inbox} totali in casella).")
        return {'success': True, 'new_messages': new_count, 'total_inbox': total_inbox}
    except Exception as e:
        logger.error(f"[IMAP] Errore connessione o sincronizzazione IMAP: {e}")
        return {'success': False, 'error': f"Errore IMAP: {str(e)}"}


# ============================================================================
# GESTIONE TEMPLATE & SNIPPET (CENTRALIZZATI & CONDIVISI)
# ============================================================================

def get_template_settings(prov=None):
    """Ritorna le impostazioni globali dei template con province sincronizzate da data/provinces.json."""
    p = _get_prov(prov)
    prov_file = os.path.join(_ROOT_DIR, 'data', p, 'distinte_templates.json')
    base_file = os.path.join(_ROOT_DIR, 'data', 'distinte_templates.json')
    target_file = prov_file if os.path.exists(prov_file) else base_file
    tpl_data = json_read(target_file, default={})
    settings = tpl_data.get('settings', {})
    
    # 1. Carica le province ufficiali registrate in data/provinces.json
    provs_path = os.path.join(_ROOT_DIR, 'data', 'provinces.json')
    sys_provs = json_read(provs_path, default={}) or {}
    
    existing_prov_settings = settings.get('province', {}) if isinstance(settings.get('province'), dict) else {}
    
    reconciled_provinces = {}
    for prov_key, pdata in sys_provs.items():
        pk = str(prov_key).upper()
        name = pdata.get('nome') or pk.capitalize()
        saved = existing_prov_settings.get(pk, {})
        reconciled_provinces[pk] = {
            'nome': saved.get('nome') or name,
            'email': saved.get('email') or settings.get(f'email_{pk.lower()}') or f"tecnico_{pk.lower()}@algecopower.it",
            'telefono': saved.get('telefono') or '011 025 2702'
        }
        
    settings['province'] = reconciled_provinces
    return settings


def resolve_signature_html(raw_signature, prov=None, email_tecnico=None, telefono_tecnico=None):
    """Risolve i segnaposto dinamici nella firma HTML ({email_tecnico}, {telefono_tecnico}, ecc.)."""
    if not raw_signature:
        return ""
    p = _get_prov(prov)
    settings = get_template_settings(p)
    prov_settings = settings.get('province', {}).get(p.upper(), {})

    email = email_tecnico or prov_settings.get('email') or settings.get(f'email_{p.lower()}') or f"tecnico_{p.lower()}@algecopower.it"
    tel = telefono_tecnico or prov_settings.get('telefono') or '011 025 2702'
    tel_clean = re.sub(r'[^0-9+]', '', str(tel))

    sig = str(raw_signature)
    sig = sig.replace('{email_tecnico}', str(email))
    sig = sig.replace('{telefono_tecnico}', str(tel))
    sig = sig.replace('{telefono_ufficio}', str(tel))
    sig = sig.replace('{telefono_tecnico_clean}', tel_clean)
    return sig


# ============================================================================
# CATEGORIE AD HOC
# ============================================================================

def list_categories(prov=None):
    """Ritorna l'elenco delle categorie configurate per la provincia."""
    p = _get_prov(prov)
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT * FROM mail_categories WHERE provincia = ? ORDER BY sort_order ASC, name ASC",
        (p,)
    ).fetchall()
    
    cats = []
    for r in rows:
        d = dict(r)
        try:
            d['keywords'] = json.loads(d['keywords_json']) if d['keywords_json'] else []
        except Exception:
            d['keywords'] = []
        cats.append(d)
    return cats


def save_category(data, prov=None):
    """Crea o modifica una categoria di email (incluse categorie di sistema)."""
    p = _get_prov(prov)
    conn = db.get_conn()
    
    cat_id = data.get('id')
    name = (data.get('name') or '').strip()
    if not name:
        raise ValueError("Il nome della categoria è obbligatorio.")
        
    if not cat_id:
        cat_id = re.sub(r'[^\w]', '_', name.lower())
        
    color = data.get('color') or '#3b82f6'
    icon = data.get('icon') or 'tag'
    badge_bg = data.get('badge_bg') or 'rgba(59,130,246,0.15)'
    badge_text = data.get('badge_text') or color
    keywords = data.get('keywords', [])
    if isinstance(keywords, str):
        keywords = [k.strip().lower() for k in keywords.split(',') if k.strip()]
    elif isinstance(keywords, list):
        keywords = [str(k).strip().lower() for k in keywords if str(k).strip()]
        
    cur = conn.execute("SELECT is_system, sort_order FROM mail_categories WHERE id = ? AND provincia = ?", (cat_id, p))
    existing = cur.fetchone()
    
    is_system = existing['is_system'] if existing else int(data.get('is_system', 0))
    sort_order = existing['sort_order'] if existing else int(data.get('sort_order', 10))
    
    conn.execute(
        """INSERT OR REPLACE INTO mail_categories (
            id, provincia, name, color, icon, badge_bg, badge_text, keywords_json, sort_order, is_system
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (cat_id, p, name, color, icon, badge_bg, badge_text, json.dumps(keywords), sort_order, is_system)
    )
    return {'success': True, 'id': cat_id}


def delete_category(cat_id, prov=None):
    """Elimina una categoria non di sistema."""
    p = _get_prov(prov)
    conn = db.get_conn()
    
    cur = conn.execute("SELECT is_system FROM mail_categories WHERE id = ? AND provincia = ?", (cat_id, p))
    row = cur.fetchone()
    if not row:
        return False
    if row['is_system']:
        raise ValueError("Non è possibile eliminare una categoria di sistema predefinita.")
        
    conn.execute("UPDATE mail_messages SET category_id = NULL WHERE category_id = ? AND provincia = ?", (cat_id, p))
    conn.execute("DELETE FROM mail_categories WHERE id = ? AND provincia = ?", (cat_id, p))
    return True


# ============================================================================
# TEMPLATE & FRASARIO ("SPIACCICA TEMPLATE")
# ============================================================================

def list_templates_and_snippets(prov=None):
    """
    Ritorna sia i template/snippet salvati in DB che quelli globali da distinte_templates.json.
    """
    p = _get_prov(prov)
    conn = db.get_conn()
    
    rows = conn.execute(
        "SELECT * FROM mail_templates_snippets WHERE provincia = ? ORDER BY type ASC, title ASC",
        (p,)
    ).fetchall()
    
    items = []
    for r in rows:
        d = dict(r)
        try:
            d['variables'] = json.loads(d['variables_json']) if d['variables_json'] else []
        except Exception:
            d['variables'] = []
        items.append(d)
        
    try:
        prov_tpl_file = os.path.join(_ROOT_DIR, 'data', p, 'distinte_templates.json')
        tpl_path = prov_tpl_file if os.path.exists(prov_tpl_file) else os.path.join(_ROOT_DIR, 'data', 'distinte_templates.json')
        data = json_read(tpl_path, default={})
        
        # 1. Carica templates completi
        for k, v in data.get('templates', {}).items():
            label = v.get('label') or v.get('name') or k.replace('_', ' ').title()
            if not any(i['title'] == label or i.get('key') == k for i in items):
                items.append({
                    'id': f"sys_{k}",
                    'key': k,
                    'provincia': p,
                    'title': label,
                    'category': v.get('category') or ('whatsapp' if v.get('type') == 'whatsapp' else 'distinte'),
                    'type': 'template',
                    'subject': v.get('subject', ''),
                    'body': v.get('body', ''),
                    'info': v.get('info', ''),
                    'icon': v.get('icon', 'fas fa-envelope'),
                    'conditionals': v.get('conditionals', {}),
                    'variables': v.get('fields', []),
                    'is_system': True
                })
                
        # 2. Carica frasario / snippets
        for k, v in data.get('snippets', {}).items():
            title = v.get('title') or k.replace('_', ' ').title()
            if not any(i['title'] == title for i in items):
                items.append({
                    'id': f"snip_{k}",
                    'key': k,
                    'provincia': p,
                    'title': title,
                    'category': 'frasario',
                    'type': 'snippet',
                    'body': v.get('body', ''),
                    'variables': [],
                    'is_system': True
                })
    except Exception as e:
        logger.error(f"Errore caricamento template centralizzati da JSON: {e}")
        
    return items


def save_template_or_snippet(data, prov=None):
    """Crea o aggiorna un template o uno snippet di frase (sia su DB che su distinte_templates.json)."""
    p = _get_prov(prov)
    conn = db.get_conn()
    now = _now_ts()
    
    item_id = data.get('id')
    title = (data.get('title') or '').strip()
    if not title:
        raise ValueError("Il titolo è obbligatorio.")
        
    category = data.get('category') or 'generale'
    item_type = data.get('type') or 'template'
    subject = data.get('subject', '')
    body = data.get('body', '')
    variables = data.get('variables', [])
    if isinstance(variables, str):
        variables = [v.strip() for v in variables.split(',') if v.strip()]
        
    if not variables and body:
        found = re.findall(r'\{([a-zA-Z0-9_]+)\}', f"{subject} {body}")
        variables = list(set(found))

    # Se il template o snippet è WhatsApp o frase rapida, normalizza il body rimuovendo tag HTML (<br>, <div>, <p>)
    is_wa_or_snippet = (
        category == 'whatsapp' or
        item_type == 'snippet' or
        (item_id and (str(item_id).startswith('snip_') or str(item_id).startswith('sys_wa_')))
    )
    if is_wa_or_snippet and ('<' in body and '>' in body):
        body = html_to_plain_text(body)

    # Aggiornamento template centralizzato di sistema
    if item_id and str(item_id).startswith('sys_'):
        k = str(item_id)[4:]
        prov_file = os.path.join(_ROOT_DIR, 'data', p, 'distinte_templates.json')
        base_file = os.path.join(_ROOT_DIR, 'data', 'distinte_templates.json')
        target_file = prov_file if os.path.exists(prov_file) else base_file
        tpl_data = json_read(target_file, default={})
        if 'templates' not in tpl_data:
            tpl_data['templates'] = {}
        if k not in tpl_data['templates']:
            tpl_data['templates'][k] = {}
        tpl_data['templates'][k]['label'] = title
        if subject:
            tpl_data['templates'][k]['subject'] = subject
        # Se è template WhatsApp, garantisci che non contenga tag HTML
        if k.startswith('wa_') or tpl_data['templates'][k].get('type') == 'whatsapp':
            body = html_to_plain_text(body)
        tpl_data['templates'][k]['body'] = body
        if variables:
            tpl_data['templates'][k]['fields'] = variables
        os.makedirs(os.path.dirname(prov_file), exist_ok=True)
        json_write(prov_file, tpl_data, indent=4)
        return {'success': True, 'id': item_id}

    # Aggiornamento snippet centralizzato di sistema
    if item_id and str(item_id).startswith('snip_'):
        k = str(item_id)[5:]
        prov_file = os.path.join(_ROOT_DIR, 'data', p, 'distinte_templates.json')
        base_file = os.path.join(_ROOT_DIR, 'data', 'distinte_templates.json')
        target_file = prov_file if os.path.exists(prov_file) else base_file
        tpl_data = json_read(target_file, default={})
        if 'snippets' not in tpl_data:
            tpl_data['snippets'] = {}
        if k not in tpl_data['snippets']:
            tpl_data['snippets'][k] = {}
        tpl_data['snippets'][k]['title'] = title
        tpl_data['snippets'][k]['body'] = html_to_plain_text(body)
        os.makedirs(os.path.dirname(prov_file), exist_ok=True)
        json_write(prov_file, tpl_data, indent=4)
        return {'success': True, 'id': item_id}
        
    if item_id and str(item_id).isdigit():
        if (category == 'whatsapp' or item_type == 'snippet') and ('<' in body and '>' in body):
            body = html_to_plain_text(body)
        conn.execute(
            """UPDATE mail_templates_snippets SET
               title = ?, category = ?, type = ?, subject = ?, body = ?,
               variables_json = ?, updated_ts = ?
               WHERE id = ? AND provincia = ?""",
            (title, category, item_type, subject, body, json.dumps(variables), now, int(item_id), p)
        )
        return {'success': True, 'id': int(item_id)}
    else:
        if (category == 'whatsapp' or item_type == 'snippet') and ('<' in body and '>' in body):
            body = html_to_plain_text(body)
        cur = conn.execute(
            """INSERT INTO mail_templates_snippets (
                provincia, title, category, type, subject, body, variables_json, created_ts, updated_ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (p, title, category, item_type, subject, body, json.dumps(variables), now, now)
        )
        return {'success': True, 'id': cur.lastrowid}




def save_template_settings(settings, prov=None):
    """Salva le impostazioni globali dei template (firma_html, province dinamiche)."""
    p = _get_prov(prov)
    prov_file = os.path.join(_ROOT_DIR, 'data', p, 'distinte_templates.json')
    base_file = os.path.join(_ROOT_DIR, 'data', 'distinte_templates.json')

    # Aggiorna sempre il file base globale (fonte centralizzata condivisa tra tutte le province)
    base_data = json_read(base_file, default={})
    if 'settings' not in base_data:
        base_data['settings'] = {}
    base_data['settings'].update(settings)

    # Mantieni retrocompatibilità per email_{prov}
    if 'province' in settings and isinstance(settings['province'], dict):
        for pk, pval in settings['province'].items():
            if isinstance(pval, dict) and pval.get('email'):
                base_data['settings'][f'email_{pk.lower()}'] = pval['email']

    json_write(base_file, base_data, indent=4)

    # Se esiste un file di configurazione specifico provinciale, allinealo
    if os.path.exists(prov_file):
        prov_data = json_read(prov_file, default={})
        if 'settings' not in prov_data:
            prov_data['settings'] = {}
        prov_data['settings'].update(settings)
        if 'province' in settings and isinstance(settings['province'], dict):
            for pk, pval in settings['province'].items():
                if isinstance(pval, dict) and pval.get('email'):
                    prov_data['settings'][f'email_{pk.lower()}'] = pval['email']
        json_write(prov_file, prov_data, indent=4)

    return {'success': True, 'settings': base_data['settings']}


def delete_template_or_snippet(item_id, prov=None):
    """Elimina qualsiasi template o snippet (sia di sistema in JSON che personalizzato in DB)."""
    p = _get_prov(prov)
    conn = db.get_conn()
    str_id = str(item_id)
    
    # 1. Se è un template di sistema salvato in distinte_templates.json
    if str_id.startswith('sys_'):
        k = str_id[4:]
        prov_file = os.path.join(_ROOT_DIR, 'data', p, 'distinte_templates.json')
        base_file = os.path.join(_ROOT_DIR, 'data', 'distinte_templates.json')
        target_file = prov_file if os.path.exists(prov_file) else base_file
        tpl_data = json_read(target_file, default={})
        if 'templates' in tpl_data and k in tpl_data['templates']:
            del tpl_data['templates'][k]
            os.makedirs(os.path.dirname(prov_file), exist_ok=True)
            json_write(prov_file, tpl_data, indent=4)
            return True
            
    # 2. Se è uno snippet di sistema in distinte_templates.json
    if str_id.startswith('snip_'):
        k = str_id[5:]
        prov_file = os.path.join(_ROOT_DIR, 'data', p, 'distinte_templates.json')
        base_file = os.path.join(_ROOT_DIR, 'data', 'distinte_templates.json')
        target_file = prov_file if os.path.exists(prov_file) else base_file
        tpl_data = json_read(target_file, default={})
        if 'snippets' in tpl_data and k in tpl_data['snippets']:
            del tpl_data['snippets'][k]
            os.makedirs(os.path.dirname(prov_file), exist_ok=True)
            json_write(prov_file, tpl_data, indent=4)
            return True

    # 3. Se è salvato nel DB SQLite
    if str_id.isdigit():
        conn.execute("DELETE FROM mail_templates_snippets WHERE id = ? AND provincia = ?", (int(item_id), p))
        return True
        
    # 4. Fallback: cerca per chiave diretta
    prov_file = os.path.join(_ROOT_DIR, 'data', p, 'distinte_templates.json')
    base_file = os.path.join(_ROOT_DIR, 'data', 'distinte_templates.json')
    target_file = prov_file if os.path.exists(prov_file) else base_file
    tpl_data = json_read(target_file, default={})
    if 'templates' in tpl_data and str_id in tpl_data['templates']:
        del tpl_data['templates'][str_id]
        os.makedirs(os.path.dirname(prov_file), exist_ok=True)
        json_write(prov_file, tpl_data, indent=4)
        return True
    if 'snippets' in tpl_data and str_id in tpl_data['snippets']:
        del tpl_data['snippets'][str_id]
        os.makedirs(os.path.dirname(prov_file), exist_ok=True)
        json_write(prov_file, tpl_data, indent=4)
        return True

    return False


def resolve_template_placeholders(text, variables=None, prov=None):
    """
    Risolve in modo universale tutti i segnaposto, condizionali e dettagli POD.
    Supporta {nome}, {pod}, {eneltel}, {matricola}, {indirizzo}, {comune},
    {data}, {email_tecnico}, {telefono_tecnico}, {telefono_ufficio}, {telefono_tecnico_clean},
    {frase_distacco}, {distacco_text}, {nome_operatore}, {telefono_operatore}.
    """
    if not text:
        return ''
    variables = dict(variables or {})
    p = _get_prov(prov)
    
    # 1. Se è fornito un POD ma mancano altri dati, effettua lookup automatico dal filone
    pod = (variables.get('pod') or '').strip().upper()
    if pod:
        ek = db.find_eneltel_by_pod(pod)
        if ek:
            filone_row = db.get_filone_row(ek) or {}
            if 'nome' not in variables or not variables['nome']:
                variables['nome'] = filone_row.get('nominativo') or ''
            if 'nome_societa' not in variables or not variables['nome_societa']:
                variables['nome_societa'] = filone_row.get('nominativo') or ''
            if 'indirizzo' not in variables or not variables['indirizzo']:
                variables['indirizzo'] = f"{filone_row.get('via') or ''} {filone_row.get('civico') or ''}".strip()
            if 'comune' not in variables or not variables['comune']:
                variables['comune'] = filone_row.get('comune') or ''
            if 'eneltel' not in variables or not variables['eneltel']:
                variables['eneltel'] = filone_row.get('eneltel_key') or ''
            if 'matricola' not in variables or not variables['matricola']:
                variables['matricola'] = filone_row.get('matricola') or ''
            if 'data' not in variables or not variables['data']:
                variables['data'] = filone_row.get('data_pianificata') or ''
            if 'operatore' not in variables or not variables['operatore']:
                variables['operatore'] = filone_row.get('operatore') or ''
            if 'nome_operatore' not in variables or not variables['nome_operatore']:
                variables['nome_operatore'] = filone_row.get('operatore') or ''
            if 'distacco' not in variables or not variables['distacco']:
                tipo_mis = (filone_row.get('tipo_misuratore') or '').lower()
                variables['distacco'] = 'no' if 'semidiretto' in tipo_mis else 'si'
                
    # 2. Risolvi defaults per email_tecnico, telefono_tecnico e data
    settings = get_template_settings(p)
    prov_settings = settings.get('province', {}).get(p.upper(), {})
    
    if not variables.get('email_tecnico'):
        variables['email_tecnico'] = prov_settings.get('email') or settings.get(f'email_{p.lower()}') or f"tecnico_{p}@algecopower.it"
        
    tel_tecnico = variables.get('telefono_tecnico') or variables.get('telefono_ufficio') or prov_settings.get('telefono') or '011 025 2702'
    variables['telefono_tecnico'] = tel_tecnico
    variables['telefono_ufficio'] = tel_tecnico
    variables['telefono_tecnico_clean'] = re.sub(r'[^0-9+]', '', tel_tecnico)
        
    if not variables.get('data'):
        variables['data'] = datetime.now().strftime('%d/%m/%Y')
        
    # 3. Risolvi condizionali distacco
    distacco_val = str(variables.get('distacco') or 'no').strip().lower()
    if '{frase_distacco}' in text:
        if distacco_val == 'si' or distacco_val == 'sì':
            frase = "Facciamo presente che durante l’intervento è previsto un breve distacco della fornitura elettrica."
        else:
            frase = "Precisiamo inoltre che durante l’intervento NON è previsto il distacco della fornitura elettrica (in allegato la specifica tecnica di riferimento)."
        variables['frase_distacco'] = frase
        
    if '{distacco_text}' in text:
        if distacco_val == 'si' or distacco_val == 'sì':
            frase = "Facciamo presente che durante l’intervento è previsto un breve distacco della fornitura elettrica."
        else:
            frase = "Precisiamo inoltre che durante l’intervento NON è previsto il distacco della fornitura elettrica (in allegato la specifica tecnica di riferimento)."
        variables['distacco_text'] = frase
        
    # 4. Sostituisci tutte le chiavi
    result = text
    for k, v in variables.items():
        result = result.replace('{' + str(k) + '}', str(v or ''))
        
    return result


# ============================================================================
# AUTOCOMPLETAMENTO CONTATTI UNIFICATO
# ============================================================================

def search_contacts_autocomplete(query, limit=15, prov=None):
    """
    Cerca contatti per autocompletamento in modo rapido e unificato su:
    1. Comuni (Ufficio Tecnico, Polizia Locale, Protocollo, PEC)
    2. Aziende e referenti (Archivio Lettere)
    3. Clienti dal Filone Contatori
    4. Storico invii e ricezioni email precedenti
    """
    if not query or len(query.strip()) < 1:
        return []
        
    p = _get_prov(prov)
    conn = db.get_conn()
    q = query.strip().lower()
    results = []
    seen_emails = set()
    
    # 1. Comuni
    com_rows = conn.execute(
        """SELECT comune, email_comune, email_uff_tecnico, email_polizia, pec_comune 
           FROM comuni_info 
           WHERE provincia = ? AND (LOWER(comune) LIKE ? OR LOWER(COALESCE(email_comune,'')) LIKE ? OR LOWER(COALESCE(pec_comune,'')) LIKE ?)
           LIMIT 10""",
        (p, f"%{q}%", f"%{q}%", f"%{q}%")
    ).fetchall()
    
    for r in com_rows:
        c_name = r['comune']
        for field, label in [
            ('email_comune', 'Ufficio'),
            ('email_uff_tecnico', 'Uff. Tecnico'),
            ('email_polizia', 'Polizia Locale'),
            ('pec_comune', 'PEC')
        ]:
            em = (r[field] or '').strip()
            if em and em.lower() not in seen_emails and (q in em.lower() or q in c_name.lower()):
                seen_emails.add(em.lower())
                results.append({
                    'email': em,
                    'name': f"Comune di {c_name} ({label})",
                    'type': 'comune',
                    'detail': f"Comune di {c_name}"
                })

    # 2. Aziende (lettere_contatti)
    az_rows = conn.execute(
        """SELECT nominativo, email, pod, comune 
           FROM lettere_contatti 
           WHERE provincia = ? AND email IS NOT NULL AND email != '' 
                 AND (LOWER(nominativo) LIKE ? OR LOWER(email) LIKE ? OR LOWER(COALESCE(pod,'')) LIKE ?)
           LIMIT 15""",
        (p, f"%{q}%", f"%{q}%", f"%{q}%")
    ).fetchall()
    
    for r in az_rows:
        row_dict = dict(r)
        em_list = [e.strip() for e in re.split(r'[,;]', row_dict.get('email') or '') if '@' in e]
        for em in em_list:
            if em.lower() not in seen_emails:
                seen_emails.add(em.lower())
                comune_str = row_dict.get('comune') or ''
                pod_str = row_dict.get('pod') or ''
                detail_str = f"{comune_str} • POD: {pod_str}".strip(' •')
                results.append({
                    'email': em,
                    'name': row_dict.get('nominativo') or em,
                    'type': 'azienda',
                    'detail': detail_str
                })

    # 3. Filone Contatori
    fil_rows = conn.execute(
        """SELECT nominativo, email, pod, comune, indirizzo 
           FROM filone 
           WHERE email IS NOT NULL AND email != '' 
                 AND (LOWER(nominativo) LIKE ? OR LOWER(email) LIKE ? OR LOWER(pod) LIKE ?)
           LIMIT 15""",
        (f"%{q}%", f"%{q}%", f"%{q}%")
    ).fetchall()
    
    for r in fil_rows:
        row_dict = dict(r)
        em_list = [e.strip() for e in re.split(r'[,;]', row_dict.get('email') or '') if '@' in e]
        for em in em_list:
            if em.lower() not in seen_emails:
                seen_emails.add(em.lower())
                ind = row_dict.get('indirizzo') or ''
                com = row_dict.get('comune') or ''
                pod = row_dict.get('pod') or ''
                results.append({
                    'email': em,
                    'name': row_dict.get('nominativo') or 'Cliente Filone',
                    'type': 'cliente',
                    'detail': f"{ind} ({com}) • POD: {pod}".strip(' •')
                })

    # 4. Storico Email Recenti
    hist_rows = conn.execute(
        """SELECT from_addr, from_name, to_addrs 
           FROM mail_messages 
           WHERE provincia = ? AND (LOWER(from_addr) LIKE ? OR LOWER(from_name) LIKE ? OR LOWER(to_addrs) LIKE ?)
           ORDER BY created_ts DESC LIMIT 20""",
        (p, f"%{q}%", f"%{q}%", f"%{q}%")
    ).fetchall()
    
    for r in hist_rows:
        if r['from_addr'] and r['from_addr'].lower() not in seen_emails:
            seen_emails.add(r['from_addr'].lower())
            results.append({
                'email': r['from_addr'],
                'name': r['from_name'] or r['from_addr'],
                'type': 'storico',
                'detail': 'Contatto recente'
            })
            
    return results[:limit]


# ============================================================================
# ALLEGATI STANDARD AZIENDALI
# ============================================================================

def list_standard_attachments(prov=None):
    """Ritorna la lista dei documenti aziendali standard disponibili per gli allegati."""
    standard_files = []
    
    if os.path.exists(_ALLEGATI_DIR):
        for fname in sorted(os.listdir(_ALLEGATI_DIR)):
            fpath = os.path.join(_ALLEGATI_DIR, fname)
            if os.path.isfile(fpath) and not fname.startswith('.'):
                size = os.path.getsize(fpath)
                desc = fname
                if 'piemonte' in fname.lower():
                    desc = "Attestazione Sostituzione Piemonte (PDF)"
                elif 'lombardia' in fname.lower():
                    desc = "Attestazione Sostituzione Lombardia (PDF)"
                elif 'semidirett' in fname.lower():
                    desc = "Informativa Contatori Semidiretti (PDF)"
                    
                standard_files.append({
                    'filename': fname,
                    'standard_name': fname,
                    'description': desc,
                    'size_bytes': size,
                    'size_formatted': f"{round(size / 1024, 1)} KB" if size < 1024*1024 else f"{round(size / (1024*1024), 2)} MB"
                })
                
    return standard_files
