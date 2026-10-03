"""Extraction precision from human labels in eval/claims_review.csv.

Labels: correct | partial | wrong. Precision counts `correct` only; a lenient
figure counts `partial` as correct too. Both are reported with n.
Run: `python -m eval.precision`
"""
from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path

CSV = Path(__file__).resolve().parent / "claims_review.csv"
VALID = {"correct", "partial", "wrong"}


def precision(rows: list[dict]) -> dict:
    labels = Counter(r["label"].strip().lower() for r in rows if r.get("label", "").strip())
    bad = set(labels) - VALID
    if bad:
        raise ValueError(f"unknown labels: {sorted(bad)}")
    n = sum(labels.values())
    return {
        "n_labelled": n,
        "n_total": len(rows),
        "counts": dict(labels),
        "strict": round(labels["correct"] / n, 3) if n else None,
        "lenient": round((labels["correct"] + labels["partial"]) / n, 3) if n else None,
    }


def main() -> None:
    r = precision(list(csv.DictReader(CSV.open())))
    print(f"labelled {r['n_labelled']}/{r['n_total']}: strict precision {r['strict']}, "
          f"lenient {r['lenient']} {r['counts']}")
    if r["n_labelled"] < r["n_total"]:
        print("warning: not every sampled claim is labelled", file=sys.stderr)


if __name__ == "__main__":
    main()
