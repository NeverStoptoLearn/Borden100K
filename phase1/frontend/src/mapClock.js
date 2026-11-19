export function createScenarioClock({min_s=0,max_s=1,initial_s=min_s,speed=1}={}){
  const number=x=>typeof x==='number'&&Number.isFinite(x);
  if(![min_s,max_s,initial_s,speed].every(number)||max_s<min_s||initial_s<min_s||initial_s>max_s||speed<=0)throw Error('Invalid scenario clock');
  let time=initial_s,playing=false,last=null,disposed=false;
  const snapshot=()=>({scenario_time_s:time,min_s,max_s,speed,playing});
  const alive=()=>{if(disposed)throw Error('Scenario clock disposed');};
  return {snapshot,tick(nowMs){alive();if(!number(nowMs))throw Error('Invalid wall time');if(playing&&last!==null){if(nowMs<last)throw Error('Wall clock reversed');time=Math.min(max_s,time+(nowMs-last)/1000*speed);if(time===max_s)playing=false;}last=nowMs;return snapshot();},play(nowMs){alive();if(nowMs!==undefined&&!number(nowMs))throw Error('Invalid wall time');playing=time<max_s;last=nowMs??null;return snapshot();},pause(){alive();playing=false;last=null;return snapshot();},seek(t){alive();if(!number(t)||t<min_s||t>max_s)throw Error('Scenario seek out of bounds');time=t;last=null;if(t===max_s)playing=false;return snapshot();},setSpeed(value){alive();if(!number(value)||value<=0)throw Error('Invalid scenario speed');speed=value;last=null;return snapshot();},dispose(){playing=false;last=null;disposed=true;return snapshot();}};
}
