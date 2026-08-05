"""Autarch — an AI-native operating layer.

You don't use AI. You preside over it.
"""
from __future__ import annotations

from .adapters import (
    Adapter,
    AzureAISearchAdapter,
    DocumentAdapter,
    ElasticsearchAdapter,
    ExtractionAdapter,
    FileSystemAdapter,
    RestSearchAdapter,
    SQLAdapter,
    SearchAdapter,
    SearchHit,
    ToolAdapter,
    VectorSearchAdapter,
    connect_mysql,
    connect_oracle,
    connect_postgres,
    connect_sqlite,
    connect_sqlserver,
    from_callables,
    from_langchain_tools,
    from_mcp_tools,
)
from .agent import Agent, RunResult, capability
from .approval import Approval, ApprovalQueue
from .compliance import (
    ComplianceReport,
    ComplianceReporter,
    Control,
    markdown_report,
)
from .gateway import GatewayClient, GovernanceGateway
from .intelligence.anthropic import AnthropicProvider
from .intelligence.openai import OpenAIProvider
from .intelligence.pricing import DEFAULT_PRICE_BOOK, PriceBook, estimate_tokens
from .policydsl import compile_policies, compile_policy, diff, simulate
from .verification import VerificationResult, verify_kernel
from . import scoping
from .contracts import Action, CapabilityGrant, HumanDecision, Intent, WhyRecord
from .delegation import attenuate_grant, delegate
from .economy import Budget, BudgetDecision, CostModel, EconomicKernel
from .evaluation import (
    AssertionEvaluator,
    Citation,
    Citer,
    ConsensusEvaluator,
    CoverageEvaluator,
    EvaluationPanel,
    Evaluator,
    GroundednessEvaluator,
    InjectionEvaluator,
    PIIEvaluator,
    PanelReport,
    ReflectionResult,
    RubricJudge,
    Verdict,
    check_grounding,
    cite,
    compress_history,
    extractive_summary,
    quality_panel,
    reflect,
    safety_panel,
)
from .errors import (
    AccessDenied,
    AdapterError,
    BudgetExceeded,
    CapabilityDenied,
    CircuitOpen,
    DelegationError,
    GovernanceError,
    ModelError,
    ModelUnavailable,
    PolicyDenied,
    RateLimited,
    SecretError,
    AutarchError,
    ValidationError,
)
from .events import CallbackSink, Event, EventSink, ListSink, NullSink
from .guarantees import GuaranteeReport, Invariant, Proof, prove_guarantees
from .health import health_check
from .langchain_bridge import (
    GovernedLangChainTool,
    as_langchain_tool,
    as_langchain_tools,
    govern_langchain_tools,
)
from .maf_bridge import (
    MAFModelProvider,
    as_maf_tool,
    as_maf_tools,
    govern_maf_tools,
    governed_function_middleware,
)
from .mcp import MCPClient, MCPServer, from_mcp_server
from .mesh import Cipher, MergeReport, Realm, export_bundle, import_bundle
from .orchestration import (
    ChildResult,
    ConcatSynthesizer,
    ModelPlanner,
    ModelSynthesizer,
    Orchestrator,
    OrchestrationResult,
    Plan,
    Planner,
    RulePlanner,
    Specialist,
    SpecialistRegistry,
    Subtask,
    Synthesizer,
)
from .policy import Policy, PolicyDecision, PolicyEffect
from .precedent import Precedent
from .provenance import NodeIdentity, derive_node_id, verify_signature
from .rbac import AccessControl, Principal, Role, RoleRegistry
from .recall import (
    EPISODIC,
    PROCEDURAL,
    SEMANTIC,
    WORKING,
    MemoryEntry,
    RecallMemory,
)
from .intelligence.embedding import EmbeddingProvider, HashingEmbedder, OllamaEmbedder
from .intelligence.factory import build_embedder, build_provider
from .intelligence.openai_embedding import OpenAIEmbedder
from .intelligence.usage import CallUsage, UsageMeter, current_label, get_usage_meter, record_usage, usage_label
from .intelligence.vision import ImageRef
from .resilience import (
    AdaptiveExecutor,
    CircuitBreaker,
    RateLimit,
    Resilient,
    RetryPolicy,
    TaskOutcome,
    count_tokens,
    make_resilient,
)
from .rewind import Rewinder, RewindStep
from .runlog import RunJournal, RunState
from .substrate import Substrate
from .telemetry import JsonlSink, otel_available, otel_sink
from .transport import GossipReport, MeshServer, gossip

__all__ = [
    "Agent",
    "RunResult",
    "capability",
    "Action",
    "CapabilityGrant",
    "HumanDecision",
    "Intent",
    "WhyRecord",
    "Policy",
    "PolicyDecision",
    "PolicyEffect",
    "Precedent",
    "Adapter",
    "FileSystemAdapter",
    "ToolAdapter",
    "SQLAdapter",
    "connect_sqlite",
    "connect_postgres",
    "connect_sqlserver",
    "connect_oracle",
    "connect_mysql",
    "SearchAdapter",
    "SearchHit",
    "VectorSearchAdapter",
    "RestSearchAdapter",
    "AzureAISearchAdapter",
    "ElasticsearchAdapter",
    "ExtractionAdapter",
    "DocumentAdapter",
    "from_callables",
    "RewindStep",
    "Realm",
    "Cipher",
    "MergeReport",
    "export_bundle",
    "import_bundle",
    "Substrate",
    "NodeIdentity",
    "derive_node_id",
    "verify_signature",
    "attenuate_grant",
    "delegate",
    "Invariant",
    "Proof",
    "GuaranteeReport",
    "prove_guarantees",
    "Budget",
    "BudgetDecision",
    "CostModel",
    "EconomicKernel",
    "MeshServer",
    "gossip",
    "GossipReport",
    "AutarchError",
    "GovernanceError",
    "CapabilityDenied",
    "PolicyDenied",
    "BudgetExceeded",
    "DelegationError",
    "AccessDenied",
    "AdapterError",
    "ModelError",
    "ModelUnavailable",
    "RateLimited",
    "CircuitOpen",
    "ValidationError",
    "SecretError",
    "Event",
    "EventSink",
    "NullSink",
    "ListSink",
    "CallbackSink",
    "RunJournal",
    "RunState",
    "Role",
    "Principal",
    "RoleRegistry",
    "AccessControl",
    "RecallMemory",
    "MemoryEntry",
    "WORKING",
    "EPISODIC",
    "SEMANTIC",
    "PROCEDURAL",
    "EmbeddingProvider",
    "HashingEmbedder",
    "OllamaEmbedder",
    "OpenAIEmbedder",
    "build_embedder",
    "build_provider",
    "Orchestrator",
    "OrchestrationResult",
    "Plan",
    "Planner",
    "RulePlanner",
    "ModelPlanner",
    "Specialist",
    "SpecialistRegistry",
    "Subtask",
    "Synthesizer",
    "ConcatSynthesizer",
    "ModelSynthesizer",
    "ChildResult",
    "MCPClient",
    "MCPServer",
    "from_mcp_server",
    "govern_langchain_tools",
    "as_langchain_tool",
    "as_langchain_tools",
    "GovernedLangChainTool",
    "govern_maf_tools",
    "as_maf_tool",
    "as_maf_tools",
    "governed_function_middleware",
    "MAFModelProvider",
    "JsonlSink",
    "otel_sink",
    "otel_available",
    "health_check",
    "Evaluator",
    "Verdict",
    "AssertionEvaluator",
    "RubricJudge",
    "ConsensusEvaluator",
    "GroundednessEvaluator",
    "CoverageEvaluator",
    "InjectionEvaluator",
    "PIIEvaluator",
    "EvaluationPanel",
    "PanelReport",
    "quality_panel",
    "safety_panel",
    "check_grounding",
    "Citation",
    "Citer",
    "cite",
    "reflect",
    "ReflectionResult",
    "extractive_summary",
    "compress_history",
    "Resilient",
    "make_resilient",
    "RetryPolicy",
    "RateLimit",
    "CircuitBreaker",
    "AdaptiveExecutor",
    "TaskOutcome",
    "count_tokens",
    # --- scope algebra ---
    "scoping",
    # --- cloud providers + pricing ---
    "OpenAIProvider",
    "AnthropicProvider",
    "PriceBook",
    "DEFAULT_PRICE_BOOK",
    "estimate_tokens",
    "UsageMeter",
    "CallUsage",
    "get_usage_meter",
    "record_usage",
    "usage_label",
    "current_label",
    "ImageRef",
    # --- async approval plane ---
    "Approval",
    "ApprovalQueue",
    # --- governance gateway ---
    "GovernanceGateway",
    "GatewayClient",
    # --- compliance evidence ---
    "ComplianceReporter",
    "ComplianceReport",
    "Control",
    "markdown_report",
    # --- policy DSL ---
    "compile_policy",
    "compile_policies",
    "simulate",
    "diff",
    # --- kernel verification ---
    "verify_kernel",
    "VerificationResult",
]

__version__ = "0.12.0"
