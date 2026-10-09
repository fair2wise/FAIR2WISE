Profile-specific application authorization
Draft v8. D34 grants full local application access without sign-in. D30 applies to ALS only; D27 retains the separate technical Tiled admin writer. Interface and audit names below are proposed implementation contracts.
1. Authority and identities
:local is trusted full-access mode. Any user who can reach the local application may use every application capability, whether signed into Tiled or not. No account, personal token, Tiled login session or admin-role check is required. An absent, expired or non-admin user credential does not reduce local functionality. The local launcher binds to loopback by default. Wider network exposure is an explicit operator choice and gives reachable users full application access.
:als-dev and :als-prod retain native Tiled authentication. Verify the user against the configured staging or production instance using its supported identity/admin API. Not-signed-in users have only sign-in and non-sensitive health access; ordinary signed-in users are chat-only; verified admins may use all scoped application functions. Do not introduce a separate identity provider or maintainer allowlist.
Keep three roles distinct:
- Requester: local-operator in :local; verified Tiled user/issuer on ALS.
- Approver: the local user explicitly approving as local-operator, or a verified Tiled admin on ALS. An LLM never approves itself.
- Executor: the configured F2W admin/service account for backend Tiled effects. Its credential does not establish the caller's identity or ALS admin privilege.
Select deployment_profile and authorization_mode from trusted startup configuration. The local profile uses local_trusted; ALS profiles use tiled_authenticated and must reject local_trusted configuration. Do not infer access from an HTTP header, hostname, client/model argument or failed login. Unknown or incompatible profiles fail startup rather than defaulting to full access. A library factory obtains its mode from trusted host configuration, not a delegated user request.
2. Capability matrix
Deployment / caller	Allowed behavior
:local - any user, signed in or not	All application functionality: chat, discovery, ingestion, extraction, figures, Knowledge Operations, curation, explicit approvals, versioned JSON creation, local Tiled publication and graph editing. No login/role check.
:als-dev / :als-prod - not signed in	Sign-in flow and non-sensitive health endpoints only.
:als-dev / :als-prod - signed-in non-admin	Chat, bounded read-only supporting tools, citations, own session history/cancellation and permitted personal/model settings. No ingestion, approval, publication or graph editing.
:als-dev / :als-prod - verified Tiled admin	Full scoped application functionality, including administrative jobs, approvals, publication and graph editing.
Background worker - any profile	Execute a submitted job within its recorded profile, target and effect scope. Local jobs use local-operator without token refresh; ALS jobs use verified authorization and bounded grants. A service credential is not independent job authority.


Full access means availability of application functions, not disabling scientific validation, source-disclosure rules, rate limits, immutable JSON artifacts, import integrity or explicit approval steps. Local users can perform those approvals without authentication. Raw paper/full-text/crop delivery stays internal under D19; quoted evidence and derived results remain permitted. Deployment secrets and targets are not model-editable settings.
3. Shared enforcement, not only UI hiding
Proposed file	Responsibility
app/modules/security/authorization.py	resolve_access_context / local_operator_context; choose by trusted profile; grant local capabilities or enforce verified ALS roles; shared scope/effect checks.
app/modules/security/tiled_identity.py	Verify ALS native identity, token scope/issuer and fresh admin status; not a local-user capability prerequisite.
app/api/auth.py and dependencies.py	ALS login/logout/session integration; inject local-operator directly in local mode. No local login redirect or role-dependent administrative UI.
app/modules/curation/approvals.py	Create profile-aware ApprovalRecord from explicit local user action or verified ALS admin action; bind decision to plan/hash/target/revision.
app/modules/agent_runtime/executor.py	Check task/effect scope and recorded approvals; local capability availability does not mean autonomous approval.
app/modules/application.py	Apply the same profile policy through FastAPI, direct Python/CLI, agent Tools, jobs and FORGE calls.


Local requests must not query a user's Tiled role to decide which features to expose. The local backend still authenticates to Tiled with its internally configured service credential. Missing or invalid service credentials fail dependent graph operations explicitly; they do not redirect the user to login or remove unrelated application access.
ALS token checks bind subject and grant to the configured issuer/server, expiry and scope. Revalidate protected effects; refuse on unavailable/invalid identity evidence. Never infer admin from writer credentials, email domains, unsigned claims, proxy headers or client booleans. Native Tiled transport/session details remain H1 engineering work within the existing selected system. Login/return paths respect /fair2wise; no broad unauthenticated GraphQL/admin proxy.
The local no-login exception cannot be activated remotely through a request, saved job record or fallback route on ALS. Keep raw backend Tiled credentials internal. Do not use a failed ALS identity lookup as a reason to switch server or authorization mode.
4. Background jobs, approvals and audit
Local job submission resolves a local-operator context without sign-in. Store the profile, authorization mode, job/operation identifier, corpus/target/effect scope, selected configuration and any explicit approval. Local workers do not require user-token refresh or admin-role revalidation; they still honor recorded job scope, cancellation, validation and service-resource failures.
Proposed local audit fields:
deployment_profile: local
authorization_mode: local_trusted
requested_by: local-operator
approved_by: local-operator  # only after an explicit approval action
executed_as: <configured local Tiled writer identity>
authenticated_tiled_subject: null
role_check_timestamp: null
Before approval, approved_by remains unset. This actor is not a verified human identity and must not be represented as one. Job/session identifiers distinguish operations without inventing authentication. The writer's real service identity remains separate and never includes its token.
ALS jobs carry a verified initiating subject and bounded grant before acquisition, inference admission or candidate writes. Revalidate relevant authority on resume and at protected boundaries; role downgrade, expiry and revocation block new effects. Persist no raw tokens in prompts, graph JSON or audit reports. The same verified admin may approve and execute; no new second-approver requirement is introduced.
A local approval or saved job grant cannot authorize staging/production effects. The receiving ALS service applies its own profile, verified identity and target-specific approval requirements. The image environment still fixes the Tiled target under D29; there is no cross-environment fallback or automatic replication.
Explicit local approvals remain actions by the operator, not an automatic consequence of granting full capabilities. H0-H7 remain project-owner migration gates in all profiles and are not satisfied by runtime role assignment. Ordinary non-admin ALS chat recording must not use the service writer to publish operational records as a role-check bypass.
5. Frontend compatibility and FORGE delegation
Local frontend startup goes straight to the application with all supported controls, including ingestion, figures, curation, approval, publication and graph Save. The result cannot depend on a Tiled user session. Preserve existing route suffixes, DTOs, SSE, settings, sessions, citations and edit readback. Native login/capability UI is required only for ALS profiles.
ALS keeps verified-admin workflows and non-admin chat with explicit backend denial of protected requests. A crafted request must fail even when an old UI renders an editing control. Authentication errors must not trigger an unprotected legacy endpoint or trusted-local fallback.
FORGE calls inherit the policy of the receiving F2W deployment. Local F2W calls use the trusted local-operator context; ALS F2W requires verified caller context or a separately authorized scoped grant. A local client cannot grant itself ALS privilege. An in-process import remains a trusted-host boundary, not a sandbox against malicious host code with filesystem/credential access.
6. Stopping points and required evidence
T25A blocks H1 on profile/mode selection, local audit origin and ALS identity integration. T25B protects H2 with no-login local access plus fake-identity ALS role tests. T25C requires real local administrative flows without user sign-in and real ALS-mode identity/role cases against an application-owned disposable Tiled target before H4/H6.
Required tests include every local capability without login; local login/expiry/non-admin state never gating features; explicit local approval and truthful audit; UI/SDK/API/Tools/job parity; loopback-default local exposure; missing local service credentials producing an operation error rather than a user-login requirement; ALS request/configuration rejection of local_trusted; no auth-outage fallback; environment-bound grants; and existing ALS unauthorized/admin/revocation cases. Local source privacy and scientific/import invariants remain tested. No application test outcome is claimed by this specification.