"""Tests for lookup_gnomad.

gnomAD is never hit in the hot suite. `_fetch_gnomad` is monkeypatched and
returns the committed BRCA1 c.5266dupC fixture — the Ashkenazi-founder
frameshift, the textbook example of per-population AF enrichment in gnomAD.
One @pytest.mark.online test hits the live GraphQL for the nightly job.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pydantic
import pytest
from bioforge.tools.base import ToolError
from bioforge.tools.variants import lookup_gnomad as lg_module
from bioforge.tools.variants.lookup_gnomad import (
    LookupGnomadInput,
    _af_from_counts,
    _map_cohort,
    _map_population,
    _map_record,
    lookup_gnomad,
)

FIXTURE = Path(__file__).parent / "fixtures" / "gnomad_brca1_5266dupc.json"


def _load_fixture() -> dict[str, Any]:
    with FIXTURE.open("r", encoding="utf-8") as f:
        return json.load(f)


def _variant_dict() -> dict[str, Any]:
    """The data.variant sub-dict — the shape _fetch_gnomad returns."""
    return _load_fixture()["data"]["variant"]


# --- Input validation --------------------------------------------------------------


def test_input_accepts_canonical_form() -> None:
    inp = LookupGnomadInput(variant_id="17-43057062-T-TG")
    assert inp.variant_id == "17-43057062-T-TG"
    assert inp.dataset == "gnomad_r4"  # default


def test_input_accepts_snv() -> None:
    LookupGnomadInput(variant_id="11-5227002-T-A")  # HBB sickle


def test_input_accepts_sex_chromosomes() -> None:
    LookupGnomadInput(variant_id="X-12345-A-G")
    LookupGnomadInput(variant_id="Y-12345-A-G")


def test_input_accepts_mitochondrial() -> None:
    LookupGnomadInput(variant_id="MT-100-A-G")
    LookupGnomadInput(variant_id="M-100-A-G")


def test_input_strips_whitespace() -> None:
    inp = LookupGnomadInput(variant_id="  17-43057062-T-TG  ")
    assert inp.variant_id == "17-43057062-T-TG"


def test_input_rejects_empty() -> None:
    with pytest.raises(pydantic.ValidationError):
        LookupGnomadInput(variant_id="")


def test_input_rejects_hgvs() -> None:
    with pytest.raises(pydantic.ValidationError, match="chrom-pos-ref-alt"):
        LookupGnomadInput(variant_id="NM_007294.4:c.5266dupC")


def test_input_rejects_rsid() -> None:
    with pytest.raises(pydantic.ValidationError, match="chrom-pos-ref-alt"):
        LookupGnomadInput(variant_id="rs80357906")


def test_input_rejects_non_canonical_bases() -> None:
    """N, *, etc. are sometimes used in upstream pipelines but rejected here."""
    with pytest.raises(pydantic.ValidationError, match="chrom-pos-ref-alt"):
        LookupGnomadInput(variant_id="17-43057062-T-N")


def test_input_rejects_chr_prefix() -> None:
    with pytest.raises(pydantic.ValidationError):
        LookupGnomadInput(variant_id="chr17-43057062-T-TG")


def test_input_rejects_invalid_chromosome() -> None:
    with pytest.raises(pydantic.ValidationError):
        LookupGnomadInput(variant_id="23-100-A-G")


def test_dataset_choices_enforced() -> None:
    LookupGnomadInput(variant_id="17-43057062-T-TG", dataset="gnomad_r4")
    LookupGnomadInput(variant_id="17-43057062-T-TG", dataset="gnomad_r3")
    LookupGnomadInput(variant_id="17-43057062-T-TG", dataset="gnomad_r2_1")
    with pytest.raises(pydantic.ValidationError):
        LookupGnomadInput(variant_id="17-43057062-T-TG", dataset="gnomad_r5")  # type: ignore[arg-type]


# --- _af_from_counts ---------------------------------------------------------------


def test_af_from_counts_canonical() -> None:
    assert _af_from_counts(101, 1461732) == pytest.approx(101 / 1461732)


def test_af_from_counts_zero_an_returns_none() -> None:
    """gnomAD omits af when an=0; we mirror that with None rather than ZeroDivisionError."""
    assert _af_from_counts(0, 0) is None


def test_af_from_counts_zero_ac_returns_zero() -> None:
    """0 / N = 0.0, NOT None — distinguishes 'no alleles seen' from 'no chromosomes assayed'."""
    assert _af_from_counts(0, 1000) == 0.0


# --- _map_population ---------------------------------------------------------------


def test_map_population_canonical() -> None:
    p = _map_population({"id": "asj", "ac": 31, "an": 26128})
    assert p is not None
    assert p.id == "asj"
    assert p.ac == 31
    assert p.an == 26128
    assert p.af == pytest.approx(31 / 26128)


def test_map_population_zero_an_yields_none_af() -> None:
    p = _map_population({"id": "ami", "ac": 0, "an": 0})
    assert p is not None
    assert p.af is None


def test_map_population_missing_id_returns_none() -> None:
    assert _map_population({"ac": 1, "an": 100}) is None


def test_map_population_malformed_counts_returns_none() -> None:
    assert _map_population({"id": "afr", "ac": "garbage", "an": 100}) is None


# --- _map_cohort -------------------------------------------------------------------


def test_map_cohort_exome_brca1() -> None:
    cohort = _map_cohort(_variant_dict()["exome"])
    assert cohort is not None
    assert cohort.ac == 101
    assert cohort.an == 1461732
    assert cohort.af == pytest.approx(101 / 1461732)
    assert cohort.filters == []  # variant passes QC
    # 30 strata in r4 exome (ancestries + sex subdivisions + global XX/XY).
    assert len(cohort.populations) > 20


def test_map_cohort_none_input_returns_none() -> None:
    assert _map_cohort(None) is None


def test_map_cohort_extracts_ashkenazi_population() -> None:
    """The headline biological assertion — ASJ enrichment for the founder mutation."""
    cohort = _map_cohort(_variant_dict()["exome"])
    assert cohort is not None
    asj = next(p for p in cohort.populations if p.id == "asj")
    assert asj.ac == 31
    assert asj.an == 26128
    assert asj.af is not None
    # ASJ AF should be ~17x global AF — the founder signal.
    global_af = cohort.af
    assert global_af is not None
    assert asj.af / global_af > 10, (
        f"ASJ enrichment ratio for BRCA1 founder mutation must be > 10x; got {asj.af / global_af:.1f}x"
    )


def test_map_cohort_genome_present_for_brca1() -> None:
    cohort = _map_cohort(_variant_dict()["genome"])
    assert cohort is not None
    assert cohort.ac == 8
    assert cohort.an == 152180


# --- _map_record -------------------------------------------------------------------


def test_map_record_full_shape() -> None:
    rec = _map_record(_variant_dict(), "gnomad_r4")
    assert rec.variant_id == "17-43057062-T-TG"
    assert rec.reference_genome == "GRCh38"
    assert rec.chrom == "17"
    assert rec.pos == 43057062
    assert rec.ref == "T"
    assert rec.alt == "TG"
    assert "rs80357906" in rec.rsids
    assert rec.flags == []
    assert rec.exome is not None
    assert rec.genome is not None
    assert rec.gnomad_url == "https://gnomad.broadinstitute.org/variant/17-43057062-T-TG?dataset=gnomad_r4"


def test_map_record_reference_genome_fallback_from_dataset() -> None:
    """When the API omits reference_genome we fall back to the dataset's known build."""
    raw = {"variant_id": "1-100-A-G", "chrom": "1", "pos": 100, "ref": "A", "alt": "G"}
    rec = _map_record(raw, "gnomad_r2_1")
    assert rec.reference_genome == "GRCh37"


# --- End-to-end (monkeypatched fetcher) --------------------------------------------


async def test_end_to_end_brca1_dupc(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[str, str]] = []

    async def fake_fetch(variant_id: str, dataset: str) -> dict[str, Any]:
        captured.append((variant_id, dataset))
        return _variant_dict()

    monkeypatch.setattr(lg_module, "_fetch_gnomad", fake_fetch)

    out = await lookup_gnomad(LookupGnomadInput(variant_id="17-43057062-T-TG"))

    assert captured == [("17-43057062-T-TG", "gnomad_r4")]
    assert out.variant_id == "17-43057062-T-TG"
    assert out.dataset == "gnomad_r4"
    assert out.record.exome is not None
    assert out.record.exome.ac == 101
    # Caveats include the 5 base entries; no extra filter caveats since variant passes QC.
    assert len(out.caveats) == 5


async def test_filter_failures_emit_extra_caveats(monkeypatch: pytest.MonkeyPatch) -> None:
    """A flagged-filter variant gets an extra caveat per cohort that's filtered."""
    flagged = json.loads(json.dumps(_variant_dict()))  # deep copy
    flagged["exome"]["filters"] = ["AC0"]
    flagged["genome"]["filters"] = ["RF"]

    async def fake_fetch(variant_id: str, dataset: str) -> dict[str, Any]:
        return flagged

    monkeypatch.setattr(lg_module, "_fetch_gnomad", fake_fetch)

    out = await lookup_gnomad(LookupGnomadInput(variant_id="17-43057062-T-TG"))
    assert any("'AC0'" in c for c in out.caveats)
    assert any("'RF'" in c for c in out.caveats)


# --- Error paths -------------------------------------------------------------------


async def test_variant_not_found_raises_actionable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """gnomAD's 'Variant not found' arrives in errors[] inside a 200 response."""
    import httpx

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            return httpx.Response(
                status_code=200,
                content=b'{"errors":[{"message":"Variant not found"}],"data":{"variant":null}}',
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(lg_module.httpx, "AsyncClient", FakeClient)
    with pytest.raises(ToolError, match="no record"):
        await lookup_gnomad(LookupGnomadInput(variant_id="17-99999999-A-G"))


async def test_graphql_other_error_surfaces_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-'not found' GraphQL errors propagate the message rather than getting swallowed as not-found."""
    import httpx

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            return httpx.Response(
                status_code=200,
                content=b'{"errors":[{"message":"Dataset not available"}],"data":{"variant":null}}',
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(lg_module.httpx, "AsyncClient", FakeClient)
    with pytest.raises(ToolError, match="Dataset not available"):
        await lookup_gnomad(LookupGnomadInput(variant_id="17-43057062-T-TG"))


async def test_null_variant_without_errors_raises_tool_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Belt-and-suspenders: variant=null even without an errors[] array still surfaces clearly."""
    import httpx

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            return httpx.Response(
                status_code=200,
                content=b'{"data":{"variant":null}}',
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(lg_module.httpx, "AsyncClient", FakeClient)
    with pytest.raises(ToolError, match="null variant"):
        await lookup_gnomad(LookupGnomadInput(variant_id="17-43057062-T-TG"))


async def test_http_429_surfaces_rate_limit_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            return httpx.Response(status_code=429, content=b"slow down", request=httpx.Request("POST", url))

    monkeypatch.setattr(lg_module.httpx, "AsyncClient", FakeClient)
    with pytest.raises(ToolError, match="rate-limited"):
        await lookup_gnomad(LookupGnomadInput(variant_id="17-43057062-T-TG"))


async def test_http_500_surfaces_generic_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            return httpx.Response(status_code=500, content=b"upstream busy", request=httpx.Request("POST", url))

    monkeypatch.setattr(lg_module.httpx, "AsyncClient", FakeClient)
    with pytest.raises(ToolError, match="HTTP 500"):
        await lookup_gnomad(LookupGnomadInput(variant_id="17-43057062-T-TG"))


async def test_non_json_response_raises_tool_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            return httpx.Response(
                status_code=200,
                content=b"<html>nginx error</html>",
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(lg_module.httpx, "AsyncClient", FakeClient)
    with pytest.raises(ToolError, match="non-JSON"):
        await lookup_gnomad(LookupGnomadInput(variant_id="17-43057062-T-TG"))


async def test_network_error_raises_tool_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            raise httpx.ConnectError("network unreachable")

    monkeypatch.setattr(lg_module.httpx, "AsyncClient", FakeClient)
    with pytest.raises(ToolError, match="unreachable"):
        await lookup_gnomad(LookupGnomadInput(variant_id="17-43057062-T-TG"))


# --- Registry --------------------------------------------------------------------


async def test_tool_registered_with_correct_metadata() -> None:
    from bioforge.tools.registry import get_tool

    spec = get_tool("lookup_gnomad")
    assert spec.cost_hint == "moderate"
    assert {"variants", "annotation", "gnomad", "frequency"} <= set(spec.tags)
    assert any("Karczewski" in c or "gnomAD" in c for c in spec.citations)
    assert any("Chen" in c or "v4" in c for c in spec.citations)
    assert spec.version == "1.0.0"
    assert spec.destructive is False


# --- Live integration (opt-in) ----------------------------------------------------


@pytest.mark.online
async def test_live_brca1_dupc_lookup_returns_asj_enrichment() -> None:
    """Hits real gnomAD GraphQL. Deselected by default; nightly job runs it.
    The ASJ founder-enrichment ratio for this variant is biology, not a quirk —
    it'll be stable across r4 minor releases."""
    out = await lookup_gnomad(LookupGnomadInput(variant_id="17-43057062-T-TG"))
    assert out.record.exome is not None
    asj = next((p for p in out.record.exome.populations if p.id == "asj"), None)
    assert asj is not None
    assert asj.af is not None
    assert asj.af > 1e-4  # well above ultra-rare; founder enrichment
