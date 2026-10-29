# -*- coding: utf-8 -*-
"""
Modulo centralizzato per la gestione delle configurazioni e dell'invio SMTP.
"""
import os
import time
import smtplib
import imaplib
import logging

from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid, getaddresses

from moduli.safe_json import json_read, json_write
from config import PROVINCIA_ATTIVA

logger = logging.getLogger('mappa_algeco')

_ROOT_DIR = os.path.join(os.path.dirname(__file__), '..')
_SMTP_SETTINGS_JSON = os.path.join(_ROOT_DIR, 'data', 'smtp_settings.json')


def test_smtp_connection(smtp_settings):
    """Verifica socket e autenticazione SMTP senza inviare email."""
    if not smtp_settings or not smtp_settings.get('host'):
        return False, 'Host SMTP non specificato'
    if not smtp_settings.get('password') and not smtp_settings.get('username'):
        return False, 'Credenziali SMTP mancanti'
    server = None
    try:
        server = connect_smtp(smtp_settings, timeout=12)
        try:
            server.quit()
        except Exception:
            pass
        return True, 'Connessione e autenticazione SMTP riuscite con successo!'
    except smtplib.SMTPAuthenticationError as e:
        return False, f'Autenticazione SMTP fallita: nome utente o password non validi ({e})'
    except Exception as e:
        return False, f'Errore di connessione SMTP: {e}'
    finally:
        if server:
            try:
                server.close()
            except Exception:
                pass


def test_send_email(smtp_settings, recipient, prov=None):
    """Invia un'email reale di prova all'indirizzo specificato."""
    recipient = (recipient or '').strip()
    if not recipient or '@' not in recipient:
        return False, 'Indirizzo email di prova non valido'

    from_email = smtp_settings.get('from_email') or smtp_settings.get('username') or ''
    from_name = smtp_settings.get('from_name') or 'Algeco Power - Test SMTP'
    prov_name = (prov or PROVINCIA_ATTIVA or '').upper()

    if not from_email:
        return False, 'Indirizzo mittente (from_email) mancante'

    msg = MIMEText(
        f"Questo e un messaggio di prova automatico inviato da Mappa Algeco.\n\n"
        f"Provincia: {prov_name}\n"
        f"Host SMTP: {smtp_settings.get('host')}:{smtp_settings.get('port', 587)}\n"
        f"Mittente: {from_email}\n"
        f"Destinatario: {recipient}\n"
        f"Data/Ora: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"Se hai ricevuto questo messaggio, la configurazione SMTP per {prov_name} e funzionante!",
        'plain', 'utf-8'
    )
    msg['From'] = formataddr((from_name, from_email))
    msg['To'] = recipient
    msg['Subject'] = f"Test Configurazione SMTP — {prov_name} (Algeco Power)"
    msg['Date'] = formatdate(localtime=True)
    msg['Message-ID'] = make_msgid(domain=from_email.split('@')[-1] if '@' in from_email else 'algecopower.it')

    ok, err = send_smtp_message(smtp_settings, msg, max_retries=1, save_sent=False, add_provincia_ccn=False)
    if ok:
        return True, f"Email di prova inviata con successo a {recipient}!"
    return False, err or "Errore durante l'invio dell'email di prova"



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


def get_safe_smtp_settings(prov=None):
    """Carica le impostazioni SMTP censurando la password per la visualizzazione API/UI."""
    prov_norm = (prov or PROVINCIA_ATTIVA).lower()
    settings = dict(load_smtp_settings(prov_norm) or {})
    settings['password_set'] = bool(settings.get('password'))
    settings['password'] = ''
    settings['provincia'] = prov_norm.upper()
    return settings


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


def get_provincia_email(prov=None) -> str:
    """Restituisce l'indirizzo email configurato per la provincia specificata (o PROVINCIA_ATTIVA).
    Cerca in:
      1. data/{prov}/distinte_templates.json o data/distinte_templates.json (settings.province[PROV].email o settings.email_{prov})
      2. data/smtp_settings.json (from_email / username)
      3. fallback: f"tecnico_{prov.lower()}@algecopower.it"
    """
    prov_norm = (prov or PROVINCIA_ATTIVA or 'TORINO').strip().upper()
    prov_lower = prov_norm.lower()

    # 1. Prova dai template distinte (per-provincia o globale)
    for tpl_path in [
        os.path.join(_ROOT_DIR, 'data', prov_lower, 'distinte_templates.json'),
        os.path.join(_ROOT_DIR, 'data', 'distinte_templates.json'),
    ]:
        if os.path.isfile(tpl_path):
            try:
                tpl_data = json_read(tpl_path, default={}) or {}
                settings = tpl_data.get('settings', {})
                prov_dict = settings.get('province', {}).get(prov_norm, {})
                email = prov_dict.get('email') or settings.get(f'email_{prov_lower}') or settings.get('email_tecnico')
                if email and '@' in str(email):
                    return str(email).strip()
            except Exception:
                pass

    # 2. Prova dalle impostazioni SMTP
    try:
        smtp_conf = load_smtp_settings(prov_lower)
        if smtp_conf:
            email = smtp_conf.get('from_email') or smtp_conf.get('username')
            if email and '@' in str(email):
                return str(email).strip()
    except Exception:
        pass

    return f"tecnico_{prov_lower}@algecopower.it"


def add_provincia_ccn_to_msg(msg, prov=None, ccn_email=None):
    """Aggiunge l'email della provincia in CCn (BCC) all'oggetto Message/MIMEMultipart,
    se non già presente nei destinatari (To, Cc, Bcc).
    Non altera i destinatari visibili (To, Cc) e rispetta lo standard RFC 2822
    in modo che l'invio via smtplib.send_message lo trasmetta come copia nascosta.
    """
    ccn = (ccn_email or get_provincia_email(prov) or '').strip()
    if not ccn or '@' not in ccn:
        return msg

    to_headers = msg.get_all('To', [])
    cc_headers = msg.get_all('Cc', [])
    bcc_headers = msg.get_all('Bcc', [])

    existing_to = [a[1].lower() for a in getaddresses(to_headers)]
    existing_cc = [a[1].lower() for a in getaddresses(cc_headers)]
    existing_bcc = [a[1].lower() for a in getaddresses(bcc_headers)]

    ccn_lower = ccn.lower()
    # Se già presente in To, Cc o Bcc, non duplicare
    if ccn_lower in existing_to or ccn_lower in existing_cc or ccn_lower in existing_bcc:
        return msg

    if bcc_headers:
        current_bcc = ', '.join(bcc_headers)
        del msg['Bcc']
        msg['Bcc'] = f"{current_bcc}, {ccn}"
    else:
        msg['Bcc'] = ccn

    return msg


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


def send_smtp_message(smtp_settings, msg, max_retries=3, base_delay=6, save_sent=True, add_provincia_ccn=True, prov=None):
    """Invia un messaggio email gestendo la connessione SMTP, l'invio e il retry
    in caso di errori temporanei (es. SMTP 452 / 421 / 450 rate limit / policy violation,
    disconnessione o timeout).
    Se add_provincia_ccn è True, assicura che l'indirizzo email configurato per la provincia
    sia incluso in copia nascosta (BCC/CCn).

    Ritorna:
        (ok: bool, error_message: str | None)
    """
    if add_provincia_ccn:
        try:
            add_provincia_ccn_to_msg(msg, prov=prov)
        except Exception as e:
            logger.warning(f"[SMTP] Impossibile applicare CCn provincia: {e}")

    last_err = None
    for attempt in range(1, max_retries + 1):
        server = None
        try:
            server = connect_smtp(smtp_settings, timeout=20)
            server.send_message(msg)
            try:
                server.quit()
            except Exception:
                pass

            if save_sent:
                save_to_sent(smtp_settings, msg)
            return True, None
        except smtplib.SMTPAuthenticationError as e:
            logger.error(f"[SMTP] Autenticazione SMTP fallita: {e}")
            return False, 'Autenticazione SMTP fallita'
        except smtplib.SMTPResponseException as e:
            last_err = e
            # 4xx = errori transienti/temporanei (es. 452 Too many recipients / policy violation, 421, 450, 451)
            is_transient = (400 <= e.smtp_code < 500)
            logger.warning(
                f"[SMTP] Tentativo {attempt}/{max_retries} fallito con codice {e.smtp_code}: {e.smtp_error} "
                f"(transient={is_transient})"
            )
            if is_transient and attempt < max_retries:
                delay = base_delay * attempt
                logger.info(f"[SMTP] Attesa {delay}s prima del retry su errore temporaneo {e.smtp_code}...")
                time.sleep(delay)
                continue
            break
        except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, TimeoutError, OSError) as e:
            last_err = e
            logger.warning(f"[SMTP] Tentativo {attempt}/{max_retries} fallito per errore di connessione: {e}")
            if attempt < max_retries:
                delay = base_delay * attempt
                time.sleep(delay)
                continue
            break
        except Exception as e:
            last_err = e
            logger.error(f"[SMTP] Errore imprevisto durante invio: {e}")
            break
        finally:
            if server:
                try:
                    server.close()
                except Exception:
                    pass

    # Gestione del messaggio di errore finale
    if isinstance(last_err, smtplib.SMTPResponseException):
        err_code = last_err.smtp_code
        raw_msg = last_err.smtp_error
        err_msg = raw_msg.decode('utf-8', errors='replace') if isinstance(raw_msg, bytes) else str(raw_msg)
        if err_code == 452 or 'policy violation' in err_msg.lower() or 'too many' in err_msg.lower():
            return False, f"Limite invio provider superato ({err_code}: {err_msg}). Riprova tra qualche istante o aumenta la pausa tra gli invii."
        return False, f"Errore SMTP {err_code}: {err_msg}"

    return False, str(last_err) if last_err else 'Errore sconosciuto durante l\'invio'



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
