# XCMate EPG Agent – Codex project rules

## Ground truth and linked repositories
- This repository is the planned **backend** for XCMate EPG Agent, not the Kodi add-on.
- Kodi client and installer: https://github.com/artattakk-creator/kodi-noodleprivate
- Master backend scope: https://github.com/artattakk-creator/kodi-noodleprivate/issues/485
- In-addon Opt-in and provider-fallback design: https://github.com/artattakk-creator/kodi-noodleprivate/pull/486
- Current bootstrap PR #1 is a **synthetic offline test only**, not a production scraper.

## Non-negotiable safety
- User requires **zero additional GitHub spend**. Use only standard free ubuntu-latest runners, normal Python code, no paid AI models, Codespaces or premium runners. Billing budget is set to 0 USD. Never increase it or ask to increase it to work around a blocked job.
- This repository is PUBLIC. Never commit user Kodi backups, portal/IPTV credentials, IPTV/M3U/stream URLs, personalized IDs, TMDB bearer token, API keys or unlicensed EPG data. Do not even put secrets in test fixture content or logs.
- Do not fetch, merge or republish third-party programme listings until their redistribution conditions have been checked and recorded.
- NEVER place TMDB_READ_ACCESS_TOKEN in XMLTV, Pages, issue comments, code, logs or image URLs. Authentication to api.themoviedb.org uses the Authorization: Bearer header. Only permitted uncredentialed CDN URLs may be rendered in the XMLTV.
- The TMDB token for this repository must be explicitly set as an Actions repository secret by the user; access to secrets in kodi-noodleprivate does not transfer automatically.
- No scheduled 24-hour task, public data publication, Pages deploy, production source download, server calls or modifications to Kodi add-on may be introduced in the bootstrap PR. All later stages require distinct review and explicit user release approval.

## Architecture and correctness expectations for a future implementation
- Fetch declared/allowed sources independently with ETag / If-Modified-Since when possible and SHA256 fallback; enforce network, XML, entity expansion, gzip decompression and file size limits.
- Keep durable source snapshots, dedup/mapping aliases, title/episode IDs, positive and negative artwork caches, and last validated XMLTV. GitHub-hosted runners are ephemeral: a daily run cannot perform true incremental work without persistent state.
- Source-qualified channel IDs and programme times; ID collisions across EPGShare/Kaimandura must never cause false merges. Kodi provider EPG always remains available as the local baseline independent of this service.
- Prefer deterministic parsing and matching; ambiguous media IDs should be left unmatched and auditable, not guessed. AI API is not needed.
- Artwork links must be from approved public HTTPS hosts (TMDB CDN, etc.), correctly attributed, validated, cached and never personalized. Channel logos require similarly approved sources.
- Output intended after clearance: xcmate-epg.xml.gz plus manifest.json and SHA256, atomic deploy and fallback to last valid version. Publish only when output is materially changed.
- Client integration is separately developed in kodi-noodleprivate, under a single "XCMate EPG Agent aktivieren" opt-in. Artwork consent remains separate. A valid XMLTV link does not prove user-visible BINGIE pixels.

## Development and QA
- Make incremental implementation changes on feature branches and draft PRs.
- Distinguish host/unit, GitHub Actions and actual RK3528/Android Kodi 21.3 results.
- Default to Python standard library; add dependencies only with justified licensing/security impact.
- Test 304/unchanged source, one-source delta, malformed gzip/XML, duplicate IDs, ambiguous titles, incorrect timezone, TMDB 401/403/429, atomic publish rollback, secret redaction and no-download with unchanged manifest.
- A successful free smoke test is only proof of free GitHub Actions availability, not EPG correctness, artwork delivery or redistribution permission.
