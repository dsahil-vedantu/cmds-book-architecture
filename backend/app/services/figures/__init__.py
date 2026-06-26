"""Figures pipeline — wraps the standalone scripts as in-process services.

Four stages mirror the upstream pipeline (see Figure Handling.docx):
  1. extractor   — PDF -> Gemini coordinate oracle -> figure bboxes + crops
  2. regenerator — original crop -> Gemini image-gen -> regenerated PNG
  3. watermark   — optional cleanup pass to strip residual watermarks
  4. overlay     — OCR + fuzzy match + PIL TrueType overlay for crisp labels

Plus two NEW helpers we add to integrate cleanly into CMDS:
  5. linker      — maps Gemini figure entries to our section_ref + question_id
                   using book.schema + extracted theory blocks / questions
                   (CPU-only, 0 Gemini calls)
  6. cache       — content-hash dedup so re-running a regen for the same
                   (figure, style, params) reuses cached bytes
"""
