# -*- coding: utf-8 -*-
"""
moduli/sms_gateway.py — Gestione e invio SMS tramite SMS Gateway configurabile
(Android SMS Gateway, Webhook generico).
"""

import logging
import os
import re
import requests
from typing import Any

from moduli.safe_json import json_read, json_write

logger = logging.getLogger('mappa_algeco')

_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SMS_SETTINGS_JSON = os.path.join(_ROOT_DIR, 'data', 'sms_settings.json')

# GSM 7-bit standard character set
_GSM_7BIT_CHARS = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ\x1bÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
_GSM_7BIT_EXTENDED = set("^{}\\[~]|€")


def normalize_phone_number(numero: str) -> str:
    """
    Normalizza un numero di telefono italiano in formato internazionale E.164 (+39...).
    Rimuove spazi, trattini, parentesi e gestisce prefissi doppi o zeri iniziali errati.
    """
    if not numero:
        return ''
    cleaned = re.sub(r'[\s\-\.\(\)\+]', '', str(numero))
    if not cleaned or not cleaned.isdigit() or len(cleaned) < 5:
        return ''
    if cleaned.startswith('0039'):
        cleaned = cleaned[4:]
    elif cleaned.startswith('39') and len(cleaned) > 10:
        cleaned = cleaned[2:]
    if cleaned.startswith('0') and len(cleaned) > 10:
        cleaned = cleaned[1:]
    return f"+39{cleaned}"


def count_sms_parts(text: str) -> dict[str, Any]:
    """
    Calcola la lunghezza e il numero di segmenti SMS necessari per il testo fornito.
    Distingue tra codifica standard GSM-7 (160/153 caratteri) e Unicode/UCS-2 (70/67 caratteri).
    """
    if not text:
        return {'length': 0, 'parts': 0, 'is_unicode': False, 'chars_per_part': 160}

    is_unicode = False
    for ch in text:
        if ch not in _GSM_7BIT_CHARS and ch not in _GSM_7BIT_EXTENDED:
            is_unicode = True
            break

    length = len(text)
    if is_unicode:
        limit_single = 70
        limit_multi = 67
    else:
        # I caratteri estesi contano come 2 caratteri GSM-7
        extra = sum(1 for ch in text if ch in _GSM_7BIT_EXTENDED)
        length += extra
        limit_single = 160
        limit_multi = 153

    if length <= limit_single:
        parts = 1 if length > 0 else 0
    else:
        parts = (length + limit_multi - 1) // limit_multi

    return {
        'length': length,
        'parts': parts,
        'is_unicode': is_unicode,
        'chars_per_part': limit_single if parts <= 1 else limit_multi,
    }


def load_sms_settings(prov: str | None = None) -> dict[str, Any]:
    """
    Carica le impostazioni del Gateway SMS.
    Cerca prima in data/{provincia}/sms_settings.json, poi nel file globale data/sms_settings.json.
    """
    defaults = {
        'enabled': False,
        'provider': 'android_sms_gateway',  # 'android_sms_gateway', 'generic_webhook'
        'endpoint_url': 'https://api.sms-gate.app/3rdparty/v1/message',
        'auth_type': 'basic',  # 'none', 'basic', 'bearer', 'api_key'
        'username': '',
        'password': '',
        'api_key': '',
        'header_name': 'X-API-Key',
        'rate_limit_delay': 4,  # Secondi di pausa consigliati tra un SMS e l'altro
        'rate_limit_delay_seconds': 4,
        'timeout_seconds': 15,
    }

    # Prova per-provincia
    if prov:
        prov_file = os.path.join(_ROOT_DIR, 'data', prov.lower(), 'sms_settings.json')
        if os.path.exists(prov_file):
            d = json_read(prov_file, default={})
            if d:
                res = dict(defaults)
                res.update(d)
                if 'rate_limit_delay' in res and 'rate_limit_delay_seconds' not in d:
                    res['rate_limit_delay_seconds'] = res['rate_limit_delay']
                elif 'rate_limit_delay_seconds' in res and 'rate_limit_delay' not in d:
                    res['rate_limit_delay'] = res['rate_limit_delay_seconds']
                return res

    # File globale
    if os.path.exists(_SMS_SETTINGS_JSON):
        d = json_read(_SMS_SETTINGS_JSON, default={})
        if d:
            res = dict(defaults)
            res.update(d)
            if 'rate_limit_delay' in res and 'rate_limit_delay_seconds' not in d:
                res['rate_limit_delay_seconds'] = res['rate_limit_delay']
            elif 'rate_limit_delay_seconds' in res and 'rate_limit_delay' not in d:
                res['rate_limit_delay'] = res['rate_limit_delay_seconds']
            return res

    return defaults


def save_sms_settings(data: dict[str, Any], prov: str | None = None) -> bool:
    """
    Salva le impostazioni del Gateway SMS nel file globale e/o per-provincia.
    Se la password/token non viene modificata (valore vuoto), preserva quella esistente.
    """
    curr = load_sms_settings(prov)
    
    clean_data = dict(curr)
    for k in ('enabled', 'provider', 'endpoint_url', 'auth_type', 'username',
              'header_name', 'rate_limit_delay', 'rate_limit_delay_seconds',
              'timeout_seconds'):
        if k in data:
            clean_data[k] = data[k]
            if k == 'rate_limit_delay' and 'rate_limit_delay_seconds' not in data:
                clean_data['rate_limit_delay_seconds'] = data[k]
            elif k == 'rate_limit_delay_seconds' and 'rate_limit_delay' not in data:
                clean_data['rate_limit_delay'] = data[k]

    # Preserva password se vuota
    if data.get('password'):
        clean_data['password'] = data['password']
    if data.get('api_key'):
        clean_data['api_key'] = data['api_key']

    target_file = _SMS_SETTINGS_JSON
    if prov:
        prov_dir = os.path.join(_ROOT_DIR, 'data', prov.lower())
        os.makedirs(prov_dir, exist_ok=True)
        target_file = os.path.join(prov_dir, 'sms_settings.json')

    return json_write(target_file, clean_data, indent=4)


def send_sms(
    phone: str,
    message: str,
    settings: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    prov: str | None = None,
) -> tuple[bool, dict[str, Any], str]:
    """
    Invia un SMS attraverso il gateway configurato.

    Ritorna:
        (ok: bool, info: dict, errore: str)
        info può contenere: {'parts': int, 'gateway_id': str, 'provider': str}
    """
    if not settings:
        settings = load_sms_settings(prov)

    if not settings.get('enabled', False):
        return False, {}, "SMS Gateway disabilitato nelle impostazioni"

    endpoint = (settings.get('endpoint_url') or '').strip()
    if not endpoint:
        return False, {}, "URL endpoint dell'SMS Gateway non configurato"

    formatted_phone = normalize_phone_number(phone)
    if not formatted_phone or len(formatted_phone) < 10:
        return False, {}, f"Numero di telefono non valido: {phone}"

    clean_message = (message or '').strip()
    if not clean_message:
        return False, {}, "Messaggio SMS vuoto"

    provider = settings.get('provider') or 'android_sms_gateway'
    timeout = int(settings.get('timeout_seconds') or 15)
    auth_type = settings.get('auth_type') or 'none'

    headers: dict[str, str] = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }
    auth = None

    if auth_type == 'basic':
        auth = (settings.get('username', ''), settings.get('password', ''))
    elif auth_type == 'bearer':
        token = settings.get('api_key') or settings.get('password', '')
        if token:
            headers['Authorization'] = f"Bearer {token}"
    elif auth_type == 'api_key':
        header_name = settings.get('header_name') or 'X-API-Key'
        token = settings.get('api_key') or settings.get('password', '')
        if token:
            headers[header_name] = token

    parts_info = count_sms_parts(clean_message)

    # Assicura il path corretto per Android SMS Gateway (Capcom6) se l'utente ha inserito solo host:porta
    if provider == 'android_sms_gateway':
        from urllib.parse import urlparse, urlunparse
        parsed = urlparse(endpoint)
        if parsed.netloc and (not parsed.path or parsed.path == '/'):
            if 'api.sms-gate.app' in parsed.netloc:
                endpoint = urlunparse((parsed.scheme or 'https', parsed.netloc, '/3rdparty/v1/message', '', '', ''))
            else:
                endpoint = urlunparse((parsed.scheme or 'http', parsed.netloc, '/message', '', '', ''))

    try:
        if provider == 'android_sms_gateway':
            # Standard capcom6 / sms-gate.app REST API
            # POST /message con {"phoneNumbers": ["+39..."], "message": "..."}
            payload = {
                'phoneNumbers': [formatted_phone],
                'message': clean_message,
            }
            resp = requests.post(
                endpoint,
                json=payload,
                headers=headers,
                auth=auth,
                timeout=timeout,
            )
            
            if resp.status_code in (200, 201, 202):
                data = resp.json() if resp.text else {}
                msg_id = data.get('id') or (data.get('messages', [{}])[0].get('id') if data.get('messages') else '')
                return True, {
                    'gateway_id': msg_id,
                    'parts': parts_info['parts'],
                    'provider': 'android_sms_gateway',
                }, ""
            else:
                err = resp.text[:200]
                logger.warning(f"[SMS_GATEWAY] Errore HTTP {resp.status_code}: {err}")
                return False, {}, f"Gateway HTTP {resp.status_code}: {err}"

        else:
            # Webhook generico
            payload = {
                'phone': formatted_phone,
                'recipient': formatted_phone,
                'message': clean_message,
                'text': clean_message,
            }
            resp = requests.post(
                endpoint,
                json=payload,
                headers=headers,
                auth=auth,
                timeout=timeout,
            )
            if resp.status_code in (200, 201, 202, 204):
                return True, {
                    'parts': parts_info['parts'],
                    'provider': 'generic_webhook',
                }, ""
            else:
                err = resp.text[:200]
                return False, {}, f"Webhook HTTP {resp.status_code}: {err}"

    except requests.exceptions.Timeout:
        logger.error(f"[SMS_GATEWAY] Timeout dopo {timeout}s verso {endpoint}")
        from urllib.parse import urlparse
        import ipaddress
        parsed = urlparse(endpoint)
        hostname = (parsed.hostname or '').strip()
        is_private = False
        try:
            ip = ipaddress.ip_address(hostname)
            is_private = ip.is_private or ip.is_loopback
        except ValueError:
            if hostname.lower() in ('localhost', '127.0.0.1'):
                is_private = True

        if is_private:
            return False, {}, (
                f"Timeout connessione ({timeout}s) verso {hostname}. "
                "ATTENZIONE: Questo gestionale si trova su un server cloud remoto e non può raggiungere l'IP privato della tua rete Wi-Fi locale (192.168.x.x). "
                "Per connettere lo smartphone, usa la modalità 'Cloud Server' dell'app (sms-gate.app) con URL: https://api.sms-gate.app/3rdparty/v1/message"
            )
        return False, {}, f"Timeout connessione verso il gateway ({timeout}s)"

    except requests.exceptions.ConnectionError as e:
        logger.error(f"[SMS_GATEWAY] Errore connessione verso {endpoint}: {e}")
        from urllib.parse import urlparse
        import ipaddress
        parsed = urlparse(endpoint)
        hostname = (parsed.hostname or '').strip()
        is_private = False
        try:
            ip = ipaddress.ip_address(hostname)
            is_private = ip.is_private or ip.is_loopback
        except ValueError:
            if hostname.lower() in ('localhost', '127.0.0.1'):
                is_private = True

        if is_private:
            return False, {}, (
                f"Connessione fallita verso {hostname}. "
                "ATTENZIONE: Il gestionale è su un server cloud remoto e non può contattare la rete Wi-Fi locale (192.168.x.x). "
                "Usa la modalità 'Cloud Server' dell'app (sms-gate.app) con URL: https://api.sms-gate.app/3rdparty/v1/message"
            )
        return False, {}, "Impossibile connettersi all'SMS Gateway (verificare che il telefono sia connesso e l'app attiva)"
    except Exception as e:
        logger.exception(f"[SMS_GATEWAY] Errore imprevisto invio SMS a {formatted_phone}: {e}")
        return False, {}, f"Errore invio: {str(e)}"


def test_sms_gateway(settings: dict[str, Any], test_phone: str, prov: str | None = None) -> tuple[bool, str]:
    """
    Esegue un test di invio verso il numero indicato per verificare le credenziali e l'endpoint.
    Se password o api_key sono vuote nei settings forniti, prova a recuperare quelle salvate sul disco.
    """
    if not test_phone:
        return False, "Specificare un numero di cellulare per il test"

    merged = dict(settings)
    if prov or not merged.get('password') or not merged.get('api_key'):
        saved = load_sms_settings(prov)
        if not merged.get('password') and saved.get('password'):
            merged['password'] = saved['password']
        if not merged.get('api_key') and saved.get('api_key'):
            merged['api_key'] = saved['api_key']

    test_msg = "Algeco Power: Test di invio SMS Gateway configurato con successo."
    ok, info, err = send_sms(test_phone, test_msg, settings=merged, prov=prov)
    if ok:
        return True, f"SMS di prova inviato con successo a {normalize_phone_number(test_phone)}"
    return False, err
