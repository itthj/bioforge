"""§0/§4.1/§4.3 OOD pre-gate (`ood_refusal`).

`ood_refusal` is the pure decision the agent loop acts on before executing a tool: in "block"
mode an out-of-envelope input is refused BEFORE the tool runs (the §0 inputs boundary); in the
default "off" mode the pre-gate is disabled (the post-response detector still records OOD). The
loop integration is covered by the full suite staying green with the default "off" — behavioral
equivalence — and the inline wiring mirrors the existing tool_error path.
"""

from __future__ import annotations

from bioforge.agent.grounding import ood_refusal

_OFF_ENVELOPE = {"guide": "ACGT" * 4 + "AC"}  # 18 nt — outside find_offtargets' 20-nt Hsu envelope
_IN_ENVELOPE = {"guide": "ACGT" * 5}  # 20 nt — within the envelope


def test_block_mode_refuses_off_envelope_input() -> None:
    report = ood_refusal("find_offtargets", _OFF_ENVELOPE, mode="block")
    assert report is not None
    assert report.flags
    assert any(f.field == "guide" for f in report.flags)


def test_off_mode_never_pre_gates() -> None:
    # Default mode: pre-gate disabled, so the loop stays behaviorally identical.
    assert ood_refusal("find_offtargets", _OFF_ENVELOPE, mode="off") is None


def test_block_mode_allows_in_envelope_input() -> None:
    assert ood_refusal("find_offtargets", _IN_ENVELOPE, mode="block") is None


def test_block_mode_skips_tools_without_a_checker() -> None:
    # Precision-first: a tool with no registered OOD checker is never blocked on a guessed envelope.
    assert ood_refusal("gc_content", {"sequence": "ACGTACGT"}, mode="block") is None
