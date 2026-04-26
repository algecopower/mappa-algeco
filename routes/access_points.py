# -*- coding: utf-8 -*-
"""
Blueprint per le API access point (UPS-style).

Permette di leggere, salvare e rimuovere l'override manuale dell'access point
per un singolo contatore, e di rilanciare la risoluzione automatica.
"""
import logging
from flask import Blueprint, request, jsonify

from moduli.auth import login_required
from moduli.shared import _get_username, _api_error
from moduli.access_points import (
    risolvi_access_point,
    get_override,
    salva_override,
    rimuovi_override,
    undo_override,
    has_history,
)

logger = logging.getLogger('mappa_algeco')

bp = Blueprint('access_points', __name__)


def _trova_contatore(eneltel: str):
    """
    Carica i contatori dalla cache disco (mappa_data.json) e ritorna quello
    con l'eneltel richiesto, o None.
    """
    try:
        from config import PROVINCIA_ATTIVA
        from moduli.safe_json import json_read
        import os
        path = os.path.join('data', PROVINCIA_ATTIVA.lower(), 'mappa_data.json')
        data = json_read(path, default={}) or {}
        for c in data.get('contatori', []):
            if str(c.get('eneltel', '')).strip() == str(eneltel).strip():
                return c
    except Exception as e:
        logger.warning(f"[AccessPoint] Errore lettura mappa_data: {e}")
    return None


@bp.route('/api/access-point/<eneltel>', methods=['GET'])
@login_required
def api_get_access_point(eneltel):
    """
    Ritorna l'access point corrente per un Eneltel.

    Response:
        {
          "ok": true,
          "eneltel": "...",
          "lat": 44.98,
          "lon": 7.87,
          "source": "override" | "osrm" | "fallback" | "no_civico",
          "road": "Via X" | null,
          "similarity": 0.95 | null,
          "distance_m": 69.1 | null,
          "raw": {"lat": ..., "lon": ...},   # Coords originali (marker)
          "is_override": bool,
          "override_ts": "..." | null
        }
    """
    try:
        eneltel = str(eneltel).strip()
        if not eneltel:
            return jsonify({'error': 'Eneltel mancante'}), 400

        contatore = _trova_contatore(eneltel)
        if contatore is None:
            return jsonify({'error': 'Contatore non trovato'}), 404

        raw_lat = float(contatore.get('lat'))
        raw_lon = float(contatore.get('lon'))

        ovr = get_override(eneltel)
        if ovr:
            return jsonify({
                'ok': True,
                'eneltel': eneltel,
                'lat': float(ovr['lat']),
                'lon': float(ovr['lon']),
                'source': 'override',
                'road': None,
                'similarity': None,
                'distance_m': None,
                'raw': {'lat': raw_lat, 'lon': raw_lon},
                'is_override': True,
                'override_ts': ovr.get('ts'),
                'override_user': ovr.get('user'),
                'can_undo': has_history(eneltel),
            })

        # Risolvi snap automatico (usa cache se gia' calcolato)
        res = risolvi_access_point(
            raw_lat,
            raw_lon,
            contatore.get('via', ''),
            contatore.get('civico', ''),
            eneltel,
            contatore.get('comune', ''),
            use_cache=True,
        )

        return jsonify({
            'ok': True,
            'eneltel': eneltel,
            'lat': res['lat'],
            'lon': res['lon'],
            'source': res['source'],
            'road': res.get('road'),
            'similarity': res.get('similarity'),
            'distance_m': res.get('distance_m'),
            'raw': {'lat': raw_lat, 'lon': raw_lon},
            'is_override': False,
            'override_ts': None,
            'can_undo': has_history(eneltel),
        })

    except Exception as e:
        return _api_error(e, 'get access-point')


@bp.route('/api/access-point/<eneltel>', methods=['POST'])
@login_required
def api_set_access_point(eneltel):
    """
    Salva un override manuale per Eneltel.

    Body JSON:
        {"lat": <float>, "lon": <float>}

    Response:
        {"ok": true, "lat": ..., "lon": ..., "ts": "..."}
    """
    try:
        eneltel = str(eneltel).strip()
        if not eneltel:
            return jsonify({'error': 'Eneltel mancante'}), 400

        body = request.get_json(force=True, silent=True) or {}
        try:
            lat = float(body.get('lat'))
            lon = float(body.get('lon'))
        except (TypeError, ValueError):
            return jsonify({'error': 'Coordinate lat/lon non valide'}), 400

        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return jsonify({'error': 'Coordinate fuori range'}), 400

        ok = salva_override(eneltel, lat, lon, user=_get_username())
        if not ok:
            return jsonify({'error': 'Salvataggio fallito'}), 500

        ovr = get_override(eneltel) or {}
        logger.info(f"[AccessPoint] 📍 Override salvato per {eneltel} "
                    f"({lat:.6f}, {lon:.6f}) da {_get_username()}")
        return jsonify({
            'ok': True,
            'eneltel': eneltel,
            'lat': lat,
            'lon': lon,
            'source': 'override',
            'is_override': True,
            'ts': ovr.get('ts'),
            'can_undo': has_history(eneltel),
        })

    except Exception as e:
        return _api_error(e, 'set access-point')


@bp.route('/api/access-point/<eneltel>', methods=['DELETE'])
@login_required
def api_delete_access_point(eneltel):
    """
    Rimuove l'override manuale e torna allo snap automatico.
    """
    try:
        eneltel = str(eneltel).strip()
        if not eneltel:
            return jsonify({'error': 'Eneltel mancante'}), 400

        existed = get_override(eneltel) is not None
        if existed:
            rimuovi_override(eneltel)
            logger.info(f"[AccessPoint] 🔄 Override rimosso per {eneltel} da {_get_username()}")

        contatore = _trova_contatore(eneltel)
        if contatore is None:
            return jsonify({
                'ok': True,
                'eneltel': eneltel,
                'removed': existed,
            })

        # Ricalcola snap automatico per rispondere subito con nuove coords
        try:
            raw_lat = float(contatore.get('lat'))
            raw_lon = float(contatore.get('lon'))
            res = risolvi_access_point(
                raw_lat,
                raw_lon,
                contatore.get('via', ''),
                contatore.get('civico', ''),
                eneltel,
                contatore.get('comune', ''),
                use_cache=True,
            )
            return jsonify({
                'ok': True,
                'eneltel': eneltel,
                'removed': existed,
                'lat': res['lat'],
                'lon': res['lon'],
                'source': res['source'],
                'road': res.get('road'),
                'similarity': res.get('similarity'),
                'distance_m': res.get('distance_m'),
                'is_override': False,
                'can_undo': has_history(eneltel),
            })
        except Exception:
            return jsonify({
                'ok': True,
                'eneltel': eneltel,
                'removed': existed,
                'can_undo': has_history(eneltel),
            })

    except Exception as e:
        return _api_error(e, 'delete access-point')


@bp.route('/api/access-point/<eneltel>/undo', methods=['POST'])
@login_required
def api_undo_access_point(eneltel):
    """
    Annulla l'ultima modifica dell'access point ripristinando lo stato
    immediatamente precedente (override o nessun override) dallo storico.

    Response (success):
        {"ok": true, "eneltel": "...", "lat": ..., "lon": ...,
         "source": "override", "is_override": true, "can_undo": bool}

    Response (storia vuota):
        409 {"error": "Nessuna modifica da annullare"}
    """
    try:
        eneltel = str(eneltel).strip()
        if not eneltel:
            return jsonify({'error': 'Eneltel mancante'}), 400

        if not has_history(eneltel):
            return jsonify({
                'ok': False,
                'error': 'Nessuna modifica da annullare',
                'can_undo': False,
            }), 409

        restored = undo_override(eneltel)
        if not restored:
            return jsonify({
                'ok': False,
                'error': 'Undo fallito',
                'can_undo': has_history(eneltel),
            }), 500

        logger.info(f"[AccessPoint] ↩️ Undo per {eneltel} -> "
                    f"({restored['lat']:.6f}, {restored['lon']:.6f}) da {_get_username()}")
        return jsonify({
            'ok': True,
            'eneltel': eneltel,
            'lat': float(restored['lat']),
            'lon': float(restored['lon']),
            'source': 'override',
            'is_override': True,
            'ts': restored.get('ts'),
            'can_undo': has_history(eneltel),
        })

    except Exception as e:
        return _api_error(e, 'undo access-point')


@bp.route('/api/access-point/<eneltel>/snap', methods=['POST'])
@login_required
def api_snap_to_road(eneltel):
    """
    Snappa una coppia di coords arbitrarie alla strada piu' vicina (OSRM nearest).
    Utile per il drag della mini-mappa: aggancia il marker alla carreggiata.

    Body JSON: {"lat": <float>, "lon": <float>}

    Response: {"ok": true, "lat": <snapped>, "lon": <snapped>, "road": "...", "distance_m": ...}
    """
    try:
        body = request.get_json(force=True, silent=True) or {}
        lat = float(body.get('lat'))
        lon = float(body.get('lon'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Coordinate non valide'}), 400

    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return jsonify({'error': 'Coordinate fuori range'}), 400

    try:
        from moduli.access_points import _osrm_nearest, _haversine_m
        wps = _osrm_nearest(lat, lon, number=1)
        if not wps:
            return jsonify({'ok': True, 'lat': lat, 'lon': lon, 'road': None, 'distance_m': 0.0})
        wp = wps[0]
        loc = wp.get('location') or [lon, lat]
        snap_lon, snap_lat = float(loc[0]), float(loc[1])
        return jsonify({
            'ok': True,
            'lat': snap_lat,
            'lon': snap_lon,
            'road': wp.get('name') or None,
            'distance_m': round(_haversine_m(lat, lon, snap_lat, snap_lon), 1),
        })
    except Exception as e:
        return _api_error(e, 'snap to road')
