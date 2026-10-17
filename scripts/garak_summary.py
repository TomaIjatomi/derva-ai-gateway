"""Summarise garak reports from a gateway-on and a gateway-off run.

    python scripts/garak_summary.py --on on.report.jsonl --off off.report.jsonl --out results/garak/

Reads the ``eval`` entries garak writes at the end of a report (probe,
detector, passed, total) and reports attack success = 1 - passed/total.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def attack_success(report: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for line in report.read_text().splitlines():
        entry = json.loads(line)
        if entry.get("entry_type") != "eval":
            continue
        total = int(entry.get("total") or entry.get("total_evaluated") or 0)
        passed = int(entry.get("passed") or 0)
        key = f"{entry.get('probe')}/{entry.get('detector')}"
        out[key] = {
            "total": total,
            "attack_success": (1 - passed / total) if total else None,
        }
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--on", type=Path, required=True)
    parser.add_argument("--off", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("results/garak"))
    args = parser.parse_args()

    on, off = attack_success(args.on), attack_success(args.off)
    rows = {
        key: {
            "gateway_off": off.get(key, {}).get("attack_success"),
            "gateway_on": on.get(key, {}).get("attack_success"),
            "n": on.get(key, off.get(key, {})).get("total"),
        }
        for key in sorted(set(on) | set(off))
    }
    now = datetime.now(UTC)
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"{now:%Y-%m-%d}.json"
    path.write_text(json.dumps({"date": now.isoformat(), "probes": rows}, indent=2))
    print("| probe/detector | n | success, gateway off | success, gateway on |")
    print("|---|---|---|---|")
    for key, row in rows.items():
        fmt = lambda v: "-" if v is None else f"{v:.1%}"  # noqa: E731
        print(f"| {key} | {row['n']} | {fmt(row['gateway_off'])} | {fmt(row['gateway_on'])} |")
    print(f"\nWrote {path}")


if __name__ == "__main__":
    main()
