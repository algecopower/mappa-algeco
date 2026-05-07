# -*- coding: utf-8 -*-
"""
Verifica gli indirizzi dei contatori contro l'anagrafe nazionale dei numeri civici
(INDIR_PIEM, ANNCSU). Suggerisce correzioni di civici sospetti o inesistenti.

Il DB SQLite è costruito da `tools/build_indir_db.py`.
"""
import logging
import os
import re
import sqlite3
import threading
import unicodedata

from rapidfuzz import process, fuzz

logger = logging.getLogger('mappa_algeco')

_DIR_CORRENTE = os.path.dirname(os.path.abspath(__file__))
_DIR_PROGETTO = os.path.dirname(_DIR_CORRENTE)
DB_PATH = os.path.join(_DIR_PROGETTO, 'cache', 'indir_piem.db')

# Thread-local connection pool (sqlite connections non condivisibili tra thread)
_local = threading.local()


def _conn() -> sqlite3.Connection | None:
    """Connessione SQLite read-only per il thread corrente."""
    if not os.path.exists(DB_PATH):
        return None
    c = getattr(_local, 'conn', None)
    if c is None:
        c = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True, check_same_thread=False)
        c.row_factory = sqlite3.Row
        _local.conn = c
    return c


def db_disponibile() -> bool:
    return os.path.exists(DB_PATH)


# ============================================================================
# NORMALIZZAZIONE (deve restare allineata a tools/build_indir_db.py)
# ============================================================================
_TIPI_VIA = (
    'VIA', 'VIALE', 'VICOLO', 'VICOLETTO', 'CORSO', 'PIAZZA', 'PIAZZALE',
    'PIAZZETTA', 'LARGO', 'STRADA', 'STRADALE', 'STRADELLA', 'BORGO', 'BORGATA',
    'FRAZIONE', 'LOCALITA', 'REGIONE', 'CASCINA', 'CASE', 'RIONE',
    'LUNGOFIUME', 'LUNGARNO', 'LUNGOPO', 'SALITA', 'DISCESA', 'GALLERIA',
    'PASSAGGIO', 'PONTE', 'RAMPA', 'SOTTOPASSO', 'TRAVERSA', 'CONTRADA',
    'CIRCONVALLAZIONE',
)
_TIPI_VIA_RE = re.compile(r'^(' + '|'.join(_TIPI_VIA) + r')\s+', re.IGNORECASE)


def normalizza_odonimo(s: str) -> str:
    if not s:
        return ''
    s = str(s).strip().upper()
    s = re.sub(r"\bV\.LE\b", "VIALE", s)
    s = re.sub(r"\bC\.SO\b", "CORSO", s)
    s = re.sub(r"\bC/SO\b", "CORSO", s)
    s = re.sub(r"\bP\.ZZA\b", "PIAZZA", s)
    s = re.sub(r"\bP\.ZA\b", "PIAZZA", s)
    s = re.sub(r"\bP\.LE\b", "PIAZZALE", s)
    s = re.sub(r"\bLOC\.", "LOCALITA ", s)
    s = re.sub(r"\bFRAZ\.", "FRAZIONE ", s)
    s = re.sub(r"\bFR\.", "FRAZIONE ", s)
    s = re.sub(r"\bSTR\.", "STRADA ", s)
    s = re.sub(r"\bB\.GO\b", "BORGO", s)
    s = re.sub(r"\bV\.\s+", "VIA ", s)
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def odonimo_senza_tipo(odonimo_norm: str) -> str:
    return _TIPI_VIA_RE.sub('', odonimo_norm).strip()


def normalizza_nome_comune(s: str) -> str:
    if not s:
        return ''
    s = str(s).strip().upper()
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def parse_civico(val) -> int | None:
    """Estrae solo la parte numerica iniziale del civico (es. '12/A' -> 12, 'SNC' -> None)."""
    if val is None:
        return None
    s = str(val).strip()
    if not s or s.upper() in ('SNC', 'NC', 'S.N.C.', 'S.N.', '-'):
        return None
    m = re.match(r'\s*(\d+)', s)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def parse_esponente(val) -> str:
    """Estrae la parte alfa del civico (es. '12/A' -> 'A', '12 BIS' -> 'BIS')."""
    if val is None:
        return ''
    s = str(val).strip().upper()
    m = re.match(r'\s*\d+\s*[/\-\s]?\s*([A-Z]+)', s)
    return m.group(1) if m else ''


# ============================================================================
# LOOKUP COMUNI
# ============================================================================
def _varianti_nome_comune(nome: str) -> list[str]:
    """
    Genera varianti di un nome di comune utili per il match.
    Es: 'COLLEGNO - LEUMANN' -> ['COLLEGNO LEUMANN', 'COLLEGNO']
        'TORINO (TO)' -> ['TORINO TO', 'TORINO']
        'BARDONECCHIA - LES ARNAUDS' -> ['BARDONECCHIA LES ARNAUDS', 'BARDONECCHIA']
    """
    if not nome:
        return []
    out = []
    # Versione completa normalizzata
    full = normalizza_nome_comune(nome)
    if full:
        out.append(full)
    # Tronca su separatori comuni: ' - ', '/', '(', ',', '.'
    # Anche su ' DI ' e ' SUL ' (potrebbero essere parte del nome ufficiale, ma se
    # il match completo fallisce, vale la pena provare il troncamento)
    for sep in (' - ', ' – ', '-', '/', '(', ',', '·'):
        if sep in nome:
            primo = nome.split(sep, 1)[0]
            v = normalizza_nome_comune(primo)
            if v and v not in out:
                out.append(v)
    # Estrema risorsa: prima parola
    if full:
        prima = full.split(' ', 1)[0]
        if prima and prima not in out and len(prima) >= 4:
            out.append(prima)
    return out


def codice_catastale_per_comune(nome_comune: str) -> tuple[str, str] | None:
    """
    Mappa nome di comune -> (codice_catastale, provincia).
    Tenta: match esatto, varianti (suffissi/frazioni rimossi), contains.
    Ritorna None se non trovato (probabile fuori Piemonte).
    """
    conn = _conn()
    if not conn:
        return None
    varianti = _varianti_nome_comune(nome_comune)
    if not varianti:
        return None

    # Match esatto su una qualunque variante
    for v in varianti:
        cur = conn.execute(
            'SELECT codice, provincia FROM comuni WHERE nome_norm = ? LIMIT 1', (v,)
        )
        row = cur.fetchone()
        if row:
            return (row['codice'], row['provincia'])

    # Fallback: contains match (per gestire alternative grafiche minori).
    # Limito alla provincia Piemonte (TO/CN/AL/AT/BI/NO/VC/VB) per evitare
    # collisioni con omonimi extra-regione.
    PROV_PIEM = ('TO', 'CN', 'AL', 'AT', 'BI', 'NO', 'VC', 'VB')
    placeholders = ','.join('?' * len(PROV_PIEM))
    for v in varianti:
        if len(v) < 4:
            continue
        cur = conn.execute(
            f'SELECT codice, provincia FROM comuni '
            f'WHERE nome_norm LIKE ? AND provincia IN ({placeholders}) LIMIT 1',
            (f'%{v}%', *PROV_PIEM),
        )
        row = cur.fetchone()
        if row:
            return (row['codice'], row['provincia'])
    return None


# ============================================================================
# LOOKUP CIVICI
# ============================================================================
def _civici_su_via(conn, codice_comune: str, odonimo_norm: str) -> list[sqlite3.Row]:
    """Tutti i civici registrati per quella via (match esatto su odonimo_norm)."""
    cur = conn.execute(
        'SELECT civico, esponente, odonimo_orig FROM civici '
        'WHERE codice_comune = ? AND odonimo_norm = ? '
        'AND civico IS NOT NULL ORDER BY civico',
        (codice_comune, odonimo_norm),
    )
    return cur.fetchall()


_TOKEN_STOPWORDS = {
    'DI', 'DA', 'DEI', 'DEL', 'DELLA', 'DELLE', 'DEGLI', 'IL', 'LA', 'LO',
    'LE', 'I', 'GLI', 'A', 'AL', 'ALLA', 'AGLI', 'ALLE', 'E', 'ED', 'IN',
    'SU', 'SUL', 'SULLA', 'CON', 'PER', 'TRA', 'FRA',
}


def _tokens_significativi(odonimo_norm: str) -> list[str]:
    """Token utili per match parziale: scarta stopword e parole < 3 caratteri."""
    if not odonimo_norm:
        return []
    return [
        t for t in odonimo_norm.split()
        if len(t) >= 3 and t not in _TOKEN_STOPWORDS
    ]


def _via_esiste(conn, codice_comune: str, odonimo_norm: str) -> tuple[str, str] | None:
    """
    Tenta di trovare la via dell'utente nel registro. In ordine:
      1. match esatto su odonimo_norm
      2. match su odonimo_norm_nt (senza prefisso VIA/CORSO/...)
      3. token-subset match: i token utente sono tutti contenuti in
         un'unica via del comune (es. utente 'ROSSINI' -> 'VIA GIOACHINO ROSSINI').
    Ritorna (odonimo_orig, odonimo_norm_csv) oppure None.
    """
    # 1) Match esatto
    cur = conn.execute(
        'SELECT odonimo_orig, odonimo_norm FROM civici '
        'WHERE codice_comune = ? AND odonimo_norm = ? LIMIT 1',
        (codice_comune, odonimo_norm),
    )
    row = cur.fetchone()
    if row:
        return row['odonimo_orig'], row['odonimo_norm']

    # 2) Match senza prefisso tipo
    odon_nt = odonimo_senza_tipo(odonimo_norm)
    if odon_nt:
        cur = conn.execute(
            'SELECT odonimo_orig, odonimo_norm FROM civici '
            'WHERE codice_comune = ? AND odonimo_norm_nt = ? LIMIT 1',
            (codice_comune, odon_nt),
        )
        row = cur.fetchone()
        if row:
            return row['odonimo_orig'], row['odonimo_norm']

    # 3) Token-subset
    user_tokens = set(_tokens_significativi(odon_nt or odonimo_norm))
    if not user_tokens:
        return None

    cur = conn.execute(
        'SELECT DISTINCT odonimo_orig, odonimo_norm, odonimo_norm_nt '
        'FROM civici WHERE codice_comune = ?',
        (codice_comune,),
    )
    candidati = []
    for r in cur.fetchall():
        cand_tokens = set(_tokens_significativi(r['odonimo_norm_nt']))
        if not cand_tokens:
            continue
        if user_tokens.issubset(cand_tokens):
            extra = len(cand_tokens) - len(user_tokens)
            candidati.append((extra, r['odonimo_orig'], r['odonimo_norm']))

    if not candidati:
        return None
    candidati.sort(key=lambda x: x[0])

    # Unico candidato -> match
    if len(candidati) == 1:
        return candidati[0][1], candidati[0][2]

    # Migliore strettamente migliore di tutti gli altri (meno token extra)
    if candidati[0][0] < candidati[1][0]:
        return candidati[0][1], candidati[0][2]

    return None


def _suggerimenti_civici_vicini(civici: list[sqlite3.Row], target: int, n: int = 5) -> list[dict]:
    """Restituisce i n civici registrati più vicini a `target`."""
    if not civici:
        return []
    arr = [(r['civico'], r['esponente'] or '') for r in civici if r['civico'] is not None]
    arr.sort(key=lambda x: (abs(x[0] - target), x[0]))
    out = []
    seen = set()
    for civ, esp in arr[: n * 2]:
        key = f"{civ}{esp}"
        if key in seen:
            continue
        seen.add(key)
        out.append({'civico': civ, 'esponente': esp})
        if len(out) >= n:
            break
    return out


def _vie_simili(conn, codice_comune: str, odonimo_norm: str, n: int = 5) -> list[dict]:
    """Fuzzy match sulle vie del comune. Ritorna le n più simili."""
    cur = conn.execute(
        'SELECT DISTINCT odonimo_orig, odonimo_norm FROM civici WHERE codice_comune = ?',
        (codice_comune,),
    )
    candidati = [(r['odonimo_orig'], r['odonimo_norm']) for r in cur.fetchall()]
    if not candidati:
        return []
    norms = [c[1] for c in candidati]
    # Provo sia con il nome completo che senza prefisso
    queries = [odonimo_norm]
    odon_nt = odonimo_senza_tipo(odonimo_norm)
    if odon_nt and odon_nt != odonimo_norm:
        queries.append(odon_nt)
    best_per_idx: dict[int, float] = {}
    for q in queries:
        results = process.extract(q, norms, scorer=fuzz.WRatio, limit=n * 3)
        for _, score, idx in results:
            if score > best_per_idx.get(idx, 0):
                best_per_idx[idx] = score
    ranked = sorted(best_per_idx.items(), key=lambda x: x[1], reverse=True)
    out = []
    seen_orig = set()
    for idx, score in ranked:
        orig, _ = candidati[idx]
        if orig in seen_orig:
            continue
        seen_orig.add(orig)
        out.append({'odonimo': orig, 'score': round(float(score), 1)})
        if len(out) >= n:
            break
    return out


# ============================================================================
# VERIFICA SINGOLO INDIRIZZO
# ============================================================================
# Soglia oltre la quale il civico utente è considerato "anomalo" anche se
# non c'è match esatto: se è > max(civici) + MARGINE_CIVICO_OUTLIER lo flagghiamo.
MARGINE_CIVICO_OUTLIER = 50


def verifica_singolo(comune: str, indirizzo: str, civico: str) -> dict:
    """
    Verifica un indirizzo singolo. Ritorna dict con:
        status: 'ok' | 'civico_mancante' | 'civico_sospetto' | 'civico_inesistente'
              | 'via_non_trovata' | 'comune_non_riconosciuto' | 'fuori_regione'
              | 'db_assente'
        odonimo_match: str | None  (la via corrispondente nel CSV)
        civici_disponibili: int    (quanti civici esistono su quella via)
        suggerimenti_civici: [{'civico': N, 'esponente': '...'}]
        suggerimenti_vie: [{'odonimo': '...', 'score': N}]
        codice_catastale: str | None
        nota: str | None  (eventuale messaggio descrittivo)
    """
    out = {
        'status': 'ok',
        'odonimo_match': None,
        'civici_disponibili': 0,
        'suggerimenti_civici': [],
        'suggerimenti_vie': [],
        'codice_catastale': None,
        'nota': None,
    }

    if not db_disponibile():
        out['status'] = 'db_assente'
        out['nota'] = 'DB indirizzi non costruito. Lancia tools/build_indir_db.py'
        return out

    conn = _conn()

    # 1) Mapping comune -> codice catastale
    cat = codice_catastale_per_comune(comune)
    if not cat:
        out['status'] = 'comune_non_riconosciuto'
        out['nota'] = f"Comune '{comune}' non trovato nel registro catastale"
        return out
    codice_cat, provincia = cat
    out['codice_catastale'] = codice_cat

    # 2) Il CSV copre solo il Piemonte: per le altre regioni non possiamo verificare
    cur = conn.execute(
        'SELECT 1 FROM civici WHERE codice_comune = ? LIMIT 1', (codice_cat,)
    )
    if cur.fetchone() is None:
        out['status'] = 'fuori_regione'
        out['nota'] = f"Comune {comune} ({provincia}) non è in INDIR_PIEM (verifica solo Piemonte)"
        return out

    # 3) Verifica che la via esista
    odon_norm = normalizza_odonimo(indirizzo)
    if not odon_norm:
        out['status'] = 'via_non_trovata'
        out['nota'] = 'Indirizzo vuoto o non leggibile'
        return out

    match = _via_esiste(conn, codice_cat, odon_norm)
    if match is None:
        out['status'] = 'via_non_trovata'
        out['suggerimenti_vie'] = _vie_simili(conn, codice_cat, odon_norm, n=5)
        return out

    odon_orig, odon_norm_csv = match
    out['odonimo_match'] = odon_orig
    # Cerca i civici usando l'odonimo trovato nel CSV (non quello utente)
    civici = _civici_su_via(conn, codice_cat, odon_norm_csv)
    out['civici_disponibili'] = len(civici)

    # 4) Civico
    civico_int = parse_civico(civico)
    if civico_int is None:
        out['status'] = 'civico_mancante'
        out['suggerimenti_civici'] = _suggerimenti_civici_vicini(
            civici, target=10, n=5,
        ) if civici else []
        return out

    # Match esatto del civico (con o senza esponente)
    esp_user = parse_esponente(civico)
    esatto = False
    for r in civici:
        if r['civico'] == civico_int:
            esp_csv = (r['esponente'] or '').strip().upper()
            if esp_user == '' and esp_csv == '':
                esatto = True
                break
            if esp_user and esp_csv and esp_user == esp_csv:
                esatto = True
                break
            if esp_user == '' or esp_csv == '':
                # match parziale (uno dei due manca): consideriamo ok ma con nota
                esatto = True
                break

    if esatto:
        out['status'] = 'ok'
        return out

    # Civico non trovato — outlier?
    if civici:
        max_civ = max((r['civico'] for r in civici if r['civico'] is not None), default=0)
        out['suggerimenti_civici'] = _suggerimenti_civici_vicini(civici, target=civico_int, n=5)
        if civico_int > max_civ + MARGINE_CIVICO_OUTLIER:
            out['status'] = 'civico_sospetto'
            out['nota'] = f"Civico {civico_int} oltre il massimo registrato ({max_civ})"
        else:
            out['status'] = 'civico_inesistente'
    else:
        out['status'] = 'civico_inesistente'

    return out


# ============================================================================
# VERIFICA BATCH
# ============================================================================
def verifica_batch(contatori: list[dict]) -> list[dict]:
    """
    Verifica una lista di contatori in batch.
    Input: lista di dict con almeno {eneltel, cliente, comune, indirizzo, civico, lcl}
    Output: stessa lista arricchita con campi 'verifica' (dict da verifica_singolo)
    """
    out = []
    for c in contatori:
        v = verifica_singolo(
            c.get('comune', ''),
            c.get('indirizzo', '') or c.get('via', ''),
            c.get('civico', ''),
        )
        item = dict(c)
        item['verifica'] = v
        out.append(item)
    return out


def stato_db() -> dict:
    """Ritorna metadati del DB (presenza, dimensioni, conteggi)."""
    if not db_disponibile():
        return {'disponibile': False}
    conn = _conn()
    cur = conn.execute('SELECT chiave, valore FROM meta')
    meta = {r['chiave']: r['valore'] for r in cur.fetchall()}
    cur = conn.execute('SELECT COUNT(*) AS n FROM civici')
    n_civici = cur.fetchone()['n']
    cur = conn.execute('SELECT COUNT(*) AS n FROM comuni')
    n_comuni = cur.fetchone()['n']
    return {
        'disponibile': True,
        'civici': n_civici,
        'comuni': n_comuni,
        'size_mb': round(os.path.getsize(DB_PATH) / 1024 / 1024, 1),
        'meta': meta,
    }
