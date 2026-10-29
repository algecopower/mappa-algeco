# -*- coding: utf-8 -*-
"""
Script per generare Excel per l'invio delle lettere agli utenti.

Genera un file Excel con fogli allineati al template lettere:
- MONOFASE: contatori monofase + trifase diretti
- EMME2: contatori fotovoltaici con Eneltel che termina con A o B
- SEMIDIRETTI: contatori trifase semidiretti
- SOLO MONOFASE e SOLO TRIFASE DIRETTI: fogli di dettaglio in coda

Elabora le DATE definite manualmente nella configurazione.
Tutte le tappe di tutti i tecnici sono raggruppate insieme in ogni foglio.
Include una colonna "Data pianificazione" per ogni tappa.
Le fasce orarie sono calcolate in base al percorso ottimizzato per tecnico.

NOTA: L'algoritmo di ottimizzazione è centralizzato in moduli/percorso.py
      Le fasce orarie sono centralizzate in moduli/fasce_orarie.py

Uso:
  python genera_excel_lettere.py                    # Usa le date definite in DATE_LETTERE
  python genera_excel_lettere.py --provincia PAVIA  # Provincia specifica
"""
import sys
import os
import re
import argparse
from datetime import datetime

# Setup path
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# ============================================================================
# CONFIGURAZIONE DATE (modificare qui per definire le date da includere)
# ============================================================================
DATE_LETTERE = [
    '2026-03-16',
    '2026-03-17',
    '2026-03-18',
    '2026-03-19',
    '2026-03-20',
    '2026-03-21',
]

# ============================================================================
# FASCE ORARIE - importate dal modulo centralizzato
# ============================================================================
from moduli.fasce_orarie import assegna_fasce, formatta_fascia_oraria, consolida_fasce_per_indirizzo
from moduli.rilevamento_aziende import is_azienda

# ============================================================================
# DATABASE COMUNI ITALIANI (CAP e PROVINCIA)
# Scaricato automaticamente da GitHub e salvato in cache locale.
# Copre TUTTI i comuni italiani, nessun dizionario hardcoded necessario.
# ============================================================================
from moduli.comuni_db import cerca_cap, cerca_provincia


def _cerca_cap_sicuro(comune, prov=None):
    """Invoca cerca_cap passando provincia se supportata, compatibile con mock nei test."""
    try:
        return cerca_cap(comune, provincia=prov)
    except TypeError:
        return cerca_cap(comune)


def _cerca_provincia_sicuro(comune, prov=None):
    """Invoca cerca_provincia passando provincia se supportata, compatibile con mock nei test."""
    try:
        return cerca_provincia(comune, provincia=prov)
    except TypeError:
        return cerca_provincia(comune)


# formatta_fascia_oraria() è ora importata da moduli/fasce_orarie.py

COLONNE_LETTERE_ORDINE = [
    'nominativo',
    'via',
    'civico',
    'comune',
    'provincia',
    'cap',
    'data_pianificazione',
    'ora_pianificazione',
    'lettera',
    'pod',
    'operatore',
    'posizione_mis',
    'appuntamento',
]

LARGHEZZE_COLONNE_LETTERE = {
    'nominativo': 58,
    'via': 30,
    'civico': 14,
    'comune': 22,
    'provincia': 10,
    'cap': 7,
    'data_pianificazione': 17,
    'ora_pianificazione': 22,
    'lettera': 10,
    'pod': 20,
    'operatore': 14,
    'posizione_mis': 18,
    'appuntamento': 14,
}

HEADERS_LETTERE = [
    'Nominativo_Cliente', 'VIA', 'Numero_Civico', 'Comune', 'PROVINCIA', 'CAP',
    'Data Pianificazione', 'Ora pianificazione', 'LETTERA', 'POD', 'Operatore',
    'Posizione mis.', 'Appuntamento',
]

CIVICI_SENZA_NUMERO = ('SN', 'SNC', 'S.N.', 'S.N.C.', 'S/N', 'S/NC')


def parse_args():
    """Parse argomenti da linea di comando."""
    parser = argparse.ArgumentParser(
        description='Genera Excel per invio lettere utenti'
    )
    parser.add_argument(
        '--provincia', '-p', 
        type=str, 
        choices=['TORINO', 'PAVIA', 'torino', 'pavia'],
        help='Provincia da utilizzare (TORINO o PAVIA)'
    )
    return parser.parse_args()


def formatta_data_italiana(data_str):
    """Converte data da YYYY-MM-DD a DD/MM/YYYY."""
    try:
        dt = datetime.strptime(data_str, '%Y-%m-%d')
        return dt.strftime('%d/%m/%Y')
    except Exception:
        return data_str


def get_giorno_settimana(data_str):
    """Restituisce il giorno della settimana in italiano."""
    try:
        dt = datetime.strptime(data_str, '%Y-%m-%d')
        giorno = dt.strftime('%A')
        giorni_ita = {
            'Monday': 'Lunedì', 
            'Tuesday': 'Martedì', 
            'Wednesday': 'Mercoledì',
            'Thursday': 'Giovedì', 
            'Friday': 'Venerdì', 
            'Saturday': 'Sabato', 
            'Sunday': 'Domenica'
        }
        return giorni_ita.get(giorno, giorno)
    except Exception:
        return ''


def separa_via_civico(via, civico):
    """
    Separa automaticamente via e civico, e pulisce il prefisso 'V '.
    
    Se il civico è vuoto, prova a estrarlo dalla fine dell'indirizzo:
    l'ultimo token dopo l'ultimo spazio viene considerato civico se
    contiene almeno una cifra (es. '10', '10A', '10BIS', '10/12', 'SN').
    
    Rimuove il prefisso 'V ' a inizio indirizzo (ridondante per "Via").
    
    Args:
        via: Stringa dell'indirizzo
        civico: Stringa del civico (può essere vuota)
    
    Returns:
        Tupla (via_pulita, civico_pulito)
    """
    via = str(via).strip() if via else ''
    civico = str(civico).strip() if civico else ''
    
    # Se il civico è vuoto (o contiene solo spazi/nan), prova a estrarlo dalla via
    if not civico or civico.lower() in ('', 'nan', 'none'):
        civico = ''
        if via:
            parti = via.rsplit(' ', 1)
            if len(parti) == 2:
                candidato = parti[1].strip()
                # Il candidato è un civico se contiene almeno una cifra
                # oppure è una variante di SN (senza numero)
                if re.search(r'\d', candidato) or candidato.upper() in ('SN', 'SNC', 'S.N.', 'S.N.C.', 'S/N', 'S/NC'):
                    via = parti[0].strip()
                    civico = candidato
    
    # Rimuovi prefisso 'VIA ' o 'V ' a inizio indirizzo (ridondante per "Via")
    if re.match(r'^VIA\s', via, re.IGNORECASE):
        via = via[4:].strip()
    elif re.match(r'^V\s', via, re.IGNORECASE):
        via = via[2:].strip()
    
    return via, civico


def pulisci_nominativo(nominativo):
    """
    Rimuove tutti i numeri e il simbolo + dal nominativo.
    
    Args:
        nominativo: Stringa con il nome cliente
        
    Returns:
        Nominativo pulito senza numeri
    """
    if not nominativo or not isinstance(nominativo, str):
        return nominativo
    
    # Rimuovi tutti i numeri e il simbolo +
    risultato = re.sub(r'[\d+]', '', nominativo)
    
    # Pulisci spazi multipli e spazi a inizio/fine
    risultato = re.sub(r'\s+', ' ', risultato).strip()
    
    # Rimuovi eventuali trattini/punti rimasti a fine stringa
    risultato = re.sub(r'[\s\-\.]+$', '', risultato)
    
    return risultato


def testo_pulito(valore):
    """Normalizza celle vuote/NaN in stringa vuota."""
    if valore is None:
        return ''
    testo = str(valore).strip()
    # "None" è anche un Comune reale della provincia di Torino.
    if testo.lower() in ('nan', 'null'):
        return ''
    return testo


def get_config_lettere(config_override=None):
    """Ritorna la configurazione Excel lettere completa di default."""
    try:
        from config import LETTERE_EXCEL_CONFIG, merge_lettere_excel_config
        base = LETTERE_EXCEL_CONFIG if config_override is None else config_override
        return merge_lettere_excel_config(base)
    except Exception:
        return {
            'fogli': {
                'monofase': 'MONOFASE',
                'emme2': 'EMME2',
                'semidiretti': 'SEMIDIRETTI',
                'contatti_aziendali': 'Contatti Aziendali',
                'solo_monofase': 'SOLO MONOFASE',
                'solo_trifase_diretti': 'SOLO TRIFASE DIRETTI',
            },
            'nomi_colonne': dict(zip(COLONNE_LETTERE_ORDINE, HEADERS_LETTERE)),
            'valori_lettera': {'si': "SI'", 'no': 'NO'},
            'regole_no': {
                'appuntamento': True,
                'accessibile_esterno': True,
                'civico_vuoto': True,
                'civico_senza_numero': True,
            },
            'parole_chiave': {
                'lettera_no': ['COMUNE', 'PROVINCIA'],
            },
            'opzioni': {
                'unisci_trifase_diretti_in_monofase': True,
                'crea_fogli_dettaglio': True,
            },
        }


def get_headers_lettere(config_override=None):
    """Ritorna le intestazioni Excel lettere secondo configurazione."""
    cfg = get_config_lettere(config_override)
    nomi_colonne = cfg.get('nomi_colonne', {})
    return [
        testo_pulito(nomi_colonne.get(chiave)) or default
        for chiave, default in zip(COLONNE_LETTERE_ORDINE, HEADERS_LETTERE)
    ]


def normalizza_parole_chiave(raw):
    """Normalizza una lista/stringa di parole chiave configurabili."""
    if raw is None:
        return []
    if isinstance(raw, str):
        items = re.split(r'[\n,;]+', raw)
    elif isinstance(raw, (list, tuple, set)):
        items = raw
    else:
        items = [raw]

    out = []
    viste = set()
    for item in items:
        keyword = testo_pulito(item).upper()
        if keyword and keyword not in viste:
            out.append(keyword)
            viste.add(keyword)
    return out


def contiene_parola_chiave(testo, parole_chiave):
    """True se una delle parole chiave compare nel testo normalizzato."""
    haystack = testo_pulito(testo).upper()
    if not haystack:
        return False
    return any(keyword in haystack for keyword in normalizza_parole_chiave(parole_chiave))


def civico_senza_numero(civico, includi_vuoto=True):
    """True se il civico è vuoto o una variante di senza numero."""
    civico_norm = testo_pulito(civico).upper()
    return (includi_vuoto and not civico_norm) or civico_norm in CIVICI_SENZA_NUMERO


def nome_foglio_excel(nome, fallback):
    """Normalizza un nome foglio secondo i vincoli Excel."""
    pulito = testo_pulito(nome) or fallback
    pulito = re.sub(r'[:\\/?*\[\]]', '-', pulito).strip("'").strip()
    return (pulito or fallback)[:31]


def richiede_lettera(punto, config_lettere=None):
    """
    Determina se il contatore richiede l'invio di una lettera.
    
    NON si invia lettera se:
    - È un appuntamento (hanno già concordato)
    - Posizione contatore è "Accessibile dall'esterno" (anche con "3" accanto)
    - Nominativo contiene una parola chiave configurata per LETTERA = NO
    - Civico è vuoto, SN o SNC
    
    Args:
        punto: Dizionario con i dati del contatore
        
    Returns:
        True se richiede lettera (SI'), False altrimenti (NO)
    """
    cfg = get_config_lettere(config_lettere)
    regole_no = cfg.get('regole_no', {})
    parole_chiave = cfg.get('parole_chiave', {})

    # Controlla se è un appuntamento (non serve lettera)
    if regole_no.get('appuntamento', True) and punto.get('IS_APPUNTAMENTO', False):
        return False
    
    # Controlla posizione contatore (NOTE_K)
    posizione = str(punto.get('NOTE_K', '')).strip().upper()
    if (
        regole_no.get('accessibile_esterno', True)
        and 'ACCESSIBILE' in posizione
        and 'ESTERNO' in posizione
    ):
        return False
    
    # Controlla parole chiave configurabili nel nominativo cliente.
    if contiene_parola_chiave(punto.get('CLI_K', ''), parole_chiave.get('lettera_no', [])):
        return False
    
    civico = testo_pulito(punto.get('CIVICO_K', '')).upper()
    if regole_no.get('civico_vuoto', True) and not civico:
        return False
    if regole_no.get('civico_senza_numero', True) and civico_senza_numero(
        civico, includi_vuoto=False,
    ):
        return False
    
    return True


def classifica_contatore(punto):
    """
    Classifica il tipo di contatore.
    
    Returns:
        'fotovoltaico' - se Eneltel termina con A o B
        'trifase_semi' - se è un trifase SEMIDIRETTO
        'trifase_dir' - se è un trifase DIRETTO (foglio separato)
        'monofase' - contatori monofase standard
    """
    eneltel = str(punto.get('ENEL_K', '')).strip().upper()
    tipo_ce = str(punto.get('TIPO_CE_K', '')).strip().upper()
    
    # Fotovoltaici: Eneltel termina con A o B
    if eneltel and (eneltel.endswith('A') or eneltel.endswith('B')):
        return 'fotovoltaico'
    
    # Trifase SEMIDIRETTI (SOLO semidiretti)
    if 'SEMIDIRETTO' in tipo_ce or 'SEMID' in tipo_ce or 'SEMI DIRETTO' in tipo_ce:
        return 'trifase_semi'
    
    # Trifase DIRETTI: foglio separato
    if 'TRIFASE' in tipo_ce or '3F' in tipo_ce or 'DIRETTO' in tipo_ce:
        return 'trifase_dir'
    
    # Default: monofase
    return 'monofase'


# assegna_fasce() è ora importata da moduli/fasce_orarie.py


def genera_excel_lettere(
    df,
    date_da_elaborare,
    filename=None,
    tappe_per_fascia=None,
    archivia=None,
    utente=None,
):
    """
    Genera Excel per invio lettere con fogli allineati al template.
    Elabora le date specificate.
    
    Fogli:
    - MONOFASE: contatori standard + trifase diretti
    - EMME2: Eneltel termina con A o B
    - SEMIDIRETTI: contatori trifase semidiretti
    - SOLO MONOFASE / SOLO TRIFASE DIRETTI: fogli tecnici in coda

    I contatti aziendali non vengono più scritti in un foglio Excel: sono
    registrati nell'Archivio Lettere del DB locale.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from moduli.percorso import ordina_percorso
    from config import PROVINCIA_ATTIVA, DEPOSITO, MATRICOLE_VIP, LETTERE_EXCEL_CONFIG
    lettere_cfg = get_config_lettere(LETTERE_EXCEL_CONFIG)
    fogli_cfg = lettere_cfg.get('fogli', {})
    opzioni_cfg = lettere_cfg.get('opzioni', {})
    valori_lettera = lettere_cfg.get('valori_lettera', {})
    valore_lettera_si = testo_pulito(valori_lettera.get('si')) or "SI'"
    valore_lettera_no = testo_pulito(valori_lettera.get('no')) or "NO"
    
    # Determina modalità auto o fisso
    auto_mode = (tappe_per_fascia is None or str(tappe_per_fascia).lower() == 'auto')
    
    if not date_da_elaborare:
        print("❌ Nessuna data definita in DATE_LETTERE")
        return None
    
    filename_auto = filename is None
    if filename is None:
        # Formato: "LETTERE DAL giorno/mese AL giorno/mese.xlsx"
        data_inizio = datetime.strptime(date_da_elaborare[0], '%Y-%m-%d')
        data_fine = datetime.strptime(date_da_elaborare[-1], '%Y-%m-%d')
        giorno_inizio = data_inizio.strftime('%d-%m')
        giorno_fine = data_fine.strftime('%d-%m')
        nome_file = f"LETTERE {PROVINCIA_ATTIVA.upper()} DAL {giorno_inizio} AL {giorno_fine}.xlsx"
        # Salva nella cartella dedicata output/lettere/
        cartella_lettere = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'output', 'lettere')
        os.makedirs(cartella_lettere, exist_ok=True)
        filename = os.path.join(cartella_lettere, nome_file)
        if os.path.exists(filename):
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            base, ext = os.path.splitext(nome_file)
            filename = os.path.join(cartella_lettere, f"{base} {timestamp}{ext}")
    if archivia is None:
        archivia = filename_auto
    
    print(f"\n📅 Date da elaborare: {len(date_da_elaborare)}")
    for d in date_da_elaborare:
        print(f"   • {formatta_data_italiana(d)} ({get_giorno_settimana(d)})")
    
    # Filtra per tutte le date specificate
    df_filtrato = df[df['DATA_K'].isin(date_da_elaborare)].copy()
    
    # Rimuovi colonne duplicate per evitare warning su to_dict('records')
    df_filtrato = df_filtrato.loc[:, ~df_filtrato.columns.duplicated()]
    
    if df_filtrato.empty:
        print("❌ Nessun dato trovato per le date specificate")
        print(f"   Date cercate: {date_da_elaborare}")
        print(f"   Date disponibili nel FILONE: {sorted(df['DATA_K'].unique())[:10]}...")
        return None
    
    print(f"📍 Contatori totali: {len(df_filtrato)}")
    
    # Per ogni data, calcola le fasce orarie
    fasce_per_contatore = {}  # eneltel -> fascia
    
    for data_target in date_da_elaborare:
        df_data = df_filtrato[df_filtrato['DATA_K'] == data_target]
        if df_data.empty:
            continue
        
        import config
        disabled_ops = {str(x).strip().upper() for x in getattr(config, 'OPERATORI_DISABILITATI', []) if str(x).strip()}
        tecnici_data = [t for t in MATRICOLE_VIP if t in df_data['TEC_K'].unique() and str(t).strip().upper() not in disabled_ops]
        if 'TUTTI' in df_data['TEC_K'].unique():
            tecnici_data.append('TUTTI')
        
        for tec in tecnici_data:
            df_tec = df_data[df_data['TEC_K'] == tec]
            if df_tec.empty:
                continue
            
            # Ordina percorso e assegna fasce
            # Usa OSRM locale per distanze stradali reali
            punti_list = df_tec.to_dict('records')
            percorso_ordinato = ordina_percorso(punti_list, DEPOSITO, usa_osrm_ottimizzazione=True)
            
            # Calcola tappe_per_fascia: auto (bilanciato) o fisso
            if auto_mode:
                # Distribuzione bilanciata: spalma uniformemente su tutte le 7 fasce
                tappe_con_fasce = assegna_fasce(percorso_ordinato, bilancia=True)
            else:
                tpf = int(tappe_per_fascia)
                tappe_con_fasce = assegna_fasce(percorso_ordinato, tappe_per_fascia=tpf)

            # Consolidamento: contatori con stesso indirizzo postale devono
            # cadere nella stessa fascia (vedi consolida_fasce_per_indirizzo).
            tappe_con_fasce = consolida_fasce_per_indirizzo(tappe_con_fasce)

            # Salva la fascia per ogni contatore
            for punto, fascia in tappe_con_fasce:
                eneltel = punto.get('ENEL_K', '')
                fasce_per_contatore[eneltel] = fascia
    
    # Classifica tutti i contatori per tipo
    tutti_punti = df_filtrato.to_dict('records')
    
    contatori_per_tipo = {
        'monofase': [],
        'fotovoltaico': [],
        'trifase_semi': [],
        'trifase_dir': []
    }
    
    for punto in tutti_punti:
        tipo = classifica_contatore(punto)
        # Aggiungi la fascia oraria e la data formattata
        eneltel = punto.get('ENEL_K', '')
        punto['FASCIA_ORARIA'] = fasce_per_contatore.get(eneltel, '')
        punto['DATA_ITALIANA'] = formatta_data_italiana(punto.get('DATA_K', ''))
        contatori_per_tipo[tipo].append(punto)
    
    print("\n📊 Classificazione contatori:")
    print(f"   • Monofase: {len(contatori_per_tipo['monofase'])}")
    print(f"   • Fotovoltaici: {len(contatori_per_tipo['fotovoltaico'])}")
    print(f"   • Trifase Semidiretti: {len(contatori_per_tipo['trifase_semi'])}")
    print(f"   • Trifase Diretti: {len(contatori_per_tipo['trifase_dir'])}")
    
    # Crea workbook
    wb = Workbook()
    
    # Stili
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF", size=11)
    mono_fill = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")      # Verde chiaro
    foto_fill = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")      # Giallo chiaro
    tri_semi_fill = PatternFill(start_color="DDEBF7", end_color="DDEBF7", fill_type="solid")  # Azzurro chiaro
    tri_dir_fill = PatternFill(start_color="F8CBAD", end_color="F8CBAD", fill_type="solid")   # Arancione chiaro
    app_fill = PatternFill(start_color="FFCCCB", end_color="FFCCCB", fill_type="solid")       # Rosa per appuntamenti
    fascia_fills = {
        '8/11': PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid"),
        '10/13': PatternFill(start_color="DDEBF7", end_color="DDEBF7", fill_type="solid"),
        '11/14': PatternFill(start_color="FCE4D6", end_color="FCE4D6", fill_type="solid"),
        '12/15': PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid"),
        '13/16': PatternFill(start_color="E4DFEC", end_color="E4DFEC", fill_type="solid"),
        '14/17': PatternFill(start_color="F8CBAD", end_color="F8CBAD", fill_type="solid"),
    }
    thin_border = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin')
    )
    
    # Configurazione fogli principali allineati al template.
    contatori_monofase_principale = list(contatori_per_tipo['monofase'])
    if opzioni_cfg.get('unisci_trifase_diretti_in_monofase', True):
        contatori_monofase_principale += contatori_per_tipo['trifase_dir']

    fogli_principali = [
        (
            nome_foglio_excel(fogli_cfg.get('monofase'), 'MONOFASE'),
            contatori_monofase_principale,
            mono_fill,
        ),
        (nome_foglio_excel(fogli_cfg.get('emme2'), 'EMME2'), contatori_per_tipo['fotovoltaico'], foto_fill),
        (
            nome_foglio_excel(fogli_cfg.get('semidiretti'), 'SEMIDIRETTI'),
            contatori_per_tipo['trifase_semi'],
            tri_semi_fill,
        ),
    ]
    fogli_dettaglio_finali = [
        (
            nome_foglio_excel(fogli_cfg.get('solo_monofase'), 'SOLO MONOFASE'),
            contatori_per_tipo['monofase'],
            mono_fill,
        ),
        (
            nome_foglio_excel(fogli_cfg.get('solo_trifase_diretti'), 'SOLO TRIFASE DIRETTI'),
            contatori_per_tipo['trifase_dir'],
            tri_dir_fill,
        ),
    ]
    if not opzioni_cfg.get('crea_fogli_dettaglio', True):
        if opzioni_cfg.get('unisci_trifase_diretti_in_monofase', True):
            fogli_dettaglio_finali = []
        else:
            fogli_dettaglio_finali = fogli_dettaglio_finali[-1:]
    
    import config
    # Per Pavia usa Località al posto di Comune (o se abilitato per la provincia attiva)
    usa_localita = (
        PROVINCIA_ATTIVA.upper() == 'PAVIA'
        or getattr(config, 'USA_LOCALITA_COME_COMUNE', False)
    )
    
    headers = get_headers_lettere(lettere_cfg)
    archive_rows = []
    
    # Verifica CAP disponibili per i comuni (considerando fallback comune/località)
    def _luogo_di_riferimento(p):
        loc = testo_pulito(p.get('LOCALITA', ''))
        com = testo_pulito(p.get('COMUNE', ''))
        return (loc or com) if usa_localita else (com or loc)

    comuni_unici = set(_luogo_di_riferimento(p) for p in tutti_punti)
    comuni_unici.discard('')
    comuni_con_cap = sum(
        1 for c in comuni_unici
        if _cerca_cap_sicuro(c, prov=PROVINCIA_ATTIVA)
    )
    print(f"\n📮 CAP trovati per {comuni_con_cap}/{len(comuni_unici)} comuni")
    
    def scrivi_foglio_lettere(
        nome_foglio,
        contatori,
        fill_color,
        first_sheet=False,
        archivia_righe=True,
    ):
        """Scrive un foglio lettere con intestazioni e ordine colonne del template."""
        if first_sheet:
            ws = wb.active
            ws.title = nome_foglio
        else:
            ws = wb.create_sheet(nome_foglio)

        # Headers tabella (riga 1, come TEMPLATE LETTERE.xlsx)
        for col, header in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=col, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal='center', vertical='center')
            cell.border = thin_border

        # Dati contatori (ordina per data, poi luogo display/comune con fallback, poi via)
        def _get_luogo_sort(p):
            loc_s = testo_pulito(p.get('LOCALITA', ''))
            com_s = testo_pulito(p.get('COMUNE', ''))
            return (loc_s if loc_s else com_s) if usa_localita else (com_s if com_s else loc_s)

        contatori_ordinati = sorted(contatori, key=lambda x: (
            str(x.get('DATA_K', '')),
            _get_luogo_sort(x).lower(),
            str(x.get('VIA_K', '')).lower()
        ))

        row = 2
        for punto in contatori_ordinati:
            is_app = punto.get('IS_APPUNTAMENTO', False)
            fascia = punto.get('FASCIA_ORARIA', '')

            loc_punto = testo_pulito(punto.get('LOCALITA', ''))
            com_punto = testo_pulito(punto.get('COMUNE', ''))

            # Luogo visualizzato nel file lettere:
            # per Pavia usa Località ma se è vuota usa Comune come fallback (e viceversa per le altre province)
            if usa_localita:
                luogo_display = loc_punto if loc_punto else com_punto
            else:
                luogo_display = com_punto if com_punto else loc_punto

            # Ricerca CAP e provincia vincolata/prioritizzata dinamicamente alla provincia attiva:
            # - Se usa_localita: prova prima località (se non vuota); se non risolve il CAP, usa Comune come fallback.
            # - Se non usa_localita: prova prima Comune; se non risolve il CAP, usa Località come fallback.
            # In questo modo non si generano celle vuote né province errate (es. TV, CT, GE) per omonimie o frazioni.
            target_prov = PROVINCIA_ATTIVA
            candidati_luogo = (
                [loc_punto, com_punto] if usa_localita
                else [com_punto, loc_punto]
            )
            candidati_luogo = [c for c in candidati_luogo if c]

            cap = ''
            sigla_prov_riga = ''
            for cand in candidati_luogo:
                c_cap = _cerca_cap_sicuro(cand, prov=target_prov)
                c_prov = _cerca_provincia_sicuro(cand, prov=target_prov)

                if c_cap:
                    cap = c_cap
                    sigla_prov_riga = c_prov
                    break
                elif c_prov and not sigla_prov_riga:
                    sigla_prov_riga = c_prov

            if not sigla_prov_riga:
                luogo_fallback = luogo_display or com_punto or loc_punto
                sigla_prov_riga = _cerca_provincia_sicuro(luogo_fallback, prov=target_prov)

            if not cap:
                luogo_fallback = luogo_display or com_punto or loc_punto
                cap = _cerca_cap_sicuro(luogo_fallback, prov=target_prov)
            lettera = (
                valore_lettera_si if richiede_lettera(punto, lettere_cfg)
                else valore_lettera_no
            )
            nominativo = pulisci_nominativo(punto.get('CLI_K', ''))
            via_raw = punto.get('VIA_K', '')
            civico_raw = punto.get('CIVICO_K', '')
            via_pulita, civico_pulito = separa_via_civico(via_raw, civico_raw)
            tipo_ce = testo_pulito(punto.get('TIPO_CE_K', ''))
            is_azienda_archivio = is_azienda(nominativo, tipo_ce)
            
            values_by_key = {
                'nominativo': nominativo,
                'via': via_pulita,
                'civico': civico_pulito,
                'comune': luogo_display,
                'provincia': sigla_prov_riga,
                'cap': cap,
                'data_pianificazione': punto.get('DATA_ITALIANA', ''),
                'ora_pianificazione': formatta_fascia_oraria(fascia),
                'lettera': lettera,
                'pod': punto.get('POD_K', ''),
                'operatore': punto.get('TEC_K', ''),
                'posizione_mis': punto.get('NOTE_K', ''),
                'appuntamento': valore_lettera_si if is_app else '',
            }
            values = [values_by_key.get(chiave, '') for chiave in COLONNE_LETTERE_ORDINE]
            if archivia_righe:
                archive_rows.append({
                    'source_sheet': nome_foglio,
                    'row_num': row,
                    'nominativo': nominativo,
                    'via': via_pulita,
                    'civico': civico_pulito,
                    'comune': luogo_display,
                    'provincia': sigla_prov_riga,
                    'cap': cap,
                    'data_pianificazione': punto.get('DATA_ITALIANA', ''),
                    'ora_pianificazione': formatta_fascia_oraria(fascia),
                    'lettera': lettera,
                    'pod': punto.get('POD_K', ''),
                    'operatore': punto.get('TEC_K', ''),
                    'posizione_mis': punto.get('NOTE_K', ''),
                    'appuntamento': valore_lettera_si if is_app else '',
                    'tipo_misuratore': tipo_ce,
                    'eneltel': punto.get('ENEL_K', ''),
                    'lcl': punto.get('LCL', ''),
                    'telefono': punto.get('TEL_K', ''),
                    'email': punto.get('EMAIL_K', ''),
                    'is_azienda': is_azienda_archivio,
                })
            
            for col, value in enumerate(values, start=1):
                chiave_colonna = COLONNE_LETTERE_ORDINE[col - 1]
                cell = ws.cell(row=row, column=col, value=value)
                cell.border = thin_border
                cell.alignment = Alignment(vertical='center')
                
                if chiave_colonna == 'data_pianificazione':
                    cell.number_format = '@'  # Formato testo
                
                if chiave_colonna == 'ora_pianificazione':
                    if fascia in fascia_fills:
                        cell.fill = fascia_fills[fascia]
                    cell.font = Font(bold=True)
                    cell.alignment = Alignment(horizontal='center', vertical='center')
                elif chiave_colonna == 'lettera':
                    cell.alignment = Alignment(horizontal='center', vertical='center')
                    if lettera == valore_lettera_no:
                        cell.fill = PatternFill(start_color="D9D9D9", end_color="D9D9D9", fill_type="solid")  # Grigio
                elif chiave_colonna == 'appuntamento':
                    cell.alignment = Alignment(horizontal='center', vertical='center')
                # Evidenzia appuntamenti (non colorare fascia e lettera)
                elif is_app and chiave_colonna in (
                    'nominativo', 'via', 'civico', 'comune', 'provincia', 'cap',
                    'data_pianificazione',
                ):
                    cell.fill = app_fill
            
            row += 1
        
        # Se il foglio è vuoto (solo headers)
        if len(contatori) == 0:
            ws.cell(row=2, column=1, value="Nessun contatore di questo tipo")
            ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(headers))
            ws.cell(row=2, column=1).alignment = Alignment(horizontal='center')
            ws.cell(row=2, column=1).font = Font(italic=True)
        
        # Larghezza colonne allineata al template, con LETTERA finale.
        col_widths = [LARGHEZZE_COLONNE_LETTERE[k] for k in COLONNE_LETTERE_ORDINE]
        for idx, width in enumerate(col_widths, start=1):
            col_letter = chr(64 + idx) if idx <= 26 else 'A' + chr(64 + idx - 26)
            ws.column_dimensions[col_letter].width = width
        
        # Blocca intestazioni
        ws.freeze_panes = 'A2'
        ws.auto_filter.ref = f"A1:{chr(64 + len(headers))}1"

        print(f"   📋 Foglio '{nome_foglio}': {len(contatori)} contatori")

    first_sheet = True
    for nome_foglio, contatori, fill_color in fogli_principali:
        scrivi_foglio_lettere(nome_foglio, contatori, fill_color, first_sheet=first_sheet)
        first_sheet = False

    for nome_foglio, contatori, fill_color in fogli_dettaglio_finali:
        scrivi_foglio_lettere(nome_foglio, contatori, fill_color, archivia_righe=False)
    
    # Salva
    wb.save(filename)
    print(f"\n✅ File Excel salvato: {filename}")

    if archivia:
        try:
            from moduli import lettere as lettere_archive
            info = lettere_archive.registra_generazione(
                filename,
                date_da_elaborare[0],
                date_da_elaborare[-1],
                archive_rows,
                provincia=PROVINCIA_ATTIVA,
                user=utente,
            )
            print(
                "🗄️ Archivio Lettere aggiornato: "
                f"{info['totale_righe']} righe, {info['totale_aziende']} aziende"
            )
        except Exception as ex_archive:
            if filename_auto:
                try:
                    os.remove(filename)
                except OSError:
                    pass
            raise RuntimeError(
                f'File Excel non mantenuto perché l’archiviazione nel DB è fallita: '
                f'{ex_archive}'
            ) from ex_archive
    
    return filename


def main():
    """Main entry point."""
    args = parse_args()
    
    # Imposta provincia se specificata
    if args.provincia:
        os.environ['PROVINCIA'] = args.provincia.upper()
    
    # Importa dopo aver impostato la provincia
    from config import PROVINCIA_ATTIVA
    from moduli.data_loader import carica_dati_google_sheets
    
    print("=" * 60)
    print("📬 GENERAZIONE EXCEL PER LETTERE UTENTI")
    print("=" * 60)
    print(f"🗺️  Provincia: {PROVINCIA_ATTIVA}")
    print(f"📅 Date configurate: {len(DATE_LETTERE)}")
    
    # Carica dati
    print("\n📥 Caricamento dati dal FILONE...")
    df = carica_dati_google_sheets()
    
    if df is None or df.empty:
        print("❌ Errore: impossibile caricare i dati")
        return 1
    
    # Genera Excel con le date definite
    risultato = genera_excel_lettere(df, DATE_LETTERE)
    
    if risultato:
        print("\n✅ Operazione completata con successo!")
        return 0
    else:
        print("\n❌ Operazione fallita")
        return 1


if __name__ == '__main__':
    sys.exit(main())
