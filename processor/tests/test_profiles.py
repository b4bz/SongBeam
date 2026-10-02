"""Independent geometry and layout checks for the shared processor."""

import json
import math
import struct

import numpy as np
import pytest
import soundfile as sf
from scipy.signal import butter, sosfilt

from songbeam.audio import beam_fft, riff_pcm
from songbeam.calibration import load_calibration
from songbeam.cli import parser
from songbeam.localize import track_blocks
from songbeam.process import Settings, process
from songbeam.profiles import ArrayProfile, load_profile
from songbeam.spatial import candidates, steering_samples


def source_audio(profile, fs=48000, az=30.0, elevation=30.0, seconds=0.7, seed=44):
    """Independent physical arrival model: microphone closer to source hears earlier."""
    rng = np.random.default_rng(seed)
    n = round(fs * seconds)
    sos = butter(4, [2200, 6700], btype="bandpass", fs=fs, output="sos")
    signal = sosfilt(sos, rng.standard_normal(n))
    signal *= 0.16 / max(abs(signal))
    a, e = np.deg2rad([az, elevation])
    direction = np.array([np.cos(e) * np.cos(a), np.cos(e) * np.sin(a), np.sin(e)])
    p = np.asarray(profile.positions_m)
    delays = -(p @ direction) * fs / 343 + 24
    N = 2 ** math.ceil(math.log2(n + 256))
    f = np.fft.rfftfreq(N)
    return np.stack(
        [
            np.fft.irfft(np.fft.rfft(signal, N) * np.exp(-2j * np.pi * f * d), N)[:n]
            for d in delays
        ],
        axis=1,
    )


def test_builtin_geometry_and_layout_contract():
    songbeam, sipeed = load_profile("songbeam-4"), load_profile("sipeed-d80")
    assert (songbeam.rank, sipeed.rank) == (1, 2)
    assert sipeed.layout("ma-usb8", 8) == ("ma-usb8", 8, tuple(range(6)))
    assert sipeed.layout(None, 6)[0] == "simulated-raw6"
    with pytest.raises(ValueError, match="expects"):
        sipeed.layout("ma-usb8", 6)
    assert load_profile("songbeam-4").sha256 == songbeam.sha256


def test_custom_geometry_uses_same_direction_engine(tmp_path):
    profile = ArrayProfile(
        "test-tetrahedron",
        1,
        ((0.0, 0.0, 0.0), (0.06, 0.0, 0.0), (0.0, 0.06, 0.0), (0.0, 0.0, 0.06)),
        {"raw4": (4, (0, 1, 2, 3))},
    )
    custom = tmp_path / "custom.json"
    custom.write_text(json.dumps(profile.as_dict()))
    loaded = load_profile(custom)
    assert loaded.rank == 3 and loaded.sha256 == profile.sha256
    x = source_audio(loaded, az=30, elevation=30)
    hit = candidates(x, 48000, loaded, max_tracks=1)[0]
    q = np.asarray(hit["direction_q"])
    assert np.linalg.norm(q - [-0.75, -0.433, -0.5]) < 0.13
    assert len(steering_samples(loaded, q, 48000, 343)) == 4


def test_profile_rejects_invalid_mapping_and_duplicate_mics(tmp_path):
    base = load_profile("sipeed-d80").as_dict()
    base["layouts"]["ma-usb8"]["map"][-1] = 6
    base["layouts"]["ma-usb8"]["map"][0] = 6
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(base))
    with pytest.raises(ValueError, match="distinct"):
        load_profile(path)
    base = load_profile("sipeed-d80").as_dict()
    base["positions_m"][1] = base["positions_m"][0]
    path.write_text(json.dumps(base))
    with pytest.raises(ValueError, match="distinct"):
        load_profile(path)


@pytest.mark.parametrize(
    "name,az,elevation",
    [
        ("songbeam-4", 30, 30),
        ("songbeam-4", 150, 30),
        ("songbeam-4", 0, 0),
        ("songbeam-4", 90, 0),
        ("songbeam-4", 180, 0),
        ("sipeed-d80", 30, 30),
        ("sipeed-d80", 200, 60),
        ("sipeed-d80", 359, 30),
    ],
)
def test_direction_is_recovered_without_oracle(name, az, elevation):
    profile = load_profile(name)
    fs = 44100 if name == "songbeam-4" else 48000
    x = source_audio(profile, fs=fs, az=az, elevation=elevation)
    hit = candidates(x, fs, profile, max_tracks=1)[0]
    if name == "songbeam-4":
        assert hit["bearing_status"] == "ambiguous_linear_projection"
        assert hit["axis_projection"] == pytest.approx(
            np.cos(np.deg2rad(elevation)) * np.cos(np.deg2rad(az)), abs=0.02
        )
        assert hit["azimuth_deg"] is None
    else:
        error = abs((hit["azimuth_deg"] - az + 180) % 360 - 180)
        assert error <= 2
        assert hit["bearing_status"] == "azimuth_with_elevation_mirror"


def test_planar_mirror_and_zenith_are_reported_ambiguous():
    p = load_profile("sipeed-d80")
    above = source_audio(p, az=30, elevation=60)
    below = source_audio(p, az=30, elevation=-60)
    assert np.array_equal(above, below)
    zenith = candidates(source_audio(p, elevation=90), 48000, p, 1)[0]
    assert zenith["azimuth_deg"] is None
    assert zenith["bearing_status"] == "azimuth_unobservable_near_zenith"


def test_tilted_planar_custom_profile_does_not_claim_horizontal_bearing():
    p = ArrayProfile(
        "vertical-plane",
        1,
        ((0.0, 0.0, 0.0), (0.06, 0.0, 0.0), (0.0, 0.0, 0.06), (0.06, 0.0, 0.06)),
        {"raw4": (4, (0, 1, 2, 3))},
    )
    hit = candidates(source_audio(p, az=30, elevation=30), 48000, p, 1)[0]
    assert hit["bearing_status"] == "ambiguous_planar_projection"
    assert hit["azimuth_deg"] is None


def test_sipeed_automatic_beam_improves_directional_contrast():
    profile = load_profile("sipeed-d80")
    target = source_audio(profile, az=30, elevation=0, seed=41)
    other = source_audio(profile, az=180, elevation=0, seed=73)
    hit = candidates(target, 48000, profile, max_tracks=1)[0]
    delays = steering_samples(profile, np.asarray(hit["direction_q"]), 48000, 343)
    wanted = beam_fft(target, 0, 0, len(target), 48000, 0, delays_samples=delays)
    unwanted = beam_fft(other, 0, 0, len(other), 48000, 0, delays_samples=delays)
    segment = slice(200, -200)
    beam_ratio = np.std(wanted[segment]) / np.std(unwanted[segment])
    channel_ratio = np.std(target[segment, 0]) / np.std(other[segment, 0])
    assert 20 * np.log10(beam_ratio / channel_ratio) > 2


def test_8ch_firmware_layout_ignores_processed_and_reserved_channels(tmp_path):
    p = load_profile("sipeed-d80")
    x = source_audio(p)
    six, eight = tmp_path / "six.wav", tmp_path / "eight.wav"
    sf.write(six, x, 48000, subtype="PCM_16")
    corrupted = np.column_stack(
        (x, np.ones(len(x)) * 0.9, np.random.default_rng(1).normal(0, 0.5, len(x)))
    )
    sf.write(eight, corrupted, 48000, subtype="PCM_16")
    a = process(
        six,
        tmp_path / "a",
        Settings(
            source_profile="sipeed-d80",
            input_layout="simulated-raw6",
            formats=("wav",),
            reserve_gib=0,
        ),
    )
    b = process(
        eight,
        tmp_path / "b",
        Settings(
            source_profile="sipeed-d80",
            input_layout="ma-usb8",
            formats=("wav",),
            reserve_gib=0,
        ),
    )
    assert (
        a["outputs"]["track-001"]["wav"]["sha256"]
        == b["outputs"]["track-001"]["wav"]["sha256"]
    )
    assert b["selected_raw_channels"] == list(range(6))
    assert b["source"]["channels"] == 8


def test_songbeam_constraints_and_calibration_file(tmp_path):
    p = load_profile("songbeam-4")
    x = source_audio(p, fs=44100)
    source = tmp_path / "source.wav"
    sf.write(source, x, 44100, subtype="PCM_16")
    cal = tmp_path / "calibration.json"
    cal.write_text(
        json.dumps(
            {
                "schema": 1,
                "profile_sha256": p.sha256,
                "input_layout": "raw4",
                "calibration_id": "bench-test",
                "channel_map": [0, 1, 2, 3],
                "gains": [1] * 4,
                "polarities": [1] * 4,
                "fixed_delays_seconds": [0] * 4,
            }
        )
    )
    checked = load_calibration(cal, p, "raw4", 44100)
    assert checked["calibration_id"] == "bench-test"
    settings = Settings(
        calibration_file=str(cal),
        known_elevation_deg=30,
        source_sector=(0, 90),
        orientation_deg=15,
        formats=("wav",),
        reserve_gib=0,
    )
    result = process(source, tmp_path / "result", settings)
    hit = next(row for row in result["tracks"] if row["active"])
    assert hit["bearing_status"] == "constrained_unique_bearing"
    assert hit["azimuth_deg"] == pytest.approx(30, abs=2)
    assert hit["compass_bearing_deg"] == pytest.approx(45, abs=2)
    assert result["calibration_sha256"] == checked["calibration_sha256"]


def test_short_second_caller_and_reacquisition():
    a = {"direction_q": [-0.7, 0, 0], "score": 0.9}
    b = {"direction_q": [0.7, 0, 0], "score": 0.8}
    assert len(track_blocks([[a], [a, b], [a]], 2)) == 2
    tracks = track_blocks([[a], [], [], [], [b], [b]], 1)
    assert tracks[0][4] == b


def test_processing_reacquires_short_call_after_one_silent_block(tmp_path):
    profile = load_profile("songbeam-4")
    fs = 44100
    signal = np.zeros((int(1.5 * fs), 4), dtype="float32")
    first = source_audio(profile, fs=fs, az=30, elevation=0, seconds=0.16, seed=14)
    second = source_audio(profile, fs=fs, az=150, elevation=0, seconds=0.16, seed=16)
    start1, start2 = int(0.15 * fs), int(1.15 * fs)
    signal[start1 : start1 + len(first)] += first
    signal[start2 : start2 + len(second)] += second
    source = tmp_path / "brief.wav"
    sf.write(source, signal, fs, subtype="PCM_16")
    result = process(
        source, tmp_path / "brief-out", Settings(formats=("wav",), reserve_gib=0)
    )
    blocks = result["tracks"]
    assert blocks[0]["active"] and blocks[0]["u"] == pytest.approx(-0.866, abs=0.04)
    assert not blocks[1]["active"]
    assert blocks[2]["active"] and blocks[2]["u"] == pytest.approx(0.866, abs=0.04)


def test_sipeed_two_directional_tracks_from_independent_sources(tmp_path):
    profile = load_profile("sipeed-d80")
    left = source_audio(profile, az=30, elevation=0, seconds=1.5, seed=15)
    right = source_audio(profile, az=140, elevation=0, seconds=1.5, seed=16)
    source = tmp_path / "two.wav"
    sf.write(source, 0.45 * left + 0.45 * right, 48000, subtype="PCM_16")
    result = process(
        source,
        tmp_path / "two-out",
        Settings(
            source_profile="sipeed-d80", auto_beams=2, formats=("wav",), reserve_gib=0
        ),
    )
    assert len(result["outputs"]) == 2
    for start in (0, 24000, 48000):
        observed = [
            r["azimuth_deg"]
            for r in result["tracks"]
            if r["start_sample"] == start and r["active"]
        ]
        assert len(observed) == 2
        for target in (30, 140):
            assert min(abs((a - target + 180) % 360 - 180) for a in observed) < 10


def test_cli_selects_profiles_and_keeps_legacy_default():
    parsed = parser().parse_args(
        [
            "process",
            "a.wav",
            "--output",
            "out",
            "--source",
            "sipeed-d80",
            "--input-layout",
            "ma-usb8",
        ]
    )
    assert parsed.source == "sipeed-d80" and parsed.channel_map is None
    legacy = parser().parse_args(["inspect", "a.wav", "--channel-map", "0,1,2,3"])
    assert legacy.source == "songbeam-4" and legacy.channel_map == (0, 1, 2, 3)


def test_multichannel_extensible_and_rf64_are_checked(tmp_path):
    base = tmp_path / "base.wav"
    sf.write(base, np.zeros((100, 6)), 48000, subtype="PCM_16")
    raw = base.read_bytes()
    payload = raw[36:]
    ext_fmt = (
        b"fmt "
        + struct.pack("<I", 40)
        + struct.pack("<H", 0xFFFE)
        + raw[22:36]
        + struct.pack("<HHI", 22, 16, 0)
        + bytes.fromhex("0100000000001000800000aa00389b71")
    )
    ext = (
        b"RIFF"
        + struct.pack("<I", 4 + len(ext_fmt) + len(payload))
        + b"WAVE"
        + ext_fmt
        + payload
    )
    ext_path = tmp_path / "ext.wav"
    ext_path.write_bytes(ext)
    assert riff_pcm(ext_path, 6)["frames"] == 100
    wrong = bytearray(ext)
    wrong[12 + 8 + 24] = 2
    wrong_path = tmp_path / "wrong.wav"
    wrong_path.write_bytes(wrong)
    with pytest.raises(ValueError, match="subtype"):
        riff_pcm(wrong_path, 6)
    ds64 = b"ds64" + struct.pack("<IQQQI", 28, 0, 1200, 100, 0)
    rf = (
        b"RF64"
        + struct.pack("<I", 0xFFFFFFFF)
        + b"WAVE"
        + ds64
        + raw[12:36]
        + b"data"
        + struct.pack("<I", 0xFFFFFFFF)
        + raw[44:]
    )
    rf = rf[:20] + struct.pack("<Q", len(rf) - 8) + rf[28:]
    rf_path = tmp_path / "rf64.wav"
    rf_path.write_bytes(rf)
    assert riff_pcm(rf_path, 6)["frames"] == 100
    rf_path.write_bytes(rf[:-2])
    with pytest.raises(ValueError):
        riff_pcm(rf_path, 6)
