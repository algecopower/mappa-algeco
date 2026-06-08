# -*- coding: utf-8 -*-
"""Test del parser delle distinte PDF."""

from moduli.distinte_manager import estrai_lavori_da_testo


def test_formazione_con_piu_parole_e_ampersand():
    """La formazione può contenere più parole e simboli prima della data."""
    testo = """NOUR EDDINE MOHAMED AMINE AG63397 1 399
Nour & Eddine 09/06/2026 00:00 0.0
09/06/2026 00:00
RCMI CC CC2G Completamento Passivi e Scambio disalimentabili 530 18 125943847
CONDOMINIO TORRE LESNA DI VITTORIO SN
335207577 10095 GRUGLIASCO D000BJ7
020562706 Non conosciuta 000000000000 0000
IT001E02056270
"""

    lavori = estrai_lavori_da_testo(testo)

    assert len(lavori) == 1
    assert lavori[0]['lavoro'] == 1
    assert lavori[0]['matricola_operatore'] == 'AG63397'
    assert lavori[0]['data_intervento'] == '09/06/2026'
    assert lavori[0]['pod'] == 'IT001E02056270'
