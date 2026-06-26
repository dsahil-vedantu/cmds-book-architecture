"""QA agent — deterministic fidelity scoring + LLM-driven completeness tests.

Pillar B (fidelity) runs first with pymupdf as ground truth — no LLM needed.
Pillar A (completeness via TestGen) is layered on top in a later pass.
"""
