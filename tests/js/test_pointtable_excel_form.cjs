const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const html = fs.readFileSync('static/index.html', 'utf8'), ids = new Map(), calls = [], downloads = [];
class Element {
  constructor(tag = 'div') { this.tag = tag; this.children = []; this.dataset = {}; this.value = ''; this.style = {}; }
  appendChild(child) { child.parent = this; this.children.push(child); }
  set innerHTML(value) {
    this.html = value; this.children = [];
    for (const match of value.matchAll(/<([a-z]+)[^>]*id="([^"]+)"/g)) ids.set(match[2], new Element(match[1]));
  }
  get innerHTML() { return this.html || ''; }
  querySelectorAll(selector) {
    const descendants = [];
    const visit = node => { for (const child of node.children) { descendants.push(child); visit(child); } };
    visit(this);
    if (selector === '[data-key]') return descendants.filter(el => el.dataset.key);
    if (selector === '[data-field]') return descendants.filter(el => el.dataset.field);
    if (selector === 'tbody tr') return descendants.filter(el => el.tag === 'tr' && el.parent?.tag === 'tbody');
    return descendants.filter(el => el.tag === selector);
  }
  querySelector() { return new Element('button'); }
  click() { downloads.push(this.download); }
}
const fixture = {
  direction: 'south', source_name: 'sample.xlsx', protocol: '104', common_fields: [], suggested_fields: {}, warnings: [],
  devices: [{通道编号:'CH1',设备类型:'通用设备',设备型号:'DEV',厂商名称:'测试',设备编码:'DEV1',协议类型:'104',电站编号:'测试电站'}],
  points: [{name:'电流',address:'70001',function:'遥测',data_type:'FLOAT',count:'1',gain:'0.1',offset:'0',
    unit:'A',persistence:'0',bit:'',dictionary:'',device_code:'DEV1'}]
};
let valid = true, uploadedNames = [];
const context = {
  document: { createElement: tag => new Element(tag) }, workflowPanel: new Element(),
  $: id => ids.get(id), closeWorkflow: () => {}, state:{csrfToken:'test-csrf'},
  FormData, File, Blob, setTimeout: () => {}, window:{confirm:() => true},
  URL:{createObjectURL:() => 'blob:review', revokeObjectURL:() => {}},
  api:async (path, options) => {
    if (path === '/point-tables/parse') {
      uploadedNames = [options.body.get('file').name];
      return structuredClone(fixture);
    }
    calls.push(JSON.parse(options.body));
    return {valid,output_points:1,errors:valid?[]:[{row:1,message:'待补充'}],warnings:[]};
  },
  fetch:async (path, options) => { calls.push(JSON.parse(options.body)); return {ok:true,blob:async()=>new Blob(['test'])}; },
};
vm.createContext(context);
vm.runInContext(html.slice(html.indexOf('async function showPointTableForm()'),html.indexOf('function closeWorkflow()')),context);
(async () => {
  await context.showPointTableForm();
  ids.get('ptFile').files = [new File(['test'],'sample.xlsx')];
  await ids.get('ptParse').onclick();
  assert(!ids.get('ptHead').innerHTML.includes('寄存器类型')); // 104 never renders Modbus controls.
  const inputs=ids.get('ptDevices').querySelectorAll('[data-field]');
  inputs.find(el=>el.dataset.field==='设备编码').value='DEV2';
  ids.get('ptRows').querySelectorAll('[data-key]').find(el=>el.dataset.key==='device_code').value='DEV2';
  await ids.get('ptReview').onclick();
  assert.equal(calls.at(-1).output_format,'xlsx');
  assert.equal(calls.at(-1).devices[0]['设备编码'],'DEV2');
  assert.equal(calls.at(-1).points[0].gain,'0.1');
  assert.equal(calls.at(-1).points[0].persistence,'0');
  assert(downloads.at(-1).includes('104_核对版.xlsx'));
  valid=false;
  const priorDownloads=downloads.length;
  await ids.get('ptGenerate').onclick();
  assert.equal(downloads.length,priorDownloads); // Invalid data cannot escape as import CSV.
  await ids.get('ptReview').onclick();
  assert(downloads.at(-1).includes('待确认核对版.xlsx'));
  fixture.protocol='MODBUS-RTU'; valid=true;
  await ids.get('ptParse').onclick();
  assert(ids.get('ptHead').innerHTML.includes('寄存器类型'));
  fixture.protocol = '多协议'; fixture.merged = true; fixture.devices = [];
  fixture.points[0]['_device_协议类型'] = '104';
  fixture.points[0]['_device_设备编码'] = 'DEV1';
  ids.get('ptFile').files = [new File(['mixed'],'南向数据.csv')];
  await ids.get('ptParse').onclick();
  assert.deepEqual(uploadedNames, ['南向数据.csv']);
  assert(ids.get('ptHead').innerHTML.includes('协议类型'));
  await ids.get('ptGenerate').onclick();
  assert.equal(calls.at(-1).points[0]['_device_协议类型'], '104');
  assert(downloads.at(-1).includes('多协议.csv'));
  console.log('Excel上传、设备编辑、104/Modbus分开显示、增益零值保留、核对版与CSV校验：全部通过');
})().catch(error=>{console.error(error);process.exitCode=1;});
