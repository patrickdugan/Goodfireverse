"""Repair metadata from cached sources and identify directly linked research artifacts."""
import json
import re
import urllib.parse
from .core import article, canonical, digest, links, rows, save_text, title, upsert
from .collect import paper_url


def enrich(root, fetch):
    records = rows(root / 'catalog/publications.jsonl')
    for record in records:
        soup, meta = fetch.html(record['url'])
        h = title(soup)
        if h.isdigit() or len(h) < 10:
            # The official index gives the title even when a linked site's H1 is a vote count.
            label = record.get('index_label', '')
            m = re.search(r'((?:Logits as|Predicting Rare).+?)(?:Santiago|Aranguri)', label)
            h = m.group(1).strip() if m else h
        update = {'url': record['url'], 'title': h}
        text = article(soup)
        update.update(save_text(root, 'private/publications', record['url'], text, meta))
        if meta['final_url'].split('/')[2] != 'www.goodfire.com':
            update['content_class'] = 'linked_paper_landing_page' if 'arxiv.org/abs/' in meta['final_url'] else 'external_link_post'
        else:
            update['content_class'] = 'official_article'
        # Read original author labels without guessing employment from a bibliography.
        strings = list(soup.stripped_strings)
        if 'Authors' in strings or 'Author' in strings:
            start = strings.index('Authors') if 'Authors' in strings else strings.index('Author')
            if 'Published' in strings[start:]:
                end = strings.index('Published', start)
                update['author_labels'] = strings[start+1:end]
        upsert(root / 'catalog/publications.jsonl', [update])
        primary = paper_url(meta['final_url'])
        if primary:
            upsert(root / 'catalog/papers.jsonl', [{'url': primary, 'relationship': 'official_research_target', 'official_entry': record['url']}])
        for u, label in links(soup, meta['final_url']).items():
            p = paper_url(u)
            if p and (label.lower().strip() in {'paper', 'full paper', 'the full paper', 'read the full paper →', 'read the full paper', 'read the paper', 'read the paper →', 'the preprint', 'published in nature', '10.5281/zenodo.14895891'}):
                upsert(root / 'catalog/papers.jsonl', [{'url': p, 'relationship': 'official_research_target', 'official_entry': record['url']}])
    # URLs returned by different publishers may differ only in a trailing slash.
    path = root / 'catalog/appearances.jsonl'
    all_records = rows(path)
    merged = {}
    for r in all_records:
        u = canonical(r['url'])
        old = merged.get(u, {})
        merged[u] = {**old, **r, 'url': u}
        if old.get('participation') == 'confirmed':
            merged[u]['participation'] = 'confirmed'
    from .core import write_json
    path.write_text('', encoding='utf8')
    upsert(path, list(merged.values()))
    transcripts = root / 'catalog/transcripts.jsonl'
    old = rows(transcripts)
    for r in old: r['url'] = canonical(r['url'])
    transcripts.write_text('', encoding='utf8')
    upsert(transcripts, old)
    paper_reviews = root / 'config/paper-reviews.json'
    if paper_reviews.exists():
        upsert(root / 'catalog/papers.jsonl', json.loads(paper_reviews.read_text(encoding='utf8')))
    from .review import apply_reviews
    apply_reviews(root)
