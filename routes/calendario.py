"""
Blueprint per le route del Calendario e dei Promemoria Appuntamento.
"""

import os
import logging
from datetime import datetime
from flask import Blueprint, request, jsonify, session, send_file

from moduli.auth import login_required
from moduli.shared import (
    get_worksheet, get_headers, get_header_map,
    _get_username, _api_error, _callbacks,
    _trova_indice_colonna,
)
from moduli.calendario import (
    get_attivita, crea_attivita, modifica_attivita,
    completa_attivita, completa_multipli, elimina_attivita, elimina_multipli, get_riepilogo,
    CATEGORIE_VALIDE, COLORI_CATEGORIE,
    crea_promemoria_boxbeat, get_promemoria_boxbeat_oggi,
    get_riepilogo_boxbeat, get_user_promemoria_settings,
    salva_user_promemoria_settings, registra_appuntamento_excel,
    genera_excel_boxbeat,
)
from config import PROVINCIA_ATTIVA

logger = logging.getLogger('mappa_algeco')

bp = Blueprint('calendario', __name__)


# ============================================================================
# CALENDARIO - API
# ============================================================================

@bp.route('/calendario')
@login_required
def calendario_page():
    """Pagina calendario/attività condivise."""
    return send_file(os.path.join(os.path.dirname(__file__), '..', 'templates', 'calendario.html'))


@bp.route('/api/calendario', methods=['GET'])
@login_required
def api_get_attivita():
    """Restituisce la lista delle attività."""
    mese = request.args.get('mese')  # formato YYYY-MM
    utente = request.args.get('utente')
    mostra_completate = request.args.get('completate', 'true').lower() == 'true'
    
    attivita = get_attivita(
        filtro_mese=mese,
        filtro_utente=utente,
        mostra_completate=mostra_completate,
        provincia=PROVINCIA_ATTIVA
    )
    # Arricchisci le attività boxbeat con dati aggiornati dal filone
    attivita = _arricchisci_boxbeat_con_filone(attivita)
    return jsonify({'attivita': attivita})


@bp.route('/api/calendario/riepilogo', methods=['GET'])
@login_required
def api_riepilogo_calendario():
    """Restituisce il riepilogo delle attività."""
    return jsonify(get_riepilogo(provincia=PROVINCIA_ATTIVA))


@bp.route('/api/calendario', methods=['POST'])
@login_required
def api_crea_attivita():
    """Crea una nuova attività."""
    dati = request.get_json()
    if not dati:
        return jsonify({'error': 'Dati mancanti'}), 400
    
    try:
        attivita = crea_attivita(
            titolo=dati.get('titolo', ''),
            data=dati.get('data', ''),
            ora=dati.get('ora', ''),
            ora_fine=dati.get('ora_fine', ''),
            descrizione=dati.get('descrizione', ''),
            categoria=dati.get('categoria', 'promemoria'),
            creato_da=session.get('user', {}).get('username', 'sconosciuto') if isinstance(session.get('user'), dict) else session.get('user', 'sconosciuto'),
            provincia=dati.get('provincia', '')
        )
        # Notifica tutti i client SSE
        _callbacks['sse_notify_all']('calendario_update', {'azione': 'creata', 'id': attivita['id']})
        return jsonify({'success': True, 'attivita': attivita})
    except ValueError as e:
        return _api_error(e, 'creazione attività', 400)


@bp.route('/api/calendario/<attivita_id>', methods=['PUT'])
@login_required
def api_modifica_attivita(attivita_id):
    """Modifica un'attività esistente."""
    dati = request.get_json()
    if not dati:
        return jsonify({'error': 'Dati mancanti'}), 400
    
    try:
        attivita = modifica_attivita(
            attivita_id=attivita_id,
            aggiornamenti=dati,
            modificato_da=session.get('user', {}).get('username', 'sconosciuto') if isinstance(session.get('user'), dict) else session.get('user', 'sconosciuto')
        )
        _callbacks['sse_notify_all']('calendario_update', {'azione': 'modificata', 'id': attivita_id})
        return jsonify({'success': True, 'attivita': attivita})
    except ValueError as e:
        return _api_error(e, 'modifica attività', 404)


@bp.route('/api/calendario/<attivita_id>/completa', methods=['POST'])
@login_required
def api_completa_attivita(attivita_id):
    """Segna un'attività come completata o la riapre."""
    try:
        attivita = completa_attivita(
            attivita_id=attivita_id,
            completata_da=session.get('user', {}).get('username', 'sconosciuto') if isinstance(session.get('user'), dict) else session.get('user', 'sconosciuto')
        )
        _callbacks['sse_notify_all']('calendario_update', {'azione': 'completata', 'id': attivita_id})
        return jsonify({'success': True, 'attivita': attivita})
    except ValueError as e:
        return _api_error(e, 'completamento attività', 404)


@bp.route('/api/calendario/bulk-complete', methods=['POST'])
@login_required
def api_completa_multipli():
    """Segna più attività come completate in una sola operazione."""
    dati = request.get_json()
    ids = dati.get('ids', []) if dati else []
    if not ids or not isinstance(ids, list):
        return jsonify({'error': 'Nessun ID fornito'}), 400
    username = session.get('user', {}).get('username', 'sconosciuto') if isinstance(session.get('user'), dict) else session.get('user', 'sconosciuto')
    completati = completa_multipli(ids, completata_da=username)
    if completati > 0:
        _callbacks['sse_notify_all']('calendario_update', {'azione': 'bulk_complete', 'count': completati})
    return jsonify({'success': True, 'completati': completati})


@bp.route('/api/calendario/<attivita_id>', methods=['DELETE'])
@login_required
def api_elimina_attivita(attivita_id):
    """Elimina un'attività."""
    if elimina_attivita(attivita_id):
        _callbacks['sse_notify_all']('calendario_update', {'azione': 'eliminata', 'id': attivita_id})
        return jsonify({'success': True})
    return jsonify({'error': 'Attività non trovata'}), 404


@bp.route('/api/calendario/bulk-delete', methods=['POST'])
@login_required
def api_elimina_multipli():
    """Elimina più attività in una sola operazione."""
    dati = request.get_json()
    ids = dati.get('ids', []) if dati else []
    if not ids or not isinstance(ids, list):
        return jsonify({'error': 'Nessun ID fornito'}), 400
    eliminati = elimina_multipli(ids)
    if eliminati > 0:
        _callbacks['sse_notify_all']('calendario_update', {'azione': 'bulk_delete', 'count': eliminati})
    return jsonify({'success': True, 'eliminati': eliminati})


@bp.route('/api/calendario/categorie', methods=['GET'])
@login_required
def api_categorie_calendario():
    """Restituisce le categorie disponibili con i colori."""
    return jsonify({
        'categorie': list(CATEGORIE_VALIDE),
        'colori': COLORI_CATEGORIE
    })


# ============================================================================
# PROMEMORIA APPUNTAMENTO - API
# ============================================================================

@bp.route('/api/boxbeat/promemoria', methods=['GET'])
@login_required
def api_get_boxbeat_oggi():
    """Restituisce i promemoria Appuntamento creati oggi per l'utente corrente."""
    completati = request.args.get('completati', 'false').lower() == 'true'
    username = session.get('user', {}).get('username', 'sconosciuto') if isinstance(session.get('user'), dict) else session.get('user', 'sconosciuto')
    promemoria = get_promemoria_boxbeat_oggi(completati=completati, username=username, provincia=PROVINCIA_ATTIVA)
    # Arricchisci con dati aggiornati dal filone (nominativo, indirizzo, note, ecc.)
    promemoria = _arricchisci_boxbeat_con_filone(promemoria)
    return jsonify({'promemoria': promemoria})


@bp.route('/api/boxbeat/riepilogo', methods=['GET'])
@login_required
def api_riepilogo_boxbeat():
    """Restituisce il riepilogo promemoria Appuntamento per l'utente corrente."""
    username = session.get('user', {}).get('username', 'sconosciuto') if isinstance(session.get('user'), dict) else session.get('user', 'sconosciuto')
    return jsonify(get_riepilogo_boxbeat(username=username, provincia=PROVINCIA_ATTIVA))


@bp.route('/api/boxbeat/promemoria', methods=['POST'])
@login_required
def api_crea_boxbeat():
    """Crea un promemoria Appuntamento manualmente."""
    dati = request.get_json()
    if not dati:
        return jsonify({'error': 'Dati mancanti'}), 400
    
    data_app = dati.get('data_appuntamento', '')
    ora_app = dati.get('ora_appuntamento', '')
    
    if not data_app:
        return jsonify({'error': 'Data appuntamento obbligatoria'}), 400
    
    username = session.get('user', {}).get('username', 'sconosciuto') if isinstance(session.get('user'), dict) else session.get('user', 'sconosciuto')
    
    try:
        attivita = crea_promemoria_boxbeat(
            data_appuntamento=data_app,
            ora_appuntamento=ora_app,
            pod=dati.get('pod', ''),
            eneltel=dati.get('eneltel', ''),
            nominativo=dati.get('nominativo', ''),
            via=dati.get('via', ''),
            civico=dati.get('civico', ''),
            comune=dati.get('comune', ''),
            note=dati.get('note', ''),
            provincia=dati.get('provincia', PROVINCIA_ATTIVA),
            creato_da=username,
            origine=dati.get('origine', 'manuale'),
            data_pianificata=dati.get('data_pianificata', '')
        )
        registra_appuntamento_excel(
            data_appuntamento=data_app, orario=ora_app,
            pod=dati.get('pod', ''), eneltel=dati.get('eneltel', ''),
            nominativo=dati.get('nominativo', ''),
            via=dati.get('via', ''), civico=dati.get('civico', ''),
            comune=dati.get('comune', ''), note=dati.get('note', ''),
            origine=dati.get('origine', 'manuale'),
            data_pianificata=dati.get('data_pianificata', ''),
            creato_da=username,
            provincia=PROVINCIA_ATTIVA
        )
        _callbacks['sse_notify_all']('calendario_update', {'azione': 'boxbeat_creato', 'id': attivita['id']})
        return jsonify({'success': True, 'attivita': attivita})
    except Exception as e:
        return _api_error(e, 'creazione boxbeat', 400)


@bp.route('/api/boxbeat/promemoria/<attivita_id>', methods=['PUT'])
@login_required
def api_modifica_boxbeat(attivita_id):
    """Aggiorna i campi boxbeat_data di un promemoria Appuntamento esistente."""
    dati = request.get_json()
    if not dati:
        return jsonify({'error': 'Dati mancanti'}), 400
    
    try:
        from moduli.calendario import _load_data, _save_data, _lock
        with _lock:
            data = _load_data()
            for att in data["attivita"]:
                if att["id"] == attivita_id:
                    if att.get("categoria") != "boxbeat":
                        return jsonify({'error': 'Attività non è di tipo BoxBeat'}), 400
                    bb = att.get("boxbeat_data", {})
                    for campo in ["pod", "eneltel", "nominativo", "via", "civico",
                                  "comune", "note", "ora_appuntamento"]:
                        if campo in dati:
                            bb[campo] = dati[campo]
                    att["boxbeat_data"] = bb
                    username = session.get('user', {}).get('username', 'sconosciuto') if isinstance(session.get('user'), dict) else session.get('user', 'sconosciuto')
                    att["modificato_da"] = username
                    att["modificato_il"] = datetime.now().isoformat()
                    _save_data(data)
                    _callbacks['sse_notify_all']('calendario_update', {'azione': 'boxbeat_modificato', 'id': attivita_id})
                    return jsonify({'success': True, 'attivita': att})
            return jsonify({'error': 'Attività non trovata'}), 404
    except Exception as e:
        return _api_error(e, 'modifica boxbeat')


@bp.route('/api/boxbeat/excel/<data_str>', methods=['GET'])
@login_required
def api_download_boxbeat_excel(data_str):
    """Scarica il file Excel degli appuntamenti Promemoria per la data specificata."""
    try:
        # Recupera TUTTI i dati aggiornati dal FILONE in un'unica lettura
        dati_filone = _recupera_dati_aggiornati_dal_filone()
        
        # Filtra per utente corrente
        username = session.get('user', {}).get('username', 'sconosciuto') if isinstance(session.get('user'), dict) else session.get('user', 'sconosciuto')
        
        filepath = genera_excel_boxbeat(data_str, dati_filone=dati_filone,
                                         username=username,
                                         provincia=PROVINCIA_ATTIVA)
        if not os.path.exists(filepath):
            return jsonify({'error': 'File non trovato'}), 404
        response = send_file(
            filepath,
            as_attachment=True,
            download_name=f'boxbeat_appuntamenti_{data_str}.xlsx',
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return response
    except Exception as e:
        return _api_error(e, 'download excel boxbeat')


@bp.route('/api/boxbeat/settings', methods=['GET'])
@login_required
def api_get_boxbeat_settings():
    """Restituisce le impostazioni promemoria BoxBeat per l'utente corrente."""
    username = session.get('user', {}).get('username', 'sconosciuto') if isinstance(session.get('user'), dict) else session.get('user', 'sconosciuto')
    settings = get_user_promemoria_settings(username)
    return jsonify(settings)


@bp.route('/api/boxbeat/settings', methods=['POST'])
@login_required
def api_salva_boxbeat_settings():
    """Salva le impostazioni promemoria BoxBeat per l'utente corrente."""
    dati = request.get_json()
    if not dati:
        return jsonify({'error': 'Dati mancanti'}), 400
    
    username = session.get('user', {}).get('username', 'sconosciuto') if isinstance(session.get('user'), dict) else session.get('user', 'sconosciuto')
    
    if salva_user_promemoria_settings(username, dati):
        return jsonify({'success': True})
    return jsonify({'error': 'Errore salvataggio'}), 500


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def _arricchisci_boxbeat_con_filone(attivita_list: list) -> list:
    """
    Arricchisce le attività di tipo boxbeat con i dati aggiornati dal filone.
    Aggiorna boxbeat_data (nominativo, via, civico, comune, note) e il titolo
    dell'attività se i dati nel filone sono più recenti.
    Modifica le attività in-place e restituisce la stessa lista.
    """
    # Controlla se ci sono boxbeat nella lista
    boxbeat_presenti = any(a.get('categoria') == 'boxbeat' for a in attivita_list)
    if not boxbeat_presenti:
        return attivita_list
    
    try:
        dati_filone = _recupera_dati_aggiornati_dal_filone()
    except Exception as e:
        logger.warning(f"[Calendario] ⚠️ Impossibile arricchire boxbeat dal filone: {e}")
        return attivita_list
    
    _note = dati_filone.get('note', {})
    _nominativi = dati_filone.get('nominativi', {})
    _vie = dati_filone.get('vie', {})
    _civici = dati_filone.get('civici', {})
    _comuni = dati_filone.get('comuni', {})
    _date_pian = dati_filone.get('date_pian', {})
    _operatori = dati_filone.get('operatori', {})
    
    # Mappa inversa matricola → nome visualizzato
    from config import OPERATORI_DICT
    _reverse_operatori = {v: k for k, v in OPERATORI_DICT.items()}
    
    for att in attivita_list:
        if att.get('categoria') != 'boxbeat':
            continue
        bb = att.get('boxbeat_data', {})
        if not bb:
            continue
        
        enel_key = bb.get('eneltel', '').strip()
        pod_key = bb.get('pod', '').strip().upper()
        
        if not enel_key and not pod_key:
            continue
        
        def _da_filone(dizionario):
            if dizionario:
                if enel_key and enel_key in dizionario:
                    return dizionario[enel_key]
                if pod_key and pod_key in dizionario:
                    return dizionario[pod_key]
            return None
        
        # Operatore: cerca matricola nel filone e mappa a nome visualizzato
        matricola_filone = _da_filone(_operatori)
        if matricola_filone:
            bb['operatore'] = _reverse_operatori.get(matricola_filone, matricola_filone)
        
        aggiornato = False
        for campo, dizionario in [('nominativo', _nominativi), ('via', _vie),
                                   ('civico', _civici), ('comune', _comuni),
                                   ('note', _note), ('data_pianificata', _date_pian)]:
            val_filone = _da_filone(dizionario)
            if val_filone and val_filone != bb.get(campo, ''):
                bb[campo] = val_filone
                aggiornato = True
        
        # Aggiorna anche il titolo se il nominativo è cambiato
        if aggiornato:
            att['boxbeat_data'] = bb
            parti_titolo = ['Appuntamento']
            if bb.get('nominativo'):
                parti_titolo.append(bb['nominativo'].strip())
            if bb.get('comune'):
                parti_titolo.append(f"- {bb['comune'].strip()}")
            if bb.get('ora_appuntamento'):
                parti_titolo.append(f"ore {bb['ora_appuntamento']}")
            att['titolo'] = ' '.join(parti_titolo)
    
    return attivita_list


def _recupera_dati_aggiornati_dal_filone() -> dict:
    """
    Recupera TUTTI i dati aggiornati dal FILONE (Google Sheets) in un'unica lettura.
    Restituisce un dict con sotto-dizionari per ogni campo, indicizzati per ENELTEL o POD.
    
    Campi restituiti:
        - note:         {eneltel_or_pod: nota_corrente}
        - nominativi:   {eneltel_or_pod: nominativo_corrente}
        - date_pian:    {eneltel_or_pod: data_pianificata_YYYY-MM-DD}
        - vie:          {eneltel_or_pod: indirizzo_corrente}
        - civici:       {eneltel_or_pod: civico_corrente}
        - comuni:       {eneltel_or_pod: comune_corrente}
        - operatori:    {eneltel_or_pod: matricola_operatore}
    
    Usato per arricchire l'Excel BoxBeat con dati scritti/modificati dopo la registrazione.
    """
    risultato = {
        'note': {},
        'nominativi': {},
        'date_pian': {},
        'vie': {},
        'civici': {},
        'comuni': {},
        'operatori': {},
    }
    try:
        ws = get_worksheet()
        all_data = ws.get_all_values()
        if not all_data:
            return risultato
        
        headers_gs = all_data[0]
        
        # Indici colonne
        col_eneltel_idx = None
        col_pod_idx = None
        col_note_idx = None
        col_nom_idx = None
        col_data_idx = None
        col_via_idx = None
        col_civico_idx = None
        col_comune_idx = None
        col_operatore_idx = None
        
        for i, h in enumerate(headers_gs):
            h_upper = h.strip().upper()
            # ENELTEL / POD (chiavi)
            if h_upper == 'ENELTEL':
                col_eneltel_idx = i
            if h_upper == 'POD':
                col_pod_idx = i
            # NOTE (cerca tra i nomi possibili)
            if h_upper in ('NOTE APP', 'NOTE_APP', 'NOTE APPUNTAMENTO', 'NOTE'):
                if col_note_idx is None:
                    col_note_idx = i
            # NOMINATIVO
            if h_upper in ('NOMINATIVO', 'CLIENTE', 'NOME'):
                col_nom_idx = i
            # DATA PIANIFICATA
            if h_upper in ('DATA PIAN', 'DATA_PIAN', 'DATA PIANIFICATA', 'DATA_PIANIFICATA', 'PROG'):
                col_data_idx = i
            # VIA / INDIRIZZO
            if h_upper in ('INDIRIZZO', 'VIA', 'VIA NUM'):
                col_via_idx = i
            # CIVICO
            if h_upper in ('NUM', 'CIVICO', 'NUMERO CIVICO', 'N. CIVICO'):
                col_civico_idx = i
            # COMUNE
            if h_upper in ('COMUNE', 'LOCALITA', 'LOCALITÀ'):
                col_comune_idx = i
            # OPERATORE
            if h_upper in ('OPERATORE', 'TECNICO', 'MATRICOLA'):
                col_operatore_idx = i
        
        parse_data = None
        if col_data_idx is not None:
            from moduli.data_loader import parse_data_flessibile
            parse_data = parse_data_flessibile
        
        for row in all_data[1:]:
            # Estrai chiavi identificative
            enel = row[col_eneltel_idx].strip() if col_eneltel_idx is not None and col_eneltel_idx < len(row) else ''
            pod = row[col_pod_idx].strip().upper() if col_pod_idx is not None and col_pod_idx < len(row) else ''
            
            if not enel and not pod:
                continue
            
            # Helper per assegnare un valore a entrambe le chiavi
            def _assegna(dizionario, valore):
                if enel:
                    dizionario[enel] = valore
                if pod:
                    dizionario[pod] = valore
            
            # Note
            if col_note_idx is not None:
                nota_val = row[col_note_idx].strip() if col_note_idx < len(row) else ''
                if nota_val:
                    _assegna(risultato['note'], nota_val)
            
            # Nominativo
            if col_nom_idx is not None:
                nom_val = row[col_nom_idx].strip() if col_nom_idx < len(row) else ''
                if nom_val:
                    _assegna(risultato['nominativi'], nom_val)
            
            # Data pianificata
            if col_data_idx is not None and parse_data:
                data_val = row[col_data_idx].strip() if col_data_idx < len(row) else ''
                if data_val:
                    parsed = parse_data(data_val)
                    if parsed:
                        _assegna(risultato['date_pian'], parsed)
            
            # Via
            if col_via_idx is not None:
                via_val = row[col_via_idx].strip() if col_via_idx < len(row) else ''
                if via_val:
                    _assegna(risultato['vie'], via_val)
            
            # Civico
            if col_civico_idx is not None:
                civ_val = row[col_civico_idx].strip() if col_civico_idx < len(row) else ''
                if civ_val:
                    _assegna(risultato['civici'], civ_val)
            
            # Comune
            if col_comune_idx is not None:
                com_val = row[col_comune_idx].strip() if col_comune_idx < len(row) else ''
                if com_val:
                    _assegna(risultato['comuni'], com_val)
            
            # Operatore
            if col_operatore_idx is not None:
                op_val = row[col_operatore_idx].strip() if col_operatore_idx < len(row) else ''
                if op_val:
                    _assegna(risultato['operatori'], op_val)
    
    except Exception as e:
        logger.warning(f"[Appuntamenti Excel] ⚠️ Errore recupero dati aggiornati dal FILONE: {e}")
    
    return risultato
