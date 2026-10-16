"""Strict source inspection and bounded directional audio transforms."""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.fft import next_fast_len

POSITIONS_M = np.array([0.0, 0.045, 0.075, 0.120], dtype=np.float64)
HALO = 96


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def riff_pcm(path: Path, expected_channels: int = 4) -> dict:
    """Reject truncated, inconsistent, compressed or multi-RIFF input."""
    size = path.stat().st_size
    with path.open("rb") as stream:
        head = stream.read(12)
        if len(head) != 12 or head[:4] not in {b"RIFF", b"RF64"} or head[8:] != b"WAVE":
            raise ValueError("Expected a RIFF/WAVE file")
        declared = struct.unpack_from("<I", head, 4)[0] + 8
        rf64 = head[:4] == b"RF64"
        if not rf64 and declared != size:
            raise ValueError(f"RIFF size mismatch: declared {declared}, actual {size}")
        fmt = None
        data = None
        ds64 = None
        while stream.tell() + 8 <= size:
            chunk_head = stream.read(8)
            chunk_size = struct.unpack_from("<I", chunk_head, 4)[0]
            start = stream.tell()
            if chunk_head[:4] == b"data" and rf64 and chunk_size == 0xFFFFFFFF:
                if ds64 is None:
                    raise ValueError("RF64 data chunk lacks ds64 header")
                chunk_size = ds64[1]
            end = start + chunk_size
            if end > size:
                raise ValueError("Truncated WAV chunk")
            if chunk_head[:4] == b"fmt ":
                payload = stream.read(min(chunk_size, 40))
                if len(payload) < 16:
                    raise ValueError("Incomplete WAV format")
                fmt = struct.unpack_from("<HHIIHH", payload)
                if fmt[0] == 0xFFFE:
                    if (
                        len(payload) < 40
                        or struct.unpack_from("<H", payload, 16)[0] < 22
                        or struct.unpack_from("<H", payload, 18)[0] not in (0, 16)
                        or payload[24:40]
                        != bytes.fromhex("0100000000001000800000aa00389b71")
                    ):
                        raise ValueError("Unsupported WAVE_FORMAT_EXTENSIBLE subtype")
                    fmt = (1, *fmt[1:])
            elif chunk_head[:4] == b"ds64":
                if chunk_size < 28:
                    raise ValueError("Incomplete RF64 ds64 chunk")
                ds64 = struct.unpack("<QQQ", stream.read(24))
            elif chunk_head[:4] == b"data":
                if data is not None:
                    raise ValueError("Multiple WAV data chunks need explicit review")
                data = (start, chunk_size)
            if end + chunk_size % 2 > size:
                raise ValueError("Truncated WAV padding")
            stream.seek(end + chunk_size % 2)
        if stream.tell() != size:
            raise ValueError("Trailing bytes outside WAV chunks")
    if fmt is None or data is None:
        raise ValueError("Missing WAV format or data chunk")
    if rf64 and (declared != 0xFFFFFFFF + 8 or ds64 is None or ds64[0] + 8 != size):
        raise ValueError("RF64 size mismatch")
    code, channels, rate, byte_rate, frame_bytes, bits = fmt
    if code != 1 or bits != 16 or channels != expected_channels:
        raise ValueError(f"Expected {expected_channels}-channel 16-bit PCM source")
    if (
        frame_bytes != channels * 2
        or byte_rate != rate * frame_bytes
        or rate < 16000
        or rate > 96000
    ):
        raise ValueError("Invalid WAV rate or frame fields")
    if data[1] == 0 or data[1] % frame_bytes:
        raise ValueError("Empty or non-integral WAV frames")
    info = sf.info(str(path))
    if (
        info.channels != channels
        or info.samplerate != rate
        or info.frames != data[1] // frame_bytes
    ):
        raise ValueError("Decoder and RIFF metadata disagree")
    return {
        "channels": channels,
        "sample_rate": rate,
        "frames": info.frames,
        "subtype": info.subtype,
        "data_offset": data[0],
        "data_bytes": data[1],
        "file_bytes": size,
        "source_sha256": sha256_file(path),
    }


def inspect(
    path: Path,
    mapping: tuple[int, ...] = (0, 1, 2, 3),
    expected_channels: int | None = None,
) -> dict:
    report = riff_pcm(path, expected_channels or len(mapping))
    if len(set(mapping)) != len(mapping) or any(
        i < 0 or i >= report["channels"] for i in mapping
    ):
        raise ValueError("Invalid selected microphone channel map")
    peaks = np.zeros(len(mapping), dtype=float)
    power = np.zeros(len(mapping), dtype=float)
    sums = np.zeros(len(mapping), dtype=float)
    samples = 0
    with sf.SoundFile(str(path)) as stream:
        while True:
            block = stream.read(65536, dtype="float32", always_2d=True)
            if not len(block):
                break
            block = block[:, mapping].astype(np.float64)
            peaks = np.maximum(peaks, np.max(np.abs(block), axis=0))
            power += np.sum(block * block, axis=0)
            sums += np.sum(block, axis=0)
            samples += len(block)
    rms = np.sqrt(power / samples)
    report.update(
        channel_map=list(mapping),
        peak=[round(float(x), 6) for x in peaks],
        rms=[round(float(x), 6) for x in rms],
        dc=[round(float(x / samples), 6) for x in sums],
        silent_channels=[i for i, x in enumerate(rms) if x < 1e-5],
        clipped_channels=[i for i, x in enumerate(peaks) if x >= 0.9999],
    )
    return report


def read_block(
    stream: sf.SoundFile,
    start: int,
    count: int,
    mapping: tuple[int, ...],
    halo: int = HALO,
) -> tuple[np.ndarray, int]:
    first = max(0, start - halo)
    last = min(len(stream), start + count + halo)
    stream.seek(first)
    data = stream.read(last - first, dtype="float32", always_2d=True)
    return data[:, mapping], first


def beam_fft(
    block: np.ndarray,
    first: int,
    start: int,
    count: int,
    rate: int,
    direction_cosine: float,
    speed: float = 343.0,
    spectrum: np.ndarray | None = None,
    delays_samples: np.ndarray | None = None,
) -> np.ndarray:
    """Far-field delay-and-sum with zero-padded fractional shifts."""
    if not np.isfinite(direction_cosine) or abs(direction_cosine) > 1:
        raise ValueError("Direction cosine must be in [-1, 1]")
    length = next_fast_len(len(block) + 2 * HALO)
    spec = np.fft.rfft(block, n=length, axis=0) if spectrum is None else spectrum
    delays = (
        POSITIONS_M * direction_cosine / speed * rate
        if delays_samples is None
        else np.asarray(delays_samples)
    )
    if len(delays) != block.shape[1]:
        raise ValueError("Steering delays do not match microphone channels")
    bins = np.fft.rfftfreq(length)
    shifted = (
        np.sum(spec * np.exp(2j * np.pi * bins[:, None] * delays[None, :]), axis=1)
        / block.shape[1]
    )
    audio = np.fft.irfft(shifted, n=length)
    offset = start - first
    result = audio[offset : offset + count].astype("float32")
    if not np.all(np.isfinite(result)) or len(result) != count:
        raise ValueError("Beam output invalid")
    return result
