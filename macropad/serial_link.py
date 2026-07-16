"""Find and maintain a connection to the MacroPad's USB serial data port.

The MacroPad enumerates as two CDC serial ports: a console (REPL) and a
data channel (opened by firmware/boot.py). We want the data channel.
"""
from __future__ import annotations

import threading
from typing import Optional

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
    """

    def __init__(self, baudrate: int = 115200, retry_seconds: float = 2.0):
        self.baudrate = baudrate
        self.retry_seconds = retry_seconds
        self._ser: Optional[serial.Serial] = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._reconnect_loop, daemon=True)
        self._thread.start()

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
            self._stop.wait(self.retry_seconds)

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
        with self._lock:
            if self._ser is not None:
                self._ser.close()
                self._ser = None
