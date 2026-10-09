const assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm');
const html=fs.readFileSync('static/index.html','utf8');
class Element {
  constructor(){this.children=[];}
  set innerHTML(value){this.html=value;this.children=[];}
  appendChild(child){this.children.push(child);}
  remove(){}
  focus(){}
}
const storage=new Map(), ids={}, seen=[];
const sessions=[{id:'old',user_id:'alice',title:'设备断联',created_at:'2026-10-08'},
                {id:'new',user_id:'alice',title:'数据不刷新',created_at:'2026-10-08'}];
const context={state:{userId:'alice',sessionId:null,busy:false},
  $: id=>ids[id] ||= new Element(), wrap:new Element(),
  document:{createElement:()=>new Element()},
  localStorage:{getItem:key=>storage.has(key)?storage.get(key):null,setItem:(key,value)=>storage.set(key,value)},
  api:async (path,options)=>{
    if(path==='/sessions' && options?.method==='POST'){
      const item={id:'empty-created',user_id:'alice',title:'新对话',created_at:'2026-10-09'};
      sessions.unshift(item); return item;
    }
    if(path.startsWith('/sessions?'))return {items:sessions,total:45};
    if(path==='/sessions/empty-created')return {messages:[]};
    if(path==='/sessions/old'||path==='/sessions/new')return {messages:[{role:'user',content:path,images:[]}]};
    throw new Error('记录不存在');
  }, clearImageDrafts:()=>{}, addUser:text=>seen.push(text), addBot:()=>{},showEmpty:()=>seen.push('empty'),esc:s=>s,
  input:new Element(),autoGrow:()=>{},closeHistorySidebar:()=>{},
};
vm.createContext(context);
vm.runInContext(html.slice(html.indexOf('function lastSessionKey()'),html.indexOf('async function checkHealth()')),context);
(async()=>{
  context.rememberSession('old');context.state.sessionId=null; // Simulated page restart.
  await context.restoreLastConversation();assert.equal(context.state.sessionId,'old');
  await context.createConversation();assert.equal(context.state.sessionId,'empty-created');
  assert(context.$('sessionList').children.some(row=>row.title==='新对话'));
  await context.openSession('old');
  await context.openSession('empty-created');assert.equal(seen.at(-1),'empty');
  context.state.sessionId=null;await context.restoreLastConversation();
  assert.equal(context.state.sessionId,'empty-created');
  assert(seen.includes('/sessions/old'));
  assert(context.$('sessionList').children.some(row=>row.textContent==='加载更早的对话'));
  context.rememberSession(null);await context.restoreLastConversation();assert.equal(seen.at(-1),'empty');
  context.state.userId='bob';context.state.sessionId=null;await context.restoreLastConversation();
  assert.equal(context.state.sessionId,null); // Alice's remembered history cannot open for Bob.
  context.state.userId='alice';storage.delete(context.lastSessionKey());
  await context.restoreLastConversation();assert.equal(context.state.sessionId,'empty-created');
  console.log('按账号恢复、新建立即入库显示、切换和重开空会话：全部通过');
})().catch(error=>{console.error(error);process.exitCode=1;});
