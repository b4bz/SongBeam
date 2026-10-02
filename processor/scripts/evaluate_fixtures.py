"""Opt-in local evaluation of an external, truth-labeled array-fixture pack.

Never imports fixture audio into the source repository. Output contains only
synthetic case IDs and error metrics; no source audio or source-site metadata.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import soundfile as sf

from songbeam.profiles import load_profile
from songbeam.spatial import candidates


def evaluate(
    root: Path, fmin: float = 2000, fmax: float = 8000, window_seconds: float = 0.5
) -> dict:
    manifest = json.loads((root / "manifest.json").read_text())
    rows = []
    for item in manifest["cases"]:
        source = item["sources"][0]
        path = root / item["file"]
        x, rate = sf.read(path, dtype="float32", always_2d=True)
        profile = load_profile(
            "songbeam-4" if item["array"] == "songbeam" else "sipeed-d80"
        )
        # Automatic estimator receives audio and a nominal profile only.
        start = rate // 2
        hit = candidates(
            x[start : start + round(window_seconds * rate)],
            rate,
            profile,
            max_tracks=1,
            fmin=fmin,
            fmax=fmax,
        )
        row = {
            "array": item["array"],
            "case": item["scenario"]["name"],
            "source_count": len(item["sources"]),
            "accepted": bool(hit),
            "bearing_status": hit[0]["bearing_status"] if hit else "no_evidence",
            "stress_case": len(item["sources"]) != 1
            or any(
                key in item["scenario"]
                for key in (
                    "snr_db",
                    "channel0_skew_samples",
                    "channel1_gain",
                    "channel2_polarity",
                )
            ),
        }
        if hit and len(item["sources"]) == 1:
            if item["array"] == "songbeam":
                true_u = float(
                    np.cos(np.deg2rad(source["elevation_deg"]))
                    * np.cos(np.deg2rad(source["azimuth_deg"]))
                )
                row["projection_abs_error"] = abs(hit[0]["axis_projection"] - true_u)
            elif hit[0]["azimuth_deg"] is not None:
                row["azimuth_abs_error_deg"] = abs(
                    (hit[0]["azimuth_deg"] - source["azimuth_deg"] + 180) % 360 - 180
                )
                row["elevation_magnitude_abs_error_deg"] = abs(
                    hit[0]["absolute_elevation_deg"] - abs(source["elevation_deg"])
                )
        rows.append(row)
    summary = {
        "schema": 1,
        "settings": {"fmin": fmin, "fmax": fmax, "window_seconds": window_seconds},
        "case_count": len(rows),
        "results": rows,
        "limits": "repeated licensed bird excerpts and ideal virtual acoustics; no field accuracy or independent held-out species claim",
    }
    for array in ("songbeam", "sipeed-d80"):
        group = [r for r in rows if r["array"] == array]
        key = "projection_abs_error" if array == "songbeam" else "azimuth_abs_error_deg"
        errors = [r[key] for r in group if key in r]
        clean_errors = [r[key] for r in group if key in r and not r["stress_case"]]
        summary[array] = {
            "cases": len(group),
            "accepted": sum(r["accepted"] for r in group),
            "scored": len(errors),
            "median_error": float(np.median(errors)) if errors else None,
            "p95_error": float(np.percentile(errors, 95)) if errors else None,
            "clean_scored": len(clean_errors),
            "clean_max_error": float(max(clean_errors)) if clean_errors else None,
            "error_unit": "projection" if array == "songbeam" else "degrees",
        }
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("fixture_root", type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--fmin", type=float, default=2000)
    ap.add_argument("--fmax", type=float, default=8000)
    ap.add_argument("--window-seconds", type=float, default=0.5)
    args = ap.parse_args()
    if not 0 <= args.fmin < args.fmax <= 20000 or not 0.02 <= args.window_seconds <= 1:
        raise ValueError("Invalid evaluation band or window")
    if args.output.exists():
        raise FileExistsError(args.output)
    result = evaluate(
        args.fixture_root.resolve(strict=True),
        args.fmin,
        args.fmax,
        args.window_seconds,
    )
    with args.output.open("x") as f:
        json.dump(result, f, indent=2)
    print(
        json.dumps(
            {k: result[k] for k in ("case_count", "songbeam", "sipeed-d80")}, indent=2
        )
    )


if __name__ == "__main__":
    main()
