# -*- coding: utf-8 -*-
"""
Modulo di autenticazione per il gestionale.
Gestisce login, sessioni e verifica credenziali.
"""
import os
import json
import hashlib
import secrets
from datetime import datetime, timedelta
from functools import wraps
from flask import session, redirect, url_for, request, jsonify

from .safe_json import json_read, json_write

# Directory base per i dati
SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USERS_FILE = os.path.join(SCRIPT_DIR, 'users.json')

# Configurazione sessioni
SESSION_LIFETIME_HOURS = 24
SECRET_KEY_LENGTH = 32


def generate_secret_key():
    """Genera una chiave segreta sicura per Flask."""
    return secrets.token_hex(SECRET_KEY_LENGTH)


def hash_password(password: str, salt: str = None) -> tuple:
    """
    Hash della password usando PBKDF2 con SHA-256.
    Ritorna (hash, salt).
    """
    if salt is None:
        salt = secrets.token_hex(16)
    
    # PBKDF2 con 100000 iterazioni
    password_hash = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode('utf-8'),
        salt.encode('utf-8'),
        100000
    ).hex()
    
    return password_hash, salt


def verify_password(password: str, stored_hash: str, salt: str) -> bool:
    """Verifica che la password corrisponda all'hash memorizzato."""
    computed_hash, _ = hash_password(password, salt)
    return secrets.compare_digest(computed_hash, stored_hash)


def generate_password(length: int = 16) -> str:
    """Genera una password casuale sicura."""
    # Caratteri per password leggibile ma sicura
    alphabet = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%&*'
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def load_users() -> dict:
    """Carica gli utenti dal file JSON."""
    return json_read(USERS_FILE, default={})


def save_users(users: dict) -> bool:
    """Salva gli utenti nel file JSON (atomico)."""
    return json_write(USERS_FILE, users, indent=2)


# Province disponibili nel sistema
PROVINCE_DISPONIBILI = ['TORINO', 'PAVIA']

# Ruoli disponibili
RUOLI_DISPONIBILI = ['admin', 'user']


def create_user(username: str, password: str = None, province: list = None, ruolo: str = 'user') -> tuple:
    """
    Crea un nuovo utente.
    Se password non è specificata, ne genera una casuale.
    Se province non è specificata, assegna tutte le province.
    Ritorna (success: bool, message: str, password: str se generata).
    """
    users = load_users()
    
    # Normalizza username
    username = username.strip().lower()
    
    if not username:
        return False, "Username non valido", None
    
    if username in users:
        return False, f"Utente '{username}' già esistente", None
    
    # Province di default: tutte
    if province is None:
        province = list(PROVINCE_DISPONIBILI)
    else:
        province = [p.upper() for p in province if p.upper() in PROVINCE_DISPONIBILI]
        if not province:
            return False, "Nessuna provincia valida specificata", None
    
    # Valida ruolo
    if ruolo not in RUOLI_DISPONIBILI:
        ruolo = 'user'
    
    # Genera password se non fornita
    generated_password = None
    if password is None:
        password = generate_password()
        generated_password = password
    
    # Hash della password
    password_hash, salt = hash_password(password)
    
    # Crea utente
    users[username] = {
        'password_hash': password_hash,
        'salt': salt,
        'created_at': datetime.now().isoformat(),
        'last_login': None,
        'active': True,
        'province': province,
        'ruolo': ruolo
    }
    
    if save_users(users):
        return True, f"Utente '{username}' creato con successo", generated_password
    else:
        return False, "Errore nel salvataggio", None


def delete_user(username: str) -> tuple:
    """Elimina un utente. Ritorna (success, message)."""
    users = load_users()
    username = username.strip().lower()
    
    if username not in users:
        return False, f"Utente '{username}' non trovato"
    
    del users[username]
    
    if save_users(users):
        return True, f"Utente '{username}' eliminato"
    else:
        return False, "Errore nel salvataggio"


def reset_password(username: str, new_password: str = None) -> tuple:
    """
    Reset password utente.
    Se new_password non è specificata, ne genera una casuale.
    Ritorna (success, message, new_password se generata).
    """
    users = load_users()
    username = username.strip().lower()
    
    if username not in users:
        return False, f"Utente '{username}' non trovato", None
    
    # Genera password se non fornita
    generated_password = None
    if new_password is None:
        new_password = generate_password()
        generated_password = new_password
    
    # Hash della nuova password
    password_hash, salt = hash_password(new_password)
    
    users[username]['password_hash'] = password_hash
    users[username]['salt'] = salt
    
    if save_users(users):
        return True, f"Password di '{username}' resettata", generated_password
    else:
        return False, "Errore nel salvataggio", None


def authenticate(username: str, password: str, provincia_attiva: str = None) -> tuple:
    """
    Autentica un utente.
    Se provincia_attiva è specificata, verifica che l'utente abbia accesso.
    Ritorna (success: bool, user_data: dict o None, message: str).
    """
    users = load_users()
    username = username.strip().lower()
    
    if username not in users:
        # Ritardo per prevenire enumerazione utenti
        import time
        time.sleep(0.5)
        return False, None, "Credenziali non valide"
    
    user = users[username]
    
    if not user.get('active', True):
        return False, None, "Account disabilitato"
    
    if not verify_password(password, user['password_hash'], user['salt']):
        return False, None, "Credenziali non valide"
    
    # Verifica accesso alla provincia
    if provincia_attiva:
        province_utente = user.get('province', list(PROVINCE_DISPONIBILI))
        if provincia_attiva.upper() not in province_utente:
            return False, None, f"Non hai accesso alla provincia {provincia_attiva}"
    
    # Aggiorna last_login
    users[username]['last_login'] = datetime.now().isoformat()
    save_users(users)
    
    return True, {
        'username': username,
        'province': user.get('province', list(PROVINCE_DISPONIBILI)),
        'ruolo': user.get('ruolo', 'user'),
    }, "Login effettuato"


def list_users() -> list:
    """Ritorna la lista degli utenti (senza dati sensibili)."""
    users = load_users()
    return [
        {
            'username': username,
            'created_at': data.get('created_at'),
            'last_login': data.get('last_login'),
            'active': data.get('active', True),
            'province': data.get('province', list(PROVINCE_DISPONIBILI)),
            'ruolo': data.get('ruolo', 'user')
        }
        for username, data in users.items()
    ]


def get_user_province(username: str) -> list:
    """Ritorna le province accessibili per un utente."""
    users = load_users()
    username = username.strip().lower()
    if username not in users:
        return []
    return users[username].get('province', list(PROVINCE_DISPONIBILI))


def set_user_province(username: str, province: list) -> tuple:
    """Imposta le province accessibili per un utente. Ritorna (success, message)."""
    users = load_users()
    username = username.strip().lower()
    
    if username not in users:
        return False, f"Utente '{username}' non trovato"
    
    # Valida province
    province_valide = [p.upper() for p in province if p.upper() in PROVINCE_DISPONIBILI]
    if not province_valide:
        return False, "Nessuna provincia valida specificata"
    
    users[username]['province'] = province_valide
    
    if save_users(users):
        return True, f"Province di '{username}' aggiornate: {', '.join(province_valide)}"
    else:
        return False, "Errore nel salvataggio"


def set_user_role(username: str, ruolo: str) -> tuple:
    """Imposta il ruolo di un utente. Ritorna (success, message)."""
    users = load_users()
    username = username.strip().lower()
    
    if username not in users:
        return False, f"Utente '{username}' non trovato"
    
    if ruolo not in RUOLI_DISPONIBILI:
        return False, f"Ruolo '{ruolo}' non valido. Ruoli disponibili: {', '.join(RUOLI_DISPONIBILI)}"
    
    users[username]['ruolo'] = ruolo
    
    if save_users(users):
        return True, f"Ruolo di '{username}' aggiornato a '{ruolo}'"
    else:
        return False, "Errore nel salvataggio"


def toggle_user_active(username: str) -> tuple:
    """Attiva/disattiva un utente. Ritorna (success, message, new_state)."""
    users = load_users()
    username = username.strip().lower()
    
    if username not in users:
        return False, f"Utente '{username}' non trovato", None
    
    current_state = users[username].get('active', True)
    users[username]['active'] = not current_state
    
    if save_users(users):
        new_state = users[username]['active']
        stato = "attivato" if new_state else "disattivato"
        return True, f"Utente '{username}' {stato}", new_state
    else:
        return False, "Errore nel salvataggio", None


# ============================================================================
# DECORATORI PER FLASK
# ============================================================================

def login_required(f):
    """Decoratore per proteggere le route che richiedono autenticazione."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            # Se è una richiesta API, ritorna JSON
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Autenticazione richiesta'}), 401
            # Altrimenti redirect al login
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    """Decoratore per proteggere le route che richiedono ruolo admin."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Autenticazione richiesta'}), 401
            return redirect(url_for('login_page'))
        if session['user'].get('ruolo') != 'admin':
            return jsonify({'error': 'Permessi insufficienti'}), 403
        return f(*args, **kwargs)
    return decorated_function


def init_auth(app, secret_key=None):
    """
    Inizializza l'autenticazione per l'app Flask.
    
    Args:
        app: L'istanza Flask
        secret_key: Chiave segreta per le sessioni (opzionale)
    """
    # Imposta chiave segreta
    if secret_key:
        app.secret_key = secret_key
    else:
        # Genera o carica chiave segreta persistente
        key_file = os.path.join(SCRIPT_DIR, '.flask_secret_key')
        if os.path.exists(key_file):
            with open(key_file, 'r') as f:
                app.secret_key = f.read().strip()
        else:
            app.secret_key = generate_secret_key()
            with open(key_file, 'w') as f:
                f.write(app.secret_key)
    
    # Configura sessioni
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['SESSION_COOKIE_SECURE'] = os.environ.get('FLASK_ENV') == 'production'  # True solo via HTTPS (Cloudflare)
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=SESSION_LIFETIME_HOURS)
    # Cookie domain: se impostato via env var (es. '.mappa-algeco.com' in prod),
    # la sessione vale per tutti i sottodomini → l'auth fatto su torino.* o
    # pavia.* vale anche su admin.* (UI di gestione province nel router).
    # Default unset: comportamento legacy (cookie scoped al singolo host).
    cookie_domain = os.environ.get('SESSION_COOKIE_DOMAIN')
    if cookie_domain:
        app.config['SESSION_COOKIE_DOMAIN'] = cookie_domain
    
    # Crea utente admin di default se non esistono utenti
    users = load_users()
    if not users:
        print("⚠️ Nessun utente trovato. Usa 'python gestione_utenti.py' per creare utenti.")
