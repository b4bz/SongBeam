"""Validated, inert microphone-array geometry and file-channel layouts."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class ArrayProfile:
    identifier: str
    version: int
    positions_m: tuple[tuple[float, float, float], ...]
    layouts: dict[str, tuple[int, tuple[int, ...]]]
    rate_range: tuple[int, int] = (16000, 96000)
    geometry_status: str = "nominal-unverified"

    def validate(self) -> None:
        if not self.identifier or len(self.identifier) > 80 or self.version < 1:
            raise ValueError("Invalid profile identity or version")
        if not 2 <= len(self.positions_m) <= 16:
            raise ValueError("A profile needs 2–16 microphones")
        p = np.asarray(self.positions_m, dtype=float)
        if p.shape != (len(self.positions_m), 3) or not np.all(np.isfinite(p)):
            raise ValueError("Microphone positions must be finite XYZ metres")
        if np.max(np.linalg.norm(p - p.mean(axis=0), axis=1)) > 2:
            raise ValueError("Microphone aperture exceeds 2 m limit")
        if any(
            np.linalg.norm(p[i] - p[j]) < 1e-5 for i in range(len(p)) for j in range(i)
        ):
            raise ValueError("Microphone positions must be distinct")
        if not self.layouts:
            raise ValueError("Profile needs an input layout")
        for name, (channels, mapping) in self.layouts.items():
            if not name or not len(p) <= channels <= 32 or len(mapping) != len(p):
                raise ValueError("Invalid input layout")
            if any(
                not isinstance(i, int) or i < 0 or i >= channels for i in mapping
            ) or len(set(mapping)) != len(mapping):
                raise ValueError("Input layout must select distinct valid raw channels")
        if (
            len(self.rate_range) != 2
            or any(type(rate) is not int for rate in self.rate_range)
            or not 16000 <= self.rate_range[0] <= self.rate_range[1] <= 96000
        ):
            raise ValueError("Invalid profile sample-rate range")

    @property
    def rank(self) -> int:
        return int(
            np.linalg.matrix_rank(
                np.asarray(self.positions_m) - np.mean(self.positions_m, axis=0),
                tol=1e-7,
            )
        )

    def as_dict(self) -> dict:
        return {
            "schema": 1,
            "id": self.identifier,
            "version": self.version,
            "positions_m": [list(p) for p in self.positions_m],
            "layouts": {
                key: {"channels": n, "map": list(mapping)}
                for key, (n, mapping) in self.layouts.items()
            },
            "rate_range": list(self.rate_range),
            "geometry_status": self.geometry_status,
            "geometry_rank": self.rank,
        }

    @property
    def sha256(self) -> str:
        payload = json.dumps(
            self.as_dict(), sort_keys=True, separators=(",", ":")
        ).encode()
        return hashlib.sha256(payload).hexdigest()

    def layout(
        self, name: str | None, file_channels: int | None = None
    ) -> tuple[str, int, tuple[int, ...]]:
        if name is None:
            choices = [
                (k, v)
                for k, v in self.layouts.items()
                if file_channels is None or v[0] == file_channels
            ]
            if len(choices) != 1:
                raise ValueError("Select an explicit input layout")
            name, (channels, mapping) = choices[0]
        else:
            if name not in self.layouts:
                raise ValueError(f"Unknown input layout: {name}")
            channels, mapping = self.layouts[name]
        if file_channels is not None and channels != file_channels:
            raise ValueError(
                f"Input layout expects {channels} file channels, got {file_channels}"
            )
        return name, channels, mapping


def _builtins() -> dict[str, ArrayProfile]:
    songbeam = ArrayProfile(
        "songbeam-4",
        1,
        tuple((x, 0.0, 0.0) for x in (0.0, 0.045, 0.075, 0.120)),
        {"raw4": (4, (0, 1, 2, 3))},
    )
    angles = (90, 30, -30, -90, -150, 150)
    sipeed = ArrayProfile(
        "sipeed-d80",
        1,
        tuple(
            (0.04 * math.cos(math.radians(a)), 0.04 * math.sin(math.radians(a)), 0.0)
            for a in angles
        ),
        {"simulated-raw6": (6, tuple(range(6))), "ma-usb8": (8, tuple(range(6)))},
        (48000, 48000),
    )
    return {p.identifier: p for p in (songbeam, sipeed)}


BUILTINS = _builtins()


def load_profile(source: str | Path) -> ArrayProfile:
    if str(source) in BUILTINS:
        return BUILTINS[str(source)]
    path = Path(source)
    if (
        path.suffix.lower() != ".json"
        or not path.is_file()
        or path.is_symlink()
        or path.stat().st_size > 65536
    ):
        raise ValueError(
            "Unknown profile; custom profiles must be local JSON files under 64 KiB"
        )
    raw = json.loads(path.read_text())
    if not isinstance(raw, dict) or raw.get("schema") != 1:
        raise ValueError("Unsupported profile schema")
    try:
        profile = ArrayProfile(
            identifier=str(raw["id"]),
            version=int(raw["version"]),
            positions_m=tuple(
                tuple(float(c) for c in row) for row in raw["positions_m"]
            ),
            layouts={
                str(k): (int(v["channels"]), tuple(v["map"]))
                for k, v in raw["layouts"].items()
            },
            rate_range=tuple(raw.get("rate_range", (16000, 96000))),
            geometry_status=str(raw.get("geometry_status", "nominal-unverified")),
        )
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise ValueError("Malformed profile JSON") from error
    profile.validate()
    return profile
