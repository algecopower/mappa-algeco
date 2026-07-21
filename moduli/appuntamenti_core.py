# -*- coding: utf-8 -*-
"""
Core unificato per il rilevamento degli appuntamenti.
Utilizzato da: dashboard, reportistica, mappa e PDF percorsi.
"""
import re
import pandas as pd

# Pattern POSITIVO: "app" deve essere la prima parola (^\W*)
_PAT_APP_TOKEN = re.compile(r'^\W*app\b', re.IGNORECASE)

# Pattern NEGATIVI storici (percorso.py): se uno di questi matcha, NON è un app.
_PAT_APP_ESCLUSIONI_DEFAULT = [
    # Negazioni dirette
    (re.compile(r'\b(?:no|non|nessun[ao]?|niente|senza)\s+app\b', re.IGNORECASE),
     'negazione'),
    # App seguita da verbi di cancellazione/spostamento/sospensione
    (re.compile(r'\bapp\b[\s,;:.\-]*(?:annull|cancell|salt|rinvi|spost|sospes|disdet|elimin|rimoss)', re.IGNORECASE),
     'annullato/spostato'),
    # App seguita da punto interrogativo (incertezza esplicita)
    (re.compile(r'\bapp\b\s*\?+', re.IGNORECASE),
     'incerto (?)'),
    # App preceduta o seguita da "da fissare/prendere..."
    (re.compile(r'\bapp\b\s+da\s+(?:fiss|prend|concord|conferm|defin|stabil|programmar|richied)', re.IGNORECASE),
     'da fissare'),
    (re.compile(r'\bda\s+(?:fiss|prend|concord|conferm|defin|stabil|programmar|richied)\w*\s+(?:l[\'aei]?\s+)?app\b', re.IGNORECASE),
     'da fissare'),
    # Verbi/sostantivi di richiesta non confermata
    (re.compile(r'\b(?:richiest|richied|chiest|chied|vuole|desider|vorrebbe|preferirebbe|gradirebbe|cerca|cercando)\w*\s+(?:un[\'a]?\s+)?app\b', re.IGNORECASE),
     'richiesta non confermata'),
    # Modalita' ipotetica
    (re.compile(r'\b(?:forse|magari|eventualmente|probabilmente|circa|se\s+possibile)\s+(?:un[\'a]?\s+)?app\b', re.IGNORECASE),
     'ipotetico'),
]

_PAT_APP_ESCLUSIONI_EXTRA = []
try:
    import config as _config_module
    _extra_raw = getattr(_config_module, 'APPUNTAMENTO_PATTERN_ESCLUSIONE', None)
    if _extra_raw:
        for _p in _extra_raw:
            try:
                _PAT_APP_ESCLUSIONI_EXTRA.append(
                    (re.compile(_p, re.IGNORECASE), 'config')
                )
            except re.error:
                pass
except Exception:
    pass


def motivo_non_appuntamento(testo: str) -> str | None:
    """
    Ritorna il motivo per cui il testo NON è considerato un appuntamento.
    Ritorna None se INVECE è un appuntamento valido.
    """
    if testo is None:
        return 'vuoto'
    if pd.isna(testo):
        return 'vuoto'
    
    s = str(testo).strip()
    if not s:
        return 'vuoto'
        
    # 1. Deve iniziare con 'app'
    if not _PAT_APP_TOKEN.search(s):
        return 'manca pattern base (^app)'
        
    # 2. Non deve contenere la parola "chiamare"
    if 'chiamare' in s.lower():
        return 'contiene "chiamare"'
        
    # 3. Controllo esclusioni (annullati, ecc.)
    for pat, motivo in _PAT_APP_ESCLUSIONI_EXTRA + _PAT_APP_ESCLUSIONI_DEFAULT:
        if pat.search(s):
            return motivo
            
    return None


def rileva_appuntamento(testo: str) -> bool:
    """
    Ritorna True se il testo rappresenta un appuntamento confermato.
    Verifica centralizzata usata in tutto il gestionale.
    """
    return motivo_non_appuntamento(testo) is None
