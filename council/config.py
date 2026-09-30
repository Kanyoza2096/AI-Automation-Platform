"""Democracy Engine configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class DemocracyConfig:
    """Configuration for the Democracy Engine."""

    # Risk thresholds
    trivial_max_confidence: float = 0.95
    low_risk_max_confidence: float = 0.80
    medium_risk_max_confidence: float = 0.65

    # Voting tiers
    tier_0_persona_count: int = 0       # Trivial — no council
    tier_1_persona_count: int = 2       # Low — mini council
    tier_2_persona_count: int = 5       # Medium — standard council
    tier_3_persona_count: int = 10      # High/Critical — full council

    # Quorum
    min_quorum_ratio: float = 0.60
    approval_threshold: float = 0.55
    rejection_threshold: float = 0.35

    # Risk-sensitive approval. A single universal majority threshold is unsafe
    # because a 55% vote is not an adequate mandate for production-critical
    # actions. These are policy floors; individual personas cannot lower them.
    high_risk_approval_threshold: float = 0.70
    critical_approval_threshold: float = 0.80
    high_risk_rejection_threshold: float = 0.55
    critical_rejection_threshold: float = 0.60
    max_high_risk_abstention_ratio: float = 0.20
    max_critical_abstention_ratio: float = 0.10
    minimum_evidence_quality_high_risk: float = 0.55
    minimum_evidence_quality_critical: float = 0.65

    # Early termination
    enable_early_stop: bool = True
    early_stop_confidence_margin: float = 0.25

    # Degradable council
    fail_open_on_low_risk: bool = True
    fail_closed_on_high_risk: bool = True

    # Reliability
    minimum_samples_for_reliability: int = 10
    reliability_weight_factor: float = 0.4

    # Seeded randomness
    use_seeded_randomness: bool = True
    council_version: str = "1.0.0"

    # Caching
    enable_result_cache: bool = True
    cache_ttl_seconds: int = 300

    # Context-aware agent selection.
    #
    # The proposal_type passed by the caller determines the panel. When a
    # proposal_type is missing from this map, DecisionRouter.select_agents
    # falls back to ALL_PERSONAS[:N], i.e. whatever the first N personas
    # happen to be in declaration order — unrelated to the action being
    # deliberated. That is the source of the pre-v1.1 "publish always
    # returns needs_revision at low quorum" behavior: content was being
    # judged by personas whose evaluate() methods did not recognise it and
    # therefore abstained by default.
    #
    # `publish` and `publish_content` are now explicit entries selecting
    # personas that read publish content fields (topic, description,
    # caption). If you introduce a new publish-like action_name, add it
    # here rather than relying on the fallback.
    agent_selection_map: Dict[str, List[str]] = field(default_factory=lambda: {
        "content_creation": ["youth_advocate", "customer_voice", "trend_spotter", "rippler"],
        "technical_change": ["data_analyst", "risk_manager", "business_owner"],
        "security_action": ["risk_manager", "data_analyst", "community_elder"],
        "financial_action": ["business_owner", "risk_manager", "community_elder"],
        "campaign_launch": ["youth_advocate", "trend_spotter", "business_owner", "customer_voice"],
        "community_event": ["community_elder", "youth_advocate", "customer_voice"],

        # Publish / publish_content — personas that actually evaluate the
        # proposal content. Chosen because each reads a field the canonical
        # pipeline now populates (topic, description, day/hour, risk):
        #   business_owner  → topic, description, has_cta
        #   customer_voice  → audience fit of the caption
        #   theologian      → religious content of topic/description
        #   trend_spotter   → topicality of topic
        #   risk_manager    → hard veto on risky content
        "publish": [
            "business_owner",
            "customer_voice",
            "theologian",
            "trend_spotter",
            "risk_manager",
        ],
        "publish_content": [
            "business_owner",
            "customer_voice",
            "theologian",
            "trend_spotter",
            "risk_manager",
        ],

        "general": None,  # Use all
    })
