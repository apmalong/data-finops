"""The phase lines that a function logs (ADR 0033, Q184), read back for the handler tests."""

import json


def phase_names(logged: str, function: str) -> list[str]:
    """The phases that `function` logged, in order. Each phase line is one JSON object with the
    seconds it took and the peak memory of the process so far, in MiB."""
    names = []
    for text in logged.splitlines():
        if not text.startswith("{"):
            continue  # another log line, such as a warning
        line = json.loads(text)
        if line.get("event") != "phase" or line["function"] != function:
            continue
        assert set(line) == {"event", "function", "phase", "seconds", "peak_mib"}
        assert isinstance(line["seconds"], float) and line["seconds"] >= 0
        peak = line["peak_mib"]
        assert peak is None or (isinstance(peak, int) and peak > 0)
        names.append(line["phase"])
    return names
