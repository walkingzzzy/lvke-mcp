"""Versioned contracts for the Deep Research engine.

The contracts intentionally use stdlib dataclasses.  They are persisted as
JSON and consumed by background workers, HTTP adapters, and tests, so keeping
them independent from FastAPI/Pydantic avoids import-time coupling and makes
checkpoint recovery possible in a minimal worker process.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


CONTRACT_VERSION = "1.0"
SESSION_SCHEMA_VERSION = "research_session.v1"
RUN_SCHEMA_VERSION = "research_run.v1"

ResearchProfile = Literal["quick", "deep_assist", "deep_standard", "deep_max"]
ResearchMaturity = Literal["none", "draft", "certified"]
ResearchStage = Literal[
    "clarify",
    "plan",
    "acquire",
    "certify",
    "compose",
    "idle",
]
ResearchRunKind = Literal[
    "initial",
    "continuation",
    "recertify",
    "report_regeneration",
    "legacy_automatic",
]
StaleFlag = Literal[
    "brief",
    "plan",
    "collection",
    "evidence",
    "report",
]
TaskStatus = Literal[
    "pending",
    "running",
    "clarifying",
    "needs_clarification",
    "planning",
    "searching",
    "normalizing",
    "extracting",
    "evidence_building",
    "reflecting",
    "report_planning",
    "drafting",
    "citation_auditing",
    "quality_check",
    "done",
    "partial",
    "blocked",
    "failed",
    "failed_report",
    "cancelled",
]

TERMINAL_STATUSES = frozenset(
    {
        "done",
        "partial",
        "needs_clarification",
        "blocked",
        "failed",
        "failed_report",
        "cancelled",
    }
)


@dataclass(slots=True)
class JsonContract:
    """Small JSON projection shared by all persisted dataclasses."""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ResearchBudget(JsonContract):
    max_search_calls: int = 80
    max_rounds: int = 4
    max_duration_seconds: int = 1800
    max_extract_calls: int = 80
    max_report_repairs: int = 2


@dataclass(slots=True)
class ResearchPolicy(JsonContract):
    profile: ResearchProfile = "deep_standard"
    contract_version: str = CONTRACT_VERSION
    min_rounds: int = 2
    min_query_variants: int = 3
    min_search_calls: int = 20
    min_raw_hits: int = 100
    min_unique_candidates: int = 50
    min_extracted_sources: int = 30
    min_independent_domains: int = 20
    min_independent_publishers: int = 20
    min_core_node_coverage: float = 1.0
    min_overall_node_coverage: float = 0.85
    max_empty_core_nodes: int = 0
    max_provider_error_rate: float = 0.20
    min_ab_source_ratio: float = 0.70
    min_primary_source_ratio: float = 0.30
    min_independent_sources_for_core_claim: int = 2
    min_independent_sources_for_quant_claim: int = 3
    min_verified_quantitative_claims: int = 3
    max_unsupported_core_claims: int = 0
    max_dead_cited_sources: int = 0
    require_exact_quote_for_quant_claim: bool = True
    min_report_body_chars: int = 6000
    min_supported_factual_chars: int = 2500
    min_net_non_extractive_research_chars: int = 2000
    min_net_research_content_ratio: float = 0.25
    min_substantive_sections: int = 6
    min_substantive_section_chars: int = 400
    min_cited_sources: int = 15
    min_report_factual_paragraphs: int = 15
    min_report_quantitative_statements: int = 3
    min_factual_paragraph_citation_coverage: float = 1.0
    min_report_section_alignment: float = 0.80
    min_multi_source_synthesis_ratio: float = 0.30
    max_unknown_citation_ids: int = 0
    max_unused_references: int = 0
    max_unsupported_cited_paragraphs: int = 0
    max_unsupported_quantitative_statements: int = 0
    max_quantitative_source_floor_failures: int = 0
    max_near_duplicate_factual_paragraphs: int = 0
    max_malformed_cited_paragraphs: int = 0
    max_extractive_paragraph_ratio: float = 0.40
    max_fallback_section_ratio: float = 0.25
    max_prompt_injection_findings: int = 0
    max_sensitive_data_findings: int = 0
    require_methodology: bool = True
    require_limitations: bool = True
    require_conflict_disclosure: bool = True
    reject_finance_redline: bool = True
    search_concurrency: int = 4
    extract_concurrency: int = 4
    allow_provider_fallback: bool = True
    budget: ResearchBudget = field(default_factory=ResearchBudget)

    @property
    def is_deep(self) -> bool:
        return self.profile in {"deep_assist", "deep_standard", "deep_max"}


@dataclass(slots=True)
class ResearchBrief(JsonContract):
    """Normalized research specification consumed by planning and reporting."""

    topic: str
    objective: str = ""
    scope: dict[str, Any] = field(default_factory=dict)
    required_dimensions: list[str] = field(default_factory=list)
    source_priorities: list[str] = field(default_factory=list)
    output_contract: dict[str, Any] = field(default_factory=dict)
    open_questions: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    status: Literal["ready", "needs_clarification"] = "ready"
    # P2 extensions (optional; default-empty keeps backward compatibility)
    template_id: str = ""
    template_version: str = ""
    clarification_items: list[dict[str, Any]] = field(default_factory=list)
    plan_approval_blocked: bool = False
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ResearchStartRequest(JsonContract):
    topic: str
    workspace_id: str = ""
    owner_actor: str = ""
    industry: str = ""
    region: str = ""
    profile: ResearchProfile = "deep_standard"
    research_brief: dict[str, Any] = field(default_factory=dict)
    plan_items: list[dict[str, Any]] = field(default_factory=list)
    chapters: list[str] = field(default_factory=list)
    source_policy: dict[str, Any] = field(default_factory=dict)
    source_policy_warnings: list[str] = field(default_factory=list)
    budget: dict[str, Any] = field(default_factory=dict)
    output_contract: dict[str, Any] = field(default_factory=dict)
    policy_overrides: dict[str, Any] = field(default_factory=dict)
    analysis_inputs: list[dict[str, Any]] = field(default_factory=list)
    connector_inputs: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class ResearchPlanNode(JsonContract):
    node_id: str
    question: str
    chapter_ref: str = ""
    intent: str = ""
    expected_claim_types: list[str] = field(default_factory=list)
    required_source_types: list[str] = field(default_factory=list)
    min_independent_sources: int = 2
    priority: Literal["critical", "normal", "supporting"] = "normal"
    status: str = "planned"


@dataclass(slots=True)
class ResearchPlan(JsonContract):
    topic: str
    industry: str = ""
    region: str = ""
    nodes: list[ResearchPlanNode] = field(default_factory=list)
    source: str = ""
    filtered_finance: list[str] = field(default_factory=list)
    augmentation_suggestions: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class TypedQuery(JsonContract):
    query_id: str
    query: str
    query_type: str
    plan_node_id: str
    round_no: int = 1
    priority: int = 0
    preferred_domains: list[str] = field(default_factory=list)
    excluded_domains: list[str] = field(default_factory=list)
    provenance: str = ""
    visibility: str = "public"


@dataclass(slots=True)
class CollectionWorkItemAttempt(JsonContract):
    """Projection of one SearchBatch attempt for UI/task panel."""

    attempt: int = 1
    provider: str = ""
    status: str = ""
    error_code: str = ""
    hit_count: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0
    cache_hit: bool = False
    provider_call_id: str = ""


@dataclass(slots=True)
class CollectionWorkItem(JsonContract):
    """First-class projection contract for collection task panel (P4).

    Still projected from TypedQuery + SearchBatch; not a separate storage root.
    learning_summary is Draft UI only and must carry source/evidence refs.
    """

    schema_version: str = "collection_work_item.v1"
    work_item_id: str = ""
    query_id: str = ""
    plan_node_id: str = ""
    query_text: str = ""
    research_goal: str = ""
    query_type: str = ""
    state: str = "queued"
    current_attempt: int = 0
    attempts: list[dict[str, Any]] = field(default_factory=list)
    hit_count: int = 0
    extract_count: int = 0
    bindable_count: int = 0
    learning_summary: dict[str, Any] = field(default_factory=dict)
    budget_cost: float = 0.0
    billed_provider_call_ids: list[str] = field(default_factory=list)
    last_error_code: str = ""
    updated_at: str = ""


@dataclass(slots=True)
class SearchHit(JsonContract):

    title: str
    url: str
    snippet: str = ""
    position: int = 0
    provider: str = ""
    query_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SearchBatch(JsonContract):
    query_id: str
    query: str
    query_type: str
    plan_node_id: str
    provider: str
    attempt: int = 1
    status: Literal[
        "ok",
        "zero_result",
        "timeout",
        "rate_limited",
        "config_error",
        "cancelled",
        "error",
    ] = "ok"
    hits: list[SearchHit] = field(default_factory=list)
    latency_ms: int = 0
    error_code: str = ""
    error_message: str = ""
    retry_after_seconds: float = 0.0
    cost_usd: float = 0.0
    cache_hit: bool = False
    provider_call_id: str = ""
    route_options: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SourceRecord(JsonContract):
    source_id: str
    canonical_url: str
    url: str
    title: str = ""
    publisher: str = ""
    publisher_id: str = ""
    registrable_domain: str = ""
    snippet: str = ""
    tier: str = "C"
    source_type: str = "web"
    language: str = ""
    published_at: str = ""
    updated_at: str = ""
    content_hash: str = ""
    content_type: str = ""
    original_url: str = ""
    archive_url: str = ""
    url_checked_at: str = ""
    duplicate_of: str = ""
    is_reprint: bool = False
    query_ids: list[str] = field(default_factory=list)
    plan_node_ids: list[str] = field(default_factory=list)
    extract_status: str = "not_run"
    url_status: str = "unchecked"
    visibility: str = "public"
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ExtractRecord(JsonContract):
    source_id: str
    url: str
    status: Literal["ok", "failed", "blocked"] = "ok"
    title: str = ""
    content: str = ""
    chunks: list[str] = field(default_factory=list)
    content_hash: str = ""
    error_code: str = ""
    error_message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    bindable_ok: bool = False
    bindable_sentence_count: int = 0


@dataclass(slots=True)
class EvidenceRecord(JsonContract):
    evidence_id: str
    source_id: str
    quote: str
    context: str = ""
    page: int | None = None
    start_offset: int | None = None
    end_offset: int | None = None
    relevance: float = 0.0
    stance: Literal["supporting", "contradicting", "context"] = "supporting"
    grounding: str = "not_checked"
    plan_node_id: str = ""
    family_id: str = ""


@dataclass(slots=True)
class ClaimNode(JsonContract):
    claim_id: str
    text: str
    claim_type: str = "qualitative"
    plan_node_id: str = ""
    quantitative: bool = False
    region: str = ""
    period: str = ""
    unit: str = ""
    status: str = "unverified"
    evidence_ids: list[str] = field(default_factory=list)
    independent_domains: list[str] = field(default_factory=list)
    source_tiers: list[str] = field(default_factory=list)
    caveat: str = ""
    chapter_refs: list[str] = field(default_factory=list)
    core: bool = False
    family_id: str = ""
    primary_section: str = ""


@dataclass(slots=True)
class EvidenceGraph(JsonContract):
    claims: dict[str, ClaimNode] = field(default_factory=dict)
    evidence: dict[str, EvidenceRecord] = field(default_factory=dict)
    sources: dict[str, SourceRecord] = field(default_factory=dict)
    families: dict[str, dict] = field(default_factory=dict)


@dataclass(slots=True)
class ReflectionResult(JsonContract):
    round_no: int
    coverage: float
    core_coverage: float
    gaps: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    freshness_gaps: list[str] = field(default_factory=list)
    authority_gaps: list[str] = field(default_factory=list)
    next_queries: list[TypedQuery] = field(default_factory=list)
    continue_research: bool = True
    gap_ledger: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ResearchMetrics(JsonContract):
    round_no: int = 0
    search_calls: int = 0
    provider_search_calls: int = 0
    search_cache_hits: int = 0
    failed_search_calls: int = 0
    raw_hits: int = 0
    unique_candidates: int = 0
    effective_unique_candidates: int = 0
    duplicate_content_candidates: int = 0
    extracted_sources: int = 0
    independent_domains: int = 0
    independent_publishers: int = 0
    core_node_coverage: float = 0.0
    overall_node_coverage: float = 0.0
    empty_core_nodes: int = 0
    ab_source_ratio: float = 0.0
    primary_source_ratio: float = 0.0
    unsupported_core_claims: int = 0
    quantitative_claims_without_exact_quote: int = 0
    quantitative_claims_below_source_floor: int = 0
    verified_quantitative_claims: int = 0
    report_quantitative_statements: int = 0
    unsupported_quantitative_statements: int = 0
    quantitative_source_floor_failures: int = 0
    bindable_ok_sources: int = 0
    bindable_gov_sentences: int = 0
    quant_families_total: int = 0
    quant_families_multi_host: int = 0
    quant_families_verified: int = 0
    cited_sources: int = 0
    dead_cited_sources: int = 0
    report_body_chars: int = 0
    supported_factual_chars: int = 0
    net_non_extractive_research_chars: int = 0
    net_research_content_ratio: float = 0.0
    substantive_sections: int = 0
    minimum_substantive_section_chars: int = 0
    report_factual_paragraphs: int = 0
    near_duplicate_factual_paragraphs: int = 0
    malformed_cited_paragraphs: int = 0
    extractive_factual_paragraphs: int = 0
    extractive_paragraph_ratio: float = 0.0
    aligned_factual_paragraphs: int = 0
    report_section_alignment: float = 0.0
    multi_source_factual_paragraphs: int = 0
    multi_source_synthesis_ratio: float = 0.0
    drafted_report_sections: int = 0
    fallback_report_sections: int = 0
    fallback_section_ratio: float = 0.0
    factual_paragraph_citation_coverage: float = 0.0
    unknown_citation_ids: int = 0
    unused_references: int = 0
    unsupported_cited_paragraphs: int = 0
    prompt_injection_findings: int = 0
    sensitive_data_findings: int = 0
    has_methodology: bool = False
    has_limitations: bool = False
    has_conflict_disclosure: bool = False
    finance_redline_terms: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    search_latency_ms_total: int = 0
    search_latency_p50_ms: int = 0
    search_latency_p95_ms: int = 0
    estimated_cost_usd: float = 0.0
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    deadline_exhausted: bool = False
    provider_health: dict[str, Any] = field(default_factory=dict)

    @property
    def provider_error_rate(self) -> float:
        if self.search_calls <= 0:
            return 0.0
        return self.failed_search_calls / self.search_calls

    @property
    def deduplicated_candidates(self) -> int:
        return self.effective_unique_candidates or self.unique_candidates

    @property
    def publisher_count(self) -> int:
        return self.independent_publishers or self.independent_domains


@dataclass(slots=True)
class QualityGateResult(JsonContract):
    gate: Literal["research", "evidence", "report", "combined"]
    passed: bool
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    evaluated_at: str = ""


@dataclass(slots=True)
class ResearchRunResult(JsonContract):
    status: TaskStatus
    policy: ResearchPolicy
    plan: ResearchPlan
    metrics: ResearchMetrics
    quality: QualityGateResult
    research_brief: ResearchBrief | None = None
    queries: list[TypedQuery] = field(default_factory=list)
    search_batches: list[SearchBatch] = field(default_factory=list)
    sources: list[SourceRecord] = field(default_factory=list)
    extracts: list[ExtractRecord] = field(default_factory=list)
    evidence_graph: EvidenceGraph = field(default_factory=EvidenceGraph)
    reflections: list[ReflectionResult] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    report_md: str = ""
    report_outline: dict[str, Any] = field(default_factory=dict)
    report_sections: list[dict[str, Any]] = field(default_factory=list)
    citation_audit: dict[str, Any] = field(default_factory=dict)
    analysis_results: list[dict[str, Any]] = field(default_factory=list)
    references: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""


@dataclass(slots=True)
class ResearchSession(JsonContract):
    """Versioned multi-run research session (P0 domain root)."""

    session_id: str
    workspace_id: str
    owner_actor: str = ""
    schema_version: str = SESSION_SCHEMA_VERSION
    template_id: str = ""
    template_version: str = ""
    template_revision_id: str = ""
    template_fingerprint: str = ""
    topic: str = ""
    current_brief_revision_id: str = ""
    current_plan_revision_id: str = ""
    current_binding_set_id: str = ""
    current_run_id: str = ""
    current_report_artifact_id: str = ""
    research_maturity: ResearchMaturity = "none"
    stage: ResearchStage = "idle"
    stale_flags: list[str] = field(default_factory=list)
    run_ids: list[str] = field(default_factory=list)
    revision: int = 0
    created_at: str = ""
    updated_at: str = ""
    legacy_task_id: str = ""


@dataclass(slots=True)
class ResearchRunRef(JsonContract):
    """Immutable research run lineage record bound to a task/checkpoint."""

    run_id: str
    session_id: str
    task_id: str
    schema_version: str = RUN_SCHEMA_VERSION
    parent_run_id: str = ""
    run_kind: ResearchRunKind = "initial"
    bound_brief_revision_id: str = ""
    bound_plan_revision_id: str = ""
    bound_binding_set_id: str = ""
    template_revision_id: str = ""
    template_fingerprint: str = ""
    status: str = "pending"
    current_step: str = "pending"
    checkpoint_version: int = 1
    budget_snapshot: dict[str, Any] = field(default_factory=dict)
    policy_snapshot: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    finished_at: str = ""


@dataclass(slots=True)
class BriefRevisionRef(JsonContract):
    brief_revision_id: str
    session_id: str
    base_revision_id: str = ""
    status: Literal["proposed", "accepted", "rejected", "superseded"] = "proposed"
    created_at: str = ""
    accepted_at: str = ""
    brief_payload: dict[str, Any] = field(default_factory=dict)
    patch: dict[str, Any] = field(default_factory=dict)
    feedback_text: str = ""
    diff_summary: str = ""


@dataclass(slots=True)
class PlanRevisionRef(JsonContract):
    plan_revision_id: str
    session_id: str
    based_on_brief_revision_id: str = ""
    status: Literal["proposed", "accepted", "rejected", "superseded", "legacy_automatic"] = (
        "proposed"
    )
    fingerprint: str = ""
    accepted_at: str = ""
    created_at: str = ""
    nodes: list[dict[str, Any]] = field(default_factory=list)
    augmentation_suggestions: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class ResourceBindingSetRef(JsonContract):
    binding_set_id: str
    session_id: str
    fingerprint: str = ""
    created_at: str = ""
    bindings: list[dict[str, Any]] = field(default_factory=list)
