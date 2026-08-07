# Performance guide

FAIR2WISE performance is dominated by graph loading, browser graph transfer and
layout, LLM/extraction latency, and context size. Tune one layer at a time and
record the graph size, question, model, backend, and cold/warm state with every
measurement.

## Establish a baseline

Before changing settings:

```bash
docker compose ps
docker compose logs --tail=200 agent splash frontend
curl -fsS http://127.0.0.1:5173/api/health | python3 -m json.tool
docker system df -v
```

For local processes, record startup-to-health time and time for a fixed
known-evidence question. In browser developer tools, inspect `/api/graph`, chat
stream duration, transferred bytes, scripting time, and memory. Compare cold
startup separately from a warm query because model, PDF, and query caches
change the result.

Environment-derived retrieval constants are read when the Python module starts.
Restart the agent after changing them. In Compose, only values listed under the
agent service's `environment:` are passed into the container; an arbitrary key
added to root `.env` has no effect until `compose.yaml` or a Compose override
maps it into the service.

## Large graph behavior

### Agent loading

`KnowledgeGraph` holds all nodes, outgoing edges, tokenized lexical documents,
and query results in process memory. With Splash as the source, startup and
reload paginate through **all** entities and links and then construct the same
in-memory MatKG shape used by JSON mode.

`KG_RAG_SPLASH_PAGE_SIZE` controls rows per GraphQL request; it is not a maximum
graph size:

- increase it to reduce request round trips when responses and memory are
  comfortable;
- decrease it when individual GraphQL responses are too large; and
- expect total load volume and final in-memory graph size to remain unchanged.

The default is 1,000. Measure both entities and links; dense edge sets can cost
more than node count suggests. A graph edit or source switch that calls
`reload_kg()` rebuilds indexes and clears the current in-memory graph instance.

Lexical retrieval scans token sets across nodes for an uncached query. Semantic
retrieval builds embeddings and a FAISS index at load time, increasing startup,
memory, dependency, and model-cache costs in exchange for vector search.

### API and browser loading

`GET /graph` reads the active MatKG JSON snapshot and returns all public nodes
and edges. The UI loads and retains that complete payload. The KG Viewer
defaults to a connected 100-node subset, but that limit only reduces layout and
rendering—it does not reduce `/graph` transfer or browser graph storage.

Current visualization safeguards are:

- normal answer view renders only highlighted result nodes;
- viewer choices are 10 through 100 nodes or `All`;
- graphs up to 300 displayed nodes use a quadratic force pass;
- larger displayed graphs use stable linear golden-angle placement;
- viewport culling limits mounted SVG nodes/edges;
- routine labels are hidden above 150 displayed nodes; and
- populate animations are disabled above 100 displayed nodes.

Prefer 50–100 nodes for interactive exploration. `All` is diagnostic and may
still be expensive because the browser already holds every node/edge and must
filter visible edges. For substantially larger corpora, the next architectural
step is a paginated/subgraph API and server-side neighborhood queries, not a
higher viewer default.

## Retrieval tuning

Start with the default lexical backend. It ships in the runtime image, uses no
embedding model download, and is the easiest baseline to reproduce.

| Setting | Default | Effect and tradeoff |
|---|---:|---|
| `KG_RAG_RETRIEVAL_BACKEND` | `lexical` in app deployment | `semantic` needs the optional semantic stack and an embedding-model download |
| `KG_RAG_TOPK` | `12` | Final nodes considered; higher values increase ranking/context work and can dilute relevance |
| `KG_RAG_ENABLE_BFS` | `1` | Expands seed hits through outgoing links |
| `KG_RAG_BFS_TOPK` | `2 × top-k` | Number of seeds expanded |
| `KG_RAG_MAX_HOPS` | `1` | Higher depth can grow rapidly on dense graphs |
| `KG_RAG_STEPWISE` | `1` | Searches decomposed sub-questions in addition to the whole query |
| `KG_RAG_STEPWISE_MAX_STEPS` | `6` | Caps decomposition searches |
| `KG_RAG_MAX_TEXT_CHARS` | `1024` | Maximum per-node text indexed for retrieval |
| `KG_RAG_GENERIC_PENALTY` | `0.8` | Down-ranks generic node names |

Recommended tuning order:

1. Verify evidence and identifiers before changing ranking.
2. Adjust `KG_RAG_TOPK` on a fixed representative question set.
3. Disable stepwise retrieval for simple queries if repeated searches dominate.
4. Disable BFS or keep one hop when dense graphs over-expand.
5. Consider semantic retrieval only when lexical misses are demonstrated.
6. Re-run retrieval, citation, sparse-graph, and context-budget tests after each
   change.

Semantic mode uses `KG_RAG_EMBED_MODEL`, a device-dependent encoding batch
(`KG_RAG_BATCH`, normally 16 on CUDA or 32 on CPU), and FAISS. Set
`KG_RAG_FORCE_CPU=1` for predictable CPU behavior or GPU troubleshooting.
Lower the batch size after out-of-memory failures; raising it can improve
throughput only when device memory permits. The standard Docker image excludes
the semantic requirements, so enabling the variable alone is insufficient.

Search results are cached in memory by exact query string. The cache has no
configured size bound and lives until the graph object is replaced or the
agent restarts. Account for it when testing long-running, high-cardinality
query workloads.

## Context limits

Retrieval context and conversational memory are separate. Conversational
memory helps resolve follow-ups but is not supplied as KG evidence.

| Limit | Current behavior |
|---|---|
| `KG_RAG_CTX_CHARS` | Context budget; retrieval uses a 75% soft limit |
| `KG_RAG_SNIP` | Maximum PDF snippet characters per selected source |
| `KG_RAG_CONTEXT_VOLUME` | Maximum structured triples rendered |
| `KG_RAG_STRUCT_CTX` | Enables/disables the structured-triple block |
| Code snippets | Retrieval currently caps selected code-snippet nodes at six |
| Browser request history | Last 8 non-empty user/assistant messages |
| Browser persisted history | Last 80 messages per chat |
| Session memory | 12 recent turns; summary capped at 3,000 characters and compressed near 2,400 |

The context builder stops after adding a complete node section, so actual text
can exceed the soft limit by that final section. Code bodies and PDF snippets
are especially expensive. Lower top-k, snippet size, or structured triples
before shrinking context so far that direct evidence disappears.

A larger context budget is not automatically better: it increases LLM input
latency/cost and can bury the most relevant evidence. Evaluate sufficiency,
citation correctness, and response time together. Do not count the model's
maximum advertised context window as fully available; system prompts,
conversation messages, retrieved context, and output all share it.

## Worker and page settings

Extraction parallelism and extraction scope solve different problems:

| Control | Common default | Meaning |
|---|---:|---|
| Standalone `--max-workers` | `4` | Page-level worker threads in `extract_terms.py` |
| `F2W_WORKERS` / agent `--workers` | `8` | Page-level workers in orchestrated extraction |
| `CBORG_MAX_CONCURRENCY` | `5` | Process-wide in-flight CBORG cap for each sync/async phase |
| `F2W_EXTRACTION_MODE` | `targeted` | Process selected relevant pages or the full PDF |
| `F2W_TARGETED_MAX_PAGES` | `6` | Maximum pages per PDF in targeted mode |
| `F2W_MAX_PAPERS` | `1` in the UI launcher | Papers selected per evidence-growth round |
| `F2W_MAX_ROUNDS` | `3` | Maximum evidence-growth rounds |

More page workers do not bypass the CBORG limiter. They can overlap parsing
and queue requests, but excessive workers increase memory and throttling
pressure. Start with 2–4 on a small machine, increase only while throughput
improves, and reduce after 429s, timeouts, memory pressure, or unstable IPv6
connections. Do not raise `CBORG_MAX_CONCURRENCY` above the service allowance.

Targeted extraction is the primary latency control for interactive use. Raise
the page cap when relevant evidence spans a long methods/results section;
choose full extraction when the goal is corpus ingestion rather than answering
one question. Record selected pages in the extraction manifest so performance
choices remain visible in provenance.

## UI bundle size

Measure the production artifact, not development-server behavior:

```bash
cd ui
npm ci
npm run build
du -h dist/assets/* | sort -h
```

The current build reports a JavaScript chunk above Vite's 500 kB warning
threshold. It remains a successful build, but is tracked performance debt.

When adding a UI dependency or feature:

- compare production asset sizes before and after;
- prefer feature-level dynamic imports for infrequently used dialogs/viewers;
- import only required modules and icons;
- use deliberate Rollup chunking when it improves cacheability or initial
  loading; and
- measure initial transfer, parse, and interaction time after compression.

Do not silence the warning by only increasing `chunkSizeWarningLimit`. A smaller
chunk count is not necessarily a faster application; verify network and parse
behavior.

## Cache and volume management

Compose persists three named volumes:

| Volume | Growth source | Safe response |
|---|---|---|
| `splash-data` | Graph entities, links, embeddings, migrations | Back up; remove data only through an intentional graph reset |
| `agent-runs` | Session PDFs, terms, graphs, memory, workflow state | Delete individual obsolete sessions through the UI/API where possible |
| `agent-cache` | PyStow/model/ontology downloads | Can be recreated, but clearing causes later downloads and cold starts |

Inspect rather than guessing:

```bash
docker system df -v
docker compose exec agent du -sh /app/runs /app/.cache
docker compose exec splash du -sh /data
```

Restarting the agent clears in-memory graph, query, and PDF caches without
removing persistent volumes:

```bash
docker compose restart agent
```

PDF text uses an in-process LRU cache controlled by `KG_RAG_PDF_CACHE`
(default 256 documents). Each entry contains full extracted PDF text, so lower
the limit for memory-constrained, PDF-heavy workloads. Model/PyStow files in
`agent-cache` survive that restart.

To clear only the persistent agent cache, stop the agent, back up anything that
is not reproducible, and remove cache contents deliberately:

```bash
docker compose stop agent
docker compose run --rm --no-deps agent \
  sh -c 'find /app/.cache -mindepth 1 -delete'
docker compose up -d agent frontend
```

This forces later cache/model downloads. Do not use a global Docker volume
prune as a FAIR2WISE maintenance command. `docker compose down --volumes`
removes the graph, agent runs, and cache together and is destructive; take the
cold backups described in [Fresh-machine deployment](deployment.md#upgrade-procedure)
first.

## Symptom-driven tuning

| Symptom | First checks |
|---|---|
| Slow startup | Graph counts, Splash page requests, semantic index/model cold start, cache volume |
| High agent memory | Total nodes/edges, semantic embeddings, PDF-cache limit, long-lived query cache |
| Slow graph viewer | Keep 100-node subset, avoid `All`, inspect `/graph` size, browser scripting/memory |
| Weak retrieval | Confirm provenance/search text, then adjust top-k/stepwise/BFS; evaluate semantic mode separately |
| Slow answers | Context size, selected code/PDF snippets, model latency, CBORG queue/concurrency |
| Slow extraction | Targeted pages, workers, CBORG cap/rate limits, PDF page complexity |
| Large frontend load | Production chunk report, dynamic-import candidates, dependency delta |
| Growing Docker disk | Inspect each named volume and image/cache usage; back up before targeted cleanup |

Performance changes can alter answer quality and provenance, not just latency.
Use [Testing](testing.md) and the LLM quality suite where applicable before
making a tuning default permanent.
