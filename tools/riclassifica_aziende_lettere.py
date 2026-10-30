#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Strumento di bonifica e riclassificazione aziende/privati nelle lettere salvate.

Rianalizza le righe storiche salvate in `lettere_righe` con l'algoritmo aggiornato:
- Risoluzione contestuale della preposizione 'DI' (esclude cognomi patronimici)
- Analisi delle note operative (esclude relazioni di parentela, include attività commerciali)
- Rilevamento enti pubblici, condomini e attività commerciali
- Rispetto degli override manuali salvati in `lettere_classificazione_override`

Uso:
    python tools/riclassifica_aziende_lettere.py                   # dry-run provincia attiva
    python tools/riclassifica_aziende_lettere.py --apply           # applica su provincia attiva
    python tools/riclassifica_aziende_lettere.py --provincia ALL   # dry-run su tutte le province
    python tools/riclassifica_aziende_lettere.py --provincia ALL --apply
"""
import os
import sys
import time
import argparse
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moduli.rilevamento_aziende import is_azienda
from moduli.lettere import contact_key


def _get_province_list(scelta):
    scelta = (scelta or '').upper().strip()
    if scelta == 'ALL':
        import config
        return list(getattr(config, 'PROVINCE', {}).keys()) or ['TORINO', 'PAVIA']
    if scelta:
        return [scelta]
    from config import PROVINCIA_ATTIVA
    return [PROVINCIA_ATTIVA]


def riclassifica_db(provincia, apply=False):
    prov = provincia.upper()
    db_path = os.path.join('data', prov.lower(), 'state.db')
    if not os.path.exists(db_path):
        print(f"⚠️  Database per {prov} non trovato in {db_path}")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Verifica se le tabelle delle lettere esistono
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='lettere_righe'")
    if not c.fetchone():
        print(f"ℹ️  Nessuna tabella lettere_righe in {prov}")
        conn.close()
        return

    # Crea tabella override se non presente
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS lettere_classificazione_override (
            provincia TEXT NOT NULL,
            contact_key TEXT NOT NULL,
            pod TEXT,
            nominativo TEXT,
            is_azienda INTEGER NOT NULL,
            updated_ts INTEGER NOT NULL,
            updated_by TEXT,
            PRIMARY KEY (provincia, contact_key)
        )
        """
    )
    conn.commit()

    # Carica override manuali
    c.execute(
        "SELECT contact_key, is_azienda FROM lettere_classificazione_override WHERE provincia=?",
        (prov,),
    )
    overrides = {r['contact_key']: bool(r['is_azienda']) for r in c.fetchall()}

    # Carica tutte le righe delle lettere con note e filone
    query = """
        SELECT lr.id, lr.generazione_id, lr.pod, lr.nominativo, lr.comune, lr.via, lr.civico,
               lr.tipo_misuratore, lr.posizione_mis, lr.data_pianificazione, lr.ora_pianificazione,
               lr.operatore, lr.lettera, lr.telefono, lr.email, lr.is_azienda,
               f.note_appuntamento AS note_filone
        FROM lettere_righe lr
        LEFT JOIN filone f ON f.pod = lr.pod AND TRIM(COALESCE(lr.pod, '')) != ''
        ORDER BY lr.generazione_id ASC, lr.id ASC
    """
    righe = c.execute(query).fetchall()

    falsi_positivi_rimossi = []   # Azienda -> Privato
    nuove_aziende_rilevate = []   # Privato -> Azienda
    invariati = 0
    now = int(time.time())

    for r in righe:
        pod = (r['pod'] or '').strip()
        nom = (r['nominativo'] or '').strip()
        com = (r['comune'] or '').strip()
        tipo_ce = (r['tipo_misuratore'] or '').strip()
        note_str = f"{r['posizione_mis'] or ''} {r['note_filone'] or ''}".strip()
        old_is_az = bool(r['is_azienda'])

        ck = contact_key(pod, nom, com)
        if ck in overrides:
            new_is_az = overrides[ck]
        else:
            new_is_az = is_azienda(nom, tipo_ce, note=note_str)

        if old_is_az and not new_is_az:
            falsi_positivi_rimossi.append((r, ck))
            if apply:
                # 1. Imposta is_azienda=0 in lettere_righe
                c.execute("UPDATE lettere_righe SET is_azienda=0 WHERE id=?", (r['id'],))
                # 2. Rimuovi da lettere_aziende_generazione per questa generazione
                if pod:
                    c.execute(
                        "DELETE FROM lettere_aziende_generazione WHERE generazione_id=? AND UPPER(TRIM(pod))=?",
                        (r['generazione_id'], pod.upper()),
                    )
                else:
                    c.execute(
                        "DELETE FROM lettere_aziende_generazione WHERE generazione_id=? AND contact_key=?",
                        (r['generazione_id'], ck),
                    )
        elif not old_is_az and new_is_az:
            nuove_aziende_rilevate.append((r, ck))
            if apply:
                # 1. Imposta is_azienda=1 in lettere_righe
                c.execute("UPDATE lettere_righe SET is_azienda=1 WHERE id=?", (r['id'],))
                # 2. Inserisci in lettere_aziende_generazione
                c.execute(
                    """
                    INSERT INTO lettere_aziende_generazione (
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
                    """,
                    (
                        r['generazione_id'], prov, ck, pod, nom, com,
                        r['data_pianificazione'] or '', r['ora_pianificazione'] or '',
                        r['operatore'] or '', tipo_ce, r['lettera'] or '', now
                    )
                )
                # 3. Assicura presenza in lettere_contatti
                c.execute(
                    """
                    INSERT INTO lettere_contatti (
                        provincia, contact_key, pod, nominativo, comune, telefono, email,
                        created_ts, updated_ts, updated_by
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(provincia, contact_key) DO UPDATE SET
                        nominativo=excluded.nominativo,
                        comune=excluded.comune,
                        updated_ts=excluded.updated_ts,
                        updated_by=excluded.updated_by
                    """,
                    (
                        prov, ck, pod, nom, com,
                        r['telefono'] or '', r['email'] or '', now, now, 'bonifica_automatica'
                    )
                )
        else:
            invariati += 1

    if apply:
        conn.commit()

    print("\n==================================================")
    print(f"📊 REPORT BONIFICA LETTERE — PROVINCIA: {prov}")
    print(f"Modalità: {'APPLICAZIONE MODIFICHE' if apply else 'DRY-RUN (nessuna modifica salvata)'}")
    print("==================================================")
    print(f"Totale righe esaminate:           {len(righe)}")
    print(f"Falsi positivi bonificati (Azienda → Privato): {len(falsi_positivi_rimossi)}")
    print(f"Nuove aziende/enti rilevati (Privato → Azienda): {len(nuove_aziende_rilevate)}")
    print(f"Righe invariate:                  {invariati}")
    if overrides:
        print(f"Override manuali rispettati:      {len(overrides)}")

    if falsi_positivi_rimossi:
        print("\n🔍 Esempi falsi positivi corretti a PRIVATI (top 10):")
        for r, ck in falsi_positivi_rimossi[:10]:
            note = (r['note_filone'] or r['posizione_mis'] or '').strip()
            print(f"  [-] {r['nominativo']:30} | COMUNE: {r['comune']:15} | POD: {r['pod'] or '-':16} | NOTE: {note[:30]}")

    if nuove_aziende_rilevate:
        print("\n🏢 Esempi rilevati come AZIENDE/ENTI (top 10):")
        for r, ck in nuove_aziende_rilevate[:10]:
            note = (r['note_filone'] or r['posizione_mis'] or '').strip()
            print(f"  [+] {r['nominativo']:30} | COMUNE: {r['comune']:15} | POD: {r['pod'] or '-':16} | NOTE: {note[:30]}")

    conn.close()


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '--provincia',
        type=str,
        default='',
        help='Provincia da elaborare (es. TORINO, PAVIA, ALL). Default: provincia attiva',
    )
    parser.add_argument(
        '--apply',
        action='store_true',
        help='Applica le modifiche al database (default: solo simulazione dry-run)',
    )
    args = parser.parse_args()

    province = _get_province_list(args.provincia)
    for p in province:
        riclassifica_db(p, apply=args.apply)

    if not args.apply:
        print("\n💡 Esegui con l'argomento `--apply` per salvare le modifiche nel database.")


if __name__ == '__main__':
    main()
