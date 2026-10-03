"""Sample 40 extracted claims for human labelling → eval/claims_review.csv.

Seeded, so re-running picks the same claims. Refuses to overwrite a file that
already has labels in it.
Run: `python -m eval.sample`
"""
from __future__ import annotations

import csv
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLAIMS = ROOT / "data" / "build" / "claims.json"
OUT = ROOT / "eval" / "claims_review.csv"
N, SEED = 40, 20261004
FIELDS = ["claim_id", "pmid", "subject", "predicate", "object", "polarity", "variant", "study_type", "n_patients",
          "quote", "label", "notes"]


def main() -> None:
    if OUT.exists() and any(r.get("label") for r in csv.DictReader(OUT.open())):
        sys.exit(f"{OUT} already has labels; not overwriting")
    claims = json.loads(CLAIMS.read_text())["claims"]
    sample = random.Random(SEED).sample(claims, min(N, len(claims)))
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for c in sorted(sample, key=lambda c: c["id"]):
            w.writerow({"claim_id": c["id"], **{k: c.get(k, "") for k in FIELDS[1:10]}, "label": "", "notes": ""})
    print(f"wrote {len(sample)} claims to {OUT}. Label each: correct | partial | wrong", file=sys.stderr)


if __name__ == "__main__":
    main()
