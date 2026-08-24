from __future__ import annotations

import csv
import logging
from pathlib import Path

log = logging.getLogger("zynk.drivers")

# CSV columns: series,full_cli,license_available,license_needed_for_cli,license_note
_SERIES_INFO: dict[str, dict] = {}


def _load() -> None:
    if _SERIES_INFO:
        return
    path = Path(__file__).resolve().parent / "switch_cli_support.csv"
    try:
        with path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                series = row["series"].strip()
                if series:
                    _SERIES_INFO[series] = {
                        "full_cli": row["full_cli"].strip().lower() == "yes",
                        "license_available": row["license_available"].strip().lower() == "yes",
                        "license_needed_for_cli": row["license_needed_for_cli"].strip().lower()
                        == "yes",
                        "note": row.get("note", "").strip(),
                    }
    except OSError as err:
        log.warning("switch CLI support list not found (%s); using generic message", err)


def series_from_model(model: str) -> str | None:
    """Extract the series prefix from a model name, e.g. 'XS1930-12HP' -> 'XS1930'."""
    _load()
    if not model:
        return None
    model = model.strip().upper()
    # longest match wins (XMG1930 before XS1930-style shorter prefixes)
    for series in sorted(_SERIES_INFO, key=len, reverse=True):
        if model.startswith(series):
            return series
    return None


def license_message(model: str) -> str | None:
    """CLI restriction explanation for a switch model, or None if unrestricted."""
    _load()
    series = series_from_model(model)
    if series is None:
        return None
    info = _SERIES_INFO[series]
    if info["full_cli"]:
        return None
    # restricted CLI
    if info["license_needed_for_cli"]:
        return (
            f"The {series} series ships with a restricted basic CLI — the model "
            f"{model} cannot run configuration commands (incl. copy tftp config) "
            f"until the CLI license from Zyxel (myzyxel.com) is activated. "
            f"Config pull (show running-config) works without the license; "
            f"config restore does not."
        )
    return (
        f"The {series} series ({model}) has no full CLI and no license can "
        f"unlock one — CLI configuration commands (incl. copy tftp config) are "
        f"not available on this model. Config pull (show running-config) works; "
        f"config restore is not possible via CLI."
    )
