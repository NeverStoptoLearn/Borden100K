"""Seal checked-in scene inputs with the certified existing scientific model."""
import json,subprocess,sys,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def save(path,value):
 path=ROOT/path;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
def ref(path):return dict(path=path,sha256=hashlib.sha256((ROOT/path).read_bytes()).hexdigest(),media_type='application/xml' if path.endswith('.xml') else 'text/x-python' if path.endswith('.py') else 'application/json')
def main():
 if (ROOT/'phase1/scenarios/catalog.json').exists():raise SystemExit('Refusing to overwrite a published scene version; use a new version and review')
 probe="from phase1.hydro_adapter.worker import capability,load_engine;from phase1.hydro_adapter.adapter import ROOT;import json;b=capability('smoke');s=load_engine(b).BordenScene(profile='smoke',config=json.loads((ROOT/'borden3d_config.json').read_text('utf-8')));print(json.dumps(dict(build=b,signature=s.signature,xyz=[s.xyz[i].tolist() for i in [100,101]])))"
 science=json.loads(subprocess.check_output([sys.argv[1],'-B','-c',probe],cwd=ROOT,text=True,encoding='utf-8'))
 rows=[]
 for ident,synthetic in [('borden_reference_100k',False),('synthetic_multiterrain_100k',True)]:
  base='phase1/scenarios/'+ident+'/v1/';traffic='phase1/traffic/scenarios/'+ident+'/v1/'
  source=dict(source_id='model-defaults-v1',reference='Original certified Borden3D synthetic uncalibrated scientific configuration; no field calibration',license='project scientific input',synthetic=True)
  sources=[source]
  if synthetic:
   sources.insert(0,json.loads((ROOT/traffic/'source.json').read_text('utf-8')))
   display=traffic+'display.layers.json';net=ref(traffic+'network.net.xml')
   params={mode:dict(scenario=dict(id=ident,version=1),risk_window_start_day=0.,risk_window_stop_day=21900.,network_path='scenarios/'+ident+'/v1/network.net.xml',traffic=json.loads((ROOT/traffic/'traffic.template.json').read_text('utf-8')),resources=json.loads((ROOT/traffic/'resources.json').read_text('utf-8'))) for mode in ('normal','closure')}
   extent=[0,0,100000,100000]
  else:
   sources.insert(0,dict(source_id='fcsi-11022032',reference='docs/roads/site11022032/REPORT.md; official registry point only, shared location, not survey or model georeference',license='Open Government Licence - Canada',synthetic=False))
   aoi=json.loads((ROOT/'phase1/traffic/data/borden_service_aoi.json').read_text('utf-8'));x,y=aoi['center_projected_m'];extent=[x-50000,y-50000,x+50000,y+50000]
   d=dict(display_version='dual-display-v1',scenario_id=ident,scenario_version=1,coordinate_frame='wgs84_projected',extent_m=extent,terrain=None,rivers=[],bridges=[],cities=[],depots=[],task_locations=[],wells=[],vehicles=[],roads=[],hydro_placement=None)
   display=base+'display.layers.json';save(display,d);net=None;params=dict(normal=None,closure=None)
  params['no_pump']=dict(scenario=dict(id=ident,version=1),risk_window_start_day=0.,risk_window_stop_day=21900.,network_path=None,traffic=None,resources=None)
  scenario=json.loads((ROOT/'schemas/examples/scenario.json').read_text('utf-8'))
  scenario.update(scenario_id=ident,scenario_version=1,seed=20261109,experiment_id='dual-scenes-v1',model_build=science['build'],data_provenance=dict(classification='synthetic' if synthetic else 'mixed',calibration='none',fixture=False,sources=sources))
  scenario['payload']['wells']=[dict(well_id='well-'+str(j+1),cell_id=100+j,synthetic=True,position=dict(zip(('x_local_m','y_local_m','z_local_m'),xyz))) for j,xyz in enumerate(science['xyz'])]
  save(base+'scenario.template.json',scenario);save(base+'parameters.template.json',params)
  gates={mode:dict(allowed=(mode=='no_pump'),blockers=[] if mode=='no_pump' else ['TARGET_ROAD_ACCEPTANCE_NOT_RUN' if not synthetic else 'ACCEPTANCE_NOT_RUN']) for mode in ('normal','closure','no_pump')}
  manifest=dict(manifest_version='dual-scenes-v1',scenario_id=ident,scenario_version=1,title='多地形合成场景' if synthetic else 'Borden 参考场景',seed=20261109,synthetic=synthetic,coordinates=dict(coordinate_frame='synthetic_local_m' if synthetic else 'wgs84_projected',projected_crs=None if synthetic else 'EPSG:32617',extent_m=extent,anchor_wgs84=None if synthetic else dict(lon=-79.9064,lat=44.2347)),availability=gates,sources=sources,science=dict(code=ref('borden3d_common.py'),config=ref('borden3d_config.json'),profile='smoke',model_signature=science['signature']),assets=dict(network=net,scenario_template=ref(base+'scenario.template.json'),parameters_template=ref(base+'parameters.template.json'),display=ref(display)))
  save(base+'manifest.json',manifest)
  summary={k:manifest[k] for k in ('scenario_id','scenario_version','title','seed','synthetic','availability')};summary.update(manifest_sha256=ref(base+'manifest.json')['sha256'],coordinate_frame=manifest['coordinates']['coordinate_frame'])
  rows.append(dict(scenario_id=ident,scenario_version=1,manifest=ref(base+'manifest.json'),summary=summary))
 save('phase1/scenarios/catalog.json',dict(catalog_version='dual-scenes-v1',scenes=rows))
 print(json.dumps(dict(science=science,scenarios=[r['scenario_id'] for r in rows])))
if __name__=='__main__':main()
