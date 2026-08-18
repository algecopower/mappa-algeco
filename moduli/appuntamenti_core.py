# -*- coding: utf-8 -*-
"""
Core unificato per il rilevamento degli appuntamenti.
Utilizzato da: dashboard, reportistica, mappa e PDF percorsi.
"""
import re
import pandas as pd

# Pattern POSITIVO: la parola "APP" (case-insensitive) deve essere all'inizio della nota (^\W*app\b)
_PAT_APP_TOKEN = re.compile(r'^\W*app\b', re.IGNORECASE)


def motivo_non_appuntamento(testo: str) -> str | None:
    """
    Ritorna il motivo per cui il testo NON è considerato un appuntamento.
    Ritorna None se INVECE è un appuntamento valido (inizia con 'APP').
    """
    if testo is None or pd.isna(testo):
        return 'vuoto'
    
    s = str(testo).strip()
    if not s:
        return 'vuoto'
        
    # Deve iniziare con 'APP' (case-insensitive)
    if not _PAT_APP_TOKEN.search(s):
        return 'manca pattern base (^app)'
        
    return None


def rileva_appuntamento(testo: str) -> bool:
    """
    Ritorna True se il testo rappresenta un appuntamento.
    Un contatore è un appuntamento se e solo se la nota inizia con 'APP' (case-insensitive).
    Nessuna blacklist di parole (es. 'chiamare', 'da rifissare', ecc.).
    """
    return motivo_non_appuntamento(testo) is None

