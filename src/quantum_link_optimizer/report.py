"""Self-contained browser report: no server, network requests or CDN assets."""

import json
from importlib.resources import files
from pathlib import Path


def render_report(report: dict, output: Path) -> Path:
    payload = json.dumps(report, ensure_ascii=False, allow_nan=False)
    # Keep telemetry strings from terminating the non-executable JSON element.
    payload = payload.replace("<", "\\u003c").replace("&", "\\u0026")
    template = files("quantum_link_optimizer").joinpath("templates/report.html")
    document = template.read_text(encoding="utf-8").replace("__REPORT_DATA__", payload)
    output.mkdir(parents=True, exist_ok=True)
    path = output / "index.html"
    path.write_text(document, encoding="utf-8")
    return path
