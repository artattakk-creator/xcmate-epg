# Issue 6: complete private XMLTV import

Authority: GitHub Issue #6 and the user's V2 master assignment. Execute autonomously on
`feat/full-epg-coverage-artwork-issue6`, based on PR #5 at e216572. Preserve all old exports,
47 tests, caches and LastGood. No source hosts/URLs, real content, personal IDs or secrets in Git.

## Design decisions

Use Python standard library, Expat callbacks with immediate record release, and private SQLite
spooling. Raising the old tree parser's 32-MiB limit would retain the entire tree; streaming without
spooling cannot safely resolve out-of-order channel declarations, deduplication and two views.
The builder selects SQLite spooling as the bounded-memory implementation of the user's spec.
Existing small-input APIs remain unchanged. Full import/export use new modules and separate state.

All configured sources are read completely, no channel whitelist. Defaults: 64 MiB compressed,
256 MiB expanded per source, expansion ratio 200, one million records, depth 32, 2 MiB record,
300 seconds per source. All limits are configurable and reported. Only the exact external
`tv SYSTEM "xmltv.dtd"` declaration is tolerated without loading a DTD. Reject internal subsets,
entities and other declarations. UTF-8, valid UTC-aware times, nonempty title and channel references
are required; isolate malformed events, quarantine structurally invalid sources. Never silently cut.

Keep source-qualified identity; exact duplicate events may be collapsed with provenance, while
conflicting/overlapping alternative records remain separately auditable. Same channel names across
sources do not authorize merging. Confirmed provider mappings alone feed the device profile.
Record confirmed/candidate_manual_review/unmapped and all denominators, including zero coverage.
Private persistent mapping store and review JSON preserve manual overrides and evidence.

Source snapshots and original media/URL metadata stay private. Validate artwork URL scheme,
host, credentials, redirects, media response and provenance before XMLTV inclusion. Preserve all
other valid programme metadata. Use existing aggregate/artwork cache logic on bounded record trees;
extend TVMaze fallback and typed sidecar for poster/backdrop/episode_still/channel_logo.
TMDB requires explicitly configured runtime secret; never copy a GitHub secret to this machine.
API failures must not poison negative cache. Budgeted requests and coverage gaps are reported.

Generate full-source and confirmed-device views with independent immutable generations,
XMLTV-GZIP, manifest and SHA. Stream writer validates references/times/counts/SHA and commits
manifest last; failed refresh/export retains LastGood. Additional private typed artwork sidecar,
mapping review, provenance and quality report remain outside Git. No full-success status with
missing sources, silent limits, absent required profile, or zero verified artwork/logo hits.

Existing raw snapshots are preferred for reproducible acceptance against 1,278/185,946 baseline.
Run actual bounded API/image requests where authorized; never claim mocks as live evidence.
Drive backup is prepared separately, destination owner-only; new file/target approval checked
before upload. Previous approved pilot folder/artifacts remain unchanged. No merge, Pages,
public payloads, daily jobs, paid APIs or Kodi changes.
