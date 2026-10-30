"""Local public-podcast ASR with a cross-corpus GPU lock and no cloud service."""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path
from .core import attempt, digest, now, rows, save_text, upsert, write_json


def process_alive(pid):
    if os.name == 'nt':
        import ctypes
        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False


def normalized(text):
    import re
    return re.sub(r'\W+', '', text.lower())


def run_audio(root, fetch, limit=100):
    other_root = os.environ.get('GOODFIREVERSE_LEVINVERSE_ROOT')
    if other_root:
        manifest = Path(other_root) / 'reports/audio-worker.json'
        if manifest.exists():
            worker = json.loads(manifest.read_text(encoding='utf8'))
            if worker.get('status') == 'running' and worker.get('pid') and process_alive(worker['pid']):
                raise RuntimeError('An existing Levinverse worker is already using the GPU')
    # Lock lives outside either corpus, so two Goodfireverse instances cannot overlap.
    lock_path = Path(os.environ.get('GOODFIREVERSE_GPU_LOCK', str(root.parent.parent / 'work/gpu-transcription.lock')))
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open('a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            lock_file.seek(0)
            lock_file.write(b'0'); lock_file.flush(); lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_file.close()
        raise RuntimeError('Another local ASR worker holds the GPU lock')
    dll_handles = []
    done = 0
    try:
        if os.name == 'nt':
            for parent in map(Path, sys.path):
                for folder in (parent / 'nvidia/cublas/bin', parent / 'nvidia/cudnn/bin'):
                    if folder.is_dir():
                        dll_handles.append(os.add_dll_directory(str(folder.resolve())))
                        os.environ['PATH'] = str(folder.resolve()) + os.pathsep + os.environ.get('PATH', '')
        from .whisper import transcribe_media
        for record in rows(root / 'catalog/appearances.jsonl'):
            if done >= limit or record.get('participation') != 'confirmed' or not record.get('audio_url'):
                continue
            # Exact source URLs and identical publisher titles prevent avoidable retranscription.
            stored = rows(root / 'catalog/transcripts.jsonl')
            stored_urls = {r['url'] for r in stored}
            appearances = rows(root / 'catalog/appearances.jsonl')
            covered_titles = {normalized(r.get('title', '')) for r in appearances if r['url'] in stored_urls}
            if record['url'] in stored_urls or (normalized(record.get('title', '')) in covered_titles):
                continue
            if record.get('access') in {'paid', 'subscriber_only'}:
                continue
            url = record['url']
            audio_url = record['audio_url'].replace('http://', 'https://')
            media = attempt(root, 'public_audio', url, lambda: fetch.get(audio_url, limit=350_000_000))
            if not media:
                continue
            raw, meta = media
            media_hash = digest(raw)
            duplicate = next((r for r in stored if r.get('media_sha256') == media_hash), None)
            if duplicate:
                upsert(root / 'catalog/transcripts.jsonl', [{**duplicate, 'url': url, 'duplicate_of': duplicate['url']}])
                continue
            target = root / 'media/permitted' / (media_hash[:20] + '.audio')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            write_json(root / 'reports/audio-worker.json', {'pid': os.getpid(), 'status': 'running', 'current_url': url, 'completed': done, 'heartbeat_at': now()})
            print(f'Transcribing: {record.get("title", url)}', flush=True)
            basis = 'User requested Levinverse-style local public-source corpus; public publisher RSS enclosure; local research only'
            companion = transcribe_media(target, root, {'permission_confirmed': True, 'permission_basis': basis,
                                        'source_url': url, 'audio_url': audio_url.split('?')[0], 'media_sha256': media_hash})
            payload = json.loads(companion.read_text(encoding='utf8'))
            text_path = companion.with_suffix('.txt')
            content = {'text_path': text_path.relative_to(root).as_posix(), 'text_sha256': digest(text_path.read_bytes()),
                       'characters': len(text_path.read_text(encoding='utf8')), 'retrieved_at': meta['retrieved_at']}
            upsert(root / 'catalog/transcripts.jsonl', [{'url': url, **content, 'kind': 'local_whisper',
                   'media_sha256': media_hash, 'segments_path': companion.relative_to(root).as_posix(), 'duration_seconds': payload['duration_seconds'],
                   'quality': 'machine_ASR_unverified', 'permission_basis': 'User requested the same local public-source corpus workflow as Levinverse; public publisher RSS enclosure; local research storage only'}])
            done += 1
        write_json(root / 'reports/audio-worker.json', {'pid': os.getpid(), 'status': 'completed', 'completed': done, 'heartbeat_at': now()})
    except Exception:
        write_json(root / 'reports/audio-worker.json', {'pid': os.getpid(), 'status': 'failed', 'completed': done, 'heartbeat_at': now()})
        raise
    finally:
        lock_file.close()
