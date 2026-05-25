# -*- coding: utf-8 -*-
"""
Route Dashboard — vista riepilogativa con statistiche operative.

Sorgente dati: DB SQLite locale (`dump_filone_as_rows`), con overlay dei
`pending_writes`. Le modifiche fatte dall'utente sono quindi visibili
immediatamente, senza dover aspettare il backup giornaliero.
Fallback al backup JSON solo se il DB è irraggiungibile.
"""
import os
import time
import logging
from datetime import datetime, date, timedelta
from collections import Counter, defaultdict

from flask import Blueprint, jsonify, send_file
from moduli.auth import login_required
from moduli.safe_json import json_read
from moduli.calendario import get_attivita
from config import PROVINCIA_ATTIVA, MATRICOLE_VIP, COLORI_TECNICI, OPERATORI_DICT

logger = logging.getLogger('mappa_algeco')

bp = Blueprint('dashboard', __name__)

# Directory base
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP_DIR = os.path.join(BASE_DIR, 'backup', PROVINCIA_ATTIVA.lower())


# ---------------------------------------------------------------------------
# Cache in-memory per /api/dashboard.
#
# Sorgente PRIMARIA: DB SQLite live (`dump_filone_as_rows`). Iterare ~30k
# righe + calcolare cento aggregazioni costa diversi secondi: la cache rende
# istantanee le request ravvicinate (es. F5 ripetuti).
#
# Chiave cache stats: (data_version, today_iso).
#   - data_version: PRAGMA SQLite che incrementa ad ogni write da un'altra
#     connessione (in WAL mode funziona cross-thread). Appena qualcuno
#     modifica il DB, la nostra cache si invalida da sola alla prossima
#     request — quindi i cambi sono live entro al massimo `_STATS_TTL_S`.
#   - today_iso: invalida automaticamente a mezzanotte (i conteggi
#     "oggi/domani" cambiano significato).
#
# In più applichiamo un TTL massimo (`_STATS_TTL_S`) come safety net: se per
# qualche motivo data_version non cambia (es. write nel nostro stesso
# thread/connessione), comunque dopo N secondi rilegge.
#
# Il vecchio _BACKUP_CACHE rimane come fallback se il DB è giù.
# ---------------------------------------------------------------------------
_BACKUP_CACHE: dict[str, tuple[float, dict]] = {}
_STATS_CACHE: dict[tuple, tuple[float, dict]] = {}  # key -> (created_at, payload)
_STATS_TTL_S = 10.0  # safety net oltre data_version


def _ultimo_backup_path() -> tuple[str, float] | None:
    """Restituisce (path, mtime) del backup più recente, o None."""
    if not os.path.isdir(BACKUP_DIR):
        return None
    try:
        files = [f for f in os.listdir(BACKUP_DIR) if f.endswith('.json')]
    except OSError:
        return None
    if not files:
        return None
    files.sort(reverse=True)
    path = os.path.join(BACKUP_DIR, files[0])
    try:
        return path, os.path.getmtime(path)
    except OSError:
        return path, 0.0


def _carica_ultimo_backup() -> dict | None:
    """Fallback: carica il backup JSON più recente se il DB è giù.

    Memoizzato per (path, mtime). Usato solo in emergenza — di norma la
    dashboard legge dal DB live (vedi `_carica_filone_live`).
    """
    info = _ultimo_backup_path()
    if info is None:
        return None
    path, mtime = info
    cached = _BACKUP_CACHE.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        payload = json_read(path, default=None)
    except Exception as e:
        logger.warning(f"[Dashboard] Errore lettura backup {os.path.basename(path)}: {e}")
        return None
    if payload is not None:
        _BACKUP_CACHE.clear()
        _BACKUP_CACHE[path] = (mtime, payload)
    return payload


def _db_data_version() -> int:
    """PRAGMA data_version: incrementa ad ogni write committed da un'altra
    connessione. In WAL mode è il segnale più rapido (~µs) per sapere se
    il DB è cambiato dal nostro ultimo snapshot."""
    try:
        from moduli import db as _db
        row = _db.get_conn().execute('PRAGMA data_version').fetchone()
        return row[0] if row else 0
    except Exception:
        return 0


# Colonne strettamente necessarie alla dashboard. 7 sono native nella tabella
# filone (mapping da column_registry → chiave_interna), 1 sta in filone_extra.
# Limitare la SELECT a queste colonne riduce ~10× il lavoro di materializzazione
# rispetto al generico dump_filone_as_rows (22k righe × 28 colonne).
_DASH_NATIVE_COLS = [
    # (header_name, sql_column_in_filone)
    ('LCL',                'lcl'),
    ('DATA PIAN',          'data_pianificata'),
    ('Operatore',          'operatore'),
    ('Esito',              'esito'),
    ('NOTE APP',           'note_appuntamento'),
    ('Comune',             'comune'),
    ('Data Installazione', 'data_installazione'),
]
_DASH_EXTRA_COLS = ['Mot. Rich.']
_DASH_HEADERS = [h for h, _ in _DASH_NATIVE_COLS] + _DASH_EXTRA_COLS


def _carica_filone_live() -> dict | None:
    """Legge dal DB SQLite solo le colonne necessarie alla dashboard.

    Ritorna lo stesso schema del backup JSON (`{headers, righe, data_backup}`)
    così le funzioni di calcolo (_calcola_statistiche_filone /
    _calcola_andamento_lcl) funzionano senza modifiche. `righe` è
    list[list[str]] con indice posizionale allineato a `headers`.

    Applica l'overlay dei `pending_writes` (state pending/in_flight) sulle
    sole colonne dashboard: scritture utente non ancora pushate a Sheets
    sono comunque visibili nei KPI.
    """
    try:
        from moduli import db as _db
        conn = _db.get_conn()

        # 1. Native columns dalla tabella filone (escludi orphan).
        sql_cols = ['eneltel_key', 'lcl'] + [c for _, c in _DASH_NATIVE_COLS if c != 'lcl']
        sql = (
            "SELECT " + ', '.join(sql_cols) +
            " FROM filone WHERE is_orphan=0 ORDER BY sheets_row"
        )
        native_rows = conn.execute(sql).fetchall()

        # 2. Mot. Rich. da filone_extra (chiave: (eneltel_key, lcl)).
        extra_motrich = {}
        cur = conn.execute(
            "SELECT eneltel_key, COALESCE(lcl,'') AS lcl, value "
            "FROM filone_extra WHERE column_name = ?",
            ('Mot. Rich.',),
        )
        for r in cur:
            extra_motrich[(r['eneltel_key'], r['lcl'])] = r['value']

        # 3. pending_writes overlay solo per le colonne che ci interessano.
        #    column_name nel DB è il nome originale del foglio (case-sensitive
        #    storico): usiamo IN su tutti gli header dashboard.
        pending_by_key: dict = {}
        placeholders = ','.join('?' for _ in _DASH_HEADERS)
        cur = conn.execute(
            "SELECT eneltel_key, COALESCE(lcl,'') AS lcl, column_name, new_value "
            "FROM pending_writes "
            f"WHERE state IN ('pending','in_flight') AND column_name IN ({placeholders})",
            _DASH_HEADERS,
        )
        for r in cur:
            pending_by_key.setdefault((r['eneltel_key'], r['lcl']), {})[r['column_name']] = r['new_value']
    except Exception as e:
        logger.warning(f"[Dashboard] Lettura DB fallita, uso backup: {e}")
        return None

    if not native_rows:
        return None

    # 4. Materializza ogni riga come lista nello stesso ordine di _DASH_HEADERS.
    #    Hot loop: usa accesso diretto al Row + valori puri (no isinstance, no
    #    iterazione headers per ogni riga).
    righe: list[list[str]] = []
    for fr in native_rows:
        ek = fr['eneltel_key']
        lcl_val = fr['lcl'] or ''
        row_pending = pending_by_key.get((ek, lcl_val))

        # 7 valori nativi nell'ordine di _DASH_NATIVE_COLS
        vals = [
            lcl_val,
            fr['data_pianificata'] or '',
            fr['operatore'] or '',
            fr['esito'] or '',
            fr['note_appuntamento'] or '',
            fr['comune'] or '',
            fr['data_installazione'] or '',
        ]
        # + Mot. Rich.
        vals.append(extra_motrich.get((ek, lcl_val), '') or '')

        # Pending overlay: solo se ci sono pending per questa riga.
        if row_pending:
            for i, h in enumerate(_DASH_HEADERS):
                if h in row_pending:
                    pv = row_pending[h]
                    vals[i] = '' if pv is None else str(pv)
        righe.append(vals)

    return {
        'headers': _DASH_HEADERS,
        'righe': righe,
        'data_backup': datetime.now().isoformat(timespec='seconds'),
        'source': 'db_live',
    }


def _col_index(headers: list, nomi: list) -> int | None:
    """Trova l'indice di una colonna cercando tra nomi alternativi (case-insensitive)."""
    lower_map = {h.lower().strip(): i for i, h in enumerate(headers)}
    for n in nomi:
        idx = lower_map.get(n.lower().strip())
        if idx is not None:
            return idx
    return None


def _cell(row: list, idx: int | None) -> str:
    """Restituisce il valore di una cella (stringa vuota se indice mancante)."""
    if idx is None or idx >= len(row):
        return ''
    return str(row[idx]).strip()


# Pre-compilato una volta sola (vs `import re` dentro la funzione hot path).
_DATE_RE = __import__('re').compile(r'^(\d{1,4})[/\-.](\d{1,2})[/\-.](\d{1,4})$')


@__import__('functools').lru_cache(maxsize=16384)
def _parse_data(val: str) -> str | None:
    """Converte una data grezza in YYYY-MM-DD (best-effort).

    Implementazione fast-path: parsing puro Python senza pandas. Sul dump
    FILONE (~22k righe × ~2 date a riga) `pd.to_datetime` scalar costa ~7-8 s
    totali; questo parser costa ~50 ms. lru_cache deduplicizza i valori
    ripetuti (molti contatori condividono la stessa data pianificata).
    """
    if not val:
        return None
    val = val.strip()
    if len(val) < 6 or 'annullat' in val.lower():
        return None
    # Strip orario (es. "26/05/2026 08:00" → "26/05/2026"), keepa solo il pezzo data.
    sp = val.find(' ')
    val_clean = val[:sp] if sp != -1 else val
    m = _DATE_RE.match(val_clean)
    if not m:
        return None
    a, b, c = m.groups()
    # Indovina ordine in base alla lunghezza dei campi
    if len(a) == 4:                    # YYYY-MM-DD
        y, mo, d = a, b, c
    elif len(c) == 4:                  # dd/mm/yyyy o dd-mm-yyyy
        d, mo, y = a, b, c
    elif len(c) == 2:                  # dd/mm/yy → 20yy
        d, mo, y = a, b, '20' + c
    else:
        return None
    try:
        di, moi, yi = int(d), int(mo), int(y)
        # Validazione minimale: range plausibili
        if not (1 <= di <= 31 and 1 <= moi <= 12 and 1900 <= yi <= 2999):
            return None
    except ValueError:
        return None
    return f"{yi:04d}-{moi:02d}-{di:02d}"


def _calcola_statistiche_filone(backup: dict) -> dict:
    """Calcola le statistiche dal backup del FILONE."""
    headers = backup.get('headers', [])
    righe = backup.get('righe', [])

    # Indici colonne
    i_lcl = _col_index(headers, ['LCL'])
    i_data = _col_index(headers, ['DATA PIAN', 'DATA_PIAN', 'Data Pian', 'PROG'])
    i_operatore = _col_index(headers, ['Operatore', 'OPERATORE'])
    i_esito = _col_index(headers, ['Esito', 'ESITO'])
    i_note = _col_index(headers, ['NOTE APP', 'NOTE_APP'])
    i_comune = _col_index(headers, ['Comune', 'COMUNE'])
    i_data_inst = _col_index(headers, ['Data Installazione', 'DATA INSTALLAZIONE'])
    _col_index(headers, ['Eneltel', 'ENELTEL'])

    oggi = date.today().isoformat()
    domani = (date.today() + timedelta(days=1)).isoformat()
    inizio_settimana = (date.today() - timedelta(days=date.today().weekday())).isoformat()
    fine_settimana = (date.today() + timedelta(days=6 - date.today().weekday())).isoformat()
    mese_corrente = date.today().strftime('%Y-%m')
    # Range ultimi 14 giorni per mini-trend
    trend_da = (date.today() - timedelta(days=13)).isoformat()
    trend_date = [(date.today() - timedelta(days=13-i)).isoformat() for i in range(14)]

    totale_righe = len(righe)
    contatori_con_data = 0
    contatori_oggi = 0
    contatori_domani = 0
    contatori_settimana = 0
    installati_totale = 0
    installati_mese = 0
    installati_settimana = 0
    appuntamenti_oggi = 0
    annullati = 0
    senza_operatore_oggi = 0

    per_operatore = defaultdict(lambda: {
        'pianificati_oggi': 0,
        'pianificati_domani': 0,
        'appuntamenti_domani': 0,
        'pianificati_settimana': 0,
        'installati_mese': 0,
        'appuntamenti_oggi': 0,
        'installati_per_giorno': Counter(),  # per mini-trend 14gg
        'esiti': Counter(),                  # esiti dettagliati
    })
    per_comune_oggi = Counter()
    per_comune_domani = Counter()
    per_data_settimana = Counter()
    esiti_mese = Counter()
    appuntamenti_domani = 0
    senza_operatore_domani = 0

    # ---- Statistiche per LCL ----
    per_lcl = defaultdict(lambda: {
        'totale': 0, 'con_data': 0, 'installati': 0, 'installati_mese': 0,
        'oggi': 0, 'domani': 0, 'app_oggi': 0, 'app_domani': 0,
        'annullati': 0,
    })
    # Operatori per LCL domani: lcl -> op -> count
    lcl_op_domani = defaultdict(Counter)
    # Comuni per operatore domani
    comuni_op_domani = defaultdict(Counter)

    import re
    app_re = re.compile(r'\bapp\b', re.IGNORECASE)

    for row in righe:
        data_raw = _cell(row, i_data)
        operatore = _cell(row, i_operatore)
        esito = _cell(row, i_esito)
        note = _cell(row, i_note)
        comune = _cell(row, i_comune)
        data_inst_raw = _cell(row, i_data_inst)
        lcl_val = _cell(row, i_lcl)

        data_pian = _parse_data(data_raw)

        # Conteggio totale per LCL
        if lcl_val:
            per_lcl[lcl_val]['totale'] += 1

        # Annullati
        if data_raw and 'annullat' in data_raw.lower():
            annullati += 1
            if lcl_val:
                per_lcl[lcl_val]['annullati'] += 1
            continue

        # Esito
        is_installato = bool(esito and 'ok' in esito.lower())
        is_appuntamento = bool(note and app_re.search(note))

        if data_pian:
            contatori_con_data += 1
            if lcl_val:
                per_lcl[lcl_val]['con_data'] += 1

        # Installati
        if is_installato:
            installati_totale += 1
            if lcl_val:
                per_lcl[lcl_val]['installati'] += 1
            # Data installazione per mese
            d_inst = _parse_data(data_inst_raw) if data_inst_raw else data_pian
            if d_inst and d_inst.startswith(mese_corrente):
                installati_mese += 1
                if lcl_val:
                    per_lcl[lcl_val]['installati_mese'] += 1
                if operatore and operatore != 'TUTTI':
                    per_operatore[operatore]['installati_mese'] += 1
            if d_inst and inizio_settimana <= d_inst <= fine_settimana:
                installati_settimana += 1
            # Conta esiti del mese
            if d_inst and d_inst.startswith(mese_corrente):
                esiti_mese[esito.strip()] += 1
            # Trend 14gg: installati per giorno per operatore
            if d_inst and trend_da <= d_inst <= oggi:
                if operatore and operatore != 'TUTTI':
                    per_operatore[operatore]['installati_per_giorno'][d_inst] += 1
                    per_operatore[operatore]['esiti'][esito.strip()] += 1

        # Pianificati oggi
        if data_pian == oggi:
            contatori_oggi += 1
            if lcl_val:
                per_lcl[lcl_val]['oggi'] += 1
            if comune:
                per_comune_oggi[comune.upper()] += 1
            if operatore and operatore != 'TUTTI':
                per_operatore[operatore]['pianificati_oggi'] += 1
            else:
                senza_operatore_oggi += 1
            if is_appuntamento:
                appuntamenti_oggi += 1
                if lcl_val:
                    per_lcl[lcl_val]['app_oggi'] += 1
                if operatore and operatore != 'TUTTI':
                    per_operatore[operatore]['appuntamenti_oggi'] += 1

        # Pianificati domani
        if data_pian == domani:
            contatori_domani += 1
            if lcl_val:
                per_lcl[lcl_val]['domani'] += 1
            if comune:
                per_comune_domani[comune.upper()] += 1
                if operatore and operatore != 'TUTTI':
                    comuni_op_domani[operatore][comune.upper()] += 1
            if operatore and operatore != 'TUTTI':
                per_operatore[operatore]['pianificati_domani'] += 1
                if lcl_val:
                    lcl_op_domani[lcl_val][operatore] += 1
            else:
                senza_operatore_domani += 1
            if is_appuntamento:
                appuntamenti_domani += 1
                if lcl_val:
                    per_lcl[lcl_val]['app_domani'] += 1
                if operatore and operatore != 'TUTTI':
                    per_operatore[operatore]['appuntamenti_domani'] += 1

        # Settimana
        if data_pian and inizio_settimana <= data_pian <= fine_settimana:
            contatori_settimana += 1
            per_data_settimana[data_pian] += 1
            if operatore and operatore != 'TUTTI':
                per_operatore[operatore]['pianificati_settimana'] += 1

    # Costruisci dati operatori arricchiti (nomi leggibili)
    nome_da_matricola = {}
    for nome, matricola in OPERATORI_DICT.items():
        nome_da_matricola[matricola] = nome

    # ── Cache distanze per km/guida ──
    cache_distanze = {}
    cache_dist_path = os.path.join(BASE_DIR, 'cache', f'cache_distanze_{PROVINCIA_ATTIVA.lower()}.json')
    if os.path.isfile(cache_dist_path):
        cache_distanze = json_read(cache_dist_path, default={})

    def _km_operatore(matricola):
        """Somma km e minuti guida dal cache distanze per il mese corrente."""
        km_tot = 0.0
        min_tot = 0.0
        for key, val in cache_distanze.items():
            if not isinstance(val, dict):
                continue
            parts = key.rsplit('_', 1)
            if len(parts) == 2 and parts[1] == matricola and parts[0].startswith(mese_corrente):
                km_tot += val.get('distanza_totale_km', 0)
                min_tot += val.get('durata_guida_min', 0)
        return round(km_tot, 1), round(min_tot)

    operatori_stats = []
    for matricola in MATRICOLE_VIP:
        stats = per_operatore[matricola]
        # Comuni domani per questo operatore (top 5)
        top_comuni_dom = [{'comune': c, 'count': n}
                          for c, n in comuni_op_domani[matricola].most_common(5)]
        # Km e guida mese
        km_mese, min_guida_mese = _km_operatore(matricola)
        # Serie trend 14gg
        serie_trend = [stats['installati_per_giorno'].get(d, 0) for d in trend_date]
        operatori_stats.append({
            'matricola': matricola,
            'nome': nome_da_matricola.get(matricola, matricola),
            'colore': COLORI_TECNICI.get(matricola, '#888'),
            'comuni_domani': top_comuni_dom,
            'km_mese': km_mese,
            'min_guida_mese': min_guida_mese,
            'serie_trend': serie_trend,
            'esiti': dict(stats['esiti'].most_common(5)),
            'pianificati_oggi': stats['pianificati_oggi'],
            'pianificati_domani': stats['pianificati_domani'],
            'appuntamenti_domani': stats['appuntamenti_domani'],
            'pianificati_settimana': stats['pianificati_settimana'],
            'installati_mese': stats['installati_mese'],
            'appuntamenti_oggi': stats['appuntamenti_oggi'],
        })

    # Distribuzione settimana (ordinata per data)
    settimana_labels = []
    settimana_values = []
    giorni_it = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom']
    for i in range(7):
        d = date.today() - timedelta(days=date.today().weekday()) + timedelta(days=i)
        ds = d.isoformat()
        settimana_labels.append(f"{giorni_it[i]} {d.day}/{d.month}")
        settimana_values.append(per_data_settimana.get(ds, 0))

    # Top comuni oggi e domani
    top_comuni = [{'comune': c, 'count': n} for c, n in per_comune_oggi.most_common(8)]
    top_comuni_domani = [{'comune': c, 'count': n} for c, n in per_comune_domani.most_common(8)]

    # Statistiche per LCL (ordinate per codice)
    lcl_stats = []
    for lcl_code in sorted(per_lcl.keys()):
        s = per_lcl[lcl_code]
        # Operatori domani per questo LCL
        ops_dom = [{'matricola': m, 'nome': nome_da_matricola.get(m, m), 'count': c}
                   for m, c in lcl_op_domani[lcl_code].most_common()]
        lcl_stats.append({
            'codice': lcl_code,
            **s,
            'operatori_domani': ops_dom,
        })

    return {
        'totale_righe': totale_righe,
        'contatori_con_data': contatori_con_data,
        'contatori_oggi': contatori_oggi,
        'contatori_domani': contatori_domani,
        'contatori_settimana': contatori_settimana,
        'installati_totale': installati_totale,
        'installati_mese': installati_mese,
        'installati_settimana': installati_settimana,
        'appuntamenti_oggi': appuntamenti_oggi,
        'appuntamenti_domani': appuntamenti_domani,
        'annullati': annullati,
        'senza_operatore_oggi': senza_operatore_oggi,
        'senza_operatore_domani': senza_operatore_domani,
        'operatori': operatori_stats,
        'settimana': {'labels': settimana_labels, 'values': settimana_values},
        'top_comuni_oggi': top_comuni,
        'top_comuni_domani': top_comuni_domani,
        'lcl': lcl_stats,
        'esiti_mese': dict(esiti_mese.most_common(10)),
        'trend_date': trend_date,
    }


def _calcola_andamento_lcl(backup: dict) -> dict:
    """Aggregazione per LCL: replica le viste del file 'Andamento LCL.xlsx'
    calcolando Assegnate/Evase/Residui/Annullate/% Produttività e andamento
    mensile direttamente dal backup FILONE."""
    headers = backup.get('headers', [])
    righe = backup.get('righe', [])

    i_lcl = _col_index(headers, ['LCL'])
    i_data = _col_index(headers, ['DATA PIAN', 'Data Pian'])
    i_esito = _col_index(headers, ['Esito', 'ESITO'])
    i_data_inst = _col_index(headers, ['Data Installazione', 'DATA INSTALLAZIONE'])
    i_mot = _col_index(headers, ['Mot. Rich.', 'MOT. RICH.', 'Mot Rich'])

    per_lcl = {}
    monthly_totale = Counter()

    for row in righe:
        lcl = _cell(row, i_lcl)
        if not lcl:
            continue
        if lcl not in per_lcl:
            per_lcl[lcl] = {
                'tipi': Counter(),
                'assegnate': 0,
                'evase': 0,
                'annullate': 0,
                'date_inst': [],
                'date_pian': [],
                'monthly': Counter(),
            }
        s = per_lcl[lcl]
        s['assegnate'] += 1

        data_raw = _cell(row, i_data)
        esito = _cell(row, i_esito)
        data_inst_raw = _cell(row, i_data_inst)
        mot = _cell(row, i_mot)

        if mot:
            s['tipi'][mot] += 1

        if data_raw and 'annullat' in data_raw.lower():
            s['annullate'] += 1
            continue

        d_pian = _parse_data(data_raw) if data_raw else None
        if d_pian:
            s['date_pian'].append(d_pian)

        if esito and 'ok' in esito.lower():
            s['evase'] += 1
            d_inst = _parse_data(data_inst_raw) if data_inst_raw else d_pian
            if d_inst and len(d_inst) >= 7:
                month = d_inst[:7]
                s['monthly'][month] += 1
                monthly_totale[month] += 1
                s['date_inst'].append(d_inst)

    lcl_list = []
    for code, s in per_lcl.items():
        ass = s['assegnate']
        eva = s['evase']
        ann = s['annullate']
        res = max(ass - eva - ann, 0)
        pct = round(eva / ass * 100, 1) if ass else 0.0
        prima = min(s['date_inst']) if s['date_inst'] else (
            min(s['date_pian']) if s['date_pian'] else '')
        ultima = max(s['date_inst']) if s['date_inst'] else (
            max(s['date_pian']) if s['date_pian'] else '')
        if res == 0 and ass > 0:
            stato = 'Chiusa'
        elif eva == 0:
            stato = 'Da avviare'
        else:
            stato = 'Attiva'
        tipo = s['tipi'].most_common(1)[0][0] if s['tipi'] else ''
        lcl_list.append({
            'codice': code,
            'tipo': tipo,
            'stato': stato,
            'assegnate': ass,
            'evase': eva,
            'residui': res,
            'annullate': ann,
            'pct_produttivita': pct,
            'prima_installazione': prima,
            'ultima_installazione': ultima,
        })

    lcl_list.sort(key=lambda x: x['codice'])

    tot_ass = sum(l['assegnate'] for l in lcl_list)
    tot_eva = sum(l['evase'] for l in lcl_list)
    tot_ann = sum(l['annullate'] for l in lcl_list)
    tot_res = sum(l['residui'] for l in lcl_list)
    pct_prod = round(tot_eva / tot_ass * 100, 1) if tot_ass else 0.0
    lcl_attivi = sum(1 for l in lcl_list if l['stato'] == 'Attiva')
    lcl_chiusi = sum(1 for l in lcl_list if l['stato'] == 'Chiusa')

    if monthly_totale:
        all_months = sorted(monthly_totale.keys())
        labels = all_months[-12:]
        values = [monthly_totale.get(m, 0) for m in labels]
    else:
        labels, values = [], []

    critici = sorted(
        [l for l in lcl_list if l['residui'] > 0 and l['stato'] == 'Attiva'],
        key=lambda x: (-x['residui'], x['pct_produttivita'])
    )[:5]

    return {
        'kpi': {
            'totale_assegnate': tot_ass,
            'totale_evase': tot_eva,
            'totale_residui': tot_res,
            'totale_annullate': tot_ann,
            'pct_produttivita': pct_prod,
            'numero_lcl': len(lcl_list),
            'numero_lcl_attivi': lcl_attivi,
            'numero_lcl_chiusi': lcl_chiusi,
        },
        'lcl_list': lcl_list,
        'andamento_mensile': {'labels': labels, 'values': values},
        'critici': critici,
    }


# ============================================================================
# ROUTE: pagina HTML
# ============================================================================
@bp.route('/dashboard')
@login_required
def dashboard_page():
    """Serve la pagina HTML della dashboard."""
    return send_file(os.path.join(BASE_DIR, 'templates', 'dashboard.html'))


# ============================================================================
# ROUTE: API dati dashboard
# ============================================================================
@bp.route('/api/dashboard', methods=['GET'])
@login_required
def api_dashboard():
    """Restituisce i dati aggregati per la dashboard.

    Legge dal DB live così le modifiche utente sono visibili subito; la
    cache è invalidata da PRAGMA data_version (qualunque write nel DB)
    oltre che da un TTL di sicurezza. Vedi commento su _STATS_CACHE.
    """
    try:
        today_iso = date.today().isoformat()
        data_ver = _db_data_version()
        stats_key = (data_ver, today_iso)

        cached_entry = _STATS_CACHE.get(stats_key)
        cached_stats = None
        if cached_entry is not None:
            created_at, payload = cached_entry
            if (time.monotonic() - created_at) < _STATS_TTL_S:
                cached_stats = payload

        if cached_stats is not None:
            filone_stats = cached_stats['filone']
            andamento_lcl = cached_stats['andamento_lcl']
            data_backup = cached_stats['data_backup']
        else:
            filone = _carica_filone_live() or _carica_ultimo_backup()
            if filone:
                filone_stats = _calcola_statistiche_filone(filone)
                andamento_lcl = _calcola_andamento_lcl(filone)
                data_backup = filone.get('data_backup', '?')
            else:
                filone_stats = {}
                andamento_lcl = {}
                data_backup = None
            # Cap dimensione: tieni un solo set di stats (l'ultimo).
            _STATS_CACHE.clear()
            _STATS_CACHE[stats_key] = (time.monotonic(), {
                'filone': filone_stats,
                'andamento_lcl': andamento_lcl,
                'data_backup': data_backup,
            })

        # 2. Attività calendario (oggi e prossime)
        oggi_str = date.today().isoformat()
        mese_str = date.today().strftime('%Y-%m')
        tutte_attivita = get_attivita(provincia=PROVINCIA_ATTIVA)
        attivita_oggi = [a for a in tutte_attivita if a.get('data') == oggi_str]
        attivita_prossime = [
            a for a in tutte_attivita
            if not a.get('completata') and a.get('data', '') >= oggi_str
        ][:8]

        cal_stats = {
            'totale_mese': len([a for a in tutte_attivita if a.get('data', '').startswith(mese_str)]),
            'completate_mese': len([a for a in tutte_attivita
                                    if a.get('data', '').startswith(mese_str) and a.get('completata')]),
            'oggi': len(attivita_oggi),
            'oggi_completate': len([a for a in attivita_oggi if a.get('completata')]),
            'prossime': attivita_prossime,
        }

        # 3. Bacheca promemoria (basata sul calendario, esclusi boxbeat)
        bacheca_items = [a for a in tutte_attivita if a.get('categoria') != 'boxbeat' and not a.get('completata')]
        bacheca_oggi = [a for a in bacheca_items if a.get('data') == oggi_str]
        bacheca_urgenti = [
            a for a in bacheca_items
            if a.get('data') and a.get('data') <= (date.today() + timedelta(days=2)).isoformat()
        ]

        bacheca_stats = {
            'totale': len(bacheca_items),
            'oggi': len(bacheca_oggi),
            'urgenti': len(bacheca_urgenti),
            'items_urgenti': bacheca_urgenti[:5],
        }

        return jsonify({
            'success': True,
            'provincia': PROVINCIA_ATTIVA,
            'data_backup': data_backup,
            'timestamp': datetime.now().isoformat(),
            'filone': filone_stats,
            'andamento_lcl': andamento_lcl,
            'calendario': cal_stats,
            'bacheca': bacheca_stats,
        })

    except Exception as e:
        logger.error(f"[Dashboard] Errore: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Errore calcolo dashboard: {e}'}), 500
