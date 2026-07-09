#!/usr/bin/env python3
"""Gate an eval report against tunable precision/recall/FPR thresholds.

Usage:
    python scripts/eval-threshold-check.py eval-report.json
    cat eval-report.json | python scripts/eval-threshold-check.py

Env:
    EVAL_PRECISION_MIN  default 0.5
    EVAL_RECALL_MIN     default 0.5
    EVAL_FPR_MAX        default 0.5
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import cast


def load_report(path: str | None) -> dict[str, object]:
    if path is None or path == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(path).read_text(encoding="utf-8")
    return cast(dict[str, object], json.loads(raw))


def _to_float(value: object) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    raise TypeError(f"expected numeric metric, got {type(value).__name__}")


def get_metric(report: dict[str, object], name: str) -> float:
    """Fetch a metric from aggregate or top-level fields."""
    for key in ("aggregate", "summary", "overall"):
        section = report.get(key)
        if isinstance(section, Mapping) and name in section:
            return _to_float(section[name])
    if name in report:
        return _to_float(report[name])
    raise KeyError(name)


def main(argv: list[str]) -> int:
    path = argv[1] if len(argv) > 1 else None
    report = load_report(path)

    precision_min = float(os.environ.get("EVAL_PRECISION_MIN", "0.5"))
    recall_min = float(os.environ.get("EVAL_RECALL_MIN", "0.5"))
    fpr_max = float(os.environ.get("EVAL_FPR_MAX", "0.5"))

    precision = get_metric(report, "precision")
    recall = get_metric(report, "recall")
    fpr = get_metric(report, "false_positive_rate")

    precision_ok = precision >= precision_min
    recall_ok = recall >= recall_min
    fpr_ok = fpr <= fpr_max

    print(f"precision:           {precision:.4f} (min {precision_min:.4f}) {'PASS' if precision_ok else 'FAIL'}")
    print(f"recall:              {recall:.4f} (min {recall_min:.4f}) {'PASS' if recall_ok else 'FAIL'}")
    print(f"false_positive_rate: {fpr:.4f} (max {fpr_max:.4f}) {'PASS' if fpr_ok else 'FAIL'}")

    if precision_ok and recall_ok and fpr_ok:
        print("eval threshold check: PASS")
        return 0

    print("eval threshold check: FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
