"""Optional local-only Whisper worker for media with documented processing permission."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def transcribe_media(media_path: Path, root: Path, permission_record: dict[str, Any], model_name: str | None = None) -> Path:
    root = root.resolve()
    media_path = media_path.resolve()
    permitted_dir = (root / "media/permitted").resolve()
    if not media_path.is_relative_to(permitted_dir) or not media_path.is_file():
        raise ValueError("Input media must be an existing file under media/permitted/.")
    if permission_record.get("permission_confirmed") is not True or not permission_record.get("permission_basis"):
        raise PermissionError("A permission record with permission_confirmed=true and permission_basis is required.")
    try:
        from faster_whisper import BatchedInferencePipeline, WhisperModel
    except ImportError as exc:
        raise RuntimeError("Install the optional 'whisper' extra to enable local transcription.") from exc

    dll_handles = []
    if os.name == "nt":
        for parent in map(Path, sys.path):
            for folder in (parent / "nvidia/cublas/bin", parent / "nvidia/cudnn/bin"):
                if folder.is_dir():
                    dll_handles.append(os.add_dll_directory(str(folder.resolve())))
                    os.environ["PATH"] = str(folder.resolve()) + os.pathsep + os.environ.get("PATH", "")
    name = model_name or os.getenv("GOODFIREVERSE_WHISPER_MODEL", "turbo")
    batch_size = int(os.getenv("GOODFIREVERSE_WHISPER_BATCH_SIZE", "1"))
    if not 1 <= batch_size <= 16:
        raise ValueError("GOODFIREVERSE_WHISPER_BATCH_SIZE must be between 1 and 16")
    os.environ.setdefault("HF_HOME", str(root / ".cache/huggingface"))
    model = WhisperModel(name, device="cuda", compute_type="float16", download_root=str(root / ".cache/whisper"))
    engine = BatchedInferencePipeline(model=model) if batch_size > 1 else model
    options = {"word_timestamps": True, "vad_filter": True}
    if batch_size > 1:
        options["batch_size"] = batch_size
    segments, info = engine.transcribe(str(media_path), **options)
    output_segments = []
    text_parts = []
    for segment in segments:
        text = segment.text.strip()
        text_parts.append(text)
        output_segments.append({
            "start": float(segment.start), "end": float(segment.end),
            "text": text, "speaker": None,
            "words": [
                {"start": float(word.start), "end": float(word.end), "text": word.word}
                for word in (segment.words or [])
            ],
        })
    output_dir = root / "private/transcripts"
    if not any(part.strip() for part in text_parts):
        raise ValueError("ASR produced no speech; no transcript stored")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{media_path.stem}.whisper.json"
    payload = {
        "media": str(media_path.relative_to(root)).replace("\\", "/"),
        "source_url": permission_record.get("source_url"),
        "permission_basis": permission_record["permission_basis"],
        "media_sha256": permission_record.get("media_sha256"),
        "audio_url": permission_record.get("audio_url"),
        "recording_role": permission_record.get("recording_role", "unknown"),
        "subject_time_ranges": permission_record.get("subject_time_ranges", []),
        "full_episode_access": permission_record.get("full_episode_access"),
        "partial_video_id": permission_record.get("partial_video_id"),
        "model": name, "device": "cuda", "compute_type": "float16",
        "batch_size": batch_size,
        "runtime_versions": {package: __import__(package).__version__ for package in ("faster_whisper", "ctranslate2", "av")},
        "language": info.language, "duration_seconds": info.duration,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "speaker_diarization": "not_performed",
        "segments": output_segments,
        "text": "\n".join(text_parts),
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output_path.with_suffix(".txt").write_text("\n".join(f"[{s['start']:.2f} --> {s['end']:.2f}] {s['text']}" for s in output_segments) + "\n", encoding="utf-8")
    return output_path
