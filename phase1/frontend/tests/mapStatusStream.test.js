import {describe,it,expect} from 'vitest';
import example from '../../../schemas/examples/run_status.json';
import {subscribeMapStatus} from '../src/mapStatusStream';
import {loopbackProxy} from '../vite.config';
describe('existing status websocket only',()=>{
 const run=()=>{const s=structuredClone(example);s.data_provenance.fixture=false;s.status='queued';s.payload.previous_status=null;return s;};
 class Socket{static last;constructor(url){this.url=url;Socket.last=this;}close(){this.closed=true;}message(value){this.onmessage({data:JSON.stringify(value)});}}
 it('uses same-origin WS, validates ordered status and disposes without vehicle data',()=>{const states=[],seen=[],a=run(),stop=subscribeMapStatus(a,{WebSocketClass:Socket,origin:'http://127.0.0.1:5173',onState:s=>states.push(s),onStatus:s=>seen.push(s)}),socket=Socket.last;expect(socket.url).toContain('/events?after=0');socket.onopen();socket.message({sequence:1,envelope:a});socket.message({sequence:1,envelope:a});const b={...structuredClone(a),status:'running',payload:{...a.payload,previous_status:'queued'}};socket.message({sequence:2,envelope:b});const c={...structuredClone(b),status:'succeeded',payload:{...b.payload,previous_status:'running'}};socket.message({sequence:3,envelope:c});expect(seen.map(s=>s.status)).toEqual(['queued','running','succeeded']);expect(states).toContain('CONFIRMED');expect(socket.closed).toBe(true);stop();socket.message({sequence:4,envelope:a});expect(seen).toHaveLength(3);expect(loopbackProxy('http://127.0.0.1:5292')['/api'].ws).toBe(true);});
 it('refuses another run even when a socket sends syntactically valid state',()=>{const states=[],a=run();subscribeMapStatus(a,{WebSocketClass:Socket,origin:'http://localhost:5173',onState:s=>states.push(s)});Socket.last.message({sequence:1,envelope:{...a,scenario_id:'other'}});expect(states.at(-1)).toContain('ERROR:');expect(Socket.last.closed).toBe(true);});
});
