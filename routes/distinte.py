# -*- coding: utf-8 -*-
"""
Blueprint per le API Distinte — gestione distinte e messaggi.
"""
import os
import json
import tempfile
import logging

import gspread.utils
from flask import Blueprint, request, jsonify, session, send_file

from moduli.auth import login_required
from moduli.shared import (
    get_worksheet, get_header_map, get_headers,
    find_row_by_eneltel, find_row_by_pod,
    _imposta_appuntamento_su_filone, _rimuovi_appuntamento_da_filone,
    _api_error, _get_colonna_note, _get_username,
    _trova_indice_colonna, _trova_indice_multi,
    _callbacks, _sheets_retry,
)
from moduli.distinte_manager import (
    parse_distinta_pdf,
    is_cellulare,
    classifica_contatti,
    confronta_con_filone,
)
from moduli.pec_lookup import (
    arricchisci_contatti as arricchisci_contatti_pec,
    genera_tutti_link_ricerca,
    classifica_tipo_contatto,
    is_azienda,
)
from config import PROVINCIA_ATTIVA

logger = logging.getLogger('mappa_algeco')

bp = Blueprint('distinte', __name__)

# Percorso root del progetto (una directory sopra moduli/)
_ROOT_DIR = os.path.join(os.path.dirname(__file__), '..')


@bp.route('/distinte')
@login_required
def serve_distinte_page():
    """Serve la pagina di gestione distinte."""
    return send_file(os.path.join(_ROOT_DIR, 'templates', 'distinte.html'))


@bp.route('/whatsapp')
@login_required
def serve_whatsapp_redirect():
    """Redirect alla nuova pagina distinte (retrocompatibilità)."""
    return send_file(os.path.join(_ROOT_DIR, 'templates', 'distinte.html'))


# Percorso del file JSON dei template messaggi
_TEMPLATES_JSON = os.path.join(_ROOT_DIR, 'data', 'distinte_templates.json')


@bp.route('/api/distinte/templates', methods=['GET'])
@login_required
def get_distinte_templates():
    """Restituisce i template messaggi dal file JSON centralizzato."""
    try:
        with open(_TEMPLATES_JSON, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return jsonify(data)
    except FileNotFoundError:
        return jsonify({'error': 'File template non trovato'}), 404
    except json.JSONDecodeError as e:
        return jsonify({'error': f'Errore parsing JSON: {str(e)}'}), 500


@bp.route('/api/distinte/templates', methods=['PUT'])
@login_required
def save_distinte_templates():
    """Salva i template messaggi aggiornati nel file JSON."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Nessun dato ricevuto'}), 400
        with open(_TEMPLATES_JSON, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        return jsonify({'ok': True})
    except Exception as e:
        logger.error(f"Errore salvataggio template: {e}")
        return jsonify({'error': str(e)}), 500


@bp.route('/api/distinte/parse-distinta', methods=['POST'])
@login_required
def parse_distinta_endpoint():
    """
    Riceve file PDF DistintaBase, li elabora e restituisce i contatti.
    Confronta anche con i dati del filone per rilevare differenze.
    """
    try:
        if 'files' not in request.files:
            return jsonify({'error': 'Nessun file caricato'}), 400

        files = request.files.getlist('files')
        if not files or all(f.filename == '' for f in files):
            return jsonify({'error': 'Nessun file selezionato'}), 400

        # Salva temporaneamente i file e elaborali
        temp_paths = []
        all_lavori = []

        try:
            for file in files:
                if file and file.filename:
                    # Crea file temporaneo
                    temp_fd, temp_path = tempfile.mkstemp(suffix='.pdf')
                    os.close(temp_fd)
                    file.save(temp_path)
                    temp_paths.append(temp_path)

                    # Parsa il PDF
                    lavori = parse_distinta_pdf(temp_path)
                    logger.info(f"[DISTINTE] Estratti {len(lavori)} lavori da {file.filename}")
                    all_lavori.extend(lavori)
        finally:
            # Pulisci file temporanei
            for path in temp_paths:
                try:
                    os.unlink(path)
                except Exception:
                    pass

        if not all_lavori:
            return jsonify({
                'error': 'Nessun lavoro estratto dai PDF',
                'contatti': [],
                'differenze': []
            }), 400

        # Carica dati filone per confronto e classificazione
        dati_filone = {}
        differenze = []
        try:
            ws = get_worksheet()
            all_data = ws.get_all_values()
            headers = all_data[0] if all_data else []

            # Trova indici colonne (usa helper condivisi)
            trova_idx = lambda nome: _trova_indice_colonna(headers, nome)
            trova_idx_multi = lambda *nomi: _trova_indice_multi(headers, *nomi)

            idx_pod = trova_idx('POD')
            idx_eneltel = trova_idx_multi('ENELTEL', 'Eneltel', 'eneltel')
            idx_data = trova_idx_multi('DATA PIAN', 'Data_pianificata')
            idx_telefono = trova_idx_multi('telefono', 'tel', 'cellulare', 'cell', 'numero telefono')
            idx_nominativo = trova_idx_multi('NOMINATIVO', 'Nominativo')
            idx_indirizzo = trova_idx_multi('Indirizzo', 'Via')
            idx_civico = trova_idx_multi('Num', 'civico')
            idx_comune = trova_idx_multi('COMUNE', 'Comune')
            idx_note = trova_idx_multi('NOTE APP', 'NOTE')
            idx_codice_ante = trova_idx_multi('Codice_Ante_Sostituzione', 'Codice misuratore in opera')
            idx_matricola_ante = trova_idx_multi('Matricola_Ante_Sostituzione', 'Matricola misuratore in opera')
            idx_tipologia = trova_idx_multi('Tipo_Misuratore', 'TIPOLOGIA CE')
            idx_posizione = trova_idx_multi('Posizione_Mis_Ante_Sost', 'Posizione_Ante_Mis', 'Posizione misuratore', 'POSIZIONE', 'Posizione')
            idx_ora_pian = trova_idx_multi('Ora pian', 'ORA PIAN', 'ORA_PIAN') if PROVINCIA_ATTIVA == 'PAVIA' else None

            for row in all_data[1:]:
                if idx_pod is not None and idx_pod < len(row):
                    pod = row[idx_pod].strip().upper()
                    if pod:
                        codice_ante = (row[idx_codice_ante].strip() if idx_codice_ante is not None and idx_codice_ante < len(row) else '')
                        matricola_ante = (row[idx_matricola_ante].strip() if idx_matricola_ante is not None and idx_matricola_ante < len(row) else '')
                        # Combina codice e matricola in unico blocco
                        matricola_completa = ' / '.join(filter(None, [codice_ante, matricola_ante]))

                        dati_filone[pod] = {
                            'pod': pod,
                            'eneltel': row[idx_eneltel].strip() if idx_eneltel is not None and idx_eneltel < len(row) else '',
                            'data_pian': row[idx_data] if idx_data is not None and idx_data < len(row) else '',
                            'telefono': row[idx_telefono] if idx_telefono is not None and idx_telefono < len(row) else '',
                            'nominativo': row[idx_nominativo] if idx_nominativo is not None and idx_nominativo < len(row) else '',
                            'indirizzo': row[idx_indirizzo] if idx_indirizzo is not None and idx_indirizzo < len(row) else '',
                            'civico': row[idx_civico] if idx_civico is not None and idx_civico < len(row) else '',
                            'comune': row[idx_comune] if idx_comune is not None and idx_comune < len(row) else '',
                            'note': row[idx_note] if idx_note is not None and idx_note < len(row) else '',
                            'matricola': matricola_completa,
                            'tipologia': row[idx_tipologia].strip() if idx_tipologia is not None and idx_tipologia < len(row) else '',
                            'posizione': row[idx_posizione].strip() if idx_posizione is not None and idx_posizione < len(row) else '',
                            'ora_pian': row[idx_ora_pian].strip() if idx_ora_pian is not None and idx_ora_pian < len(row) else '',
                        }

            # Confronta distinta con filone
            differenze = confronta_con_filone(all_lavori, dati_filone)
            logger.info(f"[DISTINTE] Rilevate {len(differenze)} differenze con il filone")

        except Exception as e:
            logger.error(f"[DISTINTE] Errore caricamento filone: {e}")

        # Classifica i contatti per tipo di telefono (passa dati filone per trovare numeri alternativi)
        classificati = classifica_contatti(all_lavori, dati_filone)

        # Prepara lista contatti con tipo
        contatti = []
        for lavoro in classificati['cellulare']:
            lavoro['tipo'] = 'cellulare'
            contatti.append(lavoro)
        for lavoro in classificati['fisso']:
            lavoro['tipo'] = 'fisso'
            contatti.append(lavoro)
        for lavoro in classificati['senza_telefono']:
            lavoro['tipo'] = 'senza_telefono'
            contatti.append(lavoro)

        # Ordina per numero lavoro
        contatti.sort(key=lambda x: x.get('lavoro', 999))

        # Aggiungi le note, la data e il telefono del filone ai contatti
        logger.info(f"[DISTINTE] idx_telefono={idx_telefono}, dati_filone contiene {len(dati_filone)} PODs")
        for contatto in contatti:
            pod = contatto.get('pod', '').strip().upper()
            # Inizializza defaults per tutti
            contatto.setdefault('note_filone', '')
            contatto.setdefault('data_pian_filone', '')
            contatto.setdefault('telefono_filone', '')
            contatto.setdefault('matricola_filone', '')
            contatto.setdefault('pod_filone', '')
            contatto.setdefault('eneltel_filone', '')
            contatto.setdefault('tipologia_contatore', '')
            contatto.setdefault('posizione_contatore', '')
            contatto.setdefault('ora_pian_filone', '')
            if pod in dati_filone:
                contatto['note_filone'] = dati_filone[pod].get('note', '')
                contatto['data_pian_filone'] = dati_filone[pod].get('data_pian', '')
                contatto['telefono_filone'] = dati_filone[pod].get('telefono', '')
                contatto['matricola_filone'] = dati_filone[pod].get('matricola', '')
                contatto['pod_filone'] = dati_filone[pod].get('pod', '')
                contatto['eneltel_filone'] = dati_filone[pod].get('eneltel', '')
                contatto['tipologia_contatore'] = dati_filone[pod].get('tipologia', '')
                contatto['posizione_contatore'] = dati_filone[pod].get('posizione', '')
                contatto['ora_pian_filone'] = dati_filone[pod].get('ora_pian', '')
                # Se il nome dalla distinta è vuoto, usa il nominativo dal filone
                if not contatto.get('nome', '').strip():
                    nominativo_filone = dati_filone[pod].get('nominativo', '').strip()
                    if nominativo_filone:
                        contatto['nome'] = nominativo_filone
                        contatto['nome_da_filone'] = True
                # Se il comune dalla distinta è vuoto, usa il comune dal filone
                if not contatto.get('comune', '').strip():
                    comune_filone = dati_filone[pod].get('comune', '').strip()
                    if comune_filone:
                        contatto['comune'] = comune_filone
                if contatto['telefono_filone']:
                    logger.info(f"[DISTINTE] POD {pod}: telefono_filone='{contatto['telefono_filone']}'")
            else:
                if pod:
                    logger.info(f"[DISTINTE] POD {pod}: NON trovato in dati_filone")

        # Arricchisci con info tipo contatto e link ricerca PEC
        try:
            contatti = arricchisci_contatti_pec(contatti)
            n_aziende = sum(1 for c in contatti if c.get('is_azienda'))
            logger.info(f"[DISTINTE] PEC: {n_aziende} aziende/enti rilevati")
        except Exception as e:
            logger.error(f"[DISTINTE] Errore arricchimento PEC: {e}")

        return jsonify({
            'success': True,
            'contatti': contatti,
            'differenze': differenze,
            'totale': len(contatti),
            'cellulare': len(classificati['cellulare']),
            'fisso': len(classificati['fisso']),
            'senza_telefono': len(classificati['senza_telefono'])
        })

    except Exception as e:
        return _api_error(e, 'parsing distinta')


@bp.route('/api/distinte/sovrascrivi', methods=['POST'])
@login_required
def sovrascrivi_valore_filone():
    """
    Sovrascrive un valore sul filone con quello della distinta.
    """
    try:
        data = request.json
        pod = data.get('pod', '').strip().upper()
        campo = data.get('campo', '')
        valore = data.get('valore', '')

        if not pod:
            return jsonify({'error': 'POD mancante'}), 400

        # Mappa campo UI -> colonna filone
        mappa_campi = {
            'Data intervento': 'DATA PIAN' if PROVINCIA_ATTIVA == 'TORINO' else 'Data_pianificata',
            'Telefono': 'telefono' if PROVINCIA_ATTIVA == 'TORINO' else 'Telefono',
            'Nominativo': 'NOMINATIVO' if PROVINCIA_ATTIVA == 'TORINO' else 'Nominativo',
            'Indirizzo': 'Indirizzo' if PROVINCIA_ATTIVA == 'TORINO' else 'Via',
            'Civico': 'Num' if PROVINCIA_ATTIVA == 'TORINO' else 'civico',
            'Comune': 'COMUNE' if PROVINCIA_ATTIVA == 'TORINO' else 'Comune',
        }

        col_name = mappa_campi.get(campo)
        if not col_name:
            return jsonify({'error': f'Campo "{campo}" non mappabile'}), 400

        # Trova riga per POD
        row = find_row_by_pod(pod)
        if row is None:
            return jsonify({'error': f'POD {pod} non trovato'}), 404

        # Aggiorna
        ws = get_worksheet()
        header_map = get_header_map()
        if col_name in header_map:
            col = header_map[col_name]
            ws.update_cell(row, col, valore)

            # Triggera sync
            _callbacks['trigger_sync_after_edit']()

            return jsonify({
                'success': True,
                'message': f'{campo} aggiornato per POD {pod}'
            })
        else:
            return jsonify({'error': f'Colonna {col_name} non trovata nel filone'}), 400

    except Exception as e:
        return _api_error(e, 'sovrascrittura valore filone')


@bp.route('/api/distinte/salva-nota', methods=['POST'])
@login_required
def salva_nota_distinte():
    """
    Salva una nota nel campo note del filone.
    Concatena con // se c'è già qualcosa.
    """
    try:
        data = request.json
        pod = data.get('pod', '').strip().upper()
        nota = data.get('nota', '').strip()
        sovrascrivere = data.get('sovrascrivere', True)  # Default: sovrascrive

        if not pod:
            return jsonify({'error': 'POD mancante'}), 400

        # Trova riga per POD
        row = find_row_by_pod(pod)
        if row is None:
            return jsonify({'error': f'POD {pod} non trovato'}), 404

        ws = get_worksheet()
        header_map = get_header_map()

        # Colonna note
        col_note = 'NOTE APP' if PROVINCIA_ATTIVA == 'TORINO' else 'NOTE'

        if col_note not in header_map:
            return jsonify({'error': f'Colonna {col_note} non trovata'}), 400

        col = header_map[col_note]

        # Leggi nota esistente
        nota_attuale = ws.cell(row, col).value or ''

        if sovrascrivere:
            # Sovrascrive completamente (il client gestisce già la concatenazione)
            nuova_nota = nota
        else:
            # Logica legacy: concatena
            if not nota:
                nuova_nota = nota_attuale
            elif not nota_attuale:
                nuova_nota = nota
            else:
                if nota not in nota_attuale:
                    nuova_nota = f"{nota_attuale} // {nota}"
                else:
                    nuova_nota = nota_attuale

        # Aggiorna
        ws.update_cell(row, col, nuova_nota)

        return jsonify({
            'success': True,
            'nota': nuova_nota
        })

    except Exception as e:
        return _api_error(e, 'salvataggio nota distinte')


@bp.route('/api/distinte/salva-ora-pian', methods=['POST'])
@login_required
def salva_ora_pian_distinte():
    """
    Salva il valore della colonna 'Ora pian' nel filone (solo Pavia).
    Body: {"pod": "IT...", "ora_pian": "10:00-12:00"}
    """
    try:
        data = request.json
        pod = data.get('pod', '').strip().upper()
        ora_pian = data.get('ora_pian', '').strip()

        if not pod:
            return jsonify({'error': 'POD mancante'}), 400

        if PROVINCIA_ATTIVA != 'PAVIA':
            return jsonify({'error': 'Ora pian è disponibile solo per Pavia'}), 400

        row = find_row_by_pod(pod)
        if row is None:
            return jsonify({'error': f'POD {pod} non trovato'}), 404

        ws = get_worksheet()
        header_map = get_header_map()

        col_ora_pian = None
        for nome in ['Ora pian', 'ORA PIAN', 'ORA_PIAN']:
            if nome in header_map:
                col_ora_pian = header_map[nome]
                break

        if col_ora_pian is None:
            return jsonify({'error': 'Colonna Ora pian non trovata'}), 400

        ws.update_cell(row, col_ora_pian, ora_pian)

        _callbacks['trigger_sync_after_edit']()

        return jsonify({
            'success': True,
            'ora_pian': ora_pian
        })

    except Exception as e:
        return _api_error(e, 'salvataggio ora pian distinte')


@bp.route('/api/distinte/strikethrough-telefono', methods=['POST'])
@login_required
def strikethrough_telefono():
    """
    Applica o rimuove il barrato (strikethrough) sulla cella telefono nel filone.
    Body: {"pod": "IT...", "strikethrough": true/false}
    """
    try:
        data = request.json
        pod = data.get('pod', '').strip().upper()
        barrato = data.get('strikethrough', True)

        if not pod:
            return jsonify({'error': 'POD mancante'}), 400

        row = find_row_by_pod(pod)
        if row is None:
            return jsonify({'error': f'POD {pod} non trovato'}), 404

        ws = get_worksheet()
        header_map = get_header_map()

        # Trova colonna TELEFONO
        col_tel = None
        for col_name in ['TELEFONO', 'Telefono', 'telefono', 'TEL', 'Tel', 'tel', 'PHONE', 'Phone']:
            if col_name in header_map:
                col_tel = header_map[col_name]
                break

        if col_tel is None:
            # Cerca in modo case-insensitive
            for h, idx in header_map.items():
                if h.strip().lower() in ['telefono', 'tel', 'phone']:
                    col_tel = idx
                    break

        if col_tel is None:
            logger.warning(f"[DISTINTE] Colonna TELEFONO non trovata. Headers disponibili: {list(header_map.keys())}")
            return jsonify({'error': 'Colonna TELEFONO non trovata'}), 400

        # Calcola riferimento cella A1
        col_letter = gspread.utils.rowcol_to_a1(1, col_tel)[:-1]
        cell_ref = f"{col_letter}{row}"

        # Applica formattazione strikethrough (con retry per evitare fallimenti da rate-limit)
        _sheets_retry(ws.format, cell_ref, {
            'textFormat': {
                'strikethrough': barrato
            }
        })

        logger.info(f"[DISTINTE] {'Barrato' if barrato else 'Rimosso barrato'} telefono per POD {pod} (cella {cell_ref})")

        return jsonify({
            'success': True,
            'message': f'Formattazione applicata per {pod}'
        })

    except Exception as e:
        return _api_error(e, 'formattazione telefono')


@bp.route('/api/distinte/segna-errato', methods=['POST'])
@login_required
def segna_numero_errato():
    """
    Segna un numero come errato nella colonna telefono del filone:
    - Aggiunge " ERRATO" al valore esistente
    - Applica formattazione barrata (strikethrough)
    Body: {"pod": "IT..."}
    """
    try:
        data = request.json
        pod = data.get('pod', '').strip().upper()

        if not pod:
            return jsonify({'error': 'POD mancante'}), 400

        row = find_row_by_pod(pod)
        if row is None:
            return jsonify({'error': f'POD {pod} non trovato'}), 404

        ws = get_worksheet()
        header_map = get_header_map()

        # Trova colonna TELEFONO
        col_tel = None
        for col_name in ['TELEFONO', 'Telefono', 'telefono', 'TEL', 'Tel', 'tel']:
            if col_name in header_map:
                col_tel = header_map[col_name]
                break
        if col_tel is None:
            for h, idx in header_map.items():
                if h.strip().lower() in ['telefono', 'tel']:
                    col_tel = idx
                    break

        if col_tel is None:
            logger.warning(f"[DISTINTE] Colonna TELEFONO non trovata. Headers: {list(header_map.keys())}")
            return jsonify({'error': 'Colonna TELEFONO non trovata'}), 400

        # Leggi valore attuale
        col_letter = gspread.utils.rowcol_to_a1(1, col_tel)[:-1]
        cell_ref = f"{col_letter}{row}"
        valore_attuale = str(ws.acell(cell_ref).value or '').strip()

        # Aggiungi " ERRATO" se non già presente
        if 'ERRATO' not in valore_attuale.upper():
            nuovo_valore = f"{valore_attuale} ERRATO" if valore_attuale else "ERRATO"
            _sheets_retry(ws.update_acell, cell_ref, nuovo_valore)

        # Applica formattazione strikethrough (con retry per evitare fallimenti da rate-limit)
        _sheets_retry(ws.format, cell_ref, {
            'textFormat': {
                'strikethrough': True
            }
        })

        logger.info(f"[DISTINTE] Numero segnato come ERRATO per POD {pod} (cella {cell_ref})")

        return jsonify({
            'success': True,
            'message': f'Numero segnato come errato per {pod}'
        })

    except Exception as e:
        return _api_error(e, 'segnalazione numero errato')


@bp.route('/api/distinte/appuntamento', methods=['POST'])
@login_required
def set_appuntamento_distinte():
    """
    Imposta un appuntamento per un contatore cercato per POD.
    Body: {"pod": "IT...", "orario": "10:00-12:00"}
    """
    try:
        data = request.json
        pod = data.get('pod', '').strip().upper()
        orario = data.get('orario', '').strip()
        data_app = data.get('data_appuntamento', '')
        note_extra = data.get('note', '')

        if not pod:
            return jsonify({'error': 'POD mancante'}), 400
        if not orario:
            return jsonify({'error': 'Orario richiesto'}), 400

        row = find_row_by_pod(pod)
        if row is None:
            return jsonify({'error': f'POD {pod} non trovato'}), 404

        result = _imposta_appuntamento_su_filone(
            row, orario, data_app=data_app, note_extra=note_extra,
            origine='distinte', pod_override=pod
        )
        return jsonify({
            'success': True,
            'message': f"Appuntamento impostato: {result['nota_app']}",
            'nota': result['nuova_nota'],
            'ora_pian': result.get('ora_pian', '')
        })

    except ValueError as ve:
        return jsonify({'error': str(ve)}), 400
    except Exception as e:
        return _api_error(e, 'appuntamento distinte')


@bp.route('/api/distinte/rimuovi-appuntamento', methods=['POST'])
@login_required
def remove_appuntamento_distinte():
    """Rimuove l'appuntamento da un contatore cercato per POD."""
    try:
        data = request.json
        pod = data.get('pod', '').strip().upper()

        if not pod:
            return jsonify({'error': 'POD mancante'}), 400

        row = find_row_by_pod(pod)
        if row is None:
            return jsonify({'error': f'POD {pod} non trovato'}), 404

        result = _rimuovi_appuntamento_da_filone(row)
        return jsonify({
            'success': True,
            'message': 'Appuntamento rimosso',
            'nota': result['nuova_nota'],
            'ora_pian': result.get('ora_pian', '')
        })

    except ValueError as ve:
        return jsonify({'error': str(ve)}), 400
    except Exception as e:
        return _api_error(e, 'rimozione appuntamento distinte')


@bp.route('/api/distinte/aggiorna-data', methods=['POST'])
@login_required
def aggiorna_data_distinte():
    """
    Aggiorna la data di pianificazione per un contatore cercato per POD.
    Body: {"pod": "IT...", "data": "DD/MM/YYYY"}
    """
    try:
        data = request.json
        pod = data.get('pod', '').strip().upper()
        nuova_data = data.get('data', '').strip()

        if not pod:
            return jsonify({'error': 'POD mancante'}), 400

        if not nuova_data:
            return jsonify({'error': 'Data richiesta'}), 400

        # Determina colonna data in base alla provincia
        col_data = 'DATA PIAN' if PROVINCIA_ATTIVA == 'TORINO' else 'Data_pianificata'

        row = find_row_by_pod(pod)
        if row is None:
            return jsonify({'error': f'POD {pod} non trovato'}), 404

        ws = get_worksheet()
        header_map = get_header_map()

        if col_data not in header_map:
            return jsonify({'error': f'Colonna {col_data} non trovata'}), 400

        col = header_map[col_data]
        ws.update_cell(row, col, nuova_data)

        # Triggera sync immediato per rigenerare i JSON
        _callbacks['trigger_sync_after_edit']()

        return jsonify({
            'success': True,
            'message': f'Data aggiornata: {nuova_data}'
        })

    except Exception as e:
        return _api_error(e, 'aggiornamento data distinte')


@bp.route('/api/distinte/salva-telefono', methods=['POST'])
@login_required
def salva_telefono_filone():
    """
    Salva un numero di telefono nel filone per un POD.
    Body: {"pod": "IT...", "telefono": "...", "fonte": "PagineBianche"}
    Se telefono è vuoto o "non trovato", scrive "non trovato" nel campo.
    """
    try:
        data = request.json
        pod = data.get('pod', '').strip().upper()
        telefono = data.get('telefono', '').strip()
        fonte = data.get('fonte', '').strip()

        if not pod:
            return jsonify({'error': 'POD mancante'}), 400

        row = find_row_by_pod(pod)
        if row is None:
            return jsonify({'error': f'POD {pod} non trovato'}), 404

        ws = get_worksheet()
        header_map = get_header_map()

        # Trova colonna TELEFONO
        col_tel = None
        for col_name in ['TELEFONO', 'Telefono', 'telefono', 'TEL', 'Tel']:
            if col_name in header_map:
                col_tel = header_map[col_name]
                break

        if col_tel is None:
            return jsonify({'error': 'Colonna TELEFONO non trovata nel filone'}), 400

        # Prepara valore da salvare
        if not telefono or telefono.lower() == 'non trovato':
            valore = 'non trovato'
        else:
            valore = telefono

        # Salva nel filone
        ws.update_cell(row, col_tel, valore)

        # Triggera sync per aggiornare i JSON
        _callbacks['trigger_sync_after_edit']()

        return jsonify({
            'success': True,
            'telefono': valore,
            'message': f'Telefono salvato: {valore}'
        })

    except Exception as e:
        return _api_error(e, 'salvataggio telefono')


# ============================================================================
# API PEC - Info tipo contatto e link di ricerca
# ============================================================================

@bp.route('/api/pec/info', methods=['GET'])
@login_required
def api_info_pec():
    """
    Restituisce il tipo di contatto e i link di ricerca PEC.
    Query params: nome, comune (opzionale)
    """
    try:
        nome = request.args.get('nome', '').strip()
        comune = request.args.get('comune', '').strip()

        if not nome:
            return jsonify({'error': 'Specificare nome'}), 400

        tipo = classifica_tipo_contatto(nome)
        links = genera_tutti_link_ricerca(nome, comune)

        return jsonify({
            'success': True,
            'tipo_contatto': tipo,
            'is_azienda': tipo != 'privato',
            'link_ricerca': links,
        })

    except Exception as e:
        return _api_error(e, 'info PEC')
