# -*- coding: utf-8 -*-
"""
Package moduli - Gestione Contatori
"""
from .cache import (
    carica_cache,
    salva_cache,
    pulisci_cache_vecchia
)

from .distanze import (
    calcola_rotta_osrm,
    calcola_distanze_percorso,
    genera_report_distanze_con_cache,
    stampa_report_distanze,
    ottieni_matrice_distanze,
    calcola_matrice_distanze_osrm
)

from .percorso import (
    normalizza_eneltel,
    estrai_orario,
    estrai_orario_e_fascia,
    minuti_to_orario,
    ordina_per_fasce_e_vicinanza,
    ordina_percorso
)

from .fasce_orarie import (
    FASCE_ORARIE,
    TAPPE_PER_FASCIA,
    assegna_fasce,
    formatta_fascia_oraria,
    get_colore_fascia
)

from .data_loader import carica_dati_google_sheets, dati_sono_cambiati, cambiamento_e_strutturale

from .zone_geografiche import (
    genera_report_zone,
    crea_cluster_zone
)

from .mappa_html import genera_mappa_html

