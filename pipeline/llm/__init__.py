"""The evidence pipeline's LLM functions. The API seam looks these up as attributes of this package
(`importlib.import_module("pipeline.llm").explain`), so they are re-exported here."""
from pipeline.llm.draft import draft
from pipeline.llm.explain import explain

__all__ = ["draft", "explain"]
