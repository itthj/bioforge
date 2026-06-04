"""Approval-gate tests: the requirement check and the agent loop pause/resume flow."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from bioforge.agent import (
    Plan,
    PlanStep,
    requires_approval,
    resume_agent,
    run_agent,
)
from bioforge.constants import DEFAULT_PROJECT_ID
from bioforge.tools.registry import REGISTRY
from bioforge.tools.sequence import blast as blast_module

# --- requires_approval unit tests ----------------------------------------------------


def test_requires_approval_false_for_cheap_tools_only() -> None:
    plan = Plan(
        is_trivial=False,
        summary="cheap pipeline",
        steps=[
            PlanStep(
                idx=0,
                description="rev comp",
                expected_tool="reverse_complement",
                rationale="x",
            ),
            PlanStep(
                idx=1,
                description="gc",
                expected_tool="gc_content",
                rationale="y",
            ),
        ],
    )
    requirement = requires_approval(plan, REGISTRY)
    assert requirement.required is False
    assert requirement.reasons == []


def test_requires_approval_true_when_blast_in_plan() -> None:
    plan = Plan(
        is_trivial=False,
        summary="includes blast",
        steps=[
            PlanStep(
                idx=0,
                description="search",
                expected_tool="blast",
                rationale="find homologs",
            ),
        ],
    )
    requirement = requires_approval(plan, REGISTRY)
    assert requirement.required is True
    assert any("expensive" in r for r in requirement.reasons)
    assert any("blast" in r for r in requirement.reasons)


def test_requires_approval_handles_unknown_tool_silently() -> None:
    """Unknown tools are surfaced as errors by the executor, not as approval prompts."""
    plan = Plan(
        is_trivial=False,
        summary="unknown tool",
        steps=[
            PlanStep(idx=0, description="?", expected_tool="not_a_tool", rationale="?"),
        ],
    )
    requirement = requires_approval(plan, REGISTRY)
    assert requirement.required is False


def test_requires_approval_false_for_none_plan() -> None:
    assert requires_approval(None, REGISTRY).required is False


def test_requires_approval_false_for_empty_plan() -> None:
    plan = Plan(is_trivial=True, summary="empty", steps=[])
    assert requires_approval(plan, REGISTRY).required is False


# --- Pause-and-resume integration through run_agent / resume_agent --------------------


@pytest.fixture
def patch_blast(monkeypatch):
    """Stub `_run_ncbi_blast` so resume_agent can actually execute the BLAST step."""

    holder: dict = {"response": None, "calls": []}

    async def _fake(*, program, database, sequence, expect, hitlist_size, task=None):
        holder["calls"].append(dict(program=program, database=database, sequence=sequence, task=task))
        return holder["response"]

    monkeypatch.setattr(blast_module, "_run_ncbi_blast", _fake)

    def setter(response):
        holder["response"] = response

    setter.calls = holder["calls"]
    return setter


def _fake_blast_record_with_one_hit() -> tuple:
    """Returns the (record, rid) tuple shape that `_run_ncbi_blast` actually emits."""
    hsp = SimpleNamespace(
        expect=1e-80,
        bits=400.0,
        identities=98,
        align_length=100,
        query_start=1,
        query_end=100,
        sbjct_start=1001,
        sbjct_end=1100,
    )
    alignment = SimpleNamespace(
        accession="NM_007294.4",
        hit_def="Homo sapiens BRCA1 mRNA [Homo sapiens]",
        hsps=[hsp],
    )
    return (SimpleNamespace(alignments=[alignment]), "RID-TEST")


async def test_run_agent_pauses_for_approval_on_blast_plan(
    fake_llm_factory, make_submit_plan_response, multi_step_plan
) -> None:
    plan_dict = multi_step_plan(
        [("blast", "Search for homologs in NCBI nt.")],
        summary="BLAST the input sequence.",
    )
    llm = fake_llm_factory([make_submit_plan_response(plan_dict)])

    result = await run_agent(
        "find homologs of ATGCATGCATGCATGCATGC",
        project_id=DEFAULT_PROJECT_ID,
        llm=llm,
    )

    assert result.status == "pending_approval"
    assert result.pending_plan is not None
    assert result.pending_plan["steps"][0]["expected_tool"] == "blast"
    assert any("expensive" in r for r in result.approval_reasons)

    step_types = [s.type for s in result.steps]
    assert step_types == ["plan", "approval_requested"]
    # Only the planner ran; executor never called.
    assert len(llm.calls) == 1


async def test_skip_approval_gate_runs_blast_directly(
    fake_llm_factory,
    make_submit_plan_response,
    make_tool_use_response,
    make_text_response,
    make_submit_verdict_response,
    multi_step_plan,
    passing_verdict,
    patch_blast,
) -> None:
    """The CLI / test harness path: skip_approval_gate=True bypasses the pause."""
    patch_blast(_fake_blast_record_with_one_hit())
    llm = fake_llm_factory(
        [
            make_submit_plan_response(multi_step_plan([("blast", "Search for homologs.")])),
            make_tool_use_response(
                "blast",
                {"sequence": "ATGCATGCATGCATGCATGC", "program": "blastn", "database": "nt"},
            ),
            make_text_response("Top hit: NM_007294.4 (BRCA1) at 98% identity, e-value 1e-80. Search via NCBI BLAST."),
            make_submit_verdict_response(passing_verdict()),
        ]
    )
    result = await run_agent(
        "find homologs",
        project_id=DEFAULT_PROJECT_ID,
        llm=llm,
        skip_approval_gate=True,
    )
    assert result.status == "completed"
    assert any(s.type == "tool_call" and s.tool_name == "blast" for s in result.steps)
    # And the network was hit (well, the stub).
    assert len(patch_blast.calls) == 1


async def test_resume_agent_executes_pending_plan(
    fake_llm_factory,
    make_tool_use_response,
    make_text_response,
    make_submit_verdict_response,
    passing_verdict,
    patch_blast,
) -> None:
    """Simulates the API flow: planner already ran (off-test), the persisted plan is
    passed to resume_agent which runs the executor + critic."""
    patch_blast(_fake_blast_record_with_one_hit())

    plan = Plan(
        is_trivial=False,
        summary="Search for homologs.",
        steps=[
            PlanStep(
                idx=0,
                description="BLAST the query.",
                expected_tool="blast",
                rationale="Find homologs.",
            )
        ],
    )

    llm = fake_llm_factory(
        [
            make_tool_use_response(
                "blast",
                {"sequence": "ATGCATGCATGCATGCATGC"},
            ),
            make_text_response("BRCA1 mRNA (NM_007294.4) at 98% identity. Source: NCBI BLAST."),
            make_submit_verdict_response(passing_verdict()),
        ]
    )

    result = await resume_agent(
        goal="find homologs of ATGCATGCATGCATGCATGC",
        plan=plan,
        project_id=DEFAULT_PROJECT_ID,
        step_idx_start=2,  # plan + approval_requested already happened
        llm=llm,
    )

    assert result.status == "completed"
    tool_steps = [s for s in result.steps if s.type == "tool_call"]
    assert len(tool_steps) == 1
    assert tool_steps[0].tool_name == "blast"
    assert tool_steps[0].tool_output["hits"][0]["accession"] == "NM_007294.4"


async def test_run_agent_does_not_pause_for_non_expensive_plan(
    fake_llm_factory,
    make_submit_plan_response,
    make_tool_use_response,
    make_text_response,
    make_submit_verdict_response,
    multi_step_plan,
    passing_verdict,
) -> None:
    """A plan composed only of cheap tools must NOT trigger the approval gate."""
    llm = fake_llm_factory(
        [
            make_submit_plan_response(
                multi_step_plan(
                    [
                        ("reverse_complement", "Reverse complement."),
                        ("gc_content", "GC content of the reverse complement."),
                    ]
                )
            ),
            make_tool_use_response("reverse_complement", {"sequence": "ATGCATGC"}),
            make_tool_use_response("gc_content", {"sequence": "GCATGCAT"}),
            make_text_response("GC of reverse complement: 50%. Tools: rc v1.0.0, gc v1.0.0."),
            make_submit_verdict_response(passing_verdict()),
        ]
    )
    result = await run_agent("GC of rev comp of ATGCATGC", project_id=DEFAULT_PROJECT_ID, llm=llm)
    assert result.status == "completed"
    assert all(s.type != "approval_requested" for s in result.steps)


# --- Review autonomy (user-set "approve every plan") ----------------------------------


def test_requires_approval_forced_in_review_mode() -> None:
    """Review autonomy pauses an all-cheap plan that auto mode would let run."""
    plan = Plan(
        is_trivial=False,
        summary="cheap pipeline",
        steps=[
            PlanStep(idx=0, description="rev comp", expected_tool="reverse_complement", rationale="x"),
            PlanStep(idx=1, description="gc", expected_tool="gc_content", rationale="y"),
        ],
    )
    assert requires_approval(plan, REGISTRY).required is False
    review = requires_approval(plan, REGISTRY, force_review=True)
    assert review.required is True
    assert any("Review mode" in r for r in review.reasons)


def test_force_review_still_false_for_empty_plan() -> None:
    """Nothing to run -> nothing to approve, even in review mode."""
    plan = Plan(is_trivial=True, summary="empty", steps=[])
    assert requires_approval(plan, REGISTRY, force_review=True).required is False


# --- Edit-the-plan-before-approving: _resolve_resume_plan (P2b) ------------------------

_ORIGINAL_PLAN_DICT = {
    "is_trivial": False,
    "summary": "original plan",
    "steps": [{"idx": 0, "description": "BLAST the query.", "expected_tool": "blast", "rationale": "r"}],
}


def _trace_with_plan(plan_dict: dict | None) -> SimpleNamespace:
    """A stand-in Trace carrying just the field _resolve_resume_plan reads."""
    return SimpleNamespace(awaiting_approval_plan=plan_dict)


def test_resolve_resume_plan_uses_original_when_no_edit() -> None:
    from bioforge.api.agent import AgentApproveRequest, _resolve_resume_plan

    plan, error = _resolve_resume_plan(
        _trace_with_plan(_ORIGINAL_PLAN_DICT), AgentApproveRequest(approved=True)
    )
    assert error is None
    assert plan is not None and plan.summary == "original plan"


def test_resolve_resume_plan_prefers_the_edited_plan() -> None:
    from bioforge.api.agent import AgentApproveRequest, _resolve_resume_plan

    edited = {
        "is_trivial": False,
        "summary": "edited plan",
        "steps": [{"idx": 0, "description": "EDITED step.", "expected_tool": "blast", "rationale": "r"}],
    }
    plan, error = _resolve_resume_plan(
        _trace_with_plan(_ORIGINAL_PLAN_DICT), AgentApproveRequest(approved=True, plan=edited)
    )
    assert error is None
    assert plan is not None
    assert plan.summary == "edited plan"
    assert plan.steps[0].description == "EDITED step."


def test_resolve_resume_plan_rejects_invalid_edited_plan_as_400() -> None:
    """A malformed EDITED plan is a client error (400), never silently ignored."""
    from bioforge.api.agent import AgentApproveRequest, _resolve_resume_plan

    bad = {"is_trivial": False, "summary": "x", "steps": "not-a-list"}
    plan, error = _resolve_resume_plan(
        _trace_with_plan(_ORIGINAL_PLAN_DICT), AgentApproveRequest(approved=True, plan=bad)
    )
    assert plan is None
    assert error is not None and error[0] == 400


def test_resolve_resume_plan_missing_persisted_plan_is_500() -> None:
    from bioforge.api.agent import AgentApproveRequest, _resolve_resume_plan

    plan, error = _resolve_resume_plan(_trace_with_plan(None), AgentApproveRequest(approved=True))
    assert plan is None
    assert error is not None and error[0] == 500


async def test_run_agent_review_mode_pauses_on_cheap_plan(
    fake_llm_factory, make_submit_plan_response, multi_step_plan
) -> None:
    """autonomy='review' pauses after planning even when no tool is expensive — the
    executor never starts until the user approves."""
    plan_dict = multi_step_plan(
        [("reverse_complement", "Reverse complement."), ("gc_content", "GC of the result.")],
        summary="GC of the reverse complement.",
    )
    llm = fake_llm_factory([make_submit_plan_response(plan_dict)])

    result = await run_agent(
        "GC of rev comp of ATGCATGC",
        project_id=DEFAULT_PROJECT_ID,
        llm=llm,
        autonomy="review",
    )

    assert result.status == "pending_approval"
    assert result.pending_plan is not None
    assert any("Review mode" in r for r in result.approval_reasons)
    assert [s.type for s in result.steps] == ["plan", "approval_requested"]
    # Only the planner ran; the executor never started.
    assert len(llm.calls) == 1
