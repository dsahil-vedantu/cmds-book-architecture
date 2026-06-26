"""QC result schemas — local (7 checks) and LLM (P6 auditor)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class QCResult(BaseModel):
    """Output of local QC (7 checks). `pass_` maps to ``pass`` in JSON."""

    pass_: bool = Field(alias="pass")
    score: float = 1.0  # 0.0 .. 1.0
    word_ratio: float = 0.0
    failures: list[str] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)
    attempts: int = 1
    method: Literal["local", "llm"] = "local"
    verified_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = {"populate_by_name": True}

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, mode="json")


class ValueDrift(BaseModel):
    source: str
    extracted: str


class LLMQCIssues(BaseModel):
    missing_content: list[str] = Field(default_factory=list)
    value_drift: list[ValueDrift] = Field(default_factory=list)
    truncated: list[str] = Field(default_factory=list)
    added_content: list[str] = Field(default_factory=list)


class LLMQCResult(BaseModel):
    """Output of P6 QC Auditor."""

    pass_: bool = Field(alias="pass", default=False)
    severity: Literal["high", "medium", "low"] = "medium"
    issues: LLMQCIssues = Field(default_factory=LLMQCIssues)
    fix_prompt: str = ""
    verdict: str = ""
    verified_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = {"populate_by_name": True}

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, mode="json")
