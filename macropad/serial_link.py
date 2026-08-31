"""Find and maintain a connection to the MacroPad's USB serial data port.

The MacroPad enumerates as two CDC serial ports: a console (REPL) and a
data channel (opened by firmware/boot.py). We want the data channel.
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

import serial
from serial.tools import list_ports

ADAFRUIT_VID = 0x239A


def find_macropad_port() -> Optional[str]:
    """Best-effort discovery of the MacroPad's data serial port."""
    try:
        from adafruit_board_toolkit import circuitpython_serial

        ports = circuitpython_serial.data_comports()
        if ports:
            return ports[0].device
    except ImportError:
        pass

    for port in list_ports.comports():
        if port.vid == ADAFRUIT_VID:
            return port.device
        if port.product and "macropad" in port.product.lower():
            return port.device
    return None


class ReconnectingSerial:
    """A serial connection to the MacroPad that self-heals.

    A background thread keeps trying to (re)connect whenever the link is
    down, so callers never block waiting for the device to be plugged in,
    wake from sleep, or re-enumerate after a reset. write_line() is
    non-blocking: it drops the message and returns False if there's
    currently no connection.

    If `on_line` is given, a second background thread continuously reads
    newline-delimited lines the board sends *back* (encoder-selection
    changes, action-key presses -- see firmware/code.py) and calls
    `on_line(line)` for each one. Never raises into the caller; parsing
    and dispatch errors are swallowed so a malformed line from the board
    can't kill the bridge.

    If `on_connect` is given, it's called (from the reconnect thread)
    every time a new connection is established -- including the first
    one and every reconnect after an unplug/sleep/reset -- so callers can
    re-sync anything that's only pushed on-change rather than polled
    (e.g. macropad-bridge asking the board to re-announce its current
    encoder selection, since the board itself has no reason to repeat a
    `{"selected": N}` it already sent once, and the bridge would
    otherwise silently keep assuming key 0 until the next physical
    rotation/press -- see Bridge._on_serial_connect()).
    """

    def __init__(self, baudrate: int = 115200, retry_seconds: float = 2.0,
                 on_line: Optional[Callable[[str], None]] = None,
                 on_connect: Optional[Callable[[], None]] = None):
        self.baudrate = baudrate
        self.retry_seconds = retry_seconds
        self._on_line = on_line
        self._on_connect = on_connect
        self._ser: Optional[serial.Serial] = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._reconnect_loop, daemon=True)
        self._thread.start()
        self._reader_thread: Optional[threading.Thread] = None
        if self._on_line is not None:
            self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
            self._reader_thread.start()

    def _reconnect_loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                connected = self._ser is not None and self._ser.is_open
            if not connected:
                port = find_macropad_port()
                if port:
                    try:
                        ser = serial.Serial(port, self.baudrate, timeout=1)
                    except serial.SerialException:
                        ser = None
                    if ser is not None:
                        with self._lock:
                            self._ser = ser
                        print(f"[macropad-bridge] connected to {port}", flush=True)
                        if self._on_connect is not None:
                            try:
                                self._on_connect()
                            except Exception as exc:  # noqa: BLE001 -- never let this kill reconnect
                                print(f"[macropad-bridge] on_connect handler failed: {exc}", flush=True)
            self._stop.wait(self.retry_seconds)

    def _read_loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                ser = self._ser
            if ser is None:
                self._stop.wait(0.2)
                continue
            try:
                raw = ser.readline()
            except serial.SerialException:
                with self._lock:
                    self._ser = None
                continue
            if not raw:
                continue
            try:
                self._on_line(raw.decode("utf-8", errors="ignore").strip())
            except Exception as exc:  # noqa: BLE001 -- never let a bad line kill the reader
                print(f"[macropad-bridge] error handling line from board: {exc}", flush=True)

    def write_line(self, line: str) -> bool:
        with self._lock:
            ser = self._ser
        if ser is None:
            return False
        try:
            ser.write((line + "\n").encode("utf-8"))
            return True
        except serial.SerialException:
            with self._lock:
                self._ser = None
            return False

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1)
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=1)
        with self._lock:
            if self._ser is not None:
                self._ser.close()
                self._ser = None
