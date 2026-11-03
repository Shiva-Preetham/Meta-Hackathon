"""Runtime settings and every tunable threshold, each with the reason for its value.

Settings come from environment variables prefixed with ``SIGNAL_`` (and ``MODEL_API_KEY``
for the key), or from an untracked ``.env`` file. Constants that are part of the product
definition (taxonomy, severity weights, simulated plan tiers) live here too, so a reviewer
can find every number that influences a ranking in one file.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# --------------------------------------------------------------------------------------
# Product taxonomy. Spark returns free-text product areas; we map them onto this list with
# a deterministic fuzzy match (signalhq.domain.taxonomy). The default brand is a music and
# podcast subscription app, so the list reflects that product surface. "other" is the
# fallback and must stay last.
# --------------------------------------------------------------------------------------
PRODUCT_AREAS: tuple[str, ...] = (
    "playback",
    "offline_downloads",
    "search",
    "playlists_library",
    "recommendations",
    "podcasts",
    "devices_connect",
    "account_login",
    "billing_subscription",
    "family_plan",
    "ads",
    "app_performance",
    "other",
)

# Synonyms make the fuzzy match robust to Spark's wording ("login" -> account_login).
PRODUCT_AREA_SYNONYMS: dict[str, str] = {
    "audio playback": "playback",
    "player": "playback",
    "streaming": "playback",
    "downloads": "offline_downloads",
    "offline": "offline_downloads",
    "offline mode": "offline_downloads",
    "library": "playlists_library",
    "playlists": "playlists_library",
    "playlist": "playlists_library",
    "discovery": "recommendations",
    "personalization": "recommendations",
    "podcast": "podcasts",
    "devices": "devices_connect",
    "bluetooth": "devices_connect",
    "car": "devices_connect",
    "smart speaker": "devices_connect",
    "login": "account_login",
    "authentication": "account_login",
    "account": "account_login",
    "account access": "account_login",
    "password": "account_login",
    "billing": "billing_subscription",
    "payments": "billing_subscription",
    "subscription": "billing_subscription",
    "pricing": "billing_subscription",
    "family": "family_plan",
    "advertising": "ads",
    "advertisements": "ads",
    "performance": "app_performance",
    "stability": "app_performance",
    "crashes": "app_performance",
    "app stability": "app_performance",
}

# Severity -> weight used by the priority score (CLAUDE.md §6). Linear on purpose: a
# critical issue counts four times a low one, which is easy to explain and to audit.
SEVERITY_WEIGHTS: dict[str, float] = {"low": 1.0, "medium": 2.0, "high": 3.0, "critical": 4.0}

# Severities that put an account's revenue "at risk" even without an explicit churn signal.
AT_RISK_SEVERITIES: frozenset[str] = frozenset({"high", "critical"})

# --------------------------------------------------------------------------------------
# Simulated revenue (labelled simulated everywhere). Each customer id is assigned a plan
# deterministically from a seeded hash, so the same dataset always gets the same accounts.
# (name, probability, monthly price in cents per seat). Business accounts draw a seat count
# from a log-uniform range, which creates a few large accounts, as real B2B books have.
# --------------------------------------------------------------------------------------
PLAN_TIERS: tuple[tuple[str, float, int], ...] = (
    ("free", 0.55, 0),
    ("individual", 0.25, 1_000),  # $10.00 / month
    ("family", 0.12, 1_700),  # $17.00 / month
    ("business", 0.08, 1_200),  # $12.00 per seat / month
)
BUSINESS_SEATS_RANGE: tuple[int, int] = (2, 250)  # log-uniform, inclusive

SIMULATED_REVENUE_NOTE = (
    "Revenue figures are simulated for demonstration; feedback text is real public data."
)
SYNTHETIC_DATA_NOTE = (
    "Revenue figures are simulated for demonstration; feedback text is synthetic sample data."
)


class Settings(BaseSettings):
    """Process configuration. Every field can be set with ``SIGNAL_<NAME>``."""

    model_config = SettingsConfigDict(
        env_prefix="SIGNAL_",
        env_file=".env",
        extra="ignore",
        protected_namespaces=(),
    )

    # --- Meta Model API -----------------------------------------------------------------
    model_api_key: str | None = Field(
        default=None, validation_alias=AliasChoices("MODEL_API_KEY", "SIGNAL_MODEL_API_KEY")
    )
    api_base_url: str = "https://api.meta.ai/v1"
    spark_model: str = "muse-spark-1.3"
    # Optional second model tried once when the primary keeps failing. Off by default.
    fallback_model: str | None = None
    voice_model: str = "muse-voice-transcribe-1.0"
    voice_url: str = "https://api.meta.ai/v1/asr/transcribe"
    # DIARIZATION fills turns[] and speaker labels (CLAUDE.md §3).
    voice_mode: str = "DIARIZATION"
    # Force offline mode (heuristic fake transport). Default: offline when no key is set.
    offline: bool | None = None

    # Prices in USD (CLAUDE.md §3). Used for the cost ledger; never invented elsewhere.
    price_input_per_m: float = 1.25
    price_output_per_m: float = 4.25
    price_cached_input_per_m: float = 0.15
    price_voice_per_hour: float = 0.18

    # Hard budget for one database's ledger (CLAUDE.md §11 rule 4). Calls stop above it.
    budget_usd: float = 15.0

    # --- LLM gateway ------------------------------------------------------------------------
    # Third-party reports say ~3,000 req/min; that is unverified, so we stay at a fifth of it.
    llm_requests_per_minute: float = 600.0
    llm_tokens_per_minute: float = 1_000_000.0
    llm_timeout_s: float = 60.0
    llm_max_retries: int = 4  # retries after the first attempt, on 429/5xx/timeouts
    llm_backoff_base_s: float = 1.0
    llm_backoff_max_s: float = 30.0
    breaker_failure_threshold: int = 5  # consecutive transient failures before opening
    breaker_reset_s: float = 30.0  # how long the breaker stays open before a trial call
    # Sent when supported; dropped automatically if the API rejects it (unverified, §3).
    reasoning_effort: str | None = "low"
    enrich_max_output_tokens: int = 400
    name_max_output_tokens: int = 300

    # --- Storage ----------------------------------------------------------------------------
    db_path: str = "signal.db"

    # --- Job queue --------------------------------------------------------------------------
    job_max_attempts: int = 5  # after this many failures a job is dead-lettered
    # Longer than the worst-case gateway call (timeout x retries), so a healthy worker never
    # loses its lease mid-call.
    job_lease_s: float = 600.0
    job_backoff_base_s: float = 2.0
    job_backoff_max_s: float = 300.0
    worker_poll_s: float = 0.2

    # --- Embeddings and clustering ------------------------------------------------------
    # "auto" uses sentence-transformers when installed and loadable, else hashing.
    embedder: str = "auto"
    embed_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    # Times the normalised product area is appended to the embedded text as an anchor
    # token (0 disables). See ADR-005 for the measured effect and its caveat.
    embed_area_anchor: int = 2
    # Assignment threshold: cosine(item, theme centroid) needed to join an existing theme.
    # None means "use the per-embedder default" below (see ADR-005 for the sweep).
    cluster_tau: float | None = None
    # Linkage threshold for periodic re-clustering of the unassigned pool (average linkage,
    # cosine similarity). None means per-embedder default.
    cluster_link_tau: float | None = None
    # Two active themes whose centroids are at least this similar are merged.
    cluster_merge_tau: float | None = None
    min_theme_size: int = 4  # smallest group of pooled items that becomes a theme
    recluster_min_pool: int = 12  # do not re-cluster a pool smaller than this
    recluster_delay_s: float = 3.0  # batch pool growth before re-clustering
    recluster_pool_cap: int = 3000  # newest N pooled items considered (bounds O(n^2))

    # --- Theme naming -----------------------------------------------------------------------
    # Rename a theme only when it has grown by this fraction since it was last named.
    rename_growth_ratio: float = 0.5
    name_sample_size: int = 12
    name_delay_s: float = 1.0

    # --- Scoring ----------------------------------------------------------------------------
    window_days: int = 7
    score_delay_s: float = 2.0

    # --- Simulation -------------------------------------------------------------------------
    simulation_seed: int = 42

    # --- Evaluation -------------------------------------------------------------------------
    eval_sample_path: str = "eval/to_label.csv"

    # --- Server -----------------------------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8000

    @property
    def is_offline(self) -> bool:
        """Offline demo mode: no key, or explicitly forced."""
        if self.offline is not None:
            return self.offline
        return not self.model_api_key

    @property
    def effective_model(self) -> str:
        """Model id recorded in the ledger; offline runs never claim to be Spark."""
        return OFFLINE_MODEL_ID if self.is_offline else self.spark_model


OFFLINE_MODEL_ID = "offline-heuristic-v1"

# Per-embedder clustering defaults, chosen by the sweep in scripts/tune_clustering.py and
# recorded in docs/adr/ADR-005-online-clustering.md. The MiniLM values are starting points
# that have NOT been measured in this repo yet (the model could not be downloaded in the
# build environment); run the sweep before relying on them.
CLUSTER_DEFAULTS: dict[str, dict[str, float]] = {
    "hashing": {"tau": 0.20, "link_tau": 0.10, "merge_tau": 0.70},
    "minilm": {"tau": 0.55, "link_tau": 0.35, "merge_tau": 0.85},
}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
