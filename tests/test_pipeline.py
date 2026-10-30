import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from bs4 import BeautifulSoup
from goodfireverse.core import Fetcher, canonical, digest, rows, title, upsert, write_json
from goodfireverse.collect import paper_url
from goodfireverse.media import publisher_transcript
from goodfireverse.report import validate, report
from goodfireverse.whisper import transcribe_media
from goodfireverse.transcribe import process_alive

SCRATCH = Path(__file__).resolve().parent.parent / '.cache/tests'
SCRATCH.mkdir(parents=True, exist_ok=True)


class PipelineTests(unittest.TestCase):
    def test_worker_pid_check_recognizes_current_process(self):
        import os
        self.assertTrue(process_alive(os.getpid()))
    def test_upload_identity_preserves_video_and_discards_tracking(self):
        self.assertEqual(canonical('https://youtu.be/_egu7OFem-k?t=30&si=tracking'), 'https://www.youtube.com/watch?v=_egu7OFem-k')
        self.assertEqual(canonical('https://www.youtube-nocookie.com/embed/_egu7OFem-k'), 'https://www.youtube.com/watch?v=_egu7OFem-k')

    def test_old_domain_and_slash_deduplicate(self):
        self.assertEqual(canonical('https://goodfire.ai/blog/example/?utm_source=x'), 'https://www.goodfire.com/blog/example')

    def test_distinct_apple_episodes_do_not_collapse(self):
        self.assertNotEqual(canonical('https://podcasts.apple.com/us/podcast/show/id1?i=100'), canonical('https://podcasts.apple.com/us/podcast/show/id1?i=101'))

    def test_rediscovery_preserves_acquired_text(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            p = Path(directory) / 'items.jsonl'
            upsert(p, [{'url': 'https://example.org', 'text_path': 'private/text.txt', 'status': 'stored'}])
            upsert(p, [{'url': 'https://example.org', 'label': 'new discovery'}])
            self.assertEqual(len(rows(p)), 1)
            self.assertEqual(rows(p)[0]['status'], 'stored')

    def test_versioned_paper_links_share_work_identity(self):
        self.assertEqual(paper_url('https://arxiv.org/html/2506.20790v2'), 'https://arxiv.org/abs/2506.20790')
        self.assertEqual(paper_url('https://arxiv.org/pdf/2506.20790'), 'https://arxiv.org/abs/2506.20790')
        self.assertEqual(paper_url('https://doi.org/10.48550/arXiv.2506.20790'), 'https://arxiv.org/abs/2506.20790')
        self.assertEqual(paper_url('https://www.biorxiv.org/content/10.1101/2025.02.18.638918v1.full.pdf'), 'https://www.biorxiv.org/content/10.1101/2025.02.18.638918')

    def test_citation_title_overrides_arxiv_subject_heading(self):
        soup = BeautifulSoup('<meta name="citation_title" content="Actual paper"><h1>Computer Science</h1>', 'html.parser')
        self.assertEqual(title(soup), 'Actual paper')

    def test_nested_publisher_heading_does_not_lose_transcript(self):
        text = 'Guest: This is a transcript paragraph. ' * 200
        soup = BeautifulSoup(f'<main><p>Show notes</p><section><div><h2>Transcript</h2></div><div><p>{text}</p></div></section><footer>Unrelated footer</footer></main>', 'html.parser')
        found = publisher_transcript(soup)
        self.assertIn('Guest:', found)
        self.assertNotIn('Show notes', found)
        self.assertNotIn('Unrelated footer', found)

    def test_headings_do_not_turn_short_show_notes_into_transcript(self):
        soup = BeautifulSoup('<article><h2>Transcript</h2><p>Coming soon.</p></article>', 'html.parser')
        self.assertIsNone(publisher_transcript(soup))

    def test_host_cooldown_prevents_network_retry(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            root = Path(directory)
            write_json(root / 'catalog/provider-state.json', {'example.org': {'retry_after': '2099-01-01T00:00:00+00:00'}})
            with patch('urllib.request.urlopen', side_effect=AssertionError('Must not retry during cooldown')):
                with self.assertRaisesRegex(RuntimeError, 'cooldown'):
                    Fetcher(root).get('https://example.org/data')

    def test_whisper_rejects_unpermitted_media_before_model_load(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            root = Path(directory)
            media = root / 'media/permitted/sample.wav'
            media.parent.mkdir(parents=True)
            media.write_bytes(b'fixture')
            with self.assertRaises(PermissionError):
                transcribe_media(media, root, {'permission_confirmed': False})
            outside = root / 'outside.wav'
            outside.write_bytes(b'fixture')
            with self.assertRaises(ValueError):
                transcribe_media(outside, root, {'permission_confirmed': True, 'permission_basis': 'fixture'})

    def test_validation_detects_corruption(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            root = Path(directory)
            p = root / 'private/record.txt'
            p.parent.mkdir(); p.write_text('original', encoding='utf8')
            upsert(root / 'catalog/transcripts.jsonl', [{'url': 'https://example.org/episode', 'text_path': 'private/record.txt', 'text_sha256': digest(p.read_bytes())}])
            validate(root)
            p.write_text('corrupted', encoding='utf8')
            with self.assertRaises(SystemExit): validate(root)

    def test_report_requires_actual_local_artifacts_after_clone(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            root = Path(directory)
            upsert(root / 'catalog/papers.jsonl', [{'url': 'https://example.org/paper', 'relationship': 'official_research_target', 'text_path': 'private/missing.txt'}])
            upsert(root / 'catalog/repositories.jsonl', [{'url': 'https://github.com/example/repo', 'scope': 'official_org', 'snapshot_status': 'stored', 'archive_path': 'private/missing.zip'}])
            upsert(root / 'catalog/appearances.jsonl', [{'url': 'https://example.org/episode', 'participation': 'confirmed'}])
            upsert(root / 'catalog/transcripts.jsonl', [{'url': 'https://example.org/episode', 'text_path': 'private/missing.txt', 'text_sha256': 'unavailable'}])
            report(root)
            result = json.loads((root / 'reports/coverage.json').read_text(encoding='utf8'))
            self.assertEqual(result['repository_snapshots'], 0)
            self.assertEqual(result['primary_paper_texts_stored'], 0)
            self.assertEqual(result['confirmed_transcript_source_records'], 0)


if __name__ == '__main__': unittest.main()
