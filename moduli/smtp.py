# -*- coding: utf-8 -*-
"""
Modulo centralizzato per la gestione delle configurazioni e dell'invio SMTP.
"""
import os
import time
import smtplib
import imaplib
import logging

from moduli.safe_json import json_read, json_write
from config import PROVINCIA_ATTIVA

logger = logging.getLogger('mappa_algeco')

_ROOT_DIR = os.path.join(os.path.dirname(__file__), '..')
_SMTP_SETTINGS_JSON = os.path.join(_ROOT_DIR, 'data', 'smtp_settings.json')


def load_smtp_settings(prov=None):
    """Carica le impostazioni SMTP per la provincia specificata (o PROVINCIA_ATTIVA)."""
    prov = (prov or PROVINCIA_ATTIVA).lower()
    data = json_read(_SMTP_SETTINGS_JSON, default={})
    # Supporta sia il vecchio formato flat che il nuovo per-provincia
    if prov in data:
        return data[prov]
    # Fallback: formato flat legacy
    if 'host' in data:
        return data
    return {}


def save_smtp_settings(data, prov=None):
    """Salva le impostazioni SMTP per la provincia specificata (o PROVINCIA_ATTIVA)."""
    prov = (prov or PROVINCIA_ATTIVA).lower()

    # Carica file esistente (tutte le province)
    all_settings = json_read(_SMTP_SETTINGS_JSON, default={})

    # Migra formato flat legacy se necessario
    if 'host' in all_settings and prov not in all_settings:
        old = {k: v for k, v in all_settings.items()}
        all_settings = {'torino': old}

    # Se la password non è stata modificata, mantieni quella esistente
    existing = all_settings.get(prov, {})
    if not data.get('password') and existing.get('password'):
        data['password'] = existing['password']

    # Rimuovi campo provincia dal salvataggio (se presente)
    data.pop('provincia', None)

    # Validazione base
    required = ['host', 'port', 'username', 'from_email']
    for field in required:
        if not data.get(field):
            raise ValueError(f'Campo "{field}" obbligatorio')

    all_settings[prov] = data
    json_write(_SMTP_SETTINGS_JSON, all_settings, indent=4)
    return True


def connect_smtp(smtp_settings, timeout=15):
    """Crea e restituisce una connessione SMTP autenticata.
    Porta 465 → SSL diretto (SMTP_SSL). Porta 587 → STARTTLS."""
    host = smtp_settings['host']
    port = int(smtp_settings.get('port', 587))
    username = smtp_settings.get('username', '')
    password = smtp_settings.get('password', '')
    use_tls = smtp_settings.get('use_tls', True)

    if port == 465:
        # SSL diretto (Aruba, ecc.)
        server = smtplib.SMTP_SSL(host, port, timeout=timeout)
    else:
        server = smtplib.SMTP(host, port, timeout=timeout)
        if use_tls:
            server.starttls()

    if username and password:
        server.login(username, password)
    return server


def save_to_sent(smtp_settings, msg):
    """Salva una copia del messaggio nella cartella Inviati via IMAP.
    Deriva l'host IMAP dall'host SMTP (es. smtps.aruba.it → imaps.aruba.it)."""
    try:
        smtp_host = smtp_settings.get('host', '')
        username = smtp_settings.get('username', '')
        password = smtp_settings.get('password', '')

        if not smtp_host or not username or not password:
            return

        # Deriva host IMAP
        imap_host = smtp_settings.get('imap_host', '')
        if not imap_host:
            imap_host = smtp_host.replace('smtps.', 'imaps.').replace('smtp.', 'imap.')

        imap = imaplib.IMAP4_SSL(imap_host, 993, timeout=15)
        imap.login(username, password)

        # Prova cartelle comuni per "Inviati"
        sent_folder = None
        for folder_name in ['Sent', 'INBOX.Sent', 'Posta inviata', 'INBOX.Posta inviata']:
            status, _ = imap.select(folder_name)
            if status == 'OK':
                sent_folder = folder_name
                break

        if not sent_folder:
            # Lista tutte le cartelle e cerca una con "sent" o "inviat"
            status, folders = imap.list()
            if status == 'OK':
                for f in folders:
                    fname = f.decode('utf-8', errors='ignore').split('"')[-2] if b'"' in f else ''
                    if fname and ('sent' in fname.lower() or 'inviat' in fname.lower()):
                        sent_folder = fname
                        break

        if sent_folder:
            msg_bytes = msg.as_bytes()
            imaplib.Time2Internaldate(time.time())
            imap.append(sent_folder, '\\Seen', None, msg_bytes)
            logger.info(f"[IMAP] Email salvata in '{sent_folder}'")
        else:
            logger.warning("[IMAP] Cartella Inviati non trovata")

        imap.logout()
    except Exception as e:
        # Non bloccare l'invio se IMAP fallisce
        logger.warning(f"[IMAP] Errore salvataggio in Inviati: {e}")
