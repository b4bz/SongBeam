"""Geometry-based far-field direction scoring and delay-and-sum steering."""

from __future__ import annotations

import itertools
import math
from functools import lru_cache

import numpy as np
from scipy.signal import find_peaks, stft

from .profiles import ArrayProfile


def _grid(profile: ArrayProfile) -> np.ndarray:
    positions = np.asarray(profile.positions_m)
    centered = positions - positions.mean(axis=0)
    if profile.rank == 1:
        axis = np.linalg.svd(centered, full_matrices=False)[2][0]
        if axis[np.argmax(abs(axis))] < 0:
            axis = -axis
        return np.linspace(-1, 1, 201)[:, None] * axis
    # q points toward later arrivals: source direction is -q. Planar arrays
    # cannot distinguish positive and negative elevation from time delays.
    az = np.deg2rad(np.arange(0, 360, 5))
    radii = np.array([0.0, 0.09, 0.25, 0.5, 0.7, 0.85, 1.0])
    if profile.rank == 2:
        basis = np.linalg.svd(centered, full_matrices=False)[2][:2]
        return np.array(
            [
                r * (np.cos(a) * basis[0] + np.sin(a) * basis[1])
                for r in radii
                for a in az
            ]
        )
    return np.array(
        [
            [np.cos(e) * np.cos(a), np.cos(e) * np.sin(a), np.sin(e)]
            for e, a in itertools.product(np.deg2rad(range(-90, 91, 15)), az)
        ]
    )


def _scores(
    spectra: np.ndarray,
    frequencies: np.ndarray,
    profile: ArrayProfile,
    grid: np.ndarray,
    speed: float,
    phase_cache: tuple[np.ndarray, ...] | None = None,
) -> np.ndarray:
    p = np.asarray(profile.positions_m)
    values = np.zeros(len(grid))
    count = 0
    for pair_index, (i, j) in enumerate(itertools.combinations(range(len(p)), 2)):
        cross = np.mean(spectra[:, i, :] * np.conj(spectra[:, j, :]), axis=1)
        mag = abs(cross)
        if mag.max() < 1e-12:
            continue
        weights = np.sqrt(mag / (mag.max() + 1e-10))
        phase = (
            phase_cache[pair_index]
            if phase_cache is not None
            else np.exp(
                2j
                * np.pi
                * frequencies[:, None]
                * ((p[i] - p[j]) @ grid.T)[None, :]
                / speed
            )
        )
        values += np.real(
            np.sum(
                (cross / np.maximum(mag, 1e-10))[:, None] * phase * weights[:, None],
                axis=0,
            )
        ) / max(weights.sum(), 1e-10)
        count += 1
    return values / max(count, 1)


@lru_cache(maxsize=4)
def _coarse_plan(
    positions: tuple[tuple[float, float, float], ...],
    rate: int,
    n: int,
    fmin: float,
    fmax: float,
    speed: float,
) -> tuple[np.ndarray, np.ndarray, tuple[np.ndarray, ...]]:
    """Reuse fixed steering phases across windows; data-dependent weights stay fresh."""
    profile = ArrayProfile(
        "phase-plan",
        1,
        positions,
        {"raw": (len(positions), tuple(range(len(positions))))},
    )
    grid = _grid(profile)
    frequencies = np.fft.rfftfreq(n, 1 / rate)
    use = (frequencies >= fmin) & (frequencies <= min(fmax, rate * 0.45))
    selected = frequencies[use]
    p = np.asarray(positions)
    phases = tuple(
        np.exp(
            2j * np.pi * selected[:, None] * ((p[i] - p[j]) @ grid.T)[None, :] / speed
        ).astype("complex64")
        for i, j in itertools.combinations(range(len(p)), 2)
    )
    return grid, use, phases


def _direction(q: np.ndarray, score: float, profile: ArrayProfile) -> dict:
    q = np.clip(q, -1, 1)
    result = {
        "direction_q": [round(float(v), 5) for v in q],
        "score": round(float(score), 4),
    }
    if profile.rank == 1:
        axis = np.linalg.svd(
            np.asarray(profile.positions_m) - np.mean(profile.positions_m, axis=0),
            full_matrices=False,
        )[2][0]
        if axis[np.argmax(abs(axis))] < 0:
            axis = -axis
        u = float(q @ axis)
        result.update(
            u=round(u, 4),
            axis_projection=round(-u, 4),
            axis_angle_deg=round(math.degrees(math.acos(np.clip(-u, -1, 1))), 2),
            bearing_status="ambiguous_linear_projection",
            azimuth_deg=None,
        )
    else:
        horizontal = float(np.linalg.norm(q[:2]))
        azimuth = (
            math.degrees(math.atan2(-q[1], -q[0])) % 360 if horizontal >= 0.1 else None
        )
        if profile.rank == 2:
            normal = np.linalg.svd(
                np.asarray(profile.positions_m) - np.mean(profile.positions_m, axis=0),
                full_matrices=False,
            )[2][-1]
            flat = abs(normal[2]) > 0.999999
            plane_angle = round(
                math.degrees(math.acos(min(float(np.linalg.norm(q)), 1))), 2
            )
            result.update(
                azimuth_deg=round(azimuth, 2) if flat and azimuth is not None else None,
                absolute_elevation_deg=plane_angle if flat else None,
                angle_from_array_plane_magnitude_deg=plane_angle,
                bearing_status="azimuth_with_elevation_mirror"
                if flat and azimuth is not None
                else "azimuth_unobservable_near_zenith"
                if flat
                else "ambiguous_planar_projection",
            )
        else:
            result.update(
                azimuth_deg=round(azimuth, 2) if azimuth is not None else None,
                elevation_deg=round(math.degrees(math.asin(np.clip(-q[2], -1, 1))), 2),
                bearing_status="3d_candidate"
                if azimuth is not None
                else "azimuth_unobservable_near_zenith",
            )
    return result


def candidates(
    block: np.ndarray,
    rate: int,
    profile: ArrayProfile,
    max_tracks: int = 2,
    fmin: float = 2000,
    fmax: float = 8000,
    speed: float = 343.0,
) -> list[dict]:
    if block.ndim != 2 or block.shape[1] != len(profile.positions_m):
        raise ValueError("Audio channels do not match selected profile")
    energy = float(np.sqrt(np.mean(np.square(block, dtype=np.float64))))
    if len(block) < 256 or energy < 2e-5:
        return []
    n = min(1024 if rate <= 48000 else 2048, len(block))
    frequencies, _, spectra = stft(
        block, fs=rate, nperseg=n, noverlap=n // 2, axis=0, boundary=None, padded=False
    )
    grid, use, phase_cache = _coarse_plan(
        profile.positions_m, rate, n, fmin, fmax, speed
    )
    if not np.any(use):
        return []
    frequencies, spectra = frequencies[use], spectra[use]
    scores = _scores(spectra, frequencies, profile, grid, speed, phase_cache)
    if scores.max() < 0.08:
        return []
    if profile.rank == 1:
        peaks, _ = find_peaks(scores, prominence=0.10, distance=25)
        indices = list(peaks)
        if scores[0] > scores[1]:
            indices.append(0)
        if scores[-1] > scores[-2]:
            indices.append(len(grid) - 1)
    else:
        indices = list(np.argsort(scores)[-min(len(scores), 100) :])
    ranked = sorted(indices, key=lambda k: scores[k], reverse=True)
    chosen = []
    for k in ranked:
        q = grid[k]
        if any(
            np.linalg.norm(q - np.asarray(old["direction_q"])) < 0.28 for old in chosen
        ):
            continue
        if chosen and scores[k] < 0.55 * scores[ranked[0]]:
            continue
        if profile.rank == 2 and np.linalg.norm(q[:2]) >= 0.1:
            # Refine the coarse azimuth/radius grid around each candidate.
            a = math.atan2(q[1], q[0])
            r = np.linalg.norm(q[:2])
            local = np.array(
                [
                    [
                        radius * math.cos(a + math.radians(delta)),
                        radius * math.sin(a + math.radians(delta)),
                        0.0,
                    ]
                    for radius in np.clip(r + np.arange(-0.08, 0.081, 0.02), 0, 1)
                    for delta in range(-4, 5)
                ]
            )
            fine = _scores(spectra, frequencies, profile, local, speed)
            q, score = local[int(np.argmax(fine))], float(np.max(fine))
        else:
            score = float(scores[k])
        chosen.append(_direction(q, score, profile))
        if len(chosen) >= max_tracks:
            break
    return chosen


def steering_samples(
    profile: ArrayProfile, direction_q: np.ndarray, rate: int, speed: float
) -> np.ndarray:
    q = np.asarray(direction_q, dtype=float)
    if q.shape != (3,) or not np.all(np.isfinite(q)) or np.linalg.norm(q) > 1.00001:
        raise ValueError("Invalid direction vector")
    return (np.asarray(profile.positions_m) @ q) * rate / speed
