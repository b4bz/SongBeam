"""CLI entry point for SongBeam offline work."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import soundfile as sf

from . import __version__
from .audio import inspect, sha256_file
from .process import Settings, _mount_guard, process


def _mapping(value: str) -> tuple[int, int, int, int]:
    try:
        result = tuple(int(part.strip()) for part in value.split(","))
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "channel map must be four comma-separated indices"
        ) from error
    if sorted(result) != [0, 1, 2, 3]:
        raise argparse.ArgumentTypeError("channel map must permute 0,1,2,3")
    return result  # type: ignore[return-value]


def _quad_floats(value: str) -> tuple[float, float, float, float]:
    try:
        result = tuple(float(part.strip()) for part in value.split(","))
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "expected four comma-separated numbers"
        ) from error
    if len(result) != 4:
        raise argparse.ArgumentTypeError("expected four comma-separated numbers")
    return result  # type: ignore[return-value]


def _quad_polarities(value: str) -> tuple[int, int, int, int]:
    try:
        result = tuple(int(part.strip()) for part in value.split(","))
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected four +1 or -1 values") from error
    if len(result) != 4 or any(x not in (-1, 1) for x in result):
        raise argparse.ArgumentTypeError("expected four +1 or -1 values")
    return result  # type: ignore[return-value]


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="songbeam", description="Validate and process four-channel SongBeam WAVs"
    )
    sub = p.add_subparsers(dest="command", required=True)
    inspect_cmd = sub.add_parser(
        "inspect", help="validate recording and report channels"
    )
    inspect_cmd.add_argument("input", type=Path)
    inspect_cmd.add_argument("--channel-map", type=_mapping, required=True)
    run = sub.add_parser("process", help="export directional mono audio")
    run.add_argument("input", type=Path)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--channel-map", type=_mapping, required=True)
    run.add_argument("--calibration-id", default="unverified-board")
    run.add_argument("--gains", type=_quad_floats, default=(1.0, 1.0, 1.0, 1.0))
    run.add_argument("--polarities", type=_quad_polarities, default=(1, 1, 1, 1))
    run.add_argument(
        "--fixed-delays-samples", type=_quad_floats, default=(0.0, 0.0, 0.0, 0.0)
    )
    run.add_argument("--manual-angle", type=float, action="append", default=[])
    run.add_argument("--auto-beams", type=int, choices=(1, 2), default=1)
    run.add_argument(
        "--format", action="append", choices=("wav", "flac", "mp3"), default=[]
    )
    run.add_argument(
        "--wav-subtype", choices=("PCM_16", "PCM_24", "FLOAT"), default="PCM_24"
    )
    run.add_argument("--flac-subtype", choices=("PCM_16", "PCM_24"), default="PCM_24")
    run.add_argument("--flac-level", type=int, default=5)
    run.add_argument("--mp3-bitrate", type=int, choices=(128, 192, 256), default=192)
    run.add_argument("--mp3-vbr-quality", type=int, choices=range(10), default=None)
    run.add_argument("--fmin", type=float, default=2000)
    run.add_argument("--fmax", type=float, default=8000)
    run.add_argument("--block-seconds", type=float, default=0.5)
    run.add_argument("--reserve-gib", type=float, default=8)
    run.add_argument(
        "--require-mount",
        type=Path,
        default=None,
        help="require output to remain on this mounted data filesystem",
    )
    run.add_argument(
        "--resume",
        action="store_true",
        help="restart a failed/canceled job in a new sibling directory",
    )
    birdnet = sub.add_parser(
        "analyze", help="run BirdNET on mono files or a flat mono directory"
    )
    birdnet.add_argument("input", type=Path)
    birdnet.add_argument("--output", type=Path, required=True)
    birdnet.add_argument("--executable", default="birdnet-analyze")
    birdnet.add_argument("--min-conf", type=float, default=0.25)
    birdnet.add_argument("--threads", type=int, default=2)
    birdnet.add_argument("--batch-size", type=int, default=8)
    birdnet.add_argument("--require-mount", type=Path, default=None)
    return p


def analyze_mono(
    input_path: Path,
    output: Path,
    executable: str,
    min_conf: float,
    threads: int = 2,
    batch_size: int = 8,
    required_mount: Path | None = None,
) -> dict:
    if input_path.is_symlink():
        raise ValueError("BirdNET input symlinks are not accepted")
    if input_path.is_dir():
        if any(item.is_dir() or item.is_symlink() for item in input_path.iterdir()):
            raise ValueError(
                "BirdNET batch input must be a flat directory without symlinks"
            )
        files = sorted(
            item
            for item in input_path.iterdir()
            if item.suffix.lower() in {".wav", ".flac"}
        )
    else:
        files = [input_path]
    if not files:
        raise ValueError("BirdNET input has no lossless files")
    if len({path.stem for path in files}) != len(files):
        raise ValueError("BirdNET input filenames must have unique stems")
    for path in files:
        info = sf.info(str(path))
        if info.channels != 1 or info.frames < 1 or info.format not in {"WAV", "FLAC"}:
            raise ValueError(
                f"BirdNET input must be nonempty mono WAV or FLAC: {path.name}"
            )
    if not 0 < min_conf < 1:
        raise ValueError("min_conf must be between 0 and 1")
    if not 1 <= threads <= 8 or not 1 <= batch_size <= 32:
        raise ValueError("threads must be 1–8 and batch size 1–32")
    if output.exists():
        raise FileExistsError(output)
    if shutil.which(executable) is None:
        raise FileNotFoundError(executable)
    output.parent.resolve(strict=True)
    _mount_guard(output.parent, required_mount)
    output.mkdir(mode=0o750)
    command = [
        executable,
        str(input_path),
        "--output",
        str(output),
        "--rtype",
        "csv",
        "--min_conf",
        str(min_conf),
        "--threads",
        str(threads),
        "--batch_size",
        str(batch_size),
    ]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    expected = [output / (path.stem + ".BirdNET.results.csv") for path in files]
    params = output / "BirdNET_analysis_params.csv"
    if (
        result.returncode
        or "Error: Cannot" in (result.stdout + result.stderr)
        or any(not path.is_file() for path in expected)
        or not params.is_file()
    ):
        (output / "job-status.json").write_text(
            json.dumps(
                {
                    "status": "failed",
                    "exit_code": result.returncode,
                    "expected_files": len(files),
                    "found_results": sum(path.is_file() for path in expected),
                }
            )
            + "\n"
        )
        raise RuntimeError("BirdNET failed or omitted its result CSV")
    status = {
        "status": "complete",
        "input_count": len(files),
        "model_run": "external-BirdNET-Analyzer",
        "inputs": [{"name": path.name, "sha256": sha256_file(path)} for path in files],
        "results": [
            {"name": path.name, "sha256": sha256_file(path)} for path in expected
        ],
        "parameters": {"name": params.name, "sha256": sha256_file(params)},
    }
    if len(expected) > 1:
        try:
            overlaps = cross_beam_overlaps(expected)
        except (ValueError, OSError) as error:
            status["status"] = "failed"
            status["postprocess_error"] = type(error).__name__
            (output / "job-status.json").write_text(json.dumps(status, indent=2) + "\n")
            raise
        report_path = output / "possible_cross_beam_overlaps.csv"
        with report_path.open("w", newline="") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=[
                    "file_a",
                    "file_b",
                    "scientific_name",
                    "start_a_s",
                    "end_a_s",
                    "start_b_s",
                    "end_b_s",
                    "confidence_a",
                    "confidence_b",
                ],
            )
            writer.writeheader()
            writer.writerows(overlaps)
        status["possible_cross_beam_overlaps"] = {
            "name": report_path.name,
            "count": len(overlaps),
            "sha256": sha256_file(report_path),
        }
    (output / "job-status.json").write_text(json.dumps(status, indent=2) + "\n")
    return status


def cross_beam_overlaps(result_paths: list[Path]) -> list[dict]:
    """Flag same-species temporal overlap without altering original predictions."""
    by_species = defaultdict(list)
    for path in result_paths:
        with path.open(newline="") as stream:
            reader = csv.DictReader(stream)
            required = {"Start (s)", "End (s)", "Scientific name", "Confidence"}
            if not required.issubset(set(reader.fieldnames or [])):
                raise ValueError(f"BirdNET result missing columns: {path.name}")
            for row in reader:
                start = float(row["Start (s)"])
                end = float(row["End (s)"])
                confidence = float(row["Confidence"])
                if not (0 <= start < end and 0 <= confidence <= 1):
                    raise ValueError(
                        f"BirdNET result has invalid timing/score: {path.name}"
                    )
                by_species[row["Scientific name"]].append(
                    (path.name, start, end, confidence)
                )
    matches = []
    for scientific_name, detections in by_species.items():
        active = []
        for entry in sorted(detections, key=lambda item: item[1]):
            active = [old for old in active if old[2] > entry[1]]
            for old in active:
                if old[0] == entry[0]:
                    continue
                shared = min(old[2], entry[2]) - max(old[1], entry[1])
                if shared < 0.5 * min(old[2] - old[1], entry[2] - entry[1]):
                    continue
                matches.append(
                    {
                        "file_a": old[0],
                        "file_b": entry[0],
                        "scientific_name": scientific_name,
                        "start_a_s": old[1],
                        "end_a_s": old[2],
                        "start_b_s": entry[1],
                        "end_b_s": entry[2],
                        "confidence_a": old[3],
                        "confidence_b": entry[3],
                    }
                )
                if len(matches) > 100000:
                    raise ValueError("Cross-beam overlap report exceeds safe size")
            active.append(entry)
    return matches


def retry_destination(prior: Path, source: Path, settings: Settings) -> Path:
    if prior.is_symlink() or not prior.is_dir():
        raise ValueError("--resume needs an existing failed output directory")
    record = json.loads((prior / "manifest.json").read_text())
    expected = {
        key: list(value) if isinstance(value, tuple) else value
        for key, value in vars(settings).items()
    }
    if (
        record.get("status") not in {"failed", "canceled"}
        or record.get("source", {}).get("source_sha256") != sha256_file(source)
        or record.get("processor_version") != __version__
        or record.get("settings") != expected
    ):
        raise ValueError("prior job status, source hash, version or settings differ")
    for suffix in range(1, 100):
        candidate = prior.with_name(f"{prior.name}-retry-{suffix:03d}")
        if not candidate.exists() and not candidate.is_symlink():
            return candidate
    raise FileExistsError("too many retries")


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "inspect":
            result = inspect(args.input, args.channel_map)
        elif args.command == "process":
            settings = Settings(
                channel_map=args.channel_map,
                calibration_id=args.calibration_id,
                gains=args.gains,
                polarities=args.polarities,
                fixed_delays_samples=args.fixed_delays_samples,
                manual_angles=tuple(args.manual_angle),
                auto_beams=args.auto_beams,
                formats=tuple(args.format or ["flac"]),
                wav_subtype=args.wav_subtype,
                flac_subtype=args.flac_subtype,
                flac_level=args.flac_level,
                mp3_bitrate=args.mp3_bitrate,
                mp3_vbr_quality=args.mp3_vbr_quality,
                fmin=args.fmin,
                fmax=args.fmax,
                block_seconds=args.block_seconds,
                reserve_gib=args.reserve_gib,
            )
            destination = (
                retry_destination(args.output, args.input, settings)
                if args.resume
                else args.output
            )
            result = process(
                args.input,
                destination,
                settings,
                retry_of=args.output.name if args.resume else None,
                required_mount=args.require_mount,
            )
        else:
            result = analyze_mono(
                args.input,
                args.output,
                args.executable,
                args.min_conf,
                args.threads,
                args.batch_size,
                args.require_mount,
            )
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, OSError, RuntimeError) as error:
        print(f"songbeam: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
