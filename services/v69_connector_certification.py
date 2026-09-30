"""V69.6 connector certification framework.

Offline certification contract for business integrations. Certification never
contacts a provider and never grants authority. Provider-live verification is
a separate operational phase; execution remains behind the governed adapter,
Action Kernel, durable outbox/ledger and reconciliation planes.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from services.business_integration_platform import ConnectorContract, ConnectionState, IntegrationConnection
from services.connectors.capabilities import ConnectorCapabilities, capabilities
from services.v69_integration_foundation import CredentialBinding


class CertificationStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    NOT_PROVEN = "not_proven"


class CertificationFailure(str, Enum):
    CONTRACT = "contract"
    TENANT_ISOLATION = "tenant_isolation"
    AUTH_IDENTITY = "auth_identity"
    CAPABILITY_HONESTY = "capability_honesty"
    AMBIGUOUS_SIDE_EFFECT = "ambiguous_side_effect"
    RECONCILIATION = "reconciliation"
    IDEMPOTENCY = "idempotency"
    RATE_LIMIT = "rate_limit_dependency"
    REVOCATION = "revocation"
    VERSION_COMPATIBILITY = "version_compatibility"
    GOVERNED_EXECUTION = "governed_execution"


@dataclass(frozen=True)
class CertificationCheck:
    name: str
    status: CertificationStatus
    failure_class: CertificationFailure | None = None
    evidence: str = ""


@dataclass(frozen=True)
class ConnectorCertificationResult:
    connector_key: str
    provider: str
    version: str
    status: CertificationStatus
    checks: tuple[CertificationCheck, ...]
    live_provider_verified: bool = False
    certification_version: str = "v69.6"

    @property
    def certified(self) -> bool:
        return self.status == CertificationStatus.PASS

    @property
    def failures(self) -> tuple[CertificationCheck, ...]:
        return tuple(c for c in self.checks if c.status == CertificationStatus.FAIL)

    @property
    def failure_classes(self) -> tuple[str, ...]:
        return tuple(sorted({c.failure_class.value for c in self.failures if c.failure_class}))


# Compatibility alias for callers that prefer the longer name.
ConnectorCertificationStatus = CertificationStatus
CertificationFailureClass = CertificationFailure


@dataclass(frozen=True)
class ConnectorCertificationSpec:
    minimum_version: str = "1"
    require_rate_limit_contract: bool = True
    probe: Any | None = None


def certify_connector(
    connector: Any,
    contract: ConnectorContract,
    connection: IntegrationConnection | None = None,
    *,
    workspace_id: str | None = None,
    credential: CredentialBinding | None = None,
    expected_version: str | None = None,
    rate_limit_contract: bool = False,
    governed_adapter: Any | None = None,
    spec: ConnectorCertificationSpec | None = None,
) -> ConnectorCertificationResult:
    """Run fail-closed offline certification.

    ``NOT_PROVEN`` is evidence debt, not a pass. It is allowed only for facts
    that genuinely require live provider evidence; hard safety failures remain
    ``FAIL``. A result can pass offline only when all authority/safety contracts
    are structurally satisfied, while still reporting live-provider proof as
    false.
    """
    checks: list[CertificationCheck] = []
    spec = spec or ConnectorCertificationSpec()
    tenant = workspace_id or (connection.workspace_id if connection else "")

    def add(name: str, ok: bool, failure: CertificationFailure | None = None, evidence: str = "") -> None:
        checks.append(CertificationCheck(name, CertificationStatus.PASS if ok else CertificationStatus.FAIL,
                                         None if ok else failure, evidence))

    add("workspace_identity", bool(tenant.strip()), CertificationFailure.TENANT_ISOLATION)
    platform = str(getattr(connector, "platform", ""))
    add("connector_identity", platform in {contract.provider, contract.connector_key}, CertificationFailure.CONTRACT)
    add("tenant_scoped_contract", contract.tenant_scoped, CertificationFailure.TENANT_ISOLATION)

    if connection is not None:
        add("connection_tenant_match", connection.workspace_id == tenant, CertificationFailure.TENANT_ISOLATION)
        add("connection_connector_match", connection.connector_key == contract.connector_key, CertificationFailure.CONTRACT)
        add("connection_has_no_authority", connection.authority == "none" and connection.execution_effect == "none",
            CertificationFailure.GOVERNED_EXECUTION)

    if credential is not None:
        add("credential_tenant_match", credential.workspace_id == tenant, CertificationFailure.AUTH_IDENTITY)
        add("credential_connection_match", connection is None or credential.connection_id == connection.connection_id,
            CertificationFailure.AUTH_IDENTITY)
        add("credential_active", credential.active, CertificationFailure.AUTH_IDENTITY)
        add("credential_revocation_state", credential.active, CertificationFailure.REVOCATION)
        add("credential_is_opaque", not _looks_like_raw_secret(credential.secret_ref), CertificationFailure.AUTH_IDENTITY)
    else:
        checks.append(CertificationCheck("credential_live_identity", CertificationStatus.NOT_PROVEN,
                                         evidence="offline certification does not prove provider authentication"))

    caps = capabilities(connector)
    add("capability_contract_unique", len(set(contract.capabilities)) == len(contract.capabilities),
        CertificationFailure.CAPABILITY_HONESTY)
    exposed = getattr(connector, "DECLARED_CAPABILITIES", None)
    add("capability_honesty", exposed is None or set(exposed).issubset(
        set(contract.capabilities) | set(contract.inbound_events) | set(contract.outbound_actions)),
        CertificationFailure.CAPABILITY_HONESTY)

    # A provider's native idempotency claim is not inferred from KDO's key.
    # Without native idempotency, authoritative lookup OR an explicitly proven
    # safe retry strategy is mandatory for ambiguous mutations.
    safe_ambiguous = bool(caps.authoritative_lookup or caps.safe_retry_after_ambiguous)
    add("native_idempotency_honesty", (not caps.native_idempotency) or caps.safe_retry_after_ambiguous,
        CertificationFailure.IDEMPOTENCY, "native idempotency requires safe ambiguous retry evidence")
    add("ambiguous_side_effect_control", safe_ambiguous, CertificationFailure.AMBIGUOUS_SIDE_EFFECT,
        "lookup/reconciliation or provider-safe retry is required")
    add("reconciliation_contract", contract.requires_reconciliation or caps.authoritative_lookup,
        CertificationFailure.RECONCILIATION)

    add("rate_limit_contract", (not spec.require_rate_limit_contract) or rate_limit_contract or bool(getattr(connector, "RATE_LIMIT_POLICY", None) or
        getattr(connector, "RATE_LIMITED", False) or callable(getattr(connector, "handle_rate_limit", None))),
        CertificationFailure.RATE_LIMIT)

    # Credential revocation is enforced by V69.3 binding lifecycle; adapters
    # must also retain a validation surface for revalidation after suspension.
    revocation_surface = callable(getattr(connector, "validate", None)) or \
        callable(getattr(connector, "on_revoked", None)) or bool(getattr(connector, "REVOCATION_POLICY", None))
    if revocation_surface:
        add("revocation_revalidation", True)
    else:
        checks.append(CertificationCheck("revocation_revalidation", CertificationStatus.NOT_PROVEN,
                                         evidence="revocation is enforced by the V69.3 credential/connection lifecycle; adapter hook not declared"))

    if expected_version is None and spec.minimum_version:
        expected_version = spec.minimum_version
    if expected_version is not None:
        add("version_compatibility", _version_compatible(contract.version, expected_version),
            CertificationFailure.VERSION_COMPATIBILITY,
            f"contract={contract.version}; expected={expected_version}")
    else:
        checks.append(CertificationCheck("version_provider_compatibility", CertificationStatus.NOT_PROVEN,
                                         evidence="no target compatibility version supplied"))

    governed_ok = _is_governed_adapter(governed_adapter)
    add("governed_execution", governed_ok, CertificationFailure.GOVERNED_EXECUTION,
        "mutations must traverse the governed execution boundary")

    # Health/provider response semantics cannot be proven without credentials
    # and a live provider. Keep this visible rather than silently passing it.
    checks.append(CertificationCheck("live_provider_behavior", CertificationStatus.NOT_PROVEN,
                                     evidence="no live provider call performed"))

    if spec.probe is not None:
        try:
            evidence = spec.probe(connector)
            probe_map = {
                "tenant_isolation": CertificationFailure.TENANT_ISOLATION,
                "capability_honesty": CertificationFailure.CAPABILITY_HONESTY,
                "authentication": CertificationFailure.AUTH_IDENTITY,
                "idempotency": CertificationFailure.IDEMPOTENCY,
                "reconciliation": CertificationFailure.RECONCILIATION,
                "rate_limit": CertificationFailure.RATE_LIMIT,
                "revocation": CertificationFailure.REVOCATION,
                "version": CertificationFailure.VERSION_COMPATIBILITY,
                "execution_boundary": CertificationFailure.GOVERNED_EXECUTION,
            }
            for name, value in evidence.items():
                if name in probe_map:
                    checks.append(CertificationCheck(
                        f"probe:{name}",
                        CertificationStatus.PASS if value is True else CertificationStatus.FAIL,
                        None if value is True else probe_map[name],
                        str(value),
                    ))
        except Exception as exc:
            checks.append(CertificationCheck("offline_probe", CertificationStatus.FAIL,
                                             CertificationFailure.CONTRACT, str(exc)[:300]))

    hard_fail = any(c.status == CertificationStatus.FAIL for c in checks)
    return ConnectorCertificationResult(
        connector_key=contract.connector_key,
        provider=contract.provider,
        version=contract.version,
        status=CertificationStatus.FAIL if hard_fail else CertificationStatus.PASS,
        checks=tuple(checks),
    )


def _is_governed_adapter(adapter: Any) -> bool:
    if adapter is None:
        return False
    # Deliberately structural: certification does not import or instantiate an
    # execution engine, but requires the adapter to expose the durable boundary.
    return all(callable(getattr(adapter, name, None)) for name in ("publish", "edit", "delete")) and \
        hasattr(adapter, "ledger") and hasattr(adapter, "executor")


def _looks_like_raw_secret(value: str) -> bool:
    value = str(value).strip()
    return value.startswith(("sk_", "eyJ", "Bearer ", "ghp_", "xoxb-", "AKIA"))


def _version_tuple(value: str) -> tuple[int, ...]:
    nums = []
    for token in str(value).split("."):
        digits = "".join(ch for ch in token if ch.isdigit())
        if not digits:
            break
        nums.append(int(digits))
    return tuple(nums or [0])


def _version_compatible(actual: str, expected: str) -> bool:
    # Expected may be a concrete version or a minimum major/minor version.
    a, e = _version_tuple(actual), _version_tuple(expected)
    return a[:len(e)] == e
