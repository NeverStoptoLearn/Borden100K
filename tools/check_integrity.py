"""Read-only model, scene and schema checks for source checkouts and CI."""

import json
import sys
from pathlib import Path

# Support `python tools/check_integrity.py` without an editable installation.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jsonschema import Draft202012Validator  # noqa: E402

from phase1.contracts import ROOT, sha, validate  # noqa: E402
from phase1.scenarios.registry import catalog, get_scene, read_asset  # noqa: E402


def check_integrity() -> dict:
    model_hashes = json.loads((ROOT / 'MODEL_SHA256.json').read_text(encoding='utf8'))
    if set(model_hashes) != {'borden3d_common.py', 'borden3d_config.json'}:
        raise ValueError('Unexpected protected-model inventory')
    for name, expected in model_hashes.items():
        if sha(ROOT / name) != expected:
            raise ValueError('Model SHA mismatch: ' + name)

    for name in ('phase1-v1', 'task-release-v1'):
        schema = json.loads((ROOT / f'schemas/{name}.schema.json').read_text(encoding='utf8'))
        Draft202012Validator.check_schema(schema)
    examples = sorted((ROOT / 'schemas/examples').glob('*.json'))
    for path in examples:
        validate(json.loads(path.read_text(encoding='utf8')))

    anchors = json.loads((ROOT / 'SCENARIO_PROVENANCE.json').read_text(encoding='utf8'))
    sources = {(row['scenario_id'], row['release_version']): row for row in anchors}
    identities = []
    assets = set()
    rows = catalog()['scenes']
    expected_ids = {'borden_reference_100k', 'synthetic_multiterrain_100k'}
    if len(rows) != 2 or {row['scenario_id'] for row in rows} != expected_ids:
        raise ValueError('Scene catalog identity mismatch')
    for row in rows:
        ident, version = row['scenario_id'], row['scenario_version']
        scene = get_scene(ident, version)
        manifest = scene['manifest']
        anchor = sources[(ident, version)]
        if anchor['release_manifest_sha256'] != row['manifest']['sha256']:
            raise ValueError('Scene provenance SHA mismatch: ' + ident)
        for ref in [
            row['manifest'],
            *manifest['assets'].values(),
            manifest['science']['code'],
            manifest['science']['config'],
        ]:
            if ref is not None:
                read_asset(ref)
                assets.add(ref['path'])
        identities.append(
            dict(
                scenario_id=ident,
                scenario_version=version,
                coordinate_frame=manifest['coordinates']['coordinate_frame'],
                manifest_sha256=row['manifest']['sha256'],
            )
        )
    return dict(
        status='PASS',
        model_files=len(model_hashes),
        schema_files=2,
        schema_examples=len(examples),
        scene_assets=len(assets),
        scenes=identities,
    )


if __name__ == '__main__':
    print(json.dumps(check_integrity(), indent=2))
