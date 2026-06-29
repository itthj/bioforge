"""Search PubMed for relevant biomedical literature.

Scientists need literature context at every stage of analysis — before designing
a CRISPR experiment, before interpreting a variant, before characterising a
protein. This tool brings that context directly into the BioForge agent loop
so the analyst never has to leave the interface to answer "what's known about X".

Uses the NCBI Entrez eUtils API:
  - esearch: converts a free-text query to a ranked list of PMIDs
  - efetch: retrieves structured abstracts for those PMIDs

No API key required for up to 3 requests/second. We stay well under that.
"""

from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from typing import Any

import httpx
from pydantic import Field, field_validator

from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import register_tool

_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
_EFETCH  = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
_ELINK   = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi"


class SearchPubmedInput(ToolInput):
    query: str = Field(
        ...,
        min_length=2,
        max_length=500,
        description=(
            "PubMed search query. Supports full PubMed syntax: field tags "
            "[ti], [ab], [au], Boolean operators AND/OR/NOT, MeSH terms. "
            "Examples: 'CRISPR VEGFA off-target', 'BRCA1[ti] AND cancer[mesh]', "
            "'COVID-19 spike protein neutralisation antibody 2024:2025[dp]'"
        ),
    )
    max_results: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Number of papers to return, ranked by PubMed relevance (default 5).",
    )
    sort: str = Field(
        default="relevance",
        description="Ranking strategy: 'relevance' (default) or 'pub_date' (most recent first).",
    )

    @field_validator("sort")
    @classmethod
    def validate_sort(cls, v: str) -> str:
        allowed = {"relevance", "pub_date"}
        if v not in allowed:
            raise ValueError(f"sort must be one of {allowed}")
        return v


class PubmedArticle(ToolOutput):
    pmid: str
    title: str
    authors: str = Field(description="First author et al., or full list if ≤3 authors.")
    journal: str
    year: str
    abstract: str = Field(description="Full abstract, or empty string if not available.")
    doi: str = Field(default="", description="DOI if available in the PubMed record.")
    pubmed_url: str


class SearchPubmedOutput(ToolOutput):
    query: str
    total_hits: int = Field(description="Total PubMed records matching the query (may exceed max_results).")
    articles: list[PubmedArticle]
    caveats: list[str] = Field(default_factory=list)


# ─── HTTP helpers ──────────────────────────────────────────────────────────────

async def _esearch(query: str, max_results: int, sort: str) -> tuple[list[str], int]:
    """Return (list of PMIDs, total_count)."""
    sort_param = "relevance" if sort == "relevance" else "pub+date"
    params = {
        "db": "pubmed",
        "term": query,
        "retmax": max_results,
        "retmode": "json",
        "sort": sort_param,
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            r = await client.get(_ESEARCH, params=params)
        except httpx.HTTPError as e:
            raise ToolError(f"PubMed esearch unreachable: {e}") from e
    if r.status_code != 200:
        raise ToolError(f"PubMed esearch HTTP {r.status_code}: {r.text[:200]}")
    data = r.json().get("esearchresult", {})
    pmids = data.get("idlist", [])
    total = int(data.get("count", 0))
    return pmids, total


async def _efetch(pmids: list[str]) -> str:
    """Return raw PubMed XML for a list of PMIDs."""
    if not pmids:
        return "<PubmedArticleSet/>"
    params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "rettype": "abstract",
        "retmode": "xml",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            r = await client.get(_EFETCH, params=params)
        except httpx.HTTPError as e:
            raise ToolError(f"PubMed efetch unreachable: {e}") from e
    if r.status_code != 200:
        raise ToolError(f"PubMed efetch HTTP {r.status_code}")
    return r.text


# ─── XML parsing ───────────────────────────────────────────────────────────────

def _text(el: ET.Element | None, *tags: str) -> str:
    """Walk a tag path and return stripped text, or empty string."""
    node = el
    for tag in tags:
        if node is None:
            return ""
        node = node.find(tag)
    return (node.text or "").strip() if node is not None else ""


def _parse_articles(xml_text: str) -> list[PubmedArticle]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    articles: list[PubmedArticle] = []
    for art in root.findall(".//PubmedArticle"):
        medline = art.find("MedlineCitation")
        if medline is None:
            continue

        pmid = _text(medline, "PMID")
        article_el = medline.find("Article")
        if article_el is None:
            continue

        title = _text(article_el, "ArticleTitle")

        # Authors
        author_list = article_el.find("AuthorList")
        authors_raw: list[str] = []
        if author_list is not None:
            for author in author_list.findall("Author"):
                last = _text(author, "LastName")
                fore = _text(author, "ForeName") or _text(author, "Initials")
                if last:
                    authors_raw.append(f"{last} {fore}".strip())
        if len(authors_raw) == 0:
            authors_str = "Unknown"
        elif len(authors_raw) <= 3:
            authors_str = ", ".join(authors_raw)
        else:
            authors_str = f"{authors_raw[0]} et al."

        # Journal + year
        journal_info = article_el.find("Journal")
        journal_name = ""
        year = ""
        if journal_info is not None:
            journal_name = _text(journal_info, "Title") or _text(journal_info, "ISOAbbreviation")
            ji = journal_info.find("JournalIssue")
            if ji is not None:
                pub_date = ji.find("PubDate")
                if pub_date is not None:
                    year = _text(pub_date, "Year") or _text(pub_date, "MedlineDate")[:4]

        # Abstract
        abstract_parts: list[str] = []
        abstract_el = article_el.find("Abstract")
        if abstract_el is not None:
            for part in abstract_el.findall("AbstractText"):
                label = part.get("Label", "")
                text = (part.text or "").strip()
                if label:
                    abstract_parts.append(f"{label}: {text}")
                elif text:
                    abstract_parts.append(text)
        abstract = " ".join(abstract_parts)

        # DOI
        doi = ""
        for id_el in art.findall(".//ArticleId"):
            if id_el.get("IdType") == "doi":
                doi = (id_el.text or "").strip()
                break

        if pmid:
            articles.append(PubmedArticle(
                pmid=pmid,
                title=title or "(no title)",
                authors=authors_str,
                journal=journal_name,
                year=year,
                abstract=abstract,
                doi=doi,
                pubmed_url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            ))
    return articles


# ─── Tool ──────────────────────────────────────────────────────────────────────

@register_tool(
    name="search_pubmed",
    description=(
        "Search PubMed for peer-reviewed biomedical literature and return "
        "ranked articles with titles, authors, abstracts, and DOIs. Use when "
        "the user asks about published research on a gene, protein, disease, "
        "technique, or drug — e.g. 'what papers describe CRISPR editing of "
        "VEGFA', 'find reviews on BRCA1 and breast cancer', 'latest trials on "
        "pembrolizumab'. Also useful for providing literature context before or "
        "after a computational analysis (e.g. after BLAST hits, search PubMed "
        "for the top-hit organism; after variant annotation, search for papers "
        "describing the variant). Returns up to 20 papers; default is 5."
    ),
    input_model=SearchPubmedInput,
    output_model=SearchPubmedOutput,
    version="1.0.0",
    citations=[
        "NCBI Entrez eUtils API (https://www.ncbi.nlm.nih.gov/books/NBK25500/)",
        "Sayers E (2010) A General Introduction to the E-utilities. NCBI.",
    ],
    cost_hint="cheap",
    tags=["knowledge", "literature", "pubmed", "ncbi"],
)
async def search_pubmed(inp: SearchPubmedInput) -> SearchPubmedOutput:
    pmids, total = await _esearch(inp.query, inp.max_results, inp.sort)

    caveats: list[str] = []
    if total == 0:
        caveats.append(
            "No PubMed records matched this query. Try broadening the search "
            "terms or removing field tags ([ti], [ab])."
        )
        return SearchPubmedOutput(query=inp.query, total_hits=0, articles=[], caveats=caveats)

    xml_text = await _efetch(pmids)
    articles = _parse_articles(xml_text)

    if total > inp.max_results:
        caveats.append(
            f"Query matched {total:,} records; showing the top {len(articles)} "
            f"by {inp.sort}. Increase max_results (up to 20) or refine the query "
            "to see different papers."
        )
    caveats.append(
        "Abstracts are retrieved verbatim from PubMed. Full-text access depends "
        "on your institutional subscriptions or open-access status of each article."
    )

    return SearchPubmedOutput(
        query=inp.query,
        total_hits=total,
        articles=articles,
        caveats=caveats,
    )
