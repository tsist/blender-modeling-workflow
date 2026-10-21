"""Standalone modeling protocol, preserving upstream JSON/SHA/error primitives."""
import hashlib
import json
import math
import os
from pathlib import Path
import time
import uuid

VERSION = '0.1.13'
SCHEMA = '1.0'
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BLENDER = ROOT / 'blender.exe'
DEFAULT_JOBS = ROOT / 'runtime' / 'jobs'
DEFAULT_TRANSACTIONS = ROOT / 'runtime' / 'transactions'
CODES = {'INVALID_REQUEST': 2, 'NOT_FOUND': 3, 'CONFLICT': 4, 'WORKER_FAILED': 5,
         'TIMEOUT': 6, 'CANCELLED': 7, 'VALIDATION_FAILED': 8, 'IO_ERROR': 9,
         'UNSUPPORTED': 10, 'RESOURCE_LIMIT': 11, 'RATE_LIMITED': 12, 'ARTIFACT_UNAVAILABLE': 13}
COMMANDS = ('modeling.run',)
HOST_COMMANDS = ('modeling.run',)


class Failure(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code

def read_json(path):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise Failure("INVALID_REQUEST", f"Duplicate JSON key: {key}")
            out[key] = value
        return out
    try:
        if os.name == "nt":
            from filesystem import read_shared_bytes
            text = read_shared_bytes(path).decode("utf-8-sig")
        else:
            text = Path(path).read_text(encoding="utf-8-sig")
        return json.loads(text, object_pairs_hook=pairs)
    except FileNotFoundError as exc:
        raise Failure("NOT_FOUND", f"JSON file not found: {path}") from exc
    except (ValueError, UnicodeError) as exc:
        raise Failure("INVALID_REQUEST", f"Invalid UTF-8 JSON: {exc}") from exc

def atomic_json(path, value):
    path = Path(path)
    # Same-directory unique O_EXCL file preserves atomic replacement without
    # inflating a legal Windows destination by its entire basename plus UUID.
    temp = path.with_name("." + uuid.uuid4().hex + ".tmp")
    with temp.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    # Windows can briefly retain a delete-pending destination while shared
    # readers finish the old JSON generation. Retry only this metadata operation,
    # never a transaction publish or a failed worker. Keep a hard time bound.
    deadline = time.monotonic() + .25
    while True:
        try:
            os.replace(temp, path)
            break
        except OSError as exc:
            if os.name != "nt" or getattr(exc, "winerror", None) not in (5, 32, 33) or time.monotonic() >= deadline:
                raise
            time.sleep(.005)

def digest(path, checkpoint=None):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            if checkpoint:
                checkpoint()
            h.update(chunk)
    return h.hexdigest()

def timeout_value(value):
    if not math.isfinite(value) or value <= 0 or value > 86400:
        raise Failure("INVALID_REQUEST", "timeout must be finite and in (0, 86400]")
    return value

def envelope(command=None, job_id=None, data=None, error=None, artifacts=None):
    return {"schema_version": SCHEMA, "cli_version": VERSION, "command": command,
            "job_id": job_id, "ok": error is None, "data": data,
            "error": error, "artifacts": artifacts or {}}


def validate(request):
    if not isinstance(request, dict):
        raise Failure('INVALID_REQUEST', 'Request must be an object')
    if set(request) - {'schema_version', 'command', 'params'} or request.get('schema_version') != SCHEMA:
        raise Failure('INVALID_REQUEST', 'Expected schema_version 1.0 and strict request fields')
    if request.get('command') != 'modeling.run':
        raise Failure('INVALID_REQUEST', 'Standalone distribution only accepts modeling.run')
    from modeling_workflow_contract import normalize_params
    return {'schema_version': SCHEMA, 'command': 'modeling.run',
            'params': normalize_params(request.get('params', {}))}
