"""Fetch + verify crisporPaper effData guide-efficiency datasets on demand.

Used ONLY by the §13 on-target accuracy benchmark, never at request time. crisporPaper
(Haeussler / Concordet -- the same source the CFD matrices were taken from verbatim) carries
NO license file -> all-rights-reserved, so its data is NEVER vendored into our git history.

Design (mirrors the inDelphi fetcher's posture, simpler because we KNOW each file's sha256):

1. **No silent network.** A network fetch happens only after the operator sets
   `BIOFORGE_CRISPOR_EFFDATA_CONSENT=true`, acknowledging the data is unlicensed and fetched
   transiently for benchmarking, not redistributed. The flag is the sole consent signal.
2. **Reproducible by commit + sha256.** Every dataset pins an immutable upstream commit
   (`settings.crispor_effdata_commit`) AND a committed `expected_sha256`. A download (or a
   cached / user-supplied file) is rejected unless its bytes match -- so the benchmark can never
   silently run on the wrong data.
3. **Not a one-way door.** The same loader reads a user-supplied `local_path` (you bring the
   file -- no network, no consent flag needed, still sha256-verified) or, by editing the spec, an
   alternate mirror URL. Fetch-on-first-use is just the default acquisition path.
4. **Testable without network.** Downloads go through an injectable `download_fn`.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from bioforge.config import Settings
from bioforge.config import settings as _default_settings

DownloadFn = Callable[[str], bytes]

_RAW_BASE = "https://raw.githubusercontent.com/maximilianh/crisporPaper"


class EffDataConsentRequired(Exception):
    """Raised when a network fetch is needed but the consent flag is unset.

    Surfaced to the operator with the opt-in instructions (and the local-file alternative)
    so they decide whether to fetch the unlicensed data.
    """


class EffDataUnknown(Exception):
    """Raised when an unregistered dataset name is requested."""


class EffDataFetchError(Exception):
    """Raised for any fetch/IO/verification failure -- a clean error, not a stack trace."""


@dataclass(frozen=True)
class EffDataset:
    """One crisporPaper effData guide-efficiency dataset.

    `expected_sha256` is the committed integrity expectation for the file AT `upstream_relpath`
    AND the pinned commit; `n_rows` is the expected DATA-row count (excluding the header) -- both
    are release-grade guards that the right bytes were loaded.
    """

    name: str
    upstream_relpath: str
    expected_sha256: str
    n_rows: int
    citation: str
    notes: str


@dataclass(frozen=True)
class EffDataRow:
    """One measured guide: the upstream guide name (carries genomic coords), the 23-mer window
    (20 nt protospacer + 3 nt PAM, ending in NGG), and the measured modification frequency."""

    guide_name: str
    seq: str
    mod_freq: float


@dataclass(frozen=True)
class LoadedDataset:
    """Parsed dataset rows plus the provenance the benchmark records (source + verified hash)."""

    spec: EffDataset
    rows: list[EffDataRow]
    source: str
    sha256: str


# The dataset registry. chari2015Train is the on-target slice-1 eval set; adding doench2014 /
# concordet2 / the chari K562 split later is a one-line addition (verify each file's sha256 the
# same way -- never trust a hash from memory). Provenance verified live 2026-05-30.
DATASETS: dict[str, EffDataset] = {
    "chari2015Train": EffDataset(
        name="chari2015Train",
        upstream_relpath="effData/chari2015Train.tab",
        expected_sha256="6a6254a3966c53aa5eceb46cddf57e940466632ebee277d7b0450b662485e576",
        n_rows=1234,
        citation=(
            "Chari R, Yeo NC, Chavez A, Church GM (2015) Unraveling CRISPR-Cas9 genome engineering "
            "parameters via a library-on-library approach. Nat Methods 12:823-826"
        ),
        notes=(
            "293T library-on-library guide-efficiency screen. `seq` is the 23-mer (20 nt protospacer "
            "+ 3 nt NGG PAM). `modFreq` is a normalized modification frequency and is NOT bounded to "
            "[0, 1] (values exceed 1.0), so treat it as a rank/linear target, not a probability."
        ),
    ),
}


def raw_url(commit_sha: str, upstream_relpath: str) -> str:
    """raw.githubusercontent.com URL for a crisporPaper file at a pinned commit."""
    return f"{_RAW_BASE}/{commit_sha}/{upstream_relpath}"


def _default_data_dir() -> Path:
    """`~/.bioforge/data/crispor_effdata/`. Created on first use."""
    return Path(os.path.expanduser("~")) / ".bioforge" / "data" / "crispor_effdata"


def _resolve_data_dir(s: Settings) -> Path:
    if s.crispor_effdata_dir:
        return Path(s.crispor_effdata_dir).expanduser().resolve()
    return _default_data_dir()


def _cache_path(s: Settings, spec: EffDataset) -> Path:
    """Commit-scoped cache path so flipping the commit pin is a clean re-bootstrap."""
    return _resolve_data_dir(s) / s.crispor_effdata_commit / Path(spec.upstream_relpath).name


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _require_consent(s: Settings) -> None:
    if s.crispor_effdata_consent:
        return
    raise EffDataConsentRequired(
        "crisporPaper effData is UNLICENSED (the repo has no LICENSE file -> all rights reserved); "
        "BioForge never vendors it. To run the on-target accuracy benchmark, set "
        "BIOFORGE_CRISPOR_EFFDATA_CONSENT=true to acknowledge the data is fetched transiently for "
        "benchmarking only and not redistributed. Alternatively, supply the file yourself via "
        "load_dataset(..., local_path=...) -- a local file needs no consent flag and no network."
    )


def _verify(blob: bytes, spec: EffDataset, source: str) -> str:
    """sha256-check `blob` against the committed expectation. Returns the verified hash."""
    actual = _sha256(blob)
    if actual != spec.expected_sha256:
        raise EffDataFetchError(
            f"sha256 mismatch for dataset {spec.name!r} from {source}: expected "
            f"{spec.expected_sha256}, got {actual}. The cache may be corrupt, the supplied file may "
            f"be the wrong one, or upstream changed at the pinned commit (unexpected -- a commit is "
            f"immutable). Delete the cache / re-check the file rather than trusting these bytes."
        )
    return actual


def _httpx_download(url: str) -> bytes:
    """Default `download_fn`. Lazy-imports httpx (kept out of import time)."""
    import httpx

    try:
        resp = httpx.get(url, timeout=60.0, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise EffDataFetchError(
            f"Failed to download {url}: {e}. Check connectivity and that the pinned commit still exists upstream."
        ) from e
    return resp.content


def parse_tab(blob: bytes) -> list[EffDataRow]:
    """Parse a crisporPaper effData `.tab`: a `guide\\tseq\\tmodFreq` header then one row per guide.

    Faithful to the upstream layout -- we never re-derive or rescale `modFreq`. Malformed input
    raises rather than silently dropping rows.
    """
    text = blob.decode("utf-8")
    lines = text.splitlines()
    if not lines:
        raise EffDataFetchError("Empty effData file (no header row).")

    header = [h.strip().lower() for h in lines[0].split("\t")]
    if header[:3] != ["guide", "seq", "modfreq"]:
        raise EffDataFetchError(
            f"Unexpected effData header {lines[0]!r}; expected a 'guide<TAB>seq<TAB>modFreq' header. "
            "The upstream format may have changed -- re-verify before trusting the parse."
        )

    rows: list[EffDataRow] = []
    for lineno, line in enumerate(lines[1:], start=2):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            raise EffDataFetchError(f"Malformed effData row {lineno} (need 3 columns): {line!r}")
        guide_name, seq = parts[0].strip(), parts[1].strip().upper()
        try:
            mod_freq = float(parts[2])
        except ValueError as e:
            raise EffDataFetchError(f"Non-numeric modFreq at effData row {lineno}: {parts[2]!r}") from e
        rows.append(EffDataRow(guide_name=guide_name, seq=seq, mod_freq=mod_freq))
    return rows


def load_dataset(
    name: str,
    *,
    settings: Settings | None = None,
    download_fn: DownloadFn | None = None,
    local_path: str | Path | None = None,
) -> LoadedDataset:
    """Load + verify + parse one effData dataset.

    Acquisition order: a `local_path` you supply (no network, no consent gate) -> the commit-scoped
    cache from a prior run -> a fresh fetch (consent-gated). Every path is sha256-verified against
    the committed expectation before the bytes are parsed or cached, and the parsed row count is
    checked against the spec. Returns parsed rows plus provenance (source + verified hash).
    """
    s = settings if settings is not None else _default_settings
    spec = DATASETS.get(name)
    if spec is None:
        raise EffDataUnknown(
            f"Unknown effData dataset {name!r}. Registered: {sorted(DATASETS)}. Add it to "
            "benchmarks.effdata.DATASETS (with its verified sha256) before requesting it."
        )

    if local_path is not None:
        path = Path(local_path).expanduser()
        try:
            blob = path.read_bytes()
        except OSError as e:
            raise EffDataFetchError(f"Could not read supplied effData file {path}: {e}") from e
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
            url = raw_url(s.crispor_effdata_commit, spec.upstream_relpath)
            blob = fetch(url)
            source = f"fetch:{url}"
            verified = _verify(blob, spec, source)  # verify BEFORE caching -- never cache bad bytes
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(blob)

    rows = parse_tab(blob)
    if len(rows) != spec.n_rows:
        raise EffDataFetchError(
            f"Dataset {spec.name!r} parsed {len(rows)} rows but the spec expects {spec.n_rows}. "
            "Refusing to benchmark on an unexpected row count -- re-verify the spec / source."
        )
    return LoadedDataset(spec=spec, rows=rows, source=source, sha256=verified)
