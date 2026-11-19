"""Frozen v1 boundary and deterministic finite resource assignment."""

import copy
import hashlib
import itertools
import json
import math
from pathlib import Path
import platform

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "schemas/phase1-v1.schema.json"
IDENTITY = ("contract_version", "experiment_id", "run_id", "scenario_id",
            "scenario_version", "parameter_version", "seed", "units")
POLICY = "resource-baseline-v1: fixed dispatch; one delivery per asset; crew rides vehicle"


class InvalidInput(ValueError):
    """Malformed or mismatched input; distinct from business infeasibility."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise InvalidInput("nonfinite number")
    if isinstance(value, dict):
        for item in value.values():
            finite(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            finite(item)


def validate(envelope, kind):
    finite(envelope)
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(envelope)
    except Exception as exc:
        raise InvalidInput(f"schema: {exc}") from exc
    if envelope["kind"] != kind:
        raise InvalidInput(f"expected {kind}")


def unique(rows, key):
    result = {}
    for row in rows:
        name = key(row) if callable(key) else row[key]
        if name in result:
            raise InvalidInput(f"duplicate ID/reference: {name}")
        result[name] = row
    return result


def window(row, start="available_from_s", stop="available_until_s"):
    if row[start] > row[stop]:
        raise InvalidInput(f"reversed window: {row}")


def inside(row, start, stop):
    return row["available_from_s"] <= start <= stop <= row["available_until_s"]


def overlap(a, b):
    return a[0] < b[1] and b[0] < a[1]


def verify_artifacts(traffic, context):
    artifacts = unique(traffic["artifacts"], "artifact_id")
    for route in traffic["payload"]["routes"]:
        if route["reachable"]:
            item = artifacts.get(route["trajectory_artifact_id"])
            if item is None or item["role"] != "trajectory":
                raise InvalidInput("missing trajectory artifact reference")
    if traffic["data_provenance"]["fixture"]:
        return  # Explicit structure fixtures have no real SUMO artifact bytes.
    if "traffic_artifact_root" not in context:
        raise InvalidInput("production requires traffic_artifact_root for verified Traffic files")
    root = Path(context["traffic_artifact_root"]).resolve(strict=True)
    for item in artifacts.values():
        path = (root / item["path"]).resolve(strict=True)
        if root not in path.parents or not path.is_file():
            raise InvalidInput("artifact escapes run root or is not a file")
        if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise InvalidInput("artifact SHA256 mismatch")
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    sample_validator = Draft202012Validator(
        {"$ref": "#/$defs/trajectory_sample", "$defs": schema["$defs"]},
        format_checker=FormatChecker())
    for route in traffic["payload"]["routes"]:
        if not route["reachable"]:
            continue
        samples = json.loads((root / artifacts[route["trajectory_artifact_id"]]["path"]).read_text(encoding="utf-8"))
        finite(samples)
        if not isinstance(samples, list):
            raise InvalidInput("trajectory must be an array")
        selected = []
        for sample in samples:
            try:
                sample_validator.validate(sample)
            except Exception as exc:
                raise InvalidInput(f"trajectory schema: {exc}") from exc
            if (sample["vehicle_id"], sample["route_id"]) == (route["vehicle_id"], route["route_id"]):
                selected.append(sample["event_time_s"])
        if not selected or any(b <= a for a, b in zip(selected, selected[1:])) or not route["depart_s"] <= selected[0] <= selected[-1] <= route["arrival_time_s"]:
            raise InvalidInput("trajectory missing/nonmonotonic/outside route interval")


def schedule(resource_request_envelope, *, scenario_context):
    """Return a new resource_result. See README for the read-only context contract.

    Malformed input raises InvalidInput. A completed feasibility query has status
    succeeded even when feasible=false; it never returns a partial pump plan.
    """
    request = copy.deepcopy(resource_request_envelope)
    context = scenario_context
    try:
        return _schedule(request, context)
    except (KeyError, TypeError, OSError, OverflowError, json.JSONDecodeError) as exc:
        raise InvalidInput(f"invalid context/input: {exc}") from exc


def _schedule(request, context):
    validate(request, "resource_request")
    if request["status"] not in ("queued", "running"):
        raise InvalidInput("resource request must be queued or running")
    if request["artifacts"]:
        raise InvalidInput("resource request artifacts must be empty; Traffic owns its files")
    scenario, traffic = context["scenario"], context["traffic_result"]
    finite(context)
    validate(scenario, "scenario")
    validate(traffic, "traffic_result")
    for other, keys in ((traffic, IDENTITY), (scenario, tuple(k for k in IDENTITY if k != "run_id"))):
        if any(request[k] != other[k] for k in keys):
            raise InvalidInput("cross-module identity/version/units mismatch")
        if request["data_provenance"]["fixture"] != other["data_provenance"]["fixture"]:
            raise InvalidInput("fixture cannot enter a production scheduling context")
    for envelope in (request, scenario, traffic):
        unique(envelope["data_provenance"]["sources"], "source_id")
        if any(envelope["model_build"][k] == "0" * 64 for k in ("code_sha256", "config_sha256")) and not envelope["data_provenance"]["fixture"]:
            raise InvalidInput("production build has placeholder hashes")
    axis = scenario["payload"]["time_axis"]
    if context["traffic_time_axis"] != axis:
        raise InvalidInput("Traffic time axis/reference day mismatch")
    if axis["reference_model_day"] >= axis["model_end_day"]:
        raise InvalidInput("invalid model horizon")
    control = context["control_window_model_day"]
    if not isinstance(control, list) or len(control) != 2 or not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in control):
        raise InvalidInput("control_window_model_day must be [start_day, stop_day]")
    if not 0 <= control[0] < control[1] <= axis["model_end_day"]:
        raise InvalidInput("invalid control window")
    lower = max(0, (control[0] - axis["reference_model_day"]) * 86400)
    upper = (control[1] - axis["reference_model_day"]) * 86400
    p = request["payload"]
    if p["traffic_run_id"] != traffic["run_id"] or p["routes"] != traffic["payload"]["routes"]:
        raise InvalidInput("routes must be unchanged from the identified Traffic result")
    verify_artifacts(traffic, context)
    routes = unique(p["routes"], "task_id")
    unique(p["routes"], "route_id")
    unique(p["routes"], "vehicle_id")  # v1 Traffic: one dispatch per vehicle/run
    vehicles = unique(p["vehicles"], "vehicle_id")
    crews = unique(p["crews"], "crew_id")
    equipment = unique(p["equipment"], "equipment_id")
    stock = unique(p["inventory"], lambda r: (r["depot_id"], r["item_id"]))
    tasks = unique(p["tasks"], "task_id")
    wells = unique(scenario["payload"]["wells"], "well_id")
    if set(routes) != set(tasks):
        raise InvalidInput("each task must have exactly one Traffic route")
    for row in itertools.chain(vehicles.values(), crews.values(), equipment.values()):
        window(row)
    for row in crews.values():
        if len(set(row["skills"])) != len(row["skills"]):
            raise InvalidInput("duplicate skill ID")
    for task in tasks.values():
        if task["well_id"] not in wells or routes[task["task_id"]]["vehicle_id"] not in vehicles:
            raise InvalidInput("unknown well/vehicle reference")
        if task["depot_id"] not in {k[0] for k in stock}:
            raise InvalidInput("unknown depot reference (v1 depots are inventory keys)")
        window(task, "earliest_start_s", "latest_stop_s")
        if task["pump_duration_s"] <= 0:
            raise InvalidInput("pump_duration_s must be positive")
    for route in routes.values():
        if route["reachable"] and route["arrival_time_s"] < route["depart_s"]:
            raise InvalidInput("arrival precedes departure")

    ordered = sorted(tasks.values(), key=lambda t: (-t["priority"], t["task_id"]))
    remaining = {k: r["quantity"] for k, r in stock.items()}
    violations = set()

    def assign(index, plan, used_crews, used_equipment, well_intervals):
        if index == len(ordered):
            return plan
        task = ordered[index]
        tid = task["task_id"]
        route = routes[tid]
        def reject(reason):
            violations.add(f"{tid}: {reason}")
        if not route["reachable"]:
            reject("UNREACHABLE: " + route["reason"])
            return None
        depart, arrival = route["depart_s"], route["arrival_time_s"]
        unload_start = max(arrival, task["earliest_start_s"], lower)
        unload = unload_start + task["unload_duration_s"]
        install = unload + task["install_duration_s"]
        stop = install + task["pump_duration_s"]
        if not all(math.isfinite(t) for t in (unload, install, stop)):
            raise InvalidInput("nonfinite derived time")
        if stop <= install:
            raise InvalidInput("pump duration lost at floating-point precision")
        if stop > min(task["latest_stop_s"], upper):
            reject("TIME_WINDOW: task/control/model end")
            return None
        vehicle = vehicles[route["vehicle_id"]]
        if not inside(vehicle, depart, unload):
            reject("VEHICLE_WINDOW: depart through unload")
            return None
        key = (task["depot_id"], task["required_item_id"])
        quantity = task["required_quantity"]
        if remaining.get(key, 0) < quantity:
            reject("INVENTORY: insufficient stock; no replenishment")
            return None
        capable = [c for c in sorted(crews) if task["required_skill"] in crews[c]["skills"] and inside(crews[c], depart, install)]
        available_crews = [c for c in capable if c not in used_crews]
        if not available_crews:
            reject("CREW_CONFLICT: occupied or unsupported relocation" if capable else "SKILL_OR_CREW_WINDOW")
            return None
        candidates = [e for e in sorted(equipment) if equipment[e]["type"] == task["required_item_id"] and inside(equipment[e], depart, stop)]
        available_equipment = [e for e in candidates if e not in used_equipment]
        if len(available_equipment) < quantity:
            reject("EQUIPMENT_CONFLICT: occupied or unsupported recovery" if len(candidates) >= quantity else "EQUIPMENT_TYPE_QUANTITY_OR_WINDOW")
            return None
        interval = (install, stop)
        if any(overlap(interval, old) for old in well_intervals.get(task["well_id"], [])):
            reject("WELL_CONFLICT: overlapping pump tasks")
            return None
        loadable = False
        for chosen in itertools.combinations(available_equipment, quantity):
            if sum(equipment[e]["mass_kg"] for e in chosen) > vehicle["capacity_kg"]:
                continue
            loadable = True
            for crew in available_crews:
                entry = dict(task_id=tid, well_id=task["well_id"], vehicle_id=route["vehicle_id"],
                             crew_ids=[crew], equipment_ids=list(chosen), vehicle_arrival_s=arrival,
                             unload_complete_s=unload, install_complete_s=install,
                             pump_start_s=install, pump_stop_s=stop,
                             extraction_rate_m3_day=task["extraction_rate_m3_day"])
                remaining[key] -= quantity
                intervals = {k: list(v) for k, v in well_intervals.items()}
                intervals.setdefault(task["well_id"], []).append(interval)
                result = assign(index + 1, plan + [entry], used_crews | {crew}, used_equipment | set(chosen), intervals)
                remaining[key] += quantity
                if result is not None:
                    return result
        if not loadable:
            reject("CAPACITY: equipment mass exceeds vehicle capacity")
        return None

    plan = assign(0, [], set(), set(), {})
    result = copy.deepcopy(request)
    result.update(kind="resource_result", status="succeeded", artifacts=[], error=None,
                  payload={"feasible": plan is not None, "tasks": plan or [],
                           "violations": [] if plan is not None else sorted(violations)})
    policy_source = dict(source_id="resource-policy-v1", reference=POLICY,
                         license="project synthetic scheduling policy", synthetic=True)
    provenance = result["data_provenance"]
    sources = {s["source_id"]: s for s in provenance["sources"]}
    for origin in (scenario, traffic):
        for source in origin["data_provenance"]["sources"]:
            if source["source_id"] in sources and sources[source["source_id"]] != source:
                raise InvalidInput("conflicting provenance source ID")
            sources[source["source_id"]] = copy.deepcopy(source)
    if policy_source["source_id"] in sources and sources[policy_source["source_id"]] != policy_source:
        raise InvalidInput("reserved policy source ID collision")
    sources[policy_source["source_id"]] = policy_source
    provenance["sources"] = [sources[k] for k in sorted(sources)]
    provenance["classification"] = "synthetic" if all(s["synthetic"] for s in sources.values()) else "mixed"
    # Hash actual module bytes, configuration and the immutable upstream snapshots.
    module_dir = Path(__file__).parent
    code = hashlib.sha256()
    for path in sorted(module_dir.glob("*.py")):
        code.update(path.name.encode())
        code.update(path.read_bytes())
    result["model_build"] = dict(engine_id="resource_baseline",
        engine_version=f"resource-1.0.0 Python/{platform.python_version()}",
        code_sha256=code.hexdigest(), config_sha256=digest(dict(policy=POLICY,
            payload=request["payload"], scenario=scenario, traffic=traffic,
            traffic_time_axis=context["traffic_time_axis"], control_window_model_day=control)))
    validate(result, "resource_result")
    return result
