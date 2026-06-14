# -*- coding: utf-8 -*-
"""
Import idempotente degli invii storici dalla cartella IMAP "Inviati".

Importa solo i messaggi riconoscibili dal soggetto usato da Gestione PDF.
La casella viene aperta in sola lettura; nessun messaggio viene modificato.

Uso:
  /opt/mappa-algeco/venv/bin/python tools/import_invii_email_storici.py
  PROVINCIA=PAVIA /opt/mappa-algeco/venv/bin/python tools/import_invii_email_storici.py
"""
import email
import imaplib
import json
import os
import re
import sys
from datetime import datetime
from email.header import decode_header, make_header
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from config import PROVINCIA_ATTIVA  # noqa: E402
from moduli import db  # noqa: E402
from moduli.lettere import registra_invio  # noqa: E402


SUBJECT_MARKER = 'SOSTITUZIONE CONTATORE E-DISTRIBUZIONE'
POD_RE = re.compile(r'\bIT[0-9A-Z]{8,}\b', re.IGNORECASE)
SMTP_SETTINGS = ROOT / 'data' / 'smtp_settings.json'


def _decode_header(value):
    try:
        return str(make_header(decode_header(value or '')))
    except Exception:
        return value or ''


def _message_text(msg):
    chunks = []
    parts = msg.walk() if msg.is_multipart() else (msg,)
    for part in parts:
        if part.get_content_maintype() != 'text':
            continue
        if part.get_content_disposition() == 'attachment':
            continue
        try:
            payload = part.get_payload(decode=True) or b''
            chunks.append(
                payload.decode(part.get_content_charset() or 'utf-8', errors='ignore')
            )
        except Exception:
            continue
    return '\n'.join(chunks)


def _split_addresses(value):
    return {
        item.strip().lower()
        for item in re.split(r'[/,;\s]+', value or '')
        if '@' in item
    }


def _load_settings():
    data = json.loads(SMTP_SETTINGS.read_text(encoding='utf-8'))
    provincia = PROVINCIA_ATTIVA.lower()
    if provincia in data:
        return data[provincia]
    return data if 'host' in data else {}


def _open_sent_readonly(settings):
    host = settings.get('imap_host', '')
    if not host:
        host = settings.get('host', '').replace('smtps.', 'imaps.').replace(
            'smtp.', 'imap.'
        )
    imap = imaplib.IMAP4_SSL(host, 993, timeout=30)
    imap.login(settings.get('username', ''), settings.get('password', ''))

    for folder in ('Sent', 'INBOX.Sent', 'Posta inviata', 'INBOX.Posta inviata'):
        if imap.select(folder, readonly=True)[0] == 'OK':
            return imap, folder

    status, folders = imap.list()
    if status == 'OK':
        for raw in folders or []:
            text = raw.decode('utf-8', errors='ignore')
            match = re.search(r'"([^"]+)"\s*$', text)
            folder = match.group(1) if match else text.rsplit(' ', 1)[-1].strip('"')
            if 'sent' not in folder.lower() and 'inviat' not in folder.lower():
                continue
            if imap.select(folder, readonly=True)[0] == 'OK':
                return imap, folder

    imap.logout()
    raise RuntimeError('Cartella Inviati non trovata')


def _load_contacts(conn):
    rows = conn.execute(
        '''
        SELECT contact_key, pod, email
        FROM lettere_contatti
        WHERE provincia=?
        ''',
        (PROVINCIA_ATTIVA,),
    ).fetchall()
    by_pod = {}
    by_email = {}
    for row in rows:
        pod = (row['pod'] or '').strip().upper()
        if pod:
            by_pod.setdefault(pod, set()).add(row['contact_key'])
        for address in _split_addresses(row['email']):
            by_email.setdefault(address, set()).add(row['contact_key'])
    return by_pod, by_email


def _match_contact(pods, recipients, by_pod, by_email):
    pod_matches = set()
    for pod in pods:
        pod_matches.update(by_pod.get(pod, set()))
    email_matches = set()
    for address in recipients:
        email_matches.update(by_email.get(address, set()))

    if len(pod_matches) == 1:
        return next(iter(pod_matches)), 'pod'
    if len(pod_matches) > 1 and len(pod_matches & email_matches) == 1:
        return next(iter(pod_matches & email_matches)), 'pod_email'
    if len(email_matches) == 1:
        return next(iter(email_matches)), 'email'
    return None, 'unmatched'


def _parse_intervento(value):
    for fmt in ('%d/%m/%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime((value or '').strip(), fmt).date()
        except ValueError:
            continue
    return None


def _match_azienda(conn, contact_key, sent_date):
    if not contact_key:
        return None
    rows = conn.execute(
        '''
        SELECT ag.id, ag.generazione_id, ag.data_pianificazione
        FROM lettere_aziende_generazione ag
        JOIN lettere_generazioni g ON g.id=ag.generazione_id
        WHERE ag.provincia=? AND ag.contact_key=?
        ORDER BY
            COALESCE(NULLIF(g.periodo_a, ''), NULLIF(g.periodo_da, ''), '') DESC,
            ag.id DESC
        ''',
        (PROVINCIA_ATTIVA, contact_key),
    ).fetchall()
    candidati = []
    for row in rows:
        data_intervento = _parse_intervento(row['data_pianificazione'])
        if not data_intervento:
            continue
        giorni = (data_intervento - sent_date).days
        if 0 <= giorni <= 60:
            candidati.append((giorni, row))
    if not candidati:
        return None
    return min(candidati, key=lambda item: item[0])[1]


def _already_imported(conn, sent_ts, email_dest, subject):
    return conn.execute(
        '''
        SELECT 1
        FROM lettere_invii
        WHERE provincia=?
          AND template_id='legacy_imap'
          AND sent_ts=?
          AND LOWER(email_dest)=LOWER(?)
          AND subject=?
        LIMIT 1
        ''',
        (PROVINCIA_ATTIVA, sent_ts, email_dest, subject),
    ).fetchone() is not None


def main():
    settings = _load_settings()
    if not settings.get('username') or not settings.get('password'):
        raise RuntimeError(f'Configurazione IMAP assente per {PROVINCIA_ATTIVA}')

    conn = db.get_conn()
    by_pod, by_email = _load_contacts(conn)
    imap, folder = _open_sent_readonly(settings)
    counters = {
        'riconosciuti': 0,
        'importati': 0,
        'gia_presenti': 0,
        'collegati_contatto': 0,
        'collegati_generazione': 0,
        'non_collegati': 0,
    }

    try:
        status, data = imap.search(None, 'SINCE', '01-Jan-2025')
        message_ids = data[0].split() if status == 'OK' and data else []
        for message_id in message_ids:
            status, parts = imap.fetch(message_id, '(BODY.PEEK[])')
            if status != 'OK':
                continue
            raw = next((part[1] for part in parts if isinstance(part, tuple)), b'')
            if not raw:
                continue
            msg = email.message_from_bytes(raw)
            subject = _decode_header(msg.get('Subject', ''))
            if SUBJECT_MARKER not in subject.upper():
                continue

            counters['riconosciuti'] += 1
            body = _message_text(msg)
            pods = {
                pod.upper().rstrip('.,;:')
                for pod in POD_RE.findall(f'{subject}\n{body}')
            }
            recipients = {
                address.lower()
                for _, address in getaddresses(msg.get_all('To', []))
                if '@' in address
            }
            email_dest = sorted(recipients)[0] if recipients else ''
            if not email_dest:
                counters['non_collegati'] += 1
                continue

            sent_dt = parsedate_to_datetime(msg.get('Date', ''))
            sent_ts = int(sent_dt.timestamp())
            if _already_imported(conn, sent_ts, email_dest, subject):
                counters['gia_presenti'] += 1
                continue

            contact_key, match_method = _match_contact(
                pods, recipients, by_pod, by_email
            )
            azienda = _match_azienda(conn, contact_key, sent_dt.date())
            if contact_key:
                counters['collegati_contatto'] += 1
            else:
                counters['non_collegati'] += 1
            if azienda:
                counters['collegati_generazione'] += 1

            registra_invio(
                azienda_generazione_id=azienda['id'] if azienda else None,
                generazione_id=azienda['generazione_id'] if azienda else None,
                contact_key_value=contact_key,
                pod=sorted(pods)[0] if len(pods) == 1 else '',
                email_dest=email_dest,
                template_id='legacy_imap',
                subject=subject,
                status='sent',
                user='import_imap',
                sent_ts=sent_ts,
                detail={
                    'source': 'imap_history',
                    'message_id': msg.get('Message-ID', ''),
                    'match_method': match_method,
                    'folder': folder,
                },
                provincia=PROVINCIA_ATTIVA,
            )
            counters['importati'] += 1
    finally:
        try:
            imap.close()
        finally:
            imap.logout()

    print(f'Provincia: {PROVINCIA_ATTIVA}')
    for key, value in counters.items():
        print(f'{key}: {value}')


if __name__ == '__main__':
    main()
