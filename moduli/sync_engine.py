# -*- coding: utf-8 -*-
"""
Sync engine — sincronizza il DB locale con Google Sheets.

Due flussi indipendenti:

1. PULL (Sheets → DB)
   `pull_once()` legge l'intero foglio, calcola hash riga per riga, applica
   i cambi remoti al DB con merge campo-per-campo. Conflitti (cella con
   pending in volo + valore remoto cambiato dalla base) vengono loggati
   in audit_log con source='conflict_local_wins' SENZA toccare il DB:
   il valore locale (pending) prevarrà al prossimo drain.

2. PUSH (DB → Sheets)
   `drain_once()` prende un batch di pending_writes in stato 'pending',
   le marca 'in_flight', e chiama il già esistente
   `safe_batch_update_by_name()` con anchor=Eneltel. Su successo le marca
   'done' e ricalcola row_hash. Su anchor lost / colonna mancante, le
   marca 'quarantined'. Su errore transitorio Sheets, retry con backoff.

Entrambi i flussi sono callabili sia in modalità "one-shot" (per test e
per integrazione col loop di sync esistente in server.py) sia in modalità
"loop daemon" via `start_workers()`.
"""
import os
import json
import time
import logging
import threading

from . import db
from . import shared

logger = logging.getLogger('mappa_algeco')


# ============================================================================
# PATTERN VALIDATION (colonne critiche storicamente soggette a span)
# ============================================================================
# Per QUESTE colonne (e solo queste), durante il pull, ogni cambio remoto
# viene validato contro un pattern atteso. Se il nuovo valore non matcha,
# il cambio viene SALTATO (DB tiene il vecchio valore) e registrato in
# filone_anomalies come 'invalid_pattern_skipped'. Skip per cella, non
# per pull: le altre celle/colonne procedono normalmente.
#
# I valori vuoti sono SEMPRE permessi (un campo può legittimamente essere
# svuotato; non blocchiamo cancellazioni).
import re as _re_validate
# Pattern calibrati sui dati reali del DB Torino (5/2026).
# Sono mutuamente esclusivi: ogni valore matcha al massimo UNA colonna.
# Usati per DUE scopi diversi:
#
# A) _COLUMN_PATTERN_VALIDATORS: valida cambi IN ARRIVO su queste colonne.
#    Se il valore non matcha → rifiutato + dispatch. Esito NON è qui:
#    accetta sempre nuovi valori (gli esiti hanno tante varianti future
#    impossibili da prevedere; meglio non rifiutare false positive).
#
# B) _PATTERN_FOR_CLASSIFY: classifica un valore per capire a quale colonna
#    appartiene (per fare il dispatch verso la colonna corretta). Esito SI
#    è qui: se vedo 'ESITO OK' in altra colonna, voglio riconoscerlo.
_OPERATORE_RE = _re_validate.compile(r'^A[A-Z]\d{5,8}$')
_SIGILLO_RE = _re_validate.compile(r'^\d{8,14}[A-Z]?$')
_MATRICOLA_CE_RE = _re_validate.compile(r'^\d{2}E[0-9A-Z]{6,30}$')
# cod. (codice installato): solo numerico, lunghezza variabile (5-10 cifre)
# perché può essere troncato o avere zeri iniziali a seconda della provincia
# (Torino tipicamente 6-7, Pavia 8 con zeri iniziali tipo '03345799').
_COD_RE = _re_validate.compile(r'^\d{5,10}$')


# ---- OPERATORE: pattern PER-PROVINCIA (lista nomi/matricole + generico) ----
_op_cache = {'ts': 0, 'provincia': None, 'valid_values': set(), 'pattern_generico': _OPERATORE_RE}
_OP_CACHE_TTL_S = 60


def _operatori_validi_provincia():
    """Set di valori validi per la colonna Operatore della provincia attiva.

    Composto da:
    - matricole/nomi in PROVINCE[PROV]['operatori_dict'] (chiave→valore)
    - matricole in PROVINCE[PROV]['operatori_vip']
    - valore speciale 'ANNULLATO' (stato comune di riassegnazione)

    Cache 60s per non rileggere config.PROVINCE ad ogni cella.
    """
    import time as _t
    now = _t.time()
    try:
        from config import PROVINCIA_ATTIVA, PROVINCE
    except ImportError:
        return _op_cache['valid_values']
    if (_op_cache['ts'] + _OP_CACHE_TTL_S > now
            and _op_cache['provincia'] == PROVINCIA_ATTIVA):
        return _op_cache['valid_values']
    cfg = PROVINCE.get(PROVINCIA_ATTIVA, {})
    valid = set()
    for v in (cfg.get('operatori_dict') or {}).values():
        if v:
            valid.add(str(v).strip().upper())
    for v in (cfg.get('operatori_vip') or []):
        if v:
            valid.add(str(v).strip().upper())
    valid.add('ANNULLATO')
    _op_cache['ts'] = now
    _op_cache['provincia'] = PROVINCIA_ATTIVA
    _op_cache['valid_values'] = valid
    return valid


def _is_operatore(value):
    """True se `value` è un operatore valido per la provincia attiva.
    Combina lista dinamica (config) + pattern matricola generica AE/AG/....
    Considera anche i valori in extra_valid_values delle regole admin.
    """
    v = (value or '').strip()
    if not v:
        return False
    upper = v.upper()
    # 1) Match esatto con lista per-provincia (case-insensitive)
    if upper in _operatori_validi_provincia():
        return True
    # 2) Extra valori configurati da admin UI per questa colonna
    rules = _load_validation_rules().get('Operatore', {})
    for extra in (rules.get('extra_valid_values') or []):
        if str(extra).strip().upper() == upper:
            return True
    # 3) Pattern matricola generica (per matricole nuove non ancora registrate)
    if _OPERATORE_RE.match(upper):
        return True
    return False


# ---- VALIDATION RULES configurabili da admin UI ----
# File: data/{provincia}/validation_rules.json
# Schema:
#   {
#     "Operatore": {"enabled": bool, "auto_correct": bool, "extra_valid_values": [...]},
#     "SIGILLO":   {"enabled": bool, "auto_correct": bool, "extra_valid_values": [...]},
#     ...
#   }
# Se file assente o colonna non presente, comportamento default:
#   enabled=True, auto_correct=True, extra_valid_values=[]
_rules_cache = {'ts': 0, 'provincia': None, 'rules': {}}
_RULES_CACHE_TTL_S = 60


def _validation_rules_path():
    try:
        from config import PROVINCIA_ATTIVA
    except ImportError:
        return None
    return os.path.join('data', PROVINCIA_ATTIVA.lower(), 'validation_rules.json')


def _load_validation_rules():
    """Ritorna dict delle regole correnti (per la provincia attiva).
    Default vuoto se file mancante. Cache 60s."""
    import time as _t
    now = _t.time()
    try:
        from config import PROVINCIA_ATTIVA
    except ImportError:
        return {}
    if (_rules_cache['ts'] + _RULES_CACHE_TTL_S > now
            and _rules_cache['provincia'] == PROVINCIA_ATTIVA):
        return _rules_cache['rules']
    path = _validation_rules_path()
    rules = {}
    if path and os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                rules = json.load(f) or {}
        except (OSError, ValueError) as e:
            logger.warning(f"[validation_rules] lettura fallita ({path}): {e}")
    _rules_cache['ts'] = now
    _rules_cache['provincia'] = PROVINCIA_ATTIVA
    _rules_cache['rules'] = rules
    return rules


def _column_rules(col_name):
    """Ritorna (enabled, auto_correct, extra_valid_set) per la colonna."""
    rules = _load_validation_rules().get(col_name, {})
    enabled = rules.get('enabled', True)
    auto_correct = rules.get('auto_correct', True)
    extra = set()
    for v in (rules.get('extra_valid_values') or []):
        if v:
            extra.add(str(v).strip().upper())
    return enabled, auto_correct, extra


def reset_rules_cache():
    """Forza il refresh delle regole alla prossima chiamata (chiamato dopo PUT)."""
    _rules_cache['ts'] = 0
    _rules_cache['provincia'] = None
    _rules_cache['rules'] = {}
# Data Installazione: DD/MM/YYYY (cifre 1-2 / cifre 1-2 / 4 cifre)
# OPPURE stato 'annullato' (case-insensitive) per contatori non installati.
_DATA_INST_RE = _re_validate.compile(
    r'^(\d{1,2}/\d{1,2}/\d{4}|annullato)$',
    _re_validate.IGNORECASE,
)
# Email: deve contenere @, niente spazi (single email, no liste).
# Forma: local@dominio.tld (TLD lungo minimo 2)
_EMAIL_RE = _re_validate.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]{2,}$')
# Esito: euristica "inizia con keyword maiuscola". La stringa è classificata
# come Esito se INIZIA con una di queste parole-chiave (case-sensitive).
# Maiuscolo per non confondersi con note generiche tipo "ho chiamato bergia".
_ESITO_RE = _re_validate.compile(
    r'^(ESITO\b|PS[A-Z]{2}\b|PROBLEMA\b|BEAT\b|BERGIA\b|VELINA\b|'
    r'BACK OFFICE\b|ANNULLATO\b|RIFIUTO\b|INSTALLATO\b|NON INSTALLATO\b|'
    r'NON SINCRONIZZATO\b|SINCRONIZZAZIONE\b|'
    r'TIMEOUT\b|CHIUSO\b|INVIATA\b).{0,100}$'
)
# Orari (fasce orarie lettere): classificatore "best effort". Riconosciamo
# solo il formato "dalle ... alle ..." (case-insensitive). Altri formati
# accettati (Pavia usa anche "9,00 12,00", non classificabili → fallback).
_ORARI_RE = _re_validate.compile(
    r'.*\bdalle\b.*\balle\b.*', _re_validate.IGNORECASE
)

# (A) Validatori: queste colonne rifiutano cambi non conformi.
_COLUMN_PATTERN_VALIDATORS = {
    'Operatore': _OPERATORE_RE,
    'SIGILLO': _SIGILLO_RE,
    'Matricola CE inst': _MATRICOLA_CE_RE,
    'cod.': _COD_RE,
    'Data Installazione': _DATA_INST_RE,
    'EMAIL': _EMAIL_RE,
}

# (B) Classificatore: include anche Esito e Orari. Usato dal dispatch.
_PATTERN_FOR_CLASSIFY = {
    'Operatore': _OPERATORE_RE,
    'SIGILLO': _SIGILLO_RE,
    'Matricola CE inst': _MATRICOLA_CE_RE,
    'cod.': _COD_RE,
    'Data Installazione': _DATA_INST_RE,
    'EMAIL': _EMAIL_RE,
    'Esito': _ESITO_RE,
    'Orari': _ORARI_RE,
}

# Mappa col_name → chiave_interna corrispondente (per scrivere su filone)
_CRITICAL_COL_TO_CHIAVE = {
    'Operatore': 'operatore',
    'SIGILLO': 'sigillo',
    'Matricola CE inst': 'matricola_installata',
    'cod.': 'codice_installato',
    'Data Installazione': 'data_installazione',
    'EMAIL': 'email',
    'Esito': 'esito',
    'Orari': 'orari_lettere',
}


def _find_target_column_by_pattern(value):
    """Per un valore, ritorna il nome della colonna critica che lo accetta
    (None se nessuno matcha). I pattern sono mutuamente esclusivi:
    ritorna al massimo una colonna.

    Operatore: classificazione dinamica per-provincia (lista nomi/matricole
    + pattern generico). Le altre colonne usano i pattern statici.
    """
    v = (value or '').strip()
    if not v:
        return None
    # Operatore prima (dinamico). Pattern mutuamente esclusivi: una matricola
    # non matcha anche SIGILLO/cod./Matricola CE/Data/EMAIL/Esito.
    if _is_operatore(v):
        return 'Operatore'
    # Altre colonne con pattern statico (skip Operatore qui)
    for col_name, pattern in _PATTERN_FOR_CLASSIFY.items():
        if col_name == 'Operatore':
            continue
        if pattern.match(v):
            return col_name
    return None


def _value_is_valid_for_column(col_name, value):
    """Ritorna True se `value` è valido per la colonna.

    Tiene conto delle regole admin (validation_rules.json):
    - enabled=False per la colonna → sempre True (validazione disattivata)
    - valore in extra_valid_values → sempre True
    - vuoto → sempre True (cancellazioni legittime)

    Per Operatore usa la lista dinamica per-provincia (vedi _is_operatore)
    invece del pattern regex statico.
    """
    if value is None or str(value).strip() == '':
        return True
    v = str(value).strip()
    col_low = col_name.strip().lower()
    # Risolvi il nome canonico della colonna (case-insensitive)
    canonical = None
    if col_low == 'operatore':
        canonical = 'Operatore'
    else:
        for known_name in _COLUMN_PATTERN_VALIDATORS.keys():
            if known_name.lower() == col_low:
                canonical = known_name
                break
    if canonical is None:
        return True  # nessun validatore per questa colonna
    enabled, _auto, extra = _column_rules(canonical)
    if not enabled:
        return True  # admin ha disattivato la validazione
    if v.upper() in extra:
        return True  # valore esplicitamente permesso dall'admin
    # Validazione standard
    if canonical == 'Operatore':
        return _is_operatore(v)
    return bool(_COLUMN_PATTERN_VALIDATORS[canonical].match(v))


# Quando il pattern check rifiuta un valore in SIGILLO/Operatore/Esito/
# Matricola CE inst/cod., e il valore NON è vuoto, viene salvato in NOTE APP
# anziché perso. Il valore già non matchava il pattern atteso della colonna,
# quindi qualsiasi sua forma (data con /, data senza /, testo, codice strano)
# va comunque conservata per non perdere informazione.
# Eccezione: scarta valori che sono chiaramente "puri codici" già conosciuti
# di altre colonne critiche (matricole operatore, sigilli) per evitare di
# riempire NOTE APP di rumore.
_PURE_MATRICOLA = _re_validate.compile(r'^A[A-Z]\d{4,7}$')
_PURE_SIGILLO = _re_validate.compile(r'^\d{6,}[A-Z]?$')


def _looks_like_rescuable_note(value):
    """Ritorna True se `value` va salvato in NOTE APP quando rifiutato dal
    pattern check di un'altra colonna. Strategia conservativa:
    - Vuoto: NO (niente da salvare).
    - Pura matricola (es. 'AG18966'): NO (è solo nel posto sbagliato per
      sbaglio, non una nota).
    - Puro sigillo (es. '20196086Z'): NO (idem).
    - Qualunque altra cosa: SÌ (date con/senza slash, testo libero, ecc.)."""
    s = (value or '').strip()
    if not s:
        return False
    if _PURE_MATRICOLA.match(s) or _PURE_SIGILLO.match(s):
        return False
    return True


# Buffer thread-local di eventi recovery raccolti durante il pull corrente.
# Alla fine di pull_once vengono scritti in data/{prov}/span_reports/recovery_YYYY-MM-DD.jsonl
_recovery_buffer = threading.local()


def _recovery_event(**fields):
    """Appende un evento al buffer del pull corrente (thread-local)."""
    if not hasattr(_recovery_buffer, 'events'):
        _recovery_buffer.events = []
    _recovery_buffer.events.append(fields)


def _flush_recovery_report():
    """Scrive il buffer eventi nel file jsonl giornaliero della provincia.
    Idempotente, no-op se buffer vuoto. Chiamata alla fine di pull_once."""
    events = getattr(_recovery_buffer, 'events', None)
    if not events:
        return
    try:
        from config import PROVINCIA_ATTIVA
        import datetime as _dt
        report_dir = os.path.join('data', PROVINCIA_ATTIVA.lower(), 'span_reports')
        os.makedirs(report_dir, exist_ok=True)
        fname = os.path.join(report_dir, f'recovery_{_dt.date.today().isoformat()}.jsonl')
        with open(fname, 'a', encoding='utf-8') as f:
            for e in events:
                f.write(json.dumps(e, ensure_ascii=False, default=str) + '\n')
        logger.warning(f"[recovery-report] scritti {len(events)} eventi in {fname}")
    except Exception as e:
        logger.warning(f"[recovery-report] write fallito: {e}")
    finally:
        _recovery_buffer.events = []


def _try_relocate_value(conn, *, eneltel_key, lcl, source_col, value, sheets_row=None):
    """Se `value` matcha il pattern di una colonna critica diversa da
    `source_col`, lo sposta lì. Ritorna True se ha fatto il dispatch.

    Comportamento dest_libera:
      - vuota: scrivi value lì
      - già con valore non valido (spanata anche lei): scrivi value lì
      - già con valore valido: ambiguità, NON sovrascrivere. Registra
        'value_relocation_skipped' per ispezione manuale.
    """
    target_col = _find_target_column_by_pattern(value)
    if not target_col or target_col == source_col:
        return False
    target_chiave = _CRITICAL_COL_TO_CHIAVE.get(target_col)
    if not target_chiave:
        return False

    cur_row = conn.execute(
        f"SELECT {target_chiave} FROM filone "
        "WHERE eneltel_key=? AND COALESCE(lcl,'')=?",
        (eneltel_key, lcl or ''),
    ).fetchone()
    cur_target = (cur_row[target_chiave] if cur_row else '') or ''
    target_pattern = _PATTERN_FOR_CLASSIFY.get(target_col)
    dest_libera = (not cur_target.strip()) or (
        target_pattern is not None
        and not target_pattern.match(cur_target.strip())
    )
    now = db._now_ts()
    value_str = str(value).strip()

    if not dest_libera:
        _recovery_event(
            ts=now,
            event='value_relocation_skipped',
            eneltel_key=eneltel_key, lcl=lcl or '',
            column=target_col,
            sheets_row=sheets_row,
            valore_in_arrivo=value_str,
            dest_attuale=cur_target,
            valore_origine=source_col,
            azione=f'spostamento {source_col}→{target_col} bloccato '
                   f'(dest ha già valore valido); ispezione manuale',
        )
        return True  # dispatch tentato (non applichiamo source_col change)

    # Dest libera: spostiamo il valore lì
    conn.execute(
        "DELETE FROM pending_writes WHERE eneltel_key=? AND COALESCE(lcl,'')=? "
        "AND column_name=? AND state IN ('pending','in_flight')",
        (eneltel_key, lcl or '', target_col),
    )
    conn.execute(
        'INSERT INTO pending_writes(eneltel_key, lcl, column_name, new_value, '
        'base_value, created_ts, user, origin, state) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (eneltel_key, lcl or '', target_col, value_str, cur_target,
         now, None, 'value_relocated', 'pending'),
    )
    # Aggiorno anche filone per UI immediata
    conn.execute(
        f"UPDATE filone SET {target_chiave}=?, last_local_change_ts=? "
        "WHERE eneltel_key=? AND COALESCE(lcl,'')=?",
        (value_str, now, eneltel_key, lcl or ''),
    )
    logger.warning(
        f"[auto-correct] {eneltel_key}: spostato {value_str[:30]!r} "
        f"da {source_col} a {target_col} (era={cur_target[:30]!r})"
    )
    _recovery_event(
        ts=now,
        event='value_relocated',
        eneltel_key=eneltel_key, lcl=lcl or '',
        column=target_col,
        sheets_row=sheets_row,
        sheets_aveva=cur_target,
        db_aveva=cur_target,
        valore_spostato_da=source_col,
        valore=value_str,
        azione=f'spostato in {target_col} (era libera o spanata)',
    )
    return True


def _enqueue_auto_correct(conn, *, eneltel_key, lcl, column_name, db_value, remote_value):
    """Genera una pending_write per ripristinare il valore corretto del DB su
    Sheets. Chiamata quando il pattern check rifiuta un valore remoto.

    La pending viene inserita con state='pending' e origin='pattern_auto_correct',
    base_value=remote_value (= valore corrente sul foglio). Il drainer la
    pusha al prossimo ciclo (~30s) e Sheets torna al valore corretto.
    Coalesce con eventuali pending già esistenti per la stessa cella (UNIQUE
    INDEX parziale): se l'utente ha già una pending in volo, la sua vince
    (questa nuova viene sostituita dalla precedente coalescing).

    EURISTICA "salva nota": se il valore rifiutato sembra una nota (inizia
    con 'XX/XX', tipico delle date utente), viene anche generata una pending
    per scriverlo nella colonna NOTE APP, concatenando al valore esistente.
    Così non si perde la nota in caso di span tipo NOTE APP↔SIGILLO.
    """
    db_val_str = '' if db_value is None else str(db_value)
    remote_val_str = '' if remote_value is None else str(remote_value)
    now = db._now_ts()
    # Verifica se auto-correzione è disabilitata per questa colonna
    _enabled, auto_correct_attivo, _extra = _column_rules(column_name)
    try:
        if auto_correct_attivo:
            # Coalesce: rimuovi eventuali pending precedenti non ancora pushate
            conn.execute(
                "DELETE FROM pending_writes WHERE eneltel_key=? AND COALESCE(lcl,'')=? "
                "AND column_name=? AND state IN ('pending','in_flight')",
                (eneltel_key, lcl or '', column_name),
            )
            conn.execute(
                'INSERT INTO pending_writes(eneltel_key, lcl, column_name, new_value, '
                'base_value, created_ts, user, origin, state) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (eneltel_key, lcl or '', column_name, db_val_str, remote_val_str,
                 now, None, 'pattern_auto_correct', 'pending'),
            )
            logger.warning(
                f"[auto-correct] {eneltel_key}/{column_name}: "
                f"Sheets={remote_val_str[:30]!r} → ripristino DB={db_val_str[:30]!r}"
            )
            _recovery_event(
                ts=db._now_ts(),
                event='pattern_auto_correct',
                eneltel_key=eneltel_key, lcl=lcl or '', column=column_name,
                sheets_aveva=remote_val_str, db_aveva=db_val_str,
                azione=f'ripristino su Sheets con valore DB',
            )
        else:
            # Auto-correzione disattivata da admin UI: registra l'anomalia
            # ma NON genera pending. Sheets resta com'è.
            logger.warning(
                f"[auto-correct DISABLED] {eneltel_key}/{column_name}: "
                f"valore '{remote_val_str[:30]}' fuori pattern, NON corretto "
                f"(auto_correct=false in validation_rules.json)"
            )
            _recovery_event(
                ts=db._now_ts(),
                event='pattern_violation_report_only',
                eneltel_key=eneltel_key, lcl=lcl or '', column=column_name,
                sheets_aveva=remote_val_str, db_aveva=db_val_str,
                azione=f'segnalato (auto-correzione disattivata da admin)',
            )

        # DISPATCH: se il valore rifiutato matcha il pattern di UN'ALTRA
        # colonna critica, prova a spostarlo lì.
        if _try_relocate_value(conn, eneltel_key=eneltel_key, lcl=lcl,
                               source_col=column_name, value=remote_val_str):
            return  # dispatch fatto (o ambiguità segnalata)

        # Fallback: il valore non matcha nessun pattern di colonna critica
        # → è probabilmente una nota libera. Salvalo in NOTE APP per non
        # perderlo, accodando al valore già esistente.
        if _looks_like_rescuable_note(remote_val_str):
            # Trova il nome esatto della colonna NOTE APP nel column_registry
            note_col_row = conn.execute(
                "SELECT column_name FROM column_registry "
                "WHERE chiave_interna='note_appuntamento' "
                "ORDER BY last_seen_ts DESC LIMIT 1"
            ).fetchone()
            if note_col_row:
                note_col = note_col_row['column_name']
                # Leggi valore corrente NOTE APP dal DB
                cur_note_row = conn.execute(
                    "SELECT note_appuntamento FROM filone "
                    "WHERE eneltel_key=? AND COALESCE(lcl,'')=?",
                    (eneltel_key, lcl or ''),
                ).fetchone()
                cur_note = (cur_note_row['note_appuntamento'] if cur_note_row else '') or ''
                # Concatena nuova nota con quella esistente (se diversa)
                rescued = remote_val_str.strip()
                if rescued in cur_note:
                    return  # già presente, niente da fare
                new_note = f"{rescued} // {cur_note}" if cur_note else rescued
                # Coalesce + insert pending per NOTE APP
                conn.execute(
                    "DELETE FROM pending_writes WHERE eneltel_key=? AND COALESCE(lcl,'')=? "
                    "AND column_name=? AND state IN ('pending','in_flight')",
                    (eneltel_key, lcl or '', note_col),
                )
                conn.execute(
                    'INSERT INTO pending_writes(eneltel_key, lcl, column_name, new_value, '
                    'base_value, created_ts, user, origin, state) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (eneltel_key, lcl or '', note_col, new_note, cur_note,
                     now, None, 'note_rescue_from_span', 'pending'),
                )
                # Aggiorno anche filone.note_appuntamento per UI immediata
                conn.execute(
                    "UPDATE filone SET note_appuntamento=?, last_local_change_ts=? "
                    "WHERE eneltel_key=? AND COALESCE(lcl,'')=?",
                    (new_note, now, eneltel_key, lcl or ''),
                )
                logger.warning(
                    f"[auto-correct] {eneltel_key}: salvata nota da {column_name} "
                    f"in {note_col}: {rescued[:40]!r}"
                )
                _recovery_event(
                    ts=now,
                    event='note_rescue_from_span',
                    eneltel_key=eneltel_key, lcl=lcl or '', column=note_col,
                    sheets_aveva=cur_note, db_aveva=cur_note,
                    nota_salvata_da_colonna=column_name,
                    valore_recuperato=rescued,
                    nota_dopo_concat=new_note,
                    azione=f'recovery di {column_name} in {note_col}',
                )
    except Exception as e:
        logger.warning(f"[auto-correct] enqueue fallito per {eneltel_key}/{column_name}: {e}")


# ============================================================================
# CONFIG ACCESSOR — leggiamo lazy così il modulo è importabile in test
# ============================================================================
def _cfg(name, default=None):
    try:
        import config as _config
        return getattr(_config, name, default)
    except ImportError:
        return default


# ============================================================================
# PULL: Sheets → DB
# ============================================================================
def pull_once(values=None):
    """Esegue una sincronizzazione PULL completa.

    Args:
        values: list[list[str]] — opzionale. Se fornito, usa questi dati
            invece di chiamare `get_all_values()` (utile per test e per
            riusare un download appena fatto da `data_loader.py`).

    Returns:
        dict con statistiche del pull (n righe, n cambi, n conflitti, ecc.)
    """
    t0 = time.time()

    if values is None:
        # Usa la cache condivisa con data_loader (TTL 30s) per evitare il
        # doppio download ad ogni sync che faceva esaurire la quota 429.
        values = shared.get_all_values_cached(force=False)

    if not values or len(values) < 2:
        logger.warning("[pull] Foglio vuoto o senza header")
        return {'rows': 0, 'changed': 0, 'new': 0, 'conflicts': 0, 'orphans': 0}

    headers = values[0]
    body = values[1:]

    # Hash globale per skip totale se invariato
    sheet_hash = db.compute_row_hash(headers + [v for row in body for v in row])
    last_hash = db.sync_state_get('last_full_pull_sheet_hash')
    if sheet_hash == last_hash:
        db.sync_state_set('last_full_pull_ts', db._now_ts())
        logger.info("[pull] Sheet invariato (hash globale identico), skip processing")
        return {'rows': len(body), 'changed': 0, 'new': 0, 'conflicts': 0,
                'orphans': 0, 'skipped_global_hash': True}

    conn = db.get_conn()

    # Trova la colonna eneltel nell'header (per identificare le righe)
    col_eneltel_idx = _find_eneltel_column_index(conn, headers)
    if col_eneltel_idx is None:
        logger.error("[pull] Colonna Eneltel non trovata negli header del foglio. Pull abortito.")
        return {'rows': 0, 'changed': 0, 'new': 0, 'conflicts': 0,
                'orphans': 0, 'error': 'eneltel_column_missing'}

    # Mappa nome_colonna_foglio -> chiave_interna (None per colonne extra)
    # Costruita una sola volta per tutto il pull.
    internal_lookup = db._build_internal_lookup()
    col_to_internal = {}  # idx_0based -> chiave_interna (o None)
    col_lcl_idx = None
    for i, name in enumerate(headers):
        if not name:
            continue
        chiave = internal_lookup.get(name.strip().lower())
        col_to_internal[i] = chiave
        if chiave == 'lcl':
            col_lcl_idx = i

    # ────────────────────────────────────────────────────────────────────
    # GUARDS ANTI-SPANAMENTO — eseguiti prima di applicare qualunque diff.
    # In caso di anomalia abortisco TUTTO il pull (no skip selettivo) e
    # lascio il DB intatto. Il prossimo pull riproverà.
    # ────────────────────────────────────────────────────────────────────
    hdr_ok, hdr_msg = _check_headers_consistent(conn, headers)
    if not hdr_ok:
        logger.error("=" * 70)
        logger.error("[pull] ⛔ SCHEMA HEADER DEL FOGLIO CAMBIATO — pull abortito")
        logger.error(f"  {hdr_msg}")
        logger.error("  Il DB locale NON è stato modificato. Verificare manualmente il foglio.")
        logger.error("=" * 70)
        try:
            db.record_anomaly(conn, kind='header_changed',
                              detail={'msg': hdr_msg})
        except Exception:
            pass
        return {'rows': len(body), 'changed': 0, 'new': 0, 'conflicts': 0,
                'orphans': 0, 'aborted': 'header_changed', 'detail': hdr_msg}

    canary_ok, canary_msg = _check_canary_rows(conn, headers, body)
    if not canary_ok:
        logger.error("=" * 70)
        logger.error("[pull] ⛔ SPANAMENTO BODY RILEVATO (canary check fallito) — pull abortito")
        logger.error(f"  {canary_msg}")
        logger.error("  Il DB locale NON è stato modificato. Probabile shift/incollaggio")
        logger.error("  errato di valori nelle celle. Verificare il foglio manualmente.")
        logger.error("=" * 70)
        try:
            db.record_anomaly(conn, kind='canary_failed',
                              detail={'msg': canary_msg})
        except Exception:
            pass
        return {'rows': len(body), 'changed': 0, 'new': 0, 'conflicts': 0,
                'orphans': 0, 'aborted': 'canary_failed', 'detail': canary_msg}

    n_changed = 0
    n_new = 0
    n_conflicts = 0
    eneltel_seen = set()
    duplicates = []

    with conn:
        conn.execute('BEGIN IMMEDIATE')

        # Aggiorna column_registry con header attuale
        db.update_column_registry(conn, headers)

        # Pulisci anomalie ricalcolabili. NB: 'duplicate_eneltel' non viene
        # più registrato dal v2 in poi (le righe con stesso Eneltel + LCL
        # diverso sono legittime); puliamo eventuali residui storici.
        conn.execute(
            "DELETE FROM filone_anomalies "
            "WHERE kind IN ('duplicate_eneltel', 'row_without_eneltel', 'duplicate_eneltel_lcl')"
        )

        for row_offset, row_values in enumerate(body):
            sheets_row = row_offset + 2  # 1-based + skip header
            eneltel_raw = (row_values[col_eneltel_idx]
                           if col_eneltel_idx < len(row_values) else '')
            eneltel_key = db.normalize_eneltel(eneltel_raw)

            if not eneltel_key:
                # riga senza Eneltel — anomalia da loggare ma non bloccante
                if any(str(v).strip() for v in row_values):
                    db.record_anomaly(
                        conn, kind='row_without_eneltel',
                        sheets_row=sheets_row,
                        detail={'first_values': row_values[:5]},
                    )
                continue

            # LCL della riga: parte della chiave composta. Se vuoto, '' (no None).
            lcl_val = ''
            if col_lcl_idx is not None and col_lcl_idx < len(row_values):
                lcl_val = str(row_values[col_lcl_idx] or '').strip()

            row_key = (eneltel_key, lcl_val)
            if row_key in eneltel_seen:
                # Stesso Eneltel + stesso LCL su 2 righe → anomalia vera
                duplicates.append({'eneltel_key': eneltel_key, 'lcl': lcl_val,
                                   'sheets_row': sheets_row})
                db.record_anomaly(
                    conn, kind='duplicate_eneltel_lcl',
                    eneltel_key=eneltel_key, sheets_row=sheets_row,
                    detail={'lcl': lcl_val},
                )
                continue
            eneltel_seen.add(row_key)

            # Hash della riga (basato sui valori nell'ordine del foglio)
            row_hash = db.compute_row_hash(row_values)

            # Confronto con stato locale (chiave composta)
            cur = conn.execute(
                "SELECT * FROM filone WHERE eneltel_key=? AND COALESCE(lcl,'')=?",
                (eneltel_key, lcl_val),
            )
            existing = cur.fetchone()

            if existing is not None and existing['row_hash'] == row_hash and \
                    existing['sheets_row'] == sheets_row:
                conn.execute(
                    "UPDATE filone SET last_remote_pull_ts=?, is_orphan=0 "
                    "WHERE eneltel_key=? AND COALESCE(lcl,'')=?",
                    (db._now_ts(), eneltel_key, lcl_val),
                )
                continue

            # Cambio rilevato (o riga nuova). Carico pending in volo per (eneltel, lcl).
            pending_by_col = _load_pending_by_col(conn, eneltel_key, lcl_val)

            # Costruisco typed_values + extra_values per upsert
            typed_values = {}
            extra_values = {}
            skip_columns = set()  # colonne con conflitto pending: NON sovrascrivere

            for col_idx, col_name in enumerate(headers):
                if not col_name or col_idx >= len(row_values):
                    continue
                remote_val = row_values[col_idx] if col_idx < len(row_values) else ''
                chiave = col_to_internal.get(col_idx)

                pending = pending_by_col.get(col_name)
                if pending:
                    # Pending in volo per questa cella — confronto con base_value
                    base_val = pending.get('base_value') or ''
                    remote_norm = '' if remote_val is None else str(remote_val)
                    if remote_norm != base_val:
                        # CONFLITTO: il valore remoto è cambiato dopo la base.
                        # Policy local_wins: NON aggiorno il DB su questa cella;
                        # il pending vincerà al prossimo drain.
                        db.audit(
                            conn,
                            eneltel_key=eneltel_key,
                            column_name=col_name,
                            old_value=base_val,
                            new_value=remote_val,
                            source='conflict_local_wins',
                            detail={
                                'pending_id': pending.get('id'),
                                'pending_value': pending.get('new_value'),
                                'sheets_row': sheets_row,
                            },
                        )
                        n_conflicts += 1
                    if chiave and chiave in db.COLONNE_NOTE:
                        skip_columns.add(chiave)
                    else:
                        skip_columns.add(col_name)
                    continue

                # Nessun pending: cambio remoto puro o riga nuova
                if chiave == 'eneltel':
                    continue  # è la PK, non la salvo come campo
                if chiave and chiave in db.COLONNE_NOTE:
                    new_typed = _coerce_value(remote_val, chiave)
                    # Per le colonne REAL (lat/lon) confronto numerico:
                    # Sheets restituisce le decimali con la virgola (locale
                    # italiano), il DB le ha come float (notazione punto).
                    # Senza coercion il confronto stringa segnerebbe
                    # sempre un falso "cambio remoto" ad ogni pull.
                    old = existing[chiave] if existing is not None and chiave in existing.keys() else None
                    if chiave in db._COLONNE_REAL:
                        old_typed = _coerce_value(old, chiave)
                        changed = (existing is not None) and (old_typed != new_typed)
                    else:
                        changed = (existing is not None) and (str(old or '') != str(remote_val or ''))

                    # Validazione pattern per colonne critiche (SIGILLO,
                    # Operatore, Esito, Matricola CE inst, cod.). Se il
                    # valore remoto non matcha il pattern atteso, NON
                    # applico il diff: DB tiene il vecchio. Inoltre,
                    # AUTO-CORREZIONE: genero una pending che il drainer
                    # pusherà su Sheets per ripristinare il valore corretto.
                    if changed and not _value_is_valid_for_column(col_name, remote_val):
                        db.record_anomaly(
                            conn, kind='invalid_pattern_skipped',
                            eneltel_key=eneltel_key, sheets_row=sheets_row,
                            detail={'column': col_name,
                                    'remote_value': str(remote_val)[:60],
                                    'db_value': str(old or '')[:60]},
                        )
                        db.audit(
                            conn,
                            eneltel_key=eneltel_key,
                            column_name=col_name,
                            old_value=old,
                            new_value=remote_val,
                            source='invalid_pattern_skipped',
                            detail={'kept_db_value': True, 'auto_correct': True},
                        )
                        _enqueue_auto_correct(
                            conn, eneltel_key=eneltel_key, lcl=lcl_val,
                            column_name=col_name, db_value=old,
                            remote_value=remote_val,
                        )
                        skip_columns.add(chiave)
                        continue

                    # Colonna senza validator (es. NOTE APP, Indirizzo, ecc.).
                    # Dispatch generico: se il valore matcha ESATTAMENTE il
                    # pattern di una colonna critica (no testo affiancato),
                    # lo sposta nella sua colonna. Esempio: 'AG18966' arriva
                    # in NOTE APP → spostato in Operatore. Invece 'AG18966
                    # in vacanza' NON matcha (testo dopo) → resta nota.
                    if changed and col_name not in _COLUMN_PATTERN_VALIDATORS:
                        if _try_relocate_value(
                            conn, eneltel_key=eneltel_key, lcl=lcl_val,
                            source_col=col_name, value=remote_val,
                            sheets_row=sheets_row,
                        ):
                            # Dispatch fatto: NON aggiorno questa cella nel DB,
                            # la sua "vecchia" semantica resta. Per la cella
                            # destinazione c'è già una pending in volo.
                            skip_columns.add(chiave)
                            continue

                    typed_values[chiave] = new_typed
                    if changed:
                        db.audit(
                            conn,
                            eneltel_key=eneltel_key,
                            column_name=col_name,
                            old_value=old,
                            new_value=remote_val,
                            source='remote',
                        )
                else:
                    # Colonne extra (non in COLONNE_NOTE): valida solo se ha
                    # un pattern definito (es. cod. non è in COLONNE_NOTE).
                    if remote_val and not _value_is_valid_for_column(col_name, remote_val):
                        # Skip dell'extra: leggo il vecchio valore da filone_extra
                        ex_cur = conn.execute(
                            "SELECT value FROM filone_extra "
                            "WHERE eneltel_key=? AND COALESCE(lcl,'')=? AND column_name=?",
                            (eneltel_key, lcl_val, col_name),
                        ).fetchone()
                        old_ex = ex_cur['value'] if ex_cur else None
                        if str(old_ex or '') != str(remote_val or ''):
                            db.record_anomaly(
                                conn, kind='invalid_pattern_skipped',
                                eneltel_key=eneltel_key, sheets_row=sheets_row,
                                detail={'column': col_name,
                                        'remote_value': str(remote_val)[:60],
                                        'db_value': str(old_ex or '')[:60]},
                            )
                            db.audit(
                                conn,
                                eneltel_key=eneltel_key,
                                column_name=col_name,
                                old_value=old_ex,
                                new_value=remote_val,
                                source='invalid_pattern_skipped',
                                detail={'kept_db_value': True, 'auto_correct': True},
                            )
                            _enqueue_auto_correct(
                                conn, eneltel_key=eneltel_key, lcl=lcl_val,
                                column_name=col_name, db_value=old_ex,
                                remote_value=remote_val,
                            )
                            skip_columns.add(col_name)
                            continue
                    extra_values[col_name] = remote_val

            db.upsert_filone_row(
                conn,
                eneltel_key=eneltel_key,
                lcl=lcl_val,
                sheets_row=sheets_row,
                typed_values=typed_values,
                extra_values=extra_values,
                row_hash=row_hash,
                source='remote',
                skip_columns=skip_columns,
            )

            if existing is None:
                n_new += 1
            else:
                n_changed += 1

        n_orphans = db.mark_orphans(conn, eneltel_seen)

        db.sync_state_set('last_full_pull_ts', db._now_ts())
        db.sync_state_set('last_full_pull_sheet_hash', sheet_hash)

    elapsed = time.time() - t0
    # Flush eventi recovery raccolti durante il pull (se presenti) nel
    # file giornaliero data/{prov}/span_reports/recovery_YYYY-MM-DD.jsonl
    _flush_recovery_report()
    result = {
        'rows': len(body),
        'changed': n_changed,
        'new': n_new,
        'conflicts': n_conflicts,
        'orphans': n_orphans,
        'duplicates': len(duplicates),
        'elapsed': round(elapsed, 2),
    }
    if n_changed or n_new or n_conflicts or n_orphans:
        logger.info(f"[pull] {result}")
    return result


def _find_eneltel_column_index(conn, headers):
    """Trova l'indice 0-based della colonna Eneltel negli header forniti."""
    lookup = db._build_internal_lookup()
    for i, name in enumerate(headers):
        if not name:
            continue
        if lookup.get(name.strip().lower()) == 'eneltel':
            return i
    return None


# ============================================================================
# GUARDS ANTI-SPANAMENTO
# ============================================================================
def _check_headers_consistent(conn, headers_now):
    """Verifica che gli header attuali del foglio corrispondano al
    column_registry del DB (nomi + posizioni).

    Ritorna (True, '') se OK; (False, msg) se: una colonna nota è SPARITA,
    è stata SPOSTATA di indice, o è stata RINOMINATA.

    NOTA: l'aggiunta di NUOVE colonne in coda è permessa (es. nuova colonna
    sul foglio che il DB non conosce ancora). Solo le colonne note che
    cambiano posizione/scompaiono fanno scattare il blocco.
    """
    cur = conn.execute(
        "SELECT column_name, last_index, last_seen_ts "
        "FROM column_registry ORDER BY last_seen_ts DESC"
    )
    # Tieni solo la versione più recente per ogni last_index (dedup alias storici)
    registry = {}
    seen_idx = set()
    for r in cur.fetchall():
        if r['last_index'] in seen_idx:
            continue
        seen_idx.add(r['last_index'])
        registry[r['column_name']] = r['last_index']
    if not registry:
        return True, 'registry vuoto (primo pull)'

    headers_idx = {h: i + 1 for i, h in enumerate(headers_now) if h}
    moved, removed = [], []
    for name, expected_idx in registry.items():
        actual_idx = headers_idx.get(name)
        if actual_idx is None:
            removed.append(name)
        elif actual_idx != expected_idx:
            moved.append(f"{name}: era idx {expected_idx}, ora {actual_idx}")
    if removed or moved:
        msg_parts = []
        if removed:
            msg_parts.append(f"colonne mancanti: {removed[:8]}")
        if moved:
            msg_parts.append(f"colonne spostate: {moved[:8]}")
        return False, '; '.join(msg_parts)
    return True, ''


# Colonne "ancora" usate dal canary check: campi che identificano una riga
# in modo stabile e cambiano raramente nel tempo. Se sul foglio un body è
# stato shiftato (incollaggio errato, span massivo), almeno una di queste 4
# si troverà in una colonna sbagliata e il confronto col DB darà mismatch.
_CANARY_COLUMNS = {
    'pod': ('POD', 'CODICE POD', 'POD '),
    'nominativo': ('NOMINATIVO', 'CLIENTE', 'NOME'),
    'indirizzo': ('INDIRIZZO', 'VIA'),
    'comune': ('COMUNE',),
}


def _check_canary_rows(conn, headers_now, body, sample_size=20, max_mismatch_ratio=0.5):
    """Verifica colonne ancora (POD/NOMINATIVO/INDIRIZZO/COMUNE) su un
    campione di righe già conosciute nel DB.

    Se sul foglio i valori del body sono stati shiftati di colonna anche
    parzialmente, almeno una delle 4 colonne ancora si troverà fuori posto
    e il confronto col DB rileverà mismatch. Sono campi che cambiano
    raramente, quindi un mismatch massivo è segnale forte di span.

    Ritorna (True, '') se OK; (False, msg) se > max_mismatch_ratio del
    campione ha ALMENO UNA delle 4 colonne ancora diversa dal DB.
    """
    # Trova indici delle ancore + Eneltel negli header del foglio
    eneltel_idx = None
    canary_idx = {}  # chiave_interna -> idx
    for i, h in enumerate(headers_now):
        if not h:
            continue
        hu = h.strip().upper()
        if hu == 'ENELTEL':
            eneltel_idx = i
            continue
        for chiave, aliases in _CANARY_COLUMNS.items():
            if hu in (a.upper() for a in aliases) and chiave not in canary_idx:
                canary_idx[chiave] = i
                break

    if eneltel_idx is None or not canary_idx:
        return True, 'colonne canary non disponibili (skip)'

    n = len(body)
    if n < 5:
        return True, 'foglio troppo piccolo (skip)'
    step = max(1, n // sample_size)
    sample_offsets = list(range(0, n, step))[:sample_size]

    n_checked = 0
    n_mismatch = 0
    examples = []
    canary_cols = list(canary_idx.keys())
    sel_cols = ', '.join(canary_cols)
    for row_offset in sample_offsets:
        row = body[row_offset]
        if eneltel_idx >= len(row):
            continue
        eneltel_key = db.normalize_eneltel(row[eneltel_idx])
        if not eneltel_key:
            continue
        existing = conn.execute(
            f"SELECT {sel_cols} FROM filone WHERE eneltel_key=? LIMIT 1",
            (eneltel_key,),
        ).fetchone()
        if existing is None:
            continue

        n_checked += 1
        mismatch_fields = []
        for chiave in canary_cols:
            i = canary_idx[chiave]
            sheet_val = (row[i] if i < len(row) else '').strip()
            db_val = (existing[chiave] or '').strip()
            # Confronto solo se entrambi popolati (modifiche legittime
            # del foglio possono svuotare un campo: non blocchiamo per quello).
            if sheet_val and db_val and sheet_val != db_val:
                mismatch_fields.append(chiave)

        if mismatch_fields:
            n_mismatch += 1
            if len(examples) < 3:
                ex = {'eneltel': eneltel_key, 'campi_diversi': mismatch_fields}
                for chiave in mismatch_fields:
                    i = canary_idx[chiave]
                    sheet_val = (row[i] if i < len(row) else '').strip()[:24]
                    db_val = (existing[chiave] or '').strip()[:24]
                    ex[chiave] = {'sheet': sheet_val, 'db': db_val}
                examples.append(ex)

    if n_checked < 5:
        return True, f'campione canary insufficiente ({n_checked} righe)'

    ratio = n_mismatch / n_checked if n_checked else 0
    if ratio > max_mismatch_ratio:
        return False, (f"{n_mismatch}/{n_checked} righe campione con almeno "
                       f"un'ancora diversa ({ratio:.0%}, soglia {max_mismatch_ratio:.0%}). "
                       f"Ancore controllate: {canary_cols}. Esempi: {examples}")
    return True, ''


def _load_pending_by_col(conn, eneltel_key, lcl=''):
    """Mappa {column_name: pending_dict} per le pending non ancora pushate
    di una specifica coppia (eneltel_key, lcl)."""
    cur = conn.execute(
        "SELECT id, column_name, new_value, base_value, state FROM pending_writes "
        "WHERE eneltel_key=? AND COALESCE(lcl,'')=? AND state IN ('pending','in_flight')",
        (eneltel_key, lcl or ''),
    )
    return {r['column_name']: dict(r) for r in cur.fetchall()}


def _coerce_value(value, chiave):
    """Coerce di un valore stringa al tipo SQL della colonna (REAL per lat/lon)."""
    if value is None:
        return None
    if chiave in ('latitudine', 'longitudine'):
        s = str(value).strip().replace(',', '.')
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None
    return str(value)


# ============================================================================
# PUSH: DB → Sheets
# ============================================================================
def drain_once(*, batch_size=None):
    """Drena un batch di pending_writes verso Google Sheets.

    Returns:
        dict con statistiche.
    """
    if _cfg('USE_DB_BACKEND', 'shadow') == 'off':
        return {'drained': 0, 'skipped': True, 'reason': 'USE_DB_BACKEND=off'}

    # Stop di sicurezza: se nelle ultime 6h un pull ha rilevato anomalia
    # strutturale del foglio (header changed, canary failed), il drainer
    # NON pusha — il foglio potrebbe essere in stato spanato e scrivere
    # sopra peggiorerebbe la situazione.
    conn = db.get_conn()
    recent_block = conn.execute(
        "SELECT ts, kind, detail FROM filone_anomalies "
        "WHERE kind IN ('header_changed','canary_failed') "
        "AND ts > ? ORDER BY ts DESC LIMIT 1",
        (db._now_ts() - 6 * 3600,),
    ).fetchone()
    if recent_block:
        logger.warning(
            f"[drain] ⏸️  bloccato per anomalia recente: kind={recent_block['kind']} "
            f"detail={(recent_block['detail'] or '')[:200]}. Il drainer riprenderà "
            f"automaticamente dopo 6h o quando l'anomalia viene risolta."
        )
        return {'drained': 0, 'skipped': True, 'reason': f"anomaly_{recent_block['kind']}"}

    batch_size = batch_size or _cfg('DRAIN_BATCH_SIZE', 400)
    pending = db.list_pending_writes(state='pending', limit=batch_size)
    if not pending:
        return {'drained': 0}

    # Marca tutte come in_flight (lock applicativo)
    ids = [p['id'] for p in pending]
    db.mark_pending_in_flight(ids)

    # Risolvi nome ANCORA (Eneltel). safe_batch_update_by_name farà
    # force_refresh_headers internamente; qui basta un nome standard.
    eneltel_col_name = shared._resolve_anchor_col_name('eneltel') or 'Eneltel'

    # Costruisci items: per ogni pending, prendi `sheets_row` dalla riga
    # corrispondente in `filone` identificata da (eneltel_key, lcl).
    # safe_batch_update_by_name rimappa al volo via ancora se la riga è
    # stata spostata sul foglio.
    conn = db.get_conn()
    rows_map = {}  # (eneltel_key, lcl) -> sheets_row
    keys = list({(p['eneltel_key'], (p['lcl'] or '') if 'lcl' in p.keys() else '')
                 for p in pending})
    for ek, lcl in keys:
        cur = conn.execute(
            "SELECT sheets_row FROM filone WHERE eneltel_key=? AND COALESCE(lcl,'')=?",
            (ek, lcl),
        ).fetchone()
        if cur:
            rows_map[(ek, lcl)] = cur['sheets_row']

    items = []
    item_to_pending_id = []
    pending_without_row = []
    for p in pending:
        ek = p['eneltel_key']
        lcl = (p['lcl'] or '') if 'lcl' in p.keys() else ''
        sheets_row = rows_map.get((ek, lcl))
        if sheets_row is None:
            pending_without_row.append(p['id'])
            continue
        items.append({
            'row': sheets_row,
            'col': p['column_name'],
            'value': p['new_value'],
            'anchor': ek,
            'anchor_lcl': lcl,
        })
        item_to_pending_id.append(p['id'])

    # Pending verso eneltel non in DB locale: quarantina (caso strano,
    # tipicamente solo durante la migrazione iniziale).
    if pending_without_row:
        db.quarantine_pending(pending_without_row, 'no_row_in_local_filone')

    if not items:
        return {'drained': 0, 'quarantined': len(pending_without_row)}

    # Esegui la scrittura tramite l'API canonica (riusa lock + anchor remap).
    # _force_sheets=True bypassa il branch primary: il drainer DEVE pushare
    # su Sheets, non re-instradare al DB (sarebbe un loop infinito).
    try:
        res = shared.safe_batch_update_by_name(
            items, anchor_col=eneltel_col_name, anchor_normalize='lstrip0',
            _force_sheets=True,
        )
    except Exception as e:
        logger.exception("[drain] safe_batch_update_by_name ha sollevato eccezione")
        db.mark_pending_failed(item_to_pending_id, str(e), retry_in_seconds=60)
        return {'drained': 0, 'error': str(e)}

    # Distribuisci esiti per item:
    #   - lost_anchors → quarantine
    #   - invalid_cols → quarantine (colonna mancante sul foglio)
    #   - written/remapped → done
    lost_anchor_keys = {(la.get('row'), la.get('col'), str(la.get('anchor', '')).lstrip('0'))
                       for la in res.get('lost_anchors', [])}
    invalid_cols = set(res.get('invalid_cols', []))

    done_ids = []
    quarantined_ids = []
    quarantined_reasons = {}

    for item, pid, p in zip(items, item_to_pending_id, pending):
        anchor_key = (item['row'], item['col'], str(item['anchor']).lstrip('0'))
        if anchor_key in lost_anchor_keys:
            quarantined_ids.append(pid)
            quarantined_reasons[pid] = 'anchor_lost'
            continue
        if item['col'] in invalid_cols:
            quarantined_ids.append(pid)
            quarantined_reasons[pid] = 'column_missing'
            continue
        done_ids.append(pid)

    if done_ids:
        db.mark_pending_done(done_ids)
        # Aggiorna last_local_change_ts e invalida row_hash così il prossimo
        # pull rileverà il cambio scritto e ricalcolerà l'hash.
        with conn:
            placeholders = ','.join('?' * len(done_ids))
            conn.execute(
                f"UPDATE filone SET row_hash='' WHERE eneltel_key IN ("
                f"  SELECT eneltel_key FROM pending_writes WHERE id IN ({placeholders})"
                f")",
                done_ids,
            )

    for pid in quarantined_ids:
        db.quarantine_pending([pid], quarantined_reasons[pid])

    if not res.get('success'):
        logger.warning(
            f"[drain] write parziale: written={res.get('written')} "
            f"skipped={res.get('skipped')} invalid_cols={res.get('invalid_cols')} "
            f"lost_anchors={len(res.get('lost_anchors', []))}"
        )

    db.sync_state_set('last_drain_ts', db._now_ts())

    return {
        'drained': len(done_ids),
        'quarantined': len(quarantined_ids) + len(pending_without_row),
        'remapped': res.get('remapped', 0),
        'gsheets_result': {
            'written': res.get('written', 0),
            'skipped': res.get('skipped', 0),
            'success': res.get('success', False),
        },
    }


# ============================================================================
# WORKERS (loop daemon)
# ============================================================================
_workers_started = False
_workers_stop = threading.Event()
_drain_wakeup = threading.Event()


def trigger_drain_now():
    """Risveglia il drain worker per far partire subito un ciclo."""
    _drain_wakeup.set()


_BACKLOG_WARN_THRESHOLD = 500   # pending in stato 'pending'/'in_flight'
_BACKLOG_ATTEMPTS_WARN = 10     # attempts cumulati su una singola pending
_backlog_alerting_state = {'last_warn_ts': 0, 'last_warn_size': 0}


def _check_backlog_health(conn):
    """Logga warning se il backlog pending è anormalmente grande o ha
    pending con troppi tentativi falliti (drainer in difficoltà o Sheets down).

    Throttle: ripete il warning solo se sono passati >5 minuti dall'ultimo
    O se il backlog è cresciuto significativamente (>20%).
    """
    import time as _time
    row = conn.execute(
        "SELECT COUNT(*) AS n, MAX(attempts) AS max_att "
        "FROM pending_writes WHERE state IN ('pending','in_flight')"
    ).fetchone()
    n = row['n'] or 0
    max_att = row['max_att'] or 0
    now = int(_time.time())

    issues = []
    if n >= _BACKLOG_WARN_THRESHOLD:
        issues.append(f"backlog={n} (soglia {_BACKLOG_WARN_THRESHOLD})")
    if max_att >= _BACKLOG_ATTEMPTS_WARN:
        issues.append(f"max_attempts={max_att} (soglia {_BACKLOG_ATTEMPTS_WARN})")

    if not issues:
        _backlog_alerting_state['last_warn_size'] = 0  # reset al rientro
        return

    last_ts = _backlog_alerting_state['last_warn_ts']
    last_size = _backlog_alerting_state['last_warn_size']
    repeat = (now - last_ts) > 300 or (n > last_size * 1.2 if last_size else True)
    if repeat:
        logger.warning(
            f"[drain] ⚠️ BACKLOG ANOMALO: {', '.join(issues)}. "
            f"Verifica /api/admin/db-pending. Possibili cause: Sheets API down, "
            f"quota esaurita, colonne mancanti."
        )
        _backlog_alerting_state['last_warn_ts'] = now
        _backlog_alerting_state['last_warn_size'] = n


def _drain_loop():
    """Loop daemon che drena pending_writes verso Sheets."""
    interval = _cfg('DRAIN_INTERVAL_SECONDI', 10)
    logger.info(f"[drain] worker avviato (interval={interval}s)")
    while not _workers_stop.is_set():
        try:
            res = drain_once()
            if res.get('drained') or res.get('quarantined'):
                logger.info(f"[drain] {res}")
            # Check backlog health (warning se anormale)
            try:
                _check_backlog_health(db.get_conn())
            except Exception:
                logger.exception("[drain] errore check_backlog_health")
        except Exception:
            logger.exception("[drain] errore nel ciclo")

        # Aspetta interval o risveglio anticipato
        _drain_wakeup.wait(timeout=interval)
        _drain_wakeup.clear()
    logger.info("[drain] worker fermato")


def _check_sheet_substitution():
    """Verifica che il foglio configurato corrisponda a quello con cui il DB
    è stato bootstrappato. Se diverso, blocca i workers e logga un errore
    esplicito: ricostruzione manuale necessaria (tools/migrate_to_sqlite.py).

    Casi che intercetta:
      - Admin edita `id_foglio` di una provincia esistente da UI router
      - Manual edit di `provinces.json` per puntare a un foglio diverso
      - Sostituzione del backend Google Sheet
    Senza questo check, il drainer pusherebbe le pending del vecchio foglio
    sul nuovo, e il pull marcherebbe come 'orphan' tutte le righe pre-esistenti.

    Ritorna True se OK (procedi con workers), False se mismatch.
    """
    try:
        from config import ID_FOGLIO_GOOGLE
    except ImportError:
        logger.warning("[sync_engine] config.ID_FOGLIO_GOOGLE non disponibile, skip check")
        return True

    stored = db.sync_state_get('last_sheet_id')
    if not stored:
        # Mai bootstrappato (caso fresco): salva il valore corrente e procedi.
        # Se è il primo avvio post-migrate_to_sqlite, il tool ha già salvato il
        # valore. Qui copriamo il caso di DB pre-existente senza marker.
        db.sync_state_set('last_sheet_id', ID_FOGLIO_GOOGLE)
        logger.info(f"[sync_engine] last_sheet_id inizializzato a {ID_FOGLIO_GOOGLE}")
        return True

    if stored == ID_FOGLIO_GOOGLE:
        return True

    logger.error("=" * 70)
    logger.error("[sync_engine] ⛔ SHEET SUBSTITUTION RILEVATA — workers NON avviati")
    logger.error(f"  Foglio bootstrappato nel DB: {stored}")
    logger.error(f"  Foglio attualmente configurato: {ID_FOGLIO_GOOGLE}")
    logger.error("  Il DB locale contiene dati del foglio precedente. Avviare drainer")
    logger.error("  e pull engine ora corromperebbe entrambi i fogli.")
    logger.error("  Soluzione: scegli una delle due opzioni:")
    logger.error("    a) Ripristina id_foglio precedente in provinces.json")
    logger.error("    b) Ricostruisci il DB dal nuovo foglio:")
    logger.error("       PROVINCIA=XXX ./venv/bin/python tools/migrate_to_sqlite.py --reset")
    logger.error("=" * 70)
    return False


def start_workers():
    """Avvia i worker daemon. Chiamato da server.py al boot.

    Idempotente: chiamate ripetute non avviano worker duplicati.
    Esegue check anti-substitution prima di avviare: se il foglio configurato
    non corrisponde a quello bootstrappato nel DB, blocca i workers e logga
    un errore esplicito (vedi _check_sheet_substitution).
    """
    global _workers_started
    if _workers_started:
        return
    if _cfg('USE_DB_BACKEND', 'shadow') == 'off':
        logger.info("[sync_engine] USE_DB_BACKEND=off, worker NON avviati")
        return

    # Recovery: integrity check + reset in_flight (già fatto in db.bootstrap,
    # questo è un secondo livello di sicurezza).
    integrity = db.integrity_check()
    if integrity != 'ok':
        logger.error(f"[sync_engine] PRAGMA integrity_check FAIL: {integrity}")
    else:
        logger.info(f"[sync_engine] DB integrity OK: {db.stats()}")

    # Anti-substitution: blocca se il foglio configurato non è quello bootstrappato.
    if not _check_sheet_substitution():
        logger.error("[sync_engine] workers NON avviati per sheet substitution")
        return

    t = threading.Thread(target=_drain_loop, daemon=True, name='db-drain')
    t.start()

    _workers_started = True


def stop_workers():
    """Ferma i worker (chiamato in shutdown)."""
    _workers_stop.set()
    _drain_wakeup.set()
