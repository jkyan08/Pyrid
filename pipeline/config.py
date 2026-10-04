"""Paths, environment switches and seed loading shared by every pipeline module.

Every path is resolved once at import time against ``REPO_ROOT`` (the parent of ``pipeline/``),
after :mod:`pipeline.cache` has loaded ``.env``, so parallel checkouts can share one cache/raw
directory by pointing ``ATLAS_CACHE_DIR`` and ``ATLAS_RAW_DIR`` at shared absolute paths.

===============  ======================  ==========================================
Constant         Environment variable    Default (relative paths resolve to REPO_ROOT)
===============  ======================  ==========================================
``CACHE_DIR``    ``ATLAS_CACHE_DIR``     ``data/cache``   (hash-keyed HTTP/LLM cache)
``RAW_DIR``      ``ATLAS_RAW_DIR``       ``data/raw``     (large binary downloads, checksummed)
``GRAPH_PATH``   ``ATLAS_GRAPH_PATH``    ``data/build/graph.json``
``BUILD_DIR``    --                      ``data/build``
``STORE_PATH``   --                      ``data/build/evidence.sqlite`` (gitignored)
``SEEDS_DIR``    --                      ``pipeline/seeds``
===============  ======================  ==========================================

``USER_AGENT`` is the value every HTTP client must send: Harvard Dataverse returns 403 to the
default Python user agents on its metadata endpoint (docs/optimuskg-spike.md section 2).

Seeds (:func:`load_seeds`) hold NAMES only: HGNC symbols, disease and drug names, thresholds.
The pipeline resolves names to ids and records how (README hard rule 2).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from pipeline.cache import REPO_ROOT, resolve_cache_dir

__all__ = [
    "BUILD_DIR",
    "CACHE_DIR",
    "ENV_GRAPH_PATH",
    "ENV_RAW_DIR",
    "GRAPH_PATH",
    "RAW_DIR",
    "REPO_ROOT",
    "SEEDS_DIR",
    "STORE_PATH",
    "USER_AGENT",
    "load_seeds",
    "resolve_graph_path",
    "resolve_raw_dir",
]

ENV_RAW_DIR = "ATLAS_RAW_DIR"
ENV_GRAPH_PATH = "ATLAS_GRAPH_PATH"
DEFAULT_RAW_DIR = "data/raw"
DEFAULT_GRAPH_PATH = "data/build/graph.json"

USER_AGENT = "pyrid-atlas/0.1 (+https://github.com/jkyan08/Pyrid)"


def _resolve(raw: str, default: str) -> Path:
    """``raw`` (or ``default`` when blank), expanded and made absolute against REPO_ROOT."""
    path = Path(raw.strip() or default).expanduser()
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


def resolve_raw_dir(root: str | os.PathLike[str] | None = None) -> Path:
    """``root`` if given, else ``$ATLAS_RAW_DIR``, else ``data/raw``."""
    raw = os.environ.get(ENV_RAW_DIR, "") if root is None else os.fspath(root)
    return _resolve(raw, DEFAULT_RAW_DIR)


def resolve_graph_path(path: str | os.PathLike[str] | None = None) -> Path:
    """``path`` if given, else ``$ATLAS_GRAPH_PATH``, else ``data/build/graph.json``."""
    raw = os.environ.get(ENV_GRAPH_PATH, "") if path is None else os.fspath(path)
    return _resolve(raw, DEFAULT_GRAPH_PATH)


CACHE_DIR: Path = resolve_cache_dir()
RAW_DIR: Path = resolve_raw_dir()
BUILD_DIR: Path = REPO_ROOT / "data" / "build"
GRAPH_PATH: Path = resolve_graph_path()
STORE_PATH: Path = BUILD_DIR / "evidence.sqlite"
SEEDS_DIR: Path = REPO_ROOT / "pipeline" / "seeds"


def load_seeds(name: str) -> dict[str, Any]:
    """``pipeline/seeds/<name>.yaml`` as a dict (``name`` may carry the ``.yaml`` suffix).

    ``cluster`` -> genes, disease_terms, drug_terms, thresholds, pathway_family_label_regex.
    ``mechanisms`` -> ``{"source": ..., "mechanisms": [rows]}`` (see the file's header comment).
    """
    filename = name if name.endswith((".yaml", ".yml")) else f"{name}.yaml"
    path = SEEDS_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(f"seed file not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"seed file {path} must hold a mapping at the top level, got {type(data).__name__}")
    return data
