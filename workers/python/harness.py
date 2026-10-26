"""Fluxonata Python action harness, protocol v1. Runs inside the isolated runtime.

Reads /work-relative input.json, imports action.py, calls entrypoint(inputs) and prints ONE JSON
envelope on the real stdout. Everything the action prints goes to stderr (logs).
"""
import importlib.util
import json
import os
import sys
import traceback

real_stdout = sys.stdout
sys.stdout = sys.stderr  # user prints become logs


def emit(envelope: dict) -> None:
    real_stdout.write(json.dumps({"protocol": 1, **envelope}) + "\n")
    real_stdout.flush()


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "input.json")) as fh:
        payload = json.load(fh)
    spec = importlib.util.spec_from_file_location("action", os.path.join(here, "action.py"))
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        result = getattr(module, payload["entrypoint"])(payload["inputs"])
        json.dumps(result)
    except TypeError as exc:  # non-serializable result or bad signature
        traceback.print_exc()
        emit({"ok": False, "error": {"code": "INVALID_RESULT", "message": str(exc)[:300]}})
        return
    except BaseException as exc:  # noqa: BLE001 - report any action failure structurally
        traceback.print_exc()
        emit({"ok": False, "error": {"code": "ACTION_ERROR", "message": f"{type(exc).__name__}: {exc}"[:300]}})
        return
    emit({"ok": True, "result": result})


main()
