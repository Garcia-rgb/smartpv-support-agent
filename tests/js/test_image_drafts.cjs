const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('static/index.html', 'utf8');
class Element {
  constructor() { this.children = []; this.value = ''; this.nodes = {}; }
  set innerHTML(value) { this.html = value; this.children = []; }
  get innerHTML() { return this.html || ''; }
  appendChild(child) { this.children.push(child); }
  append(...children) { this.children.push(...children); }
  querySelector(key) { return this.nodes[key] ||= new Element(); }
  addEventListener(key, fn) { this.nodes[key] = fn; }
  focus() {}
}
const ids = {};
let requests = [], fail = false, objectId = 0, failedMessage;
const context = {
  state: {pendingImages:[], sessionId:null, busy:false},
  $: id => ids[id] ||= new Element(),
  input: new Element(), wrap: new Element(),
  document: {createElement: () => new Element()},
  URL: {createObjectURL:()=>`blob:${++objectId}`, revokeObjectURL:()=>{}},
  FormData: class { constructor(){this.entries=[];} append(...pair){this.entries.push(pair);} },
  api: async (path, options) => {
    assert.equal(context.input.value,'');
    assert.equal(context.state.pendingImages.length,0);
    requests.push({path, entries:options.body.entries});
    if(fail) throw new Error('模拟网络故障');
    return {kind:'chat', recognized_text:'联合识别文字', chat:{session_id:'s1',answer:'回答'}};
  },
  addUser:()=>{}, addTyping:()=>{}, clearTyping:()=>{},
  addBot:()=>{failedMessage=new Element();return failedMessage;}, addRecognitionEdit:()=>{}, addQuizResult:()=>{},
  loadSessions:()=>{}, autoGrow:()=>{}, esc:s=>s,
  rememberSession:id=>{context.state.sessionId=id;},
};
context.wrap.querySelector=()=>null;
vm.createContext(context);
vm.runInContext(html.slice(html.indexOf('const MAX_PENDING_IMAGES'),html.indexOf('function autoGrow()')),context);
const bindingStart=html.indexOf("$('sendBtn').onclick =");
vm.runInContext(html.slice(bindingStart,html.indexOf("input.addEventListener('input', autoGrow);",bindingStart)),context);
const picture=n=>({name:n+'.png',type:'image/png',size:50});
(async()=>{
  let prevented=false;
  context.input.nodes.paste({clipboardData:{files:[picture('1'),picture('2')]},preventDefault(){prevented=true;}});
  assert(prevented);assert.equal(requests.length,0);assert.equal(context.state.pendingImages.length,2);
  context.input.nodes.paste({clipboardData:{files:[picture('3')]},preventDefault(){}});
  assert.equal(requests.length,0);assert.equal(context.state.pendingImages.length,3);
  const cards=context.$('imageDrafts').children[1];cards.children[1].children[1].onclick();
  assert.equal(context.state.pendingImages.length,2);
  context.input.value='一起看，现场有人';
  await context.$('sendBtn').onclick();
  assert.equal(requests.length,1);
  assert.equal(requests[0].entries.filter(([key])=>key==='files').length,2);
  assert(requests[0].entries.some(([key,value])=>key==='message'&&value==='一起看，现场有人'));
  assert.equal(context.state.pendingImages.length,0);assert.equal(context.input.value,'');
  context.queueImages([picture('retry')]);context.input.value='重试文字';fail=true;
  await context.send();
  assert.equal(context.state.pendingImages.length,0);assert.equal(context.input.value,'');
  assert.equal(context.state.busy,false);
  const retry=failedMessage.querySelector('.bubble').children[0];
  context.input.value='新的问题';retry.onclick();assert.equal(context.input.value,'新的问题');
  context.input.value='';retry.onclick();
  assert.equal(context.state.pendingImages.length,1);assert.equal(context.input.value,'重试文字');
  console.log('发送立即清空、失败可恢复重试、不覆盖新草稿：全部通过');
})().catch(error=>{console.error(error);process.exitCode=1;});
