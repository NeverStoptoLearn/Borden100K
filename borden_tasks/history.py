"""Explicit availability boundary for retired historical raw payloads.

Original databases/manifests stay immutable. Only completed ledger events apply.
Unsupported legacy scripts are outside the release CLI; this gateway never
pretends that their raw-budget replay or arbitrary continuation still works.
"""
import json
import re
from pathlib import Path

class RawRetired(FileNotFoundError):
    code='RAW_RETIRED'

def require_raw(path,ledger):
    norm=lambda p:str(p).replace('\\','/').casefold()
    targets={norm(Path(path).absolute()),norm(Path(path).resolve(strict=False))}
    if ledger and Path(ledger).is_file():
        with Path(ledger).open(encoding='utf8') as f:
            for line in f:
                event=json.loads(line)
                names=[event.get('logical_path',''),event.get('physical_path',''),*event.get('logical_aliases',[])]
                if targets.intersection(norm(n) for n in names) and event.get('state')=='RETIRED' and re.fullmatch('[0-9a-f]{64}',event.get('approval_sha256','')):
                    raise RawRetired('RAW_RETIRED: historical payload intentionally retired; numerical results remain available')
    p=Path(path)
    if not p.is_file():raise FileNotFoundError(path)
    return p
