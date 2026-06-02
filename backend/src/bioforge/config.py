from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
    )

    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    db_url: str = Field(default="sqlite+aiosqlite:///./bioforge.db", alias="BIOFORGE_DB_URL")
    default_model: str = Field(default="claude-sonnet-4-6", alias="BIOFORGE_DEFAULT_MODEL")
    default_project_id: str = Field(default="default-project", alias="BIOFORGE_DEFAULT_PROJECT_ID")
    entrez_email: str = Field(default="", alias="BIOFORGE_ENTREZ_EMAIL")
    max_agent_iterations: int = Field(default=4, alias="BIOFORGE_MAX_AGENT_ITERATIONS")

    # Grounding validator (BioForge v4 §4). Default OFF. When enabled, the agent loop
    # computes a Layer-3 numeric-grounding report over each final response and records it
    # as a `validation` trace step. SHADOW mode only for now: the report is observed and
    # recorded but NEVER alters the response. Enforcement (visible redaction of
    # unsupported claims) is a later slice. Default-off keeps the loop behaviorally
    # identical until the flag is flipped.
    grounding_enabled: bool = Field(default=True, alias="BIOFORGE_GROUNDING_ENABLED")
    # "shadow" (observe + record only) or "enforce" (also redact unsupported numeric claims
    # in place, with an audit note). Only consulted when grounding_enabled=True. Default
    # "shadow" so enabling the validator never silently changes a response until you opt in
    # to enforcement. Enforcement currently covers the numeric layer (L3) only.
    # "shadow" (observe + record only), "annotate" (append a visible grounding summary to
    # the response -- flags untraceable claims without removing anything; recommended for
    # real use), or "enforce" (redact unsupported numeric/identifier claims in place with an
    # audit note). Only consulted when grounding_enabled=True. Default "shadow" so enabling
    # the validator never changes a response until you choose annotate/enforce.
    grounding_mode: str = Field(default="annotate", alias="BIOFORGE_GROUNDING_MODE")
    # Layer 4 entity/mechanistic LLM judge. Default OFF and independent of the (free,
    # deterministic) numeric layer, because it makes an extra model call per response.
    # Only consulted when grounding_enabled=True as well. The blueprint recommends Opus
    # for the judge; set BIOFORGE_GROUNDING_JUDGE_MODEL to that model id. Empty string =
    # reuse the run's model (so it works out of the box without asserting a model name).
    grounding_judge_enabled: bool = Field(default=False, alias="BIOFORGE_GROUNDING_JUDGE_ENABLED")
    grounding_judge_model: str = Field(default="", alias="BIOFORGE_GROUNDING_JUDGE_MODEL")

    # OpenTelemetry — disabled by default so the test suite stays quiet. Enable via
    # BIOFORGE_OTEL_ENABLED=true. The exporter defaults to console; set
    # BIOFORGE_OTEL_EXPORTER=otlp + BIOFORGE_OTEL_ENDPOINT for real ingest.
    otel_enabled: bool = Field(default=False, alias="BIOFORGE_OTEL_ENABLED")
    otel_exporter: str = Field(default="console", alias="BIOFORGE_OTEL_EXPORTER")  # console | none | otlp
    otel_endpoint: str = Field(default="http://localhost:4318/v1/traces", alias="BIOFORGE_OTEL_ENDPOINT")
    otel_headers: str = Field(default="", alias="BIOFORGE_OTEL_HEADERS")

    # inDelphi (Shen 2018) — opt-in CRISPR edit-outcome predictor. The upstream
    # model carries a non-commercial-research-only license so we never bundle
    # its weights. The fetcher downloads them into `indelphi_data_dir` on first
    # use, but ONLY if `indelphi_consent_noncommercial=True` — the user has to
    # acknowledge the license terms before the fetcher will touch the network.
    # `indelphi_upstream_commit` pins the exact source commit so behavior is
    # reproducible and weight provenance is auditable.
    indelphi_data_dir: str = Field(default="", alias="BIOFORGE_INDELPHI_DATA_DIR")
    indelphi_consent_noncommercial: bool = Field(default=False, alias="BIOFORGE_INDELPHI_CONSENT_NONCOMMERCIAL")
    indelphi_upstream_commit: str = Field(
        default="9ab67ca53ebb91e49aeb4530ec1e999ee9827ca1",
        alias="BIOFORGE_INDELPHI_UPSTREAM_COMMIT",
    )

    # DeepCRISPR (Chuai 2018) -- opt-in deep on-target efficacy scorer. Apache-2.0, so
    # there is NO consent gate (unlike inDelphi). DeepCRISPR is TensorFlow 1.3 / Python
    # 3.6 and CANNOT run in this interpreter, so it executes OUT OF PROCESS in a pinned
    # legacy environment: a digest-pinned Docker image by default, or a local conda
    # python (`deepcrispr_python`) as a fallback. The fetcher downloads the Apache-2.0
    # weights into `deepcrispr_data_dir` on first use, pinned to `deepcrispr_upstream_commit`
    # for reproducible provenance. The score tool degrades gracefully (it still returns the
    # deterministic rule-based score) when the legacy env is absent or `deepcrispr_enabled`
    # is False, so the default configuration is behaviorally identical to before.
    deepcrispr_enabled: bool = Field(default=False, alias="BIOFORGE_DEEPCRISPR_ENABLED")
    deepcrispr_data_dir: str = Field(default="", alias="BIOFORGE_DEEPCRISPR_DATA_DIR")
    deepcrispr_runner: str = Field(default="docker", alias="BIOFORGE_DEEPCRISPR_RUNNER")  # docker | local
    deepcrispr_docker_image: str = Field(default="", alias="BIOFORGE_DEEPCRISPR_DOCKER_IMAGE")
    deepcrispr_python: str = Field(default="", alias="BIOFORGE_DEEPCRISPR_PYTHON")
    deepcrispr_upstream_commit: str = Field(
        # TODO(validation): pin to a real bm2-lab/DeepCRISPR commit SHA before enabling.
        default="master",
        alias="BIOFORGE_DEEPCRISPR_UPSTREAM_COMMIT",
    )
    deepcrispr_timeout_seconds: float = Field(default=300.0, alias="BIOFORGE_DEEPCRISPR_TIMEOUT_SECONDS")

    # Lindel (Chen 2019) -- opt-in per-guide edit-outcome predictor (logistic regression).
    # MIT, so NO consent gate. Pure numpy/scipy with bundled weights, but run OUT OF PROCESS
    # in a pinned env (Docker image, or a local `lindel_python`) to keep it isolated and
    # uniform with the other ML scorers -- the env carries the weights, so there is no
    # separate weight fetch. edit_outcome(model="lindel") degrades gracefully (rule_of_thumb
    # still works) when the env is absent or `lindel_enabled` is False.
    lindel_enabled: bool = Field(default=False, alias="BIOFORGE_LINDEL_ENABLED")
    lindel_runner: str = Field(default="docker", alias="BIOFORGE_LINDEL_RUNNER")  # docker | local
    lindel_docker_image: str = Field(default="", alias="BIOFORGE_LINDEL_DOCKER_IMAGE")
    lindel_python: str = Field(default="", alias="BIOFORGE_LINDEL_PYTHON")
    lindel_upstream_commit: str = Field(
        # Validated 2026-05-29 against this shendurelab/Lindel commit (built into bioforge/lindel:legacy).
        default="fdcad580ba76bcfb7a98f58c3769b76f31693d63",
        alias="BIOFORGE_LINDEL_UPSTREAM_COMMIT",
    )
    lindel_timeout_seconds: float = Field(default=120.0, alias="BIOFORGE_LINDEL_TIMEOUT_SECONDS")

    # FORECasT (Allen 2018) -- opt-in per-guide edit-outcome predictor. MIT, NO consent gate.
    # Python 3 + a compiled C++ component (indelmap), so it runs OUT OF PROCESS in a thin image
    # built FROM the authors' official image (quay.io/felicityallen/selftarget) -- build it
    # (models/forecast/legacy/) and set BIOFORGE_FORECAST_DOCKER_IMAGE -- or a local
    # `forecast_python`. The image bundles the model + indelmap, so there is no weight fetch.
    forecast_enabled: bool = Field(default=False, alias="BIOFORGE_FORECAST_ENABLED")
    forecast_runner: str = Field(default="docker", alias="BIOFORGE_FORECAST_RUNNER")  # docker | local
    forecast_docker_image: str = Field(default="", alias="BIOFORGE_FORECAST_DOCKER_IMAGE")
    forecast_python: str = Field(default="", alias="BIOFORGE_FORECAST_PYTHON")
    forecast_timeout_seconds: float = Field(default=300.0, alias="BIOFORGE_FORECAST_TIMEOUT_SECONDS")

    # Azimuth / Doench Rule Set 2 (Doench 2016) -- opt-in SECONDARY on-target scorer, shown
    # side by side with the rule-based proxy for comparison / legacy reproducibility (never the
    # primary). BSD-3-Clause (verified 2026-05-30, docs/license_audit.md), so NO consent gate.
    # The trained scikit-learn pickles ship in the upstream repo and are version-fragile, so
    # Azimuth runs OUT OF PROCESS in a pinned env (Docker image, or a local `azimuth_python`)
    # loading the committed pickles AS-IS -- no retrain, no weight fetch.
    # score_guide_on_target(model="azimuth_rs2") degrades gracefully (the deterministic
    # rule-based score still returns) when the env is absent or `azimuth_enabled` is False, so
    # the default configuration is behaviorally identical to before.
    # Validated 2026-05-30 end-to-end against bioforge/azimuth:legacy (V3_model_nopos loads under
    # the pinned scikit-learn 0.23.2; deterministic). Still opt-in / off by default.
    azimuth_enabled: bool = Field(default=False, alias="BIOFORGE_AZIMUTH_ENABLED")
    azimuth_runner: str = Field(default="docker", alias="BIOFORGE_AZIMUTH_RUNNER")  # docker | local
    azimuth_docker_image: str = Field(default="", alias="BIOFORGE_AZIMUTH_DOCKER_IMAGE")
    azimuth_python: str = Field(default="", alias="BIOFORGE_AZIMUTH_PYTHON")
    azimuth_upstream_commit: str = Field(
        # Biomatters/Azimuth (py3 port) master @ 2022-11-21; the image was built + validated from it.
        default="dbd30b9d74f90f1846c0a31bcafcec8b36215af7",
        alias="BIOFORGE_AZIMUTH_UPSTREAM_COMMIT",
    )
    azimuth_timeout_seconds: float = Field(default=300.0, alias="BIOFORGE_AZIMUTH_TIMEOUT_SECONDS")

    # MAFFT (Katoh et al.) -- multiple-sequence alignment for the section 3 / Phase 4 MSA viewer.
    # CORE MAFFT is BSD-3-Clause (verified 2026-06-02, docs/license_audit.md) -> commercial-clean,
    # NO consent gate. CRITICAL: MAFFT's bundled *extensions* (Vienna RNA, MXSCARNA) are restrictively
    # licensed, so the image MUST be core-only. Runs OUT OF PROCESS in a digest-pinned core-only image
    # (BIOFORGE_MAFFT_DOCKER_IMAGE; see models/mafft/legacy/README.md) or via a local `mafft` binary.
    # There is NO pure-Python fallback: align_msa refuses with setup guidance when the env is absent
    # (no faked alignment), so the default configuration simply does not offer MSA until configured.
    mafft_enabled: bool = Field(default=False, alias="BIOFORGE_MAFFT_ENABLED")
    mafft_runner: str = Field(default="docker", alias="BIOFORGE_MAFFT_RUNNER")  # docker | local
    mafft_docker_image: str = Field(default="", alias="BIOFORGE_MAFFT_DOCKER_IMAGE")
    mafft_binary: str = Field(default="mafft", alias="BIOFORGE_MAFFT_BINARY")
    mafft_timeout_seconds: float = Field(default=300.0, alias="BIOFORGE_MAFFT_TIMEOUT_SECONDS")

    # DeepVariant (Poplin et al. 2018) -- the variant CALLER for the section 13 GIAB concordance
    # benchmark. BSD-3-Clause (verified 2026-06-02, docs/license_audit.md) -> commercial-clean,
    # NO consent gate. Runs OUT OF PROCESS in a digest-pinned image (run_deepvariant pipeline) over
    # a reads BAM + reference; there is no pure-Python fallback. The GIAB benchmark stays guard_only
    # (it needs Docker + the GRCh38 reference + the HG002 truth set, so it never runs on a page load).
    # The reference build is USER-CONFIRMED, never assumed (section 10).
    deepvariant_enabled: bool = Field(default=False, alias="BIOFORGE_DEEPVARIANT_ENABLED")
    deepvariant_docker_image: str = Field(default="", alias="BIOFORGE_DEEPVARIANT_DOCKER_IMAGE")
    deepvariant_model_type: str = Field(
        default="WGS", alias="BIOFORGE_DEEPVARIANT_MODEL_TYPE"
    )  # WGS|WES|PACBIO|ONT_R104
    deepvariant_num_shards: int = Field(default=2, alias="BIOFORGE_DEEPVARIANT_NUM_SHARDS")
    deepvariant_timeout_seconds: float = Field(default=14400.0, alias="BIOFORGE_DEEPVARIANT_TIMEOUT_SECONDS")

    # GIAB benchmark inputs (section 13). All user-supplied paths; the reference BUILD must be stated
    # explicitly (never assumed). Empty by default -> the live benchmark is unavailable until staged.
    giab_reference_path: str = Field(default="", alias="BIOFORGE_GIAB_REFERENCE_PATH")
    giab_reference_build: str = Field(default="", alias="BIOFORGE_GIAB_REFERENCE_BUILD")  # e.g. GRCh38.p14
    giab_reads_path: str = Field(default="", alias="BIOFORGE_GIAB_READS_PATH")  # aligned BAM (indexed)
    giab_truth_vcf_path: str = Field(default="", alias="BIOFORGE_GIAB_TRUTH_VCF_PATH")
    giab_confident_bed_path: str = Field(default="", alias="BIOFORGE_GIAB_CONFIDENT_BED_PATH")
    giab_regions: str = Field(default="", alias="BIOFORGE_GIAB_REGIONS")  # e.g. chr20 or chr20:1-10000000

    # crisporPaper effData -- held-out guide-efficiency datasets (Haeussler/Concordet, the same
    # source the CFD matrices came from) used ONLY by the §13 on-target accuracy benchmark, never
    # at request time. The repo carries NO license file (all-rights-reserved), so its data is NEVER
    # vendored into our git history. The loader fetches a dataset on first use into
    # `crispor_effdata_dir` (default ~/.bioforge/data/crispor_effdata/), pinned to
    # `crispor_effdata_commit` for reproducibility + sha256-verified -- but ONLY if
    # `crispor_effdata_consent=True`. The flag is the sole consent signal: it acknowledges the
    # data is unlicensed and fetched transiently for benchmarking, not redistributed. No silent
    # network (mirrors the inDelphi consent gate). The same loader also accepts a user-supplied
    # local file or an alternate mirror URL, so this posture is not a one-way door.
    crispor_effdata_dir: str = Field(default="", alias="BIOFORGE_CRISPOR_EFFDATA_DIR")
    crispor_effdata_consent: bool = Field(default=False, alias="BIOFORGE_CRISPOR_EFFDATA_CONSENT")
    crispor_effdata_commit: str = Field(
        # crisporPaper master @ 2026-05-30 (immutable pin; verified live: chari2015Train.tab =
        # 1234 guides, sha256 6a6254a3...485e576). Flip to re-bootstrap from a different snapshot.
        default="33a8225c7bc3be7f937786f6b151ffa7d7e29e84",
        alias="BIOFORGE_CRISPOR_EFFDATA_COMMIT",
    )

    # (v4 §0/§4.1/§4.3) OOD input gate. "off" (default) = the OOD detector records flags
    # post-response only (behavior unchanged). "block" = refuse a tool call whose input falls
    # outside an involved model's validated envelope BEFORE it runs (the §0 inputs boundary).
    ood_gate: str = Field(default="off", alias="BIOFORGE_OOD_GATE")  # off | block

    # (v4 §0/§4.1) Execution-time soundness gate. "off" (default) records bound violations
    # post-response only. "block" rejects a tool output that violates a known physical bound
    # (an impossible value) before it feeds downstream steps -- the §0 execution boundary acting.
    soundness_gate: str = Field(default="off", alias="BIOFORGE_SOUNDNESS_GATE")  # off | block


settings = Settings()
