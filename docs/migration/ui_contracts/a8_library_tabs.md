# A8: Library and Extract tabs, Harvester (owner approval O-U4)

- **Task:** T40-T43, T46 (UI), with backend T33-T39, T45. **Gate:** H6. **Status:** implemented; the `FROZEN_UI_TREE` re-pin is its own commit (see `.github/workflows/forge-merge-ci.yml`).
- **Approval:** O-U4 (2026-11-02, owner request for the two tabs) and the 2026-11-03 request for category folders and the Harvester panel. The UI is additive: no existing component, route, persistence key or API call of the frozen UI changed behaviour. Everything new lives in new files except the edits below.

## 1. Edited frozen files

| File | Change |
|---|---|
| `ui/src/app/App.tsx` | wraps the shell in `QueryClientProvider` (already mounted, now used) and adds two `routes` entries, `/library` ("Library") and `/extract` ("Extract"), only when `GET /v1/library` answers (probe-and-hide, pattern of `judgeModel.ts`); on ALS the probe gets 404 and the sidebar is unchanged |
| `ui/src/app/components/KeepAliveMainContent.tsx` (new) | keeps the chat and graph views mounted while another tab is shown, so switching tabs does not lose chat or graph state |
| `ui/src/app/data/liveAgent.ts` | exports `parseSseBlock` and `readSseStream` (no behaviour change) |
| `ui/package.json`, `vitest.config.ts`, `src/test/setup.ts` | Vitest includes `*.test.tsx`; React Testing Library, user-event and jest-dom as devDependencies |

## 2. New components

`LibraryTab` (Papers / Harvester tab strip), `library/{LibraryDropzone,LibraryPapersTable,LibraryStatusBadges,FolderFilter,UploadTarget}`, `library/harvest/{HarvesterPanel,HarvestRunView,HarvestResults,HarvestRunsList,HarvestStatusBadge}`, `ExtractionTab`, `extraction/{ExtractionIdleForm,ExtractionRunView,PaperPicker,useJobLogStream}`, and the API and hook modules `apiHttp.ts`, `libraryApi.ts`, `libraryHooks.ts`, `extractionApi.ts`, `extractionHooks.ts`, `harvestApi.ts`, `harvestHooks.ts`. New code follows the ALS standards: TanStack Query for server state, JSDoc, Vitest + RTL tests per prop and state, ARIA (radiogroups for the folder filter, tablist, `aria-disabled` drop zone), `opacity-50 cursor-not-allowed` disabled styling.

## 3. API calls the UI makes (all `:local` only; ALS answers 404 and the tabs hide)

`GET /v1/library`, `GET|POST /v1/library/papers[?folder=]`, `POST /v1/library/papers/{id}/verify`, `GET /v1/library/{kgs,schemas,models}`, `POST /v1/library/estimate`, `POST /v1/library/kgs/continue`, `POST /v1/library/kgs/{name}/export`, `GET /v1/library/harvest/runs`, `GET /v1/library/harvest/runs/{id}/candidates`, `POST /v1/library/harvest/candidates/{id}/{pdf,dismiss}`, and `POST /v1/jobs` (kinds `library_verify`, `library_index`, `library_extract`, `library_harvest`), `GET /v1/jobs[?state=&kind=]`, `GET /v1/jobs/{id}`, `GET /v1/jobs/{id}/logs`, `GET /v1/jobs/{id}/stream` (SSE, resumed with `Last-Event-ID`), `POST /v1/jobs/{id}/cancel`. Policy rows V56-V59, V70-V79 in `app/api/policies.py`; the table with capabilities is `docs/migration/api/README.md`. None of these touches `PUT /settings`: the model chosen in the Extract tab applies to one run only.

## 4. Persistence

No new `localStorage` or `sessionStorage` key (`persistence_keys.json` is unchanged). The Library and Extract tabs hold state in memory and on the server (the job store and the library sidecar databases); a page reload re-attaches to a running extraction or harvest through `GET /v1/jobs?state=running&kind=...`.

## 5. Tests

- Vitest: component and API-client tests beside each file; `npm run build` in the Dockerfile frontend stage.
- Playwright (`ui/e2e/`): `library.spec.ts`, `extraction.spec.ts`, `harvester.spec.ts`, with the `/v1` mocks in `ui/e2e/fixtures/library.ts` (registered after `mockAgent`, so they win for `/v1/library` and `/v1/jobs`). Both configs pass: `npx playwright test` and `F2W_UI_PORT=5177 npx playwright test -c playwright.prefix.config.ts`.
- Backend: `tests/library`, `tests/api_v1` (route counts), `tests/jobs`, the ALS gateway tests (library routes 404).

## 6. Pin

CI compares `git rev-parse HEAD:ui` with `FROZEN_UI_TREE`. A change to `ui/` needs the `ui-change-approved` label on the pull request and a re-pin; the A8 re-pin is one commit that covers T40-T42, T46 and the T18D UI files (`317411b`), after all of them are committed. Do not push further `ui/` changes between the last UI commit and the re-pin.
