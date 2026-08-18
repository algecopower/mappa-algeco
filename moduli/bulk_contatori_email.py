# -*- coding: utf-8 -*-
"""
moduli/bulk_contatori_email.py — Modulo per raggruppamento contatori multipli,
pluralizzazione testi email e generazione dinamica dell'elenco contatori in Excel.
"""

import io
import re
import unicodedata
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter


def sanitize_filename(name: str) -> str:
    """Pulisce una stringa per usarla in modo sicuro come nome file."""
    if not name:
        return 'Azienda'
    name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
    name = re.sub(r'[^\w\s-]', '', name).strip()
    name = re.sub(r'[-\s]+', '_', name)
    return name[:50] or 'Azienda'


def generate_contatori_excel(contatori: list[dict], nome_azienda: str = '') -> bytes:
    """
    Genera un file Excel formattato professionalmente con l'elenco dei contatori
    associati all'azienda per l'invio in allegato.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Elenco Contatori"

    # Stili
    brand_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    title_font = Font(name="Calibri", size=13, bold=True, color="1F4E78")
    subtitle_font = Font(name="Calibri", size=10, italic=True, color="555555")
    cell_font = Font(name="Calibri", size=10)
    bold_cell_font = Font(name="Calibri", size=10, bold=True)

    thin_border = Border(
        left=Side(style='thin', color='D9D9D9'),
        right=Side(style='thin', color='D9D9D9'),
        top=Side(style='thin', color='D9D9D9'),
        bottom=Side(style='thin', color='D9D9D9')
    )
    zebra_fill = PatternFill(start_color="F2F5F9", end_color="F2F5F9", fill_type="solid")

    # Intestazione documento
    title_text = "ELENCO CONTATORI IN SOSTITUZIONE"
    if nome_azienda:
        title_text += f" — {nome_azienda.upper()}"
    ws.cell(row=1, column=1, value=title_text).font = title_font
    ws.cell(row=2, column=1, value="Incarico ufficiale E-Distribuzione — Algeco Power Srl").font = subtitle_font

    headers = [
        ("N°", 6, 'center'),
        ("POD", 20, 'center'),
        ("Tipologia Misuratore", 22, 'left'),
        ("Indirizzo", 28, 'left'),
        ("Civico", 10, 'center'),
        ("Comune", 20, 'left'),
        ("Matricola / Codice", 22, 'center'),
        ("Data Prevista", 16, 'center'),
    ]

    header_row = 4
    for col_idx, (head, width, align) in enumerate(headers, start=1):
        cell = ws.cell(row=header_row, column=col_idx, value=head)
        cell.fill = brand_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal=align, vertical='center', wrap_text=True)
        cell.border = thin_border
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    ws.row_dimensions[header_row].height = 24

    for i, c in enumerate(contatori, start=1):
        r = header_row + i
        ws.row_dimensions[r].height = 20
        row_fill = zebra_fill if (i % 2 == 0) else None

        pod = str(c.get('pod') or c.get('pod_filone') or '').strip()
        tipo = str(c.get('tipologia_contatore') or c.get('tipo_misuratore') or 'N/D').strip()
        indirizzo = str(c.get('indirizzo') or c.get('via') or '').strip()
        civico = str(c.get('civico') or '').strip()
        comune = str(c.get('comune') or '').strip()
        matricola = str(c.get('matricola_filone') or c.get('matricola') or c.get('matricola_ante') or c.get('codice_ante') or '').strip()
        data_int = str(c.get('data_intervento') or c.get('data_pianificazione') or c.get('data') or '').strip()

        row_data = [
            (i, 'center', bold_cell_font),
            (pod, 'center', bold_cell_font),
            (tipo, 'left', cell_font),
            (indirizzo, 'left', cell_font),
            (civico, 'center', cell_font),
            (comune, 'left', cell_font),
            (matricola or '-', 'center', cell_font),
            (data_int or '-', 'center', cell_font),
        ]

        for col_idx, (val, align, font_style) in enumerate(row_data, start=1):
            cell = ws.cell(row=r, column=col_idx, value=val)
            cell.font = font_style
            cell.alignment = Alignment(horizontal=align, vertical='center')
            cell.border = thin_border
            if row_fill:
                cell.fill = row_fill

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def pluralize_template_text(text: str) -> str:
    """
    Adatta un testo template standard dal singolare al plurale
    per la comunicazione a società con più contatori.
    """
    if not text:
        return text

    replacements = [
        (r'\bil vostro contatore\b', 'i vostri contatori'),
        (r'\bdel vostro contatore\b', 'dei vostri contatori'),
        (r'\bal vostro contatore\b', 'ai vostri contatori'),
        (r'\bil contatore\b', 'i contatori'),
        (r'\bdel contatore\b', 'dei contatori'),
        (r'\bal contatore\b', 'ai contatori'),
        (r'\bsul contatore\b', 'sui contatori'),
        (r'\bdel dispositivo\b', 'dei dispositivi'),
        (r'\bil dispositivo\b', 'i dispositivi'),
        (r'\bassociato al POD in oggetto\b', 'associati ai POD indicati'),
        (r'\bassociato al POD\b', 'associati ai POD'),
        (r'\boggetto dell’intervento\b', 'oggetto degli interventi'),
        (r'\boggetto dell\'intervento\b', 'oggetto degli interventi'),
        (r'\bLa data prevista per l\'intervento è\b', 'Le date previste per gli interventi sono'),
    ]

    result = text
    for pattern, repl in replacements:
        result = re.sub(pattern, repl, result, flags=re.IGNORECASE)
    return result


def build_plural_email(
    template_body: str,
    template_subject: str,
    contatori: list[dict],
    fields: dict,
    email_tecnico: str = '',
) -> tuple[str, str, str | None]:
    """
    Costruisce subject, body e l'eventuale nome file Excel dinamico
    in base al numero di contatori dell'azienda:
    - N == 1: Singolare standard
    - N == 2: Plurale con entrambi i POD nell'oggetto e lista nel corpo
    - N > 2: Plurale con conteggio nell'oggetto e riferimento all'allegato Excel
    """
    n = len(contatori) if contatori else 1
    nome_azienda = (
        (contatori[0].get('nome_societa') or contatori[0].get('nome') or contatori[0].get('nominativo') or '')
        if contatori else ''
    ).strip()

    if n <= 1:
        # Singolare standard
        return template_subject, template_body, None

    # Pluralizzazione
    plural_body = pluralize_template_text(template_body)

    if n == 2:
        pod1 = contatori[0].get('pod') or contatori[0].get('pod_filone') or ''
        pod2 = contatori[1].get('pod') or contatori[1].get('pod_filone') or ''

        # Oggetto con entrambi i POD
        if template_subject:
            subj = template_subject
            subj = re.sub(r'SOSTITUZIONE CONTATORE.*', f'SOSTITUZIONE CONTATORI E-DISTRIBUZIONE POD {pod1}, {pod2}', subj, flags=re.IGNORECASE)
            if '{pod}' in subj or '{nome}' in subj or '{nome_societa}' in subj:
                subj = subj.replace('{pod}', f"{pod1}, {pod2}")
        else:
            subj = f"{nome_azienda}: SOSTITUZIONE CONTATORI E-DISTRIBUZIONE POD {pod1}, {pod2}"

        # Corpo con dettaglio dei 2 contatori
        c1 = contatori[0]
        c2 = contatori[1]
        dettagli_contatori = (
            f"\n\nRiepilogo contatori oggetto della sostituzione:\n"
            f"1) POD: {pod1} — {c1.get('indirizzo', '')} {c1.get('civico', '')} ({c1.get('comune', '')}) "
            f"[Tipo: {c1.get('tipologia_contatore') or c1.get('tipo_misuratore') or 'N/D'}"
            + (f", Matricola: {c1.get('matricola_filone') or c1.get('matricola') or c1.get('matricola_ante')}" if (c1.get('matricola_filone') or c1.get('matricola') or c1.get('matricola_ante')) else "")
            + "]\n"
            f"2) POD: {pod2} — {c2.get('indirizzo', '')} {c2.get('civico', '')} ({c2.get('comune', '')}) "
            f"[Tipo: {c2.get('tipologia_contatore') or c2.get('tipo_misuratore') or 'N/D'}"
            + (f", Matricola: {c2.get('matricola_filone') or c2.get('matricola') or c2.get('matricola_ante')}" if (c2.get('matricola_filone') or c2.get('matricola') or c2.get('matricola_ante')) else "")
            + "]"
        )

        # Sostituisci il blocco singolo POD nel corpo con il riepilogo
        if "POD (presente in fattura):" in plural_body:
            plural_body = re.sub(
                r'POD \(presente in fattura\):.*?(?=Precisiamo che|\n\nPrecisiamo)',
                dettagli_contatori.strip() + "\n\n",
                plural_body,
                flags=re.DOTALL
            )
        else:
            plural_body += dettagli_contatori

        return subj, plural_body, None

    else:
        # N > 2: Plurale con conteggio e allegato Excel
        safe_nome = sanitize_filename(nome_azienda)
        excel_filename = f"Elenco_Contatori_{safe_nome}.xlsx"

        if template_subject:
            subj = template_subject
            subj = re.sub(r'SOSTITUZIONE CONTATORE.*', f'SOSTITUZIONE CONTATORI E-DISTRIBUZIONE ({n} contatori)', subj, flags=re.IGNORECASE)
            subj = subj.replace('{pod}', f"({n} contatori)")
        else:
            subj = f"{nome_azienda}: SOSTITUZIONE CONTATORI E-DISTRIBUZIONE ({n} contatori)"

        nota_excel = (
            f"\n\nIn allegato alla presente trasmettiamo il file Excel riepilogativo ({excel_filename}) "
            f"con l'elenco completo dei {n} contatori oggetto dell’intervento (comprensivo di POD, tipologia misuratore, indirizzo e civico)."
        )

        if "POD (presente in fattura):" in plural_body:
            plural_body = re.sub(
                r'POD \(presente in fattura\):.*?(?=Precisiamo che|\n\nPrecisiamo)',
                nota_excel.strip() + "\n\n",
                plural_body,
                flags=re.DOTALL
            )
        else:
            plural_body += nota_excel

        return subj, plural_body, excel_filename
