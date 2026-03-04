# -*- coding: utf-8 -*-
"""
Rilevamento intelligente degli appuntamenti basato su regex.
"""
import re


# ============================================================================
# PATTERN REGEX PER CASI OVVI
# ============================================================================

# Pattern per appuntamento SICURO (app seguito da orario)
# Nota: per le fasce HH/HH richiediamo che sia un orario valido (6-20), non una data
PATTERN_APP_SICURO = re.compile(
    r'\bapp\.?\s*'                           # "app" o "app."
    r'(?:'
    r'\d{1,2}[:/.,]\d{2}'                    # orario HH:MM (es. 9:30, 10.00)
    r'|'
    r'(?:[6-9]|1[0-9]|20)\s*/\s*(?:[6-9]|1[0-9]|20)'  # fascia HH/HH con ore valide 6-20
    r'|'
    r'dalle?\s*\d{1,2}'                      # "dalle 9"
    r'|'
    r'ore?\s*\d{1,2}'                        # "ore 9"
    r')',
    re.IGNORECASE
)

# Pattern per orario seguito da "app"
PATTERN_ORARIO_APP = re.compile(
    r'(?:\d{1,2}[:/.,]\d{2}|\d{1,2}\s*/\s*\d{1,2})\s*app\b',
    re.IGNORECASE
)

# Pattern per "appuntamento" esplicito con orario
PATTERN_APPUNTAMENTO_ESPLICITO = re.compile(
    r'appuntamento\s*(?:alle?\s*)?\d{1,2}',
    re.IGNORECASE
)

# Pattern per ESCLUSIONE (falsi positivi comuni)
PATTERN_ESCLUSIONI = re.compile(
    r'(?:'
    r'appartamento|applicazione|apparecchio|'   # parole con "app"
    r'chiamare|fissare|'                         # da chiamare/fissare (non confermato)
    r'fissare\s+app|prendere\s+app|'            # app da fissare (non confermato)
    r'richiamare\s+per\s+app|'                  # da confermare
    r'chiedere\s+per|da\s+chiedere|'            # da chiedere (non confermato)
    r'no\s+app|niente\s+app|senza\s+app|'       # negazioni
    r'annullato|annullare|disdetto|disdire'     # cancellazioni
    r')',
    re.IGNORECASE
)

# Pattern per indicatori di appuntamento confermato
# NOTA: "parlato con" e "visto con" sono troppo generici - possono indicare
# che si è parlato per ESCLUDERE l'appuntamento, non per confermarlo
PATTERN_CONFERMATO = re.compile(
    r'(?:'
    r'confermato|ok\s+app|app\s+ok|'
    r'accordato\s+(?:per|alle?)?\s*\d|'  # accordato per/alle + orario
    r'fissato\s+(?:per|alle?)?\s*\d|'    # fissato per/alle + orario
    r'appuntamento\s+(?:per|alle?|il)\s*\d'  # appuntamento per/alle/il + orario
    r')',
    re.IGNORECASE
)

# Pattern per riconoscere DATE (da escludere come orari)
# Formato: DD/MM, DD-MM, DD.MM dove DD > 12 O MM > 12 (quindi è sicuramente una data)
PATTERN_DATA = re.compile(
    r'\b(?:'
    r'(?:1[3-9]|2\d|3[01])\s*[/\-\.]\s*(?:0?[1-9]|1[0-2])'  # DD/MM con DD > 12
    r'|'
    r'(?:0?[1-9]|1[0-2])\s*[/\-\.]\s*(?:1[3-9]|2\d|3[01])'  # MM/DD con DD > 12
    r')\b'
)

def _e_orario_valido(testo):
    """
    Verifica se un pattern numerico è un orario valido (non una data).
    Ritorna True solo se c'è un orario nel range 6:00-20:00.
    """
    # Cerca pattern HH:MM o HH.MM (orario preciso)
    for match in re.finditer(r'\b(\d{1,2})[:/.,](\d{2})\b', testo):
        ora = int(match.group(1))
        minuti = int(match.group(2))
        # Orario valido: 6:00-20:59
        if 6 <= ora <= 20 and 0 <= minuti <= 59:
            return True
    
    # Cerca pattern HH/HH (fascia oraria) - escludi se sembra una data
    for match in re.finditer(r'\b(\d{1,2})\s*/\s*(\d{1,2})\b', testo):
        num1 = int(match.group(1))
        num2 = int(match.group(2))
        # È una fascia oraria se entrambi sono ore valide (6-20)
        # e num2 > num1 (fascia crescente, es. 8/10)
        if 6 <= num1 <= 20 and 6 <= num2 <= 20 and num2 > num1:
            return True
    
    return False


def _rileva_regex(nota):
    """
    Rilevamento basato su regex.
    Ritorna: True (sicuro app), False (sicuro no app), None (ambiguo)
    """
    if not nota or not nota.strip():
        return False

    nota = nota.strip()

    # Step 1: "APP" all'inizio o come parola isolata è indicatore forte
    # (prevale sulle esclusioni successive nella nota)
    if re.match(r'^app\b', nota, re.IGNORECASE):
        return True  # "APP ..." all'inizio = appuntamento sicuro

    # Step 2: Pattern sicuri per appuntamento
    if PATTERN_APP_SICURO.search(nota):
        return True

    if PATTERN_ORARIO_APP.search(nota):
        return True

    if PATTERN_APPUNTAMENTO_ESPLICITO.search(nota):
        return True

    # Step 3: Indicatori di conferma CON ORARIO VALIDO (non date!)
    if PATTERN_CONFERMATO.search(nota):
        # Verifica se c'è un ORARIO valido (non una data)
        if _e_orario_valido(nota):
            return True

    # Step 4: Esclusioni (solo se non c'è già un "APP" chiaro)
    if PATTERN_ESCLUSIONI.search(nota):
        # Se non c'è "app" nella nota, sicuramente non è appuntamento
        if not re.search(r'\bapp\b', nota, re.IGNORECASE):
            return False
        # Se c'è "app" ma anche esclusione → ambiguo, serve AI
        return None

    # Step 5: Presenza di "app" senza pattern chiaro → ambiguo
    if re.search(r'\bapp\b', nota, re.IGNORECASE):
        return None  # Ambiguo, serve AI

    # Step 6: Nessun indicatore
    return False


def rileva_appuntamenti_batch(note_list, progress_callback=None):
    """
    Rileva appuntamenti per una lista di note.
    Ottimizzato per batch processing.

    Args:
        note_list: Lista di note
        progress_callback: Callback(i, total) per progress bar

    Returns:
        list[bool]: Lista di risultati
    """
    risultati = []
    casi_ambigui = []

    # Prima passata: regex
    for i, nota in enumerate(note_list):
        risultato = _rileva_regex(nota)
        risultati.append(risultato)
        if risultato is None:
            casi_ambigui.append(i)

    # Fallback per casi ambigui: se c'è un orario, considera appuntamento
    for i in casi_ambigui:
        nota = note_list[i]
        risultati[i] = re.search(r'\d{1,2}[:/.,]\d{2}', nota) is not None

    return risultati


# ============================================================================
# LOGICA PAVIA - Colonna "Ora pian"
# ============================================================================
# Per Pavia: è appuntamento se c'è un orario NON in formato "dalle x alle x"
# Il formato "dalle x alle x" indica comunicazioni cartacee (non appuntamenti veri)

# Pattern per formato "dalle x alle x" (comunicazione cartacea, NON appuntamento)
PATTERN_DALLE_ALLE = re.compile(
    r'dalle?\s*\d{1,2}(?:[:.]\d{2})?\s*alle?\s*\d{1,2}(?:[:.]\d{2})?',
    re.IGNORECASE
)

# Pattern per riconoscere qualsiasi orario (tutti i formati possibili)
PATTERN_ORARIO_GENERICO = re.compile(
    r'(?:'
    r'\d{1,2}[:/.,;]\d{2}\s+\d{1,2}[:/.,;]\d{2}'  # 8:00 9,00 (fascia con spazio)
    r'|'
    r'\d{1,2}[:/.,;]\d{2}'          # 8:30, 8.30, 8,30, 8;30
    r'|'
    r'\d{1,2}(?:[:.]\d{2})?\s*[/\-]\s*\d{1,2}(?:[:.]\d{2})?'  # 8/10, 8-10, 8:00/10:00, 8:00-10:00
    r'|'
    r'\d{1,2}[,.]\d{1}'             # 8,3 o 8.3 (formato rapido per 8:30)
    r'|'
    r'\b([6-9]|1[0-9]|20)\b'        # Numero singolo 6-20 (ore intere)
    r')'
)


def rileva_appuntamento_pavia(ora_pian):
    """
    Rileva se un valore dalla colonna "Ora pian" indica un appuntamento.

    Logica Pavia:
    - Se c'è un orario in formato "dalle x alle x" → NON è appuntamento (cartaceo)
    - Se c'è un orario in QUALSIASI altro formato → È appuntamento
    - Se non c'è orario → NON è appuntamento

    Formati riconosciuti come appuntamento:
    - 8:30, 8.30, 8,30, 8;30
    - 8,3 o 8.3 (formato rapido)
    - 8/10 o 8:00/10:00 (fascia)
    - 9 (ora intera)

    Args:
        ora_pian: Valore dalla colonna "Ora pian"

    Returns:
        bool: True se è un appuntamento
    """
    if not ora_pian or str(ora_pian).strip() == '':
        return False

    testo = str(ora_pian).strip()

    # Se è nel formato "dalle x alle x" → NON è appuntamento
    if PATTERN_DALLE_ALLE.search(testo):
        return False

    # Se c'è qualsiasi altro formato di orario → È appuntamento
    if PATTERN_ORARIO_GENERICO.search(testo):
        return True

    return False


def estrai_orario_pavia(ora_pian):
    """
    Estrae l'orario dalla colonna "Ora pian" di Pavia.

    Gestisce formati speciali come:
    - 8,3 → 08:30
    - 8.3 → 08:30
    - 9 → 09:00

    Args:
        ora_pian: Valore dalla colonna "Ora pian"

    Returns:
        str: Orario in formato standard (es. "08:30" o "08:00-10:00") o None
    """
    if not ora_pian or str(ora_pian).strip() == '':
        return None

    testo = str(ora_pian).strip()

    # Se è formato "dalle x alle x", non estrarre (non è appuntamento)
    if PATTERN_DALLE_ALLE.search(testo):
        return None

    # === FASCE ORARIE (controllare PRIMA degli orari singoli) ===

    # Formato fascia con spazio: "8:00 9,00" o "8:00 9:00" (due orari separati da spazio)
    match = re.search(r'(\d{1,2})[:/.,;](\d{2})\s+(\d{1,2})[:/.,;](\d{2})', testo)
    if match:
        ora1, min1 = int(match.group(1)), int(match.group(2))
        ora2, min2 = int(match.group(3)), int(match.group(4))
        if 6 <= ora1 <= 20 and 6 <= ora2 <= 20:
            return f"{ora1:02d}:{min1:02d}-{ora2:02d}:{min2:02d}"

    # Formato fascia HH/HH, HH-HH, HH:MM/HH:MM, HH:MM-HH:MM
    match = re.search(r'(\d{1,2})(?:[:.:](\d{2}))?\s*[/\-]\s*(\d{1,2})(?:[:.:](\d{2}))?', testo)
    if match:
        ora1 = int(match.group(1))
        min1 = int(match.group(2)) if match.group(2) else 0
        ora2 = int(match.group(3))
        min2 = int(match.group(4)) if match.group(4) else 0
        if 6 <= ora1 <= 20 and 6 <= ora2 <= 20:
            return f"{ora1:02d}:{min1:02d}-{ora2:02d}:{min2:02d}"

    # === ORARI SINGOLI ===

    # Formato rapido: 8,3 o 8.3 → 8:30
    match = re.search(r'(\d{1,2})[,.](\d{1})\b', testo)
    if match:
        ore = int(match.group(1))
        decimi = int(match.group(2))
        # Converti decimi in minuti (3 → 30, 5 → 50, ecc.)
        minuti = decimi * 10
        if 6 <= ore <= 20 and minuti < 60:
            return f"{ore:02d}:{minuti:02d}"

    # Formato standard HH:MM
    match = re.search(r'(\d{1,2})[:/.,;](\d{2})', testo)
    if match:
        ore, minuti = int(match.group(1)), int(match.group(2))
        if 0 <= ore <= 23 and 0 <= minuti <= 59:
            return f"{ore:02d}:{minuti:02d}"

    # Ora intera singola (es. "9" → "09:00")
    match = re.search(r'\b([6-9]|1[0-9]|20)\b', testo)
    if match:
        ore = int(match.group(1))
        return f"{ore:02d}:00"

    return None


def rileva_appuntamenti_batch_pavia(ora_pian_list):
    """
    Rileva appuntamenti per una lista di valori "Ora pian" (logica Pavia).

    Args:
        ora_pian_list: Lista di valori dalla colonna "Ora pian"

    Returns:
        list[bool]: Lista di risultati
    """
    return [rileva_appuntamento_pavia(v) for v in ora_pian_list]
