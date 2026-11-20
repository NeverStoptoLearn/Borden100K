import json
import os
from pathlib import Path
import tempfile
import unittest
from borden_tasks.core import ROOT,SCENES,scene,run,sha,TASKS


class TaskReleaseTests(unittest.TestCase):
    def test_two_isolated_scene_identities_and_integrity(self):
        a,af=scene(SCENES[0]);b,bf=scene(SCENES[1])
        self.assertNotEqual(af,bf);self.assertFalse(a['synthetic']);self.assertTrue(b['synthetic'])
        self.assertEqual(a['coordinates']['projected_crs'],'EPSG:32617');self.assertIsNone(b['coordinates']['projected_crs'])
        self.assertIsNone(a['assets']['network']);self.assertIsNotNone(b['assets']['network'])
        self.assertFalse(a['availability']['normal']['allowed'])

    def test_scientific_kernel_bytes_remain_frozen(self):
        original=json.loads((ROOT/'MODEL_SHA256.json').read_text(encoding='utf8'))
        for name in ['borden3d_common.py','borden3d_config.json']:
            self.assertEqual(sha(ROOT/name),original[name])
        self.assertEqual(set(original),{'borden3d_common.py','borden3d_config.json'})
        self.assertEqual(original['borden3d_common.py'],'6a1fcf8ddf67f61654b3c6dbe4b6c67258e6908b0d2d131a5f638ebfb0005275')
        self.assertEqual(original['borden3d_config.json'],'5774c92376b878f3a88841f8a36c204bf63e6334154b699e1ba572f8acc0ff48')

    def test_experimental_tasks_refuse_to_execute(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'out'
            with self.assertRaises(ValueError):run('hydro_traffic_coupled_optimization',SCENES[1],p)
            self.assertFalse(p.exists())

    def test_official_admission_stays_blocked(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'out'
            with self.assertRaisesRegex(ValueError,'200000000000'):run('groundwater_forward',SCENES[0],p,mode='official')
            self.assertFalse(p.exists())

    def test_resource_example_is_explicit_fixture_and_create_only(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'out';r=run('resource_scheduling',SCENES[1],p)
            self.assertEqual(r['status'],'succeeded');self.assertTrue(r['engine_result']['data_provenance']['fixture'])
            from phase1.hydro_adapter.adapter import validate
            validate(r['engine_result'])
            self.assertTrue((p/'artifact-index.json').is_file())
            with self.assertRaises(FileExistsError):run('resource_scheduling',SCENES[1],p)

    def test_reference_traffic_is_not_fabricated(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'out'
            with self.assertRaisesRegex(ValueError,'road acceptance'):run('vehicle_routing',SCENES[0],p)
            self.assertEqual(json.loads((p/'error.json').read_text())['status'],'failed')
            self.assertFalse((p/'result.json').exists())

    def test_backend_scene_read_and_health(self):
        with tempfile.TemporaryDirectory() as t:
            os.environ['BORDEN_BACKEND_HOME']=t
            from fastapi.testclient import TestClient
            from phase1.backend.app import create_app
            with TestClient(create_app(t)) as c:
                self.assertEqual(c.get('/api/v1/health').status_code,200)
                r=c.get('/api/v1/scene-catalog')
                self.assertEqual(r.status_code,200)
                values=r.json();self.assertEqual(len(values['scenes']),2)
                tasks=c.get('/api/v1/task-release/tasks').json()
                self.assertEqual(tasks['project_hard_limit_bytes'],200000000000)
                self.assertEqual(set(tasks['tasks']),{'source_identification','groundwater_forward','resource_scheduling','vehicle_routing'})

    def test_public_metadata_has_no_account_paths(self):
        for p in [ROOT/'MODEL_SHA256.json',ROOT/'SCENARIO_PROVENANCE.json']:
            self.assertNotRegex(p.read_text(encoding='utf8'),r'[A-Za-z]:[/\\]Users[/\\]')

    def test_backend_worker_blocks_unapproved_science_before_engine_call(self):
        from unittest.mock import patch
        from phase1.backend.worker import Worker
        from phase1.backend.core import Problem
        with patch.dict(os.environ,{},clear=False):
            os.environ.pop('BORDEN_RELEASE_MAINTENANCE_SMOKE',None)
            with patch('phase1.backend.worker.execute') as execute:
                with self.assertRaises(Problem):Worker(None).pipeline('unused',{}, {})
                execute.assert_not_called()

    def test_catalog_genesis_is_not_an_arbitrary_version_bypass(self):
        from phase1.backend.store import Store
        from phase1.backend.core import Problem
        with tempfile.TemporaryDirectory() as t:
            store=Store(t)
            with self.assertRaises(Problem):store.put('scenario','unregistered',1001,{})
            store.put('scenario','registered',1001,{},initial_version=1001)
            with self.assertRaises(Problem):store.put('scenario','registered',1003,{},initial_version=1003)


    def test_sumo_home_resolves_windows_executable(self):
        from unittest.mock import patch
        from phase1.traffic.engine import binary
        import os
        with tempfile.TemporaryDirectory() as t:
            home=Path(t);(home/'bin').mkdir()
            executable=home/'bin'/('sumo.exe' if os.name=='nt' else 'sumo')
            executable.write_bytes(b'fixture-for-path-discovery-only')
            with patch.dict(os.environ,{'SUMO_HOME':str(home)},clear=False):
                os.environ.pop('SUMO_BINARY',None)
                with patch('phase1.traffic.engine.shutil.which',return_value=None):
                    self.assertEqual(binary('sumo'),str(executable.resolve()))


if __name__=='__main__':unittest.main()
