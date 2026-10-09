# Agent-private source content and user-facing disclosure

**Draft v8. D19 now explicitly permits relevant exact quotations; O02 is resolved.** This is an application architecture/handling policy, not a legal opinion or proof of source licensing.

## 1. Separate source access from result disclosure

| Material | Authorized agent pipeline | User-facing UI / public API / report |
| --- | --- | --- |
| Source PDF / complete paper body | Internal scoped access when existing processing authorization permits | Not served or exported |
| Parsed text / full sections / original table reproduction | Internal scoped access | Not served as paper content |
| Figure/page/panel crop, source image, image URL | Internal analysis/validation | No source crop/image delivery or directly retrievable asset URL |
| Evidence locators and bibliography | Retain source version, page/figure/section, checksum and attribution | Permitted citation metadata and source links; no storage credentials/paths |
| Derived summaries, computed counts, extracted scientific facts | Grounded with private evidence | Permitted result projection under existing QA constraints |
| Relevant exact evidence quotations | Retain verified source/version/locator | Permitted with attribution, exactness and relevant context; preserve existing F2W cited snippets. Not a full-paper/chunk-enumeration endpoint. |
| Job/checkpoint/tool/trace payloads | Private state may contain source content | Sanitize; never forward full internal state to stream or trace UI |

No paper download or public crop-serving feature is added merely because FORGE has such a route. Mandatory figure functionality means agents can inspect and extract evidence; it does not require users to receive the underlying image.

## 2. Trusted internal API versus public surface

Retain private source-reading services and agent-facing `lit_read_paper` / section / figure actions. A trusted F2W or FORGE runtime may use them via Python or protected internal HTTP. D34 removes local user sign-in requirements, not this internal-source versus user-output distinction; local full application access still does not add raw-paper/crop serving. Browser-visible endpoints use a separate serializer/DTO projection. An HTTP parameter, model tool argument, prompt instruction or user-agent header cannot confer internal-agent authority. Inject principal/capabilities at a trusted application boundary; apply authorization before retrieval and disclosure policy before returning data.

FORGE embedding must not forward internal tool-return bodies as public tool traces. It should consume a versioned safe result/evidence view for user responses. Public `read_paper` behavior is metadata/derived result only unless a trusted internal capability is present. Route shape does not define trust. Existing baseline route inventories remain source evidence; v4 exposure dispositions override earlier unqualified instructions to mirror raw serving.

A Python import in a fully trusted host process is not a security boundary against the host itself. Private service methods cannot stop malicious host code with filesystem/credential access. Use a separate process/service and scoped credentials when that is the required protection boundary. Do not claim an agent label enforces this.

## 3. Storage and graph publication

Keep source PDFs, full parser artifacts, crops, long source extracts and private evidence payloads in an access-controlled internal document store or equivalently protected Tiled content. Publicly readable graph nodes and exports may include safe citations, derived facts and relevant bounded exact quotations, but not a raw-text replica or cumulative paper reconstruction. Check evidence links, attached properties, figure URLs and export expansions. A public graph full of source paragraphs would bypass a private PDF endpoint.

Cache, job state and checkpoints inherit the source restriction. Return opaque access-scoped references, not storage URLs that bypass authorization. Content hashes identify bytes; they are not authorization tokens. Raw traces and evaluation evidence stay protected; sanitized gate reports may contain IDs, verdicts and reasons rather than copied source passages.

## 4. Output enforcement and tests

Proposed modules: `security/content_access.py` resolves trusted source-reading scope; `security/disclosure.py` projects public results. Integrate with `app/modules/application.py`, FastAPI serializers, compatibility DTOs, SSE/event adapters, graph export, operation reports and FORGE forwarding. Shared policy means direct SDK calls cannot bypass controls by avoiding HTTP.

Binary tests cover public PDF/crop requests, guessed figure/artifact IDs, private-cache paths, internal-method calls with untrusted credentials, malicious paper instructions asking to print the full text, tool traces, graph export, raw exceptions, source URLs, and incremental reconstruction attempts. Prevent raw-source forwarding; semantic answer constraints and output limits must also be tested because hiding endpoints alone does not prevent an LLM reproducing content.

## 5. Compatibility and human evaluation

Preserve F2W citation identifiers, graph edits, response shapes and session behavior. Quote-valued evidence fields may remain visible when relevant, accurate and cited. Preserve their existing wire shape and source attribution; do not blanket-redact all verbatim evidence to satisfy the no-full-paper rule. Synthetic/open fixtures may be used in public CI and demos without distributing private corpus assets.

The owner may inspect original papers through their existing authorized source access for scientific review; this does not require implementing a public F2W paper-serving route. The application provides source locators and sanitized review packets. Any requested privileged review UI for raw evidence is a separate explicit access decision.

This policy does not establish permission to process every paywalled source or send it to a new external model. Preserve source provenance and the existing approved F2W provider scope; unresolved processing restrictions remain operator review, not a new redistribution-label guessing task for the agent.

T21A blocks H1 on implementing the approved disclosure distinction, not on re-asking O02. T21B protects H2 and is reused by all paper ports. H4/H6 require binary no-leakage checks in the actual integrated pathways.

## 6. Exact quotation checks

Verify displayed text against the retained source version. A normalized matching algorithm is not permission to rewrite a quote silently; label omissions/paraphrases and keep source locators. Add positive tests that a relevant exact quote survives answer/graph-detail serialization, and negative tests for fabricated quotes, wrong-page/version citations, bulk full-text delivery, crop URLs and reconstruction by repeated unrestricted reads. Preserve source processing/access restrictions without inventing license labels or a universal quotation word count.

## Draft v8 storage and publication boundaries

Papers are mounted at deployment, never copied into image layers, CI caches or static assets. Parsed text/crops, private JSON/evidence, sessions/checkpoints and audit inherit private mount permissions; use separate writable intake when the source mount is read-only. Nginx/static routing exposes only the frontend build. Secrets are runtime-only and excluded from VITE_ values, API settings and traces. A bind mount does not provide backup: ANSIBLE_HANDOFF.md requires an owner/retention/restore plan before H7.

F2W imports via its technical admin account after checking the initiating Tiled admin's authority. D30 makes non-admin users chat-only; it does not make an admin role a right to serve full papers or original crops to the browser. D19 still separates trusted internal agent processing from user result projection. No generic admin-authorized Tiled proxy is exposed; SDK/HTTP/FORGE calls share the same guard. Record requester/approver/executor separately.
## 9. Git is another disclosure boundary (v7)

D31 commits frozen versioned KG JSON and manifests to the F2W repository. Commit safe scientific facts and permitted relevant exact quotations, with protected references to private evidence; exclude raw papers, reconstructed full text, complete parsed sections, original crops, credentials and runtime state. Review the actual repository visibility and apply disclosure/secret checks before staging Git changes. Tiled sign-in and private mounts cannot protect material already copied into publicly readable Git history. Do not silently drop scientific data; split private payloads into referenced controlled storage and validate the resulting graph version. The production import uses that exact version, not a differently redacted copy. Git-tracked corpus data is still excluded from all image layers.
