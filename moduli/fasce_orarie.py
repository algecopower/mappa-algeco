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
      Es. 22 tappe su 6 fasce → 4+4+4+4+3+3 invece di 4+4+4+4+4+2.
    
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
            # Le prime 'resto' fasce ricevono una tappa in più
            count = base + (1 if f_idx < resto else 0)
            for _ in range(count):
                if idx < n:
                    risultato.append((percorso_ordinato[idx], fascia))
                    idx += 1
    else:
        for idx, punto in enumerate(percorso_ordinato):
            # Calcola quale fascia assegnare
            fascia_idx = idx // tappe_per_fascia
            
            if fascia_idx < len(fasce):
                fascia = fasce[fascia_idx]
            else:
                # Se ci sono più tappe delle fasce disponibili, usa l'ultima fascia
                fascia = fasce[-1]
            
            risultato.append((punto, fascia))
    
    return risultato


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



