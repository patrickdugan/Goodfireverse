from __future__ import annotations
import json
import re
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path
from .core import canonical, digest, now, rows, write_json


def label(row):
    return row.get('title') or row.get('name') or row.get('title_hint') or row.get('index_label') or row['url']


def clean(text):
    return str(text or '').replace('|', '\\|').replace('\n', ' ')


def report(root):
    catalogs = {name: rows(root / f'catalog/{name}.jsonl') for name in ['publications', 'repositories', 'papers', 'appearances', 'transcripts', 'acquisition']}
    pubs, repos, papers, media, transcripts, acquisitions = [catalogs[n] for n in catalogs]
    official_repos = [r for r in repos if r.get('scope') == 'official_org']
    primary_papers = [r for r in papers if r.get('relationship') in {'official_research_target', 'official_repository_link'}]
    confirmed = {r['url']: r for r in media if r.get('participation') == 'confirmed'}
    def present(record, field):
        return bool(record.get(field)) and (root / record[field]).is_file()
    in_scope_transcripts = [t for t in transcripts if canonical(t['url']) in confirmed and present(t, 'text_path')]
    coverage = {
        'generated_at': now(), 'publication_urls': len(pubs), 'blogs': sum(p['kind'] == 'blog' for p in pubs),
        'research_entries': sum(p['kind'] == 'research' for p in pubs),
        'publication_texts_stored': sum(bool(p.get('text_path')) and (root / p['text_path']).exists() for p in pubs),
        'official_repositories': len(official_repos), 'pinned_repositories': sum(bool(r.get('revision')) for r in official_repos),
        'repository_snapshots': sum(present(r, 'archive_path') for r in official_repos),
        'indexed_repository_source_files': sum(r.get('indexed_source_files', 0) for r in official_repos if r.get('source_path') and (root / r['source_path']).is_dir()),
        'linked_repository_records': len(repos) - len(official_repos),
        'primary_linked_paper_records': len(primary_papers), 'primary_paper_texts_stored': sum(present(p, 'text_path') for p in primary_papers),
        'reference_paper_urls': len(papers) - len(primary_papers),
        'appearance_source_records': len(media), 'confirmed_appearance_source_records': len(confirmed),
        'appearance_participation': dict(Counter(r.get('participation', 'unknown') for r in media)),
        'transcript_records_all': len(transcripts), 'confirmed_transcript_source_records': len(in_scope_transcripts),
        'distinct_confirmed_text_hashes': len({t['text_sha256'] for t in in_scope_transcripts}),
        'confirmed_transcript_kinds': dict(Counter(t['kind'] for t in in_scope_transcripts)),
        'acquisition_failures': sum(a['status'] == 'failed' for a in acquisitions),
        'exhaustive_coverage_established': False,
        'limits': ['Source URLs are not distinct event counts.', 'Some paper links are citations rather than Goodfire-authored work.',
                   'YouTube rate limiting leaves caption availability unknown.', 'Full texts and snapshots are local research copies in ignored private/.',
                   'Machine transcripts are unverified and not diarized.', 'No recurring collection schedule is installed.']}
    write_json(root / 'reports/coverage.json', coverage)
    backlog = []
    for record in pubs:
        if not present(record, 'text_path'): backlog.append({'url': record['url'], 'reason': 'publication_text_missing'})
    for record in primary_papers:
        if not present(record, 'text_path'): backlog.append({'url': record['url'], 'reason': 'primary_paper_text_missing'})
    for record in official_repos:
        if not present(record, 'archive_path'): backlog.append({'url': record['url'], 'reason': 'repository_snapshot_missing'})
    transcribed_urls = {canonical(t['url']) for t in transcripts if present(t, 'text_path')}
    for record in media:
        if record.get('participation') == 'confirmed' and canonical(record['url']) not in transcribed_urls and record.get('transcript_status') != 'publisher_version_available':
            backlog.append({'url': record['url'], 'reason': 'confirmed_source_transcript_missing'})
        elif record.get('participation') == 'review_required':
            backlog.append({'url': record['url'], 'reason': 'participation_or_recording_identity_review'})
    from .core import upsert
    backlog_path = root / 'catalog/backlog.jsonl'
    backlog_path.write_text('', encoding='utf8')
    upsert(backlog_path, backlog)
    lines = ['# Goodfireverse coverage', '', f'Collected through {coverage["generated_at"][:10]} UTC.', '',
             f'{coverage["publication_texts_stored"]}/{len(pubs)} publication URLs stored locally: {coverage["blogs"]} blogs and {coverage["research_entries"]} research entries.', '',
             f'{coverage["repository_snapshots"]}/{len(official_repos)} official repository snapshots at pinned commits; {coverage["indexed_repository_source_files"]} source files indexed.', '',
             f'{coverage["primary_paper_texts_stored"]}/{len(primary_papers)} directly linked paper texts stored; {coverage["reference_paper_urls"]} additional bibliography URLs cataloged separately.', '',
             f'{len(in_scope_transcripts)} transcript source records for confirmed appearances. These include publisher versions and machine ASR; upload URLs and mirrors are not distinct events.', '',
             'Full texts are in ignored `private/`; repository source archives are in `private/repository-archives/`. Use the search command for local retrieval.', '',
             '## Publications', '', '| Kind | Title | Published | Local text |', '|---|---|---|---|']
    lines += [f'| {p["kind"]} | [{clean(label(p))}]({p["url"]}) | {clean(p.get("published"))} | {"stored" if p.get("text_path") else "missing"} |' for p in pubs]
    lines += ['', '## Official repositories', '', '| Repository | Revision | Snapshot | Source files |', '|---|---|---|---|']
    lines += [f'| [{clean(r.get("name"))}]({r["url"]}) | {r.get("revision", "unknown")[:12]} | {r.get("snapshot_status", "missing")} | {r.get("indexed_source_files", 0)} |' for r in official_repos]
    lines += ['', '## Directly linked papers', '', 'These are primary targets of official research links or official repository README links. A link alone does not establish authorship or publication venue.', '', '| Paper | Relationship | Text |', '|---|---|---|']
    lines += [f'| [{clean(label(p))}]({p["url"]}) | {p.get("relationship")} | {p.get("status", "pending")} |' for p in primary_papers]
    lines += ['', '## Confirmed appearance sources', '', '| Source | Role | Transcript |', '|---|---|---|']
    transcript_lookup = {canonical(t['url']): t for t in transcripts}
    lines += [f'| [{clean(label(r))}]({r["url"]}) | {clean(r.get("role"))} | {transcript_lookup.get(canonical(r["url"]), {}).get("kind", r.get("transcript_status", "pending"))} |' for r in confirmed.values()]
    lines += ['', '## Remaining work', '', 'See `catalog/backlog.jsonl` for missing text and recording review, and `catalog/acquisition.jsonl` for retrieval failures. Source access failures are not evidence that a transcript is absent.', '', '## Coverage limits', '']
    lines += ['- ' + s for s in coverage['limits']]
    (root / 'reports/coverage.md').write_text('\n'.join(lines) + '\n', encoding='utf8')
    relations = []
    for p in pubs:
        for target in p.get('outlinks', []):
            predicate = 'links_paper' if any(target == x['url'] for x in papers) else 'links_source'
            relations.append({'subject': p['url'], 'predicate': predicate, 'object': target, 'evidence_url': p['url'], 'status': 'observed_hyperlink'})
        for topic in p.get('topics', []):
            relations.append({'subject': p['url'], 'predicate': 'tagged_topic', 'object': topic, 'evidence_url': p['url'], 'status': 'lexical_tag_unreviewed'})
    relation_path = root / 'ontology/relations.jsonl'
    relation_path.parent.mkdir(parents=True, exist_ok=True)
    relation_path.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in relations), encoding='utf8')
    index(root)
    print(json.dumps(coverage, ensure_ascii=False, indent=2))


def index(root):
    db = root / '.cache/search.sqlite3'
    db.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute('DROP TABLE IF EXISTS documents')
        conn.execute('CREATE VIRTUAL TABLE documents USING fts5(title, source_url UNINDEXED, path UNINDEXED, text)')
        seen = set()
        for name in ['publications', 'papers', 'appearances', 'transcripts', 'repositories']:
            for r in rows(root / f'catalog/{name}.jsonl'):
                path = r.get('text_path')
                if not path or path in seen or not (root / path).exists(): continue
                seen.add(path)
                conn.execute('INSERT INTO documents VALUES (?,?,?,?)', (label(r), r['url'], path, (root / path).read_text(encoding='utf8', errors='replace')))
        for r in rows(root / 'catalog/repositories.jsonl'):
            if not r.get('source_path'): continue
            for path in (root / r['source_path']).rglob('*'):
                if path.is_file():
                    conn.execute('INSERT INTO documents VALUES (?,?,?,?)', (r.get('name', '') + '/' + path.name, r['url'], path.relative_to(root).as_posix(), path.read_text(encoding='utf8', errors='replace')))


def search(root, query, limit=10):
    if not query: raise ValueError('--query is required')
    with closing(sqlite3.connect(root / '.cache/search.sqlite3')) as conn:
        matches = conn.execute("SELECT title, source_url, path, snippet(documents,3,'[',']',' … ',32) FROM documents WHERE documents MATCH ? ORDER BY rank LIMIT ?", (query, limit)).fetchall()
    for title, url, path, snippet in matches:
        print(json.dumps({'title': title, 'source': url, 'path': path, 'excerpt': snippet}, ensure_ascii=False))


def validate(root):
    errors, checked = [], 0
    for name in ['publications', 'papers', 'appearances', 'transcripts', 'repositories']:
        data = rows(root / f'catalog/{name}.jsonl')
        urls = [r['url'] for r in data]
        if len(urls) != len(set(urls)): errors.append(f'Duplicate keys in {name}')
        for r in data:
            for field, hash_field in [('text_path', 'text_sha256'), ('archive_path', 'archive_sha256')]:
                if not r.get(field): continue
                path = (root / r[field]).resolve()
                if not path.is_relative_to(root.resolve()): errors.append(f'Path outside corpus: {path}'); continue
                if not path.exists(): errors.append(f'Missing file: {path}'); continue
                if digest(path.read_bytes()) != r.get(hash_field): errors.append(f'Hash mismatch: {path}')
                checked += 1
            if r.get('segments_path'):
                companion = json.loads((root / r['segments_path']).read_text(encoding='utf8'))
                previous = -1
                for segment in companion.get('segments', []):
                    if segment.get('start', 0) < 0 or segment.get('end', 0) < segment.get('start', 0): errors.append(f'Reversed timing: {r["url"]}')
                    if segment.get('start', 0) < previous - 0.2: errors.append(f'Nonmonotonic timing: {r["url"]}')
                    duration = companion.get('duration_seconds', float('inf'))
                    if segment.get('end', 0) > duration + 1: errors.append(f'Segment exceeds media duration: {r["url"]}')
                    if any(not (0 <= w['start'] <= w['end'] <= duration + 1) for w in segment.get('words', [])):
                        errors.append(f'Invalid word timing: {r["url"]}')
                    previous = segment.get('start', 0)
    result = {'checked_local_files': checked, 'errors': errors}
    write_json(root / 'reports/validation.json', result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if errors: raise SystemExit(1)
