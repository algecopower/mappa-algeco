# -*- coding: utf-8 -*-
"""
Modulo centralizzato per la gestione delle fasce orarie.

Questo modulo contiene:
- Configurazione fasce orarie fisse
- Funzione per assegnare fasce alle tappe
- Funzioni helper per formattazione

Utilizzato da:
- genera_fasce_orarie.py
- genera_excel_lettere.py
- mappa_html.py (eventualmente)
"""

# ============================================================================
# FASCE ORARIE FISSE
# ============================================================================
FASCE_ORARIE = [
    '8/11',
    '9/12',
    '10/13',
    '11/14',
    '12/15',
    '13/16',
    '14/17'
]

TAPPE_PER_FASCIA = 4  # Ogni operatore ha 4 tappe per ogni fascia (totale 24 tappe)


def assegna_fasce(percorso_ordinato, fasce=None, tappe_per_fascia=None, bilancia=False):
    """
    Assegna le fasce orarie alle tappe del percorso.

    Modalità:
    - bilancia=False (default): ogni fascia ha esattamente tappe_per_fascia tappe.
      Se le tappe eccedono le fasce disponibili, le rimanenti vanno nell'ultima fascia.
    - bilancia=True: le tappe vengono distribuite uniformemente su tutte le fasce.
      Es. 22 tappe su 7 fasce → 4+3+3+3+3+3+3 (le prime 'resto' fasce ricevono +1).

    Args:
        percorso_ordinato: lista di punti già ordinati dall'algoritmo di ottimizzazione
        fasce: lista di fasce orarie (default: FASCE_ORARIE)
        tappe_per_fascia: numero tappe per fascia (default: TAPPE_PER_FASCIA, ignorato se bilancia=True)
        bilancia: se True, distribuisce uniformemente le tappe su tutte le fasce

    Returns:
        Lista di tuple (punto, fascia_oraria)
    """
    if fasce is None:
        fasce = FASCE_ORARIE
    if tappe_per_fascia is None:
        tappe_per_fascia = TAPPE_PER_FASCIA

    risultato = []

    if bilancia:
        # Distribuzione bilanciata: le tappe in più vengono spalmate sulle prime fasce
        n = len(percorso_ordinato)
        n_fasce = len(fasce)
        base = n // n_fasce
        resto = n % n_fasce

        idx = 0
        for f_idx, fascia in enumerate(fasce):
            count = base + (1 if f_idx < resto else 0)
            for _ in range(count):
                if idx < n:
                    risultato.append((percorso_ordinato[idx], fascia))
                    idx += 1
    else:
        for idx, punto in enumerate(percorso_ordinato):
            fascia_idx = idx // tappe_per_fascia
            if fascia_idx < len(fasce):
                fascia = fasce[fascia_idx]
            else:
                fascia = fasce[-1]
            risultato.append((punto, fascia))

    return risultato


def consolida_fasce_per_indirizzo(tappe_con_fasce, fasce_ordinate=None):
    """
    Post-pass: allinea le fasce dei contatori con stesso indirizzo
    (comune + localita + via + civico) ma fascia diversa.

    Motivazione: il geocoder talvolta piazza coordinate leggermente diverse
    per contatori che condividono lo stesso indirizzo postale (caso tipico:
    fotovoltaico geocodificato qualche centinaio di metri lontano dal
    POD principale). Il TSP li tratta come fermate distinte e li distribuisce
    in fasce non adiacenti, producendo lettere incongruenti per lo stesso
    cliente. Questa funzione corregge a valle senza toccare l'ottimizzazione.

    Regola di allineamento (richiesta utente):
      1. Fascia di maggioranza nel gruppo.
      2. In caso di parità: fascia "centrale" — la mediana per indice
         tra le candidate (per len pari prende l'upper-middle).

    Gruppi ignorati:
      - meno di 2 punti
      - civico vuoto/NaN o SN/SNC (rischio di accorpare punti spuri lungo
        la stessa via)
      - via vuota

    Args:
        tappe_con_fasce: lista di tuple (punto_dict, fascia_str) — output
            tipicamente di assegna_fasce()
        fasce_ordinate: lista di fasce in ordine cronologico (default: FASCE_ORARIE)

    Returns:
        Nuova lista (punto, fascia) con eventuali fasce riassegnate.
        L'ordine originale è preservato.
    """
    from collections import Counter

    if not tappe_con_fasce:
        return tappe_con_fasce

    if fasce_ordinate is None:
        fasce_ordinate = FASCE_ORARIE

    fascia_idx = {f: i for i, f in enumerate(fasce_ordinate)}
    civici_invalidi = {'', 'NAN', 'NONE', 'SN', 'SNC',
                       'S.N.', 'S.N.C.', 'S/N', 'S/NC'}

    def _campo(p, *chiavi):
        for k in chiavi:
            v = p.get(k)
            if v is not None and str(v).strip():
                return str(v).strip()
        return ''

    def chiave_indirizzo(punto):
        via = _campo(punto, 'VIA_K', 'via').upper()
        civico = _campo(punto, 'CIVICO_K', 'civico').upper()
        if not via or civico in civici_invalidi:
            return None
        comune = _campo(punto, 'COMUNE', 'comune').upper()
        # LOCALITA disambigua frazioni omonime dello stesso comune (Pavia).
        localita = _campo(punto, 'LOCALITA', 'localita').upper()
        return (comune, localita, via, civico)

    gruppi = {}
    for i, (punto, _fascia) in enumerate(tappe_con_fasce):
        k = chiave_indirizzo(punto)
        if k is None:
            continue
        gruppi.setdefault(k, []).append(i)

    riallineamenti = {}
    for indici in gruppi.values():
        if len(indici) < 2:
            continue
        fasce_gruppo = [tappe_con_fasce[i][1] for i in indici]
        if len(set(fasce_gruppo)) == 1:
            continue

        conteggio = Counter(fasce_gruppo)
        max_count = max(conteggio.values())
        candidate = [f for f, c in conteggio.items() if c == max_count]
        candidate_idx = sorted(
            fascia_idx.get(f, len(fasce_ordinate)) for f in candidate
        )
        scelta_idx = candidate_idx[len(candidate_idx) // 2]
        if 0 <= scelta_idx < len(fasce_ordinate):
            scelta = fasce_ordinate[scelta_idx]
        else:
            scelta = candidate[0]

        for i in indici:
            if tappe_con_fasce[i][1] != scelta:
                riallineamenti[i] = scelta

    if not riallineamenti:
        return tappe_con_fasce

    return [
        (p, riallineamenti.get(i, f))
        for i, (p, f) in enumerate(tappe_con_fasce)
    ]


def formatta_fascia_oraria(fascia):
    """
    Converte la fascia oraria dal formato 'xx/xx' a 'dalle x:00 alle x:00'.
    
    Args:
        fascia: Stringa nel formato '8/11', '10/13', etc.
    
    Returns:
        Stringa formattata come 'dalle 8:00 alle 11:00'
    """
    if not fascia:
        return ''
    
    try:
        parti = fascia.split('/')
        if len(parti) == 2:
            ora_inizio = int(parti[0])
            ora_fine = int(parti[1])
            return f"dalle {ora_inizio}:00 alle {ora_fine}:00"
    except Exception:
        pass
    
    return fascia


def get_colore_fascia(fascia):
    """
    Restituisce il codice colore per una fascia oraria.
    Usato per formattazione Excel e visualizzazioni.
    
    Args:
        fascia: Stringa nel formato '8/11', '10/13', etc.
    
    Returns:
        Codice colore esadecimale (senza #)
    """
    colori = {
        '8/11': 'E2EFDA',   # Verde chiaro
        '9/12': 'D6E4F0',   # Celeste chiaro
        '10/13': 'DDEBF7',  # Azzurro chiaro
        '11/14': 'FCE4D6',  # Arancione chiaro
        '12/15': 'FFF2CC',  # Giallo chiaro
        '13/16': 'E4DFEC',  # Viola chiaro
        '14/17': 'F8CBAD',  # Pesca
    }
    return colori.get(fascia, 'FFFFFF')  # Bianco di default



