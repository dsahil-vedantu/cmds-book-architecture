"""Golden master tests for local QC — cover all 7 checks.

Cases ported from 03_QC_LOGIC.md. Any behavioural change here must be
reviewed against the working prototype.
"""

from __future__ import annotations

import pytest

from app.services.qc.local import local_qc


def _paragraph(text: str) -> dict:
    return {"type": "body", "content": text}


def test_perfect_match_passes() -> None:
    src = (
        "Einstein explained the photoelectric effect in 1905. "
        "The work function for sodium is 2.3 eV."
    )
    ext = [_paragraph(src)]
    qc = local_qc(src, ext)
    assert qc.pass_
    assert qc.score == pytest.approx(1.0)


def test_missing_number_fails() -> None:
    src = "The work function for sodium is 2.3 eV and the cutoff is 1.6 units."
    ext = [_paragraph("The work function for sodium is approximately 2 eV and cutoff is 1.6 units.")]
    qc = local_qc(src, ext)
    assert not qc.pass_
    assert any("Numbers missing" in f for f in qc.failures)


def test_hallucinated_sentence_flagged() -> None:
    src = (
        "Einstein explained the photoelectric effect in 1905. "
        "The work function for sodium is 2.3 eV."
    )
    ext = [
        {
            "type": "body",
            "content": (
                "Einstein explained the photoelectric effect in 1905. "
                "The work function for sodium is 2.3 eV. "
                "This was a revolutionary discovery in quantum physics that changed everything about light."
            ),
        }
    ]
    qc = local_qc(src, ext)
    assert not qc.pass_
    assert any("hallucination" in f.lower() for f in qc.failures)


def test_truncated_extraction_fails() -> None:
    src = (
        "Einstein explained the photoelectric effect in 1905 after years of research. "
        "The work function for sodium is 2.3 eV at room temperature."
    )
    ext = [
        {
            "type": "body",
            "content": "Einstein explained the photoelectric effect in 1905 after years of research.",
        }
    ]
    qc = local_qc(src, ext)
    assert not qc.pass_
    # Either the last-sentence or a numbers-missing check fires
    assert any(
        ("truncated" in f.lower()) or ("Numbers missing" in f) for f in qc.failures
    )


def test_empty_extraction_fails() -> None:
    qc = local_qc("Some source text here.", [])
    assert not qc.pass_
    assert any("Empty extraction" in f for f in qc.failures)


def test_empty_source_fails() -> None:
    qc = local_qc("", [_paragraph("Anything")])
    assert not qc.pass_
    assert any("No source text" in f for f in qc.failures)


def test_word_ratio_below_70_percent() -> None:
    src = " ".join(["word"] * 100)
    ext = [_paragraph(" ".join(["word"] * 50))]  # 50% ratio
    qc = local_qc(src, ext)
    assert not qc.pass_
    assert any("Word count" in f for f in qc.failures)


def test_qc_to_dict_uses_pass_alias() -> None:
    qc = local_qc("Hello world from a source text.", [_paragraph("Hello world from a source text.")])
    d = qc.to_dict()
    assert "pass" in d
    assert "pass_" not in d
