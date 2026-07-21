# -*- coding: utf-8 -*-
"""
Patch in-place di mappa_data.json per soft edit utente.

In primary mode (Fase 2), dopo un POST utente che modifica solo campi
COSMETICI (es. email, telefono, nominativo, indirizzo, posizione_misuratore),
non c'è bisogno di girare l'intero sync_loop (~7s) per rigenerare
mappa_data.json: le pending in volo non cambiano percorsi/zone/access points,
quindi possiamo applicare le modifiche direttamente al JSON sul disco e
inviare subito SSE 'sync_complete' al frontend (~50ms invece di 7s).

Edit STRUTTURALI (data, operatore, lat/lon, comune, note appuntamento,
esito) continuano invece a passare dal sync_loop classico.

Uso tipico (chiamato da trigger_sync_after_edit):
    from moduli.mappa_data_patcher import try_patch_inplace
    if try_patch_inplace():
        sse_notify_all('sync_complete', {'source': 'inplace_patch'})
        return
    # else: fallback al sync_loop classico
"""
import os
import logging

logger = logging.getLogger('mappa_algeco')


# Chiavi interne (DB) considerate "soft": modificarle NON cambia percorsi,
# zone, access points o filtri (esclusi/annullati/appuntamenti).
SOFT_FIELDS_DB = {
    'nominativo',
    'pod',
    'indirizzo',
    'civico',
    'telefono',
    'email',
    'posizione_misuratore',
    'tipo_misuratore',
    'orari_lettere',
    'lcl',
    'smart_test',
    'sigillo',
    'codice_ante',
    'matricola_ante',
    'matricola_installata',
    'codice_installato',
    'data_installazione',
    # 'note_appuntamento' è gestito separatamente (vedi `_is_app`):
    # è SOFT solo se né il vecchio né il nuovo testo contengono "APP"
    # (pattern \bapp\b case-insensitive). Se uno dei due lo contiene,
    # cambia IS_APPUNTAMENTO/ORA_APPUNTAMENTO → STRUCTURAL.
}

# Chiavi interne (DB) STRUTTURALI: cambiano qualcosa che il processing
# DataFrame deve ricalcolare (filtri, percorsi, zone, access points).
STRUCTURAL_FIELDS_DB = {
    'data_pianificata',     # DATA_K → percorsi
    'operatore',            # TEC_K → filtri tecnici
    'latitudine',           # Lat → distanze, access points
    'longitudine',          # Lon → distanze, access points
    'comune',               # COMUNE → zone
    'localita',             # LOCALITA → zone (per Pavia)
    'esito',                # filtra "esclusi 7535 contatori già installati"
}


from moduli.appuntamenti_core import rileva_appuntamento

def _is_app(text):
    """True se la nota indica un appuntamento (logica unificata)."""
    return rileva_appuntamento(text)


def _note_change_is_soft(old_value, new_value):
    """Una modifica della nota è SOFT se né il vecchio né il nuovo contengono
    'APP'. In tal caso `IS_APPUNTAMENTO` resta False in entrambi i casi e
    `ORA_APPUNTAMENTO` resta None: il DataFrame non cambia su colonne
    strutturali. Altrimenti (uno dei due ha APP) è STRUCTURAL.
    """
    return not _is_app(old_value) and not _is_app(new_value)

# Mapping: chiave_interna DB → chiave nel JSON di mappa_data.json/contatori.
# Solo i campi soft ci interessano (non tocchiamo i strutturali in patch).
DB_TO_JSON_FIELD = {
    'nominativo':            'cliente',
    'pod':                   'pod',
    'indirizzo':             'via',
    'civico':                'civico',
    'telefono':              'telefono',
    'email':                 'email',
    'posizione_misuratore':  'posizione_mis',  # esiste anche 'note', vedi sotto
    'tipo_misuratore':       'tipo_ce',
    'orari_lettere':         'orari_lettere',
    'lcl':                   'lcl',
    'smart_test':            'smart_test',
    'note_appuntamento':     'note_app',  # SOLO se _note_change_is_soft (vedi sotto)
    # Campi non presenti come tali in mappa_data.json (sigillo, codice_*, ...)
    # vengono semplicemente skippati lato JSON ma il valore va comunque su
    # Sheets via drainer. La UI non li mostra direttamente da mappa_data.json.
}

# Eccezione: 'posizione_misuratore' nel JSON è duplicato sia in 'note' sia
# in 'posizione_mis'. Patcho entrambi.
DB_FIELD_DUPLICATES = {
    'posizione_misuratore': ('note', 'posizione_mis'),
}


def _classify_pending(conn):
    """Classifica le pending in volo per (eneltel, lcl):
      - (eneltel_key, lcl) → {col_name: new_value} per campi SOFT
      - has_structural se almeno una pending è strutturale

    Solo le pending in stato 'pending'/'in_flight' contano.

    Returns: (soft_pending_by_row_key, has_structural)
    """
    from . import db as _db

    soft_by_key = {}
    has_structural = False

    rows = conn.execute(
        "SELECT eneltel_key, COALESCE(lcl,'') AS lcl, column_name, new_value, base_value "
        "FROM pending_writes WHERE state IN ('pending','in_flight')"
    ).fetchall()

    for r in rows:
        key = (r['eneltel_key'], r['lcl'])
        col = r['column_name']
        chiave = _db.column_to_internal_key(conn, col)

        if chiave in STRUCTURAL_FIELDS_DB:
            has_structural = True
            continue

        if chiave == 'note_appuntamento':
            if _note_change_is_soft(r['base_value'], r['new_value']):
                soft_by_key.setdefault(key, {})[chiave] = r['new_value']
            else:
                has_structural = True
            continue

        if chiave in SOFT_FIELDS_DB:
            soft_by_key.setdefault(key, {})[chiave] = r['new_value']
        elif chiave is None:
            logger.debug(f"[patcher] pending su colonna senza mapping interno: {col}")
            has_structural = True

    return soft_by_key, has_structural


def _mappa_data_json_path():
    from config import PROVINCIA_ATTIVA
    return os.path.join('data', PROVINCIA_ATTIVA.lower(), 'mappa_data.json')


def try_patch_inplace():
    """Prova ad applicare in-place mappa_data.json se ci sono pending in
    volo SOLO su campi soft.

    Returns:
        True  → patch applicata, chi chiama può saltare il sync_loop e
                inviare direttamente SSE 'sync_complete'.
        False → c'è almeno una pending strutturale (o errore): fallback al
                sync_loop classico.
    """
    from . import db as _db
    from . import safe_json as _sj

    conn = _db.get_conn()
    soft_by_key, has_structural = _classify_pending(conn)

    if has_structural:
        return False

    if not soft_by_key:
        return False

    json_path = _mappa_data_json_path()
    if not os.path.exists(json_path):
        logger.warning(f"[patcher] {json_path} non esiste, fallback al sync_loop")
        return False

    # `soft_by_key` ha chiavi (eneltel, lcl). Per matching nel JSON dei
    # contatori (che ha campi `eneltel` e `lcl`), iteriamo su tutti i
    # contatori e troviamo i match esatti.
    def _modifier(data):
        if not isinstance(data, dict) or 'contatori' not in data:
            return None
        contatori = data['contatori']
        if not isinstance(contatori, list):
            return None
        n_patched = 0
        for c in contatori:
            ek = _db.normalize_eneltel(c.get('eneltel') or c.get('id') or '')
            if not ek:
                continue
            lcl = str(c.get('lcl') or '')
            key = (ek, lcl)
            # Match esatto (eneltel, lcl). Fallback: solo eneltel se la
            # pending è per quell'eneltel ma LCL non specificato (raro).
            if key in soft_by_key:
                overrides = soft_by_key[key]
            elif (ek, '') in soft_by_key:
                overrides = soft_by_key[(ek, '')]
            else:
                continue
            for chiave_interna, new_val in overrides.items():
                if chiave_interna in DB_FIELD_DUPLICATES:
                    for jf in DB_FIELD_DUPLICATES[chiave_interna]:
                        c[jf] = '' if new_val is None else str(new_val)
                json_field = DB_TO_JSON_FIELD.get(chiave_interna)
                if json_field:
                    c[json_field] = '' if new_val is None else str(new_val)
            n_patched += 1
        if n_patched == 0:
            return None
        from datetime import datetime as _dt
        # Stesso formato di salva_dati_json() in mappa_html.py: stringa
        # YYYYMMDDHHMMSS attesa dal frontend (formatTimestamp).
        data['timestamp'] = _dt.now().strftime('%Y%m%d%H%M%S')
        return data

    ok, result = _sj.json_modify(json_path, _modifier, separators=(',', ':'))
    if not ok:
        logger.warning("[patcher] json_modify ha fallito, fallback al sync_loop")
        return False

    n_keys = len(soft_by_key)
    logger.info(f"[patcher] inplace patch OK su {n_keys} riga(he): {list(soft_by_key.keys())[:5]}")
    return True
