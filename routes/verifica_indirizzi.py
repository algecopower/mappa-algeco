# -*- coding: utf-8 -*-
"""
Route per la verifica degli indirizzi dei contatori contro il registro
nazionale dei numeri civici (INDIR_PIEM, ANNCSU).

L'utility filtra i contatori per LCL/Comune e per ciascuno verifica:
  - che la via esista nel registro
  - che il civico sia registrato (o sia plausibile)
e suggerisce correzioni per i casi anomali.
"""
import logging
import os
from collections import Counter

from flask import Blueprint, jsonify, request

from moduli.auth import login_required
from moduli.safe_json import json_read
from moduli import verifica_indirizzi as vi
from config import PROVINCIA_ATTIVA
try:
    from config import LCL_DISABILITATI
except ImportError:
    LCL_DISABILITATI = []

logger = logging.getLogger('mappa_algeco')

bp = Blueprint('verifica_indirizzi', __name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP_DIR = os.path.join(BASE_DIR, 'backup', PROVINCIA_ATTIVA.lower())


def _carica_ultimo_backup() -> dict | None:
    if not os.path.isdir(BACKUP_DIR):
        return None
    files = sorted(
        [f for f in os.listdir(BACKUP_DIR) if f.endswith('.json')],
        reverse=True,
    )
    if not files:
        return None
    return json_read(os.path.join(BACKUP_DIR, files[0]), default=None)


def _col_idx(headers: list, nomi_alt: list) -> int | None:
    """Trova indice colonna per uno dei nomi alternativi (case-insensitive)."""
    lower = {h.lower().strip(): i for i, h in enumerate(headers)}
    for n in nomi_alt:
        i = lower.get(n.lower().strip())
        if i is not None:
            return i
    return None


def _val(row: list, idx: int | None) -> str:
    if idx is None or idx >= len(row):
        return ''
    return str(row[idx]).strip() if row[idx] is not None else ''


@bp.route('/api/verifica-indirizzi/stato', methods=['GET'])
@login_required
def stato_db():
    """Ritorna info sul DB e sui filtri disponibili (LCL, Comuni)."""
    info = vi.stato_db()
    backup = _carica_ultimo_backup()

    lcl_list: list[str] = []
    comuni_list: list[str] = []
    lcl_to_comuni: dict[str, list[str]] = {}
    disabled_lcls = {str(x).strip().upper() for x in (LCL_DISABILITATI or []) if str(x).strip()}
    if backup:
        headers = backup.get('headers', [])
        righe = backup.get('righe', [])
        i_lcl = _col_idx(headers, ['LCL'])
        i_comune = _col_idx(headers, ['Comune', 'COMUNE'])
        if i_comune is not None:
            comuni_list = sorted({_val(r, i_comune) for r in righe if not (_val(r, i_lcl) and str(_val(r, i_lcl)).strip().upper() in disabled_lcls)} - {''})
        if i_lcl is not None and i_comune is not None:
            mapping: dict[str, set[str]] = {}
            for r in righe:
                lcl = _val(r, i_lcl)
                com = _val(r, i_comune)
                if not lcl or str(lcl).strip().upper() in disabled_lcls:
                    continue
                mapping.setdefault(lcl, set())
                if com:
                    mapping[lcl].add(com)
            lcl_list = sorted(mapping.keys())
            lcl_to_comuni = {k: sorted(v) for k, v in mapping.items()}
        elif i_lcl is not None:
            lcl_list = sorted({_val(r, i_lcl) for r in righe if _val(r, i_lcl) and str(_val(r, i_lcl)).strip().upper() not in disabled_lcls} - {''})

    return jsonify({
        'db': info,
        'lcl': lcl_list,
        'comuni': comuni_list,
        'lcl_to_comuni': lcl_to_comuni,
        'totale_contatori': len(backup.get('righe', [])) if backup else 0,
    })


@bp.route('/api/verifica-indirizzi', methods=['POST'])
@login_required
def verifica():
    """
    Body JSON:
      { "lcl": "...", "comune": "...", "solo_problematici": true }
    Filtri sono opzionali (None/'' = nessun filtro). Se entrambi vuoti
    verifica TUTTI i contatori (può essere lento).
    """
    if not vi.db_disponibile():
        return jsonify({
            'error': "DB indirizzi non disponibile",
            'hint': "Esegui: python tools/build_indir_db.py",
        }), 503

    body = request.get_json(silent=True) or {}
    f_lcl = (body.get('lcl') or '').strip()
    f_comune = (body.get('comune') or '').strip()
    solo_problematici = bool(body.get('solo_problematici', True))

    backup = _carica_ultimo_backup()
    if not backup:
        return jsonify({'error': 'Backup FILONE non trovato'}), 404

    headers = backup.get('headers', [])
    righe = backup.get('righe', [])

    i_lcl = _col_idx(headers, ['LCL'])
    i_comune = _col_idx(headers, ['Comune', 'COMUNE'])
    i_indirizzo = _col_idx(headers, ['Indirizzo', 'INDIRIZZO', 'Via', 'VIA'])
    i_civico = _col_idx(headers, ['Num', 'NUM', 'Civico', 'CIVICO', 'N. Civico'])
    i_eneltel = _col_idx(headers, ['Eneltel', 'ENELTEL'])
    i_cliente = _col_idx(headers, ['NOMINATIVO', 'Cliente', 'CLI_K'])

    if i_indirizzo is None or i_comune is None:
        return jsonify({'error': "Colonne 'Indirizzo' o 'Comune' non trovate nel backup"}), 500

    # Applica filtri
    disabled_lcls = {str(x).strip().upper() for x in (LCL_DISABILITATI or []) if str(x).strip()}
    selezionati = []
    for r in righe:
        lcl_r = _val(r, i_lcl)
        if lcl_r and str(lcl_r).strip().upper() in disabled_lcls:
            continue
        if f_lcl and lcl_r != f_lcl:
            continue
        if f_comune and _val(r, i_comune) != f_comune:
            continue
        selezionati.append(r)

    # Verifica
    risultati = []
    contatori_stati = Counter()
    for r in selezionati:
        comune = _val(r, i_comune)
        indirizzo = _val(r, i_indirizzo)
        civico = _val(r, i_civico)
        v = vi.verifica_singolo(comune, indirizzo, civico)
        contatori_stati[v['status']] += 1

        if solo_problematici and v['status'] in ('ok', 'fuori_regione'):
            continue

        risultati.append({
            'eneltel': _val(r, i_eneltel),
            'cliente': _val(r, i_cliente),
            'lcl': _val(r, i_lcl),
            'comune': comune,
            'indirizzo': indirizzo,
            'civico': civico,
            'verifica': v,
        })

    return jsonify({
        'totale_filtrati': len(selezionati),
        'totale_problematici': sum(
            n for k, n in contatori_stati.items() if k not in ('ok', 'fuori_regione')
        ),
        'stati': dict(contatori_stati),
        'risultati': risultati,
    })
