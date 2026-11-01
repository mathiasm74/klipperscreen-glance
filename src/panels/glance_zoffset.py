# -*- coding: utf-8 -*-
# Shared z-offset bookkeeping for the glance panels. The z-offset controls
# edit the unsaved change (0 = matches the saved offset) and show the
# resulting total (gcode_move.homing_origin Z, the number Mainsail shows)
# separately. "Base" is the total at which the change reads 0.
#
# With the BTT Eddy z-offset macros the whole offset lives in the gcode
# offset: total = probe reading at the last home + runtime_offset, and
# Z_OFFSET_APPLY_PROBE saves runtime_offset to save_variables (nvm_offset)
# without a restart. KlipperScreen doesn't subscribe to either, so they are
# queried. On a stock setup homing_origin is already the unsaved change,
# so the base is 0.


def has_nvm_offset(printer):
    return (printer.config_section_exists("save_variables")
            and printer.config_section_exists("gcode_macro SET_GCODE_OFFSET"))


def query_base(screen, printer, callback):
    # calls callback(base) once known; immediately on a stock setup
    if not has_nvm_offset(printer):
        callback(0.0)
        return

    def on_result(response, *args):
        try:
            st = response["result"]["status"]
            runtime = float(st["gcode_macro SET_GCODE_OFFSET"]["runtime_offset"])
            saved = float(st["save_variables"]["variables"].get("nvm_offset", 0))
            live = float(st["gcode_move"]["homing_origin"][2])
        except (KeyError, TypeError, ValueError):
            return
        callback(live - (runtime - saved))

    screen._ws.send_method("printer.objects.query", {"objects": {
        "gcode_macro SET_GCODE_OFFSET": ["runtime_offset"],
        "save_variables": ["variables"],
        "gcode_move": ["homing_origin"],
    }}, on_result)


def fmt_delta(delta):
    # signed, with float dust squashed so 0 never reads as -0.000
    return f"{0.0 if abs(delta) < 0.0005 else delta:+.3f}"
