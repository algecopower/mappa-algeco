# -*- coding: utf-8 -*-
"""
Modulo per il rilevamento automatico di aziende/enti dai nominativi e note operative.

Funzionalità:
- Rilevamento nominativi aziendali (SRL, SPA, condomini, ditte individuali, ecc.)
  con gestione robusta di punti, spazi e varianti tipografiche
- Risoluzione contestuale della preposizione 'DI' (distinzione tra ditte
  individuali/insegne e cognomi patronimici/note di persone fisiche)
- Analisi delle note operative (esclusione relazioni di parentela es. 'moglie di',
  rilevamento attività commerciali e sedi aziendali segnalate nelle note)
- Classificazione tipo contatto (azienda, condominio, ente_pubblico, privato)
- Arricchimento contatti con tipo_contatto e is_azienda
"""
import re
import logging
from typing import Dict, List

logger = logging.getLogger('mappa_algeco')

# ============================================================================
# PATTERN PER RILEVAMENTO AZIENDE ED ENTI
# ============================================================================

# Suffissi tipici di aziende/società italiane.
# Ogni pattern gestisce: attaccato (SRL), con punti (S.R.L.), con spazi (S R L),
# con punti e spazi (S. R. L.), maiuscolo/minuscolo.
_SUFFISSI_AZIENDA = [
    r'\bS\.?\s*R\.?\s*L\.?\s*S\.?\b',          # SRLS, S.R.L.S., S R L S
    r'\bS\.?\s*R\.?\s*L\.?\b',                # SRL, S.R.L., S R L, S. R. L.
    r'\bS\.?\s*P\.?\s*A\.?\b',                # SPA, S.P.A., S P A, S. P. A.
    r'\bS\.?\s*A\.?\s*S\.?\b',                # SAS, S.A.S., S A S
    r'\bS\.?\s*N\.?\s*C\.?\b',                # SNC, S.N.C., S N C
    r'\bS\.?\s*S\.?\b',                       # SS, S.S. (società semplice)
    r'\bS\.?\s*C\.?\s*A\.?\s*R\.?\s*L\.?\b',  # SCARL, S.C.A.R.L.
    r"\bSOC(?:IETA)?['\s.]?\b",               # SOC., SOCIETA', SOCIETA
    r'\bCOOP(?:ERATIVA)?\.?\b',               # COOP, COOPERATIVA
    r'\bCONSORZIO\b',
    r'\bASSOCIAZIONE\b',
    r'\bA\.?\s*S\.?\s*D\.?\b',                # ASD, A.S.D., A S D (Sportiva Dilettantistica)
    r'\bFONDAZIONE\b',
    r'\bA\.?\s*Z\.?\s*A\.?\s*G\.?\s*R\.?\b',  # AZAGR, A.Z.AGR., AZ AGR
    r'\bAZ\.?\s*AGR?(?:ICOLA)?\.?\b',         # AZ. AGR., AZ AGR, AZIENDA AGRICOLA
    r'\bIMPRESA\b',
    r'\bDITTA\b',
    r'(?:^|\b)D\.\s*I\.(?:\s|$)',             # D.I. esplicito puntato (Ditta Individuale)
    r'\bDITTA\s+INDIVIDUALE\b',
    r'\bAZIENDA\b',
    r'\bINTERNATIONAL\b',
    r'\bMANAGEMENT\b',
    r'\bHOLDING\b',
    r'\bGROUP\b',
    r'\bSERVICE[S]?\b',
]

# Prefissi che indicano un ente pubblico, ecclesiastico, assistenziale o condominio
_PREFISSI_ENTE = [
    r'\bCONDOMINIO\b',
    r'\bCOND\.?\b',
    r'\bAMMINISTRAZ(?:IONE)?\b',
    r'\bAMM\.?\b',
    r'\bENTE\b',
    r'\bCOMUNE\s+(?:CITTA\'?\s+)?DI\b',
    r'\bPROVINCIA\s+(?:AUTONOMA\s+)?DI\b',
    r'\bCITTA\'?\s+(?:METROPOLITANA\s+)?DI\b',
    r'\bREGIONE\b',
    r'\bPARROCCHIA\b',
    r'\bCHIESA\s+(?:DI\b|PARROCCHIALE|CRISTIANA|EVANGELICA|BATTISTA|CATTOLICA|ORTODOSSA|DEI\b|DEL\b|DELLA\b|DELLO\b|SAN\b|SANT[A\']\b)',
    r'\bDIOCESI\b',
    r'\bCURIA\b',
    r'\bSANTUARIO\b',
    r'\bMONASTERO\b',
    r'\bCONVENTO\b',
    r'\bIST(?:ITUTO)?\.?\b',
    r'\bOSPEDALE\b',
    r'\bASL\b',
    r'\bMINISTERO\b',
    r'\bPREFETTURA\b',
    r'\bQUESTURA\b',
    r'\bUNIVERSIT[AÀ]\b',
    r'\bCASA\s+DI\s+RIPOSO\b',
    r'\bORDINE\s+D(?:EI|ELLE|EGLI|I)\b',
    r'\bCROCE\s+(?:ROSSA|VERDE|BIANCA)\b',
    r'\bPUBBLICA\s+ASSISTENZA\b',
    r'\bUNIONE\s+(?:DI\s+)?COMUNI\b',
    r'\bCAMERA\s+DI\s+COMMERCIO\b',
    r'\bGUARDIA\s+DI\s+FINANZA\b',
    r'\bCARABINIERI\b',
    r'\bPOLIZIA\b',
    r'\bVIGILI\s+DEL\s+FUOCO\b',
    r'\bCOMUNIT[AÀ]\b',
    r'\bSCUOLA\b',
    r'\bCONVITTO\b',
    r'\bLICEO\b',
    r'\bCOLLEGIO\b',
    r'\bONLUS\b',
    r'\bO\.?\s*N\.?\s*L\.?\s*U\.?\s*S\.?\b',
    r'\bAPS\b',
    r'\bA\.?\s*P\.?\s*S\.?\b',
    r'\bODV\b',
    r'\bO\.?\s*D\.?\s*V\.?\b',
]

# Prefissi e parole chiave che indicano chiaramente un'attività commerciale/artigianale
_PREFISSI_COMMERCIALI = [
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
    r'\b(?:LAVORAZIONI|MECCANIC(?:A|HE|I)|OFFICINA|AUTOFFICINA|PANIFICIO|PIZZERIA|PASTICCERIA|MACELLERIA|GASTRONOMIA|ROSTICCERIA|FORNO|FORNAIO)\b',
    r'\b(?:BARBER|BEAUTY|SALON|PARRUCCHIERE|PARRUCCHIERA|ACCONCIATUR(?:E|A)|ESTETICA)\b',
    r'\b(?:LABORATORIO|TIPOGRAFIA|TIPOLITOGRAFIA|STAZIONE\s+(?:DI\s+)?SERVIZIO|DISTRIBUTORE|CARROZZERIA|AUTORIPARAZIONI|GOMMISTA|ELETTRAUTO)\b',
    r'\b(?:TERMOIDRAULICA|IDRAULICA|EDIL(?:IZIA)?|SERRAMENTI|VETRERIA|FALEGNAMERIA|ARREDAMENTI)\b',
    r'\b(?:COMMERCIALE|LOGISTICA|TRASPORTI|SPEDIZIONI|VIAGGI|VACANZE|IMMOBILIARE)\b',
    r'\b(?:STORE|MARKET|MINIMARKET|SHOP|OUTLET|BOUTIQUE|TATTOO)\b',
    r'\b(?:CAFFE|TRATTORIA|OSTERIA|PIADINERIA|PUB|BIRRERIA|GELATERIA)\b',
    r'\b(?:TABACCHI|TABACCHERIA|EDICOLA|LAVANDERIA|TINTORIA|FERRAMENTA|VIVAIO|FIORI)\b',
    r'\b(?:AGRICOLA|AGRITURISMO|APICOLTURA|ALLEVAMENTO|CANTINA|ENOTECA|PETROL[II]|VIGILANZA|SICUREZZA|PULIZIE|IMPIANTI|COSTRUZIONI)\b',
    r'\b(?:OLEODINAMICA|PRODENTAL|CARTOBOX|AUTOZETA|IDEALTERM|MARMOUTENSILI)\b',
]

# Pattern per formule societarie o titolarità dopo "DI"
_RE_SOCIETA_DI = re.compile(
    r'\bDI\s+[^,]+(?:\s+E\s+C\.?|\s+&\s+C\.?|\s+E\s+FIGLI|\s+&\s+FIGLI|\s+E\s+FRATELLI|\s+&\s+FRATELLI)\b',
    re.IGNORECASE
)

# Relazioni di parentela o note operative (nel nominativo o nelle colonne note)
_RE_PARENTELA_RELAZIONI = re.compile(
    r'\b(?:COGNAT[AO]|MOGLIE|MARITO|FIGLI[AO]|PADRE|MADRE|SUOCER[AO]|FRATELLO|SORELLA|NIPOTE|ERED[IE]|PER\s+CONTO|ATTIVIT[AÀ]|DELEGAT[AO]|ASSISTENTE|INQUILIN[AO]|BADANTE|FAMILIARE)\s+DI\b'
    r'|\b(?:PARLATO\s+CON\s+(?:LA\s+)?MOGLIE|PARLATO\s+CON\s+(?:IL\s+)?MARITO|PARLATO\s+CON\s+(?:IL\s+|LA\s+)?FIGLI[AO]|PARLATO\s+CON\s+(?:IL\s+|LA\s+)?COGNAT[AO]|ABITA\s+CON|NUMERO\s+DEL(?:LA)?\s+MOGLIE|NUMERO\s+DEL\s+MARITO)\b',
    re.IGNORECASE
)

# Rilevamento attività commerciali/professionali esplicite nella colonna Note
# (es. 'CITOFONARE STUDIO MEDICO', 'SEDE AZIENDA', 'ATTIVITÀ COMMERCIALE')
_RE_NOTE_COMMERCIALE = re.compile(
    r'\b('
    r'DITTA\s+INDIVIDUALE|(?:^|\b)D\.\s*I\.(?:\s|$)|SEDE\s+AZIEND(?:A|ALE)|ATTIVIT[AÀ]\s+COMMERCIALE|'
    r'CITOFONARE\s+(?:STUDIO|NEGOZIO|OFFICINA|BAR|DITTA|FARMACIA|LABORATORIO)|'
    r'(?:ATTIVIT[AÀ]|DITTA|IMPRESA)\s+DI\b|'
    r'STUDIO\s+(?:MEDICO|LEGALE|TECNICO|NOTARILE|ASSOCIATO|COMMERCIALISTI|DOTT\b)|'
    r'AMBULATORIO|CAPANNONE\s+AZIENDALE'
    r')\b',
    re.IGNORECASE
)

# Pulizia di recapiti telefonici, email e note descrittive per l'analisi contestuale
_RE_PULIZIA_NOMINATIVO = re.compile(
    r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b'
    r'|(?:\+39\s*)?(?:\b\d[\d\s/-]{5,}\d\b)'
    r'|\s+//.*$'
    r'|\s+(?:chiuso|errato|sig\.|sig\.ra)\b.*$'
    , re.IGNORECASE
)

# Nomi di persona italiani comuni per distinguere nominativi del tipo
# '<Nome> DI <Cognome>' o '<Nome1> <Nome2> DI <Cognome>' da ditte individuali
_NOMI_PROPRII_COMUNI = {
    'MARIA', 'GIUSEPPE', 'ANTONIO', 'GIOVANNI', 'FRANCESCO', 'MARIO', 'LUIGI', 'SALVATORE',
    'ANGELO', 'VINCENZO', 'PIETRO', 'PAOLO', 'CARMELO', 'CARMINE', 'DOMENICO', 'FRANCO',
    'ROBERTO', 'STEFANO', 'MARCO', 'ALESSANDRO', 'DANIELE', 'ANDREA', 'MICHELE', 'MAURIZIO',
    'CLAUDIO', 'MASSIMO', 'GIORGIO', 'LUCA', 'FABIO', 'ALBERTO', 'ROSARIO', 'SERGIO',
    'PASQUALE', 'GAETANO', 'COSIMO', 'ALDO', 'BRUNO', 'ENRICO', 'CARLO', 'VITO', 'CIRO',
    'LEONARDO', 'GIANLUCA', 'EMANUELE', 'FEDERICO', 'LORENZO', 'MATTEO', 'DAVIDE', 'SIMONE',
    'ALFIO', 'ORAZIO', 'ANNA', 'ROSA', 'CARMELA', 'TERESA', 'LUCIA', 'GIUSEPPINA', 'ANGELA',
    'GIOVANNA', 'FRANCESCA', 'RITA', 'CATERINA', 'MARGHERITA', 'ELENA', 'CONCETTA', 'PAOLA',
    'PATRIZIA', 'SILVIA', 'LAURA', 'SIMONA', 'DANIELA', 'CRISTINA', 'BARBARA', 'MONICA',
    'ELISABETTA', 'VALENTINA', 'ELEONORA', 'CHIARA', 'SARA', 'FEDERICA', 'ALESSIA', 'MARTINA',
    'GIULIA', 'CLAUDIA', 'MARISA', 'CINZIA', 'LOREDANA', 'GRAZIA', 'FILOMENA', 'ROSALBA',
    'CARMELINA', 'DIANA', 'DALILA', 'AMELIA', 'SAMANTHA', 'VALERIA', 'MARIANO', 'NAZARIO',
    'AUGUSTO', 'SANTO', 'CALOGERO', 'TIZIANA', 'VINCENZA', 'GUIDO', 'LUCIANO', 'GIACOMO',
    'CORRADO', 'ALFONSINA', 'ANTONELLA', 'ROSANNA', 'GABRIELLA', 'MARCELLA', 'MASSIMILIANO',
    'GIOACCHINO', 'SEBASTIANO', 'ORNELLA', 'ALFONSO', 'FERNANDO', 'REMEDIOS', 'ALICE', 'ALIDA',
    'ARIANNA', 'AURORA', 'BEATRICE', 'BENEDETTA', 'CAMILLA', 'CARLOTTA', 'CECILIA', 'ELISA',
    'EMMA', 'FABRIZIO', 'FILIPPO', 'GIADA', 'GINEVRA', 'GRETA', 'ILARIA', 'IRENE', 'LARA',
    'LIDIA', 'LILIANA', 'LISA', 'LUCREZIA', 'MANUELA', 'MARTA', 'MELISSA', 'MIRIAM', 'NADIA',
    'NOEMI', 'RACHELE', 'REBECCA', 'SABRINA', 'SERENA', 'SOFIA', 'STEFANIA', 'VANESSA',
    'VERONICA', 'VIOLA', 'VITTORIA', 'GIULIO', 'CESARE', 'ATTILIO', 'ERMINIO', 'FELICE',
    'VALERIO', 'IVAN', 'MARIATERESA', 'PIERLUIGI', 'PIERPAOLO', 'GIAMPIERO', 'GIANFRANCO',
    'MAURO', 'ROSI', 'ROSY', 'CARLA', 'CARMINA', 'DOMENICA', 'VITTORIO', 'GIANNA', 'PIERINA',
    'MARCELLO', 'DARIO', 'RENATO', 'SANDRO', 'LILIANA', 'LORENA', 'NADIA', 'LUCIA'
}

# Regex precompilate per efficienza
_RE_AZIENDA = re.compile(
    '|'.join(_SUFFISSI_AZIENDA + _PREFISSI_ENTE + _PREFISSI_COMMERCIALI),
    re.IGNORECASE
)

_RE_ENTE = re.compile(
    '|'.join(_PREFISSI_ENTE),
    re.IGNORECASE
)

_RE_CONDOMINIO = re.compile(
    r'\bCONDOMINIO\b|\bCOND\.?\b',
    re.IGNORECASE
)


def _pulisci_nominativo(nominativo: str) -> str:
    """Rimuove note, email e numeri telefonici inseriti nel campo nominativo."""
    s = _RE_PULIZIA_NOMINATIVO.sub(' ', nominativo or '')
    return re.sub(r'\s+', ' ', s).strip()


def _is_ditta_individuale_con_di(nominativo: str, note: str = '') -> bool:
    """
    Disambigua l'uso della parola 'DI' nel nominativo (e nelle note operative):
    - Riconosce se 'DI' introduce il titolare di una ditta individuale o insegna
      (es. 'BAR DA MARIO DI ROSSI MARIO', 'DEUX SOLEIL DI GIRGENTI VINCI FRANCO',
      'REZZANI LUIGI DI BARBARA REZZANI').
    - Esclude i privati con cognome patronimico (es. 'DANIELE DI GIUSEPPE',
      'DI TIZIO MONICA', 'MARIA RITA DI GIORGI') o con note operative ('MOGLIE DI...').
    """
    pulito = _pulisci_nominativo(nominativo)
    if not pulito:
        return False

    # 1. Se formula societaria esplicita (es. 'DI BORDONE B & C')
    if _RE_SOCIETA_DI.search(pulito):
        return True

    # 2. Se non compare la parola isolata 'DI' nel nominativo
    if not re.search(r'\bDI\b', pulito, re.IGNORECASE):
        return False

    # 3. Se 'DI' compare solo all'interno di una nota di parentela o relazione
    #    (verificata sia nel nominativo sia nella colonna note!)
    senza_parentela = _RE_PARENTELA_RELAZIONI.sub(' ', pulito)
    if not re.search(r'\bDI\b', senza_parentela, re.IGNORECASE):
        return False

    # Se le note operative contengono una relazione familiare/di parentela,
    # indica che si tratta di un'utenza privata domestica.
    if note and _RE_PARENTELA_RELAZIONI.search(note):
        return False

    # 4. Se la stringa inizia con 'DI ' seguito da cognome e nome -> Privato
    # (es. 'DI TIZIO MONICA', 'DI BARTOLO ROBERTO', 'DI MAIO LUIGI')
    if re.match(r'^\s*DI\s+[A-Za-z\']+\s+[A-Za-z\']+', pulito, re.IGNORECASE):
        return False

    # 5. Analisi del lato sinistro (prima di 'DI') e destro (dopo 'DI')
    m = re.search(r'^(.*?)\s+DI\s+(.*)$', senza_parentela.strip(), re.IGNORECASE)
    if not m:
        return False

    before = m.group(1).strip()
    after = m.group(2).strip()

    words_before = [w.upper() for w in before.split() if w]
    words_after = [w.upper() for w in after.split() if w]

    if not words_before or not words_after:
        return False

    # Se prima di 'DI' c'è un solo token ed è un nome proprio comune -> Persona fisica
    # (es. 'DANIELE DI GIUSEPPE', 'ALDO DI GIOIA', 'ELEONORA DI STEFANO', 'MAURO DI CARLO')
    if len(words_before) == 1 and words_before[0] in _NOMI_PROPRII_COMUNI:
        return False

    # Se prima di 'DI' ci sono due token e uno di essi è un nome proprio:
    if len(words_before) == 2 and (words_before[0] in _NOMI_PROPRII_COMUNI or words_before[1] in _NOMI_PROPRII_COMUNI):
        # Se dopo 'DI' c'è un solo token (cognome patronimico, es. 'GIORGI'), è un privato
        if len(words_after) == 1:
            return False
        # Se dopo 'DI' ci sono 2 o più token e uno è un nome proprio (es. 'REZZANI LUIGI DI BARBARA REZZANI'),
        # allora dopo 'DI' c'è un titolare (Nome + Cognome): è una ditta!
        if words_after[0] in _NOMI_PROPRII_COMUNI or (len(words_after) >= 2 and words_after[1] in _NOMI_PROPRII_COMUNI):
            return True
        return False

    # Se prima di 'DI' ci sono 3 token e i primi due sono nomi propri (es. 'MARIA PAOLA LUDA DI CORTEMIGLIA')
    if len(words_before) >= 3 and words_before[0] in _NOMI_PROPRII_COMUNI and words_before[1] in _NOMI_PROPRII_COMUNI:
        if len(words_after) == 1:
            return False

    # Altrimenti, il lato sinistro è una denominazione commerciale/insegna
    # (es. 'DEUX SOLEIL DI GIRGENTI...', 'G-UNIT DI GIORGIA...', 'LA TANA DEI MONELLI DI...')
    return True


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

def is_azienda(nominativo: str, tipologia_contatore: str = '', note: str = '') -> bool:
    """
    Determina se un nominativo (ed eventuali note operative) indica un'azienda/ente.

    Args:
        nominativo: nome cliente
        tipologia_contatore: tipo misuratore (opzionale). Se è un Semidiretto
            il contatore viene considerato azienda anche se il nominativo
            non contiene suffissi/prefissi tipici.
        note: testo delle note operative (posizione misuratore, note appuntamento, ecc.).
    """
    if is_semidiretto(tipologia_contatore):
        return True
    nom = (nominativo or '').strip()
    if not nom:
        return False

    if _RE_AZIENDA.search(nom):
        return True

    if _is_ditta_individuale_con_di(nom, note=note):
        return True

    # Rileva attività commerciali o sedi indicate esplicitamente nella colonna Note
    if note and _RE_NOTE_COMMERCIALE.search(note):
        # Se la nota non è una mera relazione di parentela
        if not _RE_PARENTELA_RELAZIONI.search(note):
            return True

    return False


def classifica_tipo_contatto(nominativo: str, tipologia_contatore: str = '', note: str = '') -> str:
    """
    Classifica il tipo di contatto.

    Args:
        nominativo: nome cliente
        tipologia_contatore: tipo misuratore (opzionale). Se Semidiretto il
            contatto viene classificato come 'azienda' a prescindere dal
            nominativo.
        note: note operative opzionali.

    Returns:
        'azienda' | 'condominio' | 'ente_pubblico' | 'privato'
    """
    nome = (nominativo or '').strip()
    nota = (note or '').strip()

    if (nome and _RE_CONDOMINIO.search(nome)) or (nota and _RE_CONDOMINIO.search(nota)):
        return 'condominio'

    if (nome and _RE_ENTE.search(nome)) or (nota and _RE_ENTE.search(nota)):
        return 'ente_pubblico'

    if is_azienda(nome, tipologia_contatore, note=nota):
        return 'azienda'

    return 'privato'


# ============================================================================
# ARRICCHIMENTO CONTATTI
# ============================================================================

def arricchisci_contatti(contatti: List[Dict]) -> List[Dict]:
    """
    Arricchisce una lista di contatti con tipo_contatto e is_azienda.

    Considera:
    - 'tipologia_contatore' per classificare i Semidiretti come aziende
    - le colonne note ('note', 'note_appuntamento', 'note_filone', 'posizione_mis',
      'posizione_contatore') per disambiguare note operative e relazioni.
    """
    for contatto in contatti:
        nome = contatto.get('nome', '')
        tipologia = contatto.get('tipologia_contatore', '')

        note_tokens = [
            str(contatto.get('note') or ''),
            str(contatto.get('note_appuntamento') or ''),
            str(contatto.get('note_filone') or ''),
            str(contatto.get('posizione_mis') or ''),
            str(contatto.get('posizione_contatore') or ''),
        ]
        note_str = " ".join(t.strip() for t in note_tokens if t and str(t).strip())

        contatto['tipo_contatto'] = classifica_tipo_contatto(nome, tipologia, note=note_str)
        contatto['is_azienda'] = contatto['tipo_contatto'] != 'privato'

    return contatti
