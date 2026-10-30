from __future__ import annotations

import html
import json
import re
import urllib.parse
from xml.etree import ElementTree as ET
from .core import (article, attempt, canonical, digest, links, now, publication,
                   rows, save_text, sitemap, title, upsert, write_json)


def publisher_transcript(soup):
    headings = soup.find_all(re.compile('^h[1-6]$'))
    heading = next((x for x in headings if x.get_text(' ', strip=True).lower() in {'transcript', 'full transcript'}), None)
    if heading is None:
        return None
    container = heading
    for _ in range(4):
        parts = []
        for tag in container.next_siblings:
            if getattr(tag, 'name', '') in {'footer', 'nav', 'script', 'style'}:
                break
            if hasattr(tag, 'get_text'):
                parts.append(tag.get_text('\n', strip=True))
        text = '\n\n'.join(p for p in parts if p)
        if len(text) > 5000:
            return text
        if not container.parent:
            break
        container = container.parent
    return None


def seed_media(root):
    seeds = json.loads((root / 'config/media-seeds.json').read_text(encoding='utf8'))
    for row in seeds:
        row['url'] = canonical(row['url'])
        row.setdefault('discovered_from', row['url'])
    upsert(root / 'catalog/appearances.jsonl', seeds)


def collect_media(root, fetch, limit=100):
    seed_media(root)
    config = json.loads((root / 'config/sources.json').read_text(encoding='utf8'))
    for index in config['publisher_sitemaps']:
        urls = attempt(root, 'publisher_index', index, lambda: sitemap(fetch, index)) or []
        candidates = [{'url': canonical(u), 'discovered_from': index, 'participation': 'review_required', 'role': 'interview'}
                      for u in urls if any(t in u.lower() for t in ['goodfire', 'eric-bigelow', 'dan-balsam', 'tom-mcgrath'])]
        # Preserve explicit review decisions from the seed list.
        existing = {r['url'] for r in rows(root / 'catalog/appearances.jsonl')}
        upsert(root / 'catalog/appearances.jsonl', [r for r in candidates if r['url'] not in existing])
    count = 0
    for record in rows(root / 'catalog/appearances.jsonl'):
        url = record['url']
        if 'youtube.com/watch?' in url or count >= limit or record.get('participation') in {'out_of_scope', 'superseded', 'commentary_or_narration'}:
            continue
        if record.get('role') == 'podcast' and record.get('participation') != 'confirmed':
            continue
        count += 1
        result = attempt(root, 'appearance_page', url, lambda: fetch.html(url))
        if not result:
            continue
        soup, meta = result
        text = article(soup)
        linked = links(soup, url)
        h = title(soup)
        updated = {**record, 'title': h, 'published': publication(soup), 'outlinks': sorted(linked),
                   'page_status': 'stored', **save_text(root, 'private/media-pages', url, text, meta)}
        # Presence of a native transcript heading is distinct from show notes.
        transcript = publisher_transcript(soup)
        if transcript:
            if len(transcript) > 5000:
                transcript_row = {'url': url, 'kind': 'publisher_transcript', 'quality': 'publisher_supplied_unverified',
                                  'speaker_diarization': 'publisher_labels_only',
                                  **save_text(root, 'private/transcripts', url, transcript, meta)}
                upsert(root / 'catalog/transcripts.jsonl', [transcript_row])
                updated['transcript_status'] = 'publisher_transcript_stored'
        if 'goodfire' in h.lower() and record.get('role') == 'interview' and 'cognitiverevolution.ai' in url:
            updated['participation'] = 'confirmed'
            updated['participation_basis'] = 'Publisher episode title explicitly identifies Goodfire guest'
        upsert(root / 'catalog/appearances.jsonl', [updated])
        # An embedded episode video is linked to its publisher page, not counted as a new event.
        video_urls = [u for u in linked if u.startswith('https://www.youtube.com/watch?v=')]
        embedded_videos = {canonical(t.get('src', ''), url) for t in soup.select('iframe[src]')
                           if 'youtube' in t.get('src', '')}
        for video in video_urls:
            primary = (len(video_urls) == 1 or (video in embedded_videos and len(embedded_videos) == 1)) and any(host in url for host in ['cognitiverevolution.ai', 'latent.space', 'mattturck.com', 'sequoiacap.com'])
            data = {'url': video, 'discovered_from': url, 'title_hint': h if primary else linked[video],
                    'participation': updated.get('participation') if primary else 'review_required',
                    'role': updated.get('role', 'unknown') if primary else 'unknown',
                    'publisher_page': url if primary else None, 'event_link': 'publisher_embed' if primary else 'unreviewed_outlink'}
            if primary and updated.get('transcript_status'):
                data['transcript_status'] = 'publisher_version_available'
                data['transcript_source'] = url
                data['version_comparison'] = 'not_compared_audio_and_video_edits_may_differ'
            upsert(root / 'catalog/appearances.jsonl', [data])
        for tag in soup.select('link[type="application/rss+xml"], link[type="application/atom+xml"]'):
            feed = canonical(tag.get('href', ''), url)
            if 'cognitiverevolution.ai' not in feed:  # Ghost post RSS is not necessarily the podcast audio feed.
                upsert(root / 'catalog/feeds.jsonl', [{'url': feed, 'discovered_from': url}])
        print(f'Media page: {h}', flush=True)
    from .review import apply_reviews
    apply_reviews(root)


def directory(root, fetch):
    config = json.loads((root / 'config/sources.json').read_text(encoding='utf8'))
    for term in config['podcast_search_terms']:
        url = 'https://itunes.apple.com/search?' + urllib.parse.urlencode({'term': term, 'entity': 'podcastEpisode', 'limit': 200, 'country': 'US'})
        result = attempt(root, 'apple_directory', url, lambda: fetch.get(url))
        if not result:
            continue
        found = json.loads(result[0]).get('results', [])
        for item in found:
            text = item.get('trackName', '') + ' ' + item.get('description', '')
            if 'goodfire' not in text.lower():
                continue
            episode = item.get('episodeUrl') or item.get('trackViewUrl')
            if not episode:
                continue
            row = {'url': canonical(item.get('trackViewUrl') or episode), 'title': item.get('trackName'),
                   'publisher': item.get('collectionName'), 'published': item.get('releaseDate'),
                   'duration_ms': item.get('trackTimeMillis'), 'audio_url': item.get('episodeUrl'),
                   'feed_url': item.get('feedUrl'), 'description': item.get('description'),
                   'participation': 'review_required', 'discovered_from': url,
                   'review_reason': 'Directory search mention is not proof of guest participation'}
            upsert(root / 'catalog/directory-candidates.jsonl', [row])
            if row['feed_url']:
                upsert(root / 'catalog/feeds.jsonl', [{'url': row['feed_url'], 'discovered_from': row['url']}])


def collect_feeds(root, fetch, limit=100):
    directory(root, fetch)
    ns = {'podcast': 'https://podcastindex.org/namespace/1.0', 'itunes': 'http://www.itunes.com/dtds/podcast-1.0.dtd'}
    names = ['tom mcgrath', 'thomas mcgrath', 'dan balsam', 'daniel balsam', 'eric ho', 'myra deng', 'mark bissell', 'eric bigelow', 'atticus geiger', 'ekdeep', 'nick cammarata']
    for feed in rows(root / 'catalog/feeds.jsonl')[:limit]:
        url = feed['url'].replace('http://', 'https://')
        result = attempt(root, 'podcast_feed', url, lambda: fetch.get(url))
        if not result:
            continue
        raw, meta = result
        tree = ET.fromstring(raw)
        for item in tree.findall('.//item'):
            h = item.findtext('title', '')
            description = html.unescape(item.findtext('description', ''))
            if 'goodfire' not in (h + ' ' + description).lower() and not any(n in h.lower() for n in names):
                continue
            enclosure = item.find('enclosure')
            audio_url = enclosure.get('url') if enclosure is not None else None
            source = item.findtext('link') or item.findtext('guid') or audio_url
            if not source or not source.startswith('http'):
                continue
            if urllib.parse.urlsplit(source).path in {'', '/'}:
                same_title = [r['url'] for r in rows(root / 'catalog/appearances.jsonl') if r.get('title') == h and urllib.parse.urlsplit(r['url']).path not in {'', '/'}]
                directory_matches = [r['url'] for r in rows(root / 'catalog/directory-candidates.jsonl') if r.get('title') == h]
                source = (same_title or directory_matches or [audio_url])[0]
                if not source:
                    continue
            source = canonical(source)
            matched = [n for n in names if n in h.lower()]
            is_other_entity = any(w in (h + ' ' + description).lower() for w in ['goodfire brewing', 'goodfires &', 'lead of goodfires'])
            commentary = any(w in h.lower() for w in ['highlights', 'weekly', 'by nick cammarata', 'news roundup'])
            confirmed = bool(matched) and not is_other_entity and not commentary
            previous = next((r for r in rows(root / 'catalog/appearances.jsonl') if r['url'] == source), {})
            if previous.get('participation') == 'confirmed' and not is_other_entity:
                confirmed = True
            row = {'url': source, 'title': h, 'published': item.findtext('pubDate'), 'feed_url': url,
                   'audio_url': audio_url, 'duration': item.findtext('itunes:duration', namespaces=ns),
                   'description': description, 'role': 'podcast', 'matched_names': matched,
                   'participation': 'out_of_scope' if is_other_entity else ('confirmed' if confirmed else 'review_required'),
                   'participation_basis': 'Publisher episode title identifies guest or Goodfire' if confirmed else 'Mention in description only',
                   'discovered_from': url, 'retrieved_at': meta['retrieved_at']}
            upsert(root / 'catalog/appearances.jsonl', [row])
            for track in item.findall('podcast:transcript', ns):
                track_url = track.get('url', '').replace('http://', 'https://')
                upsert(root / 'catalog/transcript-tracks.jsonl', [{'url': track_url, 'appearance': source, 'type': track.get('type'), 'language': track.get('language')}])
        print(f'Podcast feed: {url}', flush=True)
    from .review import apply_reviews
    apply_reviews(root)


def collect_captions(root, fetch, limit=100):
    # Share the observed YouTube restriction with the existing corpus on this machine.
    state = fetch.state.get('www.youtube.com', {})
    if state.get('retry_after', '') > now():
        print(f'YouTube caption collection deferred until {state["retry_after"]}; availability remains unknown.')
    else:
        # yt-dlp metadata/caption collection uses no login cookies, proxies or access workarounds.
        import yt_dlp
        attempts = 0
        for record in rows(root / 'catalog/appearances.jsonl'):
            if attempts >= limit or 'youtube.com/watch?' not in record['url'] or record.get('transcript_status'):
                continue
            attempts += 1
            try:
                with yt_dlp.YoutubeDL({'skip_download': True, 'quiet': True, 'noplaylist': True, 'retries': 0, 'extractor_retries': 0}) as ydl:
                    info = ydl.extract_info(record['url'], download=False)
                upsert(root / 'catalog/appearances.jsonl', [{'url': record['url'], 'title': info.get('title'), 'duration_seconds': info.get('duration'),
                       'upload_date': info.get('upload_date'), 'channel': info.get('channel'),
                       'caption_languages': sorted(set(info.get('subtitles', {})) | set(info.get('automatic_captions', {}))),
                       'transcript_status': 'tracks_cataloged' if info.get('subtitles') or info.get('automatic_captions') else 'no_tracks_returned'}])
                tracks = info.get('subtitles', {}).get('en') or info.get('automatic_captions', {}).get('en') or []
                track = next((t for t in tracks if t.get('ext') == 'json3'), None)
                if track:
                    store_track(root, fetch, {'url': track['url'], 'appearance': record['url'], 'type': 'youtube_json3'})
            except Exception as exc:
                from datetime import datetime, timezone, timedelta
                fetch.state['www.youtube.com'] = {'retry_after': (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(), 'reason': str(exc)[:250]}
                write_json(root / 'catalog/provider-state.json', fetch.state)
                print(f'YouTube stopped after failure: {exc}')
                break
    for track in rows(root / 'catalog/transcript-tracks.jsonl')[:limit]:
        attempt(root, 'transcript_track', track['url'], lambda: store_track(root, fetch, track))


def store_track(root, fetch, track):
    raw, meta = fetch.get(track['url'])
    kind = track.get('type', '') or ''
    segments = []
    if kind == 'youtube_json3':
        for e in json.loads(raw).get('events', []):
            text = ''.join(s.get('utf8', '') for s in e.get('segs', [])).strip()
            if text:
                segments.append({'start': e.get('tStartMs', 0) / 1000, 'end': (e.get('tStartMs', 0) + e.get('dDurationMs', 0)) / 1000, 'text': text, 'speaker': None})
        text = '\n'.join(s['text'] for s in segments)
    elif 'json' in kind:
        data = json.loads(raw)
        segments = data.get('segments', []) if isinstance(data, dict) else data
        text = '\n'.join(str(s.get('body', s.get('text', ''))) for s in segments)
    elif 'html' in kind:
        from bs4 import BeautifulSoup
        text = article(BeautifulSoup(raw, 'html.parser', from_encoding='utf8'))
    else:
        text = raw.decode('utf8', errors='replace')
    if len(text.strip()) < 300:
        raise ValueError('Transcript track is empty or too short')
    data = {'url': track['appearance'], 'track_url': track['url'].split('?')[0], 'kind': 'native_caption_track', 'quality': 'unverified',
            **save_text(root, 'private/transcripts', track['appearance'], text, meta)}
    if segments:
        path = root / 'private/transcripts' / (digest(track['appearance'])[:16] + '.json')
        write_json(path, {'source_url': track['appearance'], 'segments': segments, 'quality': 'unverified', 'speaker_diarization': 'not_performed'})
        data['segments_path'] = path.relative_to(root).as_posix()
    upsert(root / 'catalog/transcripts.jsonl', [data])


def collect_audio(root, fetch, limit=100):
    from .transcribe import run_audio
    run_audio(root, fetch, limit)
