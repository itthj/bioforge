# Phase-2 model license audit

**Verified 2026-05-29** against each project's *current upstream* license file / article terms
(fetched directly — see Source column). This satisfies the v4 blueprint's hard rule:
*license audit before implementation; never assert a license from memory.* Re-verify before
shipping, as upstream terms can change.

| Model | Upstream | License | Commercial use | Source |
|---|---|---|---|---|
| **Lindel** (Chen et al., *NAR* 2019) | `shendurelab/Lindel` | **MIT** | ✅ **Yes** | [LICENSE](https://github.com/shendurelab/Lindel/blob/master/LICENSE) |
| **FORECasT** (Allen et al., *Nat Biotech* 2019) | `felicityallen/SelfTarget` | **MIT** (© 2018 Felicity Allen) | ✅ **Yes** | [LICENSE.txt](https://github.com/felicityallen/SelfTarget/blob/master/LICENSE.txt) |
| **inDelphi** (Shen et al., *Nature* 2018) | `maxwshen/inDelphi-model` | **Limited Copyright License — Research Use by Non-Profit/Government only** (owners: MIT, Broad, Harvard, BWH) | ❌ **No** (separate agreement required) | [README](https://github.com/maxwshen/inDelphi-model) |
| **DeepSpCas9** (Kim et al., *Sci. Adv.* 2019) | article + `MyungjaeSong/Paired-Library` | Article **CC BY-NC** (non-commercial); **code repo has no LICENSE file** → default all-rights-reserved | ❌ **No** | [Article](https://pmc.ncbi.nlm.nih.gov/articles/PMC6834390/), [code](https://github.com/MyungjaeSong/Paired-Library) |

## Per-model detail & recommended BioForge posture

- **Lindel — MIT. Clear to integrate, even redistribute** (keep the copyright notice). No consent gate needed. Lowest-friction edit-outcome model to add.
- **FORECasT — MIT. Clear to integrate, even redistribute** (keep `© 2018 Felicity Allen`). Also low-friction.
- **inDelphi — non-commercial only.** This **confirms the existing project decision** (fetch-on-first-use, no weight redistribution, `BIOFORGE_INDELPHI_CONSENT_NONCOMMERCIAL` gate). No change needed; keep the consent gate and never bundle weights.
- **DeepSpCas9 — non-commercial (and the code itself is unlicensed).** The article is CC BY-NC; the model code in `MyungjaeSong/Paired-Library` carries **no LICENSE file**, which under GitHub's terms means *all rights reserved* — you may view/fork but have **no granted right to use, modify, or redistribute** it. Treat as non-commercial at best, and obtain explicit permission before any integration.

## ⚠ Decision-changing finding

The v4 blueprint mandates **DeepSpCas9 as the *primary* on-target scorer** — but DeepSpCas9 is
**non-commercial / unlicensed-code**, which directly conflicts with the project's standing stance
(*"treat non-commercial license constraints as something to avoid by default"*).

Options, for an explicit decision:
1. **Accept the constraint** — gate DeepSpCas9 like inDelphi (consent + no redistribution), and accept BioForge's on-target *primary* path is non-commercial. Also requires contacting the authors, since the code has no license.
2. **Swap the primary** to a commercially-licensed deep model (e.g. a permissively-licensed reimplementation, or `crisprVerse/crisprScore` algorithms — re-audit before adopting) and keep DeepSpCas9 as an optional non-commercial add-on.
3. **Defer ML on-target** entirely; keep the current transparent rule-based scorer as an explicitly-labelled heuristic until a commercially-clear deep model is chosen.

**Net:** Lindel + FORECasT are green to build now; inDelphi stays as-is (non-commercial gate); DeepSpCas9-as-primary needs your call before any work begins. None of these may be built on a commercial-use assumption without the sign-off above.

---

## On-target primary model selection — decision: option 2 (2026-05-29)

Per the decision to **swap the primary on-target scorer to a commercially-clear deep model**
(DeepSpCas9 demoted to optional), candidate deep on-target predictors were audited against
their *current upstream* licenses:

| Candidate | Upstream | License | Commercial? | Verdict |
|---|---|---|---|---|
| **DeepCRISPR** (Chuai et al., *Genome Biol* 2018) | `bm2-lab/DeepCRISPR` | **Apache-2.0** (verified) | ✅ Yes | **🟢 Recommended new primary** |
| CRISPRon (Xiang et al., *Nat Commun* 2021) | `RTH-tools/crispron` | **AGPL-3.0** | ⚠ Copyleft | 🔴 Unsuitable — network-copyleft can force open-sourcing a hosted BioForge |
| DeepHF (Wang et al., *Nat Commun* 2019) | `izhangcd/DeepHF` | **No LICENSE file** → all-rights-reserved | ❌ No grant | 🔴 Not usable without author permission |
| Azimuth / Rule Set 2 (Doench et al. 2016) | `MicrosoftResearch/Azimuth` | **BSD-3-Clause** (verified 2026-05-30) | ✅ Yes | 🟢 *Secondary* slot — cleared to build; see RS2 detail below |

**Sources:** [DeepCRISPR](https://github.com/bm2-lab/DeepCRISPR) ([paper](https://doi.org/10.1186/s13059-018-1459-4)) · [CRISPRon](https://github.com/RTH-tools/crispron) ([paper](https://www.nature.com/articles/s41467-021-23576-0)) · [DeepHF](https://github.com/izhangcd/DeepHF) ([paper](https://www.nature.com/articles/s41467-019-12281-8)) · [Azimuth](https://github.com/MicrosoftResearch/Azimuth)

### Recommendation

- **Primary: DeepCRISPR (Apache-2.0).** The only audited deep on-target model that is
  unambiguously commercial-safe (and redistributable with attribution).
- **Optional: DeepSpCas9** — kept behind a non-commercial consent gate (like inDelphi), for
  users who want it and accept the terms.
- **Secondary: Doench Rule Set 2** (Azimuth) — **license verified BSD-3-Clause (2026-05-30); cleared
  to build** (detail section below), or the existing transparent rule-based heuristic, shown
  side-by-side per the two-scorer design.

### Honest integration caveats (decide before building)

1. **DeepCRISPR is TensorFlow 1.x (2018).** That framework is effectively deprecated; integrating it into the Python 3.11 stack means either a pinned legacy-TF container invoked out-of-process (mirrors the existing inDelphi fetch-on-first-use pattern, **minus** the consent gate since Apache-2.0 is clean), or reimplementing its inference path. This is the main engineering cost of option 2.
2. **Trained on human cell-line data** (on-target training spanned HCT116, HEK293T, HeLa, and HL60; Chuai 2018) → declare its OOD envelope (§6) and flag out-of-envelope inputs.
3. **Published held-out accuracy — now sourced** (Chuai et al. 2018, *Genome Biol* 19:80; open-access mirror [PMC6020378](https://pmc.ncbi.nlm.nih.gov/articles/PMC6020378/)). Re-verify against the supplement before wiring a numeric calibration display.
   - *On-target, classification schema:* best **ROC-AUC 0.857** (full model: pretraining + data augmentation), a reported ~157% gain over sgRNA Designer (Fig. 2a,b). Leave-one-cell-type-out generalization averaged **ROC-AUC 0.722** across the four human cell lines (Fig. 2d).
   - *On-target, regression schema:* on an independent HEL dataset (425 sgRNAs) DeepCRISPR reported a "nearly twofold improvement" in Spearman correlation over sgRNA Designer, and outperformed SSC, sgRNA Scorer, and CRISPRator (Fig. 2g). The **exact Spearman ρ lives in Additional file 3**, not the main text — pull it from the supplement before displaying a numeric value; cite ROC-AUC 0.857 as the headline on-target figure until then.
   - *Off-target* (relevant only if the optional DeepCRISPR off-target path is also adopted): **ROC-AUC 0.981 / PR-AUC 0.497** at ≤6 mismatches, exceeding the CFD score (Fig. 3a–c).

---

## Doench Rule Set 2 (Azimuth) — secondary on-target slot, verified 2026-05-30

Resolves the "unverified" Azimuth row above. The earlier pass looked for `/LICENSE` at the repo root
and found none; the license file is actually **`LICENSE.txt`**, which is why it read as "not at
expected path." Verified directly this session:

| Item | Finding | Source |
|---|---|---|
| Code license | **BSD-3-Clause**, `Copyright (c) 2015, Microsoft Research` — standard three clauses, **no** non-commercial / research-only / field-of-use rider (full text read) | [LICENSE.txt](https://github.com/MicrosoftResearch/Azimuth/blob/master/LICENSE.txt); SPDX `BSD-3-Clause` via the [GitHub license API](https://api.github.com/repos/MicrosoftResearch/Azimuth/license) |
| Weights | Pretrained scikit-learn models committed in-repo (`saved_models/V3_model_full.pickle`, `V3_model_nopos.pickle`) under that same BSD-3-Clause — **redistributable with attribution** | [setup.py](https://github.com/MicrosoftResearch/Azimuth/blob/master/setup.py) declares `license="BSD"` |
| Python-3 port | **`Biomatters/Azimuth`** (for Geneious Prime) — GitHub-detected BSD-3-Clause, ships the same pickles, targets Python 3 | [Biomatters/Azimuth](https://github.com/Biomatters/Azimuth) |

**Verdict: 🟢 clear to build.** BSD-3-Clause permits commercial use **and** redistribution (retain the
copyright notice + license text + disclaimer). Unlike inDelphi, **no consent gate and no
fetch-on-first-use is required** — the pickles may be vendored with attribution. This unblocks the
two-scorer design's secondary slot (DeepCRISPR primary + Doench RS2 secondary, side-by-side).

### Honest integration caveats (decide before building)

1. **scikit-learn pickle version-fragility.** The upstream README warns the `saved_models/*.pickle`
   files are incompatible across scikit-learn versions. Run RS2 the **DeepCRISPR / Lindel / FORECasT
   way**: a pinned legacy environment invoked **out-of-process**, loading the committed pickles
   **as-is**. Do **not** retrain — that is banned ML-training code *and* would no longer be the
   published RS2 model. Pin the scikit-learn version that deserializes the pickles, and pin the
   upstream commit (add `azimuth_upstream_commit` to settings + the §10 reference-pin map, mirroring
   `lindel_upstream_commit`).
2. **Use the Python-3 port out-of-process** (`Biomatters/Azimuth`), or a pinned Python-2 container —
   the original MSR repo is Python-2-era and archived. The Py3 port is the lower-friction target for
   the 3.11 stack. Re-verify the port's license and that its pickles are byte-identical to upstream
   before adopting it specifically.
3. **Two models, different feature requirements — choose honestly.** `V3_model_full` needs positional
   context (cut position + percent-peptide of the target site); `V3_model_nopos` is sequence-only. For
   a guide-only request with no gene context, **the nopos model is the correct choice** — feeding the
   full model fabricated positional values would violate *"AI never fabricates biology."* Stamp which
   model produced each score.
4. **Declare the OOD envelope (§6)** and flag out-of-envelope inputs, like the other ML scorers.
5. **Attribution:** retain `Copyright (c) 2015, Microsoft Research` + the BSD-3-Clause text, and cite
   Doench et al., *Nat Biotechnol* 2016.

**Net:** Doench RS2 (Azimuth) is license-cleared and now **integrated + validated** (2026-05-30):
`score_guide_on_target(model="azimuth_rs2")`, out-of-process via `bioforge/azimuth:legacy`
(scikit-learn 0.23.2 deserializes the committed `V3_model_nopos.pickle`; deterministic), off by
default. No new license risk.

---

## Session 5 decisions (2026-06-02)

### DeepSpCas9 -> DeepCRISPR primary: SIGNED OFF (user decision, 2026-06-02)

The blueprint names DeepSpCas9 the *primary* on-target scorer. Per the audit above it is
non-commercial (article CC BY-NC) with unlicensed code, which conflicts with the project's
commercial-clean posture. **Decision (user, 2026-06-02): formally accept DeepCRISPR (Apache-2.0)
as the primary on-target model; DeepSpCas9 stays dropped** (available only if a user supplies it
themselves under their own non-commercial agreement). This is a deliberate, signed-off deviation
from the blueprint's named primary -- recorded here so it is intentional and visible, never silent.

### Phase 3/4 external tooling audit (verified 2026-06-02 against current upstream)

| Tool | Purpose | License | Commercial? | Source |
|---|---|---|---|---|
| **MAFFT** (core) | multiple-sequence alignment (Phase 4 MSA viewer) | **BSD-3-Clause** (verified) | ✅ Yes (+ redistribute, keep attribution) | [license.txt](https://mafft.cbrc.jp/alignment/software/license.txt) |
| **DeepVariant** | variant calling (Phase 3 GIAB benchmark) | **BSD-3-Clause** (verified; SPDX via GitHub API) | ✅ Yes | [LICENSE](https://github.com/google/deepvariant/blob/r1.6/LICENSE) · [license API](https://api.github.com/repos/google/deepvariant/license) |

- **MAFFT core is BSD-3-Clause** — clear to integrate + containerize (digest-pinned); commercial use
  and redistribution permitted with attribution. **CRITICAL nuance (rule 15):** MAFFT's *bundled
  extensions* (`license66.txt`: the Vienna RNA Package and MXSCARNA) carry a restrictive
  "not redistributed for any fee, other than media costs" clause and are **NOT** BSD. The MSA tool
  must build a **core-only MAFFT** image (no extensions) so only the BSD core is shipped. (ProbCons,
  also in the extension bundle, is public domain; the restrictive pieces are Vienna RNA + MXSCARNA.)
  Had we assumed "MAFFT is BSD" from memory we would have missed this — exactly what rule 15 exists for.
- **DeepVariant is BSD-3-Clause** — clear to integrate as a digest-pinned container for the GIAB
  variant-calling path. We pull the official image and do **not** redistribute its weights; re-verify
  the model-file terms before any redistribution.

### OOD interactive HITL (§4.3): deliberately deferred (user decision, 2026-06-02)

The blueprint's §4.3 "proceed-with-OOD-flag or cancel" interactive card requires pausing and
resuming the executor **mid-loop** (the `Plan` carries no concrete tool inputs — verified in
`agent/planner.py` — so OOD can only be evaluated at execution time). That is a substantial,
regression-risky rewrite of the stable executor. **Decision: keep the existing `block` (refuse
before running) + `annotate` (visible OOD advisory) modes, which already protect and inform the
user, and defer the interactive mid-run gate.** Recorded as an intentional architectural deviation,
not a silent gap.

