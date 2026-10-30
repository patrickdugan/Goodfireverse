"""Read pinned GitHub source archives without executing repository contents."""
import io
import json
import zipfile
from pathlib import PurePosixPath
from .core import attempt, digest, now, rows, upsert
from .collect import paper_url


def snapshots(root, fetch):
    allowed = {'.md', '.py', '.rs', '.toml', '.yaml', '.yml', '.json', '.txt', '.sh', '.ps1', '.ts', '.tsx', '.js', '.ipynb', '.html', '.css', '.cfg', '.ini', '.rst', '.tex'}
    for record in rows(root / 'catalog/repositories.jsonl'):
        if record.get('scope') != 'official_org' or not record.get('revision'):
            continue
        revision = record['revision']
        url = f'https://codeload.github.com/{record["name"]}/zip/{revision}'
        result = attempt(root, 'repository_snapshot', url, lambda: fetch.get(url, limit=160_000_000))
        if not result:
            continue
        raw, meta = result
        archive = root / 'private/repository-archives' / (record['name'].split('/')[-1] + '-' + revision[:12] + '.zip')
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(raw)
        destination = root / 'private/repository-source' / record['name'].split('/')[-1]
        files = []
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            for member in z.infolist():
                p = PurePosixPath(member.filename)
                relative = PurePosixPath(*p.parts[1:])
                if member.is_dir() or '..' in p.parts or p.is_absolute() or ':' in str(relative) or '\\' in str(relative):
                    continue
                if member.file_size > 2_000_000 or (relative.suffix.lower() not in allowed and relative.name.lower() not in {'license', 'dockerfile', 'makefile'}):
                    continue
                target = destination.joinpath(*relative.parts)
                if not target.resolve().is_relative_to(destination.resolve()):
                    raise ValueError('Archive path traversal')
                data = z.read(member)
                if b'\0' in data[:1000]:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                files.append({'path': relative.as_posix(), 'sha256': digest(data), 'bytes': len(data)})
                if relative.name.lower().startswith('readme') and len(relative.parts) == 1:
                    import re
                    for link in re.findall(r'https://arxiv.org/(?:abs|pdf)/[0-9.]+(?:v\d+)?', data.decode('utf8', errors='replace')):
                        paper = paper_url(link.rstrip('.'))
                        if paper:
                            existing = next((r for r in rows(root / 'catalog/papers.jsonl') if r['url'] == paper), {})
                            upsert(root / 'catalog/papers.jsonl', [{'url': paper, 'relationship': existing.get('relationship', 'official_repository_link'), 'repository': record['url']}])
        from .core import write_json
        manifest = root / 'sources/repositories' / (record['name'].split('/')[-1] + '.snapshot.json')
        write_json(manifest, {'repository': record['url'], 'revision': revision, 'archive_sha256': digest(raw), 'source_files': files})
        upsert(root / 'catalog/repositories.jsonl', [{'url': record['url'], 'archive_path': archive.relative_to(root).as_posix(),
               'archive_sha256': digest(raw), 'snapshot_manifest': manifest.relative_to(root).as_posix(),
               'source_path': destination.relative_to(root).as_posix(), 'indexed_source_files': len(files), 'snapshot_status': 'stored'}])
        print(f'Snapshot: {record["name"]}: {len(files)} source files', flush=True)
