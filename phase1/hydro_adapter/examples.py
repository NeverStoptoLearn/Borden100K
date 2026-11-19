"""Three independent synthetic, uncalibrated engine examples; not fixtures."""
import argparse
import json
from pathlib import Path
import uuid

from .adapter import ROOT, model_build, run, write_json


def example_request(case):
    if case not in ("no_pump", "normal", "delayed"):
        raise ValueError(case)
    message = json.loads((ROOT / "schemas/examples/hydro_request.json").read_text(encoding="utf-8"))
    message.update(run_id=str(uuid.uuid4()), scenario_id="hydro-" + case,
                   model_build=model_build(), parameter_version="root-baseline-v1")
    message["data_provenance"] = dict(classification="synthetic", calibration="none", fixture=False,
        sources=[dict(source_id="synthetic-well", reference="smoke active non-CHD cell 100; demonstration only",
                      license="project synthetic benchmark", synthetic=True)])
    message["payload"]["pump_events"] = [] if case == "no_pump" else [dict(
        task_id="install-1", well_id="synthetic-well-1", cell_id=100,
        start_s=0 if case == "normal" else 86400, stop_s=7300 * 86400, extraction_rate_m3_day=20)]
    # The legacy concentration step can act as a net sink over 14600..21900.
    # v1 prohibits negative net_source_input_mass_kg: explicitly evaluate the
    # whole model horizon here rather than changing the signed physical flux.
    message["payload"]["risk_window_start_day"] = 0
    return message


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=["all", "no_pump", "normal", "delayed"], default="all")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary = {}
    for case in (("no_pump", "normal", "delayed") if args.case == "all" else (args.case,)):
        request = example_request(case)
        result = run(request, args.output)
        summary[case] = dict(run_id=result["run_id"], status=result["status"],
                             metrics=result["payload"].get("metrics"), error=result["error"])
        print(json.dumps({case: summary[case]}, allow_nan=False), flush=True)
    # Each invocation creates new UUID directories, with a separately unique summary.
    write_json(args.output / ("examples-" + str(uuid.uuid4()) + ".json"), summary)
    return 0 if all(x["status"] == "succeeded" for x in summary.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
