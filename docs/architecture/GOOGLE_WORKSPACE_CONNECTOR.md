# Google Workspace Connector

KDO treats Google Workspace as one Connector Fabric provider boundary with explicit service capabilities rather than embedding Google-specific execution into the Action Kernel.

## Curated capabilities

- Google Drive: file lookup and search
- Gmail: send message
- Google Calendar: create event
- Google Sheets: append values
- Google Docs: create document

## Execution rules

1. OAuth/access credentials are supplied to the adapter and never accepted as action parameters.
2. Mutations are disabled by default and require explicit provider configuration.
3. Mutating capabilities execute only through Action Kernel Action Contracts.
4. Provider object identity is propagated into the canonical receipt when the API supplies durable identity.
5. A successful mutation without durable provider identity remains `uncertain` and requires reconciliation.
6. Google Workspace APIs remain the external system of record; KDO owns governance, execution state, evidence, and reconciliation semantics.

## Boundary

```text
KDO Core -> Action Kernel -> Connector Fabric -> Google Workspace Adapter
                                             -> Gmail / Drive / Calendar / Sheets / Docs
                                             -> Google APIs
                                             -> Provider Receipt -> KDO Ledger
```

Live OAuth scopes, tenant configuration, API quotas, and Google API behavior must be certified during Phase 6 production verification. Offline adapter tests do not constitute live provider certification.
