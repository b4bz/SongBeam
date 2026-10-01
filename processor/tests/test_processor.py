from __future__ import annotations

import json
import struct
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf
from scipy.fft import irfft, rfft
from scipy.signal import butter, sosfilt

import songbeam.process as processing
from songbeam.audio import beam_fft, inspect, read_block, riff_pcm
from songbeam.cli import analyze_mono, cross_beam_overlaps, retry_destination
from songbeam.localize import candidates, track_blocks
from songbeam.process import OutputSet, Settings, _calibrate, _mount_guard, process

FS = 44100
POSITIONS = np.array([0, 0.045, 0.075, 0.12])


def fixture_sources(seconds: float = 2.0):
    frames = round(seconds * FS)
    rng = np.random.default_rng(714)
    bandpass = butter(4, [2500, 6500], btype="bandpass", fs=FS, output="sos")

    def direction(u):
        waveform = sosfilt(bandpass, rng.standard_normal(frames))
        waveform /= max(abs(waveform))
        spectrum = rfft(waveform)
        frequency = np.fft.rfftfreq(frames)
        channels = np.stack(
            [
                irfft(
                    spectrum * np.exp(-2j * np.pi * frequency * FS * p * u / 343),
                    n=frames,
                )
                for p in POSITIONS
            ],
            axis=1,
        )
        return channels.astype("float32")

    return direction(0.72), direction(-0.68)


def test_inspection_rejects_malformed_and_preserves_source(tmp_path):
    source = tmp_path / "source.wav"
    one, _ = fixture_sources(0.3)
    sf.write(source, one, FS, subtype="PCM_16")
    raw = source.read_bytes()
    report = inspect(source)
    assert report["frames"] == len(one) and report["channels"] == 4
    assert source.read_bytes() == raw
    truncated = tmp_path / "truncated.wav"
    truncated.write_bytes(raw[:-8])
    with pytest.raises(ValueError, match="RIFF size mismatch"):
        riff_pcm(truncated)
    malformed = tmp_path / "malformed.wav"
    malformed.write_bytes(raw[:4] + struct.pack("<I", len(raw) - 52) + raw[8:])
    with pytest.raises(ValueError):
        riff_pcm(malformed)


def test_localization_two_sources_and_silence():
    a, b = fixture_sources(1.0)
    got = candidates(0.2 * a + 0.2 * b, FS, 2)
    assert len(got) == 2
    assert sorted(item["u"] for item in got) == pytest.approx([-0.68, 0.72], abs=0.05)
    assert candidates(np.zeros_like(a), FS, 2) == []
    assert len(track_blocks([[got[0], got[1]]] * 4, 2)) == 2
    assert len(track_blocks([[got[0]]] * 4, 2)) == 1
    same_direction = candidates(0.2 * a + 0.2 * a, FS, 2)
    assert len(same_direction) == 1


def test_beam_improves_directional_contrast_without_changing_length():
    target, interferer = fixture_sources(0.5)
    length = len(target)
    beam_target = beam_fft(target, 0, 0, length, FS, 0.72)
    beam_interferer = beam_fft(interferer, 0, 0, length, FS, 0.72)
    edge = 100
    beam_ratio = np.sqrt(np.mean(beam_target[edge:-edge] ** 2)) / np.sqrt(
        np.mean(beam_interferer[edge:-edge] ** 2)
    )
    raw_ratio = np.sqrt(np.mean(target[edge:-edge, 0] ** 2)) / np.sqrt(
        np.mean(interferer[edge:-edge, 0] ** 2)
    )
    assert length == len(beam_target)
    assert 20 * np.log10(beam_ratio / raw_ratio) > 3.0


def test_end_to_end_two_tracks_codecs_manifest_and_no_overwrite(tmp_path):
    a, b = fixture_sources(2.0)
    source = tmp_path / "four.wav"
    sf.write(source, 0.22 * a + 0.22 * b, FS, subtype="PCM_16")
    original = source.read_bytes()
    out = tmp_path / "results"
    settings = Settings(
        channel_map=(0, 1, 2, 3),
        auto_beams=2,
        formats=("wav", "flac", "mp3"),
        reserve_gib=0,
    )
    result = process(source, out, settings)
    assert result["status"] == "complete" and len(result["outputs"]) == 2
    assert source.read_bytes() == original
    for track in (1, 2):
        base = f"track-{track:03d}"
        assert sf.info(out / f"{base}.wav").frames == len(a)
        assert sf.info(out / f"{base}.flac").frames == len(a)
        assert sf.info(out / f"{base}.mp3").channels == 1
        wav, _ = sf.read(out / f"{base}.wav", dtype="int32")
        flac, _ = sf.read(out / f"{base}.flac", dtype="int32")
        assert np.array_equal(wav, flac)
        for fmt in ("wav", "flac", "mp3"):
            assert result["outputs"][base][fmt]["sha256"]
    assert json.loads((out / "manifest.json").read_text())["status"] == "complete"
    with pytest.raises(FileExistsError):
        process(source, out, settings)


def test_birdnet_rejects_multichannel_and_false_success(tmp_path):
    a, _ = fixture_sources(0.2)
    multi = tmp_path / "multi.wav"
    sf.write(multi, a, FS, subtype="PCM_16")
    with pytest.raises(ValueError, match="mono"):
        analyze_mono(multi, tmp_path / "results1", "/bin/true", 0.25)
    mono = tmp_path / "mono.wav"
    sf.write(mono, a[:, 0], FS, subtype="PCM_16")
    with pytest.raises(RuntimeError, match="omitted"):
        analyze_mono(mono, tmp_path / "results2", "/bin/true", 0.25)
    assert (
        json.loads((tmp_path / "results2/job-status.json").read_text())["status"]
        == "failed"
    )
    batch = tmp_path / "batch"
    batch.mkdir()
    sf.write(batch / "same.wav", a[:, 0], FS, subtype="PCM_16")
    sf.write(batch / "same.flac", a[:, 0], FS, subtype="PCM_16")
    with pytest.raises(ValueError, match="unique stems"):
        analyze_mono(batch, tmp_path / "results3", "/bin/true", 0.25)


def test_space_rejection_and_failure_retry_retains_old_attempt(tmp_path, monkeypatch):
    a, _ = fixture_sources(0.2)
    source = tmp_path / "four.wav"
    sf.write(source, a * 0.2, FS, subtype="PCM_16")
    settings = Settings(channel_map=(0, 1, 2, 3), manual_angles=(20,), reserve_gib=0)
    original_usage = processing.shutil.disk_usage
    monkeypatch.setattr(
        processing.shutil, "disk_usage", lambda path: SimpleNamespace(free=1)
    )
    with pytest.raises(OSError, match="Insufficient"):
        process(source, tmp_path / "too-small", settings)
    assert not (tmp_path / "too-small").exists()
    monkeypatch.setattr(processing.shutil, "disk_usage", original_usage)
    original_write = processing.OutputSet.write

    def fail_write(self, audio):
        raise OSError("synthetic encoder failure")

    monkeypatch.setattr(processing.OutputSet, "write", fail_write)
    failed = tmp_path / "failed"
    with pytest.raises(OSError, match="synthetic"):
        process(source, failed, settings)
    assert json.loads((failed / "manifest.json").read_text())["status"] == "failed"
    monkeypatch.setattr(processing.OutputSet, "write", original_write)
    retry = retry_destination(failed, source, settings)
    result = process(source, retry, settings, retry_of=failed.name)
    assert result["status"] == "complete" and result["retry_of"] == failed.name
    assert json.loads((failed / "manifest.json").read_text())["status"] == "failed"
    changed = Settings(channel_map=(0, 1, 2, 3), manual_angles=(21,), reserve_gib=0)
    with pytest.raises(ValueError, match="differ"):
        retry_destination(failed, source, changed)


def test_wav_switches_to_rf64_before_riff_limit(tmp_path):
    settings = Settings(
        channel_map=(0, 1, 2, 3), manual_angles=(0,), formats=("wav",), reserve_gib=0
    )
    large_frame_count = (2**32 // 3) + 100
    writer = OutputSet(tmp_path, 1, FS, large_frame_count, settings)
    writer.write(np.zeros(100, dtype="float32"))
    writer.close()
    assert sf.info(tmp_path / "track-001.wav").format == "RF64"


def test_explicit_gain_polarity_and_fixed_delay_calibration():
    target, _ = fixture_sources(0.3)
    observed = target.copy()
    observed[:, 1] *= -2
    observed[:, 2] = np.roll(observed[:, 2], 1)
    settings = Settings(
        channel_map=(0, 1, 2, 3),
        gains=(1, 0.5, 1, 1),
        polarities=(1, -1, 1, 1),
        fixed_delays_samples=(0, 0, 1, 0),
    )
    restored = _calibrate(observed, settings)
    for channel in (1, 2):
        assert (
            np.corrcoef(restored[100:-100, channel], target[100:-100, channel])[0, 1]
            > 0.99
        )


def test_chunk_boundaries_match_full_directional_signal(tmp_path):
    target, _ = fixture_sources(2.0)
    source = tmp_path / "four.wav"
    sf.write(source, target * 0.2, FS, subtype="PCM_16")
    with sf.SoundFile(source) as stream:
        whole, first = read_block(stream, 0, len(target), (0, 1, 2, 3))
        expected = beam_fft(whole, first, 0, len(target), FS, 0.72)
        pieces = []
        for start in (0, FS):
            block, first = read_block(stream, start, FS, (0, 1, 2, 3))
            pieces.append(beam_fft(block, first, start, FS, FS, 0.72))
    actual = np.concatenate(pieces)
    assert np.sqrt(np.mean((expected - actual) ** 2)) < 1e-5


def test_cross_beam_report_flags_overlap_without_merging_predictions(tmp_path):
    header = "Start (s),End (s),Scientific name,Common name,Confidence,File\n"
    first = tmp_path / "track-001.BirdNET.results.csv"
    second = tmp_path / "track-002.BirdNET.results.csv"
    first.write_text(header + "3,6,Testus birdus,Test Bird,0.82,one.flac\n")
    second.write_text(
        header
        + "4,7,Testus birdus,Test Bird,0.71,two.flac\n"
        + "4,7,Otherus birdus,Other Bird,0.95,two.flac\n"
    )
    before = (first.read_bytes(), second.read_bytes())
    matched = cross_beam_overlaps([first, second])
    assert len(matched) == 1
    assert matched[0]["scientific_name"] == "Testus birdus"
    assert before == (first.read_bytes(), second.read_bytes())


def test_required_data_mount_rejects_plain_directory(tmp_path):
    with pytest.raises(OSError, match="not mounted"):
        _mount_guard(tmp_path, tmp_path)
