# Screenshot comparison (developer tool)

Not run by CI or the push gate. Takes 15 scenes (17 images) of the main pages and every dialog against the
mocked backend, so a global CSS change can be compared before and after.

```
cd ui
SHOT_DIR=/tmp/shots_before npx playwright test -c tools/screenshots/playwright.config.ts   # on the old code
SHOT_DIR=/tmp/shots_after  npx playwright test -c tools/screenshots/playwright.config.ts   # on the new code
python tools/screenshots/compare.py /tmp/shots_before /tmp/shots_after /tmp/shots_compare   # side-by-side, needs Pillow
```

Scenes: chat home and answer, KG viewer, Settings, Paper search, Search chats, Bookmarks, Docs, Library Papers,
Harvester, Extract (idle, continue + Publish to Tiled dialog), Operations, Pending Approvals (with the confirm dialog), Schema panel (opened from the Extract form).
Create the output directory first. Add a scene by adding a `test(...)` to `screenshots.spec.ts`.
