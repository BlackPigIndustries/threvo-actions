# Framework access design

Status: **sequenced behind merchant-example and outside-host evidence**.

The guarantee lives in `ActionRuntime`; a framework adapter should only make
the same operations reachable. It must not translate chat history, tool
approval flags or transport identity into authority.

## Sequence

### 1. Anthropic Agent SDK recipe

After the merchant example proves the mapping, add a source example that maps
typed SDK tool inputs to the same `prepare`, `record_authority`, `execute`,
`reconcile`, `read_recovery` and `export_evidence` operations. Keep authenticated
tenant and principals in host dependencies. Return the runtime's typed outcome
and reason code; human wording may follow the SDK's UI conventions.

Trigger: one adopter using the Agent SDK, or a maintainer-owned compatibility
fixture pinned to an exact SDK version. Qualification must replay the merchant
drift, lost-acknowledgement and competing-tab cases unchanged.

### 2. Reference MCP host

Build a reference host only after its authentication boundary is named. The
server must resolve tenant, requester, confirmer and evidence consumer from a
verified connection or gateway context. Bind loopback by default. A non-loopback
deployment requires an authenticated gateway and explicit forwarded-identity
contract. MCP arguments may contain opaque business references but never
authoritative tenant, role, commitment or channel assurance.

Trigger: a real host can supply its MCP authentication and deployment model.
The example must exercise denial, cross-tenant lookup, authority pause/resume,
uncertain execution and recovery.

### 3. Packaged generic MCP integration

Publish a reusable package surface only after two independent hosts prove the
same contract. Until then, a generic server would either hide host duties or
freeze one deployment's assumptions into the library.

## Shared contract

Every adapter calls a supported bound-action operation and returns the same
machine-readable `OperationOutcome`, reason code and safe result. Exact prose
does not need to be identical across frameworks. Golden behavior tests must
assert that no path bypasses authority, live resolution, semantic-effect
admission, verification or authorized reads.

The existing Pydantic AI integration remains the only packaged agent adapter
during the 0.4 stabilization hold.
