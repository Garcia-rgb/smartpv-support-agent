/* Offline engineering tools use the authenticated chat path and persistent history. */
let engineeringScreen = null;
function openEngineeringForm(kind) {
  if (state.busy || !state.userId) return;
  if (engineeringScreen) engineeringScreen.remove();
  const frame = kind === 'modbus_parse';
  engineeringScreen = document.createElement('section');
  engineeringScreen.className = 'login-screen engineering-screen';
  engineeringScreen.setAttribute('role', 'dialog');
  engineeringScreen.setAttribute('aria-modal', 'true');
  engineeringScreen.innerHTML = '<form class="login-card" style="max-height:90vh;overflow:auto">'+
    '<h1>'+(frame?'Modbus报文解析':'寄存器数据解码')+'</h1>'+
    '<p>'+(frame?'仅解析已有报文，不连接或操作设备。':'不确认字序时列出候选值，请按厂家协议选择。')+'</p>'+
    (frame?'<label>完整十六进制单帧报文<textarea id="engData" rows="4" maxlength="1500" placeholder="01 03 00 00 00 02 C4 0B" style="width:100%" required></textarea></label>'+
      '<label>传输类型<select id="engTransport"><option value="auto">自动识别</option><option value="rtu">RTU</option><option value="tcp">TCP</option></select></label>'+
      '<label>收发方向<select id="engDirection"><option value="auto">尚未确认</option><option value="request">请求 / 发送</option><option value="response">响应 / 接收</option></select></label>':
      '<label>寄存器原值（逗号分隔，十六进制请加0x）<input id="engData" maxlength="150" placeholder="0x43C8,0x0000" required></label>'+
      '<label>数据类型<select id="engType">'+['FLOAT32','FLOAT64','UINT16','INT16','UINT32','INT32','UINT64','INT64'].map(t=>'<option>'+t+'</option>').join('')+'</select></label>'+
      '<label>寄存器内字节序<select id="engByte"><option value="big">高字节在前（协议标准）</option><option value="little">低字节在前（厂家明确指定）</option><option value="unknown">未确认，列出候选</option></select></label>'+
      '<label>跨寄存器字序<select id="engWord"><option value="unknown">未确认，列出候选</option><option value="high_first">高字在前</option><option value="low_first">低字在前</option></select></label>'+
      '<label>乘数（原值 × 乘数；点表增益若是除数请先换算）<input id="engMultiplier" type="number" step="any" value="1" required></label>'+
      '<label>偏移（相乘后加偏移）<input id="engOffset" type="number" step="any" value="0" required></label>')+
    '<button class="primary" type="submit">'+(frame?'解析报文':'解码')+'</button>'+
    '<button class="ghost" id="engClose" type="button" style="width:100%;margin-top:8px">取消</button></form>';
  document.body.appendChild(engineeringScreen);
  engineeringScreen.querySelectorAll('select').forEach(el=>{el.style.cssText='display:block;width:100%;padding:8px';});
  $('engClose').onclick=()=>{engineeringScreen.remove();engineeringScreen=null;};
  engineeringScreen.querySelector('form').onsubmit=event=>{
    event.preventDefault();
    if (state.busy) return;
    let text;
    if (frame) {
      const transport=$('engTransport').value, direction=$('engDirection').value;
      text='解析Modbus '+(transport==='auto'?'':transport.toUpperCase()+' ')+
        (direction==='request'?'请求':direction==='response'?'响应':'')+'报文：\n'+$('engData').value;
    } else {
      text='寄存器解码：值='+$('engData').value+'；类型='+$('engType').value+
        '；字节序='+$('engByte').value+'；字序='+$('engWord').value+
        '；乘数='+$('engMultiplier').value+'；偏移='+$('engOffset').value;
    }
    engineeringScreen.remove();engineeringScreen=null;
    send(text);
  };
  $('engData').focus();
}
['modbus_parse','register_decode'].forEach(kind=>{
  const button=document.createElement('button');button.type='button';button.className='chip';
  button.textContent=kind==='modbus_parse'?'Modbus报文':'寄存器解码';
  button.onclick=()=>openEngineeringForm(kind);chipBox.appendChild(button);
});
const engineeringOldLogin=showLogin;
showLogin=function(...args){
  if(engineeringScreen){engineeringScreen.remove();engineeringScreen=null;}
  return engineeringOldLogin(...args);
};
