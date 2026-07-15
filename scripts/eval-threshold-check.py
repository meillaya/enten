#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
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
import math
import os
import sys
from collections.abc import Mapping
from pathlib import Path


class InvalidEvalReportError(Exception):
    pass


def load_report(path: str | None) -> Mapping[str, object]:
    if path is None or path == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(path).read_text(encoding="utf-8")
    report = json.loads(raw)
    if not isinstance(report, Mapping):
        raise InvalidEvalReportError("report must be a JSON object")
    return report


def _to_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidEvalReportError(f"expected numeric metric, got {type(value).__name__}")
    metric = float(value)
    if not math.isfinite(metric) or not 0.0 <= metric <= 1.0:
        raise InvalidEvalReportError(f"metric must be finite and between 0 and 1: {metric}")
    return metric


def get_metric(report: Mapping[str, object], name: str) -> float:
    section = report.get("aggregate")
    if not isinstance(section, Mapping):
        raise InvalidEvalReportError("report.aggregate must be a JSON object")
    if name not in section:
        raise InvalidEvalReportError(f"report.aggregate.{name} is required")
    return _to_float(section[name])


def main(argv: list[str]) -> int:
    path = argv[1] if len(argv) > 1 else None
    try:
        report = load_report(path)
        precision_min = _to_float(float(os.environ.get("EVAL_PRECISION_MIN", "0.5")))
        recall_min = _to_float(float(os.environ.get("EVAL_RECALL_MIN", "0.5")))
        fpr_max = _to_float(float(os.environ.get("EVAL_FPR_MAX", "0.5")))
        precision = get_metric(report, "precision")
        recall = get_metric(report, "recall")
        fpr = get_metric(report, "false_positive_rate")
    except (InvalidEvalReportError, json.JSONDecodeError, OSError, ValueError) as exc:
        print(f"eval threshold check: INVALID: {exc}", file=sys.stderr)
        return 2

    precision_ok = precision >= precision_min
    recall_ok = recall >= recall_min
    fpr_ok = fpr <= fpr_max

    print(
        f"precision:           {precision:.4f} (min {precision_min:.4f}) "
        f"{'PASS' if precision_ok else 'FAIL'}"
    )
    print(
        f"recall:              {recall:.4f} (min {recall_min:.4f}) "
        f"{'PASS' if recall_ok else 'FAIL'}"
    )
    print(f"false_positive_rate: {fpr:.4f} (max {fpr_max:.4f}) {'PASS' if fpr_ok else 'FAIL'}")

    if precision_ok and recall_ok and fpr_ok:
        print("eval threshold check: PASS")
        return 0

    print("eval threshold check: FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
