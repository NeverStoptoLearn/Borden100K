"""python -B -m phase1.resources REQUEST --context CONTEXT --output-root ROOT"""

import argparse
import json
from pathlib import Path
import sys

from .scheduler import InvalidInput, schedule, validate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path)
    parser.add_argument("--context", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args()
    run_dir = None
    created = False
    try:
        request = json.loads(args.request.read_text(encoding="utf-8"))
        validate(request, "resource_request")
        run_dir = args.output_root / request["run_id"]
        run_dir.mkdir(parents=True, exist_ok=False)
        created = True
        context = json.loads(args.context.read_text(encoding="utf-8"))
        result = schedule(request, scenario_context=context)
        (run_dir / "request.json").write_text(json.dumps(request, indent=2, allow_nan=False), encoding="utf-8")
        (run_dir / "context.json").write_text(json.dumps(context, indent=2, allow_nan=False), encoding="utf-8")
        event = dict(run_id=request["run_id"], scenario_version=request["scenario_version"],
                     event_time_s=0, level="INFO", module="resources",
                     message="schedule complete", feasible=result["payload"]["feasible"],
                     fixture=result["data_provenance"]["fixture"])
        log_path = run_dir / "events.jsonl"
        log_path.write_text(json.dumps(event, allow_nan=False) + "\n", encoding="utf-8")
        import hashlib
        result["artifacts"] = [dict(artifact_id="resource-log", path="events.jsonl",
            sha256=hashlib.sha256(log_path.read_bytes()).hexdigest(), media_type="application/x-ndjson",
            role="log", units="s")]
        validate(result, "resource_result")
        (run_dir / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
        print(json.dumps(dict(result=str(run_dir / "result.json"), **event)))
        return 0
    except (InvalidInput, ValueError, OSError) as exc:
        # Never write a successful result for a rejected input or overwrite an old run.
        if created:
            import traceback
            error = dict(code="INVALID_INPUT", message=str(exc), traceback=traceback.format_exc())
            (run_dir / "error.json").write_text(json.dumps(error), encoding="utf-8")
            event = dict(run_id=request["run_id"], scenario_version=request["scenario_version"],
                         event_time_s=0, level="ERROR", module="resources", message=str(exc))
            (run_dir / "events.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")
        print(f"INVALID_INPUT: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
