"""Restore large project assets from the GitHub release and verify their contents."""
from pathlib import Path
import hashlib
import json
import tempfile
import urllib.request


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    root = Path(__file__).resolve().parent
    assets = json.loads((root / 'large-assets.json').read_text(encoding='utf-8'))
    for asset in assets:
        target = (root / asset['path']).resolve()
        if not target.is_relative_to(root):
            raise ValueError('Asset path is outside the project')
        if target.is_file() and digest(target) == asset['sha256']:
            print(f"Already verified: {asset['path']}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            request = urllib.request.Request(asset['url'], headers={'User-Agent': 'project-asset-restore'})
            with tempfile.NamedTemporaryFile(dir=target.parent, suffix='.download', delete=False) as output:
                temporary = Path(output.name)
                with urllib.request.urlopen(request, timeout=120) as response:
                    while chunk := response.read(1024 * 1024):
                        output.write(chunk)
            if temporary.stat().st_size != asset['bytes'] or digest(temporary) != asset['sha256']:
                raise RuntimeError(f"Checksum or size mismatch: {asset['path']}")
            temporary.replace(target)
            print(f"Restored and verified: {asset['path']}")
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()


if __name__ == '__main__':
    main()
