"""Synthetic stream contract checks; no recorder or private audio is required."""

from __future__ import annotations

import struct
import zlib
from io import BytesIO

import pytest

from songbeam.usb_capture import (
    END,
    HEADER,
    PAYLOAD_BYTES,
    STEREO,
    FrameReader,
    StreamError,
)


def packet(sequence: int, *, dropped: int = 0, corrupt: bool = False) -> bytes:
    payload = bytes(range(256)) * (PAYLOAD_BYTES // 256)
    crc = zlib.crc32(payload) ^ (1 if corrupt else 0)
    return (
        HEADER.pack(b"SB4C", 1, 0, 128, sequence, sequence * 3, dropped, crc) + payload
    )


def end_packet(sequence: int) -> bytes:
    return HEADER.pack(b"SB4C", 1, END, 0, sequence, sequence * 3, 0, 0)


def stereo_packet(sequence: int) -> bytes:
    payload = bytes(range(256)) * 2
    return (
        HEADER.pack(
            b"SB4C", 1, STEREO, 128, sequence, sequence * 3, 0, zlib.crc32(payload)
        )
        + payload
    )


def test_continuous_frames_and_end() -> None:
    reader = FrameReader(BytesIO(packet(0) + packet(1) + end_packet(2)))
    assert len(reader.read()) == PAYLOAD_BYTES
    assert len(reader.read()) == PAYLOAD_BYTES
    assert reader.read() is None
    assert reader.ended
    assert reader.frames == 256


def test_stereo_preview_contract() -> None:
    reader = FrameReader(BytesIO(stereo_packet(0) + end_packet(1)), channels=2)
    assert len(reader.read()) == 512
    assert reader.read() is None
    assert reader.frames == 128


@pytest.mark.parametrize(
    "stream,match",
    [
        (packet(1), "missing USB frame"),
        (packet(0, dropped=2), "dropped audio blocks"),
        (packet(0, corrupt=True), "CRC mismatch"),
        (packet(0)[:-1], "ended within a frame"),
        (struct.pack("<4sBBHIIII", b"BAD!", 1, 0, 128, 0, 0, 0, 0), "magic"),
    ],
)
def test_rejects_missing_or_corrupt_audio(stream: bytes, match: str) -> None:
    with pytest.raises(StreamError, match=match):
        FrameReader(BytesIO(stream)).read()
