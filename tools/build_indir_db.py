# -*- coding: utf-8 -*-
"""
Costruisce il database SQLite indicizzato `cache/indir_piem.db` partendo da:
  - INDIR_PIEM_*.csv  (anagrafe nazionale numeri civici, ANNCSU)
  - T4_codicicatastali_comuni_*.pdf (mapping codice catastale -> nome comune)

Il CSV è troppo grande per Excel (~125 MB, 1.7M righe). Lo streamiamo in chunk
e popoliamo SQLite con un indice composito su (codice_comune, odonimo_norm)
per lookup O(log n) lato endpoint di verifica.

Uso:
    python tools/build_indir_db.py
    python tools/build_indir_db.py --csv path/al/INDIR_PIEM.csv --pdf path/al/T4.pdf
    python tools/build_indir_db.py --force   # rigenera anche se DB esiste già
"""
import argparse
import glob
import os
import re
import sqlite3
import sys
import time
import unicodedata

import pandas as pd
import pdfplumber

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB_PATH = os.path.join(SCRIPT_DIR, 'cache', 'indir_piem.db')
_ANAGRAFE_DIR = os.path.join(SCRIPT_DIR, 'data', 'anagrafe_indirizzi')
DEFAULT_CSV_GLOB = os.path.join(_ANAGRAFE_DIR, 'INDIR_PIEM_*.csv')
DEFAULT_PDF_GLOB = os.path.join(_ANAGRAFE_DIR, 'T4_codicicatastali_comuni_*.pdf')


# ============================================================================
# NORMALIZZAZIONE CONDIVISA (deve restare allineata a moduli/verifica_indirizzi.py)
# ============================================================================
_TIPI_VIA = (
    'VIA', 'VIALE', 'VICOLO', 'VICOLETTO', 'CORSO', 'PIAZZA', 'PIAZZALE',
    'PIAZZETTA', 'LARGO', 'STRADA', 'STRADALE', 'STRADELLA', 'BORGO', 'BORGATA',
    'FRAZIONE', 'LOCALITA', 'REGIONE', 'CASCINA', 'CASE', 'RIONE',
    'LUNGOFIUME', 'LUNGARNO', 'LUNGOPO', 'SALITA', 'DISCESA', 'GALLERIA',
    'PASSAGGIO', 'PONTE', 'RAMPA', 'SOTTOPASSO', 'TRAVERSA', 'CONTRADA',
    'TRAVERSA', 'CIRCONVALLAZIONE',
)
_TIPI_VIA_RE = re.compile(r'^(' + '|'.join(_TIPI_VIA) + r')\s+', re.IGNORECASE)


def normalizza_odonimo(s: str) -> str:
    """
    Normalizza un nome di via per il match.
    - uppercase
    - rimuove accenti
    - apostrofi/punteggiatura -> spazio
    - collassa spazi
    """
    if not s:
        return ''
    s = str(s).strip().upper()
    # Espandi abbreviazioni comuni PRIMA di togliere la punteggiatura
    s = re.sub(r"\bV\.LE\b", "VIALE", s)
    s = re.sub(r"\bC\.SO\b", "CORSO", s)
    s = re.sub(r"\bC/SO\b", "CORSO", s)
    s = re.sub(r"\bP\.ZZA\b", "PIAZZA", s)
    s = re.sub(r"\bP\.ZA\b", "PIAZZA", s)
    s = re.sub(r"\bP\.LE\b", "PIAZZALE", s)
    s = re.sub(r"\bLOC\.", "LOCALITA ", s)
    s = re.sub(r"\bFRAZ\.", "FRAZIONE ", s)
    s = re.sub(r"\bFR\.", "FRAZIONE ", s)
    s = re.sub(r"\bSTR\.", "STRADA ", s)
    s = re.sub(r"\bB\.GO\b", "BORGO", s)
    s = re.sub(r"\bV\.\s+", "VIA ", s)
    # Decomponi accenti
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(c for c in s if not unicodedata.combining(c))
    # Punteggiatura/apostrofi -> spazio
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    # Collassa spazi
    s = re.sub(r"\s+", " ", s).strip()
    return s


def odonimo_senza_tipo(odonimo_norm: str) -> str:
    """Ritorna l'odonimo normalizzato senza il prefisso di tipo (VIA, CORSO, ecc.)."""
    return _TIPI_VIA_RE.sub('', odonimo_norm).strip()


def normalizza_nome_comune(s: str) -> str:
    """Normalizza il nome di un comune per match."""
    if not s:
        return ''
    s = str(s).strip().upper()
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# ============================================================================
# PARSING PDF CODICI CATASTALI
# ============================================================================
_PDF_LINE_RE = re.compile(r'^([A-Z]\d{3})\s+(.+?)\s+\(([A-Z]{2})\)\s+([A-Z]{2})\s*$')


def parsa_pdf_catastali(pdf_path: str) -> list:
    """
    Estrae da T4_codicicatastali_comuni_*.pdf una lista di tuple
    (codice_catastale, nome_comune, provincia).
    Skippa linee di header e comuni 'soppresso'.
    """
    print(f"   📖 Parsing PDF: {os.path.basename(pdf_path)}")
    out = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            txt = page.extract_text() or ''
            for line in txt.splitlines():
                line = line.strip()
                m = _PDF_LINE_RE.match(line)
                if not m:
                    continue
                codice, nome, prov_paren, prov_finale = m.groups()
                # Salta i comuni soppressi (sono sostituiti da quelli aggregati)
                if '(soppresso)' in nome.lower():
                    continue
                # Pulisci eventuali caratteri di markup tra punti tipo ".ALDEIN."
                nome = re.sub(r'\.[A-Z\s]+\.', '', nome).strip()
                out.append((codice, nome, prov_finale))
    print(f"   ✅ Estratti {len(out)} comuni dal PDF")
    return out


# ============================================================================
# COSTRUZIONE DB
# ============================================================================
def crea_schema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.executescript('''
        DROP TABLE IF EXISTS comuni;
        DROP TABLE IF EXISTS civici;
        DROP TABLE IF EXISTS meta;

        CREATE TABLE comuni (
            codice          TEXT PRIMARY KEY,
            nome            TEXT NOT NULL,
            nome_norm       TEXT NOT NULL,
            provincia       TEXT NOT NULL
        );
        CREATE INDEX idx_comuni_nome_norm ON comuni(nome_norm);

        CREATE TABLE civici (
            codice_comune   TEXT NOT NULL,
            odonimo_norm    TEXT NOT NULL,
            odonimo_norm_nt TEXT NOT NULL,  -- senza prefisso tipo
            odonimo_orig    TEXT NOT NULL,
            civico          INTEGER,
            esponente       TEXT,
            lat             REAL,
            lon             REAL
        );
        CREATE INDEX idx_civ_comune_odonimo ON civici(codice_comune, odonimo_norm);
        CREATE INDEX idx_civ_comune_odonimo_nt ON civici(codice_comune, odonimo_norm_nt);

        CREATE TABLE meta (
            chiave TEXT PRIMARY KEY,
            valore TEXT
        );
    ''')
    conn.commit()


def popola_comuni(conn: sqlite3.Connection, comuni: list) -> None:
    cur = conn.cursor()
    rows = [
        (cod, nome, normalizza_nome_comune(nome), prov)
        for cod, nome, prov in comuni
    ]
    cur.executemany(
        'INSERT OR REPLACE INTO comuni (codice, nome, nome_norm, provincia) VALUES (?,?,?,?)',
        rows,
    )
    conn.commit()


def _parse_civico(val) -> int | None:
    try:
        if val is None or pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    try:
        s = str(val).strip()
        if not s:
            return None
        return int(s)
    except (ValueError, TypeError):
        return None


def _parse_coord(val) -> float | None:
    try:
        if val is None or pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    try:
        s = str(val).strip().replace(',', '.')
        if not s:
            return None
        return float(s)
    except (ValueError, TypeError):
        return None


def popola_civici(conn: sqlite3.Connection, csv_path: str, codici_validi: set) -> int:
    """
    Streama il CSV in chunk e inserisce in `civici`.
    Filtra per codici catastali presenti nella tabella `comuni` (skippa righe orfane).
    Ritorna il conteggio totale di righe inserite.
    """
    print(f"   📖 Streaming CSV: {os.path.basename(csv_path)}")
    cur = conn.cursor()
    chunk_size = 100_000
    total = 0
    t0 = time.time()

    # NB: usecols + chunksize ha un bug con dtype esplicito su alcune versioni
    # di pandas (IndexError in _concatenate_chunks). Leggiamo tutte le colonne
    # come stringhe e selezioniamo dopo.
    for i, chunk in enumerate(pd.read_csv(
        csv_path,
        sep=';',
        chunksize=chunk_size,
        dtype=str,
        encoding='utf-8',
        on_bad_lines='skip',
        low_memory=False,
    )):
        # Filtra per codici validi
        chunk = chunk[chunk['CODICE_COMUNE'].isin(codici_validi)]
        if chunk.empty:
            continue
        # Drop righe senza odonimo (sono SNC/spazi vuoti)
        chunk = chunk[chunk['ODONIMO'].notna() & (chunk['ODONIMO'].astype(str).str.strip() != '')]
        if chunk.empty:
            continue

        rows = []
        for _, r in chunk.iterrows():
            odon_orig = str(r['ODONIMO']).strip()
            odon_norm = normalizza_odonimo(odon_orig)
            if not odon_norm:
                continue
            odon_nt = odonimo_senza_tipo(odon_norm)
            rows.append((
                r['CODICE_COMUNE'],
                odon_norm,
                odon_nt,
                odon_orig,
                _parse_civico(r['CIVICO']),
                str(r['ESPONENTE']).strip() if pd.notna(r['ESPONENTE']) else None,
                _parse_coord(r.get('COORD_Y_COMUNE')),  # Y = lat
                _parse_coord(r.get('COORD_X_COMUNE')),  # X = lon
            ))

        if rows:
            cur.executemany(
                'INSERT INTO civici (codice_comune, odonimo_norm, odonimo_norm_nt, '
                'odonimo_orig, civico, esponente, lat, lon) VALUES (?,?,?,?,?,?,?,?)',
                rows,
            )
            total += len(rows)

        if (i + 1) % 5 == 0:
            conn.commit()
            elapsed = time.time() - t0
            print(f"      chunk {i+1}: {total:,} civici inseriti ({elapsed:.1f}s)")

    conn.commit()
    print(f"   ✅ Inseriti {total:,} civici in {time.time()-t0:.1f}s")
    return total


def scrivi_meta(conn: sqlite3.Connection, csv_path: str, pdf_path: str, n_righe: int) -> None:
    cur = conn.cursor()
    csv_st = os.stat(csv_path)
    pdf_st = os.stat(pdf_path)
    meta = {
        'csv_path': csv_path,
        'csv_size': str(csv_st.st_size),
        'csv_mtime': str(int(csv_st.st_mtime)),
        'pdf_path': pdf_path,
        'pdf_mtime': str(int(pdf_st.st_mtime)),
        'civici_count': str(n_righe),
        'built_at': str(int(time.time())),
    }
    cur.executemany(
        'INSERT OR REPLACE INTO meta (chiave, valore) VALUES (?,?)',
        list(meta.items()),
    )
    conn.commit()


def trova_file(pattern: str) -> str | None:
    matches = sorted(glob.glob(pattern))
    return matches[-1] if matches else None


def main() -> int:
    ap = argparse.ArgumentParser(description='Costruisce indir_piem.db da CSV+PDF')
    ap.add_argument('--csv', help='Percorso CSV INDIR_PIEM (default: glob nel root progetto)')
    ap.add_argument('--pdf', help='Percorso PDF codici catastali')
    ap.add_argument('--db', default=DEFAULT_DB_PATH, help='Path output SQLite')
    ap.add_argument('--force', action='store_true', help='Rigenera anche se DB esiste')
    args = ap.parse_args()

    csv_path = args.csv or trova_file(DEFAULT_CSV_GLOB)
    pdf_path = args.pdf or trova_file(DEFAULT_PDF_GLOB)

    if not csv_path or not os.path.exists(csv_path):
        print(f"❌ CSV non trovato. Cercato: {DEFAULT_CSV_GLOB}")
        return 1
    if not pdf_path or not os.path.exists(pdf_path):
        print(f"❌ PDF non trovato. Cercato: {DEFAULT_PDF_GLOB}")
        return 1

    if os.path.exists(args.db) and not args.force:
        print(f"⚠️  Il DB esiste già: {args.db}")
        print("   Usa --force per rigenerarlo.")
        return 0

    os.makedirs(os.path.dirname(args.db), exist_ok=True)

    print("="*60)
    print("  COSTRUZIONE indir_piem.db")
    print("="*60)
    print(f"   CSV: {csv_path}")
    print(f"   PDF: {pdf_path}")
    print(f"   DB:  {args.db}")
    print()

    conn = sqlite3.connect(args.db)
    try:
        conn.execute('PRAGMA journal_mode = WAL')
        conn.execute('PRAGMA synchronous = NORMAL')

        crea_schema(conn)

        comuni = parsa_pdf_catastali(pdf_path)
        popola_comuni(conn, comuni)
        codici_validi = {c[0] for c in comuni}

        n = popola_civici(conn, csv_path, codici_validi)
        scrivi_meta(conn, csv_path, pdf_path, n)

        # Statistiche finali
        cur = conn.cursor()
        cur.execute("SELECT COUNT(DISTINCT codice_comune) FROM civici")
        n_comuni = cur.fetchone()[0]
        size_mb = os.path.getsize(args.db) / (1024 * 1024)
        print()
        print(f"✅ DB completato: {args.db}")
        print(f"   {n:,} civici · {n_comuni:,} comuni · {size_mb:.1f} MB")
    finally:
        conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
