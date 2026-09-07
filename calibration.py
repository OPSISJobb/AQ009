"""
Lagring av kalibrerade stegpositioner for de 9 lagena.

Filen sparas som JSON: {"1": <steg fran hem>, "2": <steg>, ...}.
Om filen saknas skapas nominella (jamnt fordelade) startvarden som sedan
kan finjusteras via kalibreringslaget i webbgranssnittet.
"""

import json
import logging
import os

import config

logger = logging.getLogger(__name__)


def _nominal_calibration() -> dict:
    spr = config.STEPS_PER_REV
    n = config.NUM_POSITIONS
    return {i: round((i - 1) * spr / n) % spr for i in range(1, n + 1)}


def load_calibration() -> dict:
    if not os.path.exists(config.CALIBRATION_FILE):
        logger.info("Ingen kalibreringsfil hittad, anvander nominella startvarden.")
        return _nominal_calibration()
    try:
        with open(config.CALIBRATION_FILE, "r") as f:
            raw = json.load(f)
        result = _nominal_calibration()
        for key, value in raw.items():
            result[int(key)] = int(value) % config.STEPS_PER_REV
        return result
    except Exception:
        logger.exception("Kunde inte lasa kalibreringsfil, anvander nominella startvarden.")
        return _nominal_calibration()


def save_calibration(calib: dict) -> None:
    os.makedirs(os.path.dirname(config.CALIBRATION_FILE), exist_ok=True)
    tmp_path = config.CALIBRATION_FILE + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump({str(k): v for k, v in calib.items()}, f, indent=2, sort_keys=True)
    os.replace(tmp_path, config.CALIBRATION_FILE)
    logger.info("Kalibrering sparad till %s", config.CALIBRATION_FILE)
