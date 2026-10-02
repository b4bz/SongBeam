"""Device-specific, inert calibration records separate from nominal geometry."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from .profiles import ArrayProfile


def load_calibration(path: Path, profile: ArrayProfile, layout: str, rate: int) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise ValueError("Calibration must be a local JSON file under 64 KiB")
    data = path.read_bytes()
    try:
        raw = json.loads(data)
        n = len(profile.positions_m)
        if (
            raw["schema"] != 1
            or raw["profile_sha256"] != profile.sha256
            or raw["input_layout"] != layout
        ):
            raise ValueError("Calibration profile/layout differs from selected input")
        mapping = tuple(raw["channel_map"])
        gains = tuple(float(v) for v in raw["gains"])
        polarities = tuple(raw["polarities"])
        delays_s = tuple(float(v) for v in raw["fixed_delays_seconds"])
        if (
            len(mapping) != n
            or len(gains) != n
            or len(polarities) != n
            or len(delays_s) != n
        ):
            raise ValueError("Calibration vector length differs from profile")
        channels = profile.layouts[layout][0]
        if len(set(mapping)) != n or any(
            type(v) is not int or not 0 <= v < channels for v in mapping
        ):
            raise ValueError("Invalid calibrated channel map")
        if any(not math.isfinite(v) or not 0 < v <= 4 for v in gains):
            raise ValueError("Invalid calibrated gains")
        if any(type(v) is not int or v not in (-1, 1) for v in polarities):
            raise ValueError("Invalid calibrated polarity")
        if any(not math.isfinite(v) or abs(v * rate) > 32 for v in delays_s):
            raise ValueError("Invalid calibrated fixed delays")
        identifier = raw["calibration_id"]
        if not isinstance(identifier, str) or not 1 <= len(identifier) <= 80:
            raise ValueError("Invalid calibration ID")
    except (KeyError, TypeError, IndexError) as error:
        raise ValueError("Malformed calibration JSON") from error
    return {
        "calibration_id": identifier,
        "channel_map": mapping,
        "gains": gains,
        "polarities": polarities,
        "fixed_delays_samples": tuple(v * rate for v in delays_s),
        "calibration_sha256": hashlib.sha256(data).hexdigest(),
    }
