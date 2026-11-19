"""Private numerical worker. Never imported by the dispatch process."""
import copy
import importlib.util
import inspect
import json
from pathlib import Path
import platform
import sys
import time
import traceback

import numpy as np
import scipy
from scipy.sparse.linalg import cg

from .adapter import (ROOT, InvalidInput, check_request, compile_schedule, model_build,
                      sha256, status_message, validate, verify_artifacts, write_json)


def capability(profile):
    build = model_build()
    reason = None
    if profile == "full":
        reason = ("legacy cg(tol=...) is incompatible with this SciPy API" if "tol" not in inspect.signature(cg).parameters
                  else "full numerical validation NOT_RUN")
    elif profile != "smoke":
        reason = "quick numerical validation NOT_RUN"
    elif (platform.python_version(), np.__version__, scipy.__version__) != ("3.10.19", "2.2.6", "1.15.3"):
        reason = "smoke numerical validation NOT_RUN for this environment combination"
    if reason:
        raise InvalidInput(f"MODEL_PROFILE_UNSUPPORTED: {profile}; {build['engine_version']}; {reason}")
    return build


def load_engine(build):
    manifest = json.loads((ROOT / "SHA256_MANIFEST.json").read_text(encoding="utf-8"))
    for filename, key in (("borden3d_common.py", "code_sha256"), ("borden3d_config.json", "config_sha256")):
        if build[key] != manifest[filename]:
            raise InvalidInput("Frozen scientific baseline hash mismatch: " + filename)
    path = ROOT / "borden3d_common.py"
    spec = importlib.util.spec_from_file_location("borden3d_common", path)
    engine = importlib.util.module_from_spec(spec)
    sys.modules["borden3d_common"] = engine
    spec.loader.exec_module(engine)
    if Path(engine.__file__).resolve() != path.resolve() or sha256(engine.__file__) != build["code_sha256"]:
        raise InvalidInput("Unexpected scientific module path/hash")
    return engine


def window_metrics(scene, simulation, lo, hi):
    """Integrate actual legacy step fluxes over exact risk boundaries."""
    times, c = simulation["times"], simulation["C"]
    i, j = (int(np.flatnonzero(times == v)[0]) for v in (lo, hi))
    steps = simulation["steps"][i:j]
    source = float(np.dot(np.diff(times)[i:j], simulation["source_flux_mg_d"][i:j]) * 1e-6)
    pumped = boundary = 0.0
    water = drawdown = 0.0
    for k, (dt, system, _) in enumerate(steps, start=i + 1):
        pumped += float(dt * np.dot(system["pumps"], c[k]) * 1e-3)
        boundary += float(dt * np.dot(np.maximum(-system["boundary_exchange"], 0), c[k]) * 1e-3)
        water = max(water, system["flow_balance_max_m3_d"])
        drawdown = max(drawdown, float(np.max(scene.base["head"] - system["head"])))
    initial, residual = (float(np.dot(scene.water, c[k]) * 1e-3) for k in (i, j))
    metrics = dict(residual_mass_kg=residual, boundary_output_mass_kg=boundary,
                   pumped_mass_kg=pumped, net_source_input_mass_kg=source,
                   max_drawdown_m=drawdown, max_concentration_mg_L=float(np.max(c[i:j + 1])),
                   water_balance_max_m3_day=water,
                   solute_mass_balance_relative=abs(initial + source - residual - pumped - boundary) / max(abs(source), 1.0))
    if source < 0:
        raise InvalidInput(f"CONTRACT_METRIC_UNREPRESENTABLE: signed net_source_input_mass_kg={source}; prescribed concentration can be a net sink in this window, but frozen v1 requires >=0")
    if not all(np.isfinite(v) and v >= 0 for v in metrics.values()):
        raise RuntimeError("Nonfinite or contract-incompatible negative metrics")
    return metrics, initial


def execute(request, run_dir, log):
    check_request(request)
    payload = request["payload"]
    build = capability(payload["profile"])
    for key in ("code_sha256", "config_sha256"):
        if request["model_build"][key] != build[key]:
            raise InvalidInput("Requested model build differs from frozen engine: " + key)
    provenance = request["data_provenance"]
    if provenance["classification"] == "observed" or provenance["calibration"] == "field_verified":
        raise InvalidInput("Default physical configuration is synthetic and uncalibrated")
    engine = load_engine(build)
    # Pass the decoded original bytes to bypass the legacy environment override.
    config = json.loads((ROOT / "borden3d_config.json").read_text(encoding="utf-8"))
    scene = engine.BordenScene(profile=payload["profile"], config=config)
    schedule = compile_schedule(payload, scene)
    np.random.seed(request["seed"])
    log("INFO", "Starting original SciPy Darcy/FV solve from model day 0")
    sim = scene.simulate(pump_schedule=schedule, end_day=payload["time_axis"]["model_end_day"], return_system=True)
    metrics, initial = window_metrics(scene, sim, payload["risk_window_start_day"], payload["risk_window_stop_day"])
    hydro = run_dir / "hydro"
    hydro.mkdir()
    np.savez_compressed(hydro / "concentration.npz", times_day=np.asarray(sim["times"], dtype=np.float64),
                        C=np.asarray(sim["C"], dtype=np.float64), cell_xyz_m=np.asarray(scene.xyz, dtype=np.float64),
                        cell_ids=np.arange(scene.n, dtype=np.int64))
    write_json(hydro / "metrics.json", dict(metrics=metrics, units=request["units"],
               initial_window_mass_kg=initial, time_axis=payload["time_axis"], schedule=schedule,
               model_build=build, model_signature=scene.signature, run_id=request["run_id"],
               scenario_version=request["scenario_version"], seed=request["seed"],
               risk_window_start_day=payload["risk_window_start_day"], risk_window_stop_day=payload["risk_window_stop_day"],
               risk_interpretation="synthetic_model_indicator_not_health_risk"))
    write_json(hydro / "capability.json", dict(model_build=build, worker_pid=__import__("os").getpid(),
               engine_path=str(Path(engine.__file__).resolve()), smoke="PASS", quick="NOT_RUN", full="NOT_RUN",
               full_supported=False, full_reason="legacy cg(tol=...) incompatible with SciPy 1.15; full unvalidated"))
    result = copy.deepcopy(request)
    result.update(kind="hydro_result", status="succeeded", model_build=build, artifacts=[], error=None,
                  payload=dict(engine_copy="root_standalone", model_signature=scene.signature, profile=payload["profile"],
                               times_day=sim["times"].tolist(), concentration_artifact_id="concentration",
                               metrics=metrics, risk_interpretation="synthetic_model_indicator_not_health_risk",
                               risk_window_start_day=payload["risk_window_start_day"], risk_window_stop_day=payload["risk_window_stop_day"]))
    result["data_provenance"]["sources"].append(dict(source_id="root-physical-config", reference="borden3d_config.json sha256=" + build["config_sha256"],
                                                      license="project synthetic benchmark", synthetic=True))
    for name, media, role, units in (("concentration.npz", "application/octet-stream", "concentration", "mg/L"),
                                     ("metrics.json", "application/json", "metrics", "see units"),
                                     ("capability.json", "application/json", "summary", "dimensionless")):
        result["artifacts"].append(dict(artifact_id=Path(name).stem, path="hydro/" + name,
                                       sha256=sha256(hydro / name), media_type=media, role=role, units=units))
    validate(result)
    verify_artifacts(result, run_dir)
    return result


def main():
    run_dir = Path(sys.argv[1]).resolve()
    request = json.loads((run_dir / "request.json").read_text(encoding="utf-8"))
    started = time.monotonic()

    def log(level, message):
        with (run_dir / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(dict(run_id=request["run_id"], scenario_version=request["scenario_version"],
                                        event_time_s=0.0, elapsed_wall_s=time.monotonic() - started,
                                        level=level, module="hydro_adapter", message=message), allow_nan=False) + "\n")

    write_json(run_dir / "status.json", status_message(request, "running"))
    log("INFO", "queued -> running")
    try:
        result = execute(request, run_dir, log)
        log("INFO", "Output validation passed; committing artifact index")
        result["artifacts"].append(dict(artifact_id="events", path="events.jsonl", sha256=sha256(run_dir / "events.jsonl"),
                                       media_type="application/x-ndjson", role="log", units="s"))
        validate(result)
        verify_artifacts(result, run_dir)
        write_json(run_dir / "artifact_index.json", result["artifacts"])
        write_json(run_dir / "result.json", result)
        write_json(run_dir / "status.json", result)
        return 0
    except Exception as exc:
        code = "INVALID_INPUT" if isinstance(exc, InvalidInput) else "ENGINE_FAILED"
        error = dict(code=code, message=str(exc), retryable=False)
        write_json(run_dir / "error.json", dict(error=error, traceback=traceback.format_exc()))
        log("ERROR", str(exc))
        result = status_message(request, "failed", error)
        write_json(run_dir / "result.json", result)
        write_json(run_dir / "status.json", result)
        return 1


if __name__ == "__main__":
    sys.exit(main())
