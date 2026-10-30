# AIMfocus – Frontend

Vue 3 + Vite single-page app. It holds no patient logic of its own: everything comes from the
core's `/api` routes ([JSON reference](../core/docs/screen-api.md)). Setup for the whole app:
[docs/getting-started.md](../docs/getting-started.md).

```bash
npm install
npm run dev        # http://localhost:5173, /api/* proxied to the core on :8001
npm run build      # production bundle in dist/
```

| Variable (`frontend/.env.local`) | Effect |
|---|---|
| `VITE_BASE_URL` | Core API root instead of `/api` through the dev proxy |
| `VITE_SIMULATE_SCREEN=1` | Dashboard replays the recorded screens (`/api/v2/simulated/stream`) instead of a live session; also `#/patient/dashboard?simulate` |

## Views (`src/views/`, hash routes in `src/router.js`)

| Route | View | What it does |
|---|---|---|
| `#/` | `IAmView.vue` | "What's your role, and what do you need?" – free text or voice (Web Speech API), rotating example prompts, recent sessions. Submitting calls `POST /api/v2/screens` so the core starts working while the patient is picked |
| `#/patient` | `PatientView.vue` | Patient search by name, ID or date of birth (`PatientAutocomplete.vue`). The pick is sent at once (`PUT /api/v2/screens/<id>/patient`); a short role label and icon come from `/summary` |
| `#/patient/dashboard` | `PatientDashboardView.vue` | The result screen, live over SSE, plus the chat |
| `#/stats` | `StatsView.vue` | Patients and validated FHIR bundles/documents in the core (`GET /api/v2/stats`) |

## Result screen (`src/components/screen/`)

`layout.js` turns the screen's cards into a reading order: tiers top to bottom (**Critical** –
check before acting, **Important** – for this task, **Background** – context, collapsed to one
line per card), and inside a tier one block per kind:

| Component | Shows |
|---|---|
| `ValueTable.vue` | All single values of a tier in one table: label, value, date, contradiction and source icons; older values on demand |
| `DataTable.vue` | Table cards (e.g. medication with strength, schedule, since) merged over the union of their columns |
| `ListGroup.vue` | Findings, diagnoses, recommendations, newest first |
| `TrendGroup.vue`, `TrendChart.vue` | Measurements over time (Chart.js), one chart per unit; clicking a point opens its source; contradicted readings are red triangles |
| `ImageGroup.vue`, `ImageViewer.vue` | DICOM studies: three-plane preview, click to scroll slices (wheel, arrows, slider) |
| `StateTag.vue` | `from text` (found by the text search, quote checked) and `check` (failed verification) |

Trust and sources:

| Component | Shows |
|---|---|
| `SourceButton.vue`, `SourceExcerpt.vue` | The cited passage inside its document section, highlighted (`GET /api/passages/<id>/context`) |
| `DocumentDrawer.vue`, `HighlightedText.vue` | The whole letter with the passage highlighted and scrolled into view |
| `ContradictionBadge.vue`, `ContradictionList.vue` | Warning icon; hover for the summary, click for every linked passage |
| `Popover.vue` | Floating panel (`@floating-ui/dom`), one open at a time |
| `ChatTurn.vue` | One question with its text answer, sources and up to two cards rendered like the screen |

Other pieces: `src/api.js` (every core call, SSE handling for screens and the streamed chat),
`src/store.js` (current session and recent sessions in `localStorage`),
`components/aimfocus-chase/` (the squirrel-and-acorn loading animation, a nod to the team
name), `AppFooter.vue` (team name and the current commit, injected by `vite.config.js`).
