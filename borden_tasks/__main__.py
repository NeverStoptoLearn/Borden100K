"""Command-line entry point for the existing task evaluators."""

import argparse
import json
from pathlib import Path

from .core import SCENES, TASKS, prepare, run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Borden100K Task Release bounded evaluation interfaces'
    )
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('list')

    prepare_parser = commands.add_parser('prepare')
    prepare_parser.add_argument('--scenario', choices=SCENES, required=True)
    prepare_parser.add_argument('--output', type=Path, required=True)

    run_parser = commands.add_parser('run')
    run_parser.add_argument('--task', choices=TASKS, required=True)
    run_parser.add_argument('--scenario', choices=SCENES, required=True)
    run_parser.add_argument('--output', type=Path, required=True)
    run_parser.add_argument('--seed', type=int, default=42)
    run_parser.add_argument('--observations', type=Path)
    run_parser.add_argument('--candidate', type=float, nargs=3)
    run_parser.add_argument('--mode', default='maintenance_smoke')
    run_parser.add_argument('--traffic-variant', choices=['normal', 'closure'], default='normal')
    run_parser.add_argument('--traffic-result', type=Path)
    run_parser.add_argument('--traffic-root', type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == 'list':
            print(json.dumps(TASKS, indent=2))
            return 0
        if args.command == 'prepare':
            output = prepare(args.scenario, args.output)
            print(json.dumps(dict(status='prepared', output=str(output))))
            return 0
        result = run(
            args.task,
            args.scenario,
            args.output,
            seed=args.seed,
            observations=args.observations,
            candidate=args.candidate,
            mode=args.mode,
            traffic_variant=args.traffic_variant,
            traffic_result=args.traffic_result,
            traffic_root=args.traffic_root,
        )
        print(
            json.dumps(
                dict(
                    status=result['status'],
                    task_id=args.task,
                    run_id=result['run_id'],
                    scenario_id=args.scenario,
                )
            )
        )
        return 0
    except Exception as exc:
        # Keep the CLI boundary: one JSON failure and exit 2, with the original code.
        print(json.dumps(dict(status='failed', code=getattr(exc, 'code', 'ERROR'), error=str(exc))))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
