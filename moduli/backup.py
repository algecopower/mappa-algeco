# -*- coding: utf-8 -*-
"""
Modulo per backup e ripristino periodico del FILONE (Google Sheet).

Funzionalità:
- Backup giornaliero automatico del FILONE in locale (cartella backup/)
- Salta il backup se già presente per la data odierna
- Pulizia automatica a fine mese (cancella tutti i backup accumulati)
- Ripristino di una versione di backup su Google Drive
"""
import os
import json
import threading
from datetime import datetime, date
import traceback

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from .safe_json import json_read, json_write

from config import (
    ID_FOGLIO_GOOGLE,
    GID_SCHEDA,
    CREDENZIALI_JSON,
    PROVINCIA_ATTIVA,
)

# Directory base dello script (dove sta server.py)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP_DIR = os.path.join(BASE_DIR, 'backup', PROVINCIA_ATTIVA.lower())

# Lock per operazioni di backup
_backup_lock = threading.Lock()


def _assicura_cartella_backup():
    """Crea la cartella di backup se non esiste."""
    os.makedirs(BACKUP_DIR, exist_ok=True)


def _nome_file_backup(data: date) -> str:
    """Genera il nome del file di backup per una data."""
    return f"filone_{PROVINCIA_ATTIVA.lower()}_{data.strftime('%Y-%m-%d')}.json"


def _percorso_backup(data: date) -> str:
    """Restituisce il percorso completo del file di backup."""
    return os.path.join(BACKUP_DIR, _nome_file_backup(data))


def backup_esiste(data: date = None) -> bool:
    """Verifica se esiste già un backup per la data specificata (default: oggi)."""
    if data is None:
        data = date.today()
    return os.path.exists(_percorso_backup(data))


def _connetti_google_sheets():
    """Crea una connessione autenticata a Google Sheets."""
    scope = [
        'https://spreadsheets.google.com/feeds',
        'https://www.googleapis.com/auth/drive'
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_name(
        os.path.join(BASE_DIR, CREDENZIALI_JSON), scope
    )
    client = gspread.authorize(creds)
    return client


def _get_worksheet(client=None):
    """Ottiene il worksheet del FILONE."""
    if client is None:
        client = _connetti_google_sheets()
    spreadsheet = client.open_by_key(ID_FOGLIO_GOOGLE)
    
    # Trova il worksheet con il GID corretto
    for ws in spreadsheet.worksheets():
        if ws.id == GID_SCHEDA:
            return ws, client
    
    # Fallback al primo foglio
    return spreadsheet.sheet1, client


def esegui_backup(data: date = None, forza: bool = False) -> dict:
    """
    Esegue il backup del FILONE per la data specificata.
    
    Args:
        data: Data per il backup (default: oggi)
        forza: Se True, sovrascrive un backup esistente
        
    Returns:
        dict con risultato: {'success': bool, 'message': str, 'file': str}
    """
    if data is None:
        data = date.today()
    
    with _backup_lock:
        try:
            _assicura_cartella_backup()
            
            percorso = _percorso_backup(data)
            
            # Controlla se esiste già
            if not forza and os.path.exists(percorso):
                return {
                    'success': True,
                    'message': f'Backup già presente per {data.strftime("%d/%m/%Y")}',
                    'file': percorso,
                    'skipped': True
                }
            
            print(f"[BACKUP] 📦 Scaricamento FILONE per {data.strftime('%d/%m/%Y')}...")
            
            # Scarica tutti i dati dal FILONE
            ws, _ = _get_worksheet()
            all_data = ws.get_all_values()
            
            if not all_data:
                return {
                    'success': False,
                    'message': 'FILONE vuoto - nessun dato da salvare',
                    'file': None
                }
            
            # Salva come JSON (headers + righe)
            backup_data = {
                'provincia': PROVINCIA_ATTIVA,
                'data_backup': data.isoformat(),
                'timestamp': datetime.now().isoformat(),
                'id_foglio': ID_FOGLIO_GOOGLE,
                'gid_scheda': GID_SCHEDA,
                'headers': all_data[0],
                'righe': all_data[1:],
                'totale_righe': len(all_data) - 1
            }
            
            with open(percorso, 'w', encoding='utf-8') as f:
                json.dump(backup_data, f, ensure_ascii=False, indent=2)
            
            dimensione_mb = os.path.getsize(percorso) / (1024 * 1024)
            
            print(f"[BACKUP] ✅ Backup salvato: {percorso} ({dimensione_mb:.1f} MB, {backup_data['totale_righe']} righe)")
            
            return {
                'success': True,
                'message': f'Backup completato: {backup_data["totale_righe"]} righe ({dimensione_mb:.1f} MB)',
                'file': percorso,
                'skipped': False,
                'righe': backup_data['totale_righe'],
                'dimensione_mb': round(dimensione_mb, 2)
            }
            
        except Exception as e:
            print(f"[BACKUP] ❌ Errore: {e}")
            traceback.print_exc()
            return {
                'success': False,
                'message': f'Errore durante il backup: {str(e)}',
                'file': None
            }


def pulisci_backup_fine_mese() -> dict:
    """
    Cancella tutti i backup accumulati se siamo al primo giorno del mese.
    Viene chiamata automaticamente dal backup periodico.
    
    Returns:
        dict con risultato della pulizia
    """
    oggi = date.today()
    
    # Pulizia solo il primo giorno del mese
    if oggi.day != 1:
        return {'cleaned': False, 'reason': 'Non è il primo del mese'}
    
    try:
        _assicura_cartella_backup()
        
        file_rimossi = []
        for filename in os.listdir(BACKUP_DIR):
            if filename.startswith('filone_') and filename.endswith('.json'):
                percorso = os.path.join(BACKUP_DIR, filename)
                os.remove(percorso)
                file_rimossi.append(filename)
        
        if file_rimossi:
            print(f"[BACKUP] 🧹 Pulizia fine mese: rimossi {len(file_rimossi)} backup")
        
        return {
            'cleaned': True,
            'files_removed': len(file_rimossi),
            'files': file_rimossi
        }
        
    except Exception as e:
        print(f"[BACKUP] ❌ Errore pulizia: {e}")
        return {'cleaned': False, 'error': str(e)}


def lista_backup() -> list:
    """
    Restituisce la lista dei backup disponibili.
    
    Returns:
        Lista di dict con info sui backup, ordinata per data (più recente prima)
    """
    _assicura_cartella_backup()
    
    backup_list = []
    
    for filename in os.listdir(BACKUP_DIR):
        if not (filename.startswith('filone_') and filename.endswith('.json')):
            continue
        
        percorso = os.path.join(BACKUP_DIR, filename)
        
        try:
            data = json_read(percorso, default=None)
            if data is None:
                backup_list.append({
                    'filename': filename,
                    'data_backup': '',
                    'timestamp': '',
                    'provincia': '',
                    'totale_righe': 0,
                    'dimensione_mb': 0,
                    'errore': 'File non leggibile'
                })
                continue
            
            dimensione_mb = os.path.getsize(percorso) / (1024 * 1024)
            
            backup_list.append({
                'filename': filename,
                'data_backup': data.get('data_backup', ''),
                'timestamp': data.get('timestamp', ''),
                'provincia': data.get('provincia', ''),
                'totale_righe': data.get('totale_righe', 0),
                'dimensione_mb': round(dimensione_mb, 2),
            })
        except Exception as e:
            # File corrotto, lo segnala comunque
            backup_list.append({
                'filename': filename,
                'data_backup': '',
                'timestamp': '',
                'provincia': '',
                'totale_righe': 0,
                'dimensione_mb': 0,
                'errore': str(e)
            })
    
    # Ordina per data backup (più recente prima)
    backup_list.sort(key=lambda x: x.get('data_backup', ''), reverse=True)
    
    return backup_list


def ripristina_backup(filename: str) -> dict:
    """
    Ripristina un backup sul FILONE (Google Drive).
    
    ATTENZIONE: Questa operazione sovrascrive TUTTI i dati del FILONE!
    
    Args:
        filename: Nome del file di backup da ripristinare
        
    Returns:
        dict con risultato del ripristino
    """
    percorso = os.path.realpath(os.path.join(BACKUP_DIR, filename))
    if not percorso.startswith(os.path.realpath(BACKUP_DIR) + os.sep):
        return {
            'success': False,
            'message': 'Percorso non valido'
        }
    
    if not os.path.exists(percorso):
        return {
            'success': False,
            'message': f'File di backup non trovato: {filename}'
        }
    
    with _backup_lock:
        try:
            # Carica il backup
            print(f"[BACKUP] 🔄 Ripristino da: {filename}")
            
            with open(percorso, 'r', encoding='utf-8') as f:
                backup_data = json.load(f)
            
            headers = backup_data.get('headers', [])
            righe = backup_data.get('righe', [])
            
            if not headers or not righe:
                return {
                    'success': False,
                    'message': 'File di backup vuoto o corrotto'
                }
            
            # Connettiti a Google Sheets
            ws, _ = _get_worksheet()
            
            # Prepara i dati: header + righe
            tutti_i_dati = [headers] + righe
            
            # Pulisci il foglio e riscrivi tutto
            print(f"[BACKUP] 📤 Scrittura {len(righe)} righe sul FILONE...")
            
            # Ridimensiona il foglio se necessario
            ws.resize(rows=len(tutti_i_dati) + 10, cols=len(headers))
            
            # Pulisci tutto il foglio
            ws.clear()
            
            # Scrivi tutti i dati in batch (molto più veloce)
            # gspread supporta update con matrice di valori
            cell_range = f'A1:{gspread.utils.rowcol_to_a1(len(tutti_i_dati), len(headers))}'
            ws.update(cell_range, tutti_i_dati)
            
            print(f"[BACKUP] ✅ Ripristino completato: {len(righe)} righe scritte")
            
            return {
                'success': True,
                'message': f'Ripristino completato: {len(righe)} righe scritte sul FILONE',
                'righe_ripristinate': len(righe),
                'data_backup': backup_data.get('data_backup', ''),
            }
            
        except Exception as e:
            print(f"[BACKUP] ❌ Errore ripristino: {e}")
            traceback.print_exc()
            return {
                'success': False,
                'message': f'Errore durante il ripristino: {str(e)}'
            }


def elimina_backup(filename: str) -> dict:
    """
    Elimina un file di backup specifico.
    
    Args:
        filename: Nome del file di backup da eliminare
        
    Returns:
        dict con risultato
    """
    percorso = os.path.realpath(os.path.join(BACKUP_DIR, filename))
    if not percorso.startswith(os.path.realpath(BACKUP_DIR) + os.sep):
        return {
            'success': False,
            'message': 'Percorso non valido'
        }
    
    if not os.path.exists(percorso):
        return {
            'success': False,
            'message': f'File non trovato: {filename}'
        }
    
    try:
        os.remove(percorso)
        print(f"[BACKUP] 🗑️ Eliminato: {filename}")
        return {
            'success': True,
            'message': f'Backup eliminato: {filename}'
        }
    except Exception as e:
        return {
            'success': False,
            'message': f'Errore eliminazione: {str(e)}'
        }


def backup_periodico_auto():
    """
    Funzione da chiamare periodicamente (es. ad ogni sync).
    - Se è il primo del mese, pulisce i backup vecchi
    - Esegue il backup per oggi (se non già fatto)
    """
    try:
        # 1. Pulizia fine mese (se è il primo del mese)
        risultato_pulizia = pulisci_backup_fine_mese()
        if risultato_pulizia.get('cleaned'):
            print(f"[BACKUP] 🧹 Pulizia mensile: {risultato_pulizia.get('files_removed', 0)} file rimossi")
        
        # 2. Backup giornaliero (se non già fatto)
        risultato = esegui_backup()
        if risultato.get('skipped'):
            # Backup già presente, non fare nulla (silenzioso)
            pass
        elif risultato.get('success'):
            print(f"[BACKUP] ✅ Backup giornaliero completato")
        else:
            print(f"[BACKUP] ⚠️ Backup fallito: {risultato.get('message')}")
            
    except Exception as e:
        print(f"[BACKUP] ❌ Errore backup periodico: {e}")
