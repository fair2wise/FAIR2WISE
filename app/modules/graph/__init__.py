"""Literature graph access (T08A): capability-aware GraphPort backends.

* :mod:`.ports` - re-exported contracts plus graph-specific errors and record helpers.
* :mod:`.identity_adapter` - F2W node/edge ids <-> Tiled entity/link ids (no display-name joins).
* :mod:`.snapshots` - read helpers over versioned JSON KG files (both shape families).
* :mod:`.backends.json` - read-only GraphPort over JSON snapshots.
* :mod:`.backends.tiled` / :mod:`.backends.tiled_client` - read/query GraphPort over Tiled 0.2.18.
* :mod:`.backends.tiled_lookup` - the retained legacy UI's live experiment-identity lookup and Settings probe
  (T17B real port of ``tiled_graph.py``; not a GraphPort).
* :mod:`.kg_update` - ``rebuild_kg`` (terms -> MatKG JSON), ``publish_extraction`` (T18E) and the legacy
  Splash entity/link operations (T17B real port of ``f2w_agent/kg_update.py``).

Publication (JSON -> Tiled import) is T08B and lives elsewhere; nothing here writes a graph.
"""
