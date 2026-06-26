"""Recap-rule config for theory regen v3.

Two distinct mechanisms:

  1. RENAME (kind="rename"):
     - Source PDF has callouts with one of `source_labels` (e.g. "Konnect").
     - Inside each section, the LLM finds matching content and PROMOTES it
       to a subsection at section end with the target `label`
       (heading + bullet list). Original mid-section position is removed.
     - Configurable: many source labels → one target label.

  2. REDISTRIBUTE (kind="redistribute"):
     - Chapter-end "Points to Remember" / "Summary" / "Key Takeaways"
       sections get DETECTED and their bullets extracted.
     - Each bullet is assigned to ONE section via deterministic Jaccard
       keyword overlap (no LLM call, no double-assignment).
     - The receiving section gets the bullet rendered as a "Key Takeaways"
       subsection (heading + list) at section end.
     - Orphan bullets (no good section match) get appended at chapter end.
     - The source chapter-end section is removed from regen output.

All rules are OPT-IN: a regen request must include the rule id in its
`recap_rule_ids` list. Empty list = identical to v1 behavior.

REVERSIBILITY: deleting this file + reverting the schema + regenerator
changes restores v1 behavior. The v1 prompt has no recap placeholders,
so even with this file present + recap_rule_ids set, v1 prompt ignores
them.
"""

from __future__ import annotations

from typing import Any

# ── Master config ────────────────────────────────────────────────────
RECAP_RULES: list[dict[str, Any]] = [
    {
        "id": "fun_fact",
        "label": "Fun Fact",
        "kind": "rename",
        "source_labels": ["Konnect"],
        "description": (
            "Find blocks labeled/styled as 'Konnect' inside a section, "
            "remove them from mid-section, and emit a 'Fun Fact' "
            "subsection (heading + bullet list) at the section end. "
            "If no Konnect content present, skip."
        ),
    },
    {
        "id": "food_for_thought",
        "label": "Food for Thought",
        "kind": "rename",
        # Both source labels map to the same target — the prompt's RENAME
        # directive merges their bullets under one heading.
        "source_labels": ["Info Edge", "Info Bytes"],
        "description": (
            "Find blocks labeled/styled as 'Info Edge' or 'Info Bytes' "
            "inside a section, remove them, and emit a 'Food for "
            "Thought' subsection at section end. If absent, skip."
        ),
    },
    {
        "id": "remember",
        "label": "Remember",
        "kind": "rename",
        "source_labels": ["Note"],
        "description": (
            "Find blocks labeled/styled as 'Note' inside a section, "
            "remove them, and emit a 'Remember' subsection at section "
            "end. If absent, skip."
        ),
    },
    {
        "id": "points_to_remember",
        "label": "Key Takeaways",
        "kind": "redistribute",
        # Section TITLE patterns that flag the source chapter-end section
        # to extract bullets from. Matched case-insensitively, substring.
        "source_section_patterns": [
            "Points to Remember",
            "Summary",
            "Key Takeaways",
            "Chapter Summary",
        ],
        # Heading text for the orphan fallback at chapter end
        "orphan_fallback_heading": "Key Takeaways",
        "description": (
            "Take chapter-end Points to Remember / Summary / Key Takeaways "
            "bullets, assign each to its best-matching topic via keyword "
            "overlap (no double-assignment), and embed as a 'Key Takeaways' "
            "subsection at the receiving section's end. Source chapter-end "
            "section is removed. Orphan bullets appended at chapter end."
        ),
    },
]


# ── Helpers ──────────────────────────────────────────────────────────
def get_rule(rule_id: str) -> dict[str, Any] | None:
    for r in RECAP_RULES:
        if r["id"] == rule_id:
            return r
    return None


def active_rename_rules(active_ids: list[str]) -> list[dict[str, Any]]:
    return [r for r in RECAP_RULES if r["kind"] == "rename" and r["id"] in active_ids]


def active_redistribute_rules(active_ids: list[str]) -> list[dict[str, Any]]:
    return [
        r for r in RECAP_RULES if r["kind"] == "redistribute" and r["id"] in active_ids
    ]


def render_renames_directive(active_ids: list[str]) -> str:
    """Render the {recap_renames_directive} substitution for the v3 prompt.

    Empty string when no rename rules are active.
    """
    rules = active_rename_rules(active_ids)
    if not rules:
        return ""
    lines = ["ACTIVE RENAMES (apply per section):"]
    for r in rules:
        sources = ", ".join(f'"{s}"' for s in r["source_labels"])
        lines.append(f'  - Find any block labeled/styled with {sources} → emit subsection "{r["label"]}"')
    return "\n".join(lines)


def render_keypoints_directive(assigned_bullets: list[str], label: str = "Key Takeaways") -> str:
    """Render the {recap_keypoints_directive} substitution for THIS section.

    Only populated when the worker pre-assigned bullets to this section.
    """
    if not assigned_bullets:
        return ""
    lines = [
        f"ACTIVE KEY POINTS BULLETS (assigned to this topic — emit as '{label}' subsection at section end):"
    ]
    for b in assigned_bullets:
        lines.append(f"  - {b}")
    return "\n".join(lines)


def render_active_ids(active_ids: list[str]) -> str:
    return ", ".join(active_ids) if active_ids else "none"


# ── Deterministic bullet → section matching ──────────────────────────
import re

_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "must", "shall", "can", "to", "of", "in",
    "on", "at", "by", "for", "with", "from", "as", "and", "or", "but",
    "if", "then", "than", "this", "that", "these", "those", "it", "its",
    "we", "you", "they", "i", "he", "she", "his", "her", "their", "our",
    "not", "no", "so", "such", "more", "less", "very", "only", "also",
    "into", "out", "over", "under", "where", "when", "what", "which",
    "who", "whom", "how", "why", "any", "all", "each", "every", "some",
    "one", "two", "three", "first", "second", "third",
}


def _tokens(text: str) -> set[str]:
    """Lowercase, strip punctuation, drop stopwords + 1-char tokens."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    toks = set()
    for w in text.split():
        if len(w) < 2:
            continue
        if w in _STOPWORDS:
            continue
        toks.add(w)
    return toks


def _section_text(section_title: str, blocks: list[dict], char_cap: int = 600) -> str:
    """Build a representative text for one section: title + first N chars of body."""
    parts = [section_title or ""]
    used = len(parts[0])
    for b in blocks or []:
        t = b.get("t")
        if t in ("p", "h3", "kp"):
            c = b.get("c") or ""
        elif t == "list":
            c = " ".join(b.get("items") or [])
        else:
            continue
        if used + len(c) > char_cap:
            parts.append(c[: char_cap - used])
            break
        parts.append(c)
        used += len(c)
    return " ".join(parts)


def assign_bullets_to_sections(
    bullets: list[str],
    sections: list[tuple[str, str, list[dict]]],
    min_score: float = 0.04,
) -> tuple[dict[str, list[str]], list[str]]:
    """Assign each bullet to its best-matching section.

    sections: list of (section_id, section_title, blocks).

    Returns (per_section_bullets, orphans). Each bullet appears EXACTLY ONCE
    — either in per_section_bullets[section_id] or in orphans.
    """
    section_tokens = {
        sid: _tokens(_section_text(title, blocks))
        for sid, title, blocks in sections
    }
    per_section: dict[str, list[str]] = {sid: [] for sid, _, _ in sections}
    orphans: list[str] = []
    for bullet in bullets:
        btoks = _tokens(bullet)
        if not btoks:
            orphans.append(bullet)
            continue
        best_sid: str | None = None
        best_score: float = 0.0
        for sid, stoks in section_tokens.items():
            if not stoks:
                continue
            inter = len(btoks & stoks)
            union = len(btoks | stoks)
            score = inter / union if union else 0.0
            if score > best_score:
                best_score = score
                best_sid = sid
        if best_sid is not None and best_score >= min_score:
            per_section[best_sid].append(bullet)
        else:
            orphans.append(bullet)
    return per_section, orphans


def detect_redistribute_source_sections(
    sections: list[tuple[str, str, list[dict]]],
    active_ids: list[str],
) -> tuple[list[str], list[str]]:
    """Return (source_section_ids, extracted_bullets).

    Scans every section's title against every active redistribute rule's
    source_section_patterns. Matched sections are returned so the worker
    can skip them in regen output. Their content is flattened into a
    list of bullets ready for Jaccard assignment.

    Block handling:
      • list → each item becomes its own bullet
      • kp/p → emitted as a new bullet
      • eq   → MERGED into the most recent bullet with " " separator
               (this preserves equations that visually trail a bullet
               like "Cost of Living Index =" + an eq block carrying the
               actual formula — without merging, only the bare label
               makes it into the Key Takeaways subsection)
      • If an eq appears before any bullet exists in the section, it
        becomes its own bullet.

    Multiple consecutive eq blocks (a derivation chain) get concatenated
    onto the same bullet so the related lines stay together when the
    Jaccard matcher assigns them to a topic.
    """
    rules = active_redistribute_rules(active_ids)
    if not rules:
        return [], []
    patterns: list[str] = []
    for r in rules:
        patterns.extend(p.lower() for p in r["source_section_patterns"])

    matched_ids: list[str] = []
    bullets: list[str] = []
    for sid, title, blocks in sections:
        t = (title or "").lower()
        if not any(p in t for p in patterns):
            continue
        matched_ids.append(sid)
        for b in blocks or []:
            bt = b.get("t")
            if bt == "list":
                for item in (b.get("items") or []):
                    s = str(item or "").strip()
                    if s:
                        bullets.append(s)
            elif bt in ("kp", "p"):
                c = str(b.get("c") or "").strip()
                if c:
                    bullets.append(c)
            elif bt == "eq":
                c = str(b.get("c") or "").strip()
                if not c:
                    continue
                if bullets:
                    # Glue equation to the preceding bullet so labels
                    # like "Cost of Living Index =" carry their formula.
                    bullets[-1] = f"{bullets[-1]} {c}"
                else:
                    bullets.append(c)
    return matched_ids, bullets
