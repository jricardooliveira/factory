"""Pydantic models for structured agent outputs."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TaskDef(BaseModel):
    id: str
    title: str
    purpose: str
    scope: list[str] = Field(default_factory=list)
    completion_evidence: str = ""
    depends_on: list[str] = Field(
        default_factory=list,
        description="Task ids that must be implemented before this one",
    )


class SpecOutput(BaseModel):
    story_id: str = "US-0001"
    title: str
    type: str = "feature"
    problem: str
    why: str
    acceptance_criteria: list[str] = Field(default_factory=list)
    non_goals: list[str] = Field(default_factory=list)
    tasks: list[TaskDef] = Field(default_factory=list)
    verdict: str = "pass"
    questions: list[str] = Field(default_factory=list)


class ArchitectOutput(BaseModel):
    verdict: str = "pass"
    architecture_notes: str = ""
    modules_affected: list[str] = Field(default_factory=list)
    data_model: str = ""
    api_design: str = "none"
    implementation_constraints: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    db_impact: str = "no"
    api_impact: str = "no"
    migration_needed: str = "no"
    # New fields for safeguards
    breaking_changes: list[str] = Field(default_factory=list)
    external_dependencies: list[str] = Field(default_factory=list)
    sensitivity: list[str] = Field(
        default_factory=list,
        description="Tags: 'legal', 'compliance', 'security', 'financial', 'pii'",
    )


class CodeBlock(BaseModel):
    path: str
    content: str
    action: str = "create"


class TestCoverage(BaseModel):
    happy_path: bool = False
    edge_case: bool = False
    error_handling: bool = False


class TesterOutput(BaseModel):
    """Post-implementation quality gate with separate sub-verdicts (spec §5.7)."""

    overall: str = "pass"  # pass | warn | fail
    qa_verdict: str = "pass"  # pass | warn | fail
    ac_coverage: list[str] = Field(
        default_factory=list,
        description="Acceptance criteria judged covered by the tests",
    )
    missing_coverage: list[str] = Field(default_factory=list)
    security_verdict: str = "pass"  # pass | warn | fail
    highest_severity: str = "none"  # none | low | medium | high | critical
    security_findings: list[str] = Field(default_factory=list)
    performance_verdict: str = "pass"  # pass | warn | fail
    performance_findings: list[str] = Field(default_factory=list)
    summary: str = ""


class CoderOutput(BaseModel):
    verdict: str = "complete"
    files_created: list[str] = Field(default_factory=list)
    files_modified: list[str] = Field(default_factory=list)
    tests_added: list[str] = Field(default_factory=list)
    implementation_summary: str = ""
    code_blocks: list[CodeBlock] = Field(default_factory=list)
    test_coverage: TestCoverage = Field(default_factory=TestCoverage)
    assumptions: list[str] = Field(default_factory=list)
    follow_ups: list[str] = Field(default_factory=list)
    design_feedback: str = Field(
        default="",
        description=(
            "Set ONLY when the agreed architecture cannot be implemented as designed "
            "(infeasible, contradicts existing code, missing a decision). Explain what "
            "is wrong; the design will be revised. Leave empty during normal work."
        ),
    )
