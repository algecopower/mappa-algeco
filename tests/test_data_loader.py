# -*- coding: utf-8 -*-
import pytest

from moduli import data_loader


@pytest.fixture
def operatori_configurati(monkeypatch):
    monkeypatch.setattr(
        data_loader,
        'OPERATORI_DICT',
        {
            'PAUL': 'AE101392',
            'Alessandro': 'ALESSANDRO',
        },
    )
    monkeypatch.setattr(
        data_loader,
        'MATRICOLE_VIP',
        ['AE101392', 'ALESSANDRO'],
    )


@pytest.mark.parametrize(
    ('valore', 'atteso'),
    [
        ('AE101392', 'AE101392'),
        ('ae101392', 'AE101392'),
        ('PAUL', 'AE101392'),
        (' paul ', 'AE101392'),
        ('Alessandro', 'ALESSANDRO'),
        ('ALESSANDRO', 'ALESSANDRO'),
    ],
)
def test_normalizza_operatore_da_matricola_o_nome(
    operatori_configurati,
    valore,
    atteso,
):
    assert data_loader._normalizza_operatore(valore) == atteso


def test_normalizza_operatore_preserva_valore_non_configurato(operatori_configurati):
    assert data_loader._normalizza_operatore('ALTRO') == 'ALTRO'
    assert data_loader._normalizza_operatore('') == ''
    assert data_loader._normalizza_operatore(None) == ''


def test_caricamento_riconosce_nome_e_matricola_come_stesso_operatore(
    operatori_configurati,
    monkeypatch,
):
    from moduli import access_points, db

    headers = [
        'Eneltel',
        'DATA PIAN',
        'Operatore',
        'Latitudine',
        'Longitudine',
    ]
    rows = [
        {
            'Eneltel': '1001',
            'DATA PIAN': '10/06/2026',
            'Operatore': 'PAUL',
            'Latitudine': '45.1',
            'Longitudine': '7.1',
        },
        {
            'Eneltel': '1002',
            'DATA PIAN': '10/06/2026',
            'Operatore': 'AE101392',
            'Latitudine': '45.2',
            'Longitudine': '7.2',
        },
    ]

    monkeypatch.setattr(
        db,
        'dump_filone_as_rows',
        lambda **kwargs: (headers, rows),
    )

    def aggiungi_access_points(df, **kwargs):
        df['Lat_access'] = df['Lat']
        df['Lon_access'] = df['Lon']
        df['access_source'] = 'test'
        df['access_road'] = None

    monkeypatch.setattr(access_points, 'arricchisci_dataframe', aggiungi_access_points)
    monkeypatch.setattr(data_loader, 'DATA_INIZIO', None)
    monkeypatch.setattr(data_loader, 'DATA_FINE', None)
    data_loader.invalidate_data_cache()

    try:
        df = data_loader.carica_dati_db_locale(force=True)
    finally:
        data_loader.invalidate_data_cache()

    assert len(df) == 2
    assert df['TEC_K'].tolist() == ['AE101392', 'AE101392']


def test_caricamento_deduplica_contatori_su_piu_lcl(monkeypatch):
    from moduli import access_points, db

    headers = [
        'Eneltel',
        'LCL',
        'DATA PIAN',
        'Operatore',
        'Latitudine',
        'Longitudine',
    ]
    rows = [
        {
            'Eneltel': '50777529',
            'LCL': '6600110809',
            'DATA PIAN': '14/10/2026',
            'Operatore': 'AG05928',
            'Latitudine': '45.1',
            'Longitudine': '7.1',
        },
        {
            'Eneltel': '50777529',
            'LCL': '6600111342',
            'DATA PIAN': '14/10/2026',
            'Operatore': 'AG05928',
            'Latitudine': '45.1',
            'Longitudine': '7.1',
        },
    ]

    monkeypatch.setattr(
        db,
        'dump_filone_as_rows',
        lambda **kwargs: (headers, rows),
    )

    def aggiungi_access_points(df, **kwargs):
        df['Lat_access'] = df['Lat']
        df['Lon_access'] = df['Lon']
        df['access_source'] = 'test'
        df['access_road'] = None

    monkeypatch.setattr(access_points, 'arricchisci_dataframe', aggiungi_access_points)
    monkeypatch.setattr(data_loader, 'DATA_INIZIO', None)
    monkeypatch.setattr(data_loader, 'DATA_FINE', None)
    monkeypatch.setattr(data_loader, 'LCL_DISABILITATI', [])
    data_loader.invalidate_data_cache()

    try:
        df = data_loader.carica_dati_db_locale(force=True)
    finally:
        data_loader.invalidate_data_cache()

    assert len(df) == 1
    assert df.iloc[0]['Eneltel_Key'] == '50777529'
    assert df.iloc[0]['LCL'] == '6600111342'

