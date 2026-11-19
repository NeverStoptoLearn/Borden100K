"""Local-only KML/KMZ export CLI. Never invokes an engine or network service."""
import argparse
import json
from pathlib import Path
from . import export_run
from .fixtures import build_fixture


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--bundle',type=Path,help='JSON with scenario/run_status/parameters/artifact_root/view_metadata')
    group.add_argument('--fixture-demo',type=Path,help='Create a NEW separately identified fixture source directory, not real engine output')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--profile',choices=['earth_web','earth_pro'],default='earth_web')
    parser.add_argument('--allow-fixture',action='store_true',help='Test channel only; unavailable in production HTTP')
    args=parser.parse_args()
    if args.fixture_demo and not args.allow_fixture:parser.error('--fixture-demo requires --allow-fixture')
    bundle=build_fixture(args.fixture_demo) if args.fixture_demo else json.loads(args.bundle.read_text('utf-8'))
    result=export_run(**bundle,output_dir=args.output,profile=args.profile,allow_fixture=args.allow_fixture)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
