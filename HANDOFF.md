# BioForge — Session Handoff Document
**Created:** 2026-06-29  
**Purpose:** Resume building BioForge exactly where we left off in a new window.  
**Read this fully before doing anything else.**

---

## 1. What BioForge Is

BioForge (`https://github.com/itthj/bioforge`) is an agentic AI bioinformatics platform built by James (`itthj`, `jamesian996@gmail.com`). It has a natural-language chat interface where scientists describe an experiment in plain English and an AI agent plans, executes, and critiques a sequence of bioinformatics tool calls.

**Strategic position:** "The only bioinformatics AI that tells you what it doesn't know — and writes your methods section for you." Its differentiators are:
- Hardcoded benchmark accuracy numbers (never LLM-generated)
- Grounding guard that rejects hallucinated values
- Mandatory accuracy caveats that survive LLM polish
- RO-Crate provenance on every run
- Methods section generator that auto-drafts manuscript text

**The mission we're executing:** Make BioForge the most powerful and comprehensive AI bioinformatics tool available — capable of solving almost every computational biology and bioinformatics problem a scientist faces, with honesty about what it can and can't do.

---

## 2. GitHub Access

- **Repo:** `https://github.com/itthj/bioforge`
- **PAT (classic):** `YOUR_CLASSIC_PAT_HERE`  
  _(Note: This may expire — ask James for a new one if it does)_
- **Push command pattern:**
  ```bash
  git push "https://itthj:YOUR_CLASSIC_PAT_HERE@github.com/itthj/bioforge.git" main
  ```
- **Git identity:**
  ```bash
  git config user.name "itthj"
  git config user.email "jamesian996@gmail.com"
  ```

---

## 3. Local Development Environment

The repo lives in the Claude container at `/home/claude/bioforge`. Clone it fresh at the start of each session:

```bash
git clone https://github.com/itthj/bioforge.git /home/claude/bioforge
cd /home/claude/bioforge
pip install -e ".[dev]" --break-system-packages -q
pip install pydeseq2 biopython --break-system-packages -q
```

Verify tool registration after any changes:
```bash
cd /home/claude/bioforge
python3 -c "from bioforge.tools import REGISTRY; print(f'{len(REGISTRY)} tools registered')"
```

---

## 4. Architecture — How to Add a New Tool

Every tool follows this exact pattern. **Read this before writing any tool.**

### File location
```
backend/src/bioforge/tools/{category}/{tool_name}.py
```
Categories: `sequence/`, `structure/`, `variants/`, `knowledge/`, `functional/`  
For new categories, create `{category}/__init__.py` and add to `tools/__init__.py`.

### Tool template
```python
from pydantic import Field
from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import register_tool

class MyToolInput(ToolInput):
    param: str = Field(..., description="...")

class MyToolOutput(ToolOutput):
    result: str
    caveats: list[str] = Field(default_factory=list)

@register_tool(
    name="my_tool",
    description="One paragraph description the LLM uses to decide when to call this tool.",
    input_model=MyToolInput,
    output_model=MyToolOutput,
    version="1.0.0",
    citations=["Author et al. (Year) Title. Journal vol:pages."],
    cost_hint="cheap",   # "cheap" | "moderate" | "expensive"
    tags=["category", "subcategory"],
    published_accuracy={"benchmark_name": "metric = value (dataset, n=N)"},
)
async def my_tool(inp: MyToolInput) -> MyToolOutput:
    # ... implementation ...
    return MyToolOutput(result="...", caveats=["Always include accuracy caveats."])
```

### Register in category `__init__.py`
```python
from bioforge.tools.{category} import my_tool  # noqa: F401
```

### Register category in `tools/__init__.py`
```python
from bioforge.tools import functional, knowledge, meta, sequence, structure, variants  # noqa: F401
```

### Critical rules
- `ToolOutput` subclasses always have a `caveats: list[str]` field — this is BioForge's honesty mechanism
- For HTTP calls: use `httpx.AsyncClient(timeout=20.0)`, catch `httpx.HTTPError`, raise `ToolError`
- For CPU-heavy sync work: `await asyncio.get_event_loop().run_in_executor(None, sync_fn, args)`
- Never invent accuracy numbers — only cite published benchmarks in `published_accuracy`
- `ToolError` messages are returned verbatim to the LLM — write them as instructions ("try X instead")

---

## 5. Current Tool Count and All 41 Registered Tools

**41 tools** as of the last commit. Here is the complete list:

### Original tools (pre-session, ~30 tools)
**Sequence:** `align_msa`, `blast`, `codon_usage`, `crispr_edit_report`, `design_guides`, `design_primers`, `edit_outcome`, `find_offtargets`, `find_orfs`, `gc_content`, `reverse_complement`, `score_guide_on_target`, `translate`  
**Structure:** `compare_structures`, `fetch_alphafold_structure`, `fetch_interpro_domains`, `fetch_pdb_structure`, `find_best_structure`, `submit_alphafold_batch`  
**Variants:** `annotate_variant`, `call_variants`, `format_hgvs`, `lookup_clinvar`, `lookup_dbsnp`, `lookup_gnomad`, `normalize_hgvs`, `parse_vcf`  
**Meta:** `read_uploaded_file`, `recall_memory`, `remember`

### Added this session (11 new tools)

**Commit `96d0d37` — Methods section generator** (not a tool, a provenance feature):
- `backend/src/bioforge/provenance/methods_draft.py` — auto-generates manuscript methods paragraphs
- `backend/src/bioforge/api/agent.py` — `GET /traces/{id}/methods-draft` endpoint
- `frontend/src/components/MethodsModal.tsx` — "Draft methods ✦" button in FinalCard
- `backend/tests/test_methods_draft.py` — 30 tests

**Commit `0835521` — 8 new knowledge + functional tools:**
- `knowledge/search_pubmed.py` — PubMed literature search (NCBI Entrez eUtils)
- `knowledge/fetch_gene_info.py` — NCBI Gene database lookup
- `knowledge/string_network.py` — STRING protein-protein interactions
- `knowledge/open_targets.py` — Disease-gene associations (OpenTargets GraphQL)
- `knowledge/drug_gene_interaction.py` — Drug-gene interactions (DGIdb GraphQL)
- `knowledge/gwas_catalog.py` — GWAS Catalog variant-trait associations
- `functional/go_enrichment.py` — GO/KEGG/Reactome enrichment (g:Profiler API)
- `functional/differential_expression.py` — RNA-seq DEA (PyDESeq2)

**Commit `e7a5379` — 3 more knowledge tools:**
- `knowledge/fetch_uniprot.py` — UniProt protein function, PTMs, disease, GO
- `knowledge/protein_properties.py` — MW, pI, GRAVY, instability, EC280, tryptic digest
- `knowledge/restriction_sites.py` — 921 restriction enzymes, virtual digest, compatible pairs

---

## 6. What Was Being Built When We Stopped

We were in the middle of **deep research + mass feature building**. The last search was:
```
most common computational biology workflows 2025 bioinformatics survey what scientists need
```

We had just committed the 3rd batch and were about to implement the next batch based on:

**Research finding:** The biggest gaps in BioForge vs. what scientists actually need daily:

| Gap | Impact | Status |
|-----|--------|--------|
| RNA-seq DEA | ★★★★★ | ✅ Done (differential_expression) |
| GO/pathway enrichment | ★★★★★ | ✅ Done (go_enrichment) |
| PubMed literature search | ★★★★★ | ✅ Done (search_pubmed) |
| UniProt protein info | ★★★★★ | ✅ Done (fetch_uniprot) |
| Gene info (NCBI Gene) | ★★★★ | ✅ Done (fetch_gene_info) |
| Drug-gene interactions | ★★★★ | ✅ Done (drug_gene_interaction) |
| Disease associations | ★★★★ | ✅ Done (open_targets) |
| GWAS lookup | ★★★ | ✅ Done (gwas_catalog) |
| Protein-protein networks | ★★★★ | ✅ Done (string_network) |
| Restriction enzyme mapping | ★★★ | ✅ Done (restriction_sites) |
| Protein properties | ★★★ | ✅ Done (protein_properties) |
| **Single-cell RNA-seq** | ★★★★★ | ❌ NOT DONE — Next priority |
| **Sequence motif search** | ★★★★ | ❌ NOT DONE |
| **Phylogenetic tree** | ★★★★ | ❌ NOT DONE |
| **AMR detection** | ★★★★ | ❌ NOT DONE — Africa angle |
| **HPO phenotype lookup** | ★★★★ | ❌ NOT DONE |
| **Metagenomics/16S** | ★★★★ | ❌ NOT DONE |
| **RNA structure prediction** | ★★★ | ❌ NOT DONE |
| **Protein-ligand docking** | ★★★ | ❌ NOT DONE |
| **Network visualisation export** | ★★★ | ❌ NOT DONE |
| **Volcano / MA plot export** | ★★★★ | ❌ NOT DONE — Key for papers |
| **ChIP-seq peak analysis** | ★★★ | ❌ NOT DONE |
| **Variant effect prediction (CADD/REVEL)** | ★★★★ | ❌ NOT DONE |

---

## 7. The Strategic Roadmap — Next Features to Build

Pick up from the next batch. Implement in this priority order:

### Batch 4 — Genomics completeness (highest impact next)

**`functional/plot_results.py`** — `plot_results` tool  
Generate publication-quality figures as base64 PNG from analysis results:
- Volcano plot from DEA results (log2FC vs -log10(padj))
- GO dot plot (terms vs gene ratio, coloured by p-value)
- Heatmap of top DE genes
- Lollipop/waterfall for CRISPR screen hits  
Uses `matplotlib` + `seaborn`. Returns base64 PNG + SVG source for editing.
This is HIGH PRIORITY — scientists can't publish without figures.

**`variants/cadd_score.py`** — `cadd_score` tool  
Fetch CADD (Combined Annotation Dependent Depletion) scores for variants.  
CADD is the standard method for variant pathogenicity scoring in clinical genetics.
REST API: `https://cadd.gs.washington.edu/api/v1.0/`  
Input: CHR:POS:REF:ALT or rsID. Returns PHRED score + raw score.
PHRED > 20 = top 1% most deleterious variants.

**`knowledge/hpo_phenotype.py`** — `hpo_phenotype` tool  
Human Phenotype Ontology lookup — clinical phenotype terms for a gene.  
API: `https://ontology.jax.org/api/`  
Input: gene symbol → associated HPO terms, phenotype descriptions, disease entities.  
Critical for clinical genetics workflows.

**`knowledge/ensembl_vep.py`** — `vep_annotate` tool  
Direct Ensembl VEP REST API for variant consequence prediction.  
More powerful than current ClinVar lookup — returns transcript consequences, SIFT, PolyPhen, regulatory impact, splicing prediction.

### Batch 5 — Africa / public health (strategic market)

**`functional/amr_detection.py`** — `amr_detection` tool  
Antimicrobial resistance gene detection via CARD (Comprehensive Antibiotic Resistance Database).  
API: `https://card.mcmaster.ca/download` (or use the RGI blast approach).  
Input: nucleotide sequence. Returns AMR genes with resistance mechanisms, drug classes, CARD accessions.  
This is James's home-ground strategic advantage — Nairobi, H3ABioNet, Africa CDC.

**`knowledge/who_pathogen.py`** — `who_pathogen` tool  
WHO Global Priority Pathogens list lookup and context.  
Links pathogen name to WHO priority status, resistance mechanisms, approved treatments.

**`functional/phylogenetics.py`** — `build_phylogenetic_tree` tool  
Build a neighbor-joining or UPGMA phylogenetic tree from aligned sequences.  
Use `Bio.Phylo` from Biopython. Returns Newick format + distance matrix.  
Input: MSA output (or raw sequences). Essential for evolutionary analysis.

### Batch 6 — Single-cell (fastest growing area in biology)

**`functional/scanpy_basic.py`** — `singlecell_qc` tool  
Basic single-cell RNA-seq quality control using scanpy.  
Input: count matrix dict, min_genes per cell, min_cells per gene.  
Output: filtered cell/gene counts, QC metrics (n_genes, n_counts, pct_mito).  
Note: Full scRNA-seq is compute-heavy. Start with QC + PCA; UMAP can be added later.

### Batch 7 — Infrastructure (what makes scientists trust the tool)

**Accuracy dashboard endpoint** — extend `GET /benchmarks/accuracy` to include new tools.  
Every new tool we added should have its accuracy exposed in the UI.

**Per-tool uncertainty badges** — surface the `published_accuracy` field in the frontend.  
When a tool runs, show a badge like "DeepCRISPR: ρ=0.130 (held-out)" next to the result.

**Citation auto-injection** — every tool result should automatically append its citations  
to the session's bibliography. When the methods section is drafted, all tool citations  
are already collected.

---

## 8. Key APIs and Their Access Patterns

All free, no API key needed unless noted:

| Service | Base URL | Notes |
|---------|----------|-------|
| NCBI Entrez | `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/` | Max 3 req/s unauthenticated |
| UniProt REST v2 | `https://rest.uniprot.org/uniprotkb/` | Fast, stable |
| STRING v11.5 | `https://string-db.org/api/json/` | `caller_identity=bioforge.tool` required |
| g:Profiler | `https://biit.cs.ut.ee/gprofiler/api/gost/profile/` | POST JSON |
| Open Targets | `https://api.platform.opentargets.org/api/v4/graphql` | GraphQL POST |
| DGIdb v5 | `https://dgidb.org/api/graphql` | GraphQL POST |
| GWAS Catalog | `https://www.ebi.ac.uk/gwas/rest/api/` | REST, paginated |
| CADD v1.7 | `https://cadd.gs.washington.edu/api/v1.0/` | Slow (15–60 s), use timeout=120 |
| HPO/Monarch | `https://ontology.jax.org/api/` | Fast REST |
| Ensembl VEP | `https://rest.ensembl.org/vep/human/hgvs/` | `Content-Type: application/json` |
| CARD/RGI | `https://card.mcmaster.ca/` | Download-based, or use local blast |
| EBI AlphaFold | `https://alphafold.ebi.ac.uk/api/` | Already used in BioForge |

---

## 9. Test Pattern

Every tool needs tests in `backend/tests/test_{category}_tools.py`. Always mock HTTP:

```python
import asyncio
from unittest.mock import AsyncMock, patch

def run(coro):
    return asyncio.run(coro)

def test_my_tool_happy_path():
    from bioforge.tools.knowledge.my_tool import MyToolInput, my_tool
    
    with patch("bioforge.tools.knowledge.my_tool._http_function",
               AsyncMock(return_value={"key": "value"})):
        result = run(my_tool(MyToolInput(param="test")))
    
    assert result.some_field == "expected"
    assert result.caveats  # always check caveats are present

def test_my_tool_not_found():
    from bioforge.tools.base import ToolError
    from bioforge.tools.knowledge.my_tool import MyToolInput, my_tool
    
    with patch("bioforge.tools.knowledge.my_tool._http_function",
               AsyncMock(return_value=None)):
        with pytest.raises(ToolError):
            run(my_tool(MyToolInput(param="nonexistent")))
```

Run tests: `cd /home/claude/bioforge && python -m pytest backend/tests/ -v --tb=short`

---

## 10. The One-Line Context for James

"We reviewed BioForge's scientific value (strong differentiators: honesty architecture, grounded benchmarks, methods section generator), identified the strategic positioning (publish Application Note, partner with H3ABioNet in Nairobi, own the 'honest AI for science' position), then spent the session implementing 11 new tools across knowledge retrieval and functional analysis, growing the tool registry from 30 to 41 tools. The next session continues adding tools from the priority list above, starting with `plot_results` (figures for papers), `cadd_score` (variant pathogenicity), `hpo_phenotype` (clinical genetics), `amr_detection` (Africa/public health), and `build_phylogenetic_tree`."

---

## 11. Commit History This Session

```
e7a5379  feat: add UniProt lookup, protein properties, restriction enzyme mapper
0835521  feat: add 8 new tools — knowledge retrieval + functional analysis  
96d0d37  feat: add manuscript-ready methods section generator
```

Start the next session by checking out main:
```bash
git clone https://github.com/itthj/bioforge.git /home/claude/bioforge
cd /home/claude/bioforge
pip install -e ".[dev]" pydeseq2 biopython --break-system-packages -q
git log --oneline -5  # confirm you're at e7a5379
```

Then immediately start implementing `plot_results` — it's the highest-impact next tool.
