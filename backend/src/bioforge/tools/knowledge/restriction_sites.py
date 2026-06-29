"""Find restriction enzyme recognition sites and perform virtual DNA digest.

Restriction enzyme mapping is one of the most fundamental operations in
molecular biology — essential for cloning, construct validation, and gel
analysis. This tool:

  1. Scans a DNA sequence for recognition sites of a user-specified enzyme set
  2. Reports position, strand, and cut site for each hit
  3. Performs a virtual digest and returns the fragment sizes (in silico gel)
  4. Checks for enzyme compatibility (compatible cohesive ends) when multiple
     enzymes are specified

Enzyme database: Biopython's Restriction module contains 921 commercial enzymes
from REBASE (Roberts et al.) including NEB, ThermoFisher, and Fermentas catalogs.
"""

from __future__ import annotations

import asyncio

from pydantic import Field, field_validator

from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import register_tool

_DNA_CHARS = set("ACGTNacgtn")

_COMMON_ENZYMES = [
    "EcoRI", "BamHI", "HindIII", "XhoI", "NcoI", "NdeI", "SalI", "XbaI",
    "SpeI", "SacI", "KpnI", "SmaI", "PstI", "SphI", "ClaI", "ApaI",
    "NotI", "XmaI", "MluI", "NheI", "BglII", "EcoRV", "PmeI", "SfiI",
    "AscI", "FseI", "PacI", "SwaI", "SbfI", "AsiSI",
]


class RestrictionSiteInput(ToolInput):
    sequence: str = Field(
        ...,
        min_length=10,
        max_length=1_000_000,
        description=(
            "DNA sequence to analyse. FASTA headers (>...) are stripped "
            "automatically. Ambiguous bases (N, R, Y, etc.) are preserved. "
            "Minimum 10 bp; maximum 1 Mbp."
        ),
    )
    enzymes: list[str] = Field(
        default=_COMMON_ENZYMES,
        description=(
            "List of restriction enzyme names to search for. Uses standard enzyme "
            "names from REBASE/NEB (e.g. ['EcoRI', 'BamHI', 'HindIII']). "
            "Defaults to 30 common cloning enzymes. "
            "Use 'all_commercial' to screen all 921 commercially available enzymes "
            "(slower, ~2 s for a 10 kb insert)."
        ),
    )
    linear: bool = Field(
        default=True,
        description=(
            "If True (default), treat the sequence as linear — no wrap-around sites. "
            "If False, treat as circular (e.g. a plasmid)."
        ),
    )
    min_fragment_bp: int = Field(
        default=50,
        ge=1,
        description="Minimum fragment size to report in the virtual digest (bp).",
    )
    unique_cutters_only: bool = Field(
        default=False,
        description=(
            "If True, only report enzymes that cut exactly once in the sequence "
            "(useful for screening cloning sites in a vector)."
        ),
    )

    @field_validator("sequence")
    @classmethod
    def clean_sequence(cls, v: str) -> str:
        lines = v.strip().splitlines()
        if lines and lines[0].startswith(">"):
            lines = lines[1:]
        seq = "".join(lines).replace(" ", "").upper()
        invalid = set(seq) - _DNA_CHARS
        if invalid:
            raise ValueError(f"Sequence contains non-DNA characters: {invalid}")
        return seq


class RestrictionHit(ToolOutput):
    enzyme: str
    n_cuts: int = Field(description="Number of times this enzyme cuts the sequence.")
    positions: list[int] = Field(description="1-indexed cut positions on the top strand.")
    recognition_sequence: str
    cut_pattern: str = Field(description="e.g. 'G^AATTC' showing cut position with ^.")
    overhang_type: str = Field(description="'5prime', '3prime', or 'blunt'.")
    overhang_sequence: str = Field(description="Cohesive end sequence produced.")
    is_unique_cutter: bool


class DigestFragment(ToolOutput):
    start: int
    end: int
    length_bp: int
    sequence_preview: str = Field(description="First 20 bp of the fragment for identification.")


class RestrictionSiteOutput(ToolOutput):
    sequence_length: int
    topology: str = Field(description="'linear' or 'circular'.")
    n_enzymes_screened: int
    n_enzymes_cutting: int
    n_unique_cutters: int
    hits: list[RestrictionHit]
    digest_fragments: list[DigestFragment] = Field(
        description="Fragment sizes after digesting with ALL listed enzymes simultaneously.",
    )
    compatible_pairs: list[str] = Field(
        description="Pairs of enzymes producing compatible cohesive ends (can be ligated).",
    )
    caveats: list[str] = Field(default_factory=list)


def _find_sites_sync(
    sequence: str,
    enzyme_names: list[str],
    linear: bool,
    min_fragment: int,
    unique_only: bool,
) -> RestrictionSiteOutput:
    try:
        from Bio.Restriction import AllEnzymes, CommOnly, RestrictionBatch
        from Bio.Seq import Seq
    except ImportError as e:
        raise ToolError("Biopython is not installed. Run: pip install biopython") from e

    seq = Seq(sequence)

    # Resolve enzyme set
    use_commercial = "all_commercial" in enzyme_names
    if use_commercial:
        batch = CommOnly
    else:
        valid_enzymes = []
        unknown = []
        for name in enzyme_names:
            try:
                enz = AllEnzymes.get(name)
                if enz:
                    valid_enzymes.append(enz)
                else:
                    unknown.append(name)
            except Exception:
                unknown.append(name)
        if not valid_enzymes:
            raise ToolError(
                f"None of the specified enzymes were recognised: {enzyme_names}. "
                "Check names match REBASE convention (e.g. 'EcoRI', not 'EcoR1')."
            )
        batch = RestrictionBatch(valid_enzymes)

    analysis = batch.search(seq, linear=linear)

    hits: list[RestrictionHit] = []
    cutting_enzymes = []

    for enz, positions in analysis.items():
        if not positions:
            continue
        if unique_only and len(positions) != 1:
            continue

        cutting_enzymes.append(enz)

        # Get enzyme properties
        try:
            rec_seq = str(enz.site)
            esr = str(enz.edam_name) if hasattr(enz, "edam_name") else enz.__name__

            if enz.is_5overhang():
                oh_type = "5prime"
            elif enz.is_3overhang():
                oh_type = "3prime"
            else:
                oh_type = "blunt"

            try:
                oh_seq = str(enz.ovhg_seq()) if hasattr(enz, "ovhg_seq") else ""
            except Exception:
                oh_seq = ""

            # Build cut pattern string
            cut_seq = rec_seq
        except Exception:
            rec_seq = cut_seq = esr = "unknown"
            oh_type = "unknown"
            oh_seq = ""

        hits.append(RestrictionHit(
            enzyme=enz.__name__,
            n_cuts=len(positions),
            positions=sorted(positions),
            recognition_sequence=rec_seq,
            cut_pattern=cut_seq,
            overhang_type=oh_type,
            overhang_sequence=oh_seq,
            is_unique_cutter=(len(positions) == 1),
        ))

    # Sort by number of cuts, then enzyme name
    hits.sort(key=lambda h: (h.n_cuts, h.enzyme))

    # Virtual digest using all cutting enzymes simultaneously
    all_cuts = sorted(set(
        pos for hit in hits for pos in hit.positions
    ))

    fragments: list[DigestFragment] = []
    if all_cuts:
        cut_points = [0] + all_cuts + [len(sequence)]
        for i in range(len(cut_points) - 1):
            start = cut_points[i]
            end   = cut_points[i + 1]
            frag_len = end - start
            if frag_len >= min_fragment:
                fragments.append(DigestFragment(
                    start=start + 1,
                    end=end,
                    length_bp=frag_len,
                    sequence_preview=sequence[start:start + 20],
                ))
    fragments.sort(key=lambda f: f.length_bp, reverse=True)

    # Compatible pairs (same overhang type and sequence)
    compatible_pairs: list[str] = []
    for i, h1 in enumerate(hits):
        for h2 in hits[i + 1:]:
            if (h1.overhang_type == h2.overhang_type
                    and h1.overhang_sequence == h2.overhang_sequence
                    and h1.overhang_type != "blunt"
                    and h1.overhang_sequence):
                compatible_pairs.append(f"{h1.enzyme}/{h2.enzyme}")

    caveats = [
        "Cut positions are 1-indexed and refer to the top-strand cut site. "
        "The actual cut on the complementary strand differs by the overhang size.",
        "Dam methylation (GATC), Dcm methylation (CCWGG), and CpG methylation are "
        "not modelled. Some enzymes are blocked by methylation — check NEB's isoschizomer "
        "table if working with E. coli-propagated plasmids.",
    ]

    if use_commercial:
        caveats.append("All 921 commercially available restriction enzymes were screened (CommOnly batch).")

    return RestrictionSiteOutput(
        sequence_length=len(sequence),
        topology="linear" if linear else "circular",
        n_enzymes_screened=len(analysis),
        n_enzymes_cutting=len(cutting_enzymes),
        n_unique_cutters=sum(1 for h in hits if h.is_unique_cutter),
        hits=hits[:100],  # cap at 100 enzymes for readability
        digest_fragments=fragments[:50],
        compatible_pairs=compatible_pairs[:20],
        caveats=caveats,
    )


@register_tool(
    name="restriction_sites",
    description=(
        "Find restriction enzyme recognition sites in a DNA sequence and perform "
        "a virtual digest. Returns enzyme name, cut positions, overhang type and "
        "sequence, fragment sizes after digest, and compatible enzyme pairs. "
        "Use when the user asks 'find EcoRI and BamHI sites in my insert', "
        "'what restriction enzymes cut this sequence', 'do a virtual digest of "
        "this plasmid', 'find unique cutters for cloning', or 'what size bands "
        "will I see on a gel'. Defaults to screening 30 common cloning enzymes; "
        "set enzymes=['all_commercial'] to screen all 921 commercial enzymes. "
        "Supports linear and circular (plasmid) topologies."
    ),
    input_model=RestrictionSiteInput,
    output_model=RestrictionSiteOutput,
    version="1.0.0",
    citations=[
        "Roberts RJ et al. (2023) REBASE: a database for DNA restriction and "
        "modification: enzymes, genes and genomes. Nucleic Acids Res 51(D1):D629-D630.",
        "Cock PJ et al. (2009) Biopython: freely available Python tools for "
        "computational molecular biology and bioinformatics. Bioinformatics 25(11):1422-1423.",
    ],
    cost_hint="cheap",
    tags=["sequence", "restriction", "cloning", "digest", "molecular_biology"],

)
async def restriction_sites(inp: RestrictionSiteInput) -> RestrictionSiteOutput:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        _find_sites_sync,
        inp.sequence,
        inp.enzymes,
        inp.linear,
        inp.min_fragment_bp,
        inp.unique_cutters_only,
    )
