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
        return json_read(os.path.join(BACKUP_DIR, files[0]), default=None)
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
    """Restituisce i dati aggregati per la dashboard."""
    try:
        # 1. Statistiche da backup FILONE
        backup = _carica_ultimo_backup()
        if backup:
            filone_stats = _calcola_statistiche_filone(backup)
            andamento_lcl = _calcola_andamento_lcl(backup)
            data_backup = backup.get('data_backup', '?')
        else:
            filone_stats = {}
            andamento_lcl = {}
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
