# Goodfireverse

A local, searchable research corpus of Goodfire's official blogs, research articles, public repositories, linked papers, and team podcast, lecture and YouTube appearances. The collection follows the Levinverse workflow, extended with GitHub snapshots and a paper bibliography.

Start with [the coverage report](reports/coverage.md), which lists the collected publications, pinned repositories, directly linked papers, confirmed appearances and unresolved work. Counts are generated from stored artifacts rather than search-result estimates. An appearance source URL is not a distinct event: audio feeds, publisher pages and video uploads can contain versions of the same conversation.

## Use the corpus

```powershell
python -m pip install -e ".[media,cuda-windows]"
goodfireverse report
goodfireverse search --query '"neural geometry"' --limit 10
goodfireverse validate
```

Search results include the source URL, local path and matching passage. Full article and paper text, publisher transcripts, timestamped ASR, and selected source files from the complete repository ZIP snapshots are stored locally under `private/`. These research copies are ignored by Git; the committed catalogs retain their paths, checksums and provenance. Local snapshots retain the complete ZIP download; the search index extracts text files up to 2 MB each and skips binary assets. No repository code or notebooks are executed by collection.

The Git repository contains the collector, source catalogs, pinned file-tree and snapshot manifests, explicit identity reviews, a hyperlink graph, coverage reports and tests. It does not contain recordings, models, credentials or full copyrighted research copies without a confirmed redistribution basis. Original source licenses still apply.

## Collect and resume

```powershell
goodfireverse discover
goodfireverse publications
goodfireverse enrich
goodfireverse repos
goodfireverse snapshots
goodfireverse papers
goodfireverse media
goodfireverse feeds
goodfireverse captions
goodfireverse audio --limit 10
goodfireverse report
goodfireverse validate
```

Commands resume from disk and merge records by canonical source URL. Responses are cached; use `--refresh` for a new upstream snapshot. Each failed acquisition remains in `catalog/acquisition.jsonl`. HTTP 403/429 responses start a host cooldown, and the collector honors robots exclusions. A failed caption request does not establish that captions are absent.

`config/sources.json` records scope and discovery seeds. `config/media-seeds.json` and `config/media-reviews.json` preserve source-linked participation decisions. Directory search hits and references remain candidates until verified. Breweries, bands, commentary, AI-narrated articles and clips are separated from confirmed team appearances.

The copied Levinverse Whisper helper supports `GOODFIREVERSE_WHISPER_MODEL` (including a cached model directory) and `GOODFIREVERSE_WHISPER_BATCH_SIZE` (1–16). Local ASR uses CUDA FP16, preserves segment/word timestamps, and is labeled unverified. Speaker diarization is not performed. Use one GPU worker at a time; see [COLLECTION.md](COLLECTION.md).

## Data map

| Location | Contents |
|---|---|
| `catalog/publications.jsonl` | Official index and sitemap URLs, titles, dates, authors, topics and text provenance |
| `catalog/repositories.jsonl` | Official repositories and separately labeled linked projects |
| `sources/repositories/` | Pinned GitHub file trees and snapshot manifests |
| `catalog/papers.jsonl` | Direct research targets and a separate cited bibliography |
| `catalog/appearances.jsonl` | Publisher, feed and video source records with participation status |
| `catalog/transcripts.jsonl` | Stored transcript provenance, quality, hashes and companion paths |
| `catalog/directory-candidates.jsonl` | Podcast search results, including unresolved mentions |
| `catalog/backlog.jsonl` | Missing acquisitions and identity review |
| `ontology/relations.jsonl` | Observed hyperlinks and explicitly unreviewed lexical topic tags |
| `reports/coverage.json` | Machine-readable actual coverage and limits |
| `private/` | Ignored local text and repository snapshots |
| `.cache/search.sqlite3` | Rebuildable full-text search index |

Paper links do not establish Goodfire authorship. Redirected research link posts can be abstract landing pages rather than complete papers; directly linked paper texts are counted separately. The graph records source relationships, not validated scientific claims. Exhaustive coverage is not asserted while source and identity queues remain unresolved.

## Validation

```powershell
python -m unittest discover -s tests -v
goodfireverse validate
```

Collection runs when invoked. No recurring worker or remote publication is installed by this repository.
