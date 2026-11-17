# Real full private EPG run implementation plan

**STOPPED BY USER — controlled workspace handoff.** No more live calls, artwork
attempts or regression reruns. Code is saved at `1e80645`; the already running
suite finished with 217 tests / exit 0. See
`docs/ABSCHLUSS_PRIVATER_WORKSPACE_2026-11-17_DE.md` for measured results, the
verified local checkpoint and the still unverified transfer to a new workspace.
The final identity audit and final-code real-data replay are incomplete.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans.

**Goal:** Repeat the complete real private import and process all eligible identities with resumable, paced provider lookups and measured output.
**Architecture:** Reuse frozen snapshots, validated SQLite spool, persistent artwork caches and immutable run bundles. Add optional bounded provider-level bulk/cache/cast support; retain exact matching. Separate source completeness, TMDB readiness, artwork coverage and provider mapping status.
**Tech Stack:** Python standard library, SQLite, Expat, existing private pipeline.
**Spec:** User's current full-run instruction and Issue #6 latest complete-private-run comment; existing docs/superpowers/specs/2026-11-17-full-epg-design.md.

## Global Constraints
- All three sources and every valid programme; no synthetic replacement or silent subset.
- Private data never in Git/logs/public artifacts; preserve pilot, caches and LastGood.
- No merge, public publish, daily job, Kodi modification or paid APIs; GitHub budget0USD.
- Only authorized runtime-secret/network paths; no credential values/files/GitHub-secret retrieval.
- Unknown identities remain unconfirmed; all image references concretely verified and attributed.

## Review Focus
- Cached show details must be revalidated for each title/year; no alias contamination.
- Bulk episode lists can have duplicates/specials/missing fields; abstain without guessed episode mapping.
- Auth/rate/budget failures are resumable, never permanent title negatives.
- Series cast/rating scope cannot masquerade as episode-specific evidence.
- Final progress must distinguish all eligible identities visited, budget/network unattempted and exact no-match results.

### Task1: Runtime/readiness and private baseline
- [x] Read Issue6/latestcomment,PR7,handoff andAGENTS.
- [x] Verify branch,privateinputs/caches/pilot; freshnormalTMDBconfigurationprobe.
- [x] Preserve prior fullrun andcreate resumecheckpoints before livechanges.

### Task2: Optional scalable metadata adapter
- [x] TDD rawsearch/detailcaches, boundedbulkepisodes,cast,provider counters,scopefix.
- [x] Fullregressions and commits; adapter/gallery independently reviewed.
- [ ] Independent review of the final consolidated safety fix (stopped).

### Task3: Actual full-identity live enrichment
- [x] Build private unique-identity queue, visit everyeligibleidentity,cache/checkpoint providerresult.
- [x] Use globalpaced requests and bounded stages; measure failures/budgets/exact identities.
- [x] Generate fullandconfirmeddevice XMLTV/manifests/hashes/sidecars;preserve oldversions.

### Task4: Independent acceptance and delivery preparation
- [ ] Verify all source programme metadata, all outputreferences/media evidence/mappings,runhashes,pilot andLastGood.
- [x] Full source-content audit; mapping/logo audit and current image proofs.
- [ ] Final source/provider identity audit and fresh sample (stopped, untriaged).
- [x] Prepare privateoutputpackage and full offline closure checkpoint, no upload.
- [x] Update public aggregate/handoff with measured results and explicit gaps.
- [ ] Independently restore private data in a new workspace after approved transfer.
