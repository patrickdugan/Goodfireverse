"""Apply explicit source-linked identity decisions after automatic discovery."""
import json
from .core import canonical, rows, upsert


def apply_reviews(root):
    path = root / 'config/media-reviews.json'
    if not path.exists():
        return
    decisions = json.loads(path.read_text(encoding='utf8'))
    upsert(root / 'catalog/appearances.jsonl', [{'url': canonical(r['url']), **r} for r in decisions])
