# Existing inference-limiter seam

Source: uploaded FAIR2WISE-rsoxs.zip, `app/modules/cborg_limiter.py` (hash and AST symbols in v4_source_notes.json).

The source exposes `_read_cap`, `sync_slot`, `get_async_semaphore` and `async_slot`. Its configured environment key is CBORG_MAX_CONCURRENCY; the source default is 5. This is a source default, not a statement about the current CBORG service limit.

The file uses distinct synchronous and asynchronous semaphores and explains that sequential pipeline phases avoid stacking their caps. That premise is not sufficient for the proposed concurrent foreground/background runtime. Preserve these calling interfaces and configured limits, but coordinate actual shared provider/account admission across simultaneous callers. Non-CBORG paths currently bypass these CBORG gates; D18 requires respecting every selected LLM's configured/provider limits without changing its model class or inventing a provider quota.

A semaphore is only an in-flight limit, not automatically a requests-per-minute or tokens-per-minute limiter. T02/T10/T11 and consumer tests must cover rate-limit responses, backoff, retries, cancellation, event-loop lifetime and cross-process admission. Choosing a coordination mechanism is an H1 implementation decision, not a reason to replace F2W's inference classes.

No source code was imported/executed for this note, and no remote provider limit was queried or asserted.
