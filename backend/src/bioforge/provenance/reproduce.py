"""§10 reproduce-in-code — emit a runnable Python script that re-executes a run's tool pipeline.

The methods report and RO-Crate describe a run for humans and machines; this closes the
GUI↔code gap (BioForge's own stated principle: a GUI for biologists, code for bioinformaticians).
A scientist clicks "Reproduce in code" and gets a script that re-runs the EXACT deterministic
tool calls — same tools, same inputs, in order — against the installed `bioforge` package.

The language model is intentionally NOT re-invoked: the script reproduces the deterministic
tool pipeline (which is what makes a result checkable), not the agent's planning.

Pure and deterministic for a given AgentResult. Never fabricates: only `tool_call` steps that
actually ran are emitted, and their inputs are the recorded inputs verbatim.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bioforge.agent.loop import AgentResult, AgentStep


def _comment_block(label: str, text: str) -> list[str]:
    lines = (text or "").strip().splitlines() or [""]
    out = [f"# {label}:"]
    out += [f"#   {ln}" for ln in lines]
    return out


def _tool_version(step: AgentStep) -> str | None:
    out = step.tool_output or {}
    if isinstance(out, dict):
        for key in ("tool_version", "version"):
            value = out.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def render_reproduce_script(result: AgentResult) -> str:
    """Render a runnable Python script re-executing the run's tool calls. Pure + deterministic."""
    tool_steps = [s for s in result.steps if s.type == "tool_call" and s.tool_name]

    out: list[str] = []
    out.append("#!/usr/bin/env python")
    out.append('"""Reproduce a BioForge run — deterministic tool pipeline.')
    out.append("")
    out.append("Re-runs the recorded tool calls (same tools, same inputs, in order) against the")
    out.append("installed `bioforge` package. The language model is NOT re-invoked, so the tool")
    out.append("pipeline is deterministic for pinned tool versions.")
    out.append('"""')
    out.append("import asyncio")
    out.append("")
    out.append("from bioforge.tools.registry import execute_tool")
    out.append("")
    out += _comment_block("Goal", result.goal)
    out.append(f"# Run status: {result.status}  |  model: {result.model}")
    out.append("")
    out.append("")
    out.append("async def main() -> None:")
    if not tool_steps:
        out.append("    # This run invoked no tools; the answer came directly from the model,")
        out.append("    # so there is no deterministic tool pipeline to reproduce.")
        out.append("    return")
    else:
        for i, step in enumerate(tool_steps, start=1):
            ver = _tool_version(step)
            label = f"{step.tool_name} (v{ver})" if ver else step.tool_name
            args = step.tool_input or {}
            out.append(f"    # Step {i}: {label}")
            out.append(f"    args_{i} = {args!r}")
            out.append(f"    out_{i} = await execute_tool({step.tool_name!r}, args_{i})")
            out.append(f"    print({f'Step {i}: {step.tool_name} ->'!r}, out_{i}.model_dump())")
            out.append("")
    out.append("")
    out.append('if __name__ == "__main__":')
    out.append("    asyncio.run(main())")
    return "\n".join(out) + "\n"
