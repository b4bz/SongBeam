"""Local command framing and failure handling without physical hardware."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from songbeam import usb_control


@pytest.mark.parametrize(
    "action,response,expected_command,code",
    [
        ("status", b"SB FW MTP1 SD=1 RTC=1 MODE=0\n", b"STATUS\n", 0),
        ("card", b"OK CARD MTP\n", b"CARD\n", 0),
        ("card", b"ERR SD unavailable\n", b"CARD\n", 1),
        ("time", b"OK TIME\n", b"TIME 1792100000\n", 0),
    ],
)
def test_usb_control(monkeypatch, capsys, action, response, expected_command, code):
    class FakePort:
        def __init__(self) -> None:
            self.commands: list[bytes] = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def reset_input_buffer(self):
            return None

        def write(self, value: bytes):
            self.commands.append(value)
            return len(value)

        def readline(self):
            return response

    port = FakePort()
    monkeypatch.setitem(
        sys.modules, "serial", SimpleNamespace(Serial=lambda *_a, **_k: port)
    )
    args = ["FAKE", action]
    if action == "time":
        args.extend(("--epoch", "1792100000"))
    assert usb_control.main(args) == code
    assert port.commands == [expected_command]
    assert response.strip().decode() in capsys.readouterr().out


def test_rejects_epoch_for_card():
    with pytest.raises(SystemExit):
        usb_control.main(["FAKE", "card", "--epoch", "1792100000"])
