"""Tests for the new knowledge and functional tool modules.

All tests are offline-safe: API calls are patched so the test suite
runs without network access.  Only the parsing and tool-registration
logic is exercised here; integration / online tests live in separate
*_online.py files.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bioforge.tools import REGISTRY


# ─── Registry smoke-tests ────────────────────────────────────────────────────

def test_all_new_tools_registered():
    expected = {
        "search_pubmed",
        "fetch_gene_info",
        "string_network",
        "open_targets",
        "drug_gene_interaction",
        "gwas_catalog",
        "go_enrichment",
        "differential_expression",
    }
    missing = expected - set(REGISTRY)
    assert not missing, f"Tools not registered: {missing}"


def test_new_tools_have_required_fields():
    new_tools = [
        "search_pubmed", "fetch_gene_info", "string_network",
        "open_targets", "drug_gene_interaction", "gwas_catalog",
        "go_enrichment", "differential_expression",
    ]
    for name in new_tools:
        spec = REGISTRY[name]
        assert spec.name == name
        assert spec.description, f"{name}: empty description"
        assert spec.version, f"{name}: empty version"
        assert spec.citations, f"{name}: no citations"
        assert spec.tags, f"{name}: no tags"


def test_new_tools_have_correct_tag_categories():
    knowledge_tools = [
        "search_pubmed", "fetch_gene_info", "string_network",
        "open_targets", "drug_gene_interaction", "gwas_catalog",
    ]
    for name in knowledge_tools:
        spec = REGISTRY[name]
        assert "knowledge" in spec.tags or any(
            t in spec.tags for t in ["literature", "gene", "network", "disease", "gwas", "drug"]
        ), f"{name}: missing expected tag"

    functional_tools = ["go_enrichment", "differential_expression"]
    for name in functional_tools:
        spec = REGISTRY[name]
        assert "functional" in spec.tags or any(
            t in spec.tags for t in ["go", "pathway", "rnaseq", "enrichment"]
        ), f"{name}: missing functional tag"


# ─── search_pubmed ───────────────────────────────────────────────────────────

def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def pubmed_esearch_response():
    return {"esearchresult": {"idlist": ["12345678", "23456789"], "count": "2"}}


@pytest.fixture
def pubmed_efetch_xml():
    return """<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>12345678</PMID>
      <Article>
        <ArticleTitle>CRISPR editing of VEGFA</ArticleTitle>
        <AuthorList>
          <Author><LastName>Smith</LastName><ForeName>A</ForeName></Author>
        </AuthorList>
        <Journal>
          <Title>Nature</Title>
          <JournalIssue><PubDate><Year>2024</Year></PubDate></JournalIssue>
        </Journal>
        <Abstract><AbstractText>Test abstract.</AbstractText></Abstract>
      </Article>
    </MedlineCitation>
    <PubmedData>
      <ArticleIdList>
        <ArticleId IdType="doi">10.1038/test</ArticleId>
      </ArticleIdList>
    </PubmedData>
  </PubmedArticle>
</PubmedArticleSet>"""


def test_search_pubmed_returns_articles(pubmed_esearch_response, pubmed_efetch_xml):
    from bioforge.tools.knowledge.search_pubmed import SearchPubmedInput, search_pubmed

    mock_esearch = AsyncMock(return_value=(["12345678"], 1))
    mock_efetch  = AsyncMock(return_value=pubmed_efetch_xml)

    with patch("bioforge.tools.knowledge.search_pubmed._esearch", mock_esearch), \
         patch("bioforge.tools.knowledge.search_pubmed._efetch", mock_efetch):
        result = run(search_pubmed(SearchPubmedInput(query="CRISPR VEGFA", max_results=5)))

    assert result.total_hits == 1
    assert len(result.articles) == 1
    assert result.articles[0].title == "CRISPR editing of VEGFA"
    assert result.articles[0].pmid == "12345678"
    assert result.articles[0].year == "2024"
    assert result.articles[0].doi == "10.1038/test"
    assert "pubmed.ncbi.nlm.nih.gov" in result.articles[0].pubmed_url


def test_search_pubmed_zero_results():
    from bioforge.tools.knowledge.search_pubmed import SearchPubmedInput, search_pubmed

    with patch("bioforge.tools.knowledge.search_pubmed._esearch",
               AsyncMock(return_value=([], 0))):
        result = run(search_pubmed(SearchPubmedInput(query="xyzzynonexistentquery")))

    assert result.total_hits == 0
    assert result.articles == []
    assert any("No PubMed records" in c for c in result.caveats)


# ─── fetch_gene_info ─────────────────────────────────────────────────────────

_GENE_SUMMARY = {
    "nomenclaturesymbol": "BRCA1",
    "nomenclaturename": "BRCA1 DNA repair associated",
    "organism": {"scientificname": "Homo sapiens"},
    "chromosome": "17",
    "maplocation": "17q21.31",
    "summary": "This gene encodes a nuclear phosphoprotein...",
    "otheraliases": "BRCAI|BRCC1|IRIS",
    "otherdesignations": "",
}


def test_fetch_gene_info_returns_expected_fields():
    from bioforge.tools.knowledge.fetch_gene_info import FetchGeneInfoInput, fetch_gene_info

    with patch("bioforge.tools.knowledge.fetch_gene_info._search_gene",
               AsyncMock(return_value="672")), \
         patch("bioforge.tools.knowledge.fetch_gene_info._summarise_gene",
               AsyncMock(return_value=_GENE_SUMMARY)):
        result = run(fetch_gene_info(FetchGeneInfoInput(gene_symbol="BRCA1")))

    assert result.gene_id == "672"
    assert result.symbol == "BRCA1"
    assert result.chromosome == "17"
    assert result.map_location == "17q21.31"
    assert "BRCAI" in result.aliases
    assert "ncbi.nlm.nih.gov/gene/672" in result.ncbi_url


def test_fetch_gene_info_not_found():
    from bioforge.tools.base import ToolError
    from bioforge.tools.knowledge.fetch_gene_info import FetchGeneInfoInput, fetch_gene_info

    with patch("bioforge.tools.knowledge.fetch_gene_info._search_gene",
               AsyncMock(return_value=None)):
        with pytest.raises(ToolError, match="No NCBI Gene entry"):
            run(fetch_gene_info(FetchGeneInfoInput(gene_symbol="XYZNOTEXIST")))


# ─── string_network ──────────────────────────────────────────────────────────

_STRING_RESOLVE = [{"stringId": "9606.ENSP00000380152", "preferredName": "BRCA1"}]
_STRING_INTERACTIONS = [
    {
        "preferredName_A": "BRCA1",
        "preferredName_B": "BARD1",
        "score": 0.999,
        "escore": 0.9,
        "coexpressionscore": 0.7,
        "tscore": 0.6,
        "dscore": 0.8,
    },
    {
        "preferredName_A": "BRCA1",
        "preferredName_B": "PALB2",
        "score": 0.980,
        "escore": 0.85,
        "coexpressionscore": 0.5,
        "tscore": 0.7,
        "dscore": 0.75,
    },
]


def test_string_network_returns_interactions():
    from bioforge.tools.knowledge.string_network import StringNetworkInput, string_network

    with patch("bioforge.tools.knowledge.string_network._resolve_protein",
               AsyncMock(return_value=("9606.ENSP00000380152", "BRCA1"))), \
         patch("bioforge.tools.knowledge.string_network._get_interactions",
               AsyncMock(return_value=_STRING_INTERACTIONS)):
        result = run(string_network(StringNetworkInput(protein="BRCA1")))

    assert result.query_protein == "BRCA1"
    assert result.n_interactors >= 2
    assert result.interactions[0].partner_name == "BARD1"
    assert result.interactions[0].combined_score == 999


def test_string_network_not_resolved():
    from bioforge.tools.base import ToolError
    from bioforge.tools.knowledge.string_network import StringNetworkInput, string_network

    with patch("bioforge.tools.knowledge.string_network._resolve_protein",
               AsyncMock(return_value=None)):
        with pytest.raises(ToolError, match="STRING could not resolve"):
            run(string_network(StringNetworkInput(protein="XYZNOTEXIST")))


# ─── go_enrichment ───────────────────────────────────────────────────────────

_GPROFILER_RESPONSE = {
    "result": [{
        "meta": {"genes_metadata": {"query": {"BRCA1": 1, "TP53": 2, "VEGFA": 3}}},
        "result": [
            {
                "native": "GO:0006281",
                "name": "DNA repair",
                "source": "GO:BP",
                "p_value": 0.0001,
                "intersection_size": 3,
                "term_size": 450,
                "intersections": ["BRCA1", "TP53", "VEGFA"],
            },
            {
                "native": "hsa04110",
                "name": "Cell cycle",
                "source": "KEGG",
                "p_value": 0.002,
                "intersection_size": 2,
                "term_size": 124,
                "intersections": ["TP53", "VEGFA"],
            },
        ]
    }]
}


def test_go_enrichment_returns_terms():
    from bioforge.tools.functional.go_enrichment import GoEnrichmentInput, go_enrichment

    with patch("bioforge.tools.functional.go_enrichment._run_gprofiler",
               AsyncMock(return_value=_GPROFILER_RESPONSE)):
        result = run(go_enrichment(GoEnrichmentInput(
            genes=["BRCA1", "TP53", "VEGFA"],
        )))

    assert result.n_significant_terms == 2
    assert result.terms[0].term_id == "GO:0006281"
    assert result.terms[0].term_name == "DNA repair"
    assert result.terms[0].source == "GO:BP"
    assert result.n_genes_mapped == 3


def test_go_enrichment_no_results():
    from bioforge.tools.functional.go_enrichment import GoEnrichmentInput, go_enrichment

    empty = {"result": [{"meta": {"genes_metadata": {"query": {}}}, "result": []}]}
    with patch("bioforge.tools.functional.go_enrichment._run_gprofiler",
               AsyncMock(return_value=empty)):
        result = run(go_enrichment(GoEnrichmentInput(genes=["BRCA1", "TP53"])))

    assert result.n_significant_terms == 0
    assert any("No significant enrichment" in c for c in result.caveats)


def test_go_enrichment_validates_min_genes():
    from bioforge.tools.functional.go_enrichment import GoEnrichmentInput
    with pytest.raises(Exception):
        GoEnrichmentInput(genes=["BRCA1"])


# ─── drug_gene_interaction ───────────────────────────────────────────────────

_DGIDB_INTERACTIONS = [
    {
        "drug": {"name": "Olaparib", "conceptId": "DB09074", "approved": True},
        "interactionTypes": [{"type": "inhibitor", "directionality": "inhibitory"}],
        "interactionScore": 8.5,
        "sources": [{"sourceDbName": "CIViC"}, {"sourceDbName": "DrugBank"}],
    },
    {
        "drug": {"name": "Talazoparib", "conceptId": "DB11748", "approved": True},
        "interactionTypes": [{"type": "inhibitor", "directionality": "inhibitory"}],
        "interactionScore": 7.2,
        "sources": [{"sourceDbName": "ChEMBL"}],
    },
]


def test_drug_gene_interaction_returns_drugs():
    from bioforge.tools.knowledge.drug_gene_interaction import DrugGeneInput, drug_gene_interaction

    with patch("bioforge.tools.knowledge.drug_gene_interaction._fetch_dgidb",
               AsyncMock(return_value=_DGIDB_INTERACTIONS)):
        result = run(drug_gene_interaction(DrugGeneInput(gene_symbol="BRCA1")))

    assert result.n_interactions == 2
    assert result.n_approved_drugs == 2
    assert result.interactions[0].drug_name == "Olaparib"
    assert result.interactions[0].approved is True
    assert "inhibitor" in result.interactions[0].interaction_type


def test_drug_gene_interaction_empty():
    from bioforge.tools.knowledge.drug_gene_interaction import DrugGeneInput, drug_gene_interaction

    with patch("bioforge.tools.knowledge.drug_gene_interaction._fetch_dgidb",
               AsyncMock(return_value=[])):
        result = run(drug_gene_interaction(DrugGeneInput(gene_symbol="UNKNOWNGENE")))

    assert result.n_interactions == 0
    assert any("No drug-gene interactions" in c for c in result.caveats)


# ─── differential_expression ─────────────────────────────────────────────────

_COUNTS = {
    "BRCA1": {"ctrl_1": 245, "ctrl_2": 312, "treat_1": 89, "treat_2": 102},
    "TP53":  {"ctrl_1": 100, "ctrl_2": 120, "treat_1": 250, "treat_2": 280},
    "VEGFA": {"ctrl_1": 50,  "ctrl_2": 60,  "treat_1": 55,  "treat_2": 58},
    "EGFR":  {"ctrl_1": 300, "ctrl_2": 290, "treat_1": 45,  "treat_2": 40},
    "MYC":   {"ctrl_1": 80,  "ctrl_2": 90,  "treat_1": 400, "treat_2": 380},
}

_CONDITIONS = {
    "ctrl_1": "control", "ctrl_2": "control",
    "treat_1": "treatment", "treat_2": "treatment",
}

_FAKE_DE_RECORDS = [
    {"gene": "BRCA1", "base_mean": 187.0, "log2_fold_change": -1.47, "lfc_se": 0.12, "stat": -12.3, "p_value": 1e-12, "p_adj": 5e-11},
    {"gene": "TP53",  "base_mean": 187.5, "log2_fold_change":  1.35, "lfc_se": 0.11, "stat":  12.1, "p_value": 2e-11, "p_adj": 8e-10},
    {"gene": "VEGFA", "base_mean":  55.75, "log2_fold_change": 0.12, "lfc_se": 0.20, "stat":   0.6, "p_value": 0.55,  "p_adj": 0.7},
    {"gene": "EGFR",  "base_mean": 168.75, "log2_fold_change": -2.8, "lfc_se": 0.13, "stat": -21.5, "p_value": 1e-20, "p_adj": 4e-19},
    {"gene": "MYC",   "base_mean": 237.5,  "log2_fold_change":  2.3, "lfc_se": 0.10, "stat":  23.0, "p_value": 5e-22, "p_adj": 2e-20},
]


def test_differential_expression_returns_results():
    from bioforge.tools.functional.differential_expression import (
        DifferentialExpressionInput,
        differential_expression,
    )

    with patch(
        "bioforge.tools.functional.differential_expression._run_deseq2_sync",
        return_value=_FAKE_DE_RECORDS,
    ):
        result = run(differential_expression(DifferentialExpressionInput(
            counts=_COUNTS,
            conditions=_CONDITIONS,
            reference_condition="control",
        )))

    assert result.n_genes_tested == 5
    assert result.n_significant >= 3  # BRCA1 (down), EGFR (down), MYC (up) all > 2-fold
    assert result.comparison == "treatment vs control"
    assert any(r.gene == "EGFR" for r in result.results)
    # VEGFA should not be significant (small fold change)
    vegfa = next(r for r in result.results if r.gene == "VEGFA")
    assert not vegfa.significant


def test_differential_expression_validates_conditions():
    from bioforge.tools.functional.differential_expression import DifferentialExpressionInput

    # Should fail if only 1 condition
    with pytest.raises(Exception):
        DifferentialExpressionInput(
            counts=_COUNTS,
            conditions={"ctrl_1": "control", "ctrl_2": "control"},
            reference_condition="control",
        )


def test_differential_expression_validates_min_genes():
    from bioforge.tools.functional.differential_expression import DifferentialExpressionInput

    with pytest.raises(Exception):
        DifferentialExpressionInput(
            counts={"BRCA1": {"c": 10, "t": 20}, "TP53": {"c": 5, "t": 8}},
            conditions={"c": "control", "t": "treatment"},
            reference_condition="control",
        )
