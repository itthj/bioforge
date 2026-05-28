"""BioForge command-line entry points.

The modules under `bioforge.cli` are thin argparse wrappers around tool
handlers, invoked by Nextflow process blocks (and occasionally by a
shell user for debugging). They are NOT the agent's interface — the
agent always calls tools through `bioforge.tools.registry.execute_tool`.

Each module is invokable as `python -m bioforge.cli.<name> [args]` so
Nextflow pipelines can declare reproducible commands without depending
on a `bioforge` console-script entry point being installed.
"""
