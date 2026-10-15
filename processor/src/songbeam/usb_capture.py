"""Receive the versioned four-channel SongBeam USB Serial PCM stream.

The parser is independent of pyserial so synthetic frames can be checked
without hardware.  It rejects missing/corrupt frames; it never fills gaps.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import time
import wave
import zlib
from pathlib import Path
from typing import BinaryIO

HEADER = struct.Struct("<4sBBHIIII")
MAGIC = b"SB4C"
VERSION = 1
AUDIO = 0
END = 1
STEREO = 2
SAMPLE_RATE = 44_100
FRAMES_PER_PACKET = 128
PAYLOAD_BYTES = FRAMES_PER_PACKET * 4 * 2


class StreamError(ValueError):
    """The USB stream is incomplete or violates its binary contract."""


def read_exact(source: BinaryIO, size: int) -> bytes:
    result = bytearray()
    while len(result) < size:
        chunk = source.read(size - len(result))
        if not chunk:
            raise StreamError("USB stream ended within a frame")
        result.extend(chunk)
    return bytes(result)


class FrameReader:
    def __init__(self, source: BinaryIO, channels: int = 4) -> None:
        if channels not in (2, 4):
            raise ValueError("USB stream supports two or four channels")
        self.source = source
        self.channels = channels
        self.next_sequence = 0
        self.frames = 0
        self.ended = False

    def read(self) -> bytes | None:
        magic, version, kind, frames, sequence, _ms, dropped, checksum = HEADER.unpack(
            read_exact(self.source, HEADER.size)
        )
        if magic != MAGIC or version != VERSION:
            raise StreamError("wrong USB frame magic or protocol version")
        if sequence != self.next_sequence:
            raise StreamError(
                f"missing USB frame: expected {self.next_sequence}, got {sequence}"
            )
        self.next_sequence += 1
        if dropped:
            raise StreamError(f"firmware reported {dropped} dropped audio blocks")
        if kind == END:
            if frames or checksum:
                raise StreamError("invalid end frame")
            self.ended = True
            return None
        expected_kind = STEREO if self.channels == 2 else AUDIO
        if kind != expected_kind or frames != FRAMES_PER_PACKET:
            raise StreamError("unsupported USB audio frame")
        payload = read_exact(self.source, FRAMES_PER_PACKET * self.channels * 2)
        if zlib.crc32(payload) != checksum:
            raise StreamError("USB frame CRC mismatch")
        self.frames += frames
        return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture SongBeam USB PCM")
    parser.add_argument("port", help="USB Serial port, such as /dev/ttyACM0")
    parser.add_argument(
        "output", type=Path, help="new WAV path outside the source repository"
    )
    parser.add_argument(
        "--seconds", type=float, required=True, help="positive capture duration"
    )
    parser.add_argument("--channels", type=int, choices=(2, 4), default=4)
    parser.add_argument(
        "--require-mount", type=Path, help="fail unless output stays on this mount"
    )
    args = parser.parse_args(argv)
    if not 0 < args.seconds <= 24 * 3600:
        parser.error("--seconds must be within (0, 86400]")
    args.output = args.output.expanduser().resolve()
    partial = args.output.with_suffix(".partial.wav")
    manifest_path = args.output.with_suffix(".manifest.json")
    if args.output.exists() or partial.exists() or manifest_path.exists():
        parser.error("output or partial output already exists")
    if args.require_mount:
        mount = args.require_mount.expanduser().resolve()
        if not os.path.ismount(mount) or not args.output.is_relative_to(mount):
            parser.error("required data mount is missing or output lies outside it")
        if (
            args.output.parent.exists()
            and os.stat(args.output.parent).st_dev != os.stat(mount).st_dev
        ):
            parser.error("output is on a different filesystem than the required mount")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    expected_bytes = int(args.seconds * SAMPLE_RATE * args.channels * 2)
    if shutil.disk_usage(args.output.parent).free < expected_bytes + 8 * (1024**3):
        parser.error("insufficient free space including the 8 GiB reserve")
    try:
        import serial  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SystemExit("Install the processor's usb extra (pyserial) first") from exc

    status = "failed"
    detail = "capture did not start"
    receiver: FrameReader | None = None
    try:
        with serial.Serial(args.port, timeout=2, write_timeout=2) as device:
            device.write(f"STREAM {args.channels}\n".encode("ascii"))
            expected_greeting = (
                f"OK STREAM{args.channels} 44100 {args.channels} S16LE".encode("ascii")
            )
            for _ in range(16):
                greeting = device.readline().strip()
                if greeting == expected_greeting:
                    break
            else:
                raise StreamError(
                    f"firmware did not accept stream mode: {greeting[:80]!r}"
                )
            receiver = FrameReader(device, args.channels)
            with (
                os.fdopen(
                    os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"
                ) as raw,
                wave.open(raw, "wb") as wav,
            ):
                wav.setnchannels(args.channels)
                wav.setsampwidth(2)
                wav.setframerate(SAMPLE_RATE)
                deadline = time.monotonic() + args.seconds
                while time.monotonic() < deadline:
                    payload = receiver.read()
                    if payload is None:
                        raise StreamError("firmware stopped before requested duration")
                    wav.writeframesraw(payload)
                    if receiver.next_sequence % 256 == 0 and shutil.disk_usage(
                        args.output.parent
                    ).free < 8 * (1024**3):
                        raise StreamError("data drive fell below the 8 GiB reserve")
                device.write(b"STOP\n")
                while not receiver.ended:
                    payload = receiver.read()
                    if payload is not None:
                        wav.writeframesraw(payload)
        os.link(partial, args.output)  # exclusive final name, same directory
        partial.unlink()
        status = "complete"
        detail = "sequence and CRC continuous"
    except (OSError, StreamError, KeyboardInterrupt) as exc:
        detail = str(exc)
        raise
    finally:
        path = args.output if status == "complete" else partial
        digest = None
        if path.exists():
            hasher = hashlib.sha256()
            with path.open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    hasher.update(block)
            digest = hasher.hexdigest()
        manifest = {
            "schema": 1,
            "status": status,
            "detail": detail,
            "protocol": "SB4C/1",
            "sample_rate": SAMPLE_RATE,
            "channels": args.channels,
            "frames": receiver.frames if receiver else 0,
            "sha256": digest,
            "audio_file": path.name if path.exists() else None,
        }
        with os.fdopen(
            os.open(manifest_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
            "w",
            encoding="utf-8",
        ) as output:
            json.dump(manifest, output, indent=2, sort_keys=True)
            output.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
