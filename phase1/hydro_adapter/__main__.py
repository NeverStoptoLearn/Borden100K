"""python -m phase1.hydro_adapter request.json --output RUNS_DIRECTORY"""
import argparse
import json
from pathlib import Path
from .adapter import run

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("request", type=Path)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
result = run(json.loads(args.request.read_text(encoding="utf-8")), args.output)
print(json.dumps(result, ensure_ascii=False, allow_nan=False))
raise SystemExit(0 if result["status"] == "succeeded" else 1)
