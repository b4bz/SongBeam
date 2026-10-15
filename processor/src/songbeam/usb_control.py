"""Small host command tool for SongBeam's local USB Serial control channel."""

from __future__ import annotations

import argparse
import time


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Control a locally connected SongBeam")
    parser.add_argument("port", help="USB Serial port, such as /dev/ttyACM0")
    parser.add_argument("action", choices=("status", "time", "card"))
    parser.add_argument("--epoch", type=int, help="UTC Unix time for the time action")
    args = parser.parse_args(argv)
    if args.epoch is not None and args.action != "time":
        parser.error("--epoch is only valid with the time action")
    epoch = args.epoch if args.epoch is not None else int(time.time())
    if args.action == "time" and not 1577836800 <= epoch <= 4102444800:
        parser.error("UTC epoch must be from 2020 through 2099")
    command = {
        "status": "STATUS",
        "time": f"TIME {epoch}",
        "card": "CARD",
    }[args.action]

    try:
        import serial  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SystemExit("Install the processor's usb extra (pyserial) first") from exc

    with serial.Serial(args.port, timeout=0.5, write_timeout=2) as device:
        device.reset_input_buffer()
        device.write((command + "\n").encode("ascii"))
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            line = device.readline().strip()
            if line.startswith((b"SB FW", b"OK ", b"ERR ")):
                message = line.decode("ascii", "replace")
                print(message)
                if args.action == "card" and line.startswith(b"OK CARD"):
                    print(
                        "MTP is ready. Safely unmount it and power-cycle before SD recording."
                    )
                return 0 if not line.startswith(b"ERR ") else 1
    raise SystemExit("SongBeam did not answer the USB control command")


if __name__ == "__main__":
    raise SystemExit(main())
