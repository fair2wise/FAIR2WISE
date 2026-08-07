# End-to-end tutorials

These tutorials use the default Compose deployment at
`http://127.0.0.1:5173`. Start with a healthy stack and Splash graph mode:

```bash
docker compose up -d
curl -fsS http://127.0.0.1:5173/api/health | python3 -m json.tool
```

The health response should report `kg_mode` as `splash`. In **Settings**, use
Agentic workflow, Targeted extraction, and the `splash_links` graph source
unless a tutorial says otherwise.

## Ask using existing evidence

1. Open FAIR2WISE and create a new chat.
2. Ask: `What is the Advanced Light Source?`
3. Wait for retrieval and evidence evaluation to finish.
4. Inspect the answer's KG/PDF citations and publication block.
5. Select **View Knowledge Graph**, then select the highlighted
   **Advanced Light Source** node.

The tracked seed contains this node with a description, source PDF, page, and
context snippet. A normal successful run answers without proposing a paper
download. The node detail should expose the same evidence chain used by the
answer. If the agent starts literature search instead, confirm the active graph
source is Splash and that the Splash volume was initialized.

This is the safest post-deployment smoke test because it reads existing data
without changing the graph.

## Find and approve a paper

Use a new chat so an earlier pending action cannot affect the workflow.

1. Ask a specific question that the current graph is unlikely to answer, or
   explicitly ask: `Find an open-access paper about TOPIC and propose it for download.`
2. The agent searches arXiv and OpenAlex metadata and returns candidate cards.
   Search alone does not download or import anything.
3. Review title, authors, abstract/metadata, repository, DOI, and PDF
   availability. Candidate ranking is assistance, not a quality guarantee.
4. In chat, approve a candidate by number, exact title, DOI, or repository.
   Say `no` or `cancel` to stop without downloading.
5. Wait for the download result. FAIR2WISE streams to a temporary file,
   requires PDF magic bytes, and can reject a semantically irrelevant file.

Candidate selection is the download approval boundary. The downloaded PDF is
stored in the session work directory, but it is not yet part of the graph.
FAIR2WISE next presents a separate extraction decision.

## Extract and update the graph

!!! warning "Back up valuable graph data first"

    Approved extraction rebuilds the cumulative session graph and, in Splash
    mode, wipes/reimports Splash from that graph. This is a replacement, not an
    append/upsert; data absent from the cumulative terms artifact can be
    removed. Follow the backup step in
    [Fresh-machine deployment](deployment.md#upgrade-procedure) before using
    this workflow against a graph with irreplaceable manual edits.

Continue from a successfully downloaded paper:

1. Review the active paper and choose **Run extraction**. **Skip** leaves the
   PDF downloaded but does not add its terms.
2. Watch the progress phases: page selection, extraction, KG rebuild, Splash
   reimport, retrieval reload, and post-extraction evaluation.
3. With the default Targeted mode, only pages ranked for the question/missing
   topics are processed, up to the configured page cap. Use Full mode when the
   goal is corpus ingestion rather than answering one question.
4. Read the final response. It reports extracted evidence or explicitly says
   the approved paper was still insufficient.
5. Open **KG Viewer** or **Search Nodes** and search for a distinctive extracted
   term. Inspect `source_papers`, pages, publication metadata, and context.

The durable Compose artifacts are in `agent-runs` (`terms.json`, `kg.json`,
PDFs, and manifests) and `splash-data` (the editable graph). A successful LLM
call does not prove a successful graph update; confirm the rebuild, reimport,
and reload phases all completed.

## Search and edit nodes

1. Open **Search Nodes**, enter part of a node label, and select a result.
2. The graph focuses the selected node and its one-hop incoming/outgoing
   neighborhood.
3. Select **Edit** in the detail panel.
4. Change the label, category, description, publication fields, or code fields
   appropriate to the node.
5. To add a relationship, choose incoming or outgoing direction, find the
   other endpoint, enter a predicate, and stage it.
6. Save once to apply the staged node, snippet, and relationship updates.
7. Refresh/search again and verify the persisted value.

Edits are available only in `splash_links` mode. JSON mode is deliberately
read-only. The API validates that relationship endpoints exist, the edited
node participates in the relationship, the predicate is CURIE-like, and the
edge is not a self-link.

There is currently no login or per-user edit role. Anyone who can reach this
deployment's `/api` path can call the mutation endpoint; keep the default
loopback bind or add an authenticated reverse proxy. See
[Security model](security.md#graph-edits-and-control-plane-access).

## Complete local PDF-to-KG workflow

This batch tutorial is for a local development checkout because it produces
named source artifacts that are easy to inspect and version. Complete the
[local-development setup](getting-started.md#local-development-setup), put one
or more PDFs in an input directory, and choose new output names:

```bash
mkdir -p papers/tutorial
cp /path/to/example.pdf papers/tutorial/

./scripts/build_kg.sh \
  papers/tutorial/ \
  storage/terminology/tutorial_terms.json \
  storage/kg/tutorial_kg.json
```

The script uses CBORG extraction, writes temporary files, validates that both
stages produced output, and only then promotes the outputs. Existing outputs
are moved to `.bak`.

Inspect the portable artifacts before importing:

```bash
python3 -m json.tool storage/terminology/tutorial_terms.json >/dev/null
python3 -m json.tool storage/kg/tutorial_kg.json >/dev/null

cd splash_links
pixi run python scripts/import_kg.py \
  --dry-run ../storage/kg/tutorial_kg.json
cd ..
```

The terms file is the richest provenance artifact. The KG file is the portable
node/edge representation. Keep both if the graph may need to be audited or
rebuilt.

### Import into an empty Splash database

Start only Splash against a new, tutorial-specific database so the tracked
development database is not modified:

```bash
cd splash_links
SPLASH_LINKS_DB=../runs/tutorial-links.sqlite pixi run serve
```

In a second terminal:

```bash
cd splash_links
pixi run python scripts/import_kg.py \
  --if-empty ../storage/kg/tutorial_kg.json
```

Stop the standalone Splash process, return to the repository root, and start
the complete stack against the imported database:

```bash
SPLASH_LINKS_DB=../runs/tutorial-links.sqlite ./scripts/start_all.sh
```

`--if-empty` prevents accidental append into a populated store. The importer
creates new Splash UUIDs, preserves each MatKG ID as the entity URI and
`properties.matkg_id`, copies non-core provenance fields into properties, and
creates directed links after all entities exist.

Use a new database filename for a later clean tutorial run. Do not point this
example at `splash_links/links.sqlite` unless replacing that local graph is
intentional.

For a populated production graph, do not run a blind import: the importer does
not merge entities by MatKG ID. Use an intentional merge/reimport procedure and
take a database backup first. See [Knowledge graph](knowledge-graph.md),
[Splash Links](splash-links.md), and [Data provenance](provenance.md).
