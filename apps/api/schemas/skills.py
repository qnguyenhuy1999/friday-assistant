from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class CreateSkillBody(BaseModel):
    key: str = Field(min_length=1, max_length=96)
    display_name: str = Field(min_length=1, max_length=256)
    description: str = Field(default="", max_length=4000)


class CreateSkillRevisionBody(BaseModel):
    instructions: str = Field(min_length=1, max_length=32000)
    source_kind: Literal["operator", "imported"]


class SkillResponse(BaseModel):
    id: str
    key: str
    display_name: str
    description: str
    status: str
    active_revision_id: str | None
    created_at: datetime
    updated_at: datetime


class SkillRevisionResponse(BaseModel):
    id: str
    skill_id: str
    version: int
    instructions: str
    content_sha256: str
    source_kind: str
    created_at: datetime


class SkillPageResponse(BaseModel):
    items: list[SkillResponse]
    next_cursor: str | None = None
