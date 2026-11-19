"""Reproduce a signed source-window diagnostic without changing the v1 result contract.

This is review evidence from the original solver, not a successful hydro_result.
"""
import argparse
import json
from pathlib import Path
import uuid

import numpy as np

from .adapter import compile_schedule, model_build, run, write_json
from .examples import example_request
from .worker import load_engine


def signed_window_evidence(request):
    scene = load_engine(model_build()).BordenScene("smoke")
    payload = request["payload"]
    simulation = scene.simulate(pump_schedule=compile_schedule(payload, scene),
                                end_day=payload["time_axis"]["model_end_day"], return_system=True)
    times, concentrations = simulation["times"], simulation["C"]
    lo, hi = payload["risk_window_start_day"], payload["risk_window_stop_day"]
    i, j = (int(np.flatnonzero(times == v)[0]) for v in (lo, hi))
    mask = scene.source_mask(scene.src["center_xy_m"])
    source = independently_reconstructed_source = pumped = boundary = 0.0
    for k in range(i + 1, j + 1):
        dt, system, _ = simulation["steps"][k - 1]
        source += dt * simulation["source_flux_mg_d"][k - 1] * 1e-6
        defect = scene.water / dt * (concentrations[k] - concentrations[k - 1]) + system["L"] @ concentrations[k]
        independently_reconstructed_source += dt * float(defect[mask].sum()) * 1e-3
        pumped += dt * float(np.dot(system["pumps"], concentrations[k])) * 1e-3
        boundary += dt * float(np.dot(np.maximum(-system["boundary_exchange"], 0), concentrations[k])) * 1e-3
    initial, residual = (float(np.dot(scene.water, concentrations[k])) * 1e-3 for k in (i, j))
    balance = initial + source - residual - pumped - boundary
    return dict(model_build=model_build(), synthetic=True, fixture=False,
                risk_window_start_day=lo, risk_window_stop_day=hi,
                initial_mass_kg=initial, residual_mass_kg=residual,
                signed_net_source_input_mass_kg=source,
                reconstructed_source_mass_kg=independently_reconstructed_source,
                pumped_mass_kg=pumped, boundary_output_mass_kg=boundary,
                signed_balance_defect_kg=balance,
                relative_balance_error=abs(balance) / max(abs(source), 1.0),
                source_switch_day=scene.src["switch_day"],
                first_source_concentration_mg_L=scene.src["first_concentration_mg_L"],
                second_source_concentration_mg_L=scene.src["first_concentration_mg_L"] * scene.src["second_ratio"],
                interpretation="Prescribed concentration cells may remove mass; the source term is signed. No clipping, absolute-value substitution, window substitution, or kernel modification.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    directory = args.output.resolve() / ("signed-window-" + str(uuid.uuid4()))
    directory.mkdir(parents=True, exist_ok=False)
    request = example_request("normal")
    request["payload"]["risk_window_start_day"] = 14600.0
    evidence = signed_window_evidence(request)
    result = run(request, directory / "runs")
    evidence.update(production_run_id=result["run_id"], production_status=result["status"],
                    production_error=result["error"], successful_production_result=False)
    write_json(directory / "signed_window.json", evidence)
    print(json.dumps(evidence, ensure_ascii=False, indent=2, allow_nan=False))
    print("REVIEW_EVIDENCE=" + str(directory))
    assert evidence["signed_net_source_input_mass_kg"] < 0
    assert abs(evidence["signed_net_source_input_mass_kg"] - evidence["reconstructed_source_mass_kg"]) < 1e-7
    assert evidence["relative_balance_error"] < 1e-8
    assert result["status"] == "failed"
    assert result["error"]["message"].startswith("CONTRACT_METRIC_UNREPRESENTABLE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
