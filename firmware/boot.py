# boot.py -- runs once at power-on, before code.py.
#
# Enables a second USB CDC "data" serial channel alongside the normal
# CircuitPython console. The host-side bridge talks to the MacroPad over
# this data channel so it never fights with the REPL/console connection.
#
# After editing this file, you must unplug and replug the MacroPad for the
# change to take effect (USB descriptors are only read at boot).

import usb_cdc

usb_cdc.enable(console=True, data=True)
