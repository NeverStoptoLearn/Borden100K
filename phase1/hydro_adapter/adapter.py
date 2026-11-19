"""Validated contract boundary and isolated process launcher (no kernel imports)."""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
from datetime import datetime, timezone

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "schemas/phase1-v1.schema.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())


class InvalidInput(ValueError):
    pass


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def model_build():
    import numpy
    import scipy
    return dict(engine_id="borden3d_scipy_fv",
                engine_version=f"Python {platform.python_version()}; NumPy {numpy.__version__}; SciPy {scipy.__version__}; executable={sys.executable}",
                code_sha256=sha256(ROOT / "borden3d_common.py"),
                config_sha256=sha256(ROOT / "borden3d_config.json"))


def validate(message):
    # JSON's encoder accepts NaN by default; the contract never does.
    try:
        json.dumps(message, allow_nan=False)
        VALIDATOR.validate(message)
    except Exception as exc:
        raise InvalidInput(str(exc)) from exc


def write_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    os.replace(temporary, path)


def status_message(request, status, error=None):
    result = copy.deepcopy(request)
    result.update(kind="run_status", status=status, artifacts=[], error=error,
                  model_build=model_build(), payload={
                      "previous_status": "queued" if status == "running" else "running",
                      "updated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                      "progress_fraction": 0.0})
    validate(result)
    return result


def check_request(request):
    validate(request)
    if request["kind"] != "hydro_request" or request["status"] != "queued" or request["artifacts"]:
        raise InvalidInput("Expected queued hydro_request with empty artifacts")
    if request["data_provenance"]["fixture"]:
        raise InvalidInput("fixture requests cannot produce production results")


def compile_schedule(payload, scene):
    """Sweep half-open events, preserving simultaneous and zero-rate boundaries."""
    axis = payload["time_axis"]
    ref, end = axis["reference_model_day"], axis["model_end_day"]
    lo, hi = payload["risk_window_start_day"], payload["risk_window_stop_day"]
    if not (0 <= ref < end == scene.src["end_day"] and 0 <= lo < hi <= end):
        raise InvalidInput("Invalid reference day, original model horizon, or risk window")
    intervals, bounds, tasks, well_cells = [], {0.0, end, lo, hi}, set(), {}
    for event in payload["pump_events"]:
        task, well, cell = event["task_id"], event["well_id"], event["cell_id"]
        if task in tasks or (well in well_cells and well_cells[well] != cell):
            raise InvalidInput("Duplicate task_id or inconsistent well mapping")
        tasks.add(task)
        well_cells[well] = cell
        if not 0 <= cell < scene.n or scene.chd[cell]:
            raise InvalidInput("Well must map to an active non-CHD cell")
        start, stop = (ref + event[key] / 86400.0 for key in ("start_s", "stop_s"))
        rate = event["extraction_rate_m3_day"]
        if not (0 <= event["start_s"] < event["stop_s"] and
                scene.cfg["control"]["start_day"] <= start < stop <= scene.cfg["control"]["end_day"] and
                stop <= end and math.isfinite(rate) and rate >= 0):
            raise InvalidInput("Invalid pumping time/rate; events must lie in original control window")
        bounds.update((start, stop))
        intervals.append((start, stop, cell, rate))
    edges = sorted(bounds)
    schedule = []
    for start, stop in zip(edges, edges[1:]):
        rates = {}
        for a, b, cell, rate in intervals:
            if a <= start < b:
                rates[cell] = rates.get(cell, 0.0) + rate
        if not all(math.isfinite(q) for q in rates.values()):
            raise InvalidInput("Aggregated rate overflow")
        # Empty segments intentionally insert risk/event boundaries into the legacy grid.
        schedule.append(dict(start_day=start, end_day=stop,
                             pumps=[dict(cell=cell, rate=q) for cell, q in sorted(rates.items()) if q > 0]))
    return schedule


def verify_artifacts(result, run_dir):
    import numpy as np
    seen = set()
    concentration_ids = set()
    root = Path(run_dir).resolve()
    for item in result["artifacts"]:
        name = item["path"]
        if item["artifact_id"] in seen or any(x in ("", ".", "..") for x in name.split("/")) or "\\" in name or ":" in name:
            raise InvalidInput("Invalid artifact identity/path")
        seen.add(item["artifact_id"])
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file() or sha256(path) != item["sha256"]:
            raise InvalidInput("Artifact missing, escaped run root, or hash mismatch")
        if item["role"] == "concentration":
            concentration_ids.add(item["artifact_id"])
            with np.load(path, allow_pickle=False) as data:
                t, c, xyz, ids = (data[k] for k in ("times_day", "C", "cell_xyz_m", "cell_ids"))
                if (t.dtype != np.float64 or c.dtype != np.float64 or xyz.dtype != np.float64 or
                        t.ndim != 1 or ids.ndim != 1 or ids.dtype.kind not in "iu" or c.shape != (len(t), len(ids)) or xyz.shape != (len(ids), 3) or
                        not np.array_equal(ids, np.arange(len(ids))) or not np.all(np.diff(t) > 0) or
                        not all(np.all(np.isfinite(a)) for a in (t, c, xyz)) or np.min(c) < -1e-7 or
                        t.tolist() != result["payload"]["times_day"]):
                    raise InvalidInput("Invalid concentration arrays")
    if result["payload"]["concentration_artifact_id"] not in concentration_ids:
        raise InvalidInput("Concentration artifact reference is missing")


def run(request, output_root, timeout_s=300):
    """Run once in a fresh interpreter; output_root/run_id must not already exist.

    Structural errors raise InvalidInput. Engine/preflight failures return v1
    failed run_status (v1 hydro_result is reserved for succeeded output).
    """
    check_request(request)
    run_dir = Path(output_root).resolve() / request["run_id"]
    run_dir.mkdir(parents=True, exist_ok=False)
    write_json(run_dir / "request.json", request)
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    env.pop("BORDEN3D_SCENE_CONFIG", None)
    command = [sys.executable, "-B", "-m", "phase1.hydro_adapter.worker", str(run_dir)]
    try:
        with (run_dir / "worker.stdout.log").open("w", encoding="utf-8") as out, (run_dir / "worker.stderr.log").open("w", encoding="utf-8") as err:
            completed = subprocess.run(command, cwd=ROOT, env=env, stdout=out, stderr=err, timeout=timeout_s)
        result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
        validate(result)
        for key in ("contract_version", "experiment_id", "run_id", "scenario_id", "scenario_version", "parameter_version", "seed", "units"):
            if result[key] != request[key]:
                raise RuntimeError("Worker output identity/version mismatch: " + key)
        if result["status"] == "succeeded":
            if completed.returncode != 0:
                raise RuntimeError("Worker exited nonzero")
            verify_artifacts(result, run_dir)
        return result
    except Exception as exc:
        code = "TIMEOUT" if isinstance(exc, subprocess.TimeoutExpired) else "ENGINE_FAILED"
        result = status_message(request, "failed", dict(code=code, message=str(exc), retryable=False))
        write_json(run_dir / "error.json", {"error": result["error"], "traceback": __import__("traceback").format_exc()})
        write_json(run_dir / "status.json", result)
        write_json(run_dir / "result.json", result)
        return result
