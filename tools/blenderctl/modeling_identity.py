"""All standalone runtime/addon Python bytes form the implementation identity."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def runtime_documents():
    roots = (ROOT / 'tools' / 'blenderctl', ROOT / 'tools' / 'modeling_workflow_addon')
    paths = []
    for source in roots:
        if not source.is_dir() or source.is_symlink():
            raise ValueError('Standalone implementation directory unavailable or aliased')
        for path in source.rglob('*.py'):
            if path.is_symlink() or not path.resolve().is_relative_to(source.resolve()):
                raise ValueError('Standalone implementation path leaves its owned source tree')
            if path.is_file():
                paths.append(path)
    if not paths or not (roots[0] / 'cli.py').is_file() or not (roots[1] / 'core.py').is_file():
        raise ValueError('Standalone implementation is incomplete')
    return [{'file': str(p.resolve()), 'expected_sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
            for p in sorted(set(paths))]


def implementation_sha256():
    rows = [{**row, 'file': str(Path(row['file']).relative_to(ROOT)).replace('\\', '/')}
            for row in runtime_documents()]
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
