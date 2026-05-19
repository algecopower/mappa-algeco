# -*- coding: utf-8 -*-
"""
Script per generare Excel per l'invio delle lettere agli utenti.

Genera un file Excel con 4 fogli separati per tipo di contatore:
- Monofase: contatori standard
- Fotovoltaici: contatori con Eneltel che termina con A o B
- Trifase Semidiretti: contatori trifase semidiretti
- Trifase Diretti: contatori trifase diretti (foglio separato)

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


# formatta_fascia_oraria() è ora importata da moduli/fasce_orarie.py


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


def richiede_lettera(punto):
    """
    Determina se il contatore richiede l'invio di una lettera.
    
    NON si invia lettera se:
    - È un appuntamento (hanno già concordato)
    - Posizione contatore è "Accessibile dall'esterno" (anche con "3" accanto)
    - Cliente è intestato a un Comune
    - Civico è SN o SNC
    
    Args:
        punto: Dizionario con i dati del contatore
        
    Returns:
        True se richiede lettera (SI'), False altrimenti (NO)
    """
    # Controlla se è un appuntamento (non serve lettera)
    if punto.get('IS_APPUNTAMENTO', False):
        return False
    
    # Controlla posizione contatore (NOTE_K)
    posizione = str(punto.get('NOTE_K', '')).strip().upper()
    if 'ACCESSIBILE' in posizione and 'ESTERNO' in posizione:
        return False
    
    # Controlla se intestato a Comune
    cliente = str(punto.get('CLI_K', '')).strip().upper()
    if cliente.startswith('COMUNE ') or cliente == 'COMUNE':
        return False
    
    # Controlla civico SN o SNC
    civico = str(punto.get('CIVICO_K', '')).strip().upper()
    if civico in ('SN', 'SNC', 'S.N.', 'S.N.C.', 'S/N', 'S/NC'):
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


def genera_excel_lettere(df, date_da_elaborare, filename=None, tappe_per_fascia=None):
    """
    Genera Excel per invio lettere con 4 fogli per tipo contatore.
    Elabora le date specificate.
    
    Fogli:
    - Monofase: contatori standard
    - Fotovoltaici: Eneltel termina con A o B  
    - Trifase Semidiretti: contatori trifase semidiretti
    - Trifase Diretti: contatori trifase diretti
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from moduli.percorso import ordina_percorso
    from config import PROVINCIA_ATTIVA, DEPOSITO, MATRICOLE_VIP
    
    # Determina modalità auto o fisso
    auto_mode = (tappe_per_fascia is None or str(tappe_per_fascia).lower() == 'auto')
    
    if not date_da_elaborare:
        print("❌ Nessuna data definita in DATE_LETTERE")
        return None
    
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
        
        # Lista tecnici per questa data
        tecnici_data = [t for t in MATRICOLE_VIP if t in df_data['TEC_K'].unique()]
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
    
    # Configurazione fogli (4 fogli)
    fogli_config = [
        ('Monofase', 'monofase', mono_fill),
        ('Fotovoltaici', 'fotovoltaico', foto_fill),
        ('Trifase Semidiretti', 'trifase_semi', tri_semi_fill),
        ('Trifase Diretti', 'trifase_dir', tri_dir_fill)
    ]
    
    # Stringa periodo
    if len(date_da_elaborare) == 1:
        periodo_str = f"Data: {formatta_data_italiana(date_da_elaborare[0])}"
    else:
        periodo_str = f"Dal {formatta_data_italiana(date_da_elaborare[0])} al {formatta_data_italiana(date_da_elaborare[-1])}"
    
    # Per Pavia usa Località al posto di Comune
    usa_localita = (PROVINCIA_ATTIVA.upper() == 'PAVIA')
    
    # Headers tabella nell'ordine richiesto
    headers = [
        "Nominativo", "Via", "Civico", "Località" if usa_localita else "Comune", "Provincia", "CAP",
        "POD", "Operatore", "Appuntamento", "Ora Appuntamento", "Posizione mis.", 
        "Data Pianificazione", "Fascia Oraria", "Lettera"
    ]
    
    # Verifica CAP disponibili per i comuni
    comuni_unici = set(str(p.get('COMUNE', '')).strip() for p in tutti_punti if p.get('COMUNE'))
    comuni_con_cap = sum(1 for c in comuni_unici if cerca_cap(c))
    print(f"\n📮 CAP trovati per {comuni_con_cap}/{len(comuni_unici)} comuni")
    
    first_sheet = True
    
    for nome_foglio, tipo_key, fill_color in fogli_config:
        contatori = contatori_per_tipo[tipo_key]
        
        if first_sheet:
            ws = wb.active
            ws.title = nome_foglio
            first_sheet = False
        else:
            ws = wb.create_sheet(nome_foglio)
        
        # Intestazione con info
        ws['A1'] = f"LETTERE - {nome_foglio.upper()}"
        ws['A1'].font = Font(bold=True, size=14)
        ws['A1'].fill = fill_color
        ws['A2'] = periodo_str
        ws['A2'].font = Font(italic=True, size=11)
        ws['A3'] = f"Totale contatori: {len(contatori)}"
        
        # Headers tabella (riga 5)
        for col, header in enumerate(headers, start=1):
            cell = ws.cell(row=5, column=col, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal='center', vertical='center')
            cell.border = thin_border
        
        # Dati contatori (ordina per data, poi località/comune, poi via)
        campo_luogo = 'LOCALITA' if usa_localita else 'COMUNE'
        contatori_ordinati = sorted(contatori, key=lambda x: (
            str(x.get('DATA_K', '')),
            str(x.get(campo_luogo, '')).lower(),
            str(x.get('VIA_K', '')).lower()
        ))
        
        row = 6
        for punto in contatori_ordinati:
            is_app = punto.get('IS_APPUNTAMENTO', False)
            ora_app = punto.get('ORA_APPUNTAMENTO', '') or ''
            fascia = punto.get('FASCIA_ORARIA', '')
            # Per Pavia usa Località per tutto (più precisa del Comune)
            luogo_display = str(punto.get('LOCALITA', '')).strip() if usa_localita else str(punto.get('COMUNE', '')).strip()
            comune_per_cap = luogo_display if usa_localita else str(punto.get('COMUNE', '')).strip()
            cap = cerca_cap(comune_per_cap)
            sigla_prov_riga = cerca_provincia(comune_per_cap)
            lettera = "SI'" if richiede_lettera(punto) else "NO"
            nominativo = pulisci_nominativo(punto.get('CLI_K', ''))
            via_raw = punto.get('VIA_K', '')
            civico_raw = punto.get('CIVICO_K', '')
            via_pulita, civico_pulito = separa_via_civico(via_raw, civico_raw)
            
            values = [
                nominativo,                        # Nominativo (pulito)
                via_pulita,                        # Via (pulita)
                civico_pulito,                     # Civico (estratto se necessario)
                luogo_display,                     # Comune o Località (Pavia)
                sigla_prov_riga,                   # Provincia (TO/PV/LO)
                cap,                               # CAP
                punto.get('POD_K', ''),           # POD
                punto.get('TEC_K', ''),           # Operatore
                "SÌ" if is_app else "",           # Appuntamento
                ora_app,                           # Ora Appuntamento
                punto.get('NOTE_K', ''),          # Posizione mis.
                punto.get('DATA_ITALIANA', ''),   # Data Pianificazione
                formatta_fascia_oraria(fascia),   # Fascia Oraria
                lettera                            # Lettera
            ]
            
            for col, value in enumerate(values, start=1):
                cell = ws.cell(row=row, column=col, value=value)
                cell.border = thin_border
                cell.alignment = Alignment(vertical='center')
                
                # Formatta Data Pianificazione come testo (colonna 12)
                if col == 12:
                    cell.number_format = '@'  # Formato testo
                
                # Colora la cella della fascia oraria (colonna 13)
                if col == 13:  # Fascia Oraria
                    if fascia in fascia_fills:
                        cell.fill = fascia_fills[fascia]
                    cell.font = Font(bold=True)
                    cell.alignment = Alignment(horizontal='center', vertical='center')
                # Colora la cella Lettera (colonna 14)
                elif col == 14:  # Lettera
                    cell.alignment = Alignment(horizontal='center', vertical='center')
                    if lettera == "NO":
                        cell.fill = PatternFill(start_color="D9D9D9", end_color="D9D9D9", fill_type="solid")  # Grigio
                # Evidenzia appuntamenti (non colorare fascia e lettera)
                elif is_app and col < 13:
                    cell.fill = app_fill
            
            row += 1
        
        # Se il foglio è vuoto (solo headers)
        if len(contatori) == 0:
            ws.cell(row=6, column=1, value="Nessun contatore di questo tipo")
            ws.merge_cells(start_row=6, start_column=1, end_row=6, end_column=len(headers))
            ws.cell(row=6, column=1).alignment = Alignment(horizontal='center')
            ws.cell(row=6, column=1).font = Font(italic=True)
        
        # Larghezza colonne nel nuovo ordine
        # Nominativo, Via, Civico, Comune, Provincia, CAP, POD, Operatore, Appuntamento, Ora App, Posizione, Data, Fascia, Lettera
        col_widths = [25, 25, 8, 18, 10, 7, 20, 14, 12, 15, 18, 18, 22, 10]
        for idx, width in enumerate(col_widths, start=1):
            col_letter = chr(64 + idx) if idx <= 26 else 'A' + chr(64 + idx - 26)
            ws.column_dimensions[col_letter].width = width
        
        # Blocca intestazioni
        ws.freeze_panes = 'A6'
        
        print(f"   📋 Foglio '{nome_foglio}': {len(contatori)} contatori")
    
    # ------------------------------------------------------------------
    # Foglio "Contatti Aziendali"
    # Elenca le aziende rilevate tra tutti i contatori del periodo,
    # usando i criteri di moduli/rilevamento_aziende.is_azienda + tutti
    # i Semidiretti (al 99% sono società). TEL/MAIL/PEC vuoti (da
    # compilare manualmente). Dedupe per POD.
    # ------------------------------------------------------------------
    ws_aziende = wb.create_sheet("Contatti Aziendali")

    headers_aziende = ["NOMINATIVO", "COMUNE", "TEL", "MAIL", "PEC", "POD"]

    ws_aziende['A1'] = "CONTATTI AZIENDALI"
    ws_aziende['A1'].font = Font(bold=True, size=14)
    ws_aziende['A1'].fill = header_fill
    ws_aziende['A1'].alignment = Alignment(horizontal='left', vertical='center')
    ws_aziende['A2'] = periodo_str
    ws_aziende['A2'].font = Font(italic=True, size=11)

    # Headers tabella (riga 5)
    for col, header in enumerate(headers_aziende, start=1):
        cell = ws_aziende.cell(row=5, column=col, value=header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal='center', vertical='center')
        cell.border = thin_border

    aziende_rows = []
    pod_visti = set()
    for punto in tutti_punti:
        nominativo = pulisci_nominativo(punto.get('CLI_K', '')) or ''
        tipo_ce = str(punto.get('TIPO_CE_K', '')).strip()
        if not is_azienda(nominativo, tipo_ce):
            continue
        pod = str(punto.get('POD_K', '')).strip()
        chiave = pod.upper() if pod else f"__NOPOD__{nominativo.upper()}"
        if chiave in pod_visti:
            continue
        pod_visti.add(chiave)
        comune = (
            str(punto.get('LOCALITA', '')).strip() if usa_localita
            else str(punto.get('COMUNE', '')).strip()
        )
        aziende_rows.append((nominativo, comune, pod))

    # Ordina per nominativo
    aziende_rows.sort(key=lambda r: r[0].lower())

    row = 6
    for nominativo, comune, pod in aziende_rows:
        values = [nominativo, comune, '', '', '', pod]
        for col, value in enumerate(values, start=1):
            cell = ws_aziende.cell(row=row, column=col, value=value)
            cell.border = thin_border
            cell.alignment = Alignment(vertical='center')
        row += 1

    if not aziende_rows:
        ws_aziende.cell(row=6, column=1, value="Nessuna azienda rilevata")
        ws_aziende.merge_cells(
            start_row=6, start_column=1,
            end_row=6, end_column=len(headers_aziende),
        )
        ws_aziende.cell(row=6, column=1).alignment = Alignment(horizontal='center')
        ws_aziende.cell(row=6, column=1).font = Font(italic=True)

    # Larghezze colonne: Nominativo, Comune, Tel, Mail, Pec, POD
    col_widths_aziende = [35, 22, 16, 28, 28, 22]
    for idx, width in enumerate(col_widths_aziende, start=1):
        col_letter = chr(64 + idx)
        ws_aziende.column_dimensions[col_letter].width = width

    ws_aziende.freeze_panes = 'A6'

    print(f"   📋 Foglio 'Contatti Aziendali': {len(aziende_rows)} aziende")

    # Crea foglio riepilogo
    ws_summary = wb.create_sheet("RIEPILOGO", 0)
    ws_summary['A1'] = "RIEPILOGO LETTERE"
    ws_summary['A1'].font = Font(bold=True, size=16)
    ws_summary['A2'] = periodo_str
    ws_summary['A2'].font = Font(italic=True, size=12)
    
    # Tabella riepilogo
    row = 4
    headers_summary = ["Tipo Contatore", "Quantità"]
    for col, header in enumerate(headers_summary, start=1):
        cell = ws_summary.cell(row=row, column=col, value=header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal='center')
        cell.border = thin_border
    
    row = 5
    totale_generale = 0
    for nome_foglio, tipo_key, fill_color in fogli_config:
        count = len(contatori_per_tipo[tipo_key])
        totale_generale += count
        
        cell1 = ws_summary.cell(row=row, column=1, value=nome_foglio)
        cell1.border = thin_border
        cell1.fill = fill_color
        
        cell2 = ws_summary.cell(row=row, column=2, value=count)
        cell2.border = thin_border
        cell2.alignment = Alignment(horizontal='center')
        cell2.fill = fill_color
        
        row += 1
    
    # Totale
    cell1 = ws_summary.cell(row=row, column=1, value="TOTALE")
    cell1.border = thin_border
    cell1.font = Font(bold=True)
    
    cell2 = ws_summary.cell(row=row, column=2, value=totale_generale)
    cell2.border = thin_border
    cell2.font = Font(bold=True)
    cell2.alignment = Alignment(horizontal='center')
    
    # Larghezza colonne riepilogo
    ws_summary.column_dimensions['A'].width = 25
    ws_summary.column_dimensions['B'].width = 15
    
    # Salva
    wb.save(filename)
    print(f"\n✅ File Excel salvato: {filename}")
    
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
