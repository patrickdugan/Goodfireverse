from __future__ import annotations

import base64
import io
import json
import re
import urllib.parse
from pathlib import Path

from .core import (Fetcher, article, attempt, canonical, digest, links, now,
                   publication, rows, save_text, sitemap, title, upsert, write_json)

HOME = 'https://www.goodfire.com'


def discover(root, fetch):
    discovered = {}
    for category in ('blog', 'research'):
        url = f'{HOME}/{category}'
        result = attempt(root, 'official_index', url, lambda: fetch.html(url))
        if result:
            soup, meta = result
            for u, label in links(soup, url).items():
                if u.startswith(f'{HOME}/{category}/'):
                    discovered[u] = {'url': u, 'kind': category, 'index_label': label,
                                     'discovered_from': url, 'discovered_at': meta['retrieved_at']}
    sitemap_url = f'{HOME}/sitemap.xml'
    all_urls = attempt(root, 'official_sitemap', sitemap_url, lambda: sitemap(fetch, sitemap_url)) or []
    for u in all_urls:
        u = canonical(u)
        if re.search(r'goodfire.com/(blog|research|papers)/.+', u):
            discovered.setdefault(u, {'url': u, 'kind': 'blog' if '/blog/' in u else 'research',
                                      'discovered_from': sitemap_url, 'discovered_at': now()})
    upsert(root / 'catalog/publications.jsonl', list(discovered.values()))
    write_json(root / 'catalog/official-index.json', {'urls': sorted(set(map(canonical, all_urls))),
               'indexed_publications': len(discovered), 'source': sitemap_url, 'checked_at': now()})
    print(f'Official publication URLs: {len(discovered)}', flush=True)


def paper_url(url):
    p = urllib.parse.urlsplit(url)
    if p.hostname == 'doi.org' and p.path.lower().startswith('/10.48550/arxiv.'):
        return 'https://arxiv.org/abs/' + p.path.split('arXiv.')[-1].split('arxiv.')[-1]
    if p.hostname in {'www.biorxiv.org', 'biorxiv.org', 'www.medrxiv.org', 'medrxiv.org'} and p.path.startswith('/content/'):
        path = re.sub(r'v\d+(?:\.full(?:\.pdf)?)?$', '', p.path)
        return 'https://' + ('www.medrxiv.org' if 'medrxiv' in p.hostname else 'www.biorxiv.org') + path
    if p.hostname in {'www.nature.com', 'nature.com'} and p.path.startswith('/articles/'):
        return 'https://www.nature.com' + p.path.removesuffix('.pdf')
    if p.hostname == 'doi.org':
        return url
    if p.hostname == 'zenodo.org' and re.fullmatch(r'/records/\d+', p.path):
        return url
    if p.hostname in {'arxiv.org', 'www.arxiv.org', 'export.arxiv.org'}:
        match = re.search(r'/(?:abs|pdf|html)/(\d{4}\.\d{4,5}(?:v\d+)?)', p.path)
        if match:
            return 'https://arxiv.org/abs/' + re.sub(r'v\d+$', '', match.group(1))
    if p.hostname == 'openreview.net' and p.path in {'/forum', '/pdf'}:
        return 'https://openreview.net/forum?' + p.query
    if p.path.lower().endswith('.pdf'):
        return url
    return None


def topic_tags(text):
    taxonomy = {
        'sparse_autoencoders': ['autoencoder', 'sae ', 'saes'],
        'neural_geometry': ['manifold', 'geometr', 'block-sparse'],
        'parameter_decomposition': ['parameter decomposition', 'parameters', 'loss curvature'],
        'intentional_design': ['intentional design', 'data debugging', 'features as rewards'],
        'monitoring_and_alignment': ['monitor', 'alignment', 'reward hack', 'eval awareness'],
        'biology': ['genom', 'evo 2', 'alzheimer', 'biosecurity', 'genetic variants'],
        'robotics_and_materials': ['robot', 'materials'],
        'interpretability_agents': ['agent', 'silico'],
    }
    return [k for k, words in taxonomy.items() if any(w in text.lower() for w in words)]


def collect_publications(root, fetch):
    for record in rows(root / 'catalog/publications.jsonl'):
        url = record['url']
        result = attempt(root, 'publication', url, lambda: fetch.html(url))
        if not result:
            continue
        soup, meta = result
        text = article(soup)
        found = links(soup, meta['final_url'])
        h = title(soup)
        description = soup.select_one('meta[name="description"]')
        content = save_text(root, 'private/publications', url, text, meta)
        updated = {**record, **content, 'title': h, 'published': publication(soup),
                   'final_url': meta['final_url'], 'topics': topic_tags(h + ' ' + text[:3000]),
                   'description': description.get('content') if description else None,
                   'status': 'text_stored', 'outlinks': sorted(found),
                   'rights': 'Original publisher copyright; local research copy; redistribution not established'}
        upsert(root / 'catalog/publications.jsonl', [updated])
        papers, repos, videos, related = [], [], [], []
        for target, label in found.items():
            paper = paper_url(target)
            if paper:
                old = next((r for r in rows(root / 'catalog/papers.jsonl') if r['url'] == paper), {})
                papers.append({'url': paper, 'label': label, 'linked_from': sorted(set(old.get('linked_from', []) + [url])),
                               'relationship': old.get('relationship', 'linked_reference_authorship_unreviewed')})
            if re.match(r'https://github.com/[^/]+/[^/]+', target):
                parts = urllib.parse.urlsplit(target).path.strip('/').split('/')
                repos.append({'url': f'https://github.com/{parts[0]}/{parts[1]}', 'discovered_from': url,
                              'scope': 'official_org' if parts[0].lower() == 'goodfire-ai' else 'linked_collaboration_or_reference'})
            if target.startswith('https://www.youtube.com/watch?v='):
                videos.append({'url': target, 'title_hint': label, 'discovered_from': url,
                               'participation': 'review_required', 'role': 'unknown'})
            host = urllib.parse.urlsplit(target).hostname or ''
            if host.endswith('goodfire.com') and host != 'www.goodfire.com' and urllib.parse.urlsplit(target).path in {'/', '/index.html'}:
                related.append({'url': target, 'discovered_from': url, 'kind': 'official_microsite'})
        for name, records in [('papers', papers), ('repositories', repos), ('appearances', videos), ('related-sites', related)]:
            if name == 'appearances':
                previous = {r['url']: r for r in rows(root / 'catalog/appearances.jsonl')}
                for discovered in records:
                    old = previous.get(discovered['url'], {})
                    for field in ['participation', 'role', 'title_hint']:
                        if field in old:
                            discovered[field] = old[field]
            upsert(root / f'catalog/{name}.jsonl', records)
        print(f'{record["kind"]}: {h}', flush=True)


def collect_microsites(root, fetch):
    for record in rows(root / 'catalog/related-sites.jsonl'):
        if urllib.parse.urlsplit(record['url']).path not in {'/', '/index.html'}:
            continue
        if record['url'].split('/')[2] not in {'already-clean.goodfire.com', 'world.goodfire.com', 'geometry.goodfire.com'}:
            continue
        result = attempt(root, 'microsite', record['url'], lambda: fetch.html(record['url']))
        if result:
            soup, meta = result
            data = {**record, **save_text(root, 'private/publications', record['url'], article(soup), meta),
                    'title': title(soup), 'status': 'text_stored', 'outlinks': sorted(links(soup, record['url']))}
            upsert(root / 'catalog/related-sites.jsonl', [data])


def collect_repos(root, fetch):
    page = 'https://api.github.com/orgs/goodfire-ai/repos?per_page=100&type=public'
    listing = []
    while page:
        result = attempt(root, 'github_org', page, lambda: fetch.get(page))
        if not result:
            break
        raw, meta = result
        listing.extend(json.loads(raw))
        next_link = re.search(r'<([^>]+)>; rel="next"', meta.get('link_header') or '')
        page = next_link.group(1) if next_link else None
    for repo in listing:
        url = repo['html_url']
        record = {'url': url, 'name': repo['full_name'], 'scope': 'official_org',
                  'description': repo['description'], 'fork': repo['fork'], 'archived': repo['archived'],
                  'default_branch': repo['default_branch'], 'license': repo.get('license'),
                  'language': repo['language'], 'created_at': repo['created_at'], 'pushed_at': repo['pushed_at'],
                  'size_kb': repo['size'], 'topics': repo.get('topics', []), 'retrieved_at': now()}
        upsert(root / 'catalog/repositories.jsonl', [record])
        api = 'https://api.github.com/repos/' + repo['full_name']
        commit = attempt(root, 'github_revision', url, lambda: fetch.get(f'{api}/commits/{repo["default_branch"]}'))
        if commit:
            sha = json.loads(commit[0])['sha']
            record['revision'] = sha
            tree = attempt(root, 'github_tree', f'{url}/tree/{sha}', lambda: fetch.get(f'{api}/git/trees/{sha}?recursive=1'))
            if tree:
                tree_json = json.loads(tree[0])
                tree_path = root / 'sources/repositories' / (repo['name'] + '.tree.json')
                write_json(tree_path, {'repository': url, 'revision': sha, 'truncated': tree_json.get('truncated'), 'tree': tree_json.get('tree', [])})
                record['tree_path'] = tree_path.relative_to(root).as_posix()
                record['tree_truncated'] = tree_json.get('truncated', False)
                readmes = [t['path'] for t in tree_json.get('tree', []) if '/' not in t['path'] and t['path'].lower().startswith('readme')]
                for filename in readmes[:1]:
                    raw_url = f'https://raw.githubusercontent.com/{repo["full_name"]}/{sha}/{urllib.parse.quote(filename)}'
                    result = attempt(root, 'github_readme', raw_url, lambda: fetch.get(raw_url))
                    if result:
                        raw, meta = result
                        record.update(save_text(root, 'private/repositories', url, raw.decode('utf8', errors='replace'), meta))
                        record['status'] = 'readme_and_revision_stored'
        upsert(root / 'catalog/repositories.jsonl', [record])
        print(f'Repo: {repo["full_name"]}', flush=True)


def collect_papers(root, fetch):
    from pypdf import PdfReader
    reviews = root / 'config/paper-reviews.json'
    if reviews.exists():
        upsert(root / 'catalog/papers.jsonl', json.loads(reviews.read_text(encoding='utf8')))
    for record in rows(root / 'catalog/papers.jsonl'):
        if record.get('relationship') not in {'official_research_target', 'official_repository_link'}:
            continue
        url = record['url']
        result = attempt(root, 'paper', url, lambda: fetch.get(url))
        if not result:
            continue
        raw, meta = result
        if raw.startswith(b'%PDF'):
            text = '\n\n'.join(p.extract_text() or '' for p in PdfReader(io.BytesIO(raw)).pages)
            updated = {**record, **save_text(root, 'private/papers', url, text, meta), 'status': 'pdf_text_stored'}
        else:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(raw, 'html.parser')
            if any(s in title(soup).lower() for s in ['client challenge', 'just a moment', 'access denied']):
                upsert(root / 'catalog/papers.jsonl', [{'url': url, 'title': None, 'status': 'publisher_access_challenge',
                       'access_note': 'HTTP 200 returned a browser challenge instead of paper content; no bypass attempted'}])
                upsert(root / 'catalog/acquisition.jsonl', [{'url': url, 'kind': 'paper', 'status': 'failed', 'checked_at': now(), 'error': 'Publisher browser challenge; paper content not acquired'}])
                continue
            def m(name):
                t = soup.select_one(f'meta[name="{name}"]')
                return t.get('content') if t else None
            authors = [a['content'] for a in soup.select('meta[name="citation_author"]')]
            updated = {**record, 'title': m('citation_title') or title(soup), 'authors': authors,
                       'published': m('citation_date') or publication(soup),
                       'pdf_url': m('citation_pdf_url'), 'status': 'metadata_stored',
                       'retrieved_at': meta['retrieved_at'], 'response_sha256': meta['sha256']}
            if 'arxiv.org' in url:
                block = soup.select_one('.abstract')
                updated['abstract'] = block.get_text(' ', strip=True) if block else None
                lic = soup.select_one('a[rel="license"]')
                updated['license_url'] = lic.get('href') if lic else None
                arxiv_id = url.rsplit('/', 1)[-1]
                html_url = f'https://arxiv.org/html/{arxiv_id}'
                html_result = attempt(root, 'paper_html', html_url, lambda: fetch.html(html_url))
                if html_result:
                    html_soup, html_meta = html_result
                    text = article(html_soup)
                    updated.update(save_text(root, 'private/papers', url, text, html_meta))
                    updated['status'] = 'html_text_stored'
                elif updated.get('pdf_url'):
                    pdf_url = updated['pdf_url'].replace('http://', 'https://')
                    pdf_result = attempt(root, 'paper_pdf', pdf_url, lambda: fetch.get(pdf_url))
                    if pdf_result:
                        pdf, pdf_meta = pdf_result
                        try:
                            text = '\n\n'.join(p.extract_text() or '' for p in PdfReader(io.BytesIO(pdf)).pages)
                            updated.update(save_text(root, 'private/papers', url, text, pdf_meta))
                            updated['status'] = 'pdf_text_stored'
                        except Exception as exc:
                            updated['extraction_error'] = str(exc)
            elif updated.get('pdf_url') or 'zenodo.org/records/' in url:
                candidates = [updated['pdf_url']] if updated.get('pdf_url') else [u for u in links(soup, url) if '.pdf' in u.lower() and '/files/' in u]
                if candidates:
                    pdf_url = candidates[0].replace('http://', 'https://')
                    pdf_result = attempt(root, 'paper_pdf', pdf_url, lambda: fetch.get(pdf_url))
                    if pdf_result:
                        pdf, pdf_meta = pdf_result
                        if pdf.startswith(b'%PDF'):
                            text = '\n\n'.join(p.extract_text() or '' for p in PdfReader(io.BytesIO(pdf)).pages)
                            updated.update(save_text(root, 'private/papers', url, text, pdf_meta))
                            updated['status'] = 'pdf_text_stored'
        upsert(root / 'catalog/papers.jsonl', [updated])
        print(f'Paper: {updated.get("title", url)} [{updated["status"]}]', flush=True)
