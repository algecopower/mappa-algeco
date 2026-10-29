# -*- coding: utf-8 -*-
"""Test per la validazione dinamica cross-provincia durante l'importazione Excel."""
import pytest
from moduli import excel_importer


def test_validate_province_fit_rejects_pavia_lcls_on_torino():
    """Un file contenente LCL noti di Pavia deve essere bloccato se importato su Torino."""
    headers = ['Eneltel', 'LCL', 'POD', 'Comune']
    # LCL di Pavia (es. 6600110023, 6600110478)
    rows = [
        ['ENEL_TEST_1', '6600110023', 'IT001E12345678', 'PAVIA'],
        ['ENEL_TEST_2', '6600110478', 'IT001E12345679', 'SIZIANO'],
        ['ENEL_TEST_3', '6600110023', 'IT001E12345680', 'BELGIOIOSO'],
    ]
    with pytest.raises(ValueError) as excinfo:
        excel_importer.validate_province_fit(rows, headers, current_province='TORINO')

    err_msg = str(excinfo.value)
    assert 'Pavia' in err_msg or 'PAVIA' in err_msg
    assert 'Import bloccato' in err_msg


def test_validate_province_fit_rejects_pavia_coords_on_torino():
    """Un file con coordinate concentrate attorno al deposito di Pavia deve essere bloccato su Torino."""
    headers = ['Eneltel', 'LCL', 'Latitudine', 'Longitudine']
    # Coordinate attorno a Pavia (Lat: ~45.19-45.23, Lon: ~9.15-9.25)
    # Distanza da Torino > 100km, distanza da Pavia < 10km
    rows = [
        [f'ENEL_TEST_{i}', '9999999999', '45.1950', '9.1850']
        for i in range(15)
    ]
    with pytest.raises(ValueError) as excinfo:
        excel_importer.validate_province_fit(rows, headers, current_province='TORINO')

    err_msg = str(excinfo.value)
    assert 'Pavia' in err_msg or 'PAVIA' in err_msg
    assert 'coordinate' in err_msg.lower()


def test_validate_province_fit_accepts_valid_torino_data():
    """Un file con coordinate e LCL di Torino su Torino deve essere accettato."""
    headers = ['Eneltel', 'LCL', 'Latitudine', 'Longitudine']
    # Coordinate vicino a Volpiano / Torino (Lat: ~45.18-45.21, Lon: ~7.75-7.82)
    # LCL tipico di Torino (6600111342)
    rows = [
        [f'ENEL_TEST_{i}', '6600111342', '45.1960', '7.7880']
        for i in range(15)
    ]
    # Non deve sollevare eccezioni
    excel_importer.validate_province_fit(rows, headers, current_province='TORINO')


def test_validate_province_fit_accepts_valid_pavia_data():
    """Un file con LCL di Pavia importato su Pavia deve essere accettato."""
    headers = ['Eneltel', 'LCL', 'POD']
    rows = [
        ['ENEL_TEST_1', '6600110023', 'IT001E12345678'],
        ['ENEL_TEST_2', '6600110478', 'IT001E12345679'],
    ]
    # Non deve sollevare eccezioni
    excel_importer.validate_province_fit(rows, headers, current_province='PAVIA')
