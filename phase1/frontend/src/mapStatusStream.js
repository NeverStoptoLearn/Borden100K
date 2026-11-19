import {sameRun,statusUpdate} from './contract';
// Only job status events. This socket never supplies vehicle positions or concentrations.
export function subscribeMapStatus(run,{onStatus=()=>{},onState=()=>{},WebSocketClass=WebSocket,origin=location.origin}={}){
 const base=new URL(origin);base.protocol=base.protocol==='https:'?'wss:':'ws:';base.pathname=`/api/v1/runs/${encodeURIComponent(run.run_id)}/events`;base.search='after=0';
 const socket=new WebSocketClass(base.href);let previous=null,sequence=0,disposed=false;
 socket.onopen=()=>{if(!disposed)onState('CONNECTED');};
 socket.onmessage=event=>{if(disposed)return;try{const item=JSON.parse(event.data);if(!Number.isSafeInteger(item.sequence)||item.sequence<=sequence)return;sameRun(run,item.envelope);previous=statusUpdate(previous,item.envelope);sequence=item.sequence;onStatus(previous);if(['succeeded','failed','cancelled'].includes(previous.status)){onState('CONFIRMED');socket.close();}}catch(e){onState('ERROR: '+e.message);socket.close();}};
 socket.onerror=()=>{if(!disposed)onState('UNAVAILABLE · HTTP状态仍有效');};
 return ()=>{disposed=true;socket.close();};
}
