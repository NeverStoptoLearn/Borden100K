let pending;
export function loadGoogle({key,enabled,onError=()=>{}}, win=window, doc=document) {
  if (!enabled || !key) return Promise.reject(Error('Google 3D 未启用：请配置环境变量并完成服务审核。'));
  if (win.google?.maps?.importLibrary) return win.google.maps.importLibrary('maps3d');
  if (pending) return pending;
  pending=new Promise((resolve,reject)=>{
    const script=doc.createElement('script');
    const finish=(error)=>{clearTimeout(timer); delete win.__bordenGoogleReady; if(error){script.remove();pending=undefined;reject(error);} else win.google.maps.importLibrary('maps3d').then(resolve,reject);};
    const timer=setTimeout(()=>finish(Error('Google 3D 加载超时，请检查网络与 API 配置。')),20000);
    win.__bordenGoogleReady=()=>finish();
    win.gm_authFailure=()=>{const error=Error('Google API 授权失败，请检查 Key / API / 账单 / referrer 限制。');onError(error);finish(error);};
    script.async=true;
    script.src=`https://maps.googleapis.com/maps/api/js?${new URLSearchParams({key,v:'weekly',loading:'async',callback:'__bordenGoogleReady'})}`;
    script.onerror=()=>finish(Error('Google 3D 网络加载失败。'));
    doc.head.append(script);
  });
  return pending;
}
export function createScene(api,host,center,onReady,onError) {
  const map=new api.Map3DElement({center:{lat:center.lat,lng:center.lon,altitude:0},range:170000,tilt:40,heading:15,mode:'SATELLITE'});
  const ready=e=>{if(e.isSteady) onReady();};
  map.addEventListener('gmp-steadychange',ready);
  map.addEventListener('gmp-error',onError);
  host.replaceChildren(map);
  let overlays=[];
  return {
    draw({boundary,markers,paths}) {
      overlays.forEach(o=>o.remove()); overlays=[];
      const add=o=>{map.append(o);overlays.push(o);};
      const path=p=>p.map(p=>({lat:p.lat,lng:p.lon,altitude:0}));
      if(boundary.length) add(new api.Polyline3DElement({path:path(boundary),strokeColor:'#63e4d0',strokeWidth:4,altitudeMode:'CLAMP_TO_GROUND'}));
      paths.map(p=>Array.isArray(p)?{points:p,color:'#ffd276'}:p).filter(p=>p.points.length>1).forEach(p=>add(new api.Polyline3DElement({path:path(p.points),strokeColor:p.color,strokeWidth:5,altitudeMode:'CLAMP_TO_GROUND'})));
      markers.forEach(m=>add(new api.Marker3DElement({position:{lat:m.lat,lng:m.lon,altitude:0},label:m.label,altitudeMode:'CLAMP_TO_GROUND'})));
    },
    focus(center,range=4500) { map.center={lat:center.lat,lng:center.lon,altitude:0};map.range=range;map.tilt=60; },
    dispose(){map.removeEventListener('gmp-steadychange',ready);map.removeEventListener('gmp-error',onError);map.remove();}
  };
}
