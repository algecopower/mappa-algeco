# -*- coding: utf-8 -*-
"""
Modulo per la gestione delle Distinte Base.

Funzionalità:
- Parsing di file PDF DistintaBase
- Estrazione dati utenti (nome, telefono, indirizzo, data intervento)
- Classificazione numeri (cellulare vs fisso)
- Generazione messaggi WhatsApp
- Confronto dati distinta vs filone
"""
import re
import logging
import pdfplumber
from datetime import datetime
from typing import List, Dict, Optional, Tuple
import os

logger = logging.getLogger('mappa_algeco')


def is_cellulare(numero: str) -> bool:
    """
    Verifica se un numero di telefono è un cellulare italiano (WhatsApp).
    
    Prefissi cellulari italiani:
    - 3xx (320-339, 340-349, 360-369, 380-389, 390-399)
    
    Gestisce anche numeri con 0 erroneamente aggiunto davanti (es. 03358311197)
    """
    if not numero:
        return False
    
    # Rimuovi spazi, trattini, punti e +
    cleaned = re.sub(r'[\s\-\.\(\)\+]', '', numero)
    
    # Rimuovi prefisso internazionale se presente
    if cleaned.startswith('0039'):
        cleaned = cleaned[4:]
    elif cleaned.startswith('39') and len(cleaned) > 10:
        cleaned = cleaned[2:]
    
    if len(cleaned) < 9:
        return False
    
    # Prefissi cellulari italiani (tutti iniziano con 3)
    if cleaned.startswith('3'):
        prefix = int(cleaned[:3])
        # Range prefissi cellulari: 320-399
        if 320 <= prefix <= 399:
            return True
    
    # Controlla se è un cellulare con 0 erroneamente aggiunto davanti
    # Es: 03358311197 -> dopo rimozione 0 diventa 3358311197 (10 cifre = cellulare valido)
    if cleaned.startswith('0') and len(cleaned) >= 10:
        senza_zero = cleaned[1:]
        # Verifica se senza lo zero iniziale è un cellulare valido
        # Un cellulare italiano ha tipicamente 10 cifre (3xx + 7 cifre)
        if senza_zero.startswith('3') and len(senza_zero) == 10:
            prefix = int(senza_zero[:3])
            if 320 <= prefix <= 399:
                return True
    
    return False


def numeri_telefono_uguali(num1: str, num2: str) -> bool:
    """
    Confronta due numeri di telefono tollerando lo 0 iniziale mancante.
    Gestisce la formattazione di Excel/Sheets che taglia lo 0 dai numeri fissi.
    Es: '1234567890' e '01234567890' sono considerati uguali.
    """
    if not num1 or not num2:
        return False
    # Rimuovi tutto tranne le cifre
    n1 = re.sub(r'[^\d]', '', num1)
    n2 = re.sub(r'[^\d]', '', num2)
    if n1 == n2:
        return True
    # Uno dei due potrebbe avere lo 0 iniziale mancante
    if n1 == '0' + n2 or n2 == '0' + n1:
        return True
    # Rimuovi eventuale prefisso internazionale 39
    if n1.startswith('39') and len(n1) > 10:
        n1 = n1[2:]
    if n2.startswith('39') and len(n2) > 10:
        n2 = n2[2:]
    if n1 == n2:
        return True
    if n1 == '0' + n2 or n2 == '0' + n1:
        return True
    return False


def is_numero_valido(numero: str) -> bool:
    """Verifica se una stringa è un numero di telefono valido."""
    if not numero:
        return False
    
    # Rimuovi spazi, trattini, punti
    cleaned = re.sub(r'[\s\-\.\(\)\+]', '', numero)
    
    # Deve contenere solo cifre
    if not cleaned.isdigit():
        return False
    
    # Lunghezza minima ragionevole
    if len(cleaned) < 8:
        return False
    
    return True


def estrai_tutti_telefoni_da_testo(testo: str) -> List[str]:
    """
    Cerca ed estrae TUTTI i numeri di telefono da un testo generico.
    Utile quando un campo contiene più numeri (es. "333 1234567 / 011 9876543").
    
    Args:
        testo: Stringa in cui cercare i numeri
        
    Returns:
        Lista di numeri trovati (solo cifre), senza duplicati
    """
    if not testo:
        return []
    
    numeri = []
    
    patterns = [
        r'\b3[0-9]{2}[\s\-\.]*[0-9]{1,4}[\s\-\.]*[0-9]{1,4}(?:[\s\-\.]*[0-9]{1,4})?\b',
        r'\b0[0-9]{1,3}[\s\-\.]*[0-9]{1,4}[\s\-\.]*[0-9]{1,4}(?:[\s\-\.]*[0-9]{1,4})?\b',
        r'\+39[\s\-\.]*[0-9]{1,4}[\s\-\.]*[0-9]{1,4}[\s\-\.]*[0-9]{1,4}(?:[\s\-\.]*[0-9]{1,4})?',
        r'\b39[\s\-\.]*3[0-9]{2}[\s\-\.]*[0-9]{1,4}[\s\-\.]*[0-9]{1,4}(?:[\s\-\.]*[0-9]{1,4})?\b',
        r'\b39[\s\-\.]*0[0-9]{1,3}[\s\-\.]*[0-9]{1,4}[\s\-\.]*[0-9]{1,4}(?:[\s\-\.]*[0-9]{1,4})?\b',
        r'\b[0-9]{10,13}\b',
    ]
    
    for pattern in patterns:
        for match in re.finditer(pattern, testo):
            numero = re.sub(r'[^\d]', '', match.group())
            if len(numero) >= 9:
                numero = normalizza_numero_estratto(numero)
                if numero and not any(numeri_telefono_uguali(numero, n) for n in numeri):
                    numeri.append(numero)
    
    return numeri


def normalizza_numero_estratto(numero: str) -> Optional[str]:
    """
    Normalizza un numero di telefono estratto da testo.
    Rimuove il prefisso internazionale 39/0039 se presente.
    
    Args:
        numero: Stringa di sole cifre
        
    Returns:
        Numero normalizzato o None se non valido
    """
    if not numero:
        return None
    
    # Rimuovi prefisso 0039
    if numero.startswith('0039'):
        numero = numero[4:]
    # Rimuovi prefisso 39 se il numero è lungo abbastanza (>= 12 cifre con prefisso)
    elif numero.startswith('39') and len(numero) >= 12:
        numero = numero[2:]
    
    # Verifica lunghezza ragionevole (9-11 cifre per numeri italiani)
    if len(numero) < 9 or len(numero) > 11:
        return None
    
    return numero


def formatta_numero_whatsapp(numero: str) -> str:
    """
    Formatta un numero per WhatsApp (formato internazionale).
    Restituisce il numero con prefisso +39 senza spazi.
    Gestisce anche numeri con 0 erroneamente aggiunto davanti.
    """
    if not numero:
        return ''
    
    # Rimuovi tutti i caratteri non numerici
    cleaned = re.sub(r'[^\d]', '', numero)
    
    # Rimuovi il prefisso 0039 o 39 se presente all'inizio
    if cleaned.startswith('0039'):
        cleaned = cleaned[4:]
    elif cleaned.startswith('39') and len(cleaned) > 10:
        cleaned = cleaned[2:]
    
    # Se inizia con 0 e dopo c'è un 3 (possibile cellulare con 0 erroneo)
    # e la lunghezza senza lo 0 è 10 (tipica lunghezza cellulare), rimuovi lo 0
    if cleaned.startswith('0') and len(cleaned) == 11:
        senza_zero = cleaned[1:]
        if senza_zero.startswith('3'):
            prefix = int(senza_zero[:3])
            if 320 <= prefix <= 399:
                cleaned = senza_zero
    
    # Aggiungi il prefisso internazionale
    return f'+39{cleaned}'


def genera_messaggio_whatsapp(
    nome: str,
    indirizzo: str,
    civico: str,
    comune: str,
    data: str,
    provincia: str = 'TORINO'
) -> str:
    """
    Genera il messaggio WhatsApp personalizzato.
    
    Args:
        nome: Nome del cliente
        indirizzo: Via/indirizzo
        civico: Numero civico
        comune: Comune
        data: Data intervento
        provincia: 'TORINO' o 'PAVIA' (per l'email corretta)
    """
    # Email tecnico basata sulla provincia
    if provincia.upper() == 'PAVIA':
        email = 'tecnico_pv1@algecopower.it'
    else:
        email = 'tecnico_to2@algecopower.it'
    
    # Pulisci e formatta i dati
    nome = nome.strip().title() if nome else 'Cliente'
    indirizzo = indirizzo.strip() if indirizzo else ''
    civico = civico.strip() if civico else ''
    comune = comune.strip().title() if comune else ''
    
    # Costruisci l'indirizzo completo
    indirizzo_completo = f"{indirizzo} {civico}".strip()
    
    messaggio = f"""Gentile {nome}
vi contattiamo per confermare la sostituzione del vostro contatore elettrico a Via {indirizzo_completo} nel comune di {comune}, come da comunicazione inviata a mezzo posta, prevista per il giorno:

{data}

Ricordiamo che la manutenzione è richiesta da E-distribuzione ed è a titolo gratuito. Per maggiori informazioni, potete visitare il seguente link:
https://www.e-distribuzione.it/open-meter.html

Chiediamo di rispondere a questo messaggio per confermare la presa visione dello stesso.
Per eventuali chiarimenti o assistenza, vi invitiamo a contattarci a questo numero o a mandarci una mail all'indirizzo {email}

Cordiali saluti,
Algeco Power"""
    
    return messaggio


def parse_distinta_pdf(pdf_path: str) -> List[Dict]:
    """
    Estrae i dati degli utenti da un file PDF DistintaBase.
    
    Args:
        pdf_path: Percorso al file PDF
        
    Returns:
        Lista di dizionari con i dati estratti per ogni lavoro
    """
    risultati = []
    
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                # Prima prova ad estrarre come tabella (più preciso)
                lavori = estrai_lavori_da_tabella(page)
                if lavori:
                    risultati.extend(lavori)
                else:
                    # Fallback al metodo testo
                    text = page.extract_text()
                    if text:
                        lavori = estrai_lavori_da_testo(text)
                        risultati.extend(lavori)
                
    except Exception as e:
        logger.error(f"[DISTINTA] Errore parsing PDF: {e}")
        raise
    
    return risultati


def estrai_lavori_da_tabella(page) -> List[Dict]:
    """
    Estrae i lavori usando le coordinate delle parole nel PDF.
    
    Il PDF ha colonne con queste coordinate X:
    - Cliente: valore a x > 60
    - Indirizzo: valore a x > 275
    - N° (civico): valore a x > 435
    - S.E.P.I.: x > 500 (da ignorare)
    
    I valori di ogni riga sono sulla stessa coordinata Y.
    """
    lavori = []
    
    try:
        # Estrai tutte le parole con le loro coordinate
        words = page.extract_words(keep_blank_chars=True, x_tolerance=3, y_tolerance=3)
        if not words:
            return []
        
        text = page.extract_text() or ''
        
        # Trova tutti i lavori dal testo (AG o AE seguito da numero e N° lavoro)
        pattern = re.compile(
            r'([A-Z][A-Z\s]+)\s+(A[EG]\d+)\s+(\d+)\s+(\d+)\s*\n'
            r'([A-Za-z]+)\s+(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2})',
            re.MULTILINE
        )
        
        matches = list(pattern.finditer(text))
        
        # Per ogni lavoro trovato, cerca i dati usando le coordinate
        for i, match in enumerate(matches):
            num_lavoro = int(match.group(3))
            data_intervento = match.group(6)
            
            # Trova la riga con "Cliente:" per questo lavoro
            # Ogni blocco lavoro ha un'etichetta "Cliente:" 
            # La Y di Cliente: per il lavoro 1 è ~368, per lavoro 2 è ~649 (differenza ~280)
            
            # Cerca l'etichetta "Cliente:" corrispondente a questo lavoro
            cliente_labels = [w for w in words if w['text'].strip() == 'Cliente:']
            
            if i < len(cliente_labels):
                cliente_label = cliente_labels[i]
                row_y = cliente_label['top']
                
                # Estrai i valori dalla stessa riga (Y simile, ±3 pixel)
                row_words = [w for w in words if abs(w['top'] - row_y) < 5]
                row_words.sort(key=lambda w: w['x0'])
                
                # Coordinate delle colonne (dal PDF analizzato)
                # Cliente: x 61-240
                # Indirizzo: x 276-420
                # Civico: x 435-500
                # SEPI: x > 500
                
                nome_parts = []
                indirizzo_parts = []
                civico = ''
                
                for w in row_words:
                    txt = w['text'].strip()
                    x = w['x0']
                    
                    # Salta etichette
                    if txt in ['Cliente:', 'Indirizzo:', 'N°:', 'S.', 'P.', 'I.']:
                        continue
                    
                    # Classifica in base alla posizione X
                    if 60 < x < 275:
                        nome_parts.append(txt)
                    elif 275 <= x < 420:
                        indirizzo_parts.append(txt)
                    elif 430 < x < 510:
                        civico = txt
                    # x > 510 è SEPI, ignorato
                
                nome = ' '.join(nome_parts)
                indirizzo = ' '.join(indirizzo_parts)
                
                # Ora estrai gli altri dati (telefono, CAP, comune, POD, eneltel) dal testo
                start_pos = match.end()
                if i + 1 < len(matches):
                    end_pos = matches[i + 1].start()
                else:
                    end_pos = len(text)
                
                blocco = text[start_pos:end_pos]
                
                risultato = {
                    'lavoro': num_lavoro,
                    'nome': nome,
                    'telefono': '',
                    'indirizzo': indirizzo,
                    'civico': civico,
                    'comune': '',
                    'cap': '',
                    'data_intervento': data_intervento,
                    'pod': '',
                    'eneltel': '',
                }
                
                # POD (formato standard IT001E... o fotovoltaico ITP0AE...)
                pod_match = re.search(r'(IT[A-Z0-9]{3}[A-Z]\d{8,})', blocco)
                if pod_match:
                    risultato['pod'] = pod_match.group(1)
                
                # Telefono - cerca prima il pattern con etichetta "Tel.:" o "Tel:"
                tel_label_pattern = re.search(
                    r'Tel\.?:\s*(\d{8,11})',
                    blocco,
                    re.IGNORECASE
                )
                if tel_label_pattern:
                    risultato['telefono'] = tel_label_pattern.group(1)
                
                # Cerca pattern CAP+Comune nel blocco di testo
                # Pattern: "3331572577 10024 MONCALIERI D000BO9" (con telefono)
                # oppure: "10024 MONCALIERI D000BO9" (senza telefono)
                # Nota: non usa ^ per gestire linee con etichette (es. "Tel.: 10024 ...")
                cap_comune_pattern = re.search(
                    r'(?:^|\s)((?:3\d{8,9})|(?:0\d{6,10}))?\s*(\d{5})\s+([A-Z][A-Z\s]+?)\s+([A-Z]\d{3}[A-Z0-9]{2,})',
                    blocco,
                    re.MULTILINE | re.IGNORECASE
                )
                if cap_comune_pattern:
                    if cap_comune_pattern.group(1) and not risultato['telefono']:
                        risultato['telefono'] = cap_comune_pattern.group(1)
                    risultato['cap'] = cap_comune_pattern.group(2)
                    risultato['comune'] = cap_comune_pattern.group(3).strip().upper()
                
                # ENELTEL (9 cifre + eventuale lettera A/B per fotovoltaici)
                # IMPORTANTE: cerchiamo DOPO la riga tel/cap/comune per evitare di
                # catturare numeri di telefono fisso a 9 cifre (es. 011234567)
                blocco_eneltel = blocco
                if cap_comune_pattern:
                    blocco_eneltel = blocco[cap_comune_pattern.end():]
                eneltel_pattern = re.search(r'\b(\d{9}[A-B]?)\b', blocco_eneltel)
                if eneltel_pattern:
                    risultato['eneltel'] = eneltel_pattern.group(1)
                
                if risultato['pod']:
                    lavori.append(risultato)
        
    except Exception as e:
        logger.error(f"[DISTINTA] Errore estrazione coordinate: {e}")
        import traceback
        traceback.print_exc()
    
    return lavori


def estrai_dati_da_blocco_v3(blocco: str, num_lavoro: int, data_intervento: str, page, words: list) -> Dict:
    """
    Estrae i dati usando le etichette del template PDF.
    Riutilizza la logica di estrai_dati_da_blocco_v2.
    """
    return estrai_dati_da_blocco_v2(blocco, num_lavoro, data_intervento)


def estrai_lavori_da_testo(text: str) -> List[Dict]:
    """
    Estrae i singoli lavori dal testo di una pagina PDF.
    Ogni pagina può contenere più lavori (template ripetuto).
    
    Il formato nel testo è:
    PREPOSTO AG##### N_LAVORO PRIORITA
    FORMAZIONE DATA ORA DISTANZA
    ...
    NOME_CLIENTE INDIRIZZO CIVICO [S/P/I]
    TELEFONO CAP COMUNE COD
    ENELTEL POSIZIONE ...
    POD ...
    """
    lavori = []
    
    # Pattern principale: cerca blocchi che iniziano con "AG" o "AE" seguito da numero
    # Il testo ha questa struttura ripetuta per ogni lavoro:
    # "KEITA SIDI AG18966 1 399" oppure "NOME AE101392 1 399"
    # dove: preposto=KEITA SIDI, matricola=AG18966/AE101392, n_lavoro=1, priorità=399
    
    # Trova tutti i match del pattern principale
    pattern = re.compile(
        r'([A-Z][A-Z\s]+)\s+(A[EG]\d+)\s+(\d+)\s+(\d+)\s*\n'  # Preposto, Matricola (AE o AG), N°lavoro, Priorità
        r'([A-Za-z]+)\s+'  # Formazione (es. "Keita")
        r'(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2})',  # Data e ora
        re.MULTILINE
    )
    
    matches = list(pattern.finditer(text))
    
    for i, match in enumerate(matches):
        num_lavoro = int(match.group(3))
        data_intervento = match.group(6)
        
        # Determina il blocco di testo per questo lavoro
        start_pos = match.end()
        if i + 1 < len(matches):
            end_pos = matches[i + 1].start()
        else:
            # Ultimo lavoro: prendi fino alla fine o al prossimo pattern "Preposto:"
            next_preposto = text.find('Preposto:', start_pos)
            end_pos = next_preposto if next_preposto > 0 else len(text)
        
        blocco = text[start_pos:end_pos]
        
        # Estrai dati dal blocco
        lavoro = estrai_dati_da_blocco_v2(blocco, num_lavoro, data_intervento)
        if lavoro and lavoro.get('pod'):
            lavori.append(lavoro)
    
    return lavori


def estrai_dati_da_blocco_v2(blocco: str, num_lavoro: int, data_intervento: str) -> Dict:
    """
    Estrae i dati da un blocco di testo di un singolo lavoro.
    
    Formato del blocco (dal PDF estratto):
    GIOVANNI CANAVESIO SANTA MARIA GORETTI 24 E        <- Nome + Indirizzo + Civico + SEPI
    3331572577 10024 MONCALIERI D000BO9                <- Tel + CAP + Comune + CodCliente
    019755355 Non conosciuta 000000000000 0000         <- Eneltel + PosMis + ...
    IT001E01975535                                     <- POD
    """
    risultato = {
        'lavoro': num_lavoro,
        'nome': '',
        'telefono': '',
        'indirizzo': '',
        'civico': '',
        'comune': '',
        'cap': '',
        'data_intervento': data_intervento,
        'pod': '',
        'eneltel': '',
    }
    
    # POD (formato standard IT001E01975535 o fotovoltaico ITP0AE...)
    pod_match = re.search(r'(IT[A-Z0-9]{3}[A-Z]\d{8,})', blocco)
    if pod_match:
        risultato['pod'] = pod_match.group(1)
    
    # Cerca PRIMA la riga con TELEFONO + CAP + COMUNE + CODICE CLIENTE
    # Pattern: "3331572577 10024 MONCALIERI D000BO9" (cellulare)
    # oppure: "0113456789 10024 MONCALIERI D000BO9" (fisso)
    # oppure senza telefono: "10024 MONCALIERI D000BO9"
    # Nota: il telefono può iniziare con 3 (cellulare) o 0 (fisso)
    # Nota: non usa ^ per gestire linee con etichette (es. "Tel.: 10024 ...")
    tel_cap_comune_pattern = re.search(
        r'(?:^|\s)((?:3\d{8,9})|(?:0\d{6,10}))?\s*(\d{5})\s+([A-Z][A-Z\s]+?)\s+([A-Z]\d{3}[A-Z0-9]{2,})',
        blocco,
        re.MULTILINE | re.IGNORECASE
    )
    
    riga_tel_cap = None
    if tel_cap_comune_pattern:
        if tel_cap_comune_pattern.group(1):
            risultato['telefono'] = tel_cap_comune_pattern.group(1)
        risultato['cap'] = tel_cap_comune_pattern.group(2)
        risultato['comune'] = tel_cap_comune_pattern.group(3).strip().upper()
        riga_tel_cap = tel_cap_comune_pattern.group(0)
    
    # ENELTEL (9 cifre + eventuale lettera A/B per fotovoltaici)
    # IMPORTANTE: cerchiamo DOPO la riga tel/cap/comune per evitare di
    # catturare numeri di telefono fisso a 9 cifre (es. 011234567)
    blocco_eneltel = blocco
    if tel_cap_comune_pattern:
        blocco_eneltel = blocco[tel_cap_comune_pattern.end():]
    eneltel_pattern = re.search(r'\b(\d{9}[A-B]?)\b', blocco_eneltel)
    if eneltel_pattern:
        risultato['eneltel'] = eneltel_pattern.group(1)
    
    # Ora cerca la riga PRIMA di quella con tel/cap/comune
    # Quella riga contiene: NOME + INDIRIZZO + CIVICO + SEPI
    lines = blocco.split('\n')
    
    for i, line in enumerate(lines):
        line_stripped = line.strip()
        
        # Salta righe vuote o troppo corte
        if len(line_stripped) < 5:
            continue
        
        # Cerca la riga con CAP (5 cifre all'inizio o dopo telefono)
        # Telefono può iniziare con 3 (cellulare) o 0 (fisso)
        if re.match(r'^((?:3\d{8,9})|(?:0\d{6,10}))?\s*(\d{5})\s+[A-Z]', line_stripped):
            # Questa è la riga con tel/cap/comune
            # La riga precedente dovrebbe avere nome + indirizzo
            if i > 0:
                prev_line = lines[i-1].strip()
                if len(prev_line) > 5 and not prev_line.startswith('RCMI') and not 'DISTINTA' in prev_line:
                    # Usa la funzione intelligente per nomi con 3+ parole
                    nome_ind = estrai_nome_indirizzo_intelligente(prev_line)
                    if not nome_ind:
                        nome_ind = estrai_nome_indirizzo_v3(prev_line)
                    if nome_ind:
                        risultato['nome'] = nome_ind.get('nome', '')
                        risultato['indirizzo'] = nome_ind.get('indirizzo', '')
                        risultato['civico'] = nome_ind.get('civico', '')
            break
    
    return risultato


def estrai_nome_indirizzo_v3(line: str) -> Optional[Dict]:
    """
    Estrae nome, indirizzo e civico da una riga nel formato:
    "GIOVANNI CANAVESIO SANTA MARIA GORETTI 24 E"
    """
    if not line or len(line) < 5:
        return None
    
    line = line.strip()
    
    # Step 1: Estrai civico e SEPI dalla fine
    # Prima rimuovi SEPI (lettera singola o doppia alla fine)
    sepi_match = re.search(r'\s+([A-Z]{1,2})$', line)
    if sepi_match:
        line = line[:sepi_match.start()].strip()
    
    # Ora estrai il civico (numero + eventuale suffisso)
    civico = ''
    civico_match = re.search(r'\s+(\d+(?:\s*(?:BIS|TER|QUATER))?(?:/[A-Z0-9]+)?)$', line, re.IGNORECASE)
    if civico_match:
        civico = civico_match.group(1).strip()
        line = line[:civico_match.start()].strip()
    
    if not line:
        return None
    
    # Step 2: Cerca prefissi via noti
    via_prefixes = [
        'VIA', 'CORSO', 'PIAZZA', 'STRADA', 'LARGO', 'VIALE', 
        'VICOLO', 'BORGATA', 'FRAZIONE', 'LOCALITA', 'REGIONE',
        'P.ZA', 'P.ZZA', 'V.LE', 'C.SO', 'STR.', 'LOC.',
        'SANTA', 'SANTO', 'SAN', "SANT'",
        'S.MARIA', 'S.GIOVANNI', 'S.PIETRO', 'S.PAOLO'
    ]
    
    words = line.split()
    
    # Cerca il primo prefisso via nella riga
    for i, word in enumerate(words):
        word_upper = word.upper().rstrip('.')
        
        # Controlla prefissi esatti
        if word_upper in via_prefixes or word_upper.rstrip('.') in ['S', 'V', 'C', 'P']:
            # Se troviamo un prefisso via, tutto prima è il nome
            if i > 0:
                nome = ' '.join(words[:i])
                indirizzo = ' '.join(words[i:])
                return {
                    'nome': nome,
                    'indirizzo': indirizzo,
                    'civico': civico
                }
    
    # Step 3: Fallback - prime 2 parole = nome, resto = indirizzo
    if len(words) > 2:
        return {
            'nome': ' '.join(words[:2]),
            'indirizzo': ' '.join(words[2:]),
            'civico': civico
        }
    elif len(words) == 2:
        return {
            'nome': words[0],
            'indirizzo': words[1],
            'civico': civico
        }
    else:
        return {
            'nome': line,
            'indirizzo': '',
            'civico': civico
        }


def estrai_nome_indirizzo_intelligente(line: str) -> Optional[Dict]:
    """
    Estrae nome, indirizzo e civico da una riga usando logica migliorata.
    
    La sfida è che nel PDF abbiamo una riga come:
    "LIUDMYLA LEONIDIVNA SHAPOVALOV CARIGNANO 54 TER TE"
    dove:
    - Nome: LIUDMYLA LEONIDIVNA SHAPOVALOV (3 parole)
    - Indirizzo: CARIGNANO (nome via senza prefisso "VIA")
    - Civico: 54 TER
    - S.E.P.I.: TE
    
    Strategia: cercare il civico (numero) partendo dalla fine,
    poi cercare pattern di nomi strade o usare euristica.
    """
    if not line or len(line) < 5:
        return None
    
    # Rimuovi label del template se presenti
    line = re.sub(r'^(Cliente:|Indirizzo:|N°:)\s*', '', line, flags=re.IGNORECASE)
    line = line.strip()
    
    # Lista di nomi di strade comuni (senza prefisso VIA) in Piemonte/Lombardia
    # Questi sono spesso nomi di luoghi, santi, personaggi storici
    nomi_strade_comuni = [
        'ROMA', 'TORINO', 'MILANO', 'ITALIA', 'GARIBALDI', 'MAZZINI', 'CAVOUR',
        'VITTORIO EMANUELE', 'DANTE', 'LEOPARDI', 'PETRARCA', 'MARCONI',
        'VERDI', 'CARDUCCI', 'MATTEOTTI', 'GRAMSCI', 'KENNEDY', 'EINAUDI',
        'CARIGNANO', 'SALUZZO', 'PINEROLO', 'RIVOLI', 'ORBASSANO', 'MONCALIERI',
        'COLLEGNO', 'GRUGLIASCO', 'CHIERI', 'SETTIMO', 'NICHELINO', 'BEINASCO',
        # Nomi di santi (comuni nelle strade)
        'SAN GIOVANNI', 'SANTA MARIA', 'SAN PAOLO', 'SAN PIETRO', 'SAN MARCO',
        'SANT\'ANNA', 'SAN LORENZO', 'SAN FRANCESCO', 'SAN GIUSEPPE',
    ]
    
    # Prima: estrai civico e S.E.P.I. dalla fine
    # Pattern: numero + eventuale suffisso (TER, BIS, A, /A) + eventuale lettera S.E.P.I.
    # Es: "54 TER TE" -> civico="54 TER", SEPI="TE"
    # Es: "24 E" -> civico="24", SEPI="E"
    # Es: "22" -> civico="22", SEPI=""
    
    civico_pattern = re.search(
        r'\s+(\d+(?:\s*(?:BIS|TER|QUATER|[A-Z]))?(?:/[A-Z0-9]+)?)'  # Civico
        r'(?:\s+([A-Z]{1,2}))?$',  # S.E.P.I. opzionale (1-2 lettere)
        line,
        re.IGNORECASE
    )
    
    civico = ''
    resto = line
    
    if civico_pattern:
        civico = civico_pattern.group(1).strip()
        resto = line[:civico_pattern.start()].strip()
    
    if not resto or len(resto) < 3:
        return None
    
    # Cerca prefissi via espliciti
    via_pattern = re.search(
        r'^(.+?)\s+(VIA|CORSO|PIAZZA|STRADA|LARGO|VIALE|V\.|C\.|P\.ZA|P\.ZZA|'
        r'S\.[A-Z]+|STR\.|VICOLO|BORGATA|FRAZ\.|LOCALITA\'?|REGIONE)\s+(.+)$',
        resto,
        re.IGNORECASE
    )
    
    if via_pattern:
        return {
            'nome': via_pattern.group(1).strip(),
            'indirizzo': f"{via_pattern.group(2)} {via_pattern.group(3)}".strip(),
            'civico': civico
        }
    
    # Cerca nomi di strade comuni (senza prefisso)
    words = resto.split()
    
    for street_name in nomi_strade_comuni:
        street_words = street_name.split()
        num_street_words = len(street_words)
        
        # Cerca la strada nelle ultime parole del resto (prima del civico)
        for i in range(len(words) - num_street_words, 0, -1):
            candidate = ' '.join(words[i:i+num_street_words]).upper()
            if candidate == street_name:
                nome = ' '.join(words[:i])
                indirizzo = ' '.join(words[i:])
                if nome:
                    return {
                        'nome': nome,
                        'indirizzo': indirizzo,
                        'civico': civico
                    }
    
    # Euristica finale: se ci sono più di 2 parole,
    # cerca la prima parola che potrebbe essere un nome di via
    # (parola singola maiuscola che non sembra un nome/cognome tipico)
    if len(words) > 2:
        # I nomi propri italiani spesso finiscono in -O, -A, -I, -E
        # I nomi di vie spesso sono nomi di città o terminano in -ANO, -INO, ecc.
        for i in range(2, len(words)):
            word = words[i].upper()
            # Se la parola sembra un nome di luogo (finisce in -ANO, -INO, -ALE, -ETTO, -ELLO)
            # o è TUTTO maiuscolo e non sembra un cognome comune
            if (word.endswith(('ANO', 'INO', 'ALE', 'ETTO', 'ELLO', 'AGNA', 'ENZA', 'ANZA')) and 
                len(word) > 4):
                nome = ' '.join(words[:i])
                indirizzo = ' '.join(words[i:])
                return {
                    'nome': nome,
                    'indirizzo': indirizzo,
                    'civico': civico
                }
    
    # Default: prime 2 parole = nome, resto = indirizzo (comportamento originale)
    if len(words) > 2:
        return {
            'nome': ' '.join(words[:2]),
            'indirizzo': ' '.join(words[2:]),
            'civico': civico
        }
    elif len(words) == 2:
        return {
            'nome': words[0],
            'indirizzo': words[1],
            'civico': civico
        }
    
    return None


def estrai_nome_indirizzo_da_riga(line: str) -> Optional[Dict]:
    """
    Estrae nome, indirizzo e civico da una riga.
    
    Esempi:
    - "GIOVANNI CANAVESIO SANTA MARIA GORETTI 24 E"
    - "CLAUDIO MANZONE SALUZZO 22"
    - "KLARA TRAJKOVIC S.MARIA 42"
    """
    if not line or len(line) < 5:
        return None
    
    # Rimuovi label del template se presenti
    line = re.sub(r'^(Cliente:|Indirizzo:|N°:)\s*', '', line, flags=re.IGNORECASE)
    line = line.strip()
    
    # Pattern per estrarre civico alla fine (numero opzionalmente seguito da lettera attaccata o /lettera)
    # Es: "24", "24A", "24/A", "22"
    # La lettera singola separata da spazio è il valore S.E.P.I. (Scala/Edificio/Piano/Interno) e viene ignorata
    # Es: "24 E" -> civico="24", la "E" è S.E.P.I.
    civico_pattern = re.search(r'\s+(\d+[A-Z]?(?:/[A-Z0-9]+)?)\s*(?:\s+[A-Z])?$', line)
    
    civico = ''
    resto = line
    
    if civico_pattern:
        civico = civico_pattern.group(1)
        # Rimuovi il civico (e eventuale lettera S.E.P.I.) dalla riga per avere nome + indirizzo
        resto = line[:civico_pattern.start()].strip()
    
    # Se non c'è civico ma la riga ha contenuto, proviamo comunque a estrarre nome/indirizzo
    if not resto or len(resto) < 3:
        return None
    
    # Ora dividi nome da indirizzo
    # L'indirizzo di solito inizia con: VIA, CORSO, PIAZZA, S., V., C., etc.
    via_pattern = re.search(
        r'^(.+?)\s+(VIA|CORSO|PIAZZA|STRADA|LARGO|VIALE|V\.|C\.|P\.ZA|S\.[A-Z]+|STRADA|STR\.|VICOLO)\s+(.+)$',
        resto,
        re.IGNORECASE
    )
    
    if via_pattern:
        return {
            'nome': via_pattern.group(1).strip(),
            'indirizzo': f"{via_pattern.group(2)} {via_pattern.group(3)}".strip(),
            'civico': civico
        }
    else:
        # Non c'è prefisso via esplicito
        # Prova a dividere: prime 2 parole = nome, resto = indirizzo
        words = resto.split()
        if len(words) >= 2:
            # Se c'è un "." nel potenziale indirizzo, probabilmente è abbreviazione
            # es: "S.MARIA" -> indirizzo
            for i in range(1, len(words)):
                word = words[i]
                if '.' in word or word.upper() in ['VIA', 'CORSO', 'PIAZZA', 'STRADA']:
                    return {
                        'nome': ' '.join(words[:i]),
                        'indirizzo': ' '.join(words[i:]),
                        'civico': civico
                    }
            
            # Default: prime 2 parole = nome, resto = indirizzo
            if len(words) > 2:
                return {
                    'nome': ' '.join(words[:2]),
                    'indirizzo': ' '.join(words[2:]),
                    'civico': civico
                }
            else:
                return {
                    'nome': words[0],
                    'indirizzo': words[1] if len(words) > 1 else '',
                    'civico': civico
                }
    
    return None


def estrai_singolo_lavoro(text: str, num_lavoro: int) -> Optional[Dict]:
    """
    Estrae i dati di un singolo lavoro dal testo.
    """
    # Il testo del PDF ha una struttura ripetitiva per ogni lavoro
    # Dobbiamo trovare il blocco corrispondente al numero di lavoro
    
    # Pattern per trovare la sezione del lavoro specifico
    # Esempio: "KEITA SIDI AG18966 1 399" dove 1 è il num_lavoro
    pattern_start = re.compile(
        rf'AG\d+\s+{num_lavoro}\s+\d+',
        re.MULTILINE
    )
    
    match = pattern_start.search(text)
    if not match:
        return None
    
    # Trova il testo tra questo lavoro e il successivo (o fine testo)
    start_pos = match.start()
    
    # Cerca il prossimo lavoro
    next_lavoro = num_lavoro + 1
    pattern_next = re.compile(rf'AG\d+\s+{next_lavoro}\s+\d+')
    next_match = pattern_next.search(text, start_pos + 10)
    
    if next_match:
        blocco = text[start_pos:next_match.start()]
    else:
        # Potrebbe essere l'ultimo lavoro, prendi fino alla fine o al prossimo "AG"
        # Cerca pattern ripetuto di intestazione
        pattern_repeat = re.compile(r'Preposto:\s+Arrivo', re.MULTILINE)
        repeat_match = pattern_repeat.search(text, start_pos + 100)
        if repeat_match:
            blocco = text[start_pos:repeat_match.start()]
        else:
            blocco = text[start_pos:]
    
    # Ora estrai i dati dal blocco
    return estrai_dati_da_blocco(blocco, num_lavoro)


def estrai_dati_da_blocco(blocco: str, num_lavoro: int) -> Dict:
    """
    Estrae i dati specifici da un blocco di testo per un lavoro.
    """
    risultato = {
        'lavoro': num_lavoro,
        'nome': '',
        'telefono': '',
        'indirizzo': '',
        'civico': '',
        'comune': '',
        'cap': '',
        'data_intervento': '',
        'pod': '',
        'eneltel': '',
    }
    
    # Data intervento (formato: 04/02/2026 00:49)
    data_match = re.search(r'(\d{2}/\d{2}/\d{4})\s+\d{2}:\d{2}', blocco)
    if data_match:
        risultato['data_intervento'] = data_match.group(1)
    
    # POD (formato standard IT001E01975535 o fotovoltaico ITP0AE...)
    pod_match = re.search(r'(IT[A-Z0-9]{3}[A-Z]\d+)', blocco)
    if pod_match:
        risultato['pod'] = pod_match.group(1)
    
    # Nome cliente e indirizzo
    # Pattern: "GIOVANNI CANAVESIO SANTA MARIA GORETTI 24 E"
    # Seguito da: "3331572577 10024 MONCALIERI D000BO9"
    
    # Cerca la riga con nome e indirizzo
    # Di solito dopo "S. P. I." c'è "NOME_CLIENTE VIA CIVICO [S/P/I]"
    nome_indirizzo_match = re.search(
        r'S\.\s*P\.\s*I\.\s+([A-Z][A-Z\s]+?)\s+'  # Nome (in maiuscolo)
        r'([A-Z][A-Z\.\'\s]+?)\s+'  # Indirizzo
        r'(\d+[A-Z]?(?:/\d+)?)\s*'  # Civico
        r'([A-Z]?)$',  # Eventuale S/P/I
        blocco,
        re.MULTILINE
    )
    
    if nome_indirizzo_match:
        risultato['nome'] = nome_indirizzo_match.group(1).strip()
        risultato['indirizzo'] = nome_indirizzo_match.group(2).strip()
        risultato['civico'] = nome_indirizzo_match.group(3).strip()
    else:
        # Pattern alternativo: cerca nel testo grezzo
        # Nome è di solito una serie di parole in maiuscolo dopo "Cliente:"
        cliente_match = re.search(r'Cliente:\s*([A-Z][A-Z\s]+)', blocco)
        if cliente_match:
            # Il nome può essere mescolato con l'indirizzo
            # Esempio: "GIOVANNI CANAVESIO SANTA MARIA GORETTI 24"
            parti = cliente_match.group(1).strip()
            # Prova a separare nome da indirizzo cercando pattern comuni vie
            via_pattern = re.search(
                r'^([A-Z][A-Z\s]+?)\s+(VIA|CORSO|PIAZZA|STRADA|V\.|C\.|S\.MARIA|LARGO|VIALE)\s+(.+?)(?:\s+(\d+[A-Z]?(?:/\d+)?))?$',
                parti,
                re.IGNORECASE
            )
            if via_pattern:
                risultato['nome'] = via_pattern.group(1).strip()
                risultato['indirizzo'] = f"{via_pattern.group(2)} {via_pattern.group(3)}".strip()
                if via_pattern.group(4):
                    risultato['civico'] = via_pattern.group(4)
    
    # Telefono e Comune
    # Prima cerca pattern con etichetta "Tel.:" o "Tel:"
    tel_label_pattern = re.search(
        r'Tel\.?:\s*(\d{8,11})',
        blocco,
        re.IGNORECASE
    )
    if tel_label_pattern:
        risultato['telefono'] = tel_label_pattern.group(1).strip()
    
    # Posizione fine riga tel/cap/comune per la ricerca eneltel successiva
    _fine_riga_tel = 0
    
    # Se non trovato con etichetta, cerca pattern: "3331572577 10024 MONCALIERI D000BO9"
    if not risultato.get('telefono'):
        tel_comune_match = re.search(
            r'(\d{9,11})\s+(\d{5})\s+([A-Z][A-Z\s]+?)\s+[A-Z0-9]+',
            blocco
        )
        
        if tel_comune_match:
            risultato['telefono'] = tel_comune_match.group(1).strip()
            risultato['cap'] = tel_comune_match.group(2).strip()
            risultato['comune'] = tel_comune_match.group(3).strip()
            _fine_riga_tel = tel_comune_match.end()
        else:
            # Pattern alternativo senza telefono
            # "10024 MONCALIERI D000BO9"
            comune_match = re.search(r'(\d{5})\s+([A-Z][A-Z\s]+?)\s+[A-Z0-9]+', blocco)
            if comune_match:
                risultato['cap'] = comune_match.group(1).strip()
                risultato['comune'] = comune_match.group(2).strip()
                _fine_riga_tel = comune_match.end()
    else:
        # Telefono già trovato, cerca solo CAP e Comune
        comune_match = re.search(r'(\d{5})\s+([A-Z][A-Z\s]+?)\s+[A-Z0-9]+', blocco)
        if comune_match:
            risultato['cap'] = comune_match.group(1).strip()
            risultato['comune'] = comune_match.group(2).strip()
            _fine_riga_tel = comune_match.end()
    
    # ENELTEL (9 cifre + eventuale lettera A/B per fotovoltaici)
    # IMPORTANTE: cerchiamo DOPO la riga tel/cap/comune per evitare di
    # catturare numeri di telefono fisso a 9 cifre (es. 011234567)
    blocco_eneltel = blocco[_fine_riga_tel:] if _fine_riga_tel > 0 else blocco
    eneltel_match = re.search(r'\b(\d{9}[A-B]?)\b', blocco_eneltel)
    if eneltel_match:
        risultato['eneltel'] = eneltel_match.group(1)
    
    return risultato


def parse_multiple_distinte(file_paths: List[str]) -> List[Dict]:
    """
    Elabora più file PDF DistintaBase e restituisce tutti i lavori.
    """
    tutti_lavori = []
    
    for path in file_paths:
        if not os.path.exists(path):
            logger.warning(f"[DISTINTA] File non trovato: {path}")
            continue
        
        if not path.lower().endswith('.pdf'):
            logger.warning(f"[DISTINTA] File non PDF ignorato: {path}")
            continue
        
        try:
            lavori = parse_distinta_pdf(path)
            logger.info(f"[DISTINTA] Estratti {len(lavori)} lavori da {os.path.basename(path)}")
            tutti_lavori.extend(lavori)
        except Exception as e:
            logger.error(f"[DISTINTA] Errore elaborazione {path}: {e}")
    
    return tutti_lavori


def confronta_con_filone(
    lavori_distinta: List[Dict],
    dati_filone: Dict[str, Dict]
) -> List[Dict]:
    """
    Confronta i dati della distinta con quelli del filone.
    Controlla:
    - Telefono presente su distinta ma mancante nel filone
    - Telefono diverso tra distinta e filone (controlla campo telefono + nominativo + note)
    - Data intervento diversa
    
    Args:
        lavori_distinta: Lista lavori estratti dalla distinta
        dati_filone: Dizionario POD -> dati del filone
        
    Returns:
        Lista di differenze rilevate con possibili sovrascritture
    """
    differenze = []
    
    for lavoro in lavori_distinta:
        pod = lavoro.get('pod', '').strip().upper()
        if not pod:
            continue
        
        # Cerca nel filone
        filone_data = dati_filone.get(pod)
        
        if not filone_data:
            # POD non trovato - skip, non è un errore critico
            continue
        
        # Controlla TELEFONO e DATA
        
        # 1. Telefono: confronta distinta vs filone (campo telefono + nominativo + note)
        valore_tel_distinta = str(lavoro.get('telefono', '')).strip()
        valore_tel_filone = str(filone_data.get('telefono', '')).strip()
        
        # Rimuovi "ERRATO" dal campo telefono per il confronto
        # (es. "0118120740 ERRATO" -> "0118120740")
        valore_tel_filone_pulito = re.sub(r'\s*ERRATO\s*', '', valore_tel_filone, flags=re.IGNORECASE).strip()
        
        # Raccogli tutti i numeri dal filone (campo telefono + nominativo + note)
        numeri_filone = []
        fonti_filone = {}  # num_norm -> label fonte
        
        # Campo telefono: può contenere più numeri (es. "333 1234567 / 011 9876543")
        if valore_tel_filone_pulito:
            numeri_tel = estrai_tutti_telefoni_da_testo(valore_tel_filone_pulito)
            if numeri_tel:
                for num in numeri_tel:
                    num_norm = re.sub(r'[^\d]', '', num)
                    if not any(numeri_telefono_uguali(num_norm, n) for n in numeri_filone):
                        numeri_filone.append(num_norm)
                        fonti_filone[num_norm] = f'campo telefono: {num}'
            elif is_numero_valido(valore_tel_filone_pulito):
                # Fallback: il campo intero è un numero
                num_norm = re.sub(r'[^\d]', '', valore_tel_filone_pulito)
                numeri_filone.append(num_norm)
                fonti_filone[num_norm] = f'campo telefono: {valore_tel_filone_pulito}'
        
        # Controlla nominativo e note del filone (anche questi possono avere più numeri)
        for campo_nome, label in [('nominativo', 'nominativo'), ('note', 'note')]:
            valore_campo = str(filone_data.get(campo_nome, '')).strip()
            if valore_campo:
                numeri_campo = estrai_tutti_telefoni_da_testo(valore_campo)
                for num in numeri_campo:
                    num_norm = re.sub(r'[^\d]', '', num)
                    # Evita duplicati considerando lo 0 iniziale
                    if not any(numeri_telefono_uguali(num_norm, n) for n in numeri_filone):
                        numeri_filone.append(num_norm)
                        fonti_filone[num_norm] = f'{label}: {num}'
        
        tel_distinta_norm = re.sub(r'[^\d]', '', valore_tel_distinta) if valore_tel_distinta else ''
        
        # Confronta tollerando lo 0 iniziale mancante (es. filone "1234" == distinta "01234")
        tel_match = any(numeri_telefono_uguali(tel_distinta_norm, n) for n in numeri_filone) if numeri_filone else False
        
        if valore_tel_distinta and not numeri_filone:
            # Distinta ha telefono, filone no -> segnala mancante
            differenze.append({
                'pod': pod,
                'lavoro': lavoro.get('lavoro'),
                'tipo': 'telefono_mancante',
                'campo': 'Telefono',
                'valore_distinta': valore_tel_distinta,
                'valore_filone': valore_tel_filone,
                'messaggio': f'Telefono presente su distinta ma mancante nel filone',
                'suggerisci_sovrascrittura': True,
                'distinta': lavoro,
                'filone': filone_data
            })
        elif valore_tel_distinta and numeri_filone and not tel_match:
            # Entrambi hanno telefono ma sono diversi -> segnala mismatch
            filone_info = ' / '.join(fonti_filone.values())
            differenze.append({
                'pod': pod,
                'lavoro': lavoro.get('lavoro'),
                'tipo': 'telefono_diverso',
                'campo': 'Telefono',
                'valore_distinta': valore_tel_distinta,
                'valore_filone': filone_info,
                'messaggio': f'Numeri di telefono diversi tra distinta e filone',
                'suggerisci_sovrascrittura': True,
                'distinta': lavoro,
                'filone': filone_data
            })
        
        # 2. Data: segnala se diverse
        valore_data_distinta = str(lavoro.get('data_intervento', '')).strip()
        valore_data_filone = str(filone_data.get('data_pian', '')).strip()
        
        if valore_data_distinta and valore_data_filone:
            dist_norm = normalizza_per_confronto(valore_data_distinta, 'data')
            fil_norm = normalizza_per_confronto(valore_data_filone, 'data')
            
            if dist_norm != fil_norm:
                differenze.append({
                    'pod': pod,
                    'lavoro': lavoro.get('lavoro'),
                    'tipo': 'data_diversa',
                    'campo': 'Data intervento',
                    'valore_distinta': valore_data_distinta,
                    'valore_filone': valore_data_filone,
                    'messaggio': f'Data diversa: distinta={valore_data_distinta}, filone={valore_data_filone}',
                    'suggerisci_sovrascrittura': True,
                    'distinta': lavoro,
                    'filone': filone_data
                })
    
    return differenze


def normalizza_per_confronto(valore: str, tipo_campo: str) -> str:
    """Normalizza un valore per il confronto."""
    if not valore:
        return ''
    
    valore = valore.strip().upper()
    
    if 'data' in tipo_campo:
        # Normalizza date in formato YYYY-MM-DD
        # Prova vari formati
        for fmt in ['%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y']:
            try:
                dt = datetime.strptime(valore, fmt)
                return dt.strftime('%Y-%m-%d')
            except Exception:
                pass
        return valore
    
    if 'telefono' in tipo_campo:
        # Rimuovi tutti i caratteri non numerici
        return re.sub(r'[^\d]', '', valore)
    
    # Per altri campi, rimuovi spazi multipli e caratteri speciali
    valore = re.sub(r'\s+', ' ', valore)
    return valore


def classifica_contatti(lavori: List[Dict], dati_filone: Dict[str, Dict] = None) -> Dict:
    """
    Classifica i contatti in base al tipo di numero.
    
    Args:
        lavori: Lista dei lavori da classificare
        dati_filone: Dizionario POD -> dati del filone (opzionale)
    
    Returns:
        Dict con:
        - cellulare: lista contatti con cellulare
        - fisso: lista contatti con fisso
        - senza_telefono: lista contatti senza numero
    """
    risultato = {
        'cellulare': [],
        'fisso': [],
        'senza_telefono': []
    }
    
    if dati_filone is None:
        dati_filone = {}
    
    for lavoro in lavori:
        telefono = lavoro.get('telefono', '').strip()
        
        if not telefono or not is_numero_valido(telefono):
            # Per contatti senza telefono, cerca numeri alternativi
            pod = lavoro.get('pod', '').strip().upper()
            filone_data = dati_filone.get(pod, {})
            
            numeri_trovati = []
            
            # Controlla telefono del filone (può contenere più numeri)
            tel_filone = filone_data.get('telefono', '').strip()
            if tel_filone:
                numeri_tel = estrai_tutti_telefoni_da_testo(tel_filone)
                if numeri_tel:
                    for num in numeri_tel:
                        if not any(numeri_telefono_uguali(num, n['numero']) for n in numeri_trovati):
                            tipo_tel = 'cellulare' if is_cellulare(num) else 'fisso'
                            numeri_trovati.append({
                                'fonte': 'filone',
                                'numero': num,
                                'tipo': tipo_tel
                            })
                elif is_numero_valido(tel_filone):
                    tipo_tel = 'cellulare' if is_cellulare(tel_filone) else 'fisso'
                    numeri_trovati.append({
                        'fonte': 'filone',
                        'numero': tel_filone,
                        'tipo': tipo_tel
                    })
            
            # Controlla nominativo del filone per numeri nascosti (può averne più di uno)
            nominativo_filone = filone_data.get('nominativo', '').strip()
            if nominativo_filone:
                numeri_nom = estrai_tutti_telefoni_da_testo(nominativo_filone)
                for num in numeri_nom:
                    if not any(numeri_telefono_uguali(num, n['numero']) for n in numeri_trovati):
                        tipo_tel = 'cellulare' if is_cellulare(num) else 'fisso'
                        numeri_trovati.append({
                            'fonte': 'nominativo_filone',
                            'numero': num,
                            'tipo': tipo_tel
                        })
            
            # Controlla anche altri campi del filone (indirizzo, comune, note)
            for campo_filone, label_campo in [('indirizzo', 'indirizzo_filone'), ('comune', 'comune_filone'), ('note', 'note_filone')]:
                valore_campo = filone_data.get(campo_filone, '').strip()
                if valore_campo:
                    numeri_campo = estrai_tutti_telefoni_da_testo(valore_campo)
                    for num in numeri_campo:
                        if not any(numeri_telefono_uguali(num, n['numero']) for n in numeri_trovati):
                            tipo_tel = 'cellulare' if is_cellulare(num) else 'fisso'
                            numeri_trovati.append({
                                'fonte': label_campo,
                                'numero': num,
                                'tipo': tipo_tel
                            })
            
            # Controlla anche il nome dalla distinta
            nome_distinta = lavoro.get('nome', '').strip()
            if nome_distinta:
                numeri_nome = estrai_tutti_telefoni_da_testo(nome_distinta)
                for num in numeri_nome:
                    if not any(numeri_telefono_uguali(num, n['numero']) for n in numeri_trovati):
                        tipo_tel = 'cellulare' if is_cellulare(num) else 'fisso'
                        numeri_trovati.append({
                            'fonte': 'nominativo_distinta',
                            'numero': num,
                            'tipo': tipo_tel
                        })
            
            if numeri_trovati:
                lavoro['numeri_alternativi'] = numeri_trovati
            
            risultato['senza_telefono'].append(lavoro)
        elif is_cellulare(telefono):
            lavoro['telefono_formattato'] = formatta_numero_whatsapp(telefono)
            risultato['cellulare'].append(lavoro)
        else:
            risultato['fisso'].append(lavoro)
    
    return risultato
