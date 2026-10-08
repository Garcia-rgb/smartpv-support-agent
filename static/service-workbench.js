/* Local file comparison. File contents never enter the chat/model endpoint. */
(() => {
  const style = document.createElement('style');
  style.textContent = `.service-screen{position:fixed;inset:18px;z-index:80;background:var(--panel);border:1px solid var(--line);border-radius:16px;box-shadow:var(--shadow);overflow:auto;padding:20px;max-width:1100px;margin:auto}.service-screen h2{margin:0 0 12px}.service-screen label{display:block;font-size:13px;margin:10px 0}.service-screen input,.service-screen textarea,.service-screen select{display:block;width:100%;padding:9px;border:1px solid var(--line);border-radius:8px;margin-top:5px;font:inherit}.service-screen textarea{min-height:72px;resize:vertical}.service-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.service-actions{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}.service-screen table{width:100%;border-collapse:collapse;font-size:13px}.service-screen td,.service-screen th{text-align:left;border-bottom:1px solid var(--line);padding:8px;overflow-wrap:anywhere}.service-note{font-size:13px;color:var(--muted)}.service-result{border:1px solid var(--line);padding:14px;border-radius:10px;margin:12px 0;white-space:normal}.service-screen .err{white-space:pre-wrap}.service-screen .close{float:right}@media(max-width:650px){.service-screen{inset:6px;padding:12px}.service-grid{grid-template-columns:1fr}}`;
  document.head.appendChild(style);
  const button = document.createElement('button');
  button.type = 'button'; button.className = 'chip'; button.textContent = '文件数据核对';
  chipBox.appendChild(button);
  let screen = null;
  const jsonPost = (path, body) => api(path, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  function saveFile(text, filename) {
    const url = URL.createObjectURL(new Blob([text], {type:'text/plain;charset=utf-8'}));
    const a = document.createElement('a'); a.href=url; a.download=filename; a.click();
    setTimeout(() => URL.revokeObjectURL(url),1000);
  }
  function shell() {
    if (screen) screen.remove();
    screen = document.createElement('section'); screen.className='service-screen';
    screen.setAttribute('role','dialog');screen.setAttribute('aria-modal','true');
    screen.innerHTML='<button class="ghost close" id="svcClose">关闭</button><h2>文件数据核对</h2>'+
      '<div class="err" id="svcError" role="alert"></div><div id="svcBody"></div>';
    document.body.appendChild(screen);
    $('svcClose').onclick=()=>{screen.remove();screen=null;button.focus();};
    return $('svcBody');
  }
  async function action(control, job) {
    $('svcError').textContent='';control.disabled=true;
    try {await job();} catch(error){if ($('svcError')) $('svcError').textContent=error.message;}
    finally {control.disabled=false;}
  }
  button.onclick=()=>showFiles();
  function showFiles() {
    const body=shell();const tables={};
    body.innerHTML='<h3>对照导出数据、点表或现场读数文件</h3><p class="service-note">仅在本机处理。每份文件最多2000行/100列，首行须为表头。选择相同含义的匹配字段；不自动推测地址基数或转换单位。</p>'+
      '<div class="service-grid">'+['left','right'].map((key,i)=>'<div><h4>'+(i?'待核对文件':'参考文件')+'</h4><label>CSV / XLSX<input type="file" id="dcFile_'+key+'" accept=".csv,.xlsx"></label><div id="dcMap_'+key+'"></div></div>').join('')+'</div>'+
      '<label>允许绝对误差（乘倍率后的数值）<input id="dcTolerance" value="0.01" maxlength="40"></label><button class="primary" id="dcCompare">开始核对</button><div id="dcResult"></div>';
    async function read(key,sheet='') {
      const file=$('dcFile_'+key).files[0]; if (!file) return;
      const form=new FormData();form.append('file',file);form.append('sheet',sheet);
      const table=await api('/data-check/read',{method:'POST',body:form});tables[key]=table;
      const choices=(empty)=> (empty?'<option value="">不使用</option>':'')+table.columns.map(c=>'<option value="'+esc(c)+'">'+esc(c)+'</option>').join('');
      $('dcMap_'+key).innerHTML=(table.sheets.length?'<label>工作表<select id="dcSheet_'+key+'">'+table.sheets.map(s=>'<option '+(s===table.sheet?'selected':'')+'>'+esc(s)+'</option>').join('')+'</select></label>':'')+
        '<p class="service-note">已读取'+table.rows.length+'行。'+esc(table.warnings.join(' '))+'</p>'+
        [['key','匹配字段'],['value','数值列'],['unit','单位列（可选）'],['gain','倍率列（可选）']].map(([field,label])=>'<label>'+label+'<select id="dc_'+key+'_'+field+'">'+choices(['unit','gain'].includes(field))+'</select></label>').join('')+
        '<label>数据采集时间（请按文件填写）<input id="dcTime_'+key+'" maxlength="80" placeholder="未提供时不会当成实时数据"></label><details><summary>前3行预览</summary><pre style="white-space:pre-wrap">'+esc(JSON.stringify(table.rows.slice(0,3),null,2))+'</pre></details>';
      $('dc_'+key+'_value').selectedIndex=Math.min(1,table.columns.length-1);
      if($('dcSheet_'+key))$('dcSheet_'+key).onchange=()=>action($('dcSheet_'+key),()=>read(key,$('dcSheet_'+key).value));
      $('dcResult').innerHTML='';
    }
    ['left','right'].forEach(key=>$('dcFile_'+key).onchange=()=>{
      delete tables[key];$('dcMap_'+key).innerHTML='';
      action($('dcFile_'+key),()=>read(key));
    });
    $('dcCompare').onclick=()=>action($('dcCompare'),async()=>{
      if(!tables.left||!tables.right)throw new Error('请先读取两份文件');
      const payload={tolerance:$('dcTolerance').value};
      ['left','right'].forEach(key=>{const t=tables[key];payload[key]={name:t.name+(t.sheet?' / '+t.sheet:''),rows:t.rows,data_time:$('dcTime_'+key).value};
        ['key','value','unit','gain'].forEach(field=>payload[key][field+'_column']=$('dc_'+key+'_'+field).value);});
      const result=await jsonPost('/data-check/compare',payload);
      $('dcResult').innerHTML='<h3>有效数值对照 '+result.compared+' 项，发现 '+result.total_issues+' 项差异 / 待确认项</h3><p class="service-note">'+esc(result.note)+'</p>'+
        result.sources.map(s=>'<p>来源：'+esc(s.name)+'；数据时间：'+esc(s.data_time)+'</p>').join('')+
        '<div style="overflow:auto"><table><thead><tr><th>类型</th><th>匹配字段</th><th>说明</th></tr></thead><tbody>'+result.issues.map(i=>'<tr><td>'+esc(i.kind)+'</td><td>'+esc(i.key)+'</td><td>'+esc(i.detail)+'</td></tr>').join('')+'</tbody></table></div>'+
        (result.truncated?'<p>显示前200项，请缩小文件范围继续核对。</p>':'')+'<button class="ghost" id="dcExport">下载核对报告</button>';
      $('dcExport').onclick=()=>saveFile('# 文件核对报告\n\n'+result.note+'\n\n'+result.sources.map(s=>'来源：'+s.name+'；数据时间：'+s.data_time).join('\n')+
        '\n\n有效数值对照：'+result.compared+'\n差异/待确认项：'+result.total_issues+'\n汇总：'+JSON.stringify(result.counts)+'\n\n'+result.issues.map(i=>'- '+i.kind+' | '+i.key+' | '+i.detail).join('\n')+
        (result.truncated?'\n\n详情仅前200项。':''),'文件核对报告.md');
    });
  }
})();
