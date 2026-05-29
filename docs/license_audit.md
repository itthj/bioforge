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
| Azimuth / Rule Set 2 (Doench et al. 2016) | `MicrosoftResearch/Azimuth` | not at expected path (unverified) | — | This is the *secondary* slot (Doench RS2); verify separately |

**Sources:** [DeepCRISPR](https://github.com/bm2-lab/DeepCRISPR) ([paper](https://doi.org/10.1186/s13059-018-1459-4)) · [CRISPRon](https://github.com/RTH-tools/crispron) ([paper](https://www.nature.com/articles/s41467-021-23576-0)) · [DeepHF](https://github.com/izhangcd/DeepHF) ([paper](https://www.nature.com/articles/s41467-019-12281-8)) · [Azimuth](https://github.com/MicrosoftResearch/Azimuth)

### Recommendation

- **Primary: DeepCRISPR (Apache-2.0).** The only audited deep on-target model that is
  unambiguously commercial-safe (and redistributable with attribution).
- **Optional: DeepSpCas9** — kept behind a non-commercial consent gate (like inDelphi), for
  users who want it and accept the terms.
- **Secondary: Doench Rule Set 2** (verify the Azimuth license first) or the existing
  transparent rule-based heuristic, shown side-by-side per the two-scorer design.

### Honest integration caveats (decide before building)

1. **DeepCRISPR is TensorFlow 1.x (2018).** That framework is effectively deprecated; integrating it into the Python 3.11 stack means either a pinned legacy-TF container invoked out-of-process (mirrors the existing inDelphi fetch-on-first-use pattern, **minus** the consent gate since Apache-2.0 is clean), or reimplementing its inference path. This is the main engineering cost of option 2.
2. **Trained on human cell-line data** (on-target training spanned HCT116, HEK293T, HeLa, and HL60; Chuai 2018) → declare its OOD envelope (§6) and flag out-of-envelope inputs.
3. **Published held-out accuracy — now sourced** (Chuai et al. 2018, *Genome Biol* 19:80; open-access mirror [PMC6020378](https://pmc.ncbi.nlm.nih.gov/articles/PMC6020378/)). Re-verify against the supplement before wiring a numeric calibration display.
   - *On-target, classification schema:* best **ROC-AUC 0.857** (full model: pretraining + data augmentation), a reported ~157% gain over sgRNA Designer (Fig. 2a,b). Leave-one-cell-type-out generalization averaged **ROC-AUC 0.722** across the four human cell lines (Fig. 2d).
   - *On-target, regression schema:* on an independent HEL dataset (425 sgRNAs) DeepCRISPR reported a "nearly twofold improvement" in Spearman correlation over sgRNA Designer, and outperformed SSC, sgRNA Scorer, and CRISPRator (Fig. 2g). The **exact Spearman ρ lives in Additional file 3**, not the main text — pull it from the supplement before displaying a numeric value; cite ROC-AUC 0.857 as the headline on-target figure until then.
   - *Off-target* (relevant only if the optional DeepCRISPR off-target path is also adopted): **ROC-AUC 0.981 / PR-AUC 0.497** at ≤6 mismatches, exceeding the CFD score (Fig. 3a–c).

