# Errata index (post-v8)

- E3 supersedes the image-exclusion clause for KG JSON (see `reviews/H8/spec_errata_E3_draft.md`). Flat `storage/kg/matkg_*.json` copies ship in images; nested `storage/kg/<corpus>/v<N>/graph.json` duplicates do not. `deployment_profiles.json` and `source_lock.json` `bundled_in_images` now read `true` accordingly.

Owner approval (2026-11-06): "E3 errata. Change what you need to in order to allow KGs to ship with the image".
