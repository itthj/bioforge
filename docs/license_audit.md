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
