from __future__ import annotations

import hashlib
import gzip
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from datetime import datetime, timezone, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

import truststore
from bs4 import BeautifulSoup

truststore.inject_into_ssl()
AGENT = 'Goodfireverse/0.1 (public-source research catalog; conservative sequential requests)'


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode('utf8')).hexdigest()


def canonical(url, base='https://www.goodfire.com'):
    p = urllib.parse.urlsplit(urllib.parse.urljoin(base, url))
    host = (p.hostname or '').lower()
    if host in {'goodfire.ai', 'www.goodfire.ai', 'goodfire.com'}:
        host = 'www.goodfire.com'
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query) if not k.startswith('utm_') and k not in {'si', 'feature', 't'}]
    if host in {'youtu.be', 'www.youtube.com', 'youtube.com', 'm.youtube.com', 'www.youtube-nocookie.com'}:
        video = p.path.strip('/') if host == 'youtu.be' else dict(q).get('v')
        if not video and ('/embed/' in p.path or '/shorts/' in p.path):
            video = p.path.split('/')[2]
        if video and re.fullmatch(r'[\w-]{11}', video):
            return f'https://www.youtube.com/watch?v={video}'
    return urllib.parse.urlunsplit(('https', host, p.path.rstrip('/') or '/', urllib.parse.urlencode(q), ''))


def rows(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf8').splitlines() if line.strip()]


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    temp.replace(path)


def upsert(path, data, key='url'):
    old = {r[key]: r for r in rows(path)}
    for r in data:
        old[r[key]] = {**old.get(r[key], {}), **r}
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(''.join(json.dumps(old[k], ensure_ascii=False, sort_keys=True) + '\n' for k in sorted(old)), encoding='utf8')
    temp.replace(path)


class Fetcher:
    """Disk checkpoints, robots policy, host cooldown, size and request limits."""
    def __init__(self, root, delay=0.6, refresh=False):
        self.root, self.delay, self.refresh = Path(root), delay, refresh
        self.cache = self.root / '.cache/http'
        self.cache.mkdir(parents=True, exist_ok=True)
        state = self.root / 'catalog/provider-state.json'
        self.state = json.loads(state.read_text(encoding='utf8')) if state.exists() else {}
        self.robots = {}
        self.last = {}

    def get(self, url, limit=30_000_000, accept=None, robots=True):
        if not url.startswith('https://'):
            raise ValueError('Only HTTPS public sources are supported')
        token = digest(url)
        target = self.cache / (token + '.bin')
        meta = self.cache / (token + '.json')
        if target.exists() and meta.exists() and not self.refresh:
            data = target.read_bytes()
            if data.startswith(b'\x1f\x8b'):
                data = gzip.decompress(data)
            return data, json.loads(meta.read_text(encoding='utf8'))
        p = urllib.parse.urlsplit(url)
        host = p.netloc
        blocked = self.state.get(host, {}).get('retry_after')
        if blocked and blocked > now():
            raise RuntimeError(f'{host}: cooldown until {blocked}')
        if robots and host not in self.robots:
            try:
                content, _ = self.get(f'https://{host}/robots.txt', robots=False)
                rp = urllib.robotparser.RobotFileParser()
                rp.parse(content.decode('utf8', errors='replace').splitlines())
                self.robots[host] = rp
            except urllib.error.HTTPError as exc:
                if exc.code not in {404, 410}:
                    raise
                self.robots[host] = None
        if robots and self.robots.get(host) and not self.robots[host].can_fetch(AGENT, url):
            raise PermissionError(f'robots.txt excludes {url}')
        time.sleep(max(0, self.delay - (time.monotonic() - self.last.get(host, 0))))
        headers = {'User-Agent': AGENT, 'Accept': accept or '*/*'}
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=45) as response:
                deadline = time.monotonic() + 120
                chunks, size = [], 0
                while size <= limit:
                    if time.monotonic() > deadline:
                        raise TimeoutError(f'Response exceeded the 120-second transfer budget: {url}')
                    chunk = response.read(min(65536, limit + 1 - size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                data = b''.join(chunks)
                if data.startswith(b'\x1f\x8b'):
                    data = gzip.decompress(data)
                if len(data) > limit:
                    raise ValueError(f'Response exceeds {limit} bytes: {url}')
                metadata = {'url': url, 'final_url': response.url, 'retrieved_at': now(),
                            'sha256': digest(data), 'bytes': len(data), 'content_type': response.headers.get('Content-Type'),
                            'link_header': response.headers.get('Link')}
        except urllib.error.HTTPError as exc:
            if exc.code in {403, 429}:
                self.state[host] = {'status': exc.code, 'retry_after': (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(), 'reason': str(exc)}
                write_json(self.root / 'catalog/provider-state.json', self.state)
            raise
        finally:
            self.last[host] = time.monotonic()
        target.write_bytes(data)
        write_json(meta, metadata)
        return data, metadata

    def html(self, url):
        raw, meta = self.get(url)
        return BeautifulSoup(raw, 'html.parser', from_encoding='utf8'), meta


def links(soup, base):
    found = {}
    for tag in soup.select('a[href], iframe[src]'):
        href = tag.get('href') or tag.get('src')
        if href and not href.startswith(('mailto:', 'javascript:', '#')):
            url = canonical(href, base)
            found[url] = tag.get_text(' ', strip=True)
    return found


def xml_urls(data):
    tree = ET.fromstring(data)
    return [x.text.strip() for x in tree.iter() if x.tag.rsplit('}', 1)[-1] == 'loc' and x.text]


def sitemap(fetcher, url):
    raw, _ = fetcher.get(url)
    urls = xml_urls(raw)
    if ET.fromstring(raw).tag.endswith('sitemapindex'):
        return [u for child in urls for u in sitemap(fetcher, child)]
    return urls


def article(soup):
    """Main content only; keep headings and source-provided speaker labels."""
    region = soup.select_one('article') or soup.select_one('main') or soup.body or soup
    region = BeautifulSoup(str(region), 'html.parser')
    for tag in region.select('script, style, nav, footer, header, form, button, noscript'):
        tag.decompose()
    return re.sub(r'\n{3,}', '\n\n', region.get_text('\n', strip=True)).strip()


def title(soup):
    citation = soup.select_one('meta[name="citation_title"]')
    if citation:
        return citation.get('content', '')
    og = soup.select_one('meta[property="og:title"]')
    if og and og.get('content'):
        return og['content']
    h1 = soup.find('h1')
    return h1.get_text(' ', strip=True) if h1 else (soup.title.get_text(' ', strip=True) if soup.title else '')


def publication(soup):
    for selector in ['meta[property="article:published_time"]', 'meta[name="citation_publication_date"]', 'time[datetime]']:
        tag = soup.select_one(selector)
        if tag:
            return tag.get('content') or tag.get('datetime')
    text = soup.get_text(' ', strip=True)
    m = re.search(r'(?:Published\s+|Blog\s*[·•]\s*)([A-Z][a-z]+ \d{1,2}, \d{4})', text)
    return m.group(1) if m else None


def save_text(root, folder, url, text, meta):
    p = root / folder / (digest(url)[:16] + '.txt')
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text + '\n', encoding='utf8')
    return {'text_path': p.relative_to(root).as_posix(), 'text_sha256': digest(p.read_bytes()),
            'characters': len(text), 'retrieved_at': meta['retrieved_at'], 'response_sha256': meta['sha256']}


def attempt(root, kind, url, operation):
    try:
        result = operation()
        upsert(root / 'catalog/acquisition.jsonl', [{'url': url, 'kind': kind, 'status': 'ok', 'checked_at': now()}])
        return result
    except Exception as exc:
        upsert(root / 'catalog/acquisition.jsonl', [{'url': url, 'kind': kind, 'status': 'failed', 'checked_at': now(), 'error': str(exc)[:500]}])
        print(f'FAILED {kind}: {url}: {exc}', flush=True)
        return None
