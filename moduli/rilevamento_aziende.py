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
    r'\bS\.?\s*R\.?\s*L\.?\s*S\.?\b',     # SRLS, S.R.L.S., S R L S
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
    r'\bA\.?\s*S\.?\s*D\.?\b',           # ASD, A.S.D., A S D (Associazione Sportiva Dilettantistica)
    r'\bFONDAZIONE\b',
    r'\bA\.?\s*Z\.?\s*A\.?\s*G\.?\s*R\.?\b',  # AZAGR, A.Z.AGR., AZ AGR, A Z A G R (Azienda Agricola)
    r'\bIMPRESA\b',
    r'\bDITTA\b',
    r'\bDI\b',                           # DI (ditta individuale: "NOME DITTA DI NOME COGNOME")
    r'\bAZIENDA\b',
    r'\bINTERNATIONAL\b',
    r'\bMANAGEMENT\b',
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


def is_semidiretto(tipologia_contatore: str) -> bool:
    """
    Determina se la tipologia indica un contatore Semidiretto.
    I Semidiretti sono al 99% società/aziende.
    """
    if not tipologia_contatore:
        return False
    t = str(tipologia_contatore).upper()
    return 'SEMIDIRETTO' in t or 'SEMIDIRETTA' in t or 'SEMID' in t or 'SEMI DIRETTO' in t


# ============================================================================
# RILEVAMENTO AZIENDE
# ============================================================================

def is_azienda(nominativo: str, tipologia_contatore: str = '') -> bool:
    """
    Determina se un nominativo è un'azienda/ente (non persona fisica).

    Args:
        nominativo: nome cliente
        tipologia_contatore: tipo misuratore (opzionale). Se è un Semidiretto
            il contatore viene considerato azienda anche se il nominativo
            non contiene suffissi/prefissi tipici.
    """
    if is_semidiretto(tipologia_contatore):
        return True
    if not nominativo:
        return False
    return bool(_RE_AZIENDA.search(nominativo.strip()))


def classifica_tipo_contatto(nominativo: str, tipologia_contatore: str = '') -> str:
    """
    Classifica il tipo di contatto.

    Args:
        nominativo: nome cliente
        tipologia_contatore: tipo misuratore (opzionale). Se Semidiretto il
            contatto viene classificato come 'azienda' a prescindere dal
            nominativo.

    Returns:
        'azienda' | 'condominio' | 'ente_pubblico' | 'privato'
    """
    nome = (nominativo or '').strip()

    if nome and re.search(r'\bCONDOMINIO\b|\bCOND\.?\b', nome, re.IGNORECASE):
        return 'condominio'

    if nome and re.search(r'\bCOMUNE\s+DI\b|\bENTE\b|\bPARROCCHIA\b|\bOSPEDALE\b|\bASL\b|\bMINISTERO\b|\bIST(?:ITUTO)?\.?\b', nome, re.IGNORECASE):
        return 'ente_pubblico'

    if is_azienda(nome, tipologia_contatore):
        return 'azienda'

    return 'privato'


# ============================================================================
# ARRICCHIMENTO CONTATTI
# ============================================================================

def arricchisci_contatti(contatti: List[Dict]) -> List[Dict]:
    """
    Arricchisce una lista di contatti con tipo_contatto e is_azienda.

    Considera anche il campo 'tipologia_contatore' (se presente) per
    classificare automaticamente i Semidiretti come aziende.
    """
    for contatto in contatti:
        nome = contatto.get('nome', '')
        tipologia = contatto.get('tipologia_contatore', '')
        contatto['tipo_contatto'] = classifica_tipo_contatto(nome, tipologia)
        contatto['is_azienda'] = contatto['tipo_contatto'] != 'privato'

    return contatti
