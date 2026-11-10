# Neighbourhood cap: performance evaluation

Date: 2026-11-09. Branch `forge_merge-T-nbhd-eval` (from `forge_merge` `4997517`). Measurements and a recommendation only;
no app code changed and **nothing here is decided** (the owner decides). Answers the owner question of
2026-11-09 (`owner_decisions_2026-11-03.md`, eighteenth set): "Is that for testing, or during normal usage? ... maybe do a
performance evaluation to determine if we need a cap".

Script: `scripts/perf/neighborhood_cap_eval.py` (stdlib plus the app, reads only, container URL optional). Raw
tables were produced with:

```
python scripts/perf/neighborhood_cap_eval.py --graph storage/kg/matkg_rsoxs_v1.json --graph storage/kg/matkg_rsoxs_v4.json \
  --graph storage/kg/matkg_bl1101_v7.json --container-url http://127.0.0.1:8080/fair2wise/api \
  --container-source tiled:e2e_demo --container-source tiled:bl1101 --container-source json:rsoxs_v1 \
  --container-source json:rsoxs_v4 --container-source json:bl1101_v7 --probe-default-tiled
python scripts/perf/neighborhood_cap_eval.py --graph ... --extra-cap 2000 --extra-cap 5000 --extra-cap 10000   # cap sweep
```

## 1. Is the cap for testing or for normal use?

Normal use, but only on one path. Facts from the code:

* The browser calls `GET /graph/neighborhood/{id}` from one place (`GraphMockup.tsx`, `fetchGraphNeighborhood(activeFocusId,
  CITE_FOCUS_HOPS, viewerSource)`), and only when the focused node looks like a Tiled identity reference (id starting
  `tiled:` or `beamline:ESAF/Proposal/Sample/Scan`, or type ESAF/Proposal/Sample/BlueskyRun). It always asks for 3 hops.
* For every other focused node (the large JSON KGs), the browser does not call the route. It walks the whole graph it
  already holds (`neighborhoodSubgraph` in `kgCatalog.ts`), 1 hop by default, with no node cap at all.
* So the server cap binds in normal use only for Tiled focus. Today it never binds there (section 3). It does bind on
  `?source=json:...` requests, which the UI does not currently make for plain JSON focus but any API client can.

## 2. Setup

* Offline: the code path of the route (`normalize_json_graph`, `build_source_graph`, `GraphSources.neighborhood`, wire
  conversion `_graph_node_from_raw` / `GraphPayload` / `model_dump` / JSON), with `NEIGHBORHOOD_MAX_NODES` rebound per call
  (500, 2,000, 5,000, 10,000, 10^9 = uncapped). Legacy Tiled helper (`graph_subset_from_lookup`) replicated over the same
  graphs through a duck-typed lookup object.
* Live: the running `:local` container, GET only: `GET /graph?source=` (whole graph), `GET /graph/neighborhood/{id}?source=&hops=`.
  The cap cannot be lifted over HTTP, so live numbers are the capped case only.
* Samples per graph: top 20 nodes by degree (hubs) plus 200 seeded random non-isolated nodes (seed 7) for sizes; timings on
  all 20 hubs plus 25 random nodes. Hops 1 to 4, undirected, edges are the induced edges between kept nodes.
  Isolated nodes are excluded from the random sample (their neighbourhood is 1 node), so shares below are for connected nodes.
* Bytes are the uncompressed JSON body. Times are single runs per node (medians shown; first requests are cold, so max
  columns are noisy). Machine: the developer Mac, not the deployment hardware.

Graphs: `matkg_rsoxs_v1` 7,022 nodes / 13,848 edges (degree p50 2, p99 37, max 396); `matkg_rsoxs_v4` 22,012 / 57,575
(p99 55, max 670); `matkg_bl1101_v7` 1,143 / 4,623 (max 322; note it is far smaller than the other two); Tiled
`e2e_demo` 268 / 126 (max 13) and Tiled `bl1101` 421 / 937 (max 298). The `json:rsoxs_v1`, `json:rsoxs_v4` and `json:bl1101_v7`
container sources returned exactly the same sizes as the offline files (cross-check of the harness).

## 3. Neighbourhood sizes (uncapped), nodes per request

| graph | hops | p50 | p90 | p99 | max | share > 500 | > 2,000 | > 10,000 |
|---|---|---|---|---|---|---|---|---|
| Tiled e2e_demo | 1 | 2 | 3 | 12 | 14 | 0% | 0% | 0% |
| | 2 | 3 | 13 | 24 | 27 | 0% | 0% | 0% |
| | 3 | 3 | 24 | 28 | 28 | 0% | 0% | 0% |
| | 4 | 3 | 27 | 28 | 28 | 0% | 0% | 0% |
| Tiled bl1101 | 1 | 3 | 6 | 26 | 199 | 0% | 0% | 0% |
| | 2 | 199 | 203 | 233 | 385 | 0% | 0% | 0% |
| | 3 | 385 | 385 | 401 | 410 | 0% | 0% | 0% |
| | 4 | 410 | 410 | 410 | 410 | 0% | 0% | 0% |
| rsoxs_v1 | 1 | 3 | 18 | 158 | 292 | 0% | 0% | 0% |
| | 2 | 25 | 436 | 1,646 | 1,920 | 9.1% | 0% | 0% |
| | 3 | 361 | 2,465 | 3,976 | 4,076 | 45.0% | 15.0% | 0% |
| | 4 | 2,309 | 4,454 | 5,237 | 5,318 | 73.6% | 53.2% | 0% |
| rsoxs_v4 | 1 | 3 | 27 | 396 | 522 | 0.4% | 0% | 0% |
| | 2 | 45 | 1,009 | 4,809 | 6,294 | 15.0% | 9.1% | 0% |
| | 3 | 1,026 | 7,156 | 13,686 | 14,007 | 61.8% | 36.8% | 8.6% |
| | 4 | 7,619 | 14,922 | 17,996 | 18,309 | 86.8% | 75.4% | 35.0% |
| bl1101_v7 | 1 | 3 | 16 | 226 | 249 | 0% | 0% | 0% |
| | 2 | 53 | 305 | 796 | 820 | 5.9% | 0% | 0% |
| | 3 | 507 | 871 | 1,080 | 1,133 | 50.4% | 0% | 0% |
| | 4 | 990 | 1,133 | 1,133 | 1,133 | 95.5% | 0% | 0% |

Edges (uncapped, hop 3): rsoxs_v1 p50 1,264, max 10,229; rsoxs_v4 p50 5,438, p90 32,332, max 47,408; bl1101_v7 p50 3,027,
max 4,622; Tiled bl1101 max 936; e2e_demo max 28. The 3-hop share over 500 / 2,000 / 10,000 is the headline: 45 / 15 / 0 %
(rsoxs_v1), 62 / 37 / 8.6 % (rsoxs_v4), 50 / 0 / 0 % (bl1101_v7), 0 / 0 / 0 % for both Tiled corpora.

With the cap, every one of those requests above 500 nodes returns exactly 500 nodes (seed first, then breadth-first order);
edges at 500 nodes still reach 2,138 (v1), 4,573 (v4) and 3,389 (v7) at most. At hop 1 the cap clips only the rsoxs_v4 hubs
(522 down to 500).

The default Tiled path (no `source`) could not be sampled: the running container's default graph is the `kg.json` MatKG
(268 nodes, graph id `kg`), and the three ESAF/Proposal ids I tried returned 404 at hops 1 and 3 (all 45 sampled node ids
return 404 on the default path, because they belong to source graphs, not to the Tiled identity graph). Cite instead the
loader constants: `ENTITY_TYPES` has 4 types, `PAGE_SIZE` 100, `MAX_PAGES` 8 (`tiled_lookup.py`), which bounds that
identity graph at a few thousand nodes at most, so its 3-hop neighbourhood is bounded by that, not by the cap.

## 4. Latency and bytes, capped versus uncapped (hubs)

Offline route emulation, medians over the 20 hubs (BFS + `subgraph` + wire conversion + JSON). Cap sweep run: single run
per cell, some timing noise (+-30%).

| graph | hops | cap 500 nodes / bytes / ms | cap 2,000 | cap 5,000 | cap 10,000 | no cap |
|---|---|---|---|---|---|---|
| rsoxs_v1 | 3 | 500 / 0.57 MB / 39 (105 sweep) | 2,000 / 2.2 MB / 293 | 3,519 / 3.7 MB / 677 | 3,519 / 3.7 MB / 679 | 3,519 / 3.7 MB / 323 to 451 |
| rsoxs_v1 | 4 | 500 / 0.57 MB / 45 | 2,000 / 2.2 MB / 298 | 5,000 / 5.1 MB / 851 | 5,078 / 5.2 MB / 954 | 5,078 / 5.2 MB / 603 to 784 |
| rsoxs_v4 | 3 | 500 / 1.3 MB / 87 | 2,000 / 4.1 MB / 472 | 5,000 / 8.4 MB / 814 | 10,000 / 14.3 MB / 2,003 | 11,398 / 15.9 MB / 1,549 to 2,201 |
| rsoxs_v4 | 4 | 500 / 1.3 MB / 85 | 2,000 / 4.1 MB / 295 | 5,000 / 8.4 MB / 726 | 10,000 / 14.3 MB / 1,680 | 16,818 / 21.9 MB / 1,901 to 2,652 |
| bl1101_v7 | 3 | 500 / 0.90 MB / 26 | 1,064 / 1.7 MB / 87 | same | same | 1,064 / 1.7 MB / 48 to 80 |
| bl1101_v7 | 4 | 500 / 0.90 MB / 27 | 1,133 / 1.7 MB / 94 | same | same | 1,133 / 1.7 MB / 22 to 100 |

(Where two numbers are given for one cell, the first run in the full table and the sweep run differ because of noise; both
are shown as a range.) Cost per node on these graphs is about 1.1 to 1.3 KB of JSON (nodes carry publications) plus edges.
Max across all hubs and hops, uncapped: 23.4 MB and 4.3 s (rsoxs_v4 hop 4, offline), against 1.4 MB and 0.57 s capped.

Live route, capped as deployed (wall clock including HTTP, auth guard and projection), medians:

| source | hops | hubs ms / bytes | random nodes ms / bytes |
|---|---|---|---|
| tiled:e2e_demo | 3 | 3 ms / 5 KB (max 30 KB) | 3 ms / 4 KB |
| tiled:bl1101 | 3 | 38 ms / 435 KB (max 461 KB) | 36 ms / 435 KB |
| json:rsoxs_v1 | 3 | 61 ms / 566 KB | 61 ms / 541 KB |
| json:rsoxs_v4 | 3 | 224 ms / 1.28 MB (max 1,343 ms at hop 4) | 276 ms / 1.09 MB |
| json:bl1101_v7 | 3 | 138 ms / 887 KB | 141 ms / 969 KB |

The live capped latency is higher than the offline 500-node cost (rsoxs_v4: 224 ms live against 28 ms offline) because the live
route also pays HTTP, the guard projection of the whole payload and the cached-graph lookup; that overhead scales with
bytes, so an uncapped live request would cost more than the offline uncapped numbers above. That could not be measured
(see section 7). The legacy Tiled replica behaves like the JSON path: at an rsoxs_v4 hub, hop 3, 15.7 MB / 0.93 s uncapped
against 0.52 MB / 0.12 s capped (its edge walk is slower, its payload thinner).

## 5. What the browser does with it

Read from `GraphMockup.tsx`, `ForceDirectedCanvas.tsx`, `kgCatalog.ts`, `liveAgent.ts`:

* No client-side cap on a focus neighbourhood: whatever the server returns is merged and laid out. The only client limits are
  the "Nodes to render" selector of the whole-graph viewer (presets 10 to 100, 1,000, 10,000 and All, default All), which does
  not apply when a node is focused.
* Default view (`layoutMode = 'existing'`): SVG. `layoutGraph` places nodes on a golden-angle spiral (linear cost, a small
  force relaxation only up to 80 nodes), then renders only nodes and edges inside the viewport (`nodeIsInView`,
  `edgeIsInView`). Above 500 nodes it shows a banner: "SVG layout may lag at this scale" with a button to switch to Force.
* Force layout: a canvas drawn per animation frame with `d3.forceSimulation` (link distance 120, many-body charge -240,
  collide, center), d3 v7. The code comment says it "handles 10k+ nodes smoothly".
* Not measured: SVG or canvas drawing time in a real browser. A headless Playwright run was not attempted: `ui/node_modules`
  is not installed in the worktree (Playwright browsers are cached on the machine, but installing the UI dependencies and building
  a fixture served by the API would exceed the 30 minute budget). Cited instead: the in-code warning above.
* Measured instead: the d3-force layout compute cost alone, headless in Node (d3 v7 force modules, same forces and parameters as
  `ForceDirectedCanvas`, 2.4 edges per node as in the rsoxs 3-hop neighbourhoods, connected random graph, 300 ticks to
  settle, no drawing). In a browser the ticks are spread over frames, so the tick time is what each frame pays on top of drawing.

| nodes | edges | first tick | average tick | 300 ticks (settle) |
|---|---|---|---|---|
| 100 | 240 | 7 ms | 0.6 ms | 0.2 s |
| 500 | 1,200 | 7 ms | 3.4 ms | 1.0 s |
| 1,000 | 2,400 | 5 ms | 6.8 ms | 2.0 s |
| 2,000 | 4,800 | 16 ms | 16 ms | 4.9 s |
| 5,000 | 12,000 | 43 ms | 56 ms | 16.9 s |
| 10,000 | 24,000 | 103 ms | 135 ms | 40.6 s |
| 20,000 | 48,000 | 880 ms | 363 ms | 109 s |

Reading: 500 nodes settle in about one second and each tick is far below a 16 ms frame; 2,000 nodes cost about one frame per
tick (16 ms) before drawing, so the animation is at the edge of smooth and takes about five seconds to settle; 10,000 nodes cost
about 135 ms per tick (roughly 7 frames per second, 40 s to settle) before any drawing. The "10k+ smoothly" comment in
`GraphMockup.tsx` is not supported by this measurement for the force layout; it may hold for drawing a settled layout.
Payload parse time in the browser (a 16 to 22 MB JSON body) was also not measured.

## 6. Options and what each costs at the hubs

All numbers rsoxs_v4 hop 3 hubs unless noted (the worst graph); server time offline, live adds overhead.

| option | what changes | hub nodes / bytes / server ms | share of 3-hop requests clipped (rsoxs_v1 / v4 / v7) | browser at that size |
|---|---|---|---|---|
| A. Keep 500 | nothing | 500 / 1.3 MB / about 90 | 45% / 62% / 50% | about 1 s to settle |
| B. Raise to 2,000 | one constant | 2,000 / 4.1 MB / about 470 | 15% / 37% / 0% | about 5 s to settle, one frame per tick |
| C. Raise to 5,000 | one constant | 5,000 / 8.4 MB / about 810 | not measured (between the 2,000 and 10,000 rows) | about 17 s to settle, 56 ms per tick |
| D. Remove the cap, keep the 100,000 whole-graph cut | delete the constant | 11,398 / 15.9 MB / about 1,550 to 2,200 at hop 3; 16,818 / 21.9 MB / about 1,900 to 2,650 at hop 4 | 0% | about 40 s and 7 fps at 10,000; worse above |
| E. Make it a setting | an environment or settings key, default 500 (or higher) | whatever is set | whatever is set | owner can raise it per deployment |

Clipped share = share of random 3-hop requests whose uncapped neighbourhood exceeds the cap (section 3); at 10,000 it is 0% / 8.6% / 0%.

## 7. Recommendation (not decided)

Plain language: the cap is a safety limit for the large JSON graphs, and it is not what limits the Tiled demo today. Both
Tiled corpora in the running container are far below 500 nodes at every hop (largest neighbourhood 410 nodes, 460 KB, 40 ms), so
a cap or no cap makes no difference there; the question only matters for large graphs and for the Tiled graph if it grows
toward a facility-size corpus.

Evidence for a cap: without it, a single focus request on `rsoxs_v4` can return 16 to 22 MB and take 1.5 to 4 s of server
time before network cost, and the force layout in the browser then needs tens of seconds. 62% of 3-hop requests on that graph
would be over 500 nodes, 8.6% over 10,000. Evidence against 500 specifically: it clips half of all 3-hop neighbourhoods on
the large graphs, so most 3-hop views there are silently incomplete (the response carries no truncation flag; the whole-graph
route has `X-F2W-Graph-Truncated` but this route does not).

If a choice is wanted, the evidence favours **E with a higher default (about 2,000)**: it keeps the server and the browser inside
one frame per tick and about 4 MB, makes every 3-hop view on bl1101_v7 and 85% of those on rsoxs_v1 complete (63% of rsoxs_v4), and still guards the 22 MB case.
Keeping 500 (A) is also defensible if the demo corpora stay small, since it costs nothing today. Removing the cap (D) is the
only option that has a measured downside in both server time (to 4 s) and browser (tens of seconds) on the real graphs, and
is not recommended unless a response truncation flag and a client-side limit come with it. Whichever is chosen, consider
returning a truncated flag so the viewer can say "showing 500 of 11,398".

## 8. Not measured, caveats

* Live uncapped latency (the deployed cap cannot be lifted over GET); offline emulation used instead.
* Real-browser rendering (SVG and canvas drawing, JSON parse, memory); no Playwright run; only the d3-force layout compute
  was timed, in Node.
* The default Tiled path (no `source`): no ESAF/Proposal/Sample/Run ids exist in the running container's data to sample.
* gzip transfer size; deployment hardware and network; concurrent requests; single-run timings (cold first requests inflate
  max columns; medians are more reliable).
* `matkg_bl1101_v7.json` has 1,143 nodes in this checkout, not a large graph.

## 9. Update: truncation flag added

Truncation flag added (branch `forge_merge-T-nbhd-flag`): `GET /graph/neighborhood/{id}` now sets the response header
`X-F2W-Graph-Truncated: true` when the node cap cut the neighbourhood (Tiled path and `source` path; absent otherwise). The body
shape, status codes and the cap value (500, not configurable) are unchanged; the owner decision on the cap is still open. The
viewer shows "Neighbourhood limited to N nodes; N+ nodes match" when the header is present.
