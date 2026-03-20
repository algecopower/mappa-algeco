# -*- coding: utf-8 -*-
"""
Blueprint per le API Sigilli e Matricole.
Gestione inserimento sigilli e matricole per contatori installati.
"""

import logging
from datetime import datetime

import gspread.utils
from flask import Blueprint, request, jsonify

from moduli.auth import login_required
from moduli.shared import (
    get_worksheet, _trova_indice_multi, _api_error,
)
from config import PROVINCIA_ATTIVA, COLONNE_SIGILLI

logger = logging.getLogger('mappa_algeco')
bp = Blueprint('sigilli', __name__)


# ============================================================================
# API SIGILLI - Gestione inserimento sigilli contatori installati
# ============================================================================

@bp.route('/api/sigilli/pending')
@login_required
def get_sigilli_pending():
    """
    Restituisce i contatori installati (Esito OK) che non hanno ancora il sigillo.
    Filtrati per: Semidiretti (con o senza Trifase) OPPURE Fotovoltaici (ENELTEL termina con A o B).
    Ordinati per data installazione più recente.
    """
    try:
        ws = get_worksheet()
        all_data = ws.get_all_values()
        headers = all_data[0] if all_data else []

        # Configurazione colonne per provincia
        cfg = COLONNE_SIGILLI.get(PROVINCIA_ATTIVA, {})
        esito_ok = cfg.get('esito_ok_valore', 'Esito OK')

        # Trova indici colonne (usa helper condiviso + mappatura personalizzata)
        idx_pod = _trova_indice_multi(headers, 'POD', 'Pod', 'pod', chiave_mapping='pod')
        idx_eneltel = _trova_indice_multi(headers, 'ENELTEL', 'Eneltel', 'eneltel', chiave_mapping='eneltel')
        idx_cliente = _trova_indice_multi(headers, 'NOMINATIVO', 'Nominativo', 'Cliente', 'CLIENTE', chiave_mapping='nominativo')
        idx_indirizzo = _trova_indice_multi(headers, 'Indirizzo', 'Via', 'VIA', chiave_mapping='indirizzo')
        idx_comune = _trova_indice_multi(headers, 'COMUNE', 'Comune', chiave_mapping='comune')
        idx_sigillo = _trova_indice_multi(headers, 'SIGILLO', 'Sigillo', 'sigillo', chiave_mapping='sigillo')
        idx_data_inst = _trova_indice_multi(headers, 'Data Installazione', 'DATA INSTALLAZIONE', chiave_mapping='data_installazione')
        idx_operatore = _trova_indice_multi(headers, 'Operatore', 'OPERATORE', chiave_mapping='operatore')
        idx_esito = _trova_indice_multi(headers, 'Esito', 'ESITO', chiave_mapping='esito')
        idx_tipologia = _trova_indice_multi(headers, 'Tipo_Misuratore', 'TIPOLOGIA CE', 'Tipologia CE', chiave_mapping='tipo_misuratore')

        if idx_esito is None:
            return jsonify({'error': 'Colonna Esito non trovata'}), 400

        # Filtra contatori: Esito OK + Operatore + Data Installazione + Sigillo vuoto
        # + (Semidiretti OPPURE ENELTEL termina con A/B)
        contatori = []
        for row_idx, row in enumerate(all_data[1:], start=2):
            # Verifica esito OK
            esito_val = row[idx_esito].strip().upper() if idx_esito < len(row) else ''
            if esito_ok.upper() not in esito_val:
                continue

            # Verifica data installazione presente
            data_inst = row[idx_data_inst].strip() if idx_data_inst is not None and idx_data_inst < len(row) else ''
            if not data_inst:
                continue

            # === FILTRO ANNO CORRENTE ===
            # Mostra solo installati nell'anno corrente
            anno_corrente = str(datetime.now().year)
            if anno_corrente not in data_inst:
                continue  # Non è dell'anno corrente, skip

            # Verifica operatore presente
            operatore = row[idx_operatore].strip() if idx_operatore is not None and idx_operatore < len(row) else ''
            if not operatore:
                continue

            # Verifica sigillo vuoto
            sigillo = row[idx_sigillo].strip() if idx_sigillo is not None and idx_sigillo < len(row) else ''
            if sigillo:
                continue  # Ha già il sigillo, skip

            # === FILTRO TIPOLOGIA ===
            # Prendi ENELTEL e tipologia
            eneltel = row[idx_eneltel].strip() if idx_eneltel is not None and idx_eneltel < len(row) else ''
            tipologia = row[idx_tipologia].strip() if idx_tipologia is not None and idx_tipologia < len(row) else ''

            # Verifica se è: Semidiretti (con o senza Trifase) OPPURE Fotovoltaico (ENELTEL termina con A o B)
            is_semidiretto = 'semidiretto' in tipologia.lower()
            is_fotovoltaico = eneltel.upper().endswith('A') or eneltel.upper().endswith('B')

            if not is_semidiretto and not is_fotovoltaico:
                continue  # Non è né semidiretto né fotovoltaico, skip

            # Determina tipo per visualizzazione
            tipo_sigillo = 'Semidiretto' if is_semidiretto else 'Fotovoltaico'

            # Aggiungi alla lista
            contatori.append({
                'row': row_idx,
                'pod': row[idx_pod] if idx_pod is not None and idx_pod < len(row) else '',
                'eneltel': eneltel,
                'cliente': row[idx_cliente] if idx_cliente is not None and idx_cliente < len(row) else '',
                'indirizzo': row[idx_indirizzo] if idx_indirizzo is not None and idx_indirizzo < len(row) else '',
                'comune': row[idx_comune] if idx_comune is not None and idx_comune < len(row) else '',
                'data_installazione': data_inst,
                'operatore': operatore,
                'tipo': tipo_sigillo,
                'tipologia_raw': tipologia,
            })

        # Ordina per data installazione (più recente prima)
        def parse_data(d):
            try:
                # Prova vari formati
                for fmt in ['%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y']:
                    try:
                        return datetime.strptime(d, fmt)
                    except Exception:
                        pass
                return datetime.min
            except Exception:
                return datetime.min

        contatori.sort(key=lambda x: parse_data(x['data_installazione']), reverse=True)

        logger.info(f"[SIGILLI] Trovati {len(contatori)} contatori (Trifase/Fotovoltaici) installati senza sigillo")

        return jsonify({
            'success': True,
            'contatori': contatori,
            'total': len(contatori)
        })

    except Exception as e:
        return _api_error(e, 'caricamento sigilli pendenti')


@bp.route('/api/sigillo/<eneltel>', methods=['PUT'])
@login_required
def salva_sigillo(eneltel):
    """
    Salva il sigillo per un contatore e evidenzia la cella in giallo.
    """
    try:
        data = request.json
        sigillo = data.get('sigillo', '').strip()

        if not sigillo:
            return jsonify({'error': 'Sigillo vuoto'}), 400

        ws = get_worksheet()
        all_data = ws.get_all_values()
        headers = all_data[0] if all_data else []

        # Trova indice colonna ENELTEL
        idx_eneltel = None
        for i, h in enumerate(headers):
            if h.strip().upper() == 'ENELTEL':
                idx_eneltel = i
                break

        if idx_eneltel is None:
            return jsonify({'error': 'Colonna ENELTEL non trovata'}), 400

        # Trova la riga del contatore
        row_num = None
        for row_idx, row in enumerate(all_data[1:], start=2):
            if idx_eneltel < len(row) and row[idx_eneltel].strip() == eneltel:
                row_num = row_idx
                break

        if row_num is None:
            return jsonify({'error': f'Contatore {eneltel} non trovato'}), 404

        # Trova colonna SIGILLO
        idx_sigillo = _trova_indice_multi(headers, 'SIGILLO', 'Sigillo', 'sigillo', chiave_mapping='sigillo')

        if idx_sigillo is None:
            return jsonify({'error': 'Colonna SIGILLO non trovata'}), 400

        # Calcola riferimento cella (A1 notation)
        col_letter = gspread.utils.rowcol_to_a1(1, idx_sigillo + 1)[:-1]  # Rimuovi il numero riga
        cell_ref = f"{col_letter}{row_num}"

        # Aggiorna valore sigillo
        ws.update_acell(cell_ref, sigillo)

        logger.info(f"[SIGILLI] ✅ Sigillo '{sigillo}' salvato per {eneltel} (cella {cell_ref})")

        return jsonify({
            'success': True,
            'message': f'Sigillo salvato per {eneltel}',
            'sigillo': sigillo,
            'cell': cell_ref
        })

    except Exception as e:
        return _api_error(e, 'salvataggio sigillo')


# ============================================================================
# API MATRICOLE - Gestione inserimento matricole contatori installati
# ============================================================================

@bp.route('/api/matricole/pending')
@login_required
def get_matricole_pending():
    """
    Restituisce i contatori installati (Esito OK) che non hanno ancora la matricola.
    TUTTI i tipi di contatori (nessun filtro tipologia).
    Ordinati per data installazione più recente.
    """
    try:
        ws = get_worksheet()
        all_data = ws.get_all_values()
        headers = all_data[0] if all_data else []

        # Configurazione colonne per provincia
        cfg = COLONNE_SIGILLI.get(PROVINCIA_ATTIVA, {})
        esito_ok = cfg.get('esito_ok_valore', 'Esito OK')

        # Trova indici colonne (usa helper condiviso + mappatura personalizzata)
        idx_pod = _trova_indice_multi(headers, 'POD', 'Pod', 'pod', chiave_mapping='pod')
        idx_eneltel = _trova_indice_multi(headers, 'ENELTEL', 'Eneltel', 'eneltel', chiave_mapping='eneltel')
        idx_cliente = _trova_indice_multi(headers, 'NOMINATIVO', 'Nominativo', 'Cliente', 'CLIENTE', chiave_mapping='nominativo')
        idx_indirizzo = _trova_indice_multi(headers, 'Indirizzo', 'Via', 'VIA', chiave_mapping='indirizzo')
        idx_comune = _trova_indice_multi(headers, 'COMUNE', 'Comune', chiave_mapping='comune')
        idx_matricola = _trova_indice_multi(headers, 'Matricola CE inst', 'Matricola misuratore installato', chiave_mapping='matricola_installata')
        idx_data_inst = _trova_indice_multi(headers, 'Data Installazione', 'DATA INSTALLAZIONE', chiave_mapping='data_installazione')
        idx_operatore = _trova_indice_multi(headers, 'Operatore', 'OPERATORE', chiave_mapping='operatore')
        idx_esito = _trova_indice_multi(headers, 'Esito', 'ESITO', chiave_mapping='esito')

        if idx_esito is None:
            return jsonify({'error': 'Colonna Esito non trovata'}), 400

        # Filtra contatori: Esito OK + Operatore + Data Installazione + Matricola vuota
        # NESSUN filtro tipologia per le matricole
        contatori = []
        for row_idx, row in enumerate(all_data[1:], start=2):
            # Verifica esito OK
            esito_val = row[idx_esito].strip().upper() if idx_esito < len(row) else ''
            if esito_ok.upper() not in esito_val:
                continue

            # Verifica data installazione presente
            data_inst = row[idx_data_inst].strip() if idx_data_inst is not None and idx_data_inst < len(row) else ''
            if not data_inst:
                continue

            # === FILTRO ANNO CORRENTE ===
            anno_corrente = str(datetime.now().year)
            if anno_corrente not in data_inst:
                continue

            # Verifica operatore presente
            operatore = row[idx_operatore].strip() if idx_operatore is not None and idx_operatore < len(row) else ''
            if not operatore:
                continue

            # Verifica matricola vuota
            matricola = row[idx_matricola].strip() if idx_matricola is not None and idx_matricola < len(row) else ''
            if matricola:
                continue  # Ha già la matricola, skip

            eneltel = row[idx_eneltel].strip() if idx_eneltel is not None and idx_eneltel < len(row) else ''

            # Aggiungi alla lista
            contatori.append({
                'row': row_idx,
                'pod': row[idx_pod] if idx_pod is not None and idx_pod < len(row) else '',
                'eneltel': eneltel,
                'cliente': row[idx_cliente] if idx_cliente is not None and idx_cliente < len(row) else '',
                'indirizzo': row[idx_indirizzo] if idx_indirizzo is not None and idx_indirizzo < len(row) else '',
                'comune': row[idx_comune] if idx_comune is not None and idx_comune < len(row) else '',
                'data_installazione': data_inst,
                'operatore': operatore,
            })

        # Ordina per data installazione (più recente prima)
        def parse_data(d):
            try:
                for fmt in ['%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y']:
                    try:
                        return datetime.strptime(d, fmt)
                    except Exception:
                        pass
                return datetime.min
            except Exception:
                return datetime.min

        contatori.sort(key=lambda x: parse_data(x['data_installazione']), reverse=True)

        logger.info(f"[MATRICOLE] Trovati {len(contatori)} contatori installati senza matricola")

        return jsonify({
            'success': True,
            'contatori': contatori,
            'total': len(contatori)
        })

    except Exception as e:
        return _api_error(e, 'caricamento matricole pendenti')


@bp.route('/api/matricola/<eneltel>', methods=['PUT'])
@login_required
def salva_matricola(eneltel):
    """
    Salva la matricola per un contatore e evidenzia la cella in verde.
    """
    try:
        data = request.json
        matricola = data.get('matricola', '').strip()

        if not matricola:
            return jsonify({'error': 'Matricola vuota'}), 400

        ws = get_worksheet()
        all_data = ws.get_all_values()
        headers = all_data[0] if all_data else []

        # Trova indice colonna ENELTEL
        idx_eneltel = _trova_indice_multi(headers, 'ENELTEL', 'Eneltel', 'eneltel', chiave_mapping='eneltel')

        if idx_eneltel is None:
            return jsonify({'error': 'Colonna ENELTEL non trovata'}), 400

        # Trova la riga del contatore
        row_num = None
        for row_idx, row in enumerate(all_data[1:], start=2):
            if idx_eneltel < len(row) and row[idx_eneltel].strip() == eneltel:
                row_num = row_idx
                break

        if row_num is None:
            return jsonify({'error': f'Contatore {eneltel} non trovato'}), 404

        # Trova colonna Matricola
        idx_matricola = _trova_indice_multi(headers, 'Matricola CE inst', 'Matricola misuratore installato', chiave_mapping='matricola_installata')

        if idx_matricola is None:
            return jsonify({'error': 'Colonna Matricola non trovata'}), 400

        # Calcola riferimento cella (A1 notation)
        col_letter = gspread.utils.rowcol_to_a1(1, idx_matricola + 1)[:-1]
        cell_ref = f"{col_letter}{row_num}"

        # Aggiorna valore matricola
        ws.update_acell(cell_ref, matricola)

        logger.info(f"[MATRICOLE] ✅ Matricola '{matricola}' salvata per {eneltel} (cella {cell_ref})")

        return jsonify({
            'success': True,
            'message': f'Matricola salvata per {eneltel}',
            'matricola': matricola,
            'cell': cell_ref
        })

    except Exception as e:
        return _api_error(e, 'salvataggio matricola')
