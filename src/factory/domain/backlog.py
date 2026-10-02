"""The backlog-agent's output contract: an ordered list of small stories.

The agent only PROPOSES; nothing is written until the operator approves, and
stories already started are never part of a proposal (they are fixed).
"""

from __future__ import annotations

from pydantic import BaseModel


class BacklogStory(BaseModel):
    title: str
    request: str  # verbatim what the spec-agent will receive when the story starts
    rationale: str = ""


class BacklogOutput(BaseModel):
    stories: list[BacklogStory]
