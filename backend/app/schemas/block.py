"""Canonical Block union — the content model from the master brief.

INVARIANT block types (eq, def, fig, example) are never sent to Claude during
regeneration; they are split out, copied verbatim, and merged back in order.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class ParagraphBlock(BaseModel):
    t: Literal["p"] = "p"
    c: str


class HeadingBlock(BaseModel):
    t: Literal["h3"] = "h3"
    c: str


class EquationBlock(BaseModel):
    t: Literal["eq"] = "eq"
    c: str


class DefinitionBlock(BaseModel):
    t: Literal["def"] = "def"
    term: str
    c: str


class KeyPointBlock(BaseModel):
    t: Literal["kp"] = "kp"
    c: str


class FigureBlock(BaseModel):
    t: Literal["fig"] = "fig"
    c: str
    label: str = ""


class ExampleRefBlock(BaseModel):
    """Placeholder for a worked example printed in the section.

    The body is intentionally not transcribed — it is captured by the question
    extraction pipeline. We only record the printed identifier so the reader
    can render a position marker.
    """

    t: Literal["example_ref"] = "example_ref"
    label: str
    number: str = ""


class ExerciseRefBlock(BaseModel):
    t: Literal["exercise_ref"] = "exercise_ref"
    label: str
    number: str = ""


class QuestionRefBlock(BaseModel):
    t: Literal["question_ref"] = "question_ref"
    label: str
    number: str = ""


class ListBlock(BaseModel):
    t: Literal["list"] = "list"
    items: list[str]


class ExampleBlock(BaseModel):
    t: Literal["example"] = "example"
    label: str
    prob: str
    eqs: list[str] = Field(default_factory=list)


Block = Annotated[
    Union[
        ParagraphBlock,
        HeadingBlock,
        EquationBlock,
        DefinitionBlock,
        KeyPointBlock,
        FigureBlock,
        ListBlock,
        ExampleBlock,
        ExampleRefBlock,
        ExerciseRefBlock,
        QuestionRefBlock,
    ],
    Field(discriminator="t"),
]

INVARIANT_TYPES: set[str] = {
    "eq",
    "def",
    "fig",
    "example",
    "table",
    "example_ref",
    "exercise_ref",
    "question_ref",
}
FREE_TYPES: set[str] = {"p", "h3", "kp", "list"}
