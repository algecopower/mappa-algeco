# -*- coding: utf-8 -*-
"""
Modulo per il rilevamento automatico di aziende/enti e generazione
link di ricerca PEC.

Funzionalità:
- Rilevamento nominativi aziendali (SRL, SPA, condomini, ecc.)
  con gestione robusta di punti, spazi e varianti tipografiche
- Classificazione tipo contatto (azienda, condominio, ente, privato)
- Generazione link ricerca Google e Ufficio Camerale

Fonti di ricerca (aperte nel browser dell'utente):
- Google Search - ricerca generica "nome azienda PEC"
- Ufficio Camerale (ufficiocamerale.it) - Cerca PEC per Partita IVA
"""
import re
import logging
from typing import Dict, List
from urllib.parse import quote_plus, urlencode

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

    Gestisce tutte le varianti tipografiche:
    - SRL, S.R.L., S.r.l., S R L, S. R. L.
    - SPA, S.P.A., S.p.A., S P A, S. P. A.
    - CONDOMINIO, FARMACIA, HOTEL, ecc.

    Args:
        nominativo: Nome del contatto

    Returns:
        True se sembra un'azienda, condominio, ente, ecc.
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

    # Condominio
    if re.search(r'\bCONDOMINIO\b|\bCOND\.?\b', nome, re.IGNORECASE):
        return 'condominio'

    # Ente pubblico
    if re.search(r'\bCOMUNE\s+DI\b|\bENTE\b|\bPARROCCHIA\b|\bOSPEDALE\b|\bIST(?:ITUTO)?\.?\b', nome, re.IGNORECASE):
        return 'ente_pubblico'

    # Azienda
    if is_azienda(nome):
        return 'azienda'

    return 'privato'


# ============================================================================
# GENERAZIONE LINK DI RICERCA
# ============================================================================

def genera_link_ufficio_camerale() -> str:
    """
    Genera il link alla homepage di Ufficio Camerale.
    La ricerca avviene per Partita IVA o Ragione Sociale + Provincia.

    Returns:
        URL homepage Ufficio Camerale
    """
    return "https://www.ufficiocamerale.it"


def genera_link_google_pec(denominazione: str, comune: str = '') -> str:
    """
    Genera link Google per cercare la PEC di un'azienda.

    Args:
        denominazione: Nome azienda
        comune: Comune (opzionale, migliora la ricerca)

    Returns:
        URL di ricerca Google
    """
    query = f'"{denominazione}" PEC'
    if comune:
        query += f' {comune}'
    return f"https://www.google.com/search?q={quote_plus(query)}"


def genera_tutti_link_ricerca(nominativo: str, comune: str = '') -> Dict[str, str]:
    """
    Genera tutti i link di ricerca disponibili per un contatto.

    Args:
        nominativo: Nome del contatto
        comune: Comune (opzionale)

    Returns:
        Dict con chiavi: 'google_pec', 'ufficio_camerale'
    """
    tipo = classifica_tipo_contatto(nominativo)

    links = {
        'google_pec': genera_link_google_pec(nominativo, comune),
    }

    if tipo in ('azienda', 'condominio', 'ente_pubblico'):
        links['ufficio_camerale'] = genera_link_ufficio_camerale()

    return links


def _pulisci_nome_per_ricerca(nome: str) -> str:
    """Pulisce il nome per una ricerca più efficace."""
    if not nome:
        return ''

    pulito = nome.strip()
    # Rimuovi prefissi generici
    pulito = re.sub(
        r'^(SOC\.|SOCIETA\'?\s*|IMPRESA\s+|DITTA\s+)',
        '', pulito, flags=re.IGNORECASE
    ).strip()

    return pulito


# ============================================================================
# ARRICCHIMENTO CONTATTI (solo tipo + link, niente cache)
# ============================================================================

def arricchisci_contatti(contatti: List[Dict]) -> List[Dict]:
    """
    Arricchisce una lista di contatti con info tipo e link di ricerca PEC.
    NON usa cache — aggiunge solo il rilevamento azienda e i link.

    Args:
        contatti: Lista di contatti (dicts con 'nome', 'comune', 'pod')

    Returns:
        Lista di contatti arricchiti con campi 'tipo_contatto', 'is_azienda',
        'link_pec'
    """
    for contatto in contatti:
        nome = contatto.get('nome', '')
        comune = contatto.get('comune', '')

        # Classifica tipo contatto
        contatto['tipo_contatto'] = classifica_tipo_contatto(nome)
        contatto['is_azienda'] = contatto['tipo_contatto'] != 'privato'

        # Genera link di ricerca per aziende/enti
        if contatto['is_azienda']:
            contatto['link_pec'] = genera_tutti_link_ricerca(nome, comune)
        else:
            contatto['link_pec'] = {}

    return contatti
