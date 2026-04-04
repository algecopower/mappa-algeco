# -*- coding: utf-8 -*-
"""
Route Dashboard — vista riepilogativa con statistiche operative.
Legge i dati dal backup locale più recente (nessuna chiamata extra a Google Sheets).
"""
import os
import json
import logging
from datetime import datetime, date, timedelta
from collections import Counter, defaultdict

from flask import Blueprint, jsonify, send_file, session
from moduli.auth import login_required
from moduli.calendario import get_attivita
from moduli.postit import get_postit
from config import PROVINCIA_ATTIVA, MATRICOLE_VIP, COLORI_TECNICI, OPERATORI_DICT

logger = logging.getLogger('mappa_algeco')

bp = Blueprint('dashboard', __name__)

# Directory base
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP_DIR = os.path.join(BASE_DIR, 'backup', PROVINCIA_ATTIVA.lower())


def _carica_ultimo_backup() -> dict | None:
    """Carica il backup locale più recente del FILONE."""
    if not os.path.isdir(BACKUP_DIR):
        return None
    files = sorted(
        [f for f in os.listdir(BACKUP_DIR) if f.endswith('.json')],
        reverse=True,
    )
    if not files:
        return None
    try:
        with open(os.path.join(BACKUP_DIR, files[0]), 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"[Dashboard] Errore lettura backup {files[0]}: {e}")
        return None


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


def _parse_data(val: str) -> str | None:
    """Converte una data grezza in YYYY-MM-DD (best-effort)."""
    val = val.strip()
    if not val or len(val) < 6 or 'annullat' in val.lower():
        return None
    import re
    val_clean = re.split(r'\s+', val)[0]
    from moduli.data_loader import parse_data_flessibile
    return parse_data_flessibile(val_clean)


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
    i_eneltel = _col_index(headers, ['Eneltel', 'ENELTEL'])

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
        try:
            with open(cache_dist_path, 'r', encoding='utf-8') as f:
                cache_distanze = json.load(f)
        except Exception:
            pass

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
    """Restituisce i dati aggregati per la dashboard."""
    try:
        # 1. Statistiche da backup FILONE
        backup = _carica_ultimo_backup()
        if backup:
            filone_stats = _calcola_statistiche_filone(backup)
            data_backup = backup.get('data_backup', '?')
        else:
            filone_stats = {}
            data_backup = None

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

        # 3. Post-it attivi
        postit = get_postit(provincia=PROVINCIA_ATTIVA)
        postit_alti = [p for p in postit if p.get('priorita') == 'alta']
        postit_in_scadenza = [
            p for p in postit
            if p.get('scadenza') and p.get('scadenza') <= (date.today() + timedelta(days=2)).isoformat()
        ]

        postit_stats = {
            'totale': len(postit),
            'alta_priorita': len(postit_alti),
            'in_scadenza': len(postit_in_scadenza),
            'items_urgenti': (postit_alti + postit_in_scadenza)[:5],
        }

        return jsonify({
            'success': True,
            'provincia': PROVINCIA_ATTIVA,
            'data_backup': data_backup,
            'timestamp': datetime.now().isoformat(),
            'filone': filone_stats,
            'calendario': cal_stats,
            'postit': postit_stats,
        })

    except Exception as e:
        logger.error(f"[Dashboard] Errore: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Errore calcolo dashboard: {e}'}), 500
