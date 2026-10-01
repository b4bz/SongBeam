"""One-dimensional, all-pairs SRP-PHAT candidate localization."""

from __future__ import annotations

import numpy as np
from scipy.signal import find_peaks, stft

from .audio import POSITIONS_M


def score_grid(
    block: np.ndarray,
    rate: int,
    speed: float = 343.0,
    fmin: float = 2000,
    fmax: float = 8000,
    grid: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, float]:
    grid = np.linspace(-1.0, 1.0, 201) if grid is None else grid
    energy = float(np.sqrt(np.mean(np.square(block, dtype=np.float64))))
    scores = np.zeros(len(grid), dtype=np.float64)
    if energy < 2e-5:
        return grid, scores, energy
    fft_size = 1024 if rate <= 48000 else 2048
    freqs, _, spectra = stft(
        block,
        fs=rate,
        nperseg=fft_size,
        noverlap=fft_size // 2,
        axis=0,
        boundary=None,
        padded=False,
    )
    use = (freqs >= fmin) & (freqs <= min(fmax, rate * 0.45))
    if not np.any(use):
        return grid, scores, energy
    x = spectra[use]
    frequencies = freqs[use]
    # One coherent cross-spectrum per pair; weighting retains moderate signal energy.
    for i in range(4):
        for j in range(i + 1, 4):
            cross = np.mean(x[:, i, :] * np.conj(x[:, j, :]), axis=1)
            mag = np.abs(cross)
            normalized = cross / np.maximum(mag, 1e-10)
            weight = np.sqrt(mag / (float(np.max(mag)) + 1e-10))
            lag = (POSITIONS_M[i] - POSITIONS_M[j]) * grid / speed
            phase = np.exp(2j * np.pi * frequencies[:, None] * lag[None, :])
            scores += np.real(
                np.sum(normalized[:, None] * phase * weight[:, None], axis=0)
            ) / max(1, np.sum(weight))
    return grid, scores / 6.0, energy


def candidates(
    block: np.ndarray,
    rate: int,
    max_tracks: int = 2,
    fmin: float = 2000,
    fmax: float = 8000,
) -> list[dict]:
    grid, scores, energy = score_grid(block, rate, fmin=fmin, fmax=fmax)
    if energy < 2e-5 or float(np.max(scores)) < 0.08:
        return []
    peaks, _ = find_peaks(scores, prominence=0.10, distance=25)
    # Endfire peaks can lie at the grid boundary.
    extrema = list(peaks)
    if scores[0] > scores[1] and scores[0] >= 0.08:
        extrema.append(0)
    if scores[-1] > scores[-2] and scores[-1] >= 0.08:
        extrema.append(len(grid) - 1)
    ranked = sorted(extrema, key=lambda index: scores[index], reverse=True)
    chosen = []
    for index in ranked:
        if chosen and abs(grid[index] - chosen[0]["u"]) < 0.28:
            continue
        if chosen and scores[index] < 0.55 * scores[ranked[0]]:
            continue
        chosen.append(
            {"u": round(float(grid[index]), 3), "score": round(float(scores[index]), 4)}
        )
        if len(chosen) >= max_tracks:
            break
    return chosen


def track_blocks(
    block_candidates: list[list[dict]], max_tracks: int = 2
) -> list[list[dict | None]]:
    """Keep identities by nearest direction, requiring persistence for a second track."""
    tracks: list[list[dict | None]] = [
        [None for _ in block_candidates] for _ in range(max_tracks)
    ]
    previous: list[float | None] = [None] * max_tracks
    for time_index, found in enumerate(block_candidates):
        remaining = list(found)
        for track_id, old in enumerate(previous):
            if old is None or not remaining:
                continue
            nearest = min(remaining, key=lambda item: abs(item["u"] - old))
            if abs(nearest["u"] - old) <= 0.35:
                tracks[track_id][time_index] = nearest
                remaining.remove(nearest)
        for track_id in range(max_tracks):
            if not remaining:
                break
            if tracks[track_id][time_index] is None and previous[track_id] is None:
                candidate = remaining.pop(0)
                tracks[track_id][time_index] = candidate
        for track_id in range(max_tracks):
            hit = tracks[track_id][time_index]
            if hit is not None:
                previous[track_id] = hit["u"]
    if max_tracks > 1:
        # A second output requires at least three adjacent blocks with evidence.
        present = [entry is not None for entry in tracks[1]]
        has_run = any(all(present[i : i + 3]) for i in range(max(0, len(present) - 2)))
        if not has_run:
            return tracks[:1]
    return tracks
