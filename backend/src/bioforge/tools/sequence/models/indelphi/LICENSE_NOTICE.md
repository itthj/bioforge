# inDelphi — license notice

BioForge integrates the **inDelphi** model (Shen et al., 2018) to predict
per-guide Cas9 edit outcomes. We **do not redistribute** inDelphi's source code
or pretrained weights. Instead, when the user explicitly opts in, BioForge's
fetcher downloads them directly from the upstream repository
([maxwshen/inDelphi-model](https://github.com/maxwshen/inDelphi-model)) at a
pinned commit into a local data directory.

## Upstream license (verbatim summary)

inDelphi is licensed for **non-commercial research use only**. From the
upstream `LICENSE.txt`:

> *"to obtain any right to use the Code for commercial purposes ... You must
> enter into an appropriate, separate and direct license agreement with the
> Owners."*

> *"You will redistribute modifications, if any, under the same terms as this
> license and only to non-profits and US government institutions."*

You must credit the authors and cite the paper:

> Shen, M. W. et al. *Predictable and precise template-free editing of
> pathogenic mutations by CRISPR-Cas9 nuclease.* **Nature** 563, 646–651 (2018).

## What this means for you

- If your use of BioForge is non-commercial research, you may opt in to
  inDelphi by setting `BIOFORGE_INDELPHI_CONSENT_NONCOMMERCIAL=true` and
  triggering the fetch.
- If your use is or may become commercial, do **not** enable inDelphi.
  Contact the upstream authors for a commercial license, or use the
  `rule_of_thumb` model (no weights, published averages only — fully
  permissive).
- BioForge displays the citation in every result that uses the inDelphi model.

## Why we don't bundle the weights

Bundling would make BioForge a redistributor of a non-commercial-only artifact
and would constrain BioForge's own license. By fetching on demand into a local
cache, the legal posture stays local to the user's machine and consent.
