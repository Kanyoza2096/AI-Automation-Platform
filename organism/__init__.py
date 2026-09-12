"""Organism — tenant intelligence unit (one per workspace)."""
from __future__ import annotations
try:
    from core.organism import Organism, OrganismRegistry  # type: ignore
except ImportError:
    Organism = None  # type: ignore
    OrganismRegistry = None  # type: ignore

from organism.learning import ExecutionFeedback, LearningEngine, LearningResult

__all__ = ["Organism", "OrganismRegistry", "ExecutionFeedback", "LearningEngine", "LearningResult", "Prediction", "CausalHypothesis", "PredictiveCausalEngine", "Dream", "DreamEngine", "DreamStep", "StrategyGenome", "GenomeFitness", "EvolutionResult", "GenomeEngine", "AuthorityRequirement", "CapabilityCheck", "CapabilityHealth", "CapabilityRegistry", "CapabilityRisk", "CapabilitySpec", "CapabilityStatus", "CoordinationProposal", "CoordinationStatus", "EcosystemCoordinator", "EcosystemPolicy", "OrganismSignal", "SignalKind", "OrganismCycleResult", "OrganismRuntime", "cycle_async", "IntegrityReport", "validate_cycle_result", "validate_declarative_mapping", "ReadinessReport", "ReadinessStatus", "assess_readiness", "ProbeStatus", "ProbeResult", "StagingValidationReport", "ChaosScenario", "StagingValidationHarness", "fail_closed_probe", "EvidenceRecord", "FailureClass", "PromotionReport", "StagingEvidenceCollector", "verify_evidence"]

from organism.prediction import CausalHypothesis, Prediction, PredictiveCausalEngine

from organism.dreaming import Dream, DreamEngine, DreamStep
from organism.genome import EvolutionResult, GenomeEngine, GenomeFitness, StrategyGenome

from organism.capabilities import (AuthorityRequirement, CapabilityCheck, CapabilityHealth, CapabilityRegistry, CapabilityRisk, CapabilitySpec, CapabilityStatus)

from organism.lifecycle import OrganismCycleResult, OrganismRuntime, cycle_async

from organism.ecosystem import CoordinationProposal, CoordinationStatus, EcosystemCoordinator, EcosystemPolicy, OrganismSignal, SignalKind
from organism.hardening import IntegrityReport, validate_cycle_result, validate_declarative_mapping

from organism.operational_readiness import ReadinessReport, ReadinessStatus, assess_readiness
from organism.staging_validation import ChaosScenario, ProbeResult, ProbeStatus, StagingValidationHarness, StagingValidationReport, fail_closed_probe
from organism.staging_evidence import EvidenceRecord, FailureClass, PromotionReport, StagingEvidenceCollector, verify_evidence
