"""
Funzioni per l'ordinamento e ottimizzazione dei percorsi.
Implementa TSP circolare (include ritorno al deposito) con distanze stradali reali.

NOTA IMPORTANTE per Torino:
- L'algoritmo usa le DURATE (tempo) da OSRM, non le distanze in km
- Le AUTOSTRADE vengono preferite quando sono più veloci
- I tecnici non passano per Torino città, usano l'autostrada
"""
import re
import math
import pandas as pd

from config import DEPOSITO, ORA_INIZIO_GIORNATA, PROVINCIA_ATTIVA, USA_OSRM

# Raggio micro-zone per raggruppamento (evita saltelli nel percorso)
try:
    from config import RAGGIO_MICROZONA_KM
except ImportError:
    RAGGIO_MICROZONA_KM = 0.5  # Default: 500 metri

# Soglia orario per appuntamenti "serali" che vanno messi per ultimi
# Torino: dopo le 17:00 (1020 minuti)
# Pavia: dopo le 15:00 (900 minuti)
ORA_LIMITE_SERALE = 17 * 60 if PROVINCIA_ATTIVA == 'TORINO' else 15 * 60


def normalizza_eneltel(val):
    """Normalizza codice Eneltel rimuovendo zeri iniziali."""
    if pd.isna(val):
        return None
    s = str(val).strip()
    s = re.sub(r'^0+', '', s)
    return s if s else None


def estrai_orario_e_fascia(testo):
    """
    Estrae orario e/o fascia da testo.

    Ritorna: (ora_min, ora_max) in minuti dall'inizio del giorno
    - Se orario preciso: ora_min == ora_max (es. 10:30 → (630, 630))
    - Se fascia: ora_min < ora_max (es. 10/12 → (600, 720))
    - Se nessun orario: (None, None)
    """
    if pd.isna(testo) or not testo:
        return (None, None)
    testo = str(testo).strip()

    # FORMATO FASCIA: "dalle HH:MM alle HH:MM" o "dalle HH alle HH"
    match = re.search(r'dalle?\s*(\d{1,2})(?:[:.:](\d{2}))?\s*alle?\s*(\d{1,2})(?:[:.:](\d{2}))?', testo, re.IGNORECASE)
    if match:
        ora_inizio = int(match.group(1))
        min_inizio = int(match.group(2)) if match.group(2) else 0
        ora_fine = int(match.group(3))
        min_fine = int(match.group(4)) if match.group(4) else 0
        tot_inizio = ora_inizio * 60 + min_inizio
        tot_fine = ora_fine * 60 + min_fine
        if 6 <= ora_inizio <= 20 and 6 <= ora_fine <= 20 and tot_inizio < tot_fine:
            return (tot_inizio, tot_fine)

    # FORMATO FASCIA CON KEYWORD: "ORE HH:MM/HH:MM" o "ORE HH:MM-HH:MM" (priorità alta, più specifico)
    match = re.search(r'ore?\s*(\d{1,2})[:.](\d{2})\s*[/\-]\s*(\d{1,2})[:.](\d{2})', testo, re.IGNORECASE)
    if match:
        ora_inizio = int(match.group(1))
        min_inizio = int(match.group(2))
        ora_fine = int(match.group(3))
        min_fine = int(match.group(4))
        tot_inizio = ora_inizio * 60 + min_inizio
        tot_fine = ora_fine * 60 + min_fine
        if 6 <= ora_inizio <= 20 and 6 <= ora_fine <= 20 and tot_inizio < tot_fine:
            return (tot_inizio, tot_fine)

    # FORMATO FASCIA CON SPAZIO: "8:00 9,00" o "8:00 9:00" (due orari separati da spazio)
    match = re.search(r'(\d{1,2})[:.;,](\d{2})\s+(\d{1,2})[:.;,](\d{2})', testo)
    if match:
        ora_inizio = int(match.group(1))
        min_inizio = int(match.group(2))
        ora_fine = int(match.group(3))
        min_fine = int(match.group(4))
        tot_inizio = ora_inizio * 60 + min_inizio
        tot_fine = ora_fine * 60 + min_fine
        if 6 <= ora_inizio <= 20 and 6 <= ora_fine <= 20 and tot_inizio < tot_fine:
            return (tot_inizio, tot_fine)

    # FORMATO FASCIA: "HH/HH", "HH-HH", "HH:MM/HH:MM", "HH:MM-HH:MM" - cerca TUTTI i match e prendi il primo valido
    # (evita di confondere date come "20/01" con fasce orarie)
    for match in re.finditer(r'\b(\d{1,2})(?:[:.](\d{2}))?\s*[/\-]\s*(\d{1,2})(?:[:.](\d{2}))?\b', testo):
        ora_inizio = int(match.group(1))
        min_inizio = int(match.group(2)) if match.group(2) else 0
        ora_fine = int(match.group(3))
        min_fine = int(match.group(4)) if match.group(4) else 0
        tot_inizio = ora_inizio * 60 + min_inizio
        tot_fine = ora_fine * 60 + min_fine
        # Valida come fascia oraria (non data)
        if 6 <= ora_inizio <= 20 and 6 <= ora_fine <= 20 and tot_inizio < tot_fine:
            return (tot_inizio, tot_fine)
    
    # FORMATO ORARIO PRECISO: HH:MM, HH.MM, HH;MM, HH,MM
    match = re.search(r'(\d{1,2})[:.;,](\d{2})', testo)
    if match:
        ore, minuti = int(match.group(1)), int(match.group(2))
        if 0 <= ore <= 23 and 0 <= minuti <= 59:
            tot = ore * 60 + minuti
            return (tot, tot)
    
    # FORMATO COMPATTO: 1030, 930 (ma NON anni come 2024, 2025, 2026...)
    match = re.search(r'\b(\d{3,4})\b', testo)
    if match:
        num = match.group(1).zfill(4)
        # Escludi numeri che sembrano anni (19xx, 20xx)
        if not (num.startswith('19') or num.startswith('20')):
            ore, minuti = int(num[:2]), int(num[2:])
            if 0 <= ore <= 23 and 0 <= minuti <= 59:
                tot = ore * 60 + minuti
                return (tot, tot)
    
    # FORMATO CON KEYWORD: "alle 10", "ore 14", "h9"
    match = re.search(r'(?:alle|ore|h)\s*(\d{1,2})\b', testo, re.IGNORECASE)
    if match:
        ore = int(match.group(1))
        if 6 <= ore <= 20:
            tot = ore * 60
            return (tot, tot)
    
    return (None, None)


def estrai_orario(testo):
    """Wrapper per retrocompatibilità - ritorna stringa orario o fascia."""
    ora_min, ora_max = estrai_orario_e_fascia(testo)
    if ora_min is None:
        return None
    # Se è un orario preciso (min == max), mostra solo l'orario
    if ora_min == ora_max:
        return f"{ora_min // 60:02d}:{ora_min % 60:02d}"
    # Se è una fascia, mostra l'intervallo completo
    return f"{ora_min // 60:02d}:{ora_min % 60:02d}-{ora_max // 60:02d}:{ora_max % 60:02d}"


def minuti_to_orario(minuti):
    """Converte minuti in stringa HH:MM."""
    if minuti is None:
        return None
    return f"{minuti // 60:02d}:{minuti % 60:02d}"


def calcola_distanza_haversine(lat1, lon1, lat2, lon2):
    """Calcola distanza in km tra due punti usando formula Haversine.
    
    Delegata a zone_geografiche.calcola_distanza_km per evitare duplicazione.
    """
    from moduli.zone_geografiche import calcola_distanza_km
    return calcola_distanza_km(lat1, lon1, lat2, lon2)


def calcola_distanza_totale(percorso, punto_partenza, include_ritorno=True):
    """
    Calcola la distanza totale di un percorso TSP.
    
    Args:
        percorso: lista di punti ordinati
        punto_partenza: deposito (partenza E ritorno)
        include_ritorno: se True, include distanza dall'ultima tappa al deposito
    
    Returns:
        Distanza totale in km (Haversine)
    """
    if not percorso:
        return 0

    # Distanza dal deposito al primo punto
    distanza = calcola_distanza_haversine(
        punto_partenza['Lat'], punto_partenza['Lon'],
        percorso[0]['Lat'], percorso[0]['Lon']
    )

    # Distanza tra tutte le tappe consecutive
    for i in range(len(percorso) - 1):
        distanza += calcola_distanza_haversine(
            percorso[i]['Lat'], percorso[i]['Lon'],
            percorso[i + 1]['Lat'], percorso[i + 1]['Lon']
        )

    # IMPORTANTE: Include ritorno al deposito (TSP circolare)
    if include_ritorno and percorso:
        distanza += calcola_distanza_haversine(
            percorso[-1]['Lat'], percorso[-1]['Lon'],
            punto_partenza['Lat'], punto_partenza['Lon']
        )

    return distanza


def calcola_distanza_totale_matrice(ordine_indici, matrice, include_ritorno=True):
    """
    Calcola la distanza totale usando una matrice pre-calcolata.
    
    Args:
        ordine_indici: lista di indici dei punti (0 = deposito)
        matrice: matrice NxN delle distanze/durate
        include_ritorno: se True, include ritorno al deposito (indice 0)
    
    Returns:
        Distanza/durata totale
    """
    if not ordine_indici:
        return 0
    
    totale = 0
    
    # Dal deposito (0) al primo punto
    totale += matrice[0][ordine_indici[0]]
    
    # Tra punti consecutivi
    for i in range(len(ordine_indici) - 1):
        totale += matrice[ordine_indici[i]][ordine_indici[i + 1]]
    
    # Ritorno al deposito
    if include_ritorno:
        totale += matrice[ordine_indici[-1]][0]
    
    return totale


def two_opt_improve(percorso, punto_partenza, max_iterazioni=100):
    """
    Migliora un percorso usando l'algoritmo 2-opt (TSP circolare).

    L'algoritmo prova a invertire sottosezioni del percorso.
    Se l'inversione riduce la distanza totale (incluso ritorno), viene accettata.

    Args:
        percorso: lista di punti già ordinati
        punto_partenza: deposito/punto di partenza E RITORNO
        max_iterazioni: numero massimo di passaggi completi

    Returns:
        Percorso migliorato
    """
    if len(percorso) < 4:
        return percorso

    percorso = percorso.copy()
    migliorato = True
    iterazione = 0

    def get_dist(p1, p2):
        """Distanza tra due punti (gestisce deposito come dict speciale)."""
        return calcola_distanza_haversine(p1['Lat'], p1['Lon'], p2['Lat'], p2['Lon'])

    while migliorato and iterazione < max_iterazioni:
        migliorato = False
        iterazione += 1

        for i in range(len(percorso) - 1):
            for j in range(i + 2, len(percorso)):
                # Calcola distanza attuale dei 2 archi che verrebbero modificati
                # Arco 1: punto_prima_di_i -> punto_i
                # Arco 2: punto_j -> punto_dopo_j
                
                if i == 0:
                    dist_prima_i = get_dist(punto_partenza, percorso[i])
                else:
                    dist_prima_i = get_dist(percorso[i - 1], percorso[i])

                # Per l'ultimo punto, consideriamo il ritorno al deposito!
                if j == len(percorso) - 1:
                    dist_dopo_j = get_dist(percorso[j], punto_partenza)
                else:
                    dist_dopo_j = get_dist(percorso[j], percorso[j + 1])

                # Calcola distanza se invertiamo il segmento [i, j]
                # Nuovo arco 1: punto_prima_di_i -> punto_j
                # Nuovo arco 2: punto_i -> punto_dopo_j
                if i == 0:
                    dist_nuova_i = get_dist(punto_partenza, percorso[j])
                else:
                    dist_nuova_i = get_dist(percorso[i - 1], percorso[j])

                if j == len(percorso) - 1:
                    dist_nuova_dopo_j = get_dist(percorso[i], punto_partenza)
                else:
                    dist_nuova_dopo_j = get_dist(percorso[i], percorso[j + 1])

                # Confronta: se invertire è meglio, inverti
                dist_attuale = dist_prima_i + dist_dopo_j
                dist_nuova = dist_nuova_i + dist_nuova_dopo_j

                if dist_nuova < dist_attuale - 0.01:  # Tolleranza per errori floating point
                    # Inverti il segmento [i, j]
                    percorso[i:j + 1] = percorso[i:j + 1][::-1]
                    migliorato = True

    return percorso


def two_opt_improve_matrice(ordine_indici, matrice, max_iterazioni=100):
    """
    Migliora un percorso usando 2-opt con matrice distanze pre-calcolata.
    
    Versione ottimizzata che usa la matrice OSRM per distanze reali stradali.
    Considera sempre il ritorno al deposito (indice 0).
    
    Args:
        ordine_indici: lista di indici dei punti (escluso deposito che è sempre 0)
        matrice: matrice NxN delle durate/distanze (indice 0 = deposito)
        max_iterazioni: numero massimo di passaggi completi
    
    Returns:
        Lista di indici ottimizzata
    """
    if len(ordine_indici) < 4:
        return ordine_indici
    
    ordine = ordine_indici.copy()
    n = len(ordine)
    
    # Limita iterazioni per percorsi molto lunghi (altrimenti ci mette troppo)
    if n > 200:
        max_iterazioni = min(max_iterazioni, 10)
    elif n > 100:
        max_iterazioni = min(max_iterazioni, 30)
    
    migliorato = True
    iterazione = 0
    
    while migliorato and iterazione < max_iterazioni:
        migliorato = False
        iterazione += 1
        
        for i in range(n - 1):
            for j in range(i + 2, n):
                # Indici nella matrice
                # Prima di i: deposito (0) se i==0, altrimenti ordine[i-1]
                prev_i = 0 if i == 0 else ordine[i - 1]
                curr_i = ordine[i]
                curr_j = ordine[j]
                # Dopo j: deposito (0) se j è ultimo, altrimenti ordine[j+1]
                next_j = 0 if j == n - 1 else ordine[j + 1]
                
                # Distanza attuale degli archi
                dist_attuale = matrice[prev_i][curr_i] + matrice[curr_j][next_j]
                
                # Distanza con inversione [i, j]
                dist_nuova = matrice[prev_i][curr_j] + matrice[curr_i][next_j]
                
                if dist_nuova < dist_attuale - 0.01:
                    ordine[i:j + 1] = ordine[i:j + 1][::-1]
                    migliorato = True
    
    return ordine


def or_opt_improve(percorso, punto_partenza, max_iterazioni=50):
    """
    Migliora un percorso usando l'algoritmo Or-opt (TSP circolare).
    
    Prova a spostare segmenti di 1, 2 o 3 punti consecutivi
    in altre posizioni del percorso per ridurre la distanza totale.
    Considera il ritorno al deposito.
    
    Args:
        percorso: lista di punti già ordinati
        punto_partenza: deposito/punto di partenza E RITORNO
        max_iterazioni: numero massimo di passaggi completi
    
    Returns:
        Percorso migliorato
    """
    if len(percorso) < 4:
        return percorso
    
    percorso = percorso.copy()
    migliorato = True
    iterazione = 0
    
    def calc_dist(p1, p2):
        """Calcola distanza tra due punti (gestisce deposito come None)."""
        if p1 is None:
            lat1, lon1 = punto_partenza['Lat'], punto_partenza['Lon']
        else:
            lat1, lon1 = p1['Lat'], p1['Lon']
        if p2 is None:
            lat2, lon2 = punto_partenza['Lat'], punto_partenza['Lon']
        else:
            lat2, lon2 = p2['Lat'], p2['Lon']
        return calcola_distanza_haversine(lat1, lon1, lat2, lon2)
    
    while migliorato and iterazione < max_iterazioni:
        migliorato = False
        iterazione += 1
        
        # Prova segmenti di lunghezza 1, 2 e 3
        for seg_len in [1, 2, 3]:
            if migliorato:
                break
                
            for i in range(len(percorso) - seg_len + 1):
                if migliorato:
                    break
                    
                # Segmento da spostare: percorso[i:i+seg_len]
                segmento = percorso[i:i + seg_len]
                
                # Punto prima del segmento (None = deposito)
                prev_punto = percorso[i - 1] if i > 0 else None
                # Punto dopo il segmento (None = deposito/ritorno)
                next_idx = i + seg_len
                next_punto = percorso[next_idx] if next_idx < len(percorso) else None
                
                # Costo rimozione: archi rimossi - arco creato
                costo_rimossi = calc_dist(prev_punto, segmento[0]) + calc_dist(segmento[-1], next_punto)
                costo_nuovo_arco = calc_dist(prev_punto, next_punto)
                guadagno_rimozione = costo_rimossi - costo_nuovo_arco
                
                # Prova a inserire il segmento in ogni altra posizione
                for j in range(len(percorso) - seg_len + 2):
                    # Salta posizioni che coinvolgono il segmento originale
                    if i <= j <= i + seg_len:
                        continue
                    
                    # Costruisci percorso temporaneo senza segmento
                    temp = percorso[:i] + percorso[i + seg_len:]
                    
                    # Aggiusta indice j dopo rimozione
                    new_j = j - seg_len if j > i else j
                    
                    # Punto prima della posizione inserimento
                    insert_prev = temp[new_j - 1] if new_j > 0 else None
                    # Punto dopo la posizione inserimento
                    insert_next = temp[new_j] if new_j < len(temp) else None
                    
                    # Costo inserimento: archi aggiunti - arco rimosso
                    costo_archi_nuovi = calc_dist(insert_prev, segmento[0]) + calc_dist(segmento[-1], insert_next)
                    costo_arco_rimosso = calc_dist(insert_prev, insert_next)
                    costo_inserimento = costo_archi_nuovi - costo_arco_rimosso
                    
                    # Se il guadagno dalla rimozione è maggiore del costo inserimento, conviene
                    if costo_inserimento < guadagno_rimozione - 0.01:
                        # Applica spostamento
                        nuovo_percorso = temp[:new_j] + segmento + temp[new_j:]
                        percorso = nuovo_percorso
                        migliorato = True
                        break
    
    return percorso


def or_opt_improve_matrice(ordine_indici, matrice, max_iterazioni=50):
    """
    Migliora un percorso usando Or-opt con matrice distanze pre-calcolata.
    
    Args:
        ordine_indici: lista di indici dei punti (0 = deposito, non incluso)
        matrice: matrice NxN delle durate/distanze (indice 0 = deposito)
        max_iterazioni: numero massimo di passaggi completi
    
    Returns:
        Lista di indici ottimizzata
    """
    if len(ordine_indici) < 4:
        return ordine_indici
    
    ordine = ordine_indici.copy()
    migliorato = True
    iterazione = 0
    
    def get_idx(pos):
        """Ritorna indice nella matrice (0=deposito se fuori range)."""
        if pos < 0 or pos >= len(ordine):
            return 0
        return ordine[pos]
    
    while migliorato and iterazione < max_iterazioni:
        migliorato = False
        iterazione += 1
        
        for seg_len in [1, 2, 3]:
            if migliorato:
                break
                
            for i in range(len(ordine) - seg_len + 1):
                if migliorato:
                    break
                
                # Indici del segmento
                seg_start = ordine[i]
                seg_end = ordine[i + seg_len - 1]
                
                # Punti adiacenti al segmento
                prev_idx = 0 if i == 0 else ordine[i - 1]
                next_idx = 0 if i + seg_len >= len(ordine) else ordine[i + seg_len]
                
                # Guadagno rimozione
                costo_rimossi = matrice[prev_idx][seg_start] + matrice[seg_end][next_idx]
                costo_nuovo = matrice[prev_idx][next_idx]
                guadagno = costo_rimossi - costo_nuovo
                
                # Prova inserimenti
                for j in range(len(ordine) - seg_len + 2):
                    if i <= j <= i + seg_len:
                        continue
                    
                    # Costruisci ordine temporaneo
                    temp = ordine[:i] + ordine[i + seg_len:]
                    new_j = j - seg_len if j > i else j
                    
                    ins_prev = 0 if new_j == 0 else temp[new_j - 1]
                    ins_next = 0 if new_j >= len(temp) else temp[new_j]
                    
                    costo_ins = matrice[ins_prev][seg_start] + matrice[seg_end][ins_next] - matrice[ins_prev][ins_next]
                    
                    if costo_ins < guadagno - 0.01:
                        ordine = temp[:new_j] + ordine[i:i + seg_len] + temp[new_j:]
                        migliorato = True
                        break
    
    return ordine


def three_opt_improve(percorso, punto_partenza, max_iterazioni=30):
    """
    Migliora un percorso usando l'algoritmo 3-opt (TSP circolare).
    
    Considera tutte le combinazioni di 3 archi e prova le 8 possibili
    riconnessioni per trovare miglioramenti. Include ritorno al deposito.
    
    Più costoso di 2-opt ma può trovare miglioramenti che 2-opt non trova.
    
    Args:
        percorso: lista di punti già ordinati
        punto_partenza: deposito/punto di partenza E RITORNO
        max_iterazioni: numero massimo di passaggi completi
    
    Returns:
        Percorso migliorato
    """
    if len(percorso) < 6:
        return percorso
    
    percorso = percorso.copy()
    n = len(percorso)
    migliorato = True
    iterazione = 0
    
    def get_punto(idx):
        """Ritorna il punto all'indice, o deposito per -1 o n."""
        if idx < 0 or idx >= n:
            return punto_partenza
        return percorso[idx]
    
    def dist(i, j):
        """Distanza tra punto i e punto j."""
        p1 = get_punto(i)
        p2 = get_punto(j)
        return calcola_distanza_haversine(p1['Lat'], p1['Lon'], p2['Lat'], p2['Lon'])
    
    while migliorato and iterazione < max_iterazioni:
        migliorato = False
        iterazione += 1
        n = len(percorso)
        
        # Considera ogni tripla di posizioni i < j < k
        for i in range(n - 4):
            for j in range(i + 2, n - 2):
                for k in range(j + 2, n):
                    # Considera anche ritorno: l'ultimo arco va a deposito
                    # Archi attuali: (i-1,i), (j-1,j), (k-1,k), e se k=n-1: (k, deposito)
                    d0 = dist(i - 1, i) + dist(j - 1, j) + dist(k - 1, k)
                    if k == n - 1:
                        d0 += dist(k, n)  # Ritorno al deposito
                    
                    # Caso 4: A - C - B - D (scambia B e C)
                    d4 = dist(i - 1, j) + dist(k - 1, i) + dist(j - 1, k)
                    if k == n - 1:
                        d4 += dist(j - 1, n)
                    if d4 < d0 - 0.01:
                        nuovo = percorso[:i] + percorso[j:k] + percorso[i:j] + percorso[k:]
                        percorso = nuovo
                        migliorato = True
                        break
                    
                    # Caso 3: A - B' - C' - D
                    d3 = dist(i - 1, j - 1) + dist(i, k - 1) + dist(j, k)
                    if d3 < d0 - 0.01:
                        nuovo = percorso[:i] + percorso[i:j][::-1] + percorso[j:k][::-1] + percorso[k:]
                        percorso = nuovo
                        migliorato = True
                        break
                    
                    # Caso 5: A - C - B' - D
                    d5 = dist(i - 1, j) + dist(k - 1, j - 1) + dist(i, k)
                    if d5 < d0 - 0.01:
                        nuovo = percorso[:i] + percorso[j:k] + percorso[i:j][::-1] + percorso[k:]
                        percorso = nuovo
                        migliorato = True
                        break
                    
                    # Caso 6: A - C' - B - D
                    d6 = dist(i - 1, k - 1) + dist(j, i) + dist(j - 1, k)
                    if d6 < d0 - 0.01:
                        nuovo = percorso[:i] + percorso[j:k][::-1] + percorso[i:j] + percorso[k:]
                        percorso = nuovo
                        migliorato = True
                        break
                    
                    # Caso 7: A - C' - B' - D
                    d7 = dist(i - 1, k - 1) + dist(j, j - 1) + dist(i, k)
                    if d7 < d0 - 0.01:
                        nuovo = percorso[:i] + percorso[j:k][::-1] + percorso[i:j][::-1] + percorso[k:]
                        percorso = nuovo
                        migliorato = True
                        break
                
                if migliorato:
                    break
            if migliorato:
                break
    
    return percorso


def three_opt_improve_matrice(ordine_indici, matrice, max_iterazioni=30):
    """
    Migliora un percorso usando 3-opt con matrice distanze pre-calcolata.
    
    Args:
        ordine_indici: lista di indici dei punti (0 = deposito, non incluso)
        matrice: matrice NxN delle durate/distanze (indice 0 = deposito)
        max_iterazioni: numero massimo di passaggi completi
    
    Returns:
        Lista di indici ottimizzata
    """
    if len(ordine_indici) < 6:
        return ordine_indici
    
    ordine = ordine_indici.copy()
    migliorato = True
    iterazione = 0
    
    def get_idx(pos):
        """Indice nella matrice (0=deposito se fuori range)."""
        if pos < 0 or pos >= len(ordine):
            return 0
        return ordine[pos]
    
    while migliorato and iterazione < max_iterazioni:
        migliorato = False
        iterazione += 1
        n = len(ordine)
        
        for i in range(n - 4):
            for j in range(i + 2, n - 2):
                for k in range(j + 2, n):
                    # Costo attuale
                    d0 = (matrice[get_idx(i-1)][get_idx(i)] + 
                          matrice[get_idx(j-1)][get_idx(j)] + 
                          matrice[get_idx(k-1)][get_idx(k)])
                    if k == n - 1:
                        d0 += matrice[get_idx(k)][0]  # Ritorno
                    
                    # Caso 4: A - C - B - D
                    d4 = (matrice[get_idx(i-1)][get_idx(j)] + 
                          matrice[get_idx(k-1)][get_idx(i)] + 
                          matrice[get_idx(j-1)][get_idx(k)])
                    if d4 < d0 - 0.01:
                        ordine = ordine[:i] + ordine[j:k] + ordine[i:j] + ordine[k:]
                        migliorato = True
                        break
                    
                    # Caso 3: A - B' - C' - D
                    d3 = (matrice[get_idx(i-1)][get_idx(j-1)] + 
                          matrice[get_idx(i)][get_idx(k-1)] + 
                          matrice[get_idx(j)][get_idx(k)])
                    if d3 < d0 - 0.01:
                        ordine = ordine[:i] + ordine[i:j][::-1] + ordine[j:k][::-1] + ordine[k:]
                        migliorato = True
                        break
                
                if migliorato:
                    break
            if migliorato:
                break
    
    return ordine


def nearest_neighbor_order(punti, punto_partenza, applica_2opt=True):
    """
    Ordina punti con algoritmo nearest neighbor + ottimizzazione 2-opt.
    Parte dal punto_partenza e sceglie sempre il più vicino,
    poi migliora il percorso con 2-opt.

    Args:
        punti: lista di punti da ordinare
        punto_partenza: deposito/punto di partenza
        applica_2opt: se True, applica ottimizzazione 2-opt (default: True)
    """
    if not punti:
        return []

    risultato = []
    rimanenti = punti.copy()
    pos = (punto_partenza['Lat'], punto_partenza['Lon'])

    while rimanenti:
        min_dist = float('inf')
        idx_min = 0
        for i, p in enumerate(rimanenti):
            dist = calcola_distanza_haversine(pos[0], pos[1], p['Lat'], p['Lon'])
            if dist < min_dist:
                min_dist = dist
                idx_min = i
        punto = rimanenti.pop(idx_min)
        risultato.append(punto)
        pos = (punto['Lat'], punto['Lon'])

    # Applica ottimizzazioni in cascata per migliorare il percorso
    if applica_2opt and len(risultato) >= 4:
        # 1. 2-opt: inversioni di segmenti (veloce, ~90-95% ottimo)
        risultato = two_opt_improve(risultato, punto_partenza)
        
        # 2. Or-opt: spostamento segmenti 1-3 punti (trova miglioramenti diversi)
        if len(risultato) >= 4:
            risultato = or_opt_improve(risultato, punto_partenza)
        
        # 3. 3-opt: riconnessioni triple (più potente ma più lento)
        if len(risultato) >= 6:
            risultato = three_opt_improve(risultato, punto_partenza)
        
        # 4. Secondo passaggio 2-opt per catturare eventuali nuovi miglioramenti
        if len(risultato) >= 4:
            risultato = two_opt_improve(risultato, punto_partenza, max_iterazioni=50)

    return risultato


def nearest_neighbor_matrice(n_punti, matrice, start_idx=None):
    """
    Nearest neighbor usando matrice distanze pre-calcolata.
    
    Args:
        n_punti: numero di punti da visitare (escluso deposito)
        matrice: matrice NxN (indice 0 = deposito)
        start_idx: indice del primo punto da visitare (1-based), None = dal deposito
    
    Returns:
        Lista di indici dei punti nell'ordine di visita (1-based, esclude deposito)
    """
    if n_punti == 0:
        return []
    
    # Indici disponibili: 1, 2, ..., n_punti (0 è il deposito)
    disponibili = set(range(1, n_punti + 1))
    ordine = []
    
    # Punto di partenza
    if start_idx is not None and start_idx in disponibili:
        ordine.append(start_idx)
        disponibili.remove(start_idx)
        pos_corrente = start_idx
    else:
        # Parti dal deposito: trova il più vicino
        pos_corrente = 0
    
    # Costruisci percorso greedy
    while disponibili:
        min_dist = float('inf')
        idx_min = None
        for idx in disponibili:
            if matrice[pos_corrente][idx] < min_dist:
                min_dist = matrice[pos_corrente][idx]
                idx_min = idx
        
        if idx_min is not None:
            ordine.append(idx_min)
            disponibili.remove(idx_min)
            pos_corrente = idx_min
    
    return ordine


def raggruppa_in_microzone(punti, raggio_zona_km=0.5):
    """
    Raggruppa i punti in micro-zone geografiche.
    
    Questo è il primo passo per creare percorsi "logici":
    - I contatori vicini (~500m) vengono raggruppati insieme
    - Ogni zona verrà completata prima di passare alla successiva
    
    Algoritmo: clustering gerarchico semplice basato su distanza
    
    Args:
        punti: lista di punti con Lat/Lon
        raggio_zona_km: raggio massimo di una micro-zona (default 500m)
    
    Returns:
        Lista di zone, ogni zona è una lista di punti
    """
    if not punti:
        return []
    
    if len(punti) == 1:
        return [punti]
    
    # Copia per non modificare originali
    punti_rimanenti = punti.copy()
    zone = []
    
    while punti_rimanenti:
        # Prendi il primo punto come "seed" della nuova zona
        seed = punti_rimanenti.pop(0)
        zona_corrente = [seed]
        
        # Trova tutti i punti entro il raggio dalla zona
        # (distanza dal centroide della zona)
        i = 0
        while i < len(punti_rimanenti):
            punto = punti_rimanenti[i]
            
            # Calcola centroide della zona corrente
            lat_centro = sum(p['Lat'] for p in zona_corrente) / len(zona_corrente)
            lon_centro = sum(p['Lon'] for p in zona_corrente) / len(zona_corrente)
            
            # Distanza dal centroide
            dist = calcola_distanza_haversine(lat_centro, lon_centro, punto['Lat'], punto['Lon'])
            
            if dist <= raggio_zona_km:
                zona_corrente.append(punto)
                punti_rimanenti.pop(i)
                # Non incrementare i, ricontrolla dalla stessa posizione
            else:
                i += 1
        
        zone.append(zona_corrente)
    
    return zone


def ottimizza_percorso_per_zone(punti, deposito, usa_osrm=True, raggio_zona_km=0.5):
    """
    Ottimizza il percorso usando NEAREST NEIGHBOR GEOGRAFICO PURO.
    
    LOGICA SEMPLICE E INTUITIVA:
    - Parti dal deposito
    - Vai al contatore PIÙ VICINO geograficamente
    - Da lì, vai al successivo più vicino
    - Continua fino a visitarli tutti
    - Torna al deposito
    
    Questo crea un percorso che "ha senso" visivamente sulla mappa:
    non salta mai da un punto a uno lontano se ce n'è uno più vicino.
    
    Args:
        punti: lista di punti da visitare
        deposito: punto di partenza E ritorno
        usa_osrm: NON USATO (sempre geografico per ordine logico)
        raggio_zona_km: NON USATO (nearest neighbor puro)
    
    Returns:
        dict con 'percorso', 'distanza_totale', 'fonte', 'n_zone'
    """
    if not punti:
        return {'percorso': [], 'distanza_totale': 0, 'fonte': 'N/A', 'n_zone': 0}
    
    # NEAREST NEIGHBOR GEOGRAFICO PURO
    # Semplice, intuitivo, crea percorsi che "hanno senso"
    percorso = nearest_neighbor_order(punti, deposito, applica_2opt=True)
    distanza = calcola_distanza_totale(percorso, deposito, include_ritorno=True)
    
    return {
        'percorso': percorso,
        'distanza_totale': distanza,
        'fonte': 'Geografico (nearest neighbor)',
        'n_zone': 1
    }


def ottimizza_percorso_tsp_circolare(punti, deposito, usa_osrm=True):
    """
    Ottimizza un percorso TSP circolare usando distanze stradali reali.
    
    Questa è la funzione principale per l'ottimizzazione:
    1. Calcola matrice distanze (OSRM o Haversine)
    2. Applica multi-start nearest neighbor
    3. Ottimizza con 2-opt, Or-opt, 3-opt usando la matrice
    4. Considera sempre il ritorno al deposito
    
    Args:
        punti: lista di punti da visitare
        deposito: punto di partenza E ritorno
        usa_osrm: se True, usa OSRM per distanze reali stradali
    
    Returns:
        dict con:
        - 'percorso': lista di punti ordinati
        - 'distanza_totale': distanza/durata totale
        - 'fonte': origine delle distanze usate
    """
    if not punti:
        return {'percorso': [], 'distanza_totale': 0, 'fonte': 'N/A'}
    
    # Importa qui per evitare import circolare
    from .distanze import ottieni_matrice_distanze
    
    n = len(punti)
    
    # Ottieni matrice distanze
    risultato_matrice = ottieni_matrice_distanze(punti, deposito, usa_osrm)
    
    # Importa configurazione per tipo di ottimizzazione
    try:
        from config import OTTIMIZZA_PER_TEMPO
    except ImportError:
        OTTIMIZZA_PER_TEMPO = True  # Default: ottimizza per tempo (preferisce autostrade)
    
    # Scegli la metrica di ottimizzazione:
    # - DURATE (tempo): preferisce percorsi più veloci (autostrade)
    # - DISTANZE (km): preferisce percorsi più corti
    # Per Torino: i tecnici usano l'autostrada, quindi preferiamo TEMPO
    if OTTIMIZZA_PER_TEMPO:
        matrice = risultato_matrice.get('durate') or risultato_matrice.get('distanze')
        metrica_usata = 'tempo (preferisce autostrade)'
    else:
        matrice = risultato_matrice.get('distanze') or risultato_matrice.get('durate')
        metrica_usata = 'distanza km'
    
    if not matrice or not risultato_matrice['successo']:
        # OSRM è obbligatorio - propaga l'errore
        raise RuntimeError(
            f"❌ ERRORE: Impossibile ottenere matrice distanze OSRM!\n"
            f"   Fonte: {risultato_matrice.get('fonte', 'N/A')}\n"
            "   Verifica che Docker con OSRM sia attivo."
        )
    
    # ================================================================
    # MULTI-START: prova diversi punti di partenza e tieni il migliore
    # Per dataset grandi, limita il numero di tentativi per velocità
    # ================================================================
    migliore_ordine = None
    migliore_costo = float('inf')
    
    # Prova partendo dal deposito (indice 0)
    ordine = nearest_neighbor_matrice(n, matrice, start_idx=None)
    
    # Ottimizza con 2-opt (usando matrice)
    if len(ordine) >= 4:
        ordine = two_opt_improve_matrice(ordine, matrice)
    
    costo = calcola_distanza_totale_matrice(ordine, matrice, include_ritorno=True)
    if costo < migliore_costo:
        migliore_costo = costo
        migliore_ordine = ordine
    
    # Per dataset grandi, limita il numero di punti di partenza da provare
    # n > 200: solo 5 punti campione
    # n > 100: solo 20 punti campione
    # n <= 100: tutti i punti
    if n > 200:
        # Solo 5 punti: primo, ultimo, e 3 equidistanti
        start_points = [1, n // 4, n // 2, 3 * n // 4, n]
    elif n > 100:
        # 20 punti equidistanti
        step = max(1, n // 20)
        start_points = list(range(1, n + 1, step))[:20]
    else:
        # Tutti i punti
        start_points = list(range(1, n + 1))
    
    # Prova i punti di partenza selezionati
    for start in start_points:
        ordine = nearest_neighbor_matrice(n, matrice, start_idx=start)
        
        if len(ordine) >= 4:
            ordine = two_opt_improve_matrice(ordine, matrice)
        
        costo = calcola_distanza_totale_matrice(ordine, matrice, include_ritorno=True)
        if costo < migliore_costo:
            migliore_costo = costo
            migliore_ordine = ordine
    
    # ================================================================
    # OTTIMIZZAZIONI AVANZATE sul percorso migliore
    # Per dataset grandi (>200 punti), salta le ottimizzazioni lente
    # ================================================================
    if migliore_ordine and len(migliore_ordine) >= 4:
        # Per dataset grandi, limita le ottimizzazioni
        if n <= 200:
            # Or-opt
            migliore_ordine = or_opt_improve_matrice(migliore_ordine, matrice)
            
            # 3-opt (se abbastanza punti e non troppi)
            if 6 <= len(migliore_ordine) <= 100:
                migliore_ordine = three_opt_improve_matrice(migliore_ordine, matrice)
            
            # Ultimo passaggio 2-opt
            migliore_ordine = two_opt_improve_matrice(migliore_ordine, matrice, max_iterazioni=50)
        else:
            # Per n > 200: solo un rapido 2-opt finale
            migliore_ordine = two_opt_improve_matrice(migliore_ordine, matrice, max_iterazioni=5)
    
    # Ricostruisci percorso con i punti originali
    percorso_finale = [punti[i - 1] for i in migliore_ordine]  # -1 perché indici sono 1-based
    
    # Calcola distanza totale finale (in km se abbiamo distanze)
    distanza_km = None
    if risultato_matrice.get('distanze'):
        distanza_km = calcola_distanza_totale_matrice(
            migliore_ordine, 
            risultato_matrice['distanze'], 
            include_ritorno=True
        )
    
    return {
        'percorso': percorso_finale,
        'distanza_totale': migliore_costo,
        'distanza_km': distanza_km,
        'fonte': risultato_matrice['fonte']
    }


def ordina_percorso(punti, deposito, usa_osrm_ottimizzazione=True):
    """
    Ordina i punti per creare un percorso ottimizzato (TSP circolare).
    
    NUOVA LOGICA con distanze stradali reali:
    1. Se non ci sono appuntamenti: usa ottimizzazione TSP con matrice OSRM
    2. Se ci sono appuntamenti: usa ancore temporali + nearest neighbor ottimizzato
    3. Considera SEMPRE il ritorno al deposito nel calcolo
    
    Parametri:
    - Inizio giornata: da config (Torino: 9:00, Pavia: 8:00)
    - Tempo per intervento: 20 minuti
    - Velocità media: 35 km/h
    
    Args:
        punti: lista di punti da visitare
        deposito: punto di partenza E RITORNO
        usa_osrm_ottimizzazione: se True, usa OSRM per distanze reali (default: True)
    """
    if not punti:
        return []
    
    # Costanti per stima tempi
    ORA_INIZIO = ORA_INIZIO_GIORNATA  # Da config (Torino: 9:00, Pavia: 8:00)
    TEMPO_INTERVENTO = 20  # minuti per contatore
    VELOCITA_MEDIA = 35  # km/h
    
    # Arricchisci punti con info fascia
    for p in punti:
        if p.get('IS_APPUNTAMENTO'):
            # Prima prova con NOTE_APP_RAW (dati originali dal data_loader)
            ora_min, ora_max = estrai_orario_e_fascia(p.get('NOTE_APP_RAW', ''))
            # Se non ha trovato orario, prova con ORA_APPUNTAMENTO (dati già processati)
            if ora_min is None:
                ora_min, ora_max = estrai_orario_e_fascia(p.get('ORA_APPUNTAMENTO', ''))
            p['_ORA_MIN'] = ora_min
            p['_ORA_MAX'] = ora_max
        else:
            p['_ORA_MIN'] = None
            p['_ORA_MAX'] = None
    
    # Separa le categorie:
    # 1. App con orario NORMALE (prima di ORA_LIMITE_SERALE)
    # 2. App con orario SERALE (dopo ORA_LIMITE_SERALE) -> vanno alla fine
    # 3. Punti base (contatori normali + app senza orario)
    app_con_orario = []
    app_serali = []  # Appuntamenti dopo le 17:00 (Torino) o 15:00 (Pavia)
    punti_base = []  # Contatori normali + app senza orario
    
    for p in punti:
        if p.get('IS_APPUNTAMENTO') and p['_ORA_MIN'] is not None:
            # Appuntamento con orario: verifica se è serale
            if p['_ORA_MIN'] >= ORA_LIMITE_SERALE:
                app_serali.append(p)
            else:
                app_con_orario.append(p)
        else:
            punti_base.append(p)
    
    # Ordina app con orario (normali):
    # 1. Per INIZIO della fascia (prima chi inizia prima)
    # 2. A parità di inizio, per DURATA FINESTRA (prima le finestre più strette)
    app_con_orario.sort(key=lambda x: (x['_ORA_MIN'], (x['_ORA_MAX'] or x['_ORA_MIN']) - x['_ORA_MIN']))
    
    # Ordina anche app serali per orario
    app_serali.sort(key=lambda x: (x['_ORA_MIN'], (x['_ORA_MAX'] or x['_ORA_MIN']) - x['_ORA_MIN']))

    # Assegna orario appuntamento (mostra fascia se presente)
    for app in app_con_orario:
        ora_min = app['_ORA_MIN']
        ora_max = app['_ORA_MAX']
        if ora_min == ora_max:
            app['ORA_APPUNTAMENTO'] = minuti_to_orario(ora_min)
        else:
            app['ORA_APPUNTAMENTO'] = f"{minuti_to_orario(ora_min)}-{minuti_to_orario(ora_max)}"
        # Target = inizio fascia, tolleranza fino a fine fascia
        app['_ORA_TARGET'] = ora_min
        app['_ORA_MAX_TOLLERANZA'] = ora_max
    
    # Per app senza orario
    for p in punti_base:
        if p.get('IS_APPUNTAMENTO'):
            p['ORA_APPUNTAMENTO'] = None
    
    # ================================================================
    # LOGICA PRINCIPALE: con o senza appuntamenti
    # ================================================================
    
    if not app_con_orario:
        # ==============================================================
        # CASO 1: Nessun appuntamento con orario
        # Usa ottimizzazione PER ZONE: raggruppa contatori vicini e completa
        # ogni zona prima di passare alla successiva (evita saltelli)
        # ==============================================================
        if not USA_OSRM:
            raise RuntimeError(
                "❌ ERRORE: OSRM è obbligatorio ma USA_OSRM è False in config.py!\n"
                "   Imposta USA_OSRM = True e avvia Docker con OSRM."
            )

        if len(punti_base) >= 3:
            # NUOVA LOGICA: ottimizzazione per micro-zone geografiche
            # - Raggruppa contatori entro RAGGIO_MICROZONA_KM (default 500m)
            # - Ordina le zone usando OSRM (autostrade tra zone)
            # - Completa ogni zona prima di passare alla successiva
            risultato = ottimizza_percorso_per_zone(punti_base, deposito, usa_osrm=True, raggio_zona_km=RAGGIO_MICROZONA_KM)
            percorso_finale = risultato['percorso']
        else:
            # Con meno di 3 punti, nearest neighbor è sufficiente (non serve matrice)
            percorso_finale = nearest_neighbor_order(punti_base, deposito, applica_2opt=False)
    else:
        # ==============================================================
        # CASO 2: Ci sono appuntamenti con orario
        # Usa ancore temporali + nearest neighbor negli spazi
        # ==============================================================
        percorso_finale = []
        contatori_rimanenti = punti_base.copy()
        posizione_corrente = deposito
        orario_corrente = ORA_INIZIO

        for idx_app, app in enumerate(app_con_orario):
            orario_target = app['_ORA_TARGET']

            # Calcola la tolleranza massima, considerando il prossimo appuntamento
            ora_max_tolleranza = app.get('_ORA_MAX_TOLLERANZA', orario_target + 15)

            # Se c'è un prossimo appuntamento, la tolleranza non può superare
            # l'orario necessario per arrivarci in tempo
            if idx_app + 1 < len(app_con_orario):
                next_app = app_con_orario[idx_app + 1]
                dist_to_next = calcola_distanza_haversine(
                    app['Lat'], app['Lon'],
                    next_app['Lat'], next_app['Lon']
                )
                tempo_to_next = (dist_to_next / VELOCITA_MEDIA) * 60
                # Devo finire questo app in tempo per arrivare al prossimo
                ora_limite_per_next = next_app['_ORA_TARGET'] - tempo_to_next - TEMPO_INTERVENTO
                ora_max_tolleranza = min(ora_max_tolleranza, ora_limite_per_next)

            # Calcola quanto tempo ci vuole per raggiungere l'appuntamento
            dist_app = calcola_distanza_haversine(
                posizione_corrente['Lat'], posizione_corrente['Lon'],
                app['Lat'], app['Lon']
            )
            tempo_viaggio_app = (dist_app / VELOCITA_MEDIA) * 60  # minuti

            # Orario limite: devo partire per l'appuntamento
            orario_partenza_per_app = orario_target - tempo_viaggio_app

            # Riempi il tempo disponibile con contatori usando nearest neighbor
            while contatori_rimanenti:
                # Trova il contatore più vicino
                min_dist = float('inf')
                idx_min = -1
                for i, c in enumerate(contatori_rimanenti):
                    dist = calcola_distanza_haversine(
                        posizione_corrente['Lat'], posizione_corrente['Lon'],
                        c['Lat'], c['Lon']
                    )
                    if dist < min_dist:
                        min_dist = dist
                        idx_min = i

                if idx_min < 0:
                    break

                # Calcola se c'è tempo per questo contatore prima dell'appuntamento
                tempo_viaggio = (min_dist / VELOCITA_MEDIA) * 60
                orario_arrivo_contatore = orario_corrente + tempo_viaggio
                orario_fine_contatore = orario_arrivo_contatore + TEMPO_INTERVENTO

                # Calcola tempo per andare dal contatore all'appuntamento
                contatore = contatori_rimanenti[idx_min]
                dist_cont_app = calcola_distanza_haversine(
                    contatore['Lat'], contatore['Lon'],
                    app['Lat'], app['Lon']
                )
                tempo_cont_app = (dist_cont_app / VELOCITA_MEDIA) * 60

                # Posso fare questo contatore e arrivare in tempo all'appuntamento?
                orario_arrivo_app = orario_fine_contatore + tempo_cont_app

                if orario_arrivo_app <= ora_max_tolleranza:
                    # Sì, aggiungi il contatore
                    percorso_finale.append(contatori_rimanenti.pop(idx_min))
                    posizione_corrente = contatore
                    orario_corrente = orario_fine_contatore
                else:
                    # No, è ora di andare all'appuntamento
                    break

            # Aggiungi l'appuntamento
            percorso_finale.append(app)
            posizione_corrente = app
            orario_corrente = orario_target + TEMPO_INTERVENTO
        
        # ==============================================================
        # Dopo l'ultimo appuntamento: ottimizza i contatori rimanenti
        # considerando il RITORNO AL DEPOSITO (OSRM obbligatorio)
        # ==============================================================
        if contatori_rimanenti:
            if len(contatori_rimanenti) >= 3:
                # Usa ottimizzazione TSP per i rimanenti
                # Nota: qui consideriamo che partiamo dalla posizione_corrente
                # e dobbiamo tornare al deposito
                risultato = ottimizza_percorso_tsp_circolare(
                    contatori_rimanenti,
                    deposito,  # Il deposito è comunque la destinazione finale
                    usa_osrm=True
                )
                rimanenti_ordinati = risultato['percorso']
            else:
                # Con meno di 3 punti, nearest neighbor è sufficiente
                rimanenti_ordinati = nearest_neighbor_order(contatori_rimanenti, posizione_corrente, applica_2opt=False)

            percorso_finale.extend(rimanenti_ordinati)
            if rimanenti_ordinati:
                posizione_corrente = rimanenti_ordinati[-1]
    
    # ================================================================
    # APPUNTAMENTI SERALI: aggiungi alla fine del percorso
    # (Torino: dopo le 17:00, Pavia: dopo le 15:00)
    # ================================================================
    if app_serali:
        # Assegna orario appuntamento per i serali
        for app in app_serali:
            ora_min = app['_ORA_MIN']
            ora_max = app['_ORA_MAX']
            if ora_min == ora_max:
                app['ORA_APPUNTAMENTO'] = minuti_to_orario(ora_min)
            else:
                app['ORA_APPUNTAMENTO'] = f"{minuti_to_orario(ora_min)}-{minuti_to_orario(ora_max)}"
        
        # Aggiungi gli appuntamenti serali in ordine di orario
        percorso_finale.extend(app_serali)
    
    # Pulisci campi temporanei
    for p in percorso_finale:
        p.pop('_ORA_MIN', None)
        p.pop('_ORA_MAX', None)
        p.pop('_ORA_TARGET', None)
        p.pop('_ORA_MAX_TOLLERANZA', None)
    
    return percorso_finale


def stima_orario_arrivo(percorso, posizione, deposito, ora_inizio, tempo_intervento, velocita):
    """
    Stima l'orario di arrivo alla posizione indicata del percorso.
    
    Args:
        percorso: lista di punti già ordinati
        posizione: indice della posizione (0 = prima tappa)
        deposito: punto di partenza
        ora_inizio: orario inizio giornata in minuti (es. 480 = 8:00)
        tempo_intervento: minuti per ogni intervento
        velocita: km/h media
    
    Returns:
        Orario stimato in minuti dall'inizio del giorno
    """
    orario = ora_inizio
    punto_precedente = deposito
    
    for i in range(posizione):
        if i < len(percorso):
            punto = percorso[i]
            # Tempo di spostamento
            dist = calcola_distanza_haversine(
                punto_precedente['Lat'], punto_precedente['Lon'],
                punto['Lat'], punto['Lon']
            )
            tempo_spostamento = (dist / velocita) * 60  # in minuti
            orario += tempo_spostamento
            # Tempo intervento
            orario += tempo_intervento
            punto_precedente = punto
    
    return orario


def stima_orario_arrivo_con_punto(percorso, posizione, nuovo_punto, deposito, ora_inizio, tempo_intervento, velocita):
    """
    Stima l'orario di arrivo a un nuovo punto se inserito alla posizione indicata.
    
    Args:
        percorso: lista di punti già ordinati
        posizione: indice dove inserire il nuovo punto (0 = prima tappa)
        nuovo_punto: il punto da inserire (con Lat, Lon)
        deposito: punto di partenza
        ora_inizio: orario inizio giornata in minuti (es. 480 = 8:00)
        tempo_intervento: minuti per ogni intervento
        velocita: km/h media
    
    Returns:
        Orario stimato di arrivo al nuovo punto, in minuti dall'inizio del giorno
    """
    orario = ora_inizio
    punto_precedente = deposito
    
    # Calcola tempo per arrivare alla posizione PRIMA di dove inseriamo
    for i in range(posizione):
        if i < len(percorso):
            punto = percorso[i]
            # Tempo di spostamento
            dist = calcola_distanza_haversine(
                punto_precedente['Lat'], punto_precedente['Lon'],
                punto['Lat'], punto['Lon']
            )
            tempo_spostamento = (dist / velocita) * 60  # in minuti
            orario += tempo_spostamento
            # Tempo intervento
            orario += tempo_intervento
            punto_precedente = punto
    
    # Ora aggiungi il tempo di spostamento verso il nuovo punto
    dist_verso_nuovo = calcola_distanza_haversine(
        punto_precedente['Lat'], punto_precedente['Lon'],
        nuovo_punto['Lat'], nuovo_punto['Lon']
    )
    tempo_verso_nuovo = (dist_verso_nuovo / velocita) * 60
    orario += tempo_verso_nuovo
    
    return orario


# Alias per retrocompatibilità
def ordina_per_fasce_e_vicinanza(punti, deposito):
    """Alias per retrocompatibilità. Usa OSRM locale per distanze reali."""
    return ordina_percorso(punti, deposito, usa_osrm_ottimizzazione=True)