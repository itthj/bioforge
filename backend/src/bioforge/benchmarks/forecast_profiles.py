"""Fetch + verify FORECasT processed mutational profiles on demand (the OBSERVED data).

Used ONLY by the section-13 edit-outcome distribution-agreement benchmark, never at request
time. These are the measured (observed) indel-outcome profiles from Allen et al. 2018 -- the
ground truth against which a FORECasT prediction's indel distribution is scored (TVD/JSD).

Why a loader (mirrors `benchmarks/effdata.py`'s posture):

1. **Consent-gated network.** A fetch happens only after `BIOFORGE_FORECAST_PROFILES_CONSENT=true`.
   The processed profiles are CC BY 4.0 (figshare 10.6084/m9.figshare.7312067.v2) so redistribution
   would be permitted, but we still fetch-on-first-use + never vendor (keeps the repo lean and the
   provenance explicit -- the posture license_audit.md recommends).
2. **Reproducible by sha256.** Each sample pins the figshare file's immutable `expected_sha256` AND
   the expected oligo count `n_oligos`; bytes that do not match are rejected before parsing/caching,
   so the benchmark can never silently run on the wrong data.
3. **Not a one-way door.** A user-supplied `local_path` (you bring the file -- no network, no consent
   flag) is read + sha256-verified the same way; figshare's `download_url` (an immutable file id) is
   the default acquisition path.
4. **Testable without network.** Downloads go through an injectable `download_fn`.

The observed profile layout (verified against the real deposit; produced by the authors'
`collate_indels.py`): each sample ZIP contains many `*_processedindels.txt` members, each holding
one block per oligo:

    @@@<oligo_id>
    <indel_label>\t<count>\t<representative_read>
    ...

`<indel_label>` is FORECasT's OWN taxonomy (`D2_L-3R0`, `I1_L-2C1R0`) -- the SAME vocabulary the
FORECasT predictor emits, which is what makes a TVD/JSD comparison meaningful (no remapping). Counts
are normalized to a per-oligo distribution; the wild-type `-` line (a viewer placeholder, not a
repair outcome) is dropped -- exactly as the predictor-side parser in `models/forecast/legacy`
already does.
"""

from __future__ import annotations

import hashlib
import io
import os
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from bioforge.config import Settings
from bioforge.config import settings as _default_settings

DownloadFn = Callable[[str], bytes]

_PROFILE_MEMBER_SUFFIX = "_processedindels.txt"
_BLOCK_PREFIX = "@@@"
_WT_LABEL = "-"  # the wild-type reference line collate emits; never a repair outcome


class ForecastProfilesConsentRequired(Exception):
    """Raised when a network fetch is needed but the consent flag is unset."""


class ForecastProfilesUnknown(Exception):
    """Raised when an unregistered sample name is requested."""


class ForecastProfilesFetchError(Exception):
    """Raised for any fetch/IO/verification/parse failure -- a clean error, not a stack trace."""


@dataclass(frozen=True)
class ObservedSpec:
    """One FORECasT observed-profile sample (a figshare file id) + its integrity expectations.

    `expected_sha256` is the committed hash of the (zip) file at `download_url`; `n_oligos` is the
    expected number of oligo blocks across all `*_processedindels.txt` members. Both are
    release-grade guards that the right bytes were loaded.
    """

    name: str
    download_url: str
    cache_filename: str
    expected_sha256: str
    n_oligos: int
    sample_label: str
    citation: str
    notes: str


@dataclass(frozen=True)
class OligoProfile:
    """One oligo's observed indel outcome: the normalized label->frequency distribution plus the
    raw edited-read total behind it (so a consumer can drop low-coverage oligos before scoring).
    `distribution` sums to 1 over FORECasT labels; it is empty when the oligo had no edited reads.
    """

    oligo_id: str
    total_reads: int
    distribution: dict[str, float]


@dataclass(frozen=True)
class LoadedObserved:
    """Parsed observed profiles (oligo_id -> OligoProfile) plus the provenance the benchmark
    records (source + verified hash)."""

    spec: ObservedSpec
    profiles: dict[str, OligoProfile]
    source: str
    sha256: str


# The observed-sample registry. K562 (standard SpCas9, no TREX2/eCAS9 perturbation) is FORECasT's
# primary cell line, so it is the most interpretable agreement target (model error is not conflated
# with cross-cell-type repair-biology differences). Add other samples (LV7B replicate, etc.) the
# same way -- verify each file's sha256 + oligo count live, never trust a hash from memory.
# Provenance verified live 2026-06-02 (figshare file id 13513499).
OBSERVED: dict[str, ObservedSpec] = {
    "K562_LV7A_DPI7": ObservedSpec(
        name="K562_LV7A_DPI7",
        download_url="https://ndownloader.figshare.com/files/13513499",
        cache_filename="ST_June_2017_K562_800x_LV7A_DPI7.zip",
        expected_sha256="526dbcbfaa86375fc65d4c040486d27f8e9d0e68a7204c1354ac25f71d3c0bfc",
        n_oligos=35131,
        sample_label="K562 (800x, LV7A, DPI7)",
        citation=(
            "Allen F, Crepaldi L, Alsinet C, et al. (2018) Predicting the mutations generated by "
            "repair of Cas9-induced double-strand breaks. Nat Biotechnol 37:64-72. Processed "
            "mutational profiles: figshare 10.6084/m9.figshare.7312067.v2 (CC BY 4.0)."
        ),
        notes=(
            "Standard SpCas9 (no TREX2/2A-TREX2/eCAS9 perturbation). Per-oligo measured indel "
            "distribution in FORECasT's own label taxonomy; the wild-type `-` line is dropped."
        ),
    ),
}


def _default_data_dir() -> Path:
    """`~/.bioforge/data/forecast_profiles/`. Created on first use."""
    return Path(os.path.expanduser("~")) / ".bioforge" / "data" / "forecast_profiles"


def _resolve_data_dir(s: Settings) -> Path:
    if s.forecast_profiles_dir:
        return Path(s.forecast_profiles_dir).expanduser().resolve()
    return _default_data_dir()


def _cache_path(s: Settings, spec: ObservedSpec) -> Path:
    return _resolve_data_dir(s) / spec.cache_filename


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _require_consent(s: Settings) -> None:
    if s.forecast_profiles_consent:
        return
    raise ForecastProfilesConsentRequired(
        "FORECasT processed mutational profiles are fetched on first use for the section-13 "
        "edit-outcome benchmark. They are CC BY 4.0 (figshare 10.6084/m9.figshare.7312067.v2), but "
        "BioForge does not vendor them. To fetch, set BIOFORGE_FORECAST_PROFILES_CONSENT=true. "
        "Alternatively, supply the file yourself via load_observed(..., local_path=...) -- a local "
        "file needs no consent flag and no network."
    )


def _verify(blob: bytes, spec: ObservedSpec, source: str) -> str:
    actual = _sha256(blob)
    if actual != spec.expected_sha256:
        raise ForecastProfilesFetchError(
            f"sha256 mismatch for sample {spec.name!r} from {source}: expected "
            f"{spec.expected_sha256}, got {actual}. The cache may be corrupt, the supplied file may "
            f"be the wrong one, or the figshare file changed (unexpected -- a file id is immutable). "
            f"Delete the cache / re-check the file rather than trusting these bytes."
        )
    return actual


def _httpx_download(url: str) -> bytes:
    """Default `download_fn`. Lazy-imports httpx (kept out of import time)."""
    import httpx

    try:
        resp = httpx.get(url, timeout=300.0, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise ForecastProfilesFetchError(
            f"Failed to download {url}: {e}. Check connectivity and that the figshare file id still exists."
        ) from e
    return resp.content


def _normalize(counts: dict[str, float]) -> dict[str, float]:
    total = sum(counts.values())
    if total <= 0:
        return {}
    return {label: c / total for label, c in counts.items()}


def _store_block(profiles: dict[str, OligoProfile], oligo_id: str | None, counts: dict[str, float]) -> None:
    """Finalize one parsed `@@@` block into `profiles` (no-op before the first header)."""
    if oligo_id is None:
        return
    if oligo_id in profiles:
        raise ForecastProfilesFetchError(f"Duplicate oligo id {oligo_id!r} across observed-profile members.")
    total = int(round(sum(counts.values())))
    profiles[oligo_id] = OligoProfile(oligo_id=oligo_id, total_reads=total, distribution=_normalize(counts))


def parse_observed_profiles(zip_bytes: bytes) -> dict[str, OligoProfile]:
    """Parse a FORECasT processed-profiles ZIP into `{oligo_id: OligoProfile}`.

    Faithful to the upstream layout: per `@@@<oligo_id>` block, sum the edited-read counts by label,
    normalize to a distribution, and drop the wild-type `-` line. A block with no edited reads yields
    an empty distribution (total_reads=0) -- the consumer decides whether to score it. Malformed
    structure (a count row before any `@@@` header, a non-numeric count) raises rather than guessing.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as e:
        raise ForecastProfilesFetchError(f"Observed-profile payload is not a valid ZIP: {e}") from e

    profiles: dict[str, OligoProfile] = {}
    members = [n for n in zf.namelist() if n.endswith(_PROFILE_MEMBER_SUFFIX)]
    if not members:
        raise ForecastProfilesFetchError(
            f"No '*{_PROFILE_MEMBER_SUFFIX}' members in the observed-profile ZIP "
            f"(found {len(zf.namelist())} entries). The deposit layout may have changed."
        )

    for member in members:
        text = zf.read(member).decode("utf-8", "replace")
        current_id: str | None = None
        current: dict[str, float] = {}
        for lineno, raw in enumerate(text.splitlines(), start=1):
            line = raw.strip()
            if not line:
                continue
            if line.startswith(_BLOCK_PREFIX):
                _store_block(profiles, current_id, current)
                current_id = line[len(_BLOCK_PREFIX) :].split()[0]
                current = {}
                continue
            if current_id is None:
                raise ForecastProfilesFetchError(
                    f"{member}:{lineno}: indel row before any '{_BLOCK_PREFIX}<oligo>' header: {line!r}"
                )
            parts = line.split("\t")
            label = parts[0].strip()
            if label == _WT_LABEL:
                continue  # wild-type viewer placeholder, not a repair outcome
            if len(parts) < 2:
                raise ForecastProfilesFetchError(f"{member}:{lineno}: indel row missing a count: {line!r}")
            try:
                count = float(parts[1])
            except ValueError as e:
                raise ForecastProfilesFetchError(f"{member}:{lineno}: non-numeric count {parts[1]!r}") from e
            current[label] = current.get(label, 0.0) + count
        _store_block(profiles, current_id, current)

    return profiles


def load_observed(
    name: str,
    *,
    settings: Settings | None = None,
    download_fn: DownloadFn | None = None,
    local_path: str | Path | None = None,
) -> LoadedObserved:
    """Load + verify + parse one FORECasT observed-profile sample.

    Acquisition order: a `local_path` you supply (no network, no consent gate) -> the cache from a
    prior run -> a fresh fetch (consent-gated). Every path is sha256-verified against the committed
    expectation before the bytes are parsed or cached, and the parsed oligo count is checked against
    the spec. Returns parsed profiles plus provenance (source + verified hash).
    """
    s = settings if settings is not None else _default_settings
    spec = OBSERVED.get(name)
    if spec is None:
        raise ForecastProfilesUnknown(
            f"Unknown FORECasT observed sample {name!r}. Registered: {sorted(OBSERVED)}. Add it to "
            "benchmarks.forecast_profiles.OBSERVED (with its verified sha256 + oligo count) first."
        )

    if local_path is not None:
        path = Path(local_path).expanduser()
        try:
            blob = path.read_bytes()
        except OSError as e:
            raise ForecastProfilesFetchError(f"Could not read supplied profile file {path}: {e}") from e
        source = f"local:{path}"
        verified = _verify(blob, spec, source)
    else:
        cache = _cache_path(s, spec)
        if cache.exists():
            blob = cache.read_bytes()
            source = f"cache:{cache}"
            verified = _verify(blob, spec, source)
        else:
            _require_consent(s)
            fetch = download_fn if download_fn is not None else _httpx_download
            blob = fetch(spec.download_url)
            source = f"fetch:{spec.download_url}"
            verified = _verify(blob, spec, source)  # verify BEFORE caching -- never cache bad bytes
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(blob)

    profiles = parse_observed_profiles(blob)
    if len(profiles) != spec.n_oligos:
        raise ForecastProfilesFetchError(
            f"Sample {spec.name!r} parsed {len(profiles)} oligos but the spec expects {spec.n_oligos}. "
            "Refusing to benchmark on an unexpected oligo count -- re-verify the spec / source."
        )
    return LoadedObserved(spec=spec, profiles=profiles, source=source, sha256=verified)
