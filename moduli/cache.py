"""
Sistema di cache per ottimizzare i ricalcoli.
"""
import os
import json
import hashlib

from config import FILE_CACHE_DISTANZE, FILE_CACHE_HASH
from .safe_json import json_read, json_write


def genera_hash_dati(df_tecnico):
    """
    Genera un hash univoco basato sui dati rilevanti del tecnico.
    """
    if df_tecnico.empty:
        return "EMPTY"
    
    colonne_rilevanti = ['Eneltel_Key', 'Lat', 'Lon', 'IS_APPUNTAMENTO', 
                         'ORA_APPUNTAMENTO', 'Fascia_Oraria', 'CLI_K']
    
    colonne_esistenti = [c for c in colonne_rilevanti if c in df_tecnico.columns]
    
    df_subset = df_tecnico[colonne_esistenti].copy()
    df_subset = df_subset.sort_values('Eneltel_Key')
    
    data_string = df_subset.to_json(orient='records', default_handler=str)
    hash_obj = hashlib.md5(data_string.encode())
    
    return hash_obj.hexdigest()


def carica_cache():
    """Carica cache distanze e hash da file."""
    cache_distanze = json_read(FILE_CACHE_DISTANZE, default={})
    if cache_distanze:
        print(f"📂 Cache distanze caricata ({len(cache_distanze)} voci)")

    cache_hash = json_read(FILE_CACHE_HASH, default={})
    
    return cache_distanze, cache_hash


def salva_cache(cache_distanze, cache_hash):
    """Salva cache distanze e hash su file (atomico)."""
    ok1 = json_write(FILE_CACHE_DISTANZE, cache_distanze, indent=2)
    ok2 = json_write(FILE_CACHE_HASH, cache_hash, indent=2)
    if not (ok1 and ok2):
        print(f"⚠️ Errore salvataggio cache")


def pulisci_cache_vecchia(cache_distanze, cache_hash, giorni_da_mantenere=None):
    """
    Mantiene TUTTI i dati in cache senza rimuovere nulla.

    La cache è persistente: tutto quello che è stato calcolato rimane per sempre.
    Questo evita ricalcoli inutili per percorsi già elaborati.

    Args:
        cache_distanze: dizionario cache distanze
        cache_hash: dizionario cache hash
        giorni_da_mantenere: ignorato (mantenuto per compatibilità)

    Returns:
        cache_distanze, cache_hash invariati
    """
    # Non rimuovere nulla - la cache è permanente
    return cache_distanze, cache_hash