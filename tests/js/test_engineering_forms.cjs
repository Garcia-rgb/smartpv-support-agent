const fs=require('fs'), vm=require('vm'), assert=require('assert');
const elements=new Map(), sent=[];
function element(){
  const el={children:[],style:{},value:'',removed:false,appendChild(c){this.children.push(c);},
    setAttribute(){},focus(){},remove(){this.removed=true;},querySelectorAll(){return [];}};
  Object.defineProperty(el,'innerHTML',{set(html){
    for(const match of html.matchAll(/id="([^"]+)"/g))elements.set(match[1],element());
    this.form=element();
  }});
  el.querySelector=()=>el.form;
  return el;
}
const context={document:{createElement:element,body:element()},state:{busy:false,userId:'alice'},
  chipBox:element(),$:id=>elements.get(id),send:text=>sent.push(text),showLogin:()=>{},
  wrap:element(),fmt:s=>s,stateBlock:()=>null,scroll:()=>{},
  showPointTableForm:()=>elements.set('ptDirection',element())};
vm.createContext(context);
vm.runInContext(fs.readFileSync('static/engineering-tools.js','utf8'),context);
assert.equal(context.chipBox.children.length,2);
context.openEngineeringForm('modbus_parse');
elements.get('engData').value='01 03 00 00 00 02 C4 0B';
elements.get('engTransport').value='rtu';elements.get('engDirection').value='request';
context.document.body.children.at(-1).form.onsubmit({preventDefault(){}});
assert.equal(sent.length,1);assert(sent[0].includes('RTU 请求报文'));assert(sent[0].includes('C4 0B'));
context.openEngineeringForm('register_decode');
for(const [id,value] of Object.entries({engData:'0x43C8,0x0000',engType:'FLOAT32',engByte:'big',
  engWord:'unknown',engMultiplier:'0.1',engOffset:'0'}))elements.get(id).value=value;
context.document.body.children.at(-1).form.onsubmit({preventDefault(){}});
assert.equal(sent.length,2);assert(sent[1].includes('字序=unknown'));assert(sent[1].includes('乘数=0.1'));
context.openEngineeringForm('modbus_parse');const old=context.document.body.children.at(-1);
context.showLogin();assert(old.removed);context.state.busy=true;
const count=context.document.body.children.length;context.openEngineeringForm('modbus_parse');
assert.equal(context.document.body.children.length,count);
context.state.busy=false;
const html=fs.readFileSync('static/index.html','utf8');
vm.runInContext(html.slice(html.indexOf('function addBot('),html.indexOf('function addQuizResult(')),context);
context.addBot({answer:'点表制作：北向，上传设备协议Word/PDF。',next_action:'point_table'});
context.wrap.children.at(-1).children[0].children[0].onclick();
assert.equal(elements.get('ptDirection').value,'north');
console.log('两种工具参数表单、统一聊天提交、注销清理、北向点表入口：全部通过');
