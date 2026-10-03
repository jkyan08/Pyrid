"""Write the JSON Schema for graph.json to docs/graph.schema.json.

    uv run python -m pipeline.schema            # writes docs/graph.schema.json
    uv run python -m pipeline.schema out.json   # writes somewhere else

The generated file is committed; regenerate it whenever pipeline/models.py changes.
"""

from __future__ import annotations

import sys
from pathlib import Path

from pipeline.models import schema_json

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "docs" / "graph.schema.json"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    out = Path(args[0]) if args else DEFAULT_OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(schema_json(), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
