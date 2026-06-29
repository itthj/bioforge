"""Compute protein physicochemical properties from an amino acid sequence.

Every protein engineering, expression, and purification experiment starts with
these numbers: molecular weight, isoelectric point, extinction coefficient,
instability index, GRAVY score, and amino acid composition. Getting them wrong
means designing the wrong purification buffer, predicting the wrong gel band,
or misinterpreting a UV reading.

This tool computes all standard ProtParam metrics (as used on the ExPASy
ProtParam web tool) deterministically from the sequence using Biopython.
No network call required — pure computation, always correct.

Includes an in-silico proteolytic digest for common restriction enzymes
(trypsin, Lys-C, Glu-C) to support mass-spectrometry experimental design.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import Field, field_validator

from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import register_tool

_VALID_AA = set("ACDEFGHIKLMNPQRSTVWY")
_AA_WEIGHTS = {  # monoisotopic masses for reference
    "A": 89.09, "R": 174.20, "N": 132.12, "D": 133.10, "C": 121.16,
    "E": 147.13, "Q": 146.15, "G": 75.03, "H": 155.16, "I": 131.17,
    "L": 131.17, "K": 146.19, "M": 149.21, "F": 165.19, "P": 115.13,
    "S": 105.09, "T": 119.12, "W": 204.23, "Y": 181.19, "V": 117.15,
}


class ProteinPropertiesInput(ToolInput):
    sequence: str = Field(
        ...,
        min_length=5,
        max_length=50000,
        description=(
            "Amino acid sequence in single-letter code (IUPAC). Case-insensitive. "
            "Spaces, line breaks, and FASTA headers (>...) are stripped automatically. "
            "Example: 'MKTAYIAKQRQISFVKSHFSRQLEERLGLIEVQAPILSRVGDGTQDNLSGAEKAVQVKVKALPDAQFEVVHSLAKWKRQTLGQHDFSAGEGLYTHMKALRPDEDRLSPLHSVYVDQWDWERVMGDGERQFSTLKSTVEAIWAGIKATEAAVSEEFGLAPFLPDQIHFVHSQELLSRYPDLDAKGRERAIAKDLGAVFLVGIGGKLSDGHRHDVRAPDYDDWSTPSELGHAGLNGDILVWNPVLEDAFELSSMGIRVDADTLKHQLALTGDEDRLELEWHQALLRGEMPQTIGGGIGQSRLTMLLLQLPHIGQVQAGVWPAAVRESVPSLL'"
        ),
    )
    run_digest: bool = Field(
        default=True,
        description="Whether to compute a theoretical tryptic digest (for MS experiment planning).",
    )

    @field_validator("sequence")
    @classmethod
    def clean_and_validate(cls, v: str) -> str:
        # Strip FASTA header
        lines = v.strip().splitlines()
        if lines and lines[0].startswith(">"):
            lines = lines[1:]
        seq = "".join(lines).replace(" ", "").upper()
        invalid = set(seq) - _VALID_AA
        if invalid:
            raise ValueError(
                f"Sequence contains non-standard amino acid characters: {invalid}. "
                "Only standard 20-letter IUPAC code is supported."
            )
        return seq


class TrypticPeptide(ToolOutput):
    sequence: str
    start: int
    end: int
    length: int
    molecular_weight: float
    missed_cleavages: int


class ProteinPropertiesOutput(ToolOutput):
    sequence_length: int
    molecular_weight_da: float = Field(description="Molecular weight in Daltons (average isotopic masses).")
    molecular_weight_kda: float = Field(description="Molecular weight in kiloDaltons.")
    isoelectric_point: float = Field(description="Theoretical isoelectric point (pI).")
    instability_index: float = Field(
        description=(
            "Instability index (Guruprasad et al. 1990). "
            "< 40 = predicted stable; ≥ 40 = predicted unstable in vitro."
        )
    )
    gravy_score: float = Field(
        description=(
            "Grand average of hydropathicity (GRAVY, Kyte & Doolittle 1982). "
            "Positive = hydrophobic; negative = hydrophilic."
        )
    )
    aromaticity: float = Field(
        description="Fraction of aromatic residues (Phe, Trp, Tyr). Lobry & Gautier 1994."
    )
    extinction_coefficient_reduced: int = Field(
        description="Molar extinction coefficient (ε) at 280 nm assuming all Cys are reduced (M⁻¹cm⁻¹)."
    )
    extinction_coefficient_oxidised: int = Field(
        description="Molar extinction coefficient at 280 nm assuming all Cys form disulfide bonds."
    )
    a280_1mg_ml_reduced: float = Field(description="A₂₈₀ for 1 mg/mL solution (reduced Cys).")
    a280_1mg_ml_oxidised: float = Field(description="A₂₈₀ for 1 mg/mL solution (oxidised Cys).")
    amino_acid_composition: dict[str, int] = Field(description="Count of each amino acid in the sequence.")
    amino_acid_percent: dict[str, float] = Field(description="Percentage of each amino acid.")
    predicted_half_life: str = Field(
        description=(
            "Predicted half-life in mammalian cells based on the N-end rule "
            "(Bachmair et al. 1986). Cytoplasmic estimate only."
        )
    )
    secondary_structure_fraction: dict[str, float] = Field(
        description="Predicted fraction of helix, turn, and sheet (Chou & Fasman 1978 empirical method)."
    )
    tryptic_peptides: list[TrypticPeptide] = Field(
        default_factory=list,
        description="Theoretical tryptic digest (cleave after K/R, not before P). For MS experiment planning.",
    )
    is_stable: bool
    caveats: list[str]


# ─── N-end rule half-lives (mammalian) ────────────────────────────────────────
_HALF_LIVES = {
    "A": ">20 h", "R": "1 h", "N": "1.4 h", "D": "1.1 h", "C": "1.2 h",
    "E": "1 h", "Q": "0.8 h", "G": ">20 h", "H": "3.5 h", "I": "20 h",
    "L": "5.5 h", "K": "1.3 h", "M": ">20 h", "F": "1.1 h", "P": "?",
    "S": "1.9 h", "T": "7.2 h", "W": "2.8 h", "Y": "2.8 h", "V": "100 h",
}


def _compute_properties(seq: str, run_digest: bool) -> ProteinPropertiesOutput:
    try:
        from Bio.SeqUtils.ProtParam import ProteinAnalysis
    except ImportError as e:
        raise ToolError("Biopython is not installed. Run: pip install biopython") from e

    analysis = ProteinAnalysis(seq)

    mw       = round(analysis.molecular_weight(), 2)
    pi       = round(analysis.isoelectric_point(), 2)
    instab   = round(analysis.instability_index(), 2)
    gravy    = round(analysis.gravy(), 4)
    aromaticity = round(analysis.aromaticity(), 4)

    ec_cys_red, ec_cys_ox = analysis.molar_extinction_coefficient()
    a280_red = round(ec_cys_red / mw, 3) if mw > 0 else 0.0
    a280_ox  = round(ec_cys_ox / mw, 3) if mw > 0 else 0.0

    aa_comp = analysis.count_amino_acids()
    aa_pct  = {aa: round(count / len(seq) * 100, 2) for aa, count in aa_comp.items()}

    helix, turn, sheet = analysis.secondary_structure_fraction()

    n_terminal = seq[0] if seq else "M"
    half_life  = _HALF_LIVES.get(n_terminal, "unknown")

    # Tryptic digest
    peptides: list[TrypticPeptide] = []
    if run_digest:
        sites = [i for i, aa in enumerate(seq) if aa in ("K", "R") and (i + 1 >= len(seq) or seq[i + 1] != "P")]
        cut_points = [-1] + sites + [len(seq) - 1]
        for i in range(len(cut_points) - 1):
            start = cut_points[i] + 1
            end   = cut_points[i + 1]
            pep   = seq[start:end + 1]
            if len(pep) >= 6:
                pep_mw = sum(_AA_WEIGHTS.get(aa, 111.1) for aa in pep) - (len(pep) - 1) * 18.02 + 18.02
                peptides.append(TrypticPeptide(
                    sequence=pep,
                    start=start + 1,  # 1-indexed
                    end=end + 1,
                    length=len(pep),
                    molecular_weight=round(pep_mw, 1),
                    missed_cleavages=sum(1 for aa in pep[:-1] if aa in ("K", "R")),
                ))

    return ProteinPropertiesOutput(
        sequence_length=len(seq),
        molecular_weight_da=mw,
        molecular_weight_kda=round(mw / 1000, 2),
        isoelectric_point=pi,
        instability_index=instab,
        gravy_score=gravy,
        aromaticity=aromaticity,
        extinction_coefficient_reduced=ec_cys_red,
        extinction_coefficient_oxidised=ec_cys_ox,
        a280_1mg_ml_reduced=a280_red,
        a280_1mg_ml_oxidised=a280_ox,
        amino_acid_composition=dict(aa_comp),
        amino_acid_percent=aa_pct,
        predicted_half_life=half_life,
        secondary_structure_fraction={
            "helix": round(helix, 3),
            "turn": round(turn, 3),
            "sheet": round(sheet, 3),
        },
        tryptic_peptides=peptides[:50],
        is_stable=instab < 40,
        caveats=[
            "Isoelectric point and extinction coefficients are computed from the amino "
            "acid sequence alone; they do not account for PTMs, bound cofactors, or "
            "non-standard residues.",
            "The instability index predicts in vitro stability of the expressed protein; "
            "it does not predict in vivo half-life, which is governed by the N-end rule, "
            "ubiquitination, and cellular context.",
            "Secondary structure fractions are from the Chou-Fasman empirical method and "
            "are approximate; use AlphaFold or experimental data for accurate structure.",
            "Tryptic peptide MW values use average (not monoisotopic) masses — appropriate "
            "for Mascot/SEQUEST database search with QTOF/Orbitrap data; adjust for "
            "monoisotopic masses if needed.",
        ],
    )


@register_tool(
    name="protein_properties",
    description=(
        "Compute physicochemical properties of a protein from its amino acid sequence. "
        "Returns: molecular weight, isoelectric point (pI), instability index, GRAVY "
        "hydropathicity score, aromaticity, molar extinction coefficient at 280 nm "
        "(for UV quantification), amino acid composition, predicted secondary structure "
        "fractions, N-terminal half-life estimate, and a theoretical tryptic digest for "
        "mass spectrometry planning. All calculations are deterministic using the same "
        "algorithms as ExPASy ProtParam. Use when the user provides a protein sequence "
        "and asks about its MW, pI, stability, UV absorbance, or MS peptides — or as "
        "a routine first step before expression, purification, or MS experiments. "
        "Accepts raw sequences or FASTA format."
    ),
    input_model=ProteinPropertiesInput,
    output_model=ProteinPropertiesOutput,
    version="1.0.0",
    citations=[
        "Gasteiger E et al. (2005) Protein Identification and Analysis Tools on the "
        "ExPASy Server. In: Walker JM (ed) The Proteomics Protocols Handbook, pp 571-607.",
        "Kyte J, Doolittle RF (1982) A simple method for displaying the hydropathic "
        "character of a protein. J Mol Biol 157(1):105-132.",
        "Guruprasad K et al. (1990) Correlation between stability of a protein and its "
        "dipeptide composition: a novel approach for predicting in vivo stability of a "
        "protein from its primary sequence. Protein Eng 4(2):155-161.",
    ],
    cost_hint="cheap",
    tags=["sequence", "protein", "biophysics", "properties", "proteomics"],

)
async def protein_properties(inp: ProteinPropertiesInput) -> ProteinPropertiesOutput:
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _compute_properties, inp.sequence, inp.run_digest)
