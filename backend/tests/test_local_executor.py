"""Tests for the Phase 5.2 LocalExecutor role.

The wrapper is thin — LocalExecutor calls into the existing `_execute()` and
shapes the result. We test:
  - It satisfies the Executor Protocol via isinstance.
  - The side-channel attributes (last_steps, last_usage, last_status) are
    populated after each call.
  - tool_calls in the ExecutionResult mirror the tool_call/tool_error AgentSteps.
  - refused / iteration_cap statuses map onto ExecutionResult flags correctly.
  - run_agent accepts a custom Executor — proves the injection wires through.
"""

from __future__ import annotations

from bioforge.agent.local_executor import LocalExecutor
from bioforge.agent.roles import ExecutionResult, Executor, ExecutorContext

# --- Protocol conformance ----------------------------------------------------------


def test_local_executor_satisfies_executor_protocol(fake_llm_factory) -> None:
    executor = LocalExecutor(fake_llm_factory([]))
    assert isinstance(executor, Executor)


# --- Happy path: single tool call, end_turn -------------------------------------


async def test_execute_runs_one_tool_and_returns_text(
    fake_llm_factory, make_tool_use_response, make_text_response, trivial_plan
) -> None:
    from bioforge.agent.planner import Plan

    llm = fake_llm_factory(
        [
            make_tool_use_response("gc_content", {"sequence": "ATGCATGC"}, tool_use_id="toolu_1"),
            make_text_response("GC content is 50%."),
        ]
    )
    executor = LocalExecutor(llm)
    plan = Plan.model_validate(trivial_plan(tool_name="gc_content"))

    result = await executor.execute(
        ExecutorContext(plan=plan, goal="GC content of ATGCATGC", model="claude-sonnet-4-6")
    )

    assert isinstance(result, ExecutionResult)
    assert "50%" in result.response_text
    # One tool call landed.
    assert len(result.tool_calls) == 1
    tc = result.tool_calls[0]
    assert tc["name"] == "gc_content"
    assert tc["input"] == {"sequence": "ATGCATGC"}
    # Two LLM iterations (the tool-use turn + the final text turn).
    assert result.iterations_used == 2
    assert result.refused is False
    assert result.finished_with_tool_use is False


async def test_execute_populates_side_channels(
    fake_llm_factory, make_tool_use_response, make_text_response, trivial_plan
) -> None:
    """last_steps, last_usage, last_status must be set so the agent loop can read them."""
    from bioforge.agent.planner import Plan

    llm = fake_llm_factory(
        [
            make_tool_use_response("gc_content", {"sequence": "ATGC"}, tool_use_id="toolu_1"),
            make_text_response("GC content is 50%."),
        ]
    )
    executor = LocalExecutor(llm)
    plan = Plan.model_validate(trivial_plan(tool_name="gc_content"))

    assert executor.last_status == ""
    assert executor.last_usage is None
    assert executor.last_steps == []

    await executor.execute(ExecutorContext(plan=plan, goal="g", model="claude-sonnet-4-6"))

    assert executor.last_status == "completed"
    assert executor.last_usage is not None
    assert executor.last_usage.input_tokens > 0
    # Two llm_call steps + one tool_call + one final step at minimum.
    types = [s.type for s in executor.last_steps]
    assert "tool_call" in types
    assert "final" in types


# --- Refusal path ---------------------------------------------------------------


async def test_execute_maps_refusal_to_execution_result(fake_llm_factory) -> None:
    """When the model returns stop_reason='refusal', LocalExecutor must set
    `refused=True` and stash the refusal text in `refusal_reason`."""
    from anthropic.types import Message, TextBlock, Usage
    from bioforge.agent.planner import Plan, PlanStep

    refusal_msg = Message(
        id="msg_refusal_test",
        type="message",
        role="assistant",
        model="claude-sonnet-4-6",
        content=[TextBlock(type="text", text="I cannot help with this.", citations=None)],
        stop_reason="refusal",
        stop_sequence=None,
        usage=Usage(
            input_tokens=10,
            output_tokens=5,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
            service_tier=None,
        ),
    )
    llm = fake_llm_factory([refusal_msg])
    executor = LocalExecutor(llm)
    plan = Plan(
        is_trivial=True,
        summary="...",
        steps=[PlanStep(idx=0, description="x", expected_tool="gc_content", rationale="r")],
    )

    result = await executor.execute(ExecutorContext(plan=plan, goal="g", model="claude-sonnet-4-6"))
    assert result.refused is True
    assert result.refusal_reason
    assert executor.last_status == "refused"


# --- Iteration cap ---------------------------------------------------------------


async def test_execute_maps_iteration_cap(fake_llm_factory, make_tool_use_response, trivial_plan) -> None:
    """If max_iterations runs out while the model keeps requesting tools, the
    executor must set `finished_with_tool_use=True` and last_status='iteration_cap'."""
    from bioforge.agent.planner import Plan

    # max_iterations=2 with two tool_use responses → never reaches end_turn.
    llm = fake_llm_factory(
        [
            make_tool_use_response("gc_content", {"sequence": "ATGCATGC"}, tool_use_id="toolu_1"),
            make_tool_use_response("gc_content", {"sequence": "GGGGCCCC"}, tool_use_id="toolu_2"),
        ]
    )
    executor = LocalExecutor(llm)
    plan = Plan.model_validate(trivial_plan(tool_name="gc_content"))

    result = await executor.execute(ExecutorContext(plan=plan, goal="g", model="claude-sonnet-4-6", max_iterations=2))
    assert executor.last_status == "iteration_cap"
    assert result.finished_with_tool_use is True
    assert result.refused is False


# --- Complaints propagate to the user message -----------------------------------


async def test_complaints_appear_in_executor_user_message(fake_llm_factory, make_text_response, trivial_plan) -> None:
    """The executor's user message must surface the complaints so the model
    addresses them in the retry."""
    from bioforge.agent.planner import Plan

    llm = fake_llm_factory([make_text_response("Done addressing complaints.")])
    executor = LocalExecutor(llm)
    plan = Plan.model_validate(trivial_plan(tool_name="gc_content"))

    await executor.execute(
        ExecutorContext(
            plan=plan,
            goal="GC content",
            model="claude-sonnet-4-6",
            complaints=["First attempt didn't actually call gc_content.", "Provide a number."],
        )
    )

    user_msg = llm.calls[0].messages[0]["content"]
    assert "previous attempt" in user_msg.lower()
    assert "didn't actually call gc_content" in user_msg.lower()
    assert "provide a number" in user_msg.lower()


# --- Injectable into run_agent ---------------------------------------------------


async def test_run_agent_accepts_custom_executor(fake_llm_factory, make_submit_plan_response, trivial_plan) -> None:
    """Loop must dispatch through the injected Executor. A stub Executor that
    returns a fixed ExecutionResult should drive run_agent's terminal path."""
    from bioforge.agent import run_agent
    from bioforge.agent.llm import UsageSummary
    from bioforge.agent.roles import ExecutionResult

    class StubExecutor:
        def __init__(self):
            self.last_steps: list = []  # type: ignore[assignment]
            self.last_usage = UsageSummary.zero("claude-sonnet-4-6")
            self.last_status = "completed"
            self.calls = 0

        async def execute(self, ctx: ExecutorContext) -> ExecutionResult:
            self.calls += 1
            return ExecutionResult(
                response_text="Stubbed: GC content is 50%.",
                tool_calls=[],
                iterations_used=0,
            )

    # Only the planner LLM is exercised; the stub executor short-circuits the rest.
    llm = fake_llm_factory([make_submit_plan_response(trivial_plan(tool_name="gc_content"))])
    stub = StubExecutor()
    result = await run_agent(
        "GC content of ATGCATGC",
        project_id="default-project",
        llm=llm,
        executor=stub,
        enable_critic=False,
        skip_approval_gate=True,
    )
    assert stub.calls == 1
    assert result.status == "completed"
    assert "Stubbed" in result.response_text
