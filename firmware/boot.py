# boot.py -- runs once at power-on, before code.py.
#
# Enables a second USB CDC "data" serial channel alongside the normal
# CircuitPython console. The host-side bridge talks to the MacroPad over
# this data channel so it never fights with the REPL/console connection.
#
# Also flips CircuitPython's default filesystem permissions: normally the
# USB mass-storage drive (CIRCUITPY) is read-write and the running code/
# REPL side is read-only, so a host computer can edit files. We want the
# opposite -- code.py/boot.py get pushed over the serial REPL instead
# (`ampy`/`mpremote`, see docs/customization.md), which is far more
# reliable over a tunneled USB connection (e.g. usbipd-win -> WSL) than
# mass-storage block writes, which silently fail to persist in that
# setup despite every host-side check reporting success. CIRCUITPY still
# shows up as a normal drive for browsing/reading, just read-only.
#
# After editing this file, you must unplug and replug the MacroPad (or
# trigger a hard reset) for the change to take effect -- boot.py only
# runs at actual power-on/reset, not on every code.py auto-reload.

import storage
import usb_cdc

usb_cdc.enable(console=True, data=True)
storage.remount("/", readonly=False)
