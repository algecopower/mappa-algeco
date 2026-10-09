# -*- coding: utf-8 -*-
"""
Archivio lettere e contatti aziendali.

I file Excel in output/lettere restano artefatti scaricabili; la fonte dati
operativa è il DB locale. I recapiti compilati qui vengono salvati nella
rubrica lettere e, quando il POD è presente nel filone, anche nelle colonne
telefono/email del filone locale.
"""
import hashlib
import json
import logging
import os
import re
import time
from typing import Iterable

from . import db

logger = logging.getLogger(__name__)


_PEC_RE = re.compile(
    r'(@pec\.|@pec-|\.pec@|@legalmail\.|\.legalmail\.|@cert\.|@postacert\.|'
    r'@cgn\.legalmail|@arubapec\.|@pcert\.|@sicurezzapostale\.|'
    r'@certifiposta\.|@interfreepec\.)',
    re.IGNORECASE,
)


def _provincia_attiva():
    try:
        from config import PROVINCIA_ATTIVA
        return PROVINCIA_ATTIVA.upper()
    except Exception:
        return 'TORINO'


def _now_ts():
    return int(time.time())


def _clean(value):
    if value is None:
        return ''
    text = str(value).strip()
    if text.lower() in ('nan', 'none', 'null'):
        return ''
    return text


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _relpath(path):
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.relpath(os.path.abspath(path), root)
    except Exception:
        return path


def contact_key(pod='', nominativo='', comune=''):
    """Chiave stabile del contatto: POD quando presente, hash altrimenti."""
    pod_norm = _clean(pod).upper()
    if pod_norm:
        return f'POD:{pod_norm}'
    raw = f"{_clean(nominativo).upper()}|{_clean(comune).upper()}"
    digest = hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16]
    return f'NOPOD:{digest}'


def is_pec(email):
    email = _clean(email).lower()
    return bool(
        email
        and (
            _PEC_RE.search(email)
            or email.endswith('.pec.it')
            or email.endswith('@pec.it')
        )
    )


def split_emails(campo):
    """Splitta il campo email unico in indirizzi singoli."""
    if not campo:
        return []
    out = []
    visti = set()
    for item in re.split(r'[\/,;\s]+', str(campo)):
        email = item.strip()
        if '@' not in email:
            continue
        key = email.lower()
        if key in visti:
            continue
        visti.add(key)
        out.append({'email': email, 'pec': is_pec(email)})
    return out


def _column_name_for_internal(conn, chiave_interna, fallback=None):
    row = conn.execute(
        '''
        SELECT column_name
        FROM column_registry
        WHERE chiave_interna=?
        ORDER BY last_seen_ts DESC, last_index DESC
        LIMIT 1
        ''',
        (chiave_interna,),
    ).fetchone()
    if row and row['column_name']:
        return row['column_name']
    if fallback is not None:
        return fallback
    if chiave_interna == 'note_appuntamento':
        try:
            from moduli.shared import _get_colonna_note
            return _get_colonna_note()
        except Exception:
            return 'note_appuntamento'
    return chiave_interna


def _get_disabled_lcls(provincia=None):
    """Ritorna l'insieme di codici LCL disabilitati per la provincia specificata."""
    prov = (provincia or _provincia_attiva()).upper()
    try:
        import config
        prov_dict = getattr(config, 'PROVINCE', {}).get(prov, {})
        dis = prov_dict.get('lcl_disabilitati')
        if dis is None and prov == getattr(config, 'PROVINCIA_ATTIVA', '').upper():
            dis = getattr(config, 'LCL_DISABILITATI', [])
        return {str(x).strip().upper() for x in (dis or []) if str(x).strip()}
    except Exception:
        return set()


def _sync_contatto_su_filone(pod, telefono=None, email=None, note=None, user=None, lcl=None, provincia=None, eneltel=None):
    """Aggiorna telefono/email/note sul filone locale se il POD o l'Eneltel esiste."""
    disabled = _get_disabled_lcls(provincia)
    found = None
    if pod:
        found = db.find_filone_row_by_pod(pod, lcl=lcl, disabled_lcls=disabled)
    if not found and eneltel:
        found = db.find_filone_row_by_eneltel(eneltel, lcl=lcl, disabled_lcls=disabled)
    if not found:
        return {'synced': False, 'reason': 'pod_or_eneltel_not_found'}

    resolved_eneltel, resolved_lcl = found
    conn = db.get_conn()
    fields = {}
    if telefono is not None:
        fields[_column_name_for_internal(conn, 'telefono', 'telefono')] = telefono
    if email is not None:
        fields[_column_name_for_internal(conn, 'email', 'email')] = email
    if note is not None:
        col_note = _column_name_for_internal(conn, 'note_appuntamento', None)
        fields[col_note] = note
    if not fields:
        return {'synced': False, 'reason': 'empty_fields'}

    db.record_local_change(
        resolved_eneltel,
        fields,
        user=user,
        origin='lettere_contatti',
        lcl=resolved_lcl,
    )
    return {'synced': True, 'eneltel': resolved_eneltel, 'lcl': resolved_lcl, 'fields': list(fields)}


def _reconcile_notes(ag_note, filone_note):
    """Riconcilia la nota di ag con filone: filone è la fonte di verità."""
    ag_n = (ag_note or '').strip()
    f_n = (filone_note or '').strip()
    if not ag_n:
        return f_n
    if not f_n:
        return ag_n
    if ag_n == f_n or ag_n in f_n:
        return f_n
    if f_n in ag_n:
        return ag_n
    return f"{f_n} // {ag_n}"



def registra_generazione(
    file_path,
    periodo_da,
    periodo_a,
    righe: Iterable[dict],
    *,
    provincia=None,
    user=None,
    note='',
):
    """Registra nel DB una generazione Excel lettere e le aziende rilevate."""
    righe = list(righe or [])
    provincia = (provincia or _provincia_attiva()).upper()
    file_name = os.path.basename(file_path or '')
    file_hash = _sha256_file(file_path) if file_path and os.path.exists(file_path) else None
    now = _now_ts()

    conn = db.get_conn()
    with conn:
        cur = conn.execute(
            '''
            INSERT OR IGNORE INTO lettere_generazioni(
                provincia, file_name, file_path, file_hash, periodo_da, periodo_a,
                created_ts, created_by, totale_righe, totale_aziende, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?)
            ''',
            (
                provincia, file_name, _relpath(file_path), file_hash,
                _clean(periodo_da), _clean(periodo_a), now, user, note,
            ),
        )
        if cur.lastrowid:
            generazione_id = cur.lastrowid
        else:
            row = conn.execute(
                '''
                SELECT id
                FROM lettere_generazioni
                WHERE provincia=? AND file_hash=?
                ''',
                (provincia, file_hash),
            ).fetchone()
            generazione_id = row['id']
            gia_popolata = conn.execute(
                'SELECT COUNT(*) AS c FROM lettere_righe WHERE generazione_id=?',
                (generazione_id,),
            ).fetchone()['c']
            if gia_popolata:
                return {
                    'generazione_id': generazione_id,
                    'file_name': file_name,
                    'totale_righe': gia_popolata,
                    'totale_aziende': conn.execute(
                        '''
                        SELECT COUNT(*) AS c
                        FROM lettere_aziende_generazione
                        WHERE generazione_id=?
                        ''',
                        (generazione_id,),
                    ).fetchone()['c'],
                    'already_present': True,
                }

        aziende_viste = set()
        totale_aziende = 0

        for idx, r in enumerate(righe, start=1):
            raw_json = json.dumps(r, ensure_ascii=False, default=str)
            is_azienda_row = 1 if r.get('is_azienda') else 0
            pod = _clean(r.get('pod')).upper()
            nominativo = _clean(r.get('nominativo'))
            comune = _clean(r.get('comune'))
            ck = contact_key(pod, nominativo, comune)

            conn.execute(
                '''
                INSERT INTO lettere_righe(
                    generazione_id, source_sheet, row_num, pod, eneltel, lcl,
                    nominativo, via, civico, comune, provincia_sigla, cap,
                    data_pianificazione, ora_pianificazione, lettera, operatore,
                    posizione_mis, appuntamento, tipo_misuratore, telefono, email,
                    is_azienda, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    generazione_id,
                    _clean(r.get('source_sheet')),
                    int(r.get('row_num') or idx),
                    pod,
                    _clean(r.get('eneltel')),
                    _clean(r.get('lcl')),
                    nominativo,
                    _clean(r.get('via')),
                    _clean(r.get('civico')),
                    comune,
                    _clean(r.get('provincia')),
                    _clean(r.get('cap')),
                    _clean(r.get('data_pianificazione')),
                    _clean(r.get('ora_pianificazione')),
                    _clean(r.get('lettera')),
                    _clean(r.get('operatore')),
                    _clean(r.get('posizione_mis')),
                    _clean(r.get('appuntamento')),
                    _clean(r.get('tipo_misuratore')),
                    _clean(r.get('telefono')),
                    _clean(r.get('email')),
                    is_azienda_row,
                    raw_json,
                ),
            )

            if not is_azienda_row or ck in aziende_viste:
                continue
            aziende_viste.add(ck)
            totale_aziende += 1

            telefono = _clean(r.get('telefono'))
            email = _clean(r.get('email'))
            conn.execute(
                '''
                INSERT INTO lettere_contatti(
                    provincia, contact_key, pod, nominativo, comune, telefono, email,
                    created_ts, updated_ts, updated_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provincia, contact_key) DO UPDATE SET
                    pod=CASE
                        WHEN TRIM(COALESCE(lettere_contatti.pod, ''))=''
                        THEN excluded.pod ELSE lettere_contatti.pod END,
                    nominativo=excluded.nominativo,
                    comune=excluded.comune,
                    telefono=CASE
                        WHEN TRIM(COALESCE(lettere_contatti.telefono, ''))=''
                        THEN excluded.telefono ELSE lettere_contatti.telefono END,
                    email=CASE
                        WHEN TRIM(COALESCE(lettere_contatti.email, ''))=''
                        THEN excluded.email ELSE lettere_contatti.email END,
                    updated_ts=excluded.updated_ts
                ''',
                (
                    provincia, ck, pod, nominativo, comune, telefono, email,
                    now, now, user,
                ),
            )
            conn.execute(
                '''
                INSERT INTO lettere_aziende_generazione(
                    generazione_id, provincia, contact_key, pod, nominativo, comune,
                    data_pianificazione, ora_pianificazione, operatore,
                    tipo_misuratore, lettera, created_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(generazione_id, contact_key) DO UPDATE SET
                    pod=excluded.pod,
                    nominativo=excluded.nominativo,
                    comune=excluded.comune,
                    data_pianificazione=excluded.data_pianificazione,
                    ora_pianificazione=excluded.ora_pianificazione,
                    operatore=excluded.operatore,
                    tipo_misuratore=excluded.tipo_misuratore,
                    lettera=excluded.lettera
                ''',
                (
                    generazione_id, provincia, ck, pod, nominativo, comune,
                    _clean(r.get('data_pianificazione')),
                    _clean(r.get('ora_pianificazione')),
                    _clean(r.get('operatore')),
                    _clean(r.get('tipo_misuratore')),
                    _clean(r.get('lettera')),
                    now,
                ),
            )

        conn.execute(
            '''
            UPDATE lettere_generazioni
            SET totale_righe=?, totale_aziende=?
            WHERE id=?
            ''',
            (len(righe), totale_aziende, generazione_id),
        )

    return {
        'generazione_id': generazione_id,
        'file_name': file_name,
        'totale_righe': len(righe),
        'totale_aziende': totale_aziende,
        'already_present': False,
    }


def get_generazione_by_file(file_name, provincia=None):
    provincia = (provincia or _provincia_attiva()).upper()
    conn = db.get_conn()
    row = conn.execute(
        '''
        SELECT *
        FROM lettere_generazioni
        WHERE provincia=? AND file_name=?
        ORDER BY id DESC
        LIMIT 1
        ''',
        (provincia, os.path.basename(file_name or '')),
    ).fetchone()
    return dict(row) if row else None


def list_generazioni(limit=100, provincia=None):
    """Lista generazioni, deduplicate per periodo.

    Se lo stesso periodo (periodo_da, periodo_a) è stato generato più volte,
    restituisce solo la generazione più recente (MAX id).
    """
    provincia = (provincia or _provincia_attiva()).upper()
    conn = db.get_conn()
    rows = conn.execute(
        '''
        SELECT g.*
        FROM lettere_generazioni g
        INNER JOIN (
            SELECT MAX(id) AS max_id
            FROM lettere_generazioni
            WHERE provincia=?
            GROUP BY provincia,
                     COALESCE(periodo_da, ''),
                     COALESCE(periodo_a, '')
        ) latest ON g.id = latest.max_id
        ORDER BY
            COALESCE(NULLIF(g.periodo_a, ''), NULLIF(g.periodo_da, ''), '0000-00-00') DESC,
            COALESCE(NULLIF(g.periodo_da, ''), '0000-00-00') DESC,
            g.id DESC
        LIMIT ?
        ''',
        (provincia, int(limit)),
    ).fetchall()
    return [dict(r) for r in rows]


def get_generazione(generazione_id, provincia=None):
    provincia = (provincia or _provincia_attiva()).upper()
    conn = db.get_conn()
    gen = conn.execute(
        'SELECT * FROM lettere_generazioni WHERE id=? AND provincia=?',
        (int(generazione_id), provincia),
    ).fetchone()
    if not gen:
        return None
    righe = conn.execute(
        '''
        SELECT *
        FROM lettere_righe
        WHERE generazione_id=?
        ORDER BY id
        ''',
        (int(generazione_id),),
    ).fetchall()
    out = dict(gen)
    out['righe'] = [dict(r) for r in righe]
    return out


def _get_lettere_blacklist(provincia=None):
    """Restituisce la lista di parole chiave blacklistate per la provincia (es. COMUNE, PROVINCIA, CONDOMINIO)."""
    prov = (provincia or _provincia_attiva()).upper()
    keywords = ['CONDOMINIO', 'COND.', 'COND ']
    try:
        settings_file = os.path.join('data', prov.lower(), 'settings.json')
        if os.path.exists(settings_file):
            with open(settings_file, 'r', encoding='utf-8') as f:
                user_settings = json.load(f)
                lettere_cfg = user_settings.get('lettere_excel', {})
                cfg_kw = lettere_cfg.get('parole_chiave', {}).get('lettera_no', [])
                if cfg_kw:
                    keywords.extend(cfg_kw)
        if len(keywords) == 3:
            from config import LETTERE_EXCEL_CONFIG
            cfg_kw = LETTERE_EXCEL_CONFIG.get('parole_chiave', {}).get('lettera_no', [])
            if cfg_kw:
                keywords.extend(cfg_kw)
    except Exception:
        keywords.extend(['COMUNE', 'PROVINCIA'])

    seen = set()
    out = []
    for k in keywords:
        k_clean = str(k).strip().upper()
        if k_clean and k_clean not in seen:
            seen.add(k_clean)
            out.append(k_clean)
    return out


def list_aziende(generazione_id=None, search='', limit=500, provincia=None):
    provincia = (provincia or _provincia_attiva()).upper()
    conn = db.get_conn()
    where = ['ag.provincia=?']
    params = [provincia]

    # Escludi Comuni, Enti e parole chiave blacklistate per la provincia
    blacklist = _get_lettere_blacklist(provincia)
    for kw in blacklist:
        where.append("UPPER(COALESCE(ag.nominativo, '')) NOT LIKE ?")
        params.append(f'%{kw}%')

    if generazione_id:
        where.append('ag.generazione_id=?')
        params.append(int(generazione_id))
    q = _clean(search)
    if q:
        like = f'%{q.lower()}%'
        where.append(
            '''
            (
                LOWER(COALESCE(ag.nominativo, '')) LIKE ?
                OR LOWER(COALESCE(ag.pod, '')) LIKE ?
                OR LOWER(COALESCE(ag.comune, '')) LIKE ?
                OR LOWER(COALESCE(c.email, '')) LIKE ?
            )
            '''
        )
        params.extend([like, like, like, like])
    disabled_lcls = _get_disabled_lcls(provincia)
    dis_sql = ', '.join(f"'{re.sub(r'[^A-Za-z0-9_-]', '', x)}'" for x in disabled_lcls if x) or "''"

    params.append(int(limit))
    rows = conn.execute(
        f'''
        WITH righe_pod AS (
            SELECT
                generazione_id,
                UPPER(TRIM(pod)) AS pod_norm,
                MIN(id) AS row_id
            FROM lettere_righe
            WHERE TRIM(COALESCE(pod, ''))!=''
            GROUP BY generazione_id, UPPER(TRIM(pod))
        ),
        righe_senza_pod AS (
            SELECT
                generazione_id,
                UPPER(TRIM(COALESCE(nominativo, ''))) AS nominativo_norm,
                UPPER(TRIM(COALESCE(comune, ''))) AS comune_norm,
                MIN(id) AS row_id
            FROM lettere_righe
            WHERE TRIM(COALESCE(pod, ''))=''
            GROUP BY
                generazione_id,
                UPPER(TRIM(COALESCE(nominativo, ''))),
                UPPER(TRIM(COALESCE(comune, '')))
        ),
        invii_per_azienda AS (
            SELECT
                azienda_generazione_id,
                SUM(CASE WHEN status='sent' THEN 1 ELSE 0 END) AS invii_ok,
                MAX(CASE WHEN status='sent' THEN sent_ts END) AS last_sent_ts
            FROM lettere_invii
            WHERE azienda_generazione_id IS NOT NULL
            GROUP BY azienda_generazione_id
        ),
        invii_per_contatto AS (
            SELECT
                provincia,
                contact_key,
                SUM(CASE WHEN status='sent' THEN 1 ELSE 0 END) AS invii_ok,
                MAX(CASE WHEN status='sent' THEN sent_ts END) AS last_sent_ts
            FROM lettere_invii
            WHERE TRIM(COALESCE(contact_key, ''))!=''
            GROUP BY provincia, contact_key
        )
        SELECT
            ag.*,
            g.file_name,
            g.periodo_da,
            g.periodo_a,
            COALESCE(NULLIF(c.telefono, ''), NULLIF(c_same.telefono, ''), NULLIF(lr.telefono, ''), f.telefono, f_same.telefono) AS telefono,
            COALESCE(NULLIF(c.email, ''), NULLIF(c_same.email, ''), NULLIF(lr.email, ''), f.email, f_same.email) AS email,
            COALESCE(NULLIF(lr.via, ''), NULLIF(f.indirizzo, '')) AS via,
            COALESCE(NULLIF(lr.civico, ''), NULLIF(f.civico, '')) AS civico,
            COALESCE(NULLIF(ag.note, ''), NULLIF(f.note_appuntamento, ''), NULLIF(f_same.note_appuntamento, ''), '') AS note_appuntamento,
            COALESCE(NULLIF(f.note_appuntamento, ''), NULLIF(f_same.note_appuntamento, ''), '') AS note_filone,
            lr.cap,
            lr.eneltel,
            lr.lcl,
            lr.posizione_mis,
            f.latitudine AS latitudine,
            f.longitudine AS longitudine,
            COALESCE(ic.invii_ok, ia.invii_ok, 0) AS invii_ok,
            COALESCE(ia.invii_ok, 0) AS invii_generazione,
            COALESCE(ic.last_sent_ts, ia.last_sent_ts) AS last_sent_ts,
            COALESCE(NULLIF(f.codice_ante, ''), '') AS codice_ante,
            COALESCE(NULLIF(f.matricola_ante, ''), '') AS matricola_ante,
            COALESCE(NULLIF(f.data_pianificata, ''), '') AS data_pian_filone
        FROM lettere_aziende_generazione ag
        JOIN lettere_generazioni g ON g.id=ag.generazione_id
        LEFT JOIN lettere_contatti c
          ON c.provincia=ag.provincia AND c.contact_key=ag.contact_key
        LEFT JOIN (
            SELECT
                provincia,
                UPPER(TRIM(COALESCE(nominativo, ''))) AS nom_norm,
                UPPER(TRIM(COALESCE(comune, ''))) AS com_norm,
                MAX(NULLIF(telefono, '')) AS telefono,
                MAX(NULLIF(email, '')) AS email
            FROM lettere_contatti
            WHERE TRIM(COALESCE(nominativo, ''))!=''
            GROUP BY provincia, UPPER(TRIM(COALESCE(nominativo, ''))), UPPER(TRIM(COALESCE(comune, '')))
        ) c_same ON c_same.provincia=ag.provincia
                AND c_same.nom_norm=UPPER(TRIM(COALESCE(ag.nominativo, '')))
                AND (c_same.com_norm=UPPER(TRIM(COALESCE(ag.comune, ''))) OR c_same.com_norm='' OR TRIM(COALESCE(ag.comune, ''))='')
        LEFT JOIN righe_pod rp
          ON rp.generazione_id=ag.generazione_id
         AND rp.pod_norm=UPPER(TRIM(COALESCE(ag.pod, '')))
        LEFT JOIN righe_senza_pod rn
          ON TRIM(COALESCE(ag.pod, ''))=''
         AND rn.generazione_id=ag.generazione_id
         AND rn.nominativo_norm=UPPER(TRIM(COALESCE(ag.nominativo, '')))
         AND rn.comune_norm=UPPER(TRIM(COALESCE(ag.comune, '')))
        LEFT JOIN lettere_righe lr ON lr.id=COALESCE(rp.row_id, rn.row_id)
        LEFT JOIN filone f ON f.rowid = COALESCE(
            (
                SELECT f1.rowid FROM filone f1
                WHERE f1.pod = UPPER(TRIM(COALESCE(ag.pod, lr.pod, '')))
                  AND f1.lcl = lr.lcl
                  AND TRIM(COALESCE(lr.lcl, '')) != ''
                ORDER BY f1.sheets_row DESC
                LIMIT 1
            ),
            (
                SELECT fe1.rowid FROM filone fe1
                WHERE fe1.eneltel_key = LTRIM(COALESCE(lr.eneltel, ''), '0')
                  AND fe1.lcl = lr.lcl
                  AND TRIM(COALESCE(lr.lcl, '')) != ''
                  AND TRIM(COALESCE(lr.eneltel, '')) != ''
                ORDER BY fe1.sheets_row DESC
                LIMIT 1
            ),
            (
                SELECT f2.rowid FROM filone f2
                WHERE f2.pod = UPPER(TRIM(COALESCE(ag.pod, lr.pod, '')))
                  AND UPPER(TRIM(f2.lcl)) NOT IN ({dis_sql})
                ORDER BY f2.sheets_row DESC
                LIMIT 1
            ),
            (
                SELECT fe2.rowid FROM filone fe2
                WHERE fe2.eneltel_key = LTRIM(COALESCE(lr.eneltel, ''), '0')
                  AND TRIM(COALESCE(lr.eneltel, '')) != ''
                  AND UPPER(TRIM(fe2.lcl)) NOT IN ({dis_sql})
                ORDER BY fe2.sheets_row DESC
                LIMIT 1
            ),
            (
                SELECT f3.rowid FROM filone f3
                WHERE f3.pod = UPPER(TRIM(COALESCE(ag.pod, lr.pod, '')))
                ORDER BY f3.sheets_row DESC
                LIMIT 1
            )
        )
        LEFT JOIN (
            SELECT
                UPPER(TRIM(COALESCE(nominativo, ''))) AS nom_norm,
                UPPER(TRIM(COALESCE(comune, ''))) AS com_norm,
                MAX(NULLIF(telefono, '')) AS telefono,
                MAX(NULLIF(email, '')) AS email,
                MAX(NULLIF(note_appuntamento, '')) AS note_appuntamento,
                MAX(NULLIF(codice_ante, '')) AS codice_ante,
                MAX(NULLIF(matricola_ante, '')) AS matricola_ante,
                MAX(NULLIF(data_pianificata, '')) AS data_pianificata
            FROM filone
            WHERE TRIM(COALESCE(nominativo, ''))!=''
            GROUP BY 1, 2
        ) f_same ON f_same.nom_norm=UPPER(TRIM(COALESCE(ag.nominativo, '')))
                AND (f_same.com_norm=UPPER(TRIM(COALESCE(ag.comune, ''))) OR f_same.com_norm='' OR TRIM(COALESCE(ag.comune, ''))='')
        LEFT JOIN invii_per_azienda ia ON ia.azienda_generazione_id=ag.id
        LEFT JOIN invii_per_contatto ic
          ON ic.provincia=ag.provincia AND ic.contact_key=ag.contact_key
        WHERE {' AND '.join(where)}
        ORDER BY
            COALESCE(NULLIF(g.periodo_a, ''), NULLIF(g.periodo_da, ''), '0000-00-00') DESC,
            g.periodo_da DESC,
            ag.nominativo COLLATE NOCASE
        LIMIT ?
        ''',
        params,
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        eff_note = _reconcile_notes(d.get('note'), d.get('note_filone'))
        d['note'] = eff_note
        d['note_appuntamento'] = eff_note
        d['note_filone'] = eff_note
        out.append(d)
    return out


def sincronizza_contatti_da_filone(*, provincia=None, user='sync_filone'):
    """Completa i recapiti vuoti dell'archivio usando il filone locale.

    Non sovrascrive mai valori già compilati nell'Archivio Lettere.
    """
    provincia = (provincia or _provincia_attiva()).upper()
    conn = db.get_conn()
    filone_rows = conn.execute(
        '''
        SELECT pod, telefono, email
        FROM filone
        WHERE TRIM(COALESCE(pod, ''))!=''
        ORDER BY sheets_row
        '''
    ).fetchall()

    recapiti = {}
    for row in filone_rows:
        pod = _clean(row['pod']).upper()
        if not pod:
            continue
        current = recapiti.setdefault(pod, {'telefono': '', 'email': ''})
        telefono = _clean(row['telefono'])
        email = _clean(row['email'])
        if not current['telefono'] and any(ch.isdigit() for ch in telefono):
            current['telefono'] = telefono
        if not current['email'] and '@' in email:
            current['email'] = email

    contatti = conn.execute(
        '''
        SELECT contact_key, pod, telefono, email
        FROM lettere_contatti
        WHERE provincia=? AND TRIM(COALESCE(pod, ''))!=''
        ''',
        (provincia,),
    ).fetchall()
    now = _now_ts()
    updates = []
    telefono_compilati = 0
    email_compilate = 0
    for contatto in contatti:
        source = recapiti.get(_clean(contatto['pod']).upper())
        if not source:
            continue
        telefono = _clean(contatto['telefono'])
        email = _clean(contatto['email'])
        nuovo_telefono = telefono or source['telefono']
        nuova_email = email or source['email']
        if nuovo_telefono == telefono and nuova_email == email:
            continue
        telefono_compilati += int(not telefono and bool(nuovo_telefono))
        email_compilate += int(not email and bool(nuova_email))
        updates.append((
            nuovo_telefono,
            nuova_email,
            now,
            user,
            provincia,
            contatto['contact_key'],
        ))

    if updates:
        with conn:
            conn.executemany(
                '''
                UPDATE lettere_contatti
                SET telefono=?, email=?, updated_ts=?, updated_by=?
                WHERE provincia=? AND contact_key=?
                ''',
                updates,
            )
    return {
        'contatti_aggiornati': len(updates),
        'telefoni_compilati': telefono_compilati,
        'email_compilate': email_compilate,
    }


def update_contatto(contact_key_value, *, telefono=None, email=None, user=None, provincia=None):
    provincia = (provincia or _provincia_attiva()).upper()
    ck = _clean(contact_key_value)
    if not ck:
        raise ValueError('contact_key mancante')

    conn = db.get_conn()
    now = _now_ts()
    with conn:
        row = conn.execute(
            '''
            SELECT *
            FROM lettere_contatti
            WHERE provincia=? AND contact_key=?
            ''',
            (provincia, ck),
        ).fetchone()
        if not row:
            raise LookupError('Contatto non trovato')

        updates = ['updated_ts=?', 'updated_by=?']
        params = [now, user]
        if telefono is not None:
            updates.append('telefono=?')
            params.append(_clean(telefono))
        if email is not None:
            updates.append('email=?')
            params.append(_clean(email))
        params.extend([provincia, ck])
        conn.execute(
            f'''
            UPDATE lettere_contatti
            SET {', '.join(updates)}
            WHERE provincia=? AND contact_key=?
            ''',
            params,
        )

        # Propaga anche agli altri contatori della stessa azienda nello stesso comune
        nom = _clean(row['nominativo']).upper()
        com = _clean(row['comune']).upper()
        if nom:
            p_prop = [now, user]
            u_prop = ['updated_ts=?', 'updated_by=?']
            if telefono is not None:
                u_prop.append('telefono=?')
                p_prop.append(_clean(telefono))
            if email is not None:
                u_prop.append('email=?')
                p_prop.append(_clean(email))
            p_prop.extend([provincia, nom])
            where_extra = 'AND UPPER(TRIM(COALESCE(comune, ""))) = ?' if com else ''
            if com:
                p_prop.append(com)
            conn.execute(
                f'''
                UPDATE lettere_contatti
                SET {', '.join(u_prop)}
                WHERE provincia=? AND UPPER(TRIM(COALESCE(nominativo, '')))=? {where_extra}
                ''',
                p_prop,
            )

        pod = row['pod'] or ''

    sync = {'synced': False, 'reason': 'no_pod'}
    if pod:
        sync = _sync_contatto_su_filone(pod, telefono=telefono, email=email, user=user)

    # Sincronizza anche gli altri POD della stessa azienda/comune
    if nom:
        altri = conn.execute(
            '''
            SELECT pod FROM lettere_contatti
            WHERE provincia=? AND UPPER(TRIM(COALESCE(nominativo, '')))=? AND pod!=?
            ''',
            (provincia, nom, pod),
        ).fetchall()
        for a_row in altri:
            p_pod = a_row['pod'] or ''
            if p_pod:
                _sync_contatto_su_filone(p_pod, telefono=telefono, email=email, user=user)

    updated = conn.execute(
        '''
        SELECT *
        FROM lettere_contatti
        WHERE provincia=? AND contact_key=?
        ''',
        (provincia, ck),
    ).fetchone()
    out = dict(updated)
    out['sync_filone'] = sync
    return out


def update_contatti_by_nominativo(nominativo, comune=None, *, telefono=None, email=None, user=None, provincia=None):
    """Aggiorna telefono/email per TUTTI i contatti con lo stesso nominativo (e comune se specificato).

    Utile quando la stessa azienda compare con più contatori ma i recapiti
    sono identici. Evita di dover reinserire i dati per ogni entry.
    """
    provincia = (provincia or _provincia_attiva()).upper()
    nom = _clean(nominativo).upper()
    if not nom:
        raise ValueError('nominativo mancante')

    conn = db.get_conn()
    where = ['provincia=?', 'UPPER(TRIM(COALESCE(nominativo, "")))=?']
    params = [provincia, nom]
    if comune:
        com = _clean(comune).upper()
        if com:
            where.append('UPPER(TRIM(COALESCE(comune, "")))=?')
            params.append(com)

    rows = conn.execute(
        f'''
        SELECT contact_key, pod
        FROM lettere_contatti
        WHERE {' AND '.join(where)}
        ''',
        params,
    ).fetchall()

    if not rows:
        raise LookupError('Nessun contatto trovato con questo nominativo')

    now = _now_ts()
    updated_keys = []
    synced_any = False

    with conn:
        for row in rows:
            ck = row['contact_key']
            updates = ['updated_ts=?', 'updated_by=?']
            p_update = [now, user]
            if telefono is not None:
                updates.append('telefono=?')
                p_update.append(_clean(telefono))
            if email is not None:
                updates.append('email=?')
                p_update.append(_clean(email))
            p_update.extend([provincia, ck])
            conn.execute(
                f'''
                UPDATE lettere_contatti
                SET {', '.join(updates)}
                WHERE provincia=? AND contact_key=?
                ''',
                p_update,
            )
            updated_keys.append(ck)

    # Sync filone per ogni contatto con POD
    for row in rows:
        pod = row['pod'] or ''
        if pod:
            sync = _sync_contatto_su_filone(pod, telefono=telefono, email=email, user=user)
            if sync.get('synced'):
                synced_any = True

    return {
        'updated_keys': updated_keys,
        'updated_count': len(updated_keys),
        'synced_filone': synced_any,
    }


def set_risposta(azienda_id, risposta, note=None, user=None, provincia=None, lcl=None):
    provincia = (provincia or _provincia_attiva()).upper()
    conn = db.get_conn()
    row = conn.execute(
        '''
        SELECT ag.id, ag.pod, ag.generazione_id, ag.contact_key, ag.nominativo, ag.comune, lr.lcl, lr.eneltel
        FROM lettere_aziende_generazione ag
        LEFT JOIN lettere_righe lr ON lr.generazione_id=ag.generazione_id AND lr.pod=ag.pod AND TRIM(COALESCE(ag.pod, ''))!=''
        WHERE ag.id=? AND ag.provincia=?
        LIMIT 1
        ''',
        (int(azienda_id), provincia),
    ).fetchone()
    if not row:
        raise LookupError('Azienda generazione non trovata')
    pod = row['pod'] if row else None
    gen_id = row['generazione_id']
    contact_key_val = row['contact_key']
    nominativo_val = row['nominativo']
    comune_val = row['comune']
    resolved_lcl = (str(lcl).strip() if lcl else '') or (row['lcl'] if row else '')
    now = _now_ts()

    with conn:
        ag_where = "id=?"
        ag_params = [int(azienda_id)]
        if nominativo_val:
            ag_where = "id=? OR (generazione_id=? AND UPPER(TRIM(nominativo))=UPPER(TRIM(?)) AND (UPPER(TRIM(comune))=UPPER(TRIM(?)) OR TRIM(COALESCE(comune, ''))=''))"
            ag_params = [int(azienda_id), gen_id, nominativo_val, comune_val or '']
        elif contact_key_val:
            ag_where = "id=? OR (generazione_id=? AND contact_key=?)"
            ag_params = [int(azienda_id), gen_id, contact_key_val]

        if risposta is not None and note is not None:
            risposta_val = 1 if risposta else 0
            conn.execute(
                f'''
                UPDATE lettere_aziende_generazione
                SET risposta=?, risposta_ts=?, risposta_by=?, note=?
                WHERE {ag_where}
                ''',
                [risposta_val, now if risposta_val else None, user if risposta_val else None, note] + ag_params,
            )
        elif risposta is not None:
            risposta_val = 1 if risposta else 0
            conn.execute(
                f'''
                UPDATE lettere_aziende_generazione
                SET risposta=?, risposta_ts=?, risposta_by=?
                WHERE {ag_where}
                ''',
                [risposta_val, now if risposta_val else None, user if risposta_val else None] + ag_params,
            )
        elif note is not None:
            conn.execute(
                f'''
                UPDATE lettere_aziende_generazione
                SET note=?
                WHERE {ag_where}
                ''',
                [note] + ag_params,
            )

    if note is not None:
        meters = []
        if nominativo_val:
            meters = conn.execute(
                '''
                SELECT DISTINCT pod, eneltel, lcl
                FROM lettere_righe
                WHERE generazione_id=?
                  AND UPPER(TRIM(nominativo))=UPPER(TRIM(?))
                  AND (UPPER(TRIM(comune))=UPPER(TRIM(?)) OR TRIM(COALESCE(comune, ''))='')
                ''',
                (gen_id, nominativo_val, comune_val or ''),
            ).fetchall()
        if not meters and pod:
            meters = conn.execute(
                '''
                SELECT DISTINCT pod, eneltel, lcl
                FROM lettere_righe
                WHERE generazione_id=? AND UPPER(TRIM(pod))=UPPER(TRIM(?))
                ''',
                (gen_id, pod),
            ).fetchall()
        if not meters:
            meters = [{'pod': pod, 'eneltel': row['eneltel'], 'lcl': resolved_lcl}]

        for m in meters:
            m_pod = m['pod'] or pod
            m_eneltel = m['eneltel']
            m_lcl = m['lcl'] or resolved_lcl
            try:
                _sync_contatto_su_filone(m_pod, note=note, user=user, lcl=m_lcl, provincia=provincia, eneltel=m_eneltel)
            except Exception as ex:
                logger.warning(f"[LETTERE] Errore sync nota su filone per POD {m_pod} / Eneltel {m_eneltel}: {ex}")

    return {
        'id': int(azienda_id),
        'risposta': bool(risposta) if risposta is not None else None,
        'note': note,
    }


def get_azienda(azienda_id, provincia=None):
    provincia = (provincia or _provincia_attiva()).upper()
    disabled_lcls = _get_disabled_lcls(provincia)
    dis_sql = ', '.join(f"'{re.sub(r'[^A-Za-z0-9_-]', '', x)}'" for x in disabled_lcls if x) or "''"
    conn = db.get_conn()
    row = conn.execute(
        f'''
        SELECT
            ag.*,
            g.file_name,
            g.periodo_da,
            g.periodo_a,
            COALESCE(NULLIF(c.telefono, ''), NULLIF(lr.telefono, ''), f.telefono, f_same.telefono) AS telefono,
            COALESCE(NULLIF(c.email, ''), NULLIF(lr.email, ''), f.email, f_same.email) AS email,
            lr.via,
            lr.civico,
            lr.cap,
            lr.eneltel,
            lr.lcl,
            lr.posizione_mis,
            COALESCE(NULLIF(f.codice_ante, ''), NULLIF(f_same.codice_ante, ''), '') AS codice_ante,
            COALESCE(NULLIF(f.matricola_ante, ''), NULLIF(f_same.matricola_ante, ''), '') AS matricola_ante,
            COALESCE(NULLIF(ag.note, ''), NULLIF(f.note_appuntamento, ''), NULLIF(f_same.note_appuntamento, ''), '') AS note_appuntamento,
            COALESCE(NULLIF(f.note_appuntamento, ''), NULLIF(f_same.note_appuntamento, ''), '') AS note_filone,
            COALESCE(NULLIF(f.data_pianificata, ''), NULLIF(f_same.data_pianificata, ''), '') AS data_pian_filone
        FROM lettere_aziende_generazione ag
        JOIN lettere_generazioni g ON g.id=ag.generazione_id
        LEFT JOIN lettere_contatti c
          ON c.provincia=ag.provincia AND c.contact_key=ag.contact_key
        LEFT JOIN lettere_righe lr ON lr.id=(
            SELECT lr2.id
            FROM lettere_righe lr2
            WHERE lr2.generazione_id=ag.generazione_id
              AND (
                  (
                      TRIM(COALESCE(ag.pod, ''))!=''
                      AND UPPER(TRIM(COALESCE(lr2.pod, '')))=UPPER(TRIM(ag.pod))
                  )
                  OR (
                      TRIM(COALESCE(ag.pod, ''))=''
                      AND UPPER(TRIM(COALESCE(lr2.nominativo, '')))=
                          UPPER(TRIM(COALESCE(ag.nominativo, '')))
                      AND UPPER(TRIM(COALESCE(lr2.comune, '')))=
                          UPPER(TRIM(COALESCE(ag.comune, '')))
                  )
              )
            ORDER BY lr2.id
            LIMIT 1
        )
        LEFT JOIN filone f ON f.rowid = COALESCE(
            (
                SELECT f1.rowid FROM filone f1
                WHERE f1.pod = UPPER(TRIM(COALESCE(ag.pod, lr.pod, '')))
                  AND f1.lcl = lr.lcl
                  AND TRIM(COALESCE(lr.lcl, '')) != ''
                ORDER BY f1.sheets_row DESC
                LIMIT 1
            ),
            (
                SELECT fe1.rowid FROM filone fe1
                WHERE fe1.eneltel_key = LTRIM(COALESCE(lr.eneltel, ''), '0')
                  AND fe1.lcl = lr.lcl
                  AND TRIM(COALESCE(lr.lcl, '')) != ''
                  AND TRIM(COALESCE(lr.eneltel, '')) != ''
                ORDER BY fe1.sheets_row DESC
                LIMIT 1
            ),
            (
                SELECT f2.rowid FROM filone f2
                WHERE f2.pod = UPPER(TRIM(COALESCE(ag.pod, lr.pod, '')))
                  AND UPPER(TRIM(f2.lcl)) NOT IN ({dis_sql})
                ORDER BY f2.sheets_row DESC
                LIMIT 1
            ),
            (
                SELECT fe2.rowid FROM filone fe2
                WHERE fe2.eneltel_key = LTRIM(COALESCE(lr.eneltel, ''), '0')
                  AND TRIM(COALESCE(lr.eneltel, '')) != ''
                  AND UPPER(TRIM(fe2.lcl)) NOT IN ({dis_sql})
                ORDER BY fe2.sheets_row DESC
                LIMIT 1
            ),
            (
                SELECT f3.rowid FROM filone f3
                WHERE f3.pod = UPPER(TRIM(COALESCE(ag.pod, lr.pod, '')))
                ORDER BY f3.sheets_row DESC
                LIMIT 1
            )
        )
        LEFT JOIN (
            SELECT
                UPPER(TRIM(COALESCE(nominativo, ''))) AS nom_norm,
                UPPER(TRIM(COALESCE(comune, ''))) AS com_norm,
                MAX(NULLIF(telefono, '')) AS telefono,
                MAX(NULLIF(email, '')) AS email,
                MAX(NULLIF(note_appuntamento, '')) AS note_appuntamento,
                MAX(NULLIF(codice_ante, '')) AS codice_ante,
                MAX(NULLIF(matricola_ante, '')) AS matricola_ante,
                MAX(NULLIF(data_pianificata, '')) AS data_pianificata
            FROM filone
            WHERE TRIM(COALESCE(nominativo, ''))!=''
            GROUP BY 1, 2
        ) f_same ON f_same.nom_norm=UPPER(TRIM(COALESCE(ag.nominativo, '')))
                AND (f_same.com_norm=UPPER(TRIM(COALESCE(ag.comune, ''))) OR f_same.com_norm='' OR TRIM(COALESCE(ag.comune, ''))='')
        WHERE ag.id=? AND ag.provincia=?
        ''',
        (int(azienda_id), provincia),
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    eff_note = _reconcile_notes(d.get('note'), d.get('note_filone'))
    d['note'] = eff_note
    d['note_appuntamento'] = eff_note
    d['note_filone'] = eff_note
    return d


def registra_invio(
    *,
    azienda_generazione_id=None,
    generazione_id=None,
    contact_key_value=None,
    pod='',
    email_dest,
    template_id='',
    subject='',
    status,
    error='',
    user=None,
    detail=None,
    sent_ts=None,
    provincia=None,
):
    provincia = (provincia or _provincia_attiva()).upper()
    conn = db.get_conn()
    with conn:
        cur = conn.execute(
            '''
            INSERT INTO lettere_invii(
                azienda_generazione_id, generazione_id, provincia, contact_key,
                pod, email_dest, is_pec, template_id, subject, sent_ts, sent_by,
                status, error, detail_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                azienda_generazione_id,
                generazione_id,
                provincia,
                contact_key_value,
                _clean(pod).upper(),
                _clean(email_dest),
                1 if is_pec(email_dest) else 0,
                _clean(template_id),
                _clean(subject),
                int(sent_ts) if sent_ts is not None else _now_ts(),
                user,
                _clean(status) or 'failed',
                _clean(error),
                json.dumps(detail, ensure_ascii=False, default=str) if detail else None,
            ),
        )
    return cur.lastrowid


def statistiche_feedback(provincia=None):
    provincia = (provincia or _provincia_attiva()).upper()
    conn = db.get_conn()

    def scalar(sql, params=()):
        return conn.execute(sql, params).fetchone()[0] or 0

    totale_aziende = scalar(
        'SELECT COUNT(*) FROM lettere_aziende_generazione WHERE provincia=?',
        (provincia,),
    )
    contattabili = scalar(
        '''
        SELECT COUNT(*)
        FROM lettere_aziende_generazione ag
        JOIN lettere_contatti c
          ON c.provincia=ag.provincia AND c.contact_key=ag.contact_key
        WHERE ag.provincia=?
          AND TRIM(COALESCE(c.email, ''))!=''
        ''',
        (provincia,),
    )
    contattate = scalar(
        '''
        SELECT COUNT(DISTINCT azienda_generazione_id)
        FROM lettere_invii
        WHERE provincia=? AND status='sent' AND azienda_generazione_id IS NOT NULL
        ''',
        (provincia,),
    )
    risposte = scalar(
        '''
        SELECT COUNT(*)
        FROM lettere_aziende_generazione
        WHERE provincia=? AND risposta=1
        ''',
        (provincia,),
    )
    risposte_contattate = scalar(
        '''
        SELECT COUNT(*)
        FROM lettere_aziende_generazione ag
        WHERE ag.provincia=? AND ag.risposta=1
          AND EXISTS (
              SELECT 1
              FROM lettere_invii i
              WHERE i.azienda_generazione_id=ag.id AND i.status='sent'
          )
        ''',
        (provincia,),
    )
    invii_totali = scalar(
        "SELECT COUNT(*) FROM lettere_invii WHERE provincia=? AND status='sent'",
        (provincia,),
    )
    invii_pec = scalar(
        "SELECT COUNT(*) FROM lettere_invii WHERE provincia=? AND status='sent' AND is_pec=1",
        (provincia,),
    )
    generazioni = scalar(
        'SELECT COUNT(*) FROM lettere_generazioni WHERE provincia=?',
        (provincia,),
    )
    totale_privati = scalar(
        '''
        SELECT COUNT(*)
        FROM lettere_righe lr
        JOIN lettere_generazioni g ON g.id=lr.generazione_id
        WHERE g.provincia=? AND COALESCE(lr.is_azienda, 0)=0
        ''',
        (provincia,),
    )

    trend_rows = conn.execute(
        '''
        SELECT
            COALESCE(substr(g.periodo_da, 1, 7), 'N/D') AS mese,
            COUNT(ag.id) AS aziende,
            SUM(CASE WHEN ag.risposta=1 THEN 1 ELSE 0 END) AS risposte
        FROM lettere_aziende_generazione ag
        JOIN lettere_generazioni g ON g.id=ag.generazione_id
        WHERE ag.provincia=?
        GROUP BY mese
        ORDER BY mese
        ''',
        (provincia,),
    ).fetchall()

    return {
        'generazioni': generazioni,
        'aziende': totale_aziende,
        'privati': totale_privati,
        'contattabili': contattabili,
        'contattate': contattate,
        'risposte': risposte,
        'risposte_contattate': risposte_contattate,
        'invii_totali': invii_totali,
        'invii_pec': invii_pec,
        'copertura_contatti': round((contattabili / totale_aziende * 100), 1)
        if totale_aziende else 0,
        'tasso_feedback': round((risposte_contattate / contattate * 100), 1)
        if contattate else 0,
        'trend': [dict(r) for r in trend_rows],
    }


def list_privati(generazione_id=None, search='', limit=2000, provincia=None):
    """Lista utenti privati per le generazioni lettere.

    Estrae le righe con is_azienda = 0 (o COALESCE(is_azienda, 0) = 0),
    arricchite con i dati aggiornati del filone locale (telefono, email,
    note appuntamento, codice/matricola, posizione) e categorizzate per
    tipo di telefono (cellulare, fisso, senza_telefono) tramite classifica_contatti.
    """
    from moduli.distinte_manager import classifica_contatti

    provincia = (provincia or _provincia_attiva()).upper()
    conn = db.get_conn()
    where = ['g.provincia=?', 'COALESCE(lr.is_azienda, 0)=0']
    params = [provincia]

    if generazione_id:
        where.append('lr.generazione_id=?')
        params.append(int(generazione_id))

    q = _clean(search).strip()
    if q:
        like = f'%{q.lower()}%'
        where.append(
            '''
            (
                LOWER(COALESCE(lr.nominativo, '')) LIKE ?
                OR LOWER(COALESCE(lr.pod, '')) LIKE ?
                OR LOWER(COALESCE(lr.comune, '')) LIKE ?
                OR LOWER(COALESCE(lr.via, '')) LIKE ?
                OR LOWER(COALESCE(lr.telefono, '')) LIKE ?
                OR LOWER(COALESCE(f.telefono, '')) LIKE ?
            )
            '''
        )
        params.extend([like, like, like, like, like, like])

    disabled_lcls = _get_disabled_lcls(provincia)
    dis_sql = ', '.join(f"'{re.sub(r'[^A-Za-z0-9_-]', '', x)}'" for x in disabled_lcls if x) or "''"

    params.append(int(limit))

    query = f'''
        SELECT
            lr.*,
            g.file_name,
            g.periodo_da,
            g.periodo_a,
            COALESCE(NULLIF(f.telefono, ''), '') AS telefono_filone,
            COALESCE(NULLIF(f.email, ''), '') AS email_filone,
            COALESCE(NULLIF(f.note_appuntamento, ''), '') AS note_filone,
            COALESCE(NULLIF(f.data_pianificata, ''), '') AS data_pian_filone,
            COALESCE(NULLIF(f.codice_ante, ''), '') AS codice_ante,
            COALESCE(NULLIF(f.matricola_ante, ''), '') AS matricola_ante,
            COALESCE(NULLIF(f.tipo_misuratore, ''), '') AS tipo_misuratore_filone,
            COALESCE(NULLIF(f.posizione_misuratore, ''), '') AS posizione_misuratore_filone,
            COALESCE(NULLIF(c.telefono, ''), NULLIF(lr.telefono, ''), NULLIF(f.telefono, ''), '') AS telefono_effettivo,
            COALESCE(NULLIF(c.email, ''), NULLIF(lr.email, ''), NULLIF(f.email, ''), '') AS email_effettiva,
            COALESCE(NULLIF(lr.via, ''), NULLIF(f.indirizzo, ''), '') AS via_effettiva,
            COALESCE(NULLIF(lr.civico, ''), NULLIF(f.civico, ''), '') AS civico_effettivo
        FROM lettere_righe lr
        JOIN lettere_generazioni g ON g.id=lr.generazione_id
        LEFT JOIN filone f ON f.rowid = COALESCE(
            (
                SELECT f1.rowid FROM filone f1
                WHERE f1.pod = UPPER(TRIM(lr.pod))
                  AND f1.lcl = lr.lcl
                  AND TRIM(COALESCE(lr.lcl, '')) != ''
                ORDER BY f1.sheets_row DESC
                LIMIT 1
            ),
            (
                SELECT fe1.rowid FROM filone fe1
                WHERE fe1.eneltel_key = LTRIM(COALESCE(lr.eneltel, ''), '0')
                  AND fe1.lcl = lr.lcl
                  AND TRIM(COALESCE(lr.lcl, '')) != ''
                  AND TRIM(COALESCE(lr.eneltel, '')) != ''
                ORDER BY fe1.sheets_row DESC
                LIMIT 1
            ),
            (
                SELECT f2.rowid FROM filone f2
                WHERE f2.pod = UPPER(TRIM(lr.pod))
                  AND UPPER(TRIM(f2.lcl)) NOT IN ({dis_sql})
                ORDER BY f2.sheets_row DESC
                LIMIT 1
            ),
            (
                SELECT fe2.rowid FROM filone fe2
                WHERE fe2.eneltel_key = LTRIM(COALESCE(lr.eneltel, ''), '0')
                  AND TRIM(COALESCE(lr.eneltel, '')) != ''
                  AND UPPER(TRIM(fe2.lcl)) NOT IN ({dis_sql})
                ORDER BY fe2.sheets_row DESC
                LIMIT 1
            ),
            (
                SELECT f3.rowid FROM filone f3
                WHERE f3.pod = UPPER(TRIM(lr.pod))
                ORDER BY f3.sheets_row DESC
                LIMIT 1
            )
        )
        LEFT JOIN lettere_contatti c
          ON c.provincia=g.provincia AND c.contact_key='POD:' || UPPER(TRIM(COALESCE(lr.pod, '')))
        WHERE {' AND '.join(where)}
        ORDER BY
            COALESCE(NULLIF(g.periodo_a, ''), NULLIF(g.periodo_da, ''), '0000-00-00') DESC,
            lr.generazione_id DESC,
            lr.row_num ASC,
            lr.id ASC
        LIMIT ?
    '''

    rows = conn.execute(query, params).fetchall()

    lavori = []
    dati_filone = {}
    for r in rows:
        pod = _clean(r['pod']).upper()
        matricola_ante = _clean(r['matricola_ante'])
        codice_ante = _clean(r['codice_ante'])
        matricola_completa = ' / '.join(filter(None, [codice_ante, matricola_ante]))
        tel_filone = _clean(r['telefono_filone'])
        email_filone = _clean(r['email_filone'])
        note_filone = _clean(r['note_filone'])
        nom = _clean(r['nominativo'])
        via = _clean(r['via_effettiva']) or _clean(r['via'])
        civico = _clean(r['civico_effettivo']) or _clean(r['civico'])
        comune = _clean(r['comune'])
        cap = _clean(r['cap'])
        tel = _clean(r['telefono_effettivo']) or tel_filone
        email = _clean(r['email_effettiva']) or email_filone
        tipo_ce = _clean(r['tipo_misuratore_filone']) or _clean(r['tipo_misuratore'])
        pos_ce = _clean(r['posizione_misuratore_filone']) or _clean(r['posizione_mis'])
        data_pian_filone = _clean(r['data_pian_filone'])
        data_lettera = _clean(r['data_pianificazione'])
        data_pian = data_pian_filone or data_lettera

        item = {
            'lavoro': r['row_num'] or len(lavori) + 1,
            'id': r['id'],
            'generazione_id': r['generazione_id'],
            'file_name': r['file_name'],
            'periodo_da': r['periodo_da'],
            'periodo_a': r['periodo_a'],
            'pod': pod,
            'eneltel': _clean(r['eneltel']),
            'lcl': _clean(r['lcl']),
            'nome': nom,
            'nominativo': nom,
            'indirizzo': via,
            'via': via,
            'civico': civico,
            'comune': comune,
            'cap': cap,
            'data_intervento': data_lettera,
            'data_lettera': data_lettera,
            'ora_intervento': _clean(r['ora_pianificazione']),
            'data_pian_filone': data_pian_filone,
            'data_effettiva': data_pian,
            'lettera': _clean(r['lettera']),
            'operatore': _clean(r['operatore']),
            'matricola_operatore': _clean(r['operatore']),
            'posizione_mis': pos_ce,
            'posizione_contatore': pos_ce,
            'appuntamento': _clean(r['appuntamento']),
            'tipologia_contatore': tipo_ce,
            'tipo_misuratore': tipo_ce,
            'telefono': tel,
            'telefono_filone': tel_filone,
            'email': email,
            'email_filone': email_filone,
            'note_filone': note_filone,
            'note': note_filone,
            'note_appuntamento': note_filone,
            'matricola_filone': matricola_completa,
            'pod_filone': pod,
            'eneltel_filone': _clean(r['eneltel']),
            'is_azienda': False,
            'tipo_contatto': 'privato',
        }
        lavori.append(item)
        if pod:
            dati_filone[pod] = {
                'pod': pod,
                'telefono': tel_filone,
                'nominativo': nom,
                'indirizzo': via,
                'civico': civico,
                'comune': comune,
                'note': note_filone,
                'matricola': matricola_completa,
            }

    classificati = classifica_contatti(lavori, dati_filone)

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

    contatti.sort(key=lambda x: (x.get('generazione_id') or 0, x.get('lavoro') or 999999))

    return {
        'contatti': contatti,
        'totale': len(contatti),
        'cellulare': len(classificati['cellulare']),
        'fisso': len(classificati['fisso']),
        'senza_telefono': len(classificati['senza_telefono']),
    }

