from pathlib import Path
import argparse
from .core import Fetcher
from .collect import discover, collect_publications, collect_repos, collect_papers, collect_microsites


def main():
    import sys
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf8', errors='replace')
    parser = argparse.ArgumentParser(description='Resumable Goodfire research corpus')
    parser.add_argument('command', choices=['discover', 'publications', 'repos', 'snapshots', 'papers', 'microsites', 'enrich', 'media', 'feeds', 'captions', 'audio', 'report', 'validate', 'search'])
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--limit', type=int, default=100)
    parser.add_argument('--query', default='')
    args = parser.parse_args()
    root = args.root.resolve()
    fetch = Fetcher(root, refresh=args.refresh)
    operations = {'discover': discover, 'publications': collect_publications, 'repos': collect_repos,
                  'papers': collect_papers, 'microsites': collect_microsites}
    from .snapshots import snapshots
    from .enrich import enrich
    operations.update({'snapshots': snapshots, 'enrich': enrich})
    if args.command in operations:
        operations[args.command](root, fetch)
    elif args.command in {'media', 'feeds', 'captions', 'audio'}:
        from .media import collect_media, collect_feeds, collect_captions, collect_audio
        {'media': collect_media, 'feeds': collect_feeds, 'captions': collect_captions,
         'audio': collect_audio}[args.command](root, fetch, args.limit)
    elif args.command in {'report', 'validate', 'search'}:
        from .report import report, validate, search
        if args.command == 'report': report(root)
        elif args.command == 'validate': validate(root)
        else: search(root, args.query, args.limit)


if __name__ == '__main__':
    main()
