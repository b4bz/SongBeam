"""Bounded, procedural runtime benchmark for the offline processor.

Creates and removes only its own TemporaryDirectory. No field audio is read.
Results are printed as JSON; scratch parent must be supplied explicitly.
"""

from __future__ import annotations

import argparse
import json
import resource
import tempfile
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfilt

from songbeam.process import Settings, process
from songbeam.profiles import load_profile


def one_second(profile, rate: int) -> np.ndarray:
    n = rate
    rng = np.random.default_rng(20261002)
    noise = sosfilt(
        butter(4, [2000, 6500], fs=rate, btype="bandpass", output="sos"),
        rng.standard_normal(n),
    )
    # Repeated broadband calls with quiet gaps, no abrupt file boundaries.
    envelope = np.sin(np.linspace(0, np.pi, n)) ** 2
    noise = (noise / max(abs(noise)) * envelope * 0.15).astype("float32")
    p = np.asarray(profile.positions_m)
    unit = np.array([np.cos(np.pi / 6), np.sin(np.pi / 6), 0.0])
    frequencies = np.fft.rfftfreq(2 * n)
    spectrum = np.fft.rfft(noise, 2 * n)
    return np.stack(
        [
            np.fft.irfft(
                spectrum
                * np.exp(
                    -2j * np.pi * frequencies * (24 - position @ unit * rate / 343)
                ),
                2 * n,
            )[:n]
            for position in p
        ],
        axis=1,
    ).astype("float32")


def benchmark(profile_name: str, minutes: int, scratch_parent: Path) -> dict:
    profile = load_profile(profile_name)
    if len(profile.layouts) > 1:
        layout = (
            "simulated-raw6"
            if profile_name == "sipeed-d80"
            else next(iter(profile.layouts))
        )
    else:
        layout = next(iter(profile.layouts))
    rate = 44100 if profile_name == "songbeam-4" else 48000
    if not profile.rate_range[0] <= rate <= profile.rate_range[1]:
        raise ValueError("Benchmark sample rate outside profile range")
    block = one_second(profile, rate)
    with tempfile.TemporaryDirectory(
        prefix="array-profile-benchmark-", dir=scratch_parent
    ) as temporary:
        root = Path(temporary)
        source = root / "procedural.wav"
        with sf.SoundFile(
            source,
            "x",
            samplerate=rate,
            channels=len(profile.positions_m),
            format="WAV",
            subtype="PCM_16",
        ) as output:
            for _ in range(minutes * 60):
                output.write(block)
        started = time.perf_counter()
        result = process(
            source,
            root / "beamformed",
            Settings(
                source_profile=profile_name,
                input_layout=layout,
                formats=("flac",),
                reserve_gib=0,
            ),
        )
        elapsed = time.perf_counter() - started
        return {
            "schema": 1,
            "profile": profile_name,
            "profile_sha256": profile.sha256,
            "processor_version": result["processor_version"],
            "minutes_audio": minutes,
            "sample_rate": rate,
            "channels": len(profile.positions_m),
            "input_bytes": source.stat().st_size,
            "output_bytes": sum(
                v["bytes"]
                for formats in result["outputs"].values()
                for v in formats.values()
            ),
            "seconds_processing": elapsed,
            "audio_to_wall_ratio": minutes * 60 / elapsed,
            "peak_process_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            / 1024,
            "status": result["status"],
            "limits": "procedural repeated signal, one FLAC beam, scratch reserve zero; not outdoor performance",
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--profile", choices=("songbeam-4", "sipeed-d80"), required=True
    )
    parser.add_argument("--minutes", type=int, choices=(1, 10, 30), required=True)
    parser.add_argument("--scratch-parent", type=Path, required=True)
    args = parser.parse_args()
    if not args.scratch_parent.is_dir() or args.scratch_parent.is_symlink():
        raise ValueError("Scratch parent must be an existing real directory")
    print(
        json.dumps(benchmark(args.profile, args.minutes, args.scratch_parent), indent=2)
    )


if __name__ == "__main__":
    main()
