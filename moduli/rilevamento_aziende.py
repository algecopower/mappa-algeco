# -*- coding: utf-8 -*-
"""
Modulo per il rilevamento automatico di aziende/enti dai nominativi.

Funzionalità:
- Rilevamento nominativi aziendali (SRL, SPA, condomini, ecc.)
  con gestione robusta di punti, spazi e varianti tipografiche
- Classificazione tipo contatto (azienda, condominio, ente_pubblico, privato)
- Arricchimento contatti con tipo_contatto e is_azienda
"""
import re
import logging
from typing import Dict, List

logger = logging.getLogger('mappa_algeco')

# ============================================================================
# PATTERN PER RILEVAMENTO AZIENDE
# ============================================================================

# Suffissi tipici di aziende/società italiane.
# Ogni pattern gestisce: attaccato (SRL), con punti (S.R.L.), con spazi (S R L),
# con punti e spazi (S. R. L.), maiuscolo/minuscolo.
_SUFFISSI_AZIENDA = [
    r'\bS\.?\s*R\.?\s*L\.?\b',           # SRL, S.R.L., S R L, S. R. L.
    r'\bS\.?\s*P\.?\s*A\.?\b',           # SPA, S.P.A., S P A, S. P. A.
    r'\bS\.?\s*A\.?\s*S\.?\b',           # SAS, S.A.S., S A S
    r'\bS\.?\s*N\.?\s*C\.?\b',           # SNC, S.N.C., S N C
    r'\bS\.?\s*S\.?\b',                  # SS, S.S. (società semplice)
    r'\bS\.?\s*C\.?\s*A\.?\s*R\.?\s*L\.?\b',  # SCARL, S.C.A.R.L.
    r"\bSOC(?:IETA)?['\s.]?\b",          # SOC., SOCIETA', SOCIETA
    r'\bCOOP(?:ERATIVA)?\.?\b',          # COOP, COOPERATIVA
    r'\bCONSORZIO\b',
    r'\bASSOCIAZIONE\b',
    r'\bFONDAZIONE\b',
    r'\bIMPRESA\b',
    r'\bDITTA\b',
    r'\bDI\b',                           # DI (ditta individuale)
    r'\bAZIENDA\b',
    r'\bINTERNATIONAL\b',
]

# Prefissi che indicano un ente/condominio (non persona fisica)
_PREFISSI_ENTE = [
    r'\bCONDOMINIO\b',
    r'\bCOND\.?\b',
    r'\bAMMINISTRAZ(?:IONE)?\b',
    r'\bAMM\.?\b',
    r'\bENTE\b',
    r'\bCOMUNE\s+DI\b',
    r'\bPARROCCHIA\b',
    r'\bIST(?:ITUTO)?\.?\b',
    r'\bOSPEDALE\b',
    r'\bASL\b',
    r'\bMINISTERO\b',
    r'\bFARMACIA\b',
    r'\bALBERGO\b',
    r'\bHOTEL\b',
    r'\bRIST(?:ORANTE)?\.?\b',
    r'\bBAR\b',
    r'\bNEGOZIO\b',
    r'\bSUPERMERCATO\b',
    r'\bCENTRO\s+COMMERCIALE\b',
    r'\bSTUDIO\b',
    r'\bFILIALE\b',
    r'\bAGENZIA\b',
    r'\bB\s*&\s*B\b',
    r'\bBED\s+AND\s+BREAKFAST\b',
]

# Pattern compilato per efficienza
_RE_AZIENDA = re.compile(
    '|'.join(_SUFFISSI_AZIENDA + _PREFISSI_ENTE),
    re.IGNORECASE
)


# ============================================================================
# RILEVAMENTO AZIENDE
# ============================================================================

def is_azienda(nominativo: str) -> bool:
    """
    Determina se un nominativo è un'azienda/ente (non persona fisica).
    """
    if not nominativo:
        return False
    return bool(_RE_AZIENDA.search(nominativo.strip()))


def classifica_tipo_contatto(nominativo: str) -> str:
    """
    Classifica il tipo di contatto.

    Returns:
        'azienda' | 'condominio' | 'ente_pubblico' | 'privato'
    """
    if not nominativo:
        return 'privato'

    nome = nominativo.strip()

    if re.search(r'\bCONDOMINIO\b|\bCOND\.?\b', nome, re.IGNORECASE):
        return 'condominio'

    if re.search(r'\bCOMUNE\s+DI\b|\bENTE\b|\bPARROCCHIA\b|\bOSPEDALE\b|\bASL\b|\bMINISTERO\b|\bIST(?:ITUTO)?\.?\b', nome, re.IGNORECASE):
        return 'ente_pubblico'

    if is_azienda(nome):
        return 'azienda'

    return 'privato'


# ============================================================================
# ARRICCHIMENTO CONTATTI
# ============================================================================

def arricchisci_contatti(contatti: List[Dict]) -> List[Dict]:
    """
    Arricchisce una lista di contatti con tipo_contatto e is_azienda.
    """
    for contatto in contatti:
        nome = contatto.get('nome', '')
        contatto['tipo_contatto'] = classifica_tipo_contatto(nome)
        contatto['is_azienda'] = contatto['tipo_contatto'] != 'privato'

    return contatti
