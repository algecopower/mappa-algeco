# -*- coding: utf-8 -*-
"""
Strumento A/B: confronta l'algoritmo di ordinamento percorsi LEGACY
con quello NUOVO (refactorato) su backup storici.

Uso:
    python tools/confronta_percorsi.py --provincia torino
    python tools/confronta_percorsi.py --provincia pavia --backup-glob "backup/pavia/filone_pavia_2026-04-2*.json"
    python tools/confronta_percorsi.py --provincia torino --output /tmp/ab_torino.html --max-giornate 5

Produce:
- <output>.html : report leggibile con tabella + mini-mappe Leaflet
- <output>.csv  : stesso contenuto in formato tabellare per Excel

Lavora offline: legge i file in backup/{provincia}/filone_*.json, ricostruisce
il DataFrame come il runtime, e per ogni coppia (data, operatore) ricalcola
i percorsi con entrambi gli algoritmi usando la stessa matrice OSRM.

OSRM deve essere attivo (localhost:5000).
"""
import argparse
import csv
import glob
import html
import json
import math
import os
import sys
import time
from datetime import datetime

# Assicura l'import dai moduli del progetto
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pandas as pd


def _provincia_da_arg(nome):
    n = nome.strip().lower()
    if n in ('torino', 'to'):
        return 'TORINO'
    if n in ('pavia', 'pv'):
        return 'PAVIA'
    raise ValueError(f"Provincia non riconosciuta: {nome}")


def carica_backup(path):
    """Legge un file di backup e ritorna il dict completo."""
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _lat_lon_float(val):
    """Converte '45,003609' o '45.003609' in float, None altrimenti."""
    if val is None or val == '':
        return None
    s = str(val).strip().replace(',', '.')
    try:
        return float(s)
    except ValueError:
        return None


def prepara_dataframe(backup):
    """Costruisce un DataFrame minimo con le colonne necessarie all'ordinamento."""
    headers = backup['headers']
    righe = backup['righe']
    df = pd.DataFrame(righe, columns=headers)

    # Coordinate
    df['Lat'] = df['Latitudine'].apply(_lat_lon_float)
    df['Lon'] = df['Longitudine'].apply(_lat_lon_float)

    # Note appuntamento + flag IS_APPUNTAMENTO (stessa regex del data_loader)
    import re as _re
    _pat_app = _re.compile(r'\bapp\b', _re.IGNORECASE)
    note_raw = df['NOTE APP'].fillna('').astype(str).str.strip() if 'NOTE APP' in df.columns else pd.Series([''] * len(df))
    df['NOTE_APP_RAW'] = note_raw
    df['IS_APPUNTAMENTO'] = note_raw.apply(lambda n: bool(_pat_app.search(n)) if n else False)
    df['ORA_APPUNTAMENTO'] = None  # popolato dall'algoritmo

    # Dati identificativi minimi
    for col in ('LCL', 'Eneltel', 'Operatore', 'Comune', 'Indirizzo', 'Num', 'DATA PIAN', 'POD', 'NOMINATIVO'):
        if col not in df.columns:
            df[col] = ''

    # Normalizza data (formato sorgente: DD/MM/YYYY)
    def _parse_data(s):
        s = str(s).strip()
        if not s:
            return None
        for fmt in ('%d/%m/%Y', '%Y-%m-%d'):
            try:
                return datetime.strptime(s, fmt).date().isoformat()
            except ValueError:
                continue
        return None

    df['DATA_ISO'] = df['DATA PIAN'].apply(_parse_data)

    return df


def raggruppa_tecnici(df, min_punti=3, solo_da_oggi=False):
    """Raggruppa (DATA_ISO, Operatore) e ritorna lista di (data, operatore, punti_dict)."""
    gruppi = []
    mask = df['Lat'].notna() & df['Lon'].notna() & df['DATA_ISO'].notna() & (df['Operatore'].astype(str).str.strip() != '')
    df = df[mask]
    oggi = datetime.now().date().isoformat()

    for (data, op), sub in df.groupby(['DATA_ISO', 'Operatore']):
        if solo_da_oggi and data < oggi:
            continue
        if len(sub) < min_punti:
            continue
        punti = []
        for _, row in sub.iterrows():
            punti.append({
                'Lat': float(row['Lat']),
                'Lon': float(row['Lon']),
                'NOTE_APP_RAW': row['NOTE_APP_RAW'],
                'IS_APPUNTAMENTO': bool(row['IS_APPUNTAMENTO']),
                'ORA_APPUNTAMENTO': row.get('ORA_APPUNTAMENTO'),
                'Eneltel': str(row.get('Eneltel', '')).strip(),
                'Comune': str(row.get('Comune', '')).strip(),
                'Indirizzo': str(row.get('Indirizzo', '')).strip(),
                'Num': str(row.get('Num', '')).strip(),
            })
        gruppi.append((data, op, punti))
    return gruppi


def _haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


def _angolo_tra_vettori(lat_a, lon_a, lat_b, lon_b, lat_c, lon_c):
    """Angolo in gradi tra i vettori AB e BC (per contare inversioni di direzione)."""
    vx1 = lon_b - lon_a
    vy1 = lat_b - lat_a
    vx2 = lon_c - lon_b
    vy2 = lat_c - lat_b
    n1 = math.hypot(vx1, vy1)
    n2 = math.hypot(vx2, vy2)
    if n1 == 0 or n2 == 0:
        return 0.0
    cos_a = (vx1 * vx2 + vy1 * vy2) / (n1 * n2)
    cos_a = max(-1.0, min(1.0, cos_a))
    return math.degrees(math.acos(cos_a))


def calcola_metriche(percorso, deposito, matrice_distanze=None, matrice_durate=None, idx_map=None):
    """
    Calcola le metriche per un percorso ordinato.
    idx_map: dict identificativo_punto -> indice 1-based nella matrice (0 = deposito).
    """
    n = len(percorso)
    metriche = {
        'num_tappe': n,
        'km_totali': 0.0,
        'minuti_totali': 0.0,
        'inversioni_gt_90': 0,
        'max_hop_km': 0.0,
        'app_fuori_finestra': 0,
    }
    if n == 0:
        return metriche

    def _chiave(p):
        # Identificativo stabile per lookup in idx_map
        return p.get('Eneltel') or f"{p['Lat']:.6f},{p['Lon']:.6f}"

    # km e minuti totali (deposito -> tappe -> deposito)
    sequenza = [deposito] + percorso + [deposito]
    for i in range(len(sequenza) - 1):
        a, b = sequenza[i], sequenza[i + 1]
        if matrice_distanze is not None and idx_map is not None:
            ka = 0 if i == 0 else idx_map.get(_chiave(a), None)
            kb = 0 if (i + 1 == len(sequenza) - 1) else idx_map.get(_chiave(b), None)
            if ka is not None and kb is not None:
                km = matrice_distanze[ka][kb]
                mn = matrice_durate[ka][kb] if matrice_durate else 0
            else:
                km = _haversine_km(a['Lat'], a['Lon'], b['Lat'], b['Lon'])
                mn = km / 35 * 60
        else:
            km = _haversine_km(a['Lat'], a['Lon'], b['Lat'], b['Lon'])
            mn = km / 35 * 60
        metriche['km_totali'] += km
        metriche['minuti_totali'] += mn
        if km > metriche['max_hop_km']:
            metriche['max_hop_km'] = km

    # Inversioni di direzione > 90° (terzetti consecutivi)
    punti_tutti = [deposito] + percorso + [deposito]
    for i in range(len(punti_tutti) - 2):
        a, b, c = punti_tutti[i], punti_tutti[i + 1], punti_tutti[i + 2]
        ang = _angolo_tra_vettori(a['Lat'], a['Lon'], b['Lat'], b['Lon'], c['Lat'], c['Lon'])
        if ang > 90:
            metriche['inversioni_gt_90'] += 1

    return metriche


def _costruisci_idx_map(punti_originali):
    """Mappa identificativo_punto -> indice 1-based (0 riservato al deposito)."""
    mappa = {}
    for i, p in enumerate(punti_originali, start=1):
        k = p.get('Eneltel') or f"{p['Lat']:.6f},{p['Lon']:.6f}"
        mappa[k] = i
    return mappa


def esegui_confronto(gruppo, deposito, ordina_legacy, ordina_nuovo, ottieni_matrice):
    """Esegue A/B su un singolo gruppo (data, operatore)."""
    data, operatore, punti = gruppo

    # Matrice OSRM una volta sola sui punti originali
    ris_matrice = ottieni_matrice(punti, deposito, usa_osrm=True)
    if not ris_matrice.get('successo'):
        return None
    matrice_distanze = ris_matrice.get('distanze')
    matrice_durate = ris_matrice.get('durate')
    idx_map = _costruisci_idx_map(punti)

    # Esegui entrambi gli algoritmi su copie profonde (evita side-effect)
    punti_a = [dict(p) for p in punti]
    punti_b = [dict(p) for p in punti]

    t0 = time.time()
    ord_legacy = ordina_legacy(punti_a, deposito, usa_osrm_ottimizzazione=True)
    t_legacy = time.time() - t0

    t0 = time.time()
    ord_nuovo = ordina_nuovo(punti_b, deposito, usa_osrm_ottimizzazione=True)
    t_nuovo = time.time() - t0

    met_legacy = calcola_metriche(ord_legacy, deposito, matrice_distanze, matrice_durate, idx_map)
    met_nuovo = calcola_metriche(ord_nuovo, deposito, matrice_distanze, matrice_durate, idx_map)

    return {
        'data': data,
        'operatore': operatore,
        'n_punti': len(punti),
        'punti_orig': punti,
        'ord_legacy': ord_legacy,
        'ord_nuovo': ord_nuovo,
        'metriche_legacy': met_legacy,
        'metriche_nuovo': met_nuovo,
        'tempo_legacy_s': round(t_legacy, 2),
        'tempo_nuovo_s': round(t_nuovo, 2),
        'fonte_matrice': ris_matrice.get('fonte', 'N/A'),
    }


def esegui_confronto_access(gruppo, deposito, ordina_nuovo, ottieni_matrice):
    """
    A/B "diff-access": stesso algoritmo (NUOVO) confrontato con e senza
    access point UPS-style. Usa una matrice OSRM separata per ciascun caso
    (raw vs access) cosi' i km/minuti riflettono il routing reale.

    L'ordine "RAW" simula il comportamento pre-fix (snap geometricamente alla
    strada piu' vicina, anche se sbagliata).
    L'ordine "ACCESS" usa Lat_access/Lon_access (snap-by-name) per il routing.
    """
    data, operatore, punti = gruppo

    # Calcola access points (popola Lat_access/Lon_access)
    from moduli.access_points import arricchisci_punti
    punti_access = [dict(p) for p in punti]
    arricchisci_punti(punti_access, max_workers=8, persisti_cache=True)

    # ----- RAW: ordina usando solo Lat/Lon originali -----
    punti_raw = [dict(p) for p in punti]
    for p in punti_raw:
        p.pop('Lat_access', None)
        p.pop('Lon_access', None)

    ris_matrice_raw = ottieni_matrice(punti_raw, deposito, usa_osrm=True)
    if not ris_matrice_raw.get('successo'):
        return None
    idx_raw = _costruisci_idx_map(punti_raw)

    t0 = time.time()
    ord_raw = ordina_nuovo([dict(p) for p in punti_raw], deposito, usa_osrm_ottimizzazione=True)
    t_raw = time.time() - t0

    # ----- ACCESS: ordina usando Lat_access/Lon_access -----
    ris_matrice_acc = ottieni_matrice(punti_access, deposito, usa_osrm=True)
    if not ris_matrice_acc.get('successo'):
        return None
    idx_acc = _costruisci_idx_map(punti_access)

    t0 = time.time()
    ord_acc = ordina_nuovo([dict(p) for p in punti_access], deposito, usa_osrm_ottimizzazione=True)
    t_acc = time.time() - t0

    met_raw = calcola_metriche(ord_raw, deposito, ris_matrice_raw['distanze'], ris_matrice_raw['durate'], idx_raw)
    met_acc = calcola_metriche(ord_acc, deposito, ris_matrice_acc['distanze'], ris_matrice_acc['durate'], idx_acc)

    # Conta differenze d'ordine (per Eneltel)
    seq_raw = [p.get('Eneltel') or f"{p['Lat']:.6f},{p['Lon']:.6f}" for p in ord_raw]
    seq_acc = [p.get('Eneltel') or f"{p['Lat']:.6f},{p['Lon']:.6f}" for p in ord_acc]
    diff_pos = sum(1 for a, b in zip(seq_raw, seq_acc) if a != b)

    # Distribuzione access_source per i punti
    src_count = {}
    for p in punti_access:
        s = p.get('access_source') or 'unknown'
        src_count[s] = src_count.get(s, 0) + 1

    # Casi specifici: tappe con access_source == 'osrm' (snap-by-name riuscito)
    n_snap = src_count.get('osrm', 0)

    return {
        'data': data,
        'operatore': operatore,
        'n_punti': len(punti),
        'punti_orig': punti_access,
        'ord_legacy': ord_raw,        # campo riusato per HTML
        'ord_nuovo': ord_acc,         # idem
        'metriche_legacy': met_raw,
        'metriche_nuovo': met_acc,
        'tempo_legacy_s': round(t_raw, 2),
        'tempo_nuovo_s': round(t_acc, 2),
        'fonte_matrice': 'OSRM (access vs raw)',
        # Specifici per diff-access
        'access_diff_pos': diff_pos,
        'access_sources': src_count,
        'n_snap_osrm': n_snap,
    }


def _mini_mappa_leaflet(percorso_leg, percorso_new, deposito, titolo_id):
    """Genera un div HTML con due mini-mappe Leaflet (legacy vs nuovo) affiancate."""
    def _coords_js(percorso):
        pts = [[deposito['Lat'], deposito['Lon']]]
        pts += [[p['Lat'], p['Lon']] for p in percorso]
        pts.append([deposito['Lat'], deposito['Lon']])
        return json.dumps(pts)

    map_leg_id = f"map_leg_{titolo_id}"
    map_new_id = f"map_new_{titolo_id}"
    coords_leg = _coords_js(percorso_leg)
    coords_new = _coords_js(percorso_new)

    return f"""
<div style="display:flex;gap:10px;margin:10px 0;">
  <div style="flex:1;">
    <div style="font-weight:bold;color:#c00;">LEGACY</div>
    <div id="{map_leg_id}" style="height:320px;border:1px solid #ccc;"></div>
  </div>
  <div style="flex:1;">
    <div style="font-weight:bold;color:#060;">NUOVO</div>
    <div id="{map_new_id}" style="height:320px;border:1px solid #ccc;"></div>
  </div>
</div>
<script>
(function(){{
  var pts_leg = {coords_leg};
  var pts_new = {coords_new};
  function mk(id, pts, color){{
    var map = L.map(id);
    L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',
      {{maxZoom: 19, attribution: '© OSM'}}).addTo(map);
    var line = L.polyline(pts, {{color: color, weight: 3}}).addTo(pts.length ? map : map);
    pts.forEach(function(p, i){{
      var icon = (i === 0 || i === pts.length - 1)
        ? L.divIcon({{html: '<div style=\"background:#333;color:white;padding:2px 5px;border-radius:3px;\">D</div>', className:'', iconSize:[20,20]}})
        : L.divIcon({{html: '<div style=\"background:'+color+';color:white;padding:1px 4px;border-radius:10px;font-size:10px;\">'+i+'</div>', className:'', iconSize:[18,18]}});
      L.marker(p, {{icon: icon}}).addTo(map);
    }});
    map.fitBounds(line.getBounds(), {{padding: [15,15]}});
  }}
  mk('{map_leg_id}', pts_leg, '#c00');
  mk('{map_new_id}', pts_new, '#060');
}})();
</script>
"""


def genera_report_html(risultati, path_output, provincia, deposito, diff_access=False):
    """Genera un report HTML con tabella + mini-mappe affiancate.

    Se ``diff_access=True``, ``ord_legacy``/``metriche_legacy`` rappresentano
    il risultato con coords RAW (pre-fix), e ``ord_nuovo``/``metriche_nuovo``
    quello con access points UPS-style.
    """
    n = len(risultati)
    if n == 0:
        with open(path_output, 'w', encoding='utf-8') as f:
            f.write("<html><body><h1>Nessun gruppo idoneo trovato</h1></body></html>")
        return

    # Aggregati
    agg_leg = {'km': 0, 'min': 0, 'inv': 0, 'maxhop': 0}
    agg_new = {'km': 0, 'min': 0, 'inv': 0, 'maxhop': 0}
    for r in risultati:
        agg_leg['km'] += r['metriche_legacy']['km_totali']
        agg_leg['min'] += r['metriche_legacy']['minuti_totali']
        agg_leg['inv'] += r['metriche_legacy']['inversioni_gt_90']
        agg_leg['maxhop'] = max(agg_leg['maxhop'], r['metriche_legacy']['max_hop_km'])
        agg_new['km'] += r['metriche_nuovo']['km_totali']
        agg_new['min'] += r['metriche_nuovo']['minuti_totali']
        agg_new['inv'] += r['metriche_nuovo']['inversioni_gt_90']
        agg_new['maxhop'] = max(agg_new['maxhop'], r['metriche_nuovo']['max_hop_km'])

    def _pct(a, b):
        return ((b - a) / a * 100) if a else 0.0

    # Casi più migliorati / peggiorati (delta km negativo = miglioramento)
    for r in risultati:
        r['_delta_km'] = r['metriche_nuovo']['km_totali'] - r['metriche_legacy']['km_totali']
        r['_delta_inv'] = r['metriche_nuovo']['inversioni_gt_90'] - r['metriche_legacy']['inversioni_gt_90']

    piu_migliorati = sorted(risultati, key=lambda r: r['_delta_km'])[:8]
    piu_peggiorati = sorted(risultati, key=lambda r: -r['_delta_km'])[:8]

    # Tabella dettaglio (ordinata per data, operatore)
    righe_det = []
    for r in sorted(risultati, key=lambda x: (x['data'], x['operatore'])):
        righe_det.append({
            'data': r['data'],
            'operatore': r['operatore'],
            'n': r['n_punti'],
            'km_leg': round(r['metriche_legacy']['km_totali'], 1),
            'km_new': round(r['metriche_nuovo']['km_totali'], 1),
            'min_leg': round(r['metriche_legacy']['minuti_totali'], 0),
            'min_new': round(r['metriche_nuovo']['minuti_totali'], 0),
            'inv_leg': r['metriche_legacy']['inversioni_gt_90'],
            'inv_new': r['metriche_nuovo']['inversioni_gt_90'],
            'maxhop_leg': round(r['metriche_legacy']['max_hop_km'], 1),
            'maxhop_new': round(r['metriche_nuovo']['max_hop_km'], 1),
            't_leg': r['tempo_legacy_s'],
            't_new': r['tempo_nuovo_s'],
        })

    def _th(*cols):
        return ''.join(f"<th>{c}</th>" for c in cols)

    rows_html = []
    for r in righe_det:
        cls_km = 'ok' if r['km_new'] < r['km_leg'] else ('ko' if r['km_new'] > r['km_leg'] else 'eq')
        cls_inv = 'ok' if r['inv_new'] < r['inv_leg'] else ('ko' if r['inv_new'] > r['inv_leg'] else 'eq')
        rows_html.append(
            f"<tr>"
            f"<td>{r['data']}</td><td>{r['operatore']}</td><td>{r['n']}</td>"
            f"<td>{r['km_leg']}</td><td class='{cls_km}'>{r['km_new']}</td>"
            f"<td>{r['min_leg']}</td><td>{r['min_new']}</td>"
            f"<td>{r['inv_leg']}</td><td class='{cls_inv}'>{r['inv_new']}</td>"
            f"<td>{r['maxhop_leg']}</td><td>{r['maxhop_new']}</td>"
            f"<td>{r['t_leg']}s / {r['t_new']}s</td>"
            f"</tr>"
        )

    # Mini-mappe Leaflet per i casi estremi
    blocchi_mappe = []
    for tipo, lista in (('Più migliorati', piu_migliorati), ('Più peggiorati', piu_peggiorati)):
        if not lista:
            continue
        blocchi_mappe.append(f"<h2>{tipo}</h2>")
        for i, r in enumerate(lista):
            titolo = f"{html.escape(r['data'])} — {html.escape(r['operatore'])} ({r['n_punti']} tappe)"
            met = (f"Legacy: {r['metriche_legacy']['km_totali']:.1f} km, "
                   f"{r['metriche_legacy']['inversioni_gt_90']} inversioni &gt;90° | "
                   f"Nuovo: {r['metriche_nuovo']['km_totali']:.1f} km, "
                   f"{r['metriche_nuovo']['inversioni_gt_90']} inversioni &gt;90° "
                   f"(Δ {r['_delta_km']:+.1f} km)")
            blocchi_mappe.append(f"<h3>{titolo}</h3><div class='meta'>{met}</div>")
            blocchi_mappe.append(_mini_mappa_leaflet(r['ord_legacy'], r['ord_nuovo'], deposito,
                                                    f"{tipo[:3]}_{i}"))

    title_main = ("A/B Percorsi — Access Points" if diff_access else "A/B Percorsi")
    label_a = "RAW" if diff_access else "Legacy"
    label_b = "ACCESS" if diff_access else "Nuovo"

    # Sezione extra in modalita' diff-access: distribuzione fonti
    extra_html = ""
    if diff_access:
        agg_src = {}
        tot_diff_pos = 0
        for r in risultati:
            for k, v in r.get('access_sources', {}).items():
                agg_src[k] = agg_src.get(k, 0) + v
            tot_diff_pos += r.get('access_diff_pos', 0)
        if agg_src:
            righe_src = ''.join(
                f"<tr><td>{html.escape(k)}</td><td>{v}</td></tr>"
                for k, v in sorted(agg_src.items(), key=lambda kv: -kv[1])
            )
            extra_html = (
                f"<h2>Distribuzione access_source</h2>"
                f"<table class='riepilogo'>"
                f"<tr><th>Fonte</th><th>Contatori</th></tr>{righe_src}</table>"
                f"<p><b>Posizioni cambiate (RAW→ACCESS):</b> {tot_diff_pos}</p>"
            )

    html_doc = f"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="utf-8">
<title>{title_main} — {provincia}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 1400px; margin: 20px auto; padding: 0 15px; }}
h1 {{ border-bottom: 2px solid #333; padding-bottom: 5px; }}
table.riepilogo {{ border-collapse: collapse; margin: 15px 0; }}
table.riepilogo th, table.riepilogo td {{ border: 1px solid #ccc; padding: 6px 12px; text-align: right; }}
table.riepilogo th {{ background: #f0f0f0; text-align: center; }}
table.dett {{ border-collapse: collapse; width: 100%; font-size: 13px; margin: 15px 0; }}
table.dett th, table.dett td {{ border: 1px solid #ddd; padding: 4px 8px; text-align: center; }}
table.dett th {{ background: #e0e0e0; position: sticky; top: 0; }}
td.ok {{ background: #d4edda; color: #155724; font-weight: bold; }}
td.ko {{ background: #f8d7da; color: #721c24; font-weight: bold; }}
td.eq {{ background: #fff3cd; color: #856404; }}
.meta {{ color: #555; font-size: 13px; margin: 5px 0; }}
</style>
</head>
<body>
<h1>{title_main} — {provincia}</h1>
<p><b>Giornate-tecnico analizzate:</b> {n} | <b>Generato:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>

<h2>Riepilogo aggregato</h2>
<table class="riepilogo">
<tr>{_th('Metrica', label_a, label_b, 'Δ', 'Δ %')}</tr>
<tr><td>km totali (somma)</td><td>{agg_leg['km']:.1f}</td><td>{agg_new['km']:.1f}</td><td>{agg_new['km']-agg_leg['km']:+.1f}</td><td>{_pct(agg_leg['km'], agg_new['km']):+.1f}%</td></tr>
<tr><td>minuti totali (somma)</td><td>{agg_leg['min']:.0f}</td><td>{agg_new['min']:.0f}</td><td>{agg_new['min']-agg_leg['min']:+.0f}</td><td>{_pct(agg_leg['min'], agg_new['min']):+.1f}%</td></tr>
<tr><td>inversioni &gt;90° (somma)</td><td>{agg_leg['inv']}</td><td>{agg_new['inv']}</td><td>{agg_new['inv']-agg_leg['inv']:+d}</td><td>{_pct(agg_leg['inv'], agg_new['inv']):+.1f}%</td></tr>
<tr><td>max hop km (globale)</td><td>{agg_leg['maxhop']:.1f}</td><td>{agg_new['maxhop']:.1f}</td><td>{agg_new['maxhop']-agg_leg['maxhop']:+.1f}</td><td>—</td></tr>
</table>

{extra_html}

<h2>Dettaglio per giornata-tecnico</h2>
<table class="dett">
<tr>{_th('Data', 'Operatore', 'N', f'km {label_a[0]}', f'km {label_b[0]}', f'min {label_a[0]}', f'min {label_b[0]}', f'inv {label_a[0]}', f'inv {label_b[0]}', f'maxhop {label_a[0]}', f'maxhop {label_b[0]}', f'tempo {label_a[0]}/{label_b[0]}')}</tr>
{''.join(rows_html)}
</table>

{''.join(blocchi_mappe)}

</body></html>
"""
    with open(path_output, 'w', encoding='utf-8') as f:
        f.write(html_doc)

    # CSV affiancato
    csv_path = path_output.rsplit('.', 1)[0] + '.csv'
    with open(csv_path, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(righe_det[0].keys()))
        w.writeheader()
        w.writerows(righe_det)

    return path_output, csv_path


def main():
    parser = argparse.ArgumentParser(description="A/B test percorsi: legacy vs nuovo")
    parser.add_argument('--provincia', required=True, choices=['torino', 'pavia'],
                        help="Provincia (determina deposito e config)")
    parser.add_argument('--backup-glob', default=None,
                        help="Glob dei backup (default: ultimo file in backup/{prov}/)")
    parser.add_argument('--output', default=None,
                        help="Path HTML di output (default: /tmp/ab_{prov}.html)")
    parser.add_argument('--max-giornate', type=int, default=None,
                        help="Limita il numero di (data, tecnico) da processare (utile per test rapidi)")
    parser.add_argument('--solo-da-oggi', action='store_true',
                        help="Analizza solo date future o di oggi")
    parser.add_argument('--diff-access', action='store_true',
                        help=("Modalita' alternativa: confronta lo stesso algoritmo NUOVO "
                              "con e senza access point UPS-style (Lat_access/Lon_access). "
                              "Mostra il delta di ordine, km e inversioni introdotti dallo "
                              "snap-by-name OSRM."))
    args = parser.parse_args()

    prov = _provincia_da_arg(args.provincia)
    os.environ['PROVINCIA'] = prov
    os.environ.setdefault('FLASK_PORT', '5001' if prov == 'TORINO' else '5002')

    # Ora si può importare config + moduli
    from config import DEPOSITO
    from moduli.distanze import ottieni_matrice_distanze
    from moduli.percorso import ordina_percorso as ordina_nuovo
    if not args.diff_access:
        from moduli.percorso_legacy import ordina_percorso_legacy
    else:
        ordina_percorso_legacy = None  # non usato in modalita' diff-access

    # Determina glob
    if args.backup_glob:
        files = sorted(glob.glob(args.backup_glob))
    else:
        # Ultimo backup disponibile
        files = sorted(glob.glob(os.path.join(ROOT, f'backup/{prov.lower()}/filone_{prov.lower()}_*.json')))
        if files:
            files = files[-1:]
    if not files:
        print(f"❌ Nessun backup trovato per {prov}")
        return 2

    print(f"📂 Backup da processare: {len(files)}")
    for f in files:
        print(f"   • {os.path.basename(f)}")

    # Aggrega gruppi da tutti i file
    tutti_gruppi = []
    for f in files:
        backup = carica_backup(f)
        df = prepara_dataframe(backup)
        gruppi = raggruppa_tecnici(df, solo_da_oggi=args.solo_da_oggi)
        tutti_gruppi.extend(gruppi)

    # Dedup per (data, operatore) tenendo il più recente (ultimo file vince)
    seen = {}
    for g in tutti_gruppi:
        seen[(g[0], g[1])] = g
    gruppi_finali = list(seen.values())
    if args.max_giornate:
        gruppi_finali = gruppi_finali[:args.max_giornate]

    print(f"🎯 Giornate-tecnico da confrontare: {len(gruppi_finali)}")

    risultati = []
    for i, g in enumerate(gruppi_finali, 1):
        data, op, punti = g
        print(f"   [{i}/{len(gruppi_finali)}] {data} / {op} ({len(punti)} punti)...", end=' ', flush=True)
        try:
            if args.diff_access:
                r = esegui_confronto_access(g, DEPOSITO, ordina_nuovo, ottieni_matrice_distanze)
            else:
                r = esegui_confronto(g, DEPOSITO, ordina_percorso_legacy, ordina_nuovo, ottieni_matrice_distanze)
            if r is None:
                print("skip (matrice non disponibile)")
                continue
            dk = r['metriche_nuovo']['km_totali'] - r['metriche_legacy']['km_totali']
            if args.diff_access:
                print(
                    f"Δkm={dk:+.1f}  Δinv={r['metriche_nuovo']['inversioni_gt_90']-r['metriche_legacy']['inversioni_gt_90']:+d}  "
                    f"diffPos={r.get('access_diff_pos', 0)}  snap={r.get('n_snap_osrm', 0)}/{r['n_punti']}"
                )
            else:
                print(f"Δkm={dk:+.1f}  Δinv={r['metriche_nuovo']['inversioni_gt_90']-r['metriche_legacy']['inversioni_gt_90']:+d}")
            risultati.append(r)
        except RuntimeError as e:
            # OSRM down o simili
            print(f"ERRORE: {e}")
            return 3

    suffix = '_access' if args.diff_access else ''
    output = args.output or f"/tmp/ab_{prov.lower()}{suffix}.html"
    html_path, csv_path = genera_report_html(risultati, output, prov, DEPOSITO,
                                             diff_access=args.diff_access)
    print(f"\n✅ Report HTML: {html_path}")
    print(f"✅ Report CSV:  {csv_path}")

    if args.diff_access and risultati:
        # Riepilogo su terminale: distribuzione access_source aggregata
        agg_src = {}
        tot_diff = 0
        tot_pts = 0
        for r in risultati:
            for k, v in r.get('access_sources', {}).items():
                agg_src[k] = agg_src.get(k, 0) + v
            tot_diff += r.get('access_diff_pos', 0)
            tot_pts += r['n_punti']
        print(f"\n📍 Access points distribuzione: {agg_src}")
        print(f"   Posizioni cambiate (RAW vs ACCESS): {tot_diff}/{tot_pts} "
              f"({100*tot_diff/tot_pts if tot_pts else 0:.1f}%)")
    return 0


if __name__ == '__main__':
    sys.exit(main())
