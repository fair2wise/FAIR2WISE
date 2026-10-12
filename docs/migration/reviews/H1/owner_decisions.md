# H1 owner decisions log

Decisions the project owner has made during H1 contract work. These are not
the H1 gate approval; that is recorded separately in `decision.json` when the
owner reviews the H1 packet.

## O-D1: verbatim snippets in stored KG JSON (2026-10-12)

**Finding (T21A).** Tracked `storage/kg` JSON holds about 127k verbatim
context snippets. A 300-sample check matched every sampled snippet to its
cited PDF page, and combined they reproduce a median 31% (max 48%) of each
paper's text across 90 papers. `storage/terminology` holds about 141k more.
Under D19 this would make the files a partial source-text export, in the
public repository and in any image that includes `storage/`.

**Decision.** Keep the KG JSON files, snippets included, in the repository and
in the Docker image.

**Scope of the exception.**

- Applies to stored KG artifacts: committing them to Git (D31) and baking them
  into images (H0 E3).
- Does not relax D19 for serving. What routes, SSE, traces, graph detail and
  exports return to users still goes through the T21A disclosure projection.
- Does not cover raw PDFs, full parsed paper bodies, figure crops, the
  source-RAG index (`storage/source_index`, H0 E4), credentials, local paths
  or operational databases.
- `storage/terminology` is already in the image under H0 E3 and stays in Git;
  this decision does not change that.

**Updated in.** `docs/adr/content-disclosure.md` (O-D1); T26A release contract
(relayed to the T23A/T26A worker).
