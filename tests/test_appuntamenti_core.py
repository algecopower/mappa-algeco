# -*- coding: utf-8 -*-
import pytest
import pandas as pd
from moduli.appuntamenti_core import rileva_appuntamento, motivo_non_appuntamento
from routes.contatori import _format_row_to_contatore


@pytest.mark.parametrize(
    ('testo', 'atteso'),
    [
        # Positivi standard (iniziano con APP, case insensitive)
        ('APP', True),
        ('app', True),
        ('App.', True),
        ('APP: 14:00', True),
        ('  APP 10/12', True),
        ('- APP confermato', True),
        ('APP 19/03 mandata mail', True),
        ('app 8,00-12,00', True),

        # Positivi con parole precedentemente in blacklist (ora considerate appuntamenti)
        ('APP chiamare prima di andare', True),
        ('app da rifissare', True),
        ('APP DA CONFERMARE 11/13', True),
        ('APP annullato dal cliente', True),
        ('APP?', True),
        ('APP da fissare', True),
        ('APP richiesta cliente', True),
        ('APP forse nel pomeriggio', True),
        ('APP non risponde', True),

        # Negativi (non iniziano con APP)
        ('chiamare per app', False),
        ('14/07 chiamare marito // APP 12:30', False),
        ('18/02 mandato wa promemoria // APP 17:45', False),
        ('no app', False),
        ('appartamento 3 piano terra', False),
        ('applicazione sigillo', False),
        ('cliente assente', False),
        ('richiesta appuntamento', False),
        ('', False),
        ('   ', False),
        (None, False),
    ],
)
def test_rileva_appuntamento(testo, atteso):
    assert rileva_appuntamento(testo) is atteso
    if atteso:
        assert motivo_non_appuntamento(testo) is None
    else:
        assert motivo_non_appuntamento(testo) is not None


def test_orario_estratto_solo_da_colonna_orari_in_data_loader(monkeypatch):
    from moduli import data_loader, db, access_points

    headers = [
        'Eneltel',
        'DATA PIAN',
        'Operatore',
        'Latitudine',
        'Longitudine',
        'NOTE APP',
        'Orari',
    ]
    rows = [
        # Caso 1: Ha APP nelle note con orario vecchio (16:00), ma Orari ha "08:00 alle 11:00"
        # Deve prendere 08:00-11:00 da Orari!
        {
            'Eneltel': '1001',
            'DATA PIAN': '10/06/2026',
            'Operatore': 'TUTTI',
            'Latitudine': '45.1',
            'Longitudine': '7.1',
            'NOTE APP': 'APP dalle 16:00 alle 17:00 vecchia nota',
            'Orari': 'dalle 08:00 alle 11:00',
        },
        # Caso 2: Ha APP nelle note ma Orari è vuoto -> IS_APPUNTAMENTO True, ORA_APPUNTAMENTO None
        {
            'Eneltel': '1002',
            'DATA PIAN': '10/06/2026',
            'Operatore': 'TUTTI',
            'Latitudine': '45.2',
            'Longitudine': '7.2',
            'NOTE APP': 'APP chiamare cliente',
            'Orari': '',
        },
        # Caso 3: NON ha APP nelle note (es. "chiamare") ma Orari è compilato -> IS_APPUNTAMENTO False, ORA_APPUNTAMENTO None
        {
            'Eneltel': '1003',
            'DATA PIAN': '10/06/2026',
            'Operatore': 'TUTTI',
            'Latitudine': '45.3',
            'Longitudine': '7.3',
            'NOTE APP': 'chiamare per appuntamento ore 14',
            'Orari': 'dalle 14:00 alle 17:00',
        },
    ]

    monkeypatch.setattr(
        db,
        'dump_filone_as_rows',
        lambda **kwargs: (headers, rows),
    )

    def dummy_access_points(df, **kwargs):
        df['Lat_access'] = df['Lat']
        df['Lon_access'] = df['Lon']
        df['access_source'] = 'test'
        df['access_road'] = None

    monkeypatch.setattr(access_points, 'arricchisci_dataframe', dummy_access_points)
    monkeypatch.setattr(data_loader, 'DATA_INIZIO', None)
    monkeypatch.setattr(data_loader, 'DATA_FINE', None)
    data_loader.invalidate_data_cache()

    try:
        df = data_loader.carica_dati_db_locale(force=True)
    finally:
        data_loader.invalidate_data_cache()

    r1 = df[df['Eneltel_Key'] == '1001'].iloc[0]
    assert r1['IS_APPUNTAMENTO'] is True or r1['IS_APPUNTAMENTO'] == 1
    assert r1['ORA_APPUNTAMENTO'] == '08:00-11:00'

    r2 = df[df['Eneltel_Key'] == '1002'].iloc[0]
    assert r2['IS_APPUNTAMENTO'] is True or r2['IS_APPUNTAMENTO'] == 1
    assert pd.isna(r2['ORA_APPUNTAMENTO']) or r2['ORA_APPUNTAMENTO'] is None

    r3 = df[df['Eneltel_Key'] == '1003'].iloc[0]
    assert r3['IS_APPUNTAMENTO'] is False or r3['IS_APPUNTAMENTO'] == 0
    assert pd.isna(r3['ORA_APPUNTAMENTO']) or r3['ORA_APPUNTAMENTO'] is None


def test_format_row_to_contatore():
    data = {
        'Eneltel': '12345',
        'NOTE APP': 'APP dalle 15:00 alle 17:00 nota vecchia',
        'Orari': 'dalle 09:00 alle 12:00',
    }
    c = _format_row_to_contatore(data)
    assert c['is_appuntamento'] is True
    # Orario preso SOLO da orari_lettere (colonna Orari)
    assert c['ora_appuntamento'] == '09:00-12:00'

    data_no_app = {
        'Eneltel': '12346',
        'NOTE APP': 'chiamare per app',
        'Orari': 'dalle 09:00 alle 12:00',
    }
    c_no = _format_row_to_contatore(data_no_app)
    assert c_no['is_appuntamento'] is False
    assert c_no['ora_appuntamento'] is None
