"""macropad-flash: pushes firmware/boot.py and firmware/code.py to the
MacroPad over its serial REPL (via `ampy`), then triggers a soft reboot.

Why not just copy to the CIRCUITPY drive: mass-storage writes tunneled
through usbipd-win into WSL are unreliable for this device -- they can
report success at every step (cp, sync, umount, even a hard reboot) while
never actually reaching flash, or worse, leave the FAT filesystem in a
state that gets reported as write-protected to *any* host afterwards,
tunnel or native. See docs/troubleshooting.md ("Updating firmware over
WSL/USB") for the full story. Writing over the serial REPL instead
sidesteps USB mass storage entirely and has been completely reliable in
comparison.

This requires firmware/boot.py's `storage.remount("/", readonly=False)`
to already be active on the board -- that's what grants the REPL/runtime
side write access in the first place (CircuitPython's default is the
opposite: host-writable over USB, runtime-read-only). If the board is
still running an older boot.py without that call, this fails with
`OSError: [Errno 30] Read-only filesystem` -- see docs/troubleshooting.md
for the one-time bootstrap (a native, non-tunneled write) needed to get
a boot.py with that call onto the board in the first place.

Requires the `firmware` extra: `uv sync --extra firmware`.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import serial
from serial.tools import list_ports

ADAFRUIT_VID = 0x239A
REPO_ROOT = Path(__file__).resolve().parent.parent


def find_console_port() -> Optional[str]:
    """Best-effort discovery of the MacroPad's *console* (REPL) serial
    port -- distinct from serial_link.find_macropad_port(), which finds
    the data channel macropad-bridge talks to instead."""
    try:
        from adafruit_board_toolkit import circuitpython_serial

        ports = circuitpython_serial.repl_comports()
        if ports:
            return ports[0].device
    except ImportError:
        pass

    candidates = [p for p in list_ports.comports() if p.vid == ADAFRUIT_VID]
    if not candidates:
        return None
    # Without the toolkit we can't distinguish console from data by
    # description alone -- in every case we've seen, the console
    # enumerates as the lowest-numbered device node (e.g. ttyACM0 vs
    # ttyACM2), so fall back to that.
    return sorted(candidates, key=lambda p: p.device)[0].device


def _run_ampy(port: str, *args: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "ampy.cli", "--port", port, *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ampy {' '.join(args)} failed:\n{result.stdout}{result.stderr}")


def push_file(port: str, local_path: Path, remote_name: str) -> None:
    print(f"[macropad-flash] pushing {local_path} -> {remote_name} ...", flush=True)
    _run_ampy(port, "put", str(local_path), remote_name)


def soft_reboot(port: str) -> str:
    """Sends Ctrl-B (exit raw REPL, in case ampy left it there) then
    Ctrl-D (CircuitPython soft reboot -- unlike vanilla MicroPython, this
    re-runs boot.py *and* code.py). Returns whatever the board printed,
    so callers can check for a traceback."""
    ser = serial.Serial(port, 115200, timeout=1)
    try:
        ser.write(b"\x02")
        time.sleep(0.5)
        ser.read(2000)
        ser.write(b"\x04")
        time.sleep(3)
        return ser.read(4000).decode("utf-8", errors="replace")
    finally:
        ser.close()


def main() -> None:
    try:
        import ampy  # noqa: F401
    except ImportError:
        print("[macropad-flash] the 'ampy' package isn't installed -- run "
              "`uv sync --extra firmware` first.", flush=True)
        raise SystemExit(1)

    port = find_console_port()
    if port is None:
        print("[macropad-flash] no MacroPad console port found -- is it plugged in "
              "(and attached to WSL, if applicable -- `usbipd attach --wsl "
              "--busid <busid>`)?", flush=True)
        raise SystemExit(1)
    print(f"[macropad-flash] using console port {port}", flush=True)

    try:
        push_file(port, REPO_ROOT / "firmware" / "boot.py", "boot.py")
        push_file(port, REPO_ROOT / "firmware" / "code.py", "code.py")
    except RuntimeError as exc:
        print(f"[macropad-flash] {exc}", flush=True)
        if "Read-only filesystem" in str(exc):
            print(
                "[macropad-flash] the board's *runtime* filesystem is read-only, "
                "meaning it's still running an old boot.py without "
                "storage.remount('/', readonly=False). See docs/troubleshooting.md "
                "-- 'Updating firmware over WSL/USB' -- for the one-time native "
                "(non-tunneled) write needed to bootstrap this.",
                flush=True,
            )
        raise SystemExit(1)

    print("[macropad-flash] rebooting board...", flush=True)
    output = soft_reboot(port)
    if "Traceback" in output:
        print(f"[macropad-flash] warning: code.py raised on startup:\n{output}", flush=True)
    else:
        print("[macropad-flash] done -- code.py is running the new firmware.", flush=True)


if __name__ == "__main__":
    main()
