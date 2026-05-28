"""Tests for annotate_variant (Phase 3 — Ensembl VEP).

Strategy: monkeypatch `_fetch_vep` to return the committed BRCA1 fixture so the
suite is hermetic. One @pytest.mark.online test at the bottom hits the real
Ensembl REST endpoint for the same variant — deselected by default; catches
upstream API drift on the nightly run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from bioforge.tools.base import ToolError
from bioforge.tools.variants import annotate_variant as av_module
from bioforge.tools.variants.annotate_variant import (
    AnnotateVariantInput,
    _derive_cdna_change,
    _derive_protein_change,
    _pick_canonical_consequence,
    annotate_variant,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"
BRCA1_FIXTURE = FIXTURE_DIR / "ensembl_vep_brca1_c181tg.json"


def _load_brca1_payload() -> list[dict[str, Any]]:
    with BRCA1_FIXTURE.open("r", encoding="utf-8") as f:
        return json.load(f)


# --- Input validation ---------------------------------------------------------------


def test_input_strips_whitespace() -> None:
    inp = AnnotateVariantInput(hgvs="  ENST00000357654.9:c.181T>G  ")
    assert inp.hgvs == "ENST00000357654.9:c.181T>G"


def test_input_rejects_garbage_characters() -> None:
    import pydantic

    with pytest.raises(pydantic.ValidationError, match="unexpected characters"):
        AnnotateVariantInput(hgvs="ENST00000357654.9:c.181T>G; DROP TABLE variants;--")


def test_input_rejects_empty_string() -> None:
    import pydantic

    # min_length=4 catches an empty string before the validator fires.
    with pytest.raises(pydantic.ValidationError):
        AnnotateVariantInput(hgvs="")


def test_input_rejects_bad_species_slug() -> None:
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        AnnotateVariantInput(hgvs="ENST00000357654.9:c.181T>G", species="Homo Sapiens")


# --- Derivation helpers -------------------------------------------------------------


def test_derive_protein_change_for_missense() -> None:
    assert _derive_protein_change("C/G", 61) == "C61G"


def test_derive_protein_change_none_for_missing_inputs() -> None:
    assert _derive_protein_change(None, 61) is None
    assert _derive_protein_change("C/G", None) is None
    assert _derive_protein_change("C", 61) is None  # no slash
    assert _derive_protein_change("CC/GG", 61) is None  # multi-residue


def test_derive_cdna_change_for_substitution() -> None:
    assert _derive_cdna_change(181, 181, "T/G") == "c.181T>G"


def test_derive_cdna_change_none_for_indels() -> None:
    # Different start/end → indel-like, we don't synthesize HGVS for these.
    assert _derive_cdna_change(181, 183, "TAG/A") is None
    # Missing positions.
    assert _derive_cdna_change(None, None, "T/G") is None
    # Multi-allelic.
    assert _derive_cdna_change(181, 181, "T/G/C") is None


# --- End-to-end against the committed BRCA1 fixture ---------------------------------


async def _stub_fetch_brca1(*args, **kwargs) -> list[dict[str, Any]]:
    return _load_brca1_payload()


async def test_brca1_canonical_consequence_is_missense_in_protein_coding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(av_module, "_fetch_vep", _stub_fetch_brca1)

    out = await annotate_variant(AnnotateVariantInput(hgvs="ENST00000357654.9:c.181T>G"))

    assert out.canonical_consequence is not None
    cc = out.canonical_consequence
    assert cc.gene_symbol == "BRCA1"
    assert cc.biotype == "protein_coding"
    assert "missense_variant" in cc.consequence_terms
    assert cc.protein_change == "C61G"
    assert cc.cdna_change == "c.181T>G"
    assert cc.sift_prediction == "deleterious"


async def test_brca1_top_level_provenance_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(av_module, "_fetch_vep", _stub_fetch_brca1)

    out = await annotate_variant(AnnotateVariantInput(hgvs="ENST00000357654.9:c.181T>G"))

    assert out.assembly_name == "GRCh38"
    assert out.seq_region_name == "17"
    assert out.start == 43106487
    assert out.end == 43106487
    assert out.strand == -1
    assert out.allele_string == "T/G"
    assert out.most_severe_consequence == "missense_variant"


async def test_brca1_consequences_sorted_canonical_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(av_module, "_fetch_vep", _stub_fetch_brca1)

    out = await annotate_variant(AnnotateVariantInput(hgvs="ENST00000357654.9:c.181T>G"))

    assert out.transcript_consequences[0].canonical is True
    assert out.transcript_consequences[0].transcript_id == "ENST00000357654"
    # Each transcript is either coding-MODERATE or non-coding-MODIFIER; the MODIFIER
    # rows should come AFTER all MODERATE rows in the sorted list.
    impacts = [r.impact for r in out.transcript_consequences]
    moderate_idxs = [i for i, x in enumerate(impacts) if x == "MODERATE"]
    modifier_idxs = [i for i, x in enumerate(impacts) if x == "MODIFIER"]
    assert max(moderate_idxs) < min(modifier_idxs)


async def test_brca1_picks_up_clinvar_and_pathogenic_significance(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real reason this slice exists — surface the clinical significance and
    ClinVar accessions for the agent. ClinVar marks this variant pathogenic."""
    monkeypatch.setattr(av_module, "_fetch_vep", _stub_fetch_brca1)

    out = await annotate_variant(AnnotateVariantInput(hgvs="ENST00000357654.9:c.181T>G"))

    assert "pathogenic" in out.clinvar_significance
    assert "likely_pathogenic" in out.clinvar_significance

    # The dbSNP record has both flags.
    dbsnp_record = next(c for c in out.colocated_variants if c.id == "rs28897672")
    assert "pathogenic" in dbsnp_record.clin_sig
    assert dbsnp_record.phenotype_or_disease is True
    assert "VCV000017661" in dbsnp_record.clinvar_accessions
    assert dbsnp_record.pubmed_count == 5  # fixture has 5 pubmed IDs
    # gnomAD overall AF surfaces.
    assert dbsnp_record.gnomad_af is not None
    assert dbsnp_record.gnomad_af == pytest.approx(1.721e-05)


async def test_brca1_somatic_cosmic_variant_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(av_module, "_fetch_vep", _stub_fetch_brca1)

    out = await annotate_variant(AnnotateVariantInput(hgvs="ENST00000357654.9:c.181T>G"))

    cosmic = next(c for c in out.colocated_variants if c.id.startswith("COSV"))
    assert cosmic.somatic is True


async def test_nmd_caveat_appears_when_nmd_transcripts_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fixture has an NMD transcript — the response should call that out so the
    agent doesn't claim 'the protein change is C61G in this transcript' without
    qualifying that NMD targets it."""
    monkeypatch.setattr(av_module, "_fetch_vep", _stub_fetch_brca1)

    out = await annotate_variant(AnnotateVariantInput(hgvs="ENST00000357654.9:c.181T>G"))

    full_caveats = " ".join(out.caveats).lower()
    assert "nmd" in full_caveats
    assert "vep consequences are computational" in full_caveats
    # Required honesty: this is not a clinical assertion.
    assert "clinvar" in full_caveats or "clinical" in full_caveats


# --- Empty / degenerate responses ----------------------------------------------------


async def test_empty_payload_returns_empty_output_with_caveat(monkeypatch: pytest.MonkeyPatch) -> None:
    async def stub(*a, **kw):
        return []

    monkeypatch.setattr(av_module, "_fetch_vep", stub)
    out = await annotate_variant(AnnotateVariantInput(hgvs="ENST99999999.1:c.1A>G"))

    assert out.transcript_consequences == []
    assert out.canonical_consequence is None
    assert any("empty result" in c.lower() for c in out.caveats)


async def test_no_transcript_consequences_emits_extra_caveat(monkeypatch: pytest.MonkeyPatch) -> None:
    async def stub(*a, **kw):
        return [
            {
                "input": "X",
                "assembly_name": "GRCh38",
                "seq_region_name": "1",
                "start": 1000,
                "end": 1000,
                "strand": 1,
                "allele_string": "A/G",
                "most_severe_consequence": "intergenic_variant",
                "transcript_consequences": [],
                "colocated_variants": [],
            }
        ]

    monkeypatch.setattr(av_module, "_fetch_vep", stub)
    out = await annotate_variant(AnnotateVariantInput(hgvs="1:g.1000A>G"))

    assert out.transcript_consequences == []
    assert out.canonical_consequence is None
    assert out.most_severe_consequence == "intergenic_variant"
    assert any("no transcript-level consequence" in c.lower() for c in out.caveats)


# --- Error paths ---------------------------------------------------------------------


async def test_http_400_surfaces_as_tool_error_with_actionable_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensembl returns 400 for malformed HGVS — the agent needs to see the body
    so it can correct the input rather than infinite-loop retrying."""

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            return httpx.Response(
                status_code=400,
                content=b'{"error":"Could not parse HGVS notation thats_not_real"}',
                request=httpx.Request("GET", url),
            )

    monkeypatch.setattr(av_module.httpx, "AsyncClient", FakeClient)

    with pytest.raises(ToolError) as exc:
        await annotate_variant(AnnotateVariantInput(hgvs="thats_not_real:c.1A>G"))
    assert "400" in str(exc.value)
    assert "HGVS" in str(exc.value)
    # Body excerpt is included so the agent can see WHY it was rejected.
    assert "Could not parse" in str(exc.value)


async def test_http_404_surfaces_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            return httpx.Response(status_code=404, content=b"", request=httpx.Request("GET", url))

    monkeypatch.setattr(av_module.httpx, "AsyncClient", FakeClient)
    with pytest.raises(ToolError, match="404"):
        await annotate_variant(AnnotateVariantInput(hgvs="ENST99999999.9:c.1A>G"))


async def test_network_error_surfaces_as_tool_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr(av_module.httpx, "AsyncClient", FakeClient)
    with pytest.raises(ToolError, match="unreachable"):
        await annotate_variant(AnnotateVariantInput(hgvs="ENST00000357654.9:c.181T>G"))


async def test_rate_limit_surfaces_distinctly(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            return httpx.Response(status_code=429, content=b"", request=httpx.Request("GET", url))

    monkeypatch.setattr(av_module.httpx, "AsyncClient", FakeClient)
    with pytest.raises(ToolError, match="rate-limited"):
        await annotate_variant(AnnotateVariantInput(hgvs="ENST00000357654.9:c.181T>G"))


# --- canonical-picker unit tests -----------------------------------------------------


def test_canonical_picker_prefers_marked_canonical() -> None:
    from bioforge.tools.variants.annotate_variant import VariantConsequence

    rows = [
        VariantConsequence(transcript_id="A", canonical=False, biotype="protein_coding", impact="HIGH"),
        VariantConsequence(transcript_id="B", canonical=True, biotype="protein_coding", impact="MODERATE"),
        VariantConsequence(transcript_id="C", canonical=False, biotype="protein_coding", impact="HIGH"),
    ]
    pick = _pick_canonical_consequence(rows)
    assert pick is not None and pick.transcript_id == "B"


def test_canonical_picker_falls_back_to_highest_impact_coding() -> None:
    from bioforge.tools.variants.annotate_variant import VariantConsequence

    rows = [
        VariantConsequence(transcript_id="A", canonical=False, biotype="protein_coding", impact="MODERATE"),
        VariantConsequence(transcript_id="B", canonical=False, biotype="protein_coding", impact="HIGH"),
        VariantConsequence(transcript_id="C", canonical=False, biotype="retained_intron", impact="HIGH"),
    ]
    pick = _pick_canonical_consequence(rows)
    assert pick is not None and pick.transcript_id == "B"


def test_canonical_picker_returns_none_on_empty() -> None:
    assert _pick_canonical_consequence([]) is None


# --- Registry -----------------------------------------------------------------------


async def test_tool_registered_with_correct_tags_and_citations() -> None:
    from bioforge.tools.registry import get_tool

    spec = get_tool("annotate_variant")
    assert spec.cost_hint == "moderate"
    assert {"variants", "annotation"} <= set(spec.tags)
    citations_blob = " ".join(spec.citations)
    assert "VEP" in citations_blob or "McLaren" in citations_blob
    assert "ClinVar" in citations_blob or "Landrum" in citations_blob
    assert "gnomAD" in citations_blob or "Karczewski" in citations_blob


# --- Composition with parse_vcf ------------------------------------------------------


async def test_composes_with_parse_vcf(monkeypatch: pytest.MonkeyPatch) -> None:
    """Smoke test: a parse_vcf record's chrom/pos/ref/alt can be formatted into an
    HGVS string we'd feed annotate_variant. We don't run the real composition (that
    would touch the network); we just verify the shapes line up — coords from
    parse_vcf are 1-based per VCF convention, matching Ensembl's g. positions."""
    from bioforge.tools.sequence.parse_vcf import ParseVcfInput, parse_vcf

    monkeypatch.setattr(av_module, "_fetch_vep", _stub_fetch_brca1)

    vcf = (
        "##fileformat=VCFv4.2\n"
        "##contig=<ID=17>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "17\t43106487\trs28897672\tT\tG\t.\tPASS\t.\n"
    )
    parsed = await parse_vcf(ParseVcfInput(vcf_text=vcf))
    assert len(parsed.variants) == 1
    v = parsed.variants[0]
    hgvs = f"{v.chrom}:g.{v.pos}{v.ref}>{v.alt[0]}"
    assert hgvs == "17:g.43106487T>G"

    # The agent would now call annotate_variant(hgvs); we exercise the call path.
    out = await annotate_variant(AnnotateVariantInput(hgvs=hgvs))
    assert out.canonical_consequence is not None
    assert out.canonical_consequence.gene_symbol == "BRCA1"


# --- Live integration (opt-in) -------------------------------------------------------


@pytest.mark.online
async def test_real_ensembl_brca1_p_cys61gly() -> None:
    """Hits the real Ensembl REST. Deselected by default; runs on the nightly job."""
    out = await annotate_variant(AnnotateVariantInput(hgvs="ENST00000357654.9:c.181T>G"))

    # Structural assertions only — Ensembl re-releases periodically, so exact
    # transcript counts will drift. The biology is stable: BRCA1, missense, C61G.
    assert out.canonical_consequence is not None
    assert out.canonical_consequence.gene_symbol == "BRCA1"
    assert out.most_severe_consequence == "missense_variant"
    assert any(cv.id.startswith("rs") for cv in out.colocated_variants), "Expected at least one dbSNP rsID"
    # The variant is well-established as (likely_)pathogenic in ClinVar.
    sig = {s.lower() for s in out.clinvar_significance}
    assert "pathogenic" in sig or "likely_pathogenic" in sig
