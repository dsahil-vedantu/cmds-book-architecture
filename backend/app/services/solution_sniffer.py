"""Detect solution / answer / question markers leaking into theory blocks.

Theory Worker Unit 6. Closes the "solutions bleed into theory section"
class of bugs (Issue #4b in the prod-pain audit).

Architectural guarantee: ``block_normalizer`` runs only on Cat B (theory)
sections — Cat A sections are filtered out at ``extract.py:405-408``
before reaching this code path. Therefore ANY solution / answer / question
marker appearing as the start of a theory ``p`` / ``kp`` body is, by
definition, a misclassification by Gemini. We drop it deterministically.

Decisions locked (Q1-Q5):
  Q1 — Drop matched blocks entirely. They belong inside the parent
       Cat A subsection's ``eqs`` field, not in theory body.
  Q2 — Markers: Soln / Solution / Sol. / Step N / Ans / Answer / Proof
       (solutions) and Q.N / Question N (questions).
  Q3 — Case-insensitive (Gemini varies SOLN vs Soln vs soln).
  Q4 — Marker MUST be at the start of content (avoids false positives
       like "the solution is" mid-sentence, or "Quetzal" matching "Q").
  Q5 — >2 drops in one section → QC fail → retry with corrective prompt.
       (Threshold lives in ``theory_extractor._simple_qc``, not here.)

Outputs:
  detect_solution_bleed(content) -> bool
  detect_question_bleed(content) -> bool

The block_normalizer drops the block AND increments the relevant
NormalizationResult counter (``dropped_solution_bleed`` /
``dropped_question_bleed``) so logs and QC can see what happened.
"""

from __future__ import annotations

import re


# Solution / answer markers — STRICT set (always applied).
# Pattern requires marker at the very start of content followed by a
# separator (space, colon, period, dash, em-dash, paren). This avoids
# false positives like:
#   "the solution is x = 5"  — "solution" not at start
#   "Quetzal bird"           — Q not followed by digit
#   "Solving equations"      — "Solving" not exact match
_SOLUTION_MARKERS = re.compile(
    r"""
    ^
    (?P<marker>
        Soln\.?                       # Soln  /  Soln.
        | Solution                    # Solution
        | Sol\.                       # Sol.  (period required to avoid name "Sol")
        | Ans \.?:?                   # Ans, Ans., Ans:
        | Answer :?                   # Answer, Answer:
        | Proof \b                    # Proof
    )
    (?:
        \s | : | \. | \- | — | $
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


# "Step N:" pattern — split out because it's context-sensitive.
# In ACTIVITY / ICT / PRACTICAL sections, "Step 1: Open the browser..." is
# legitimate theory body (activity instructions per prompt §4.4). In other
# sections, "Step 1: The equation is x² - 5x + 6 = 0..." is a solution bleed.
# The detector receives section_title and skips this pattern in activity contexts.
_STEP_MARKER = re.compile(
    r"""
    ^
    Step \s+ \d+                       # Step 1, Step 2, ...
    (?:
        \s | : | \. | \- | — | $
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


_ACTIVITY_TITLE = re.compile(
    r"""
    (?:
        \b activity                    # "Activity 1", "Activity Steps"
        | \b ict                       # "ICT Corner"
        | \b practical                 # "Practical 5"
        | \b lab \b                    # "Lab Activity"
        | \b construct \w*             # "Construction", "Constructions"
        | \b how \s+ to                # "How to use the calculator"
        | \b experiment                # "Experiment 2"
        | \b procedure                 # "Procedure"
        | \b demo \w*                  # "Demo", "Demonstration"
        | let .? s \s+ solve           # "Let's Solve", "Lets Solve"
        | let \s+ us \s+ solve         # "Let us solve"
        | \b try \s+ it \b             # "Try It"
        | \b try \s+ this              # "Try This"
        | \b worked \s+ out            # "Worked Out Example" / etc.
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


# Question stem markers — Cat A bleed.
_QUESTION_MARKERS = re.compile(
    r"""
    ^                                 # start of content
    (?P<marker>
        Q \.? \s* \d+                 # Q1, Q.1, Q. 1
        | Question \s+ \d+            # Question 1, Question 2
    )
    (?:                               # separator
        \s | : | \. | \- | \) | — | $
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def is_activity_context(section_title: str | None) -> bool:
    """True iff this section's title indicates Activity / ICT / Practical
    content where "Step N:" prose is legitimate theory body (per prompt §4.4).
    """
    if not section_title or not isinstance(section_title, str):
        return False
    return _ACTIVITY_TITLE.search(section_title) is not None


def detect_solution_bleed(content: str, section_title: str | None = None) -> bool:
    """True iff content STARTS with a solution / answer / step marker.

    Catches Gemini misclassifying solution prose ("Soln. Let x = ...") as a
    theory paragraph. Case-insensitive. Strict on position (marker must be at
    the start, not mid-prose).

    Context-aware: in Activity / ICT / Practical / Construction / Let's-Solve
    sections, ALL solution markers (Soln, Solution, Step N, Proof, etc.) are
    legitimate theory body per prompt §4.4 — the section's PURPOSE is to walk
    through an activity that includes solution prose. Sniffer skips entirely.

    In any other (pure-theory) section, solution markers at start = misplaced
    Cat A content. Drop.
    """
    if not isinstance(content, str):
        return False
    s = content.lstrip()
    if not s:
        return False
    # Context-aware: activity sections legitimately contain solution prose.
    if is_activity_context(section_title):
        return False
    # Pure theory section — solution markers are bleed.
    if _SOLUTION_MARKERS.match(s) is not None:
        return True
    if _STEP_MARKER.match(s) is not None:
        return True
    return False


def detect_question_bleed(content: str) -> bool:
    """True iff content STARTS with a question-stem marker (Q.N, Question N).

    Catches Gemini emitting question stems as theory paragraphs. Architectural
    guarantee: this function only runs on Cat B (theory) sections via
    block_normalizer, so any question marker = bleed.
    """
    if not isinstance(content, str):
        return False
    s = content.lstrip()
    if not s:
        return False
    return _QUESTION_MARKERS.match(s) is not None
