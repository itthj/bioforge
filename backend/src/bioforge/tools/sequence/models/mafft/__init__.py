"""MAFFT out-of-process runner (multiple-sequence alignment).

Core MAFFT is BSD-3-Clause (commercial-clean; see docs/license_audit.md). The bundled
extensions (Vienna RNA, MXSCARNA) are restrictively licensed and are intentionally NOT used
-- the image must be core-only.
"""

from bioforge.tools.sequence.models.mafft.runner import (
    MafftError,
    MafftUnavailable,
    build_command,
    run_alignment,
)

__all__ = ["MafftError", "MafftUnavailable", "build_command", "run_alignment"]
