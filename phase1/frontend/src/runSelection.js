const modes=['normal','closure','no_pump'];
export function modeOfRun(run){
 if(modes.includes(run.payload?.mode))return run.payload.mode;
 const parameter=run.parameter_version??'';
 return modes.find(mode=>parameter===mode||parameter.startsWith(mode+'.')||parameter.includes('.'+mode+'.')||parameter.endsWith('.'+mode))??null;
}
export function defaultReplayRun(rows){const succeeded=rows.filter(run=>run.status==='succeeded');return succeeded.find(run=>modeOfRun(run)==='normal')??succeeded.find(run=>modeOfRun(run)==='closure')??succeeded[0];}
export function modeLabel(run){return {normal:'正常道路',closure:'封路',no_pump:'无抽水（无交通调度）'}[modeOfRun(run)]??'运行（模式未记录）';}
