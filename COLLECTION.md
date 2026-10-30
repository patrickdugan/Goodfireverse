# Collection runbook

## Sources and scope

The official Goodfire blog and research indexes plus sitemap define the core publication set. Public GitHub API pagination enumerates `goodfire-ai`, including forks and archived repositories. Each repository snapshot is pinned to a commit; a separately labeled linked repository is not counted as an official repository.

Official research redirects, explicit paper links and repository README links identify primary paper candidates. Bibliographic references remain separate. The research index also contains essays, summaries and link posts, so its entry count is not a count of formal papers.

Publisher sitemaps, explicit source seeds, and the public Apple episode directory seed appearance discovery. Episode descriptions and feeds supply identity evidence and audio enclosures. Search is bounded by directory ranking and current feed windows. Index-only conference pages and unreviewed video references are not confirmed appearances. Upload dates and publication dates do not establish event dates.

## Local text

Full texts, captions, ASR and repository archives are in ignored `private/`. Media is in ignored `media/`; HTTP caches and models are in `.cache/`. Text records retain source and response hashes. Original publisher labels and timestamps are preserved when present; absent speakers are not invented. Publisher transcript versions linked to an embedded video are labeled as un-compared versions, not exact audio equivalence.

The collector's own public catalogs and code can be committed independently of the local research copies. Review source licenses before any future redistribution. No push, PR or publishing action is part of collection.

## CUDA processing

Before an audio batch, inspect `reports/audio-worker.json`, verify its PID, and check any Levinverse audio worker using the same machine. The Goodfireverse process holds a cross-corpus lock under this workspace's `work/` directory; an older Levinverse installation has its own lock and must be checked separately. Set `GOODFIREVERSE_GPU_LOCK` to a shared location if using multiple workspaces.

The optional `GOODFIREVERSE_LEVINVERSE_ROOT` points to an existing Levinverse checkout; the worker checks its `reports/audio-worker.json` for a live running PID before starting. The initial collection used the existing cached turbo model and RTX 5080. No cloud transcription or paid compute is required.

Only confirmed public publisher audio is queued. Identical audio hashes reuse text. Matching publisher titles skip avoidable ASR, but do not prove that separately edited audio/video versions are identical. ASR remains unverified and has no diarization.

## Continuation

1. Read Git status, `reports/coverage.json`, `catalog/provider-state.json`, and any worker manifest.
2. If a batch is running, inspect it and allow it to finish before rewriting its catalogs.
3. Resume the applicable collection command. Never erase a cooldown merely to retry another video.
4. Apply identity reviews, regenerate the report/search index, validate checksums/timing, and run tests for code changes.
5. Commit only completed task files on the current branch. Leave unrelated changes untouched. Push or open a PR only when explicitly requested.

YouTube's existing machine-wide HTTP 429 restriction was inherited from Levinverse through November 1, 2026 at 10:29 UTC (18:29 Singapore). Availability remains unknown until a permitted retry succeeds. No browser, account-cookie or proxy workaround is used.

See `catalog/backlog.jsonl` for the exact unresolved source queue. No recurring schedule has been created.
