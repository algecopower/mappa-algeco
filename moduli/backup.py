# -*- coding: utf-8 -*-
"""
Modulo per backup e ripristino del filone (DB locale SQLite).

Post-disconnessione Google Sheets (2026-05-21) la fonte di verità è il DB
locale. Il backup giornaliero produce un dump JSON del filone direttamente
dal DB. Lo snapshot binario .db (VACUUM INTO) è gestito da `esegui_backup_db`.

Funzionalità:
- Backup giornaliero automatico (dump JSON dal DB)
- Pulizia automatica a fine mese
- Snapshot binario del DB SQLite (per restore rapidi)
"""
import os
import json
import threading
from datetime import datetime, date
import traceback

from .safe_json import json_read

from config import PROVINCIA_ATTIVA

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


def esegui_backup(data: date = None, forza: bool = False) -> dict:
    """
    Esegue il dump JSON del filone per la data specificata, leggendo dal DB locale.

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

            if not forza and os.path.exists(percorso):
                return {
                    'success': True,
                    'message': f'Backup già presente per {data.strftime("%d/%m/%Y")}',
                    'file': percorso,
                    'skipped': True
                }

            print(f"[BACKUP] 📦 Dump filone per {data.strftime('%d/%m/%Y')}...")

            from . import db as _db
            headers, rows = _db.dump_filone_as_rows(
                include_orphans=False,
                apply_pending_overlay=True,
            )
            if not headers:
                return {
                    'success': False,
                    'message': 'DB filone vuoto - nessun dato da salvare',
                    'file': None
                }

            righe = [[r.get(h, '') for h in headers] for r in rows]

            backup_data = {
                'provincia': PROVINCIA_ATTIVA,
                'data_backup': data.isoformat(),
                'timestamp': datetime.now().isoformat(),
                'source': 'db_local',
                'headers': headers,
                'righe': righe,
                'totale_righe': len(righe),
            }

            with open(percorso, 'w', encoding='utf-8') as f:
                json.dump(backup_data, f, ensure_ascii=False, indent=2)

            dimensione_mb = os.path.getsize(percorso) / (1024 * 1024)
            print(f"[BACKUP] ✅ Salvato: {percorso} ({dimensione_mb:.1f} MB, {len(righe)} righe)")

            return {
                'success': True,
                'message': f'Backup completato: {len(righe)} righe ({dimensione_mb:.1f} MB)',
                'file': percorso,
                'skipped': False,
                'righe': len(righe),
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
    """Restore non più supportato post-disconnessione Sheets.

    I file JSON in backup/ restano come storico ispezionabile a mano.
    Per ricostruire il DB da un backup binario .db (creato da
    `esegui_backup_db`), usa `sqlite3` direttamente: il file è un DB
    SQLite completo, basta copiarlo come `data/{provincia}/state.db`.
    """
    return {
        'success': False,
        'unsupported': True,
        'message': 'Restore del filone non più disponibile (Sheets disconnesso). '
                   'Per ripristinare uno stato precedente, sostituire '
                   'data/{provincia}/state.db con uno snapshot da backup/.',
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


def _percorso_backup_db(data: date = None) -> str:
    """Percorso del file di backup .db per la data indicata."""
    if data is None:
        data = date.today()
    cartella = os.path.dirname(_percorso_backup(data))
    return os.path.join(cartella, f'state_{data.isoformat()}.db')


def esegui_backup_db(data: date = None, forza: bool = False,
                     mantieni_ultimi: int = 7) -> dict:
    """Snapshot consistente del DB SQLite locale via VACUUM INTO.

    VACUUM INTO crea una copia transactional/atomica del DB sotto carico
    (non serve stop del processo). Richiede SQLite 3.27+.

    Args:
        data: data del backup (default: oggi)
        forza: se True, sovrascrive un backup esistente per la stessa data
        mantieni_ultimi: dopo il nuovo backup, mantieni solo gli ultimi N file
            (default 7 = una settimana di rolling backup)

    Returns:
        dict {success, file, dimensione_mb, skipped?, message?}
    """
    if data is None:
        data = date.today()
    try:
        _assicura_cartella_backup()
        percorso = _percorso_backup_db(data)

        if not forza and os.path.exists(percorso):
            return {'success': True, 'file': percorso, 'skipped': True,
                    'message': f'Backup DB già presente per {data.isoformat()}'}

        # Per sovrascrivere, VACUUM INTO non gestisce overwrite: rimuoviamo prima.
        if os.path.exists(percorso):
            os.remove(percorso)

        from moduli import db as _db
        conn = _db.get_conn()
        # Path quotato per SQLite (singoli apici, escape se necessario).
        safe_path = percorso.replace("'", "''")
        conn.execute(f"VACUUM INTO '{safe_path}'")

        dim_mb = round(os.path.getsize(percorso) / (1024 * 1024), 2)
        print(f"[BACKUP-DB] ✅ Snapshot salvato: {percorso} ({dim_mb} MB)")

        # Rotation: mantieni solo gli ultimi N file state_*.db
        rimossi = _rotate_backup_db(percorso, mantieni_ultimi)

        return {
            'success': True,
            'file': percorso,
            'dimensione_mb': dim_mb,
            'rotated_removed': rimossi,
        }
    except Exception as e:
        print(f"[BACKUP-DB] ❌ Errore: {e}")
        traceback.print_exc()
        return {'success': False, 'message': str(e), 'file': None}


def _rotate_backup_db(percorso_corrente: str, mantieni: int) -> int:
    """Cancella i backup .db più vecchi mantenendone solo `mantieni`.
    Ritorna il numero di file rimossi.
    """
    if mantieni <= 0:
        return 0
    cartella = os.path.dirname(percorso_corrente)
    if not os.path.isdir(cartella):
        return 0
    files = sorted(
        (f for f in os.listdir(cartella)
         if f.startswith('state_') and f.endswith('.db')),
        reverse=True,
    )
    da_eliminare = files[mantieni:]
    n = 0
    for f in da_eliminare:
        try:
            os.remove(os.path.join(cartella, f))
            n += 1
        except OSError as e:
            print(f"[BACKUP-DB] ⚠️ impossibile rimuovere {f}: {e}")
    if n:
        print(f"[BACKUP-DB] 🧹 rotation: {n} backup .db vecchi rimossi")
    return n


def backup_periodico_auto():
    """
    Funzione da chiamare periodicamente (es. ad ogni sync).
    - Se è il primo del mese, pulisce i backup vecchi
    - Esegue il backup JSON per oggi (se non già fatto)
    - Esegue lo snapshot DB SQLite per oggi (rotation: ultimi 7)
    """
    try:
        # 1. Pulizia fine mese (se è il primo del mese)
        risultato_pulizia = pulisci_backup_fine_mese()
        if risultato_pulizia.get('cleaned'):
            print(f"[BACKUP] 🧹 Pulizia mensile: {risultato_pulizia.get('files_removed', 0)} file rimossi")

        # 2. Backup JSON giornaliero (se non già fatto)
        risultato = esegui_backup()
        if risultato.get('skipped'):
            pass  # già presente, silenzioso
        elif risultato.get('success'):
            print("[BACKUP] ✅ Backup giornaliero completato")
        else:
            print(f"[BACKUP] ⚠️ Backup fallito: {risultato.get('message')}")

        # 3. Snapshot DB SQLite (recovery in caso di corruzione)
        risultato_db = esegui_backup_db()
        if risultato_db.get('skipped'):
            pass
        elif not risultato_db.get('success'):
            print(f"[BACKUP-DB] ⚠️ Snapshot DB fallito: {risultato_db.get('message')}")

    except Exception as e:
        print(f"[BACKUP] ❌ Errore backup periodico: {e}")
