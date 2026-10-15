/* Shared, escaped answer formatting for live responses and saved history. */
(function () {
  const escape = value => String(value).replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function inline(value) {
    return String(value).split(/(`[^`\n]+`)/g).map(part => {
      if (part.startsWith('`') && part.endsWith('`')) {
        return '<code>' + escape(part.slice(1, -1)) + '</code>';
      }
      return escape(part).replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
    }).join('');
  }
  const label = /^(结论|初步判断|问题分析|原因分析|可能原因|处理步骤|排查步骤|解决方法|解决方案|建议|注意事项|注意|需要补充信息|需要补充|资料依据|依据|为什么|解题思路|选项分析|通用核查|下一步|说明|解释|风险提示)[：:]\s*(.*)$/;
  const cells = line => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(s => s.trim());
  function tableSeparator(line) {
    const parts = cells(line);
    return line.includes('|') && parts.length > 1 && parts.every(s => /^:?-{3,}:?$/.test(s));
  }
  globalThis.fmtAnswer = function (text) {
    const lines = String(text || '').replace(/\r\n?/g, '\n').split('\n');
    const output = [];
    let paragraph = [], list = null, fenced = false, code = [], nextNumber = null;
    function flushParagraph() {
      if (paragraph.length) output.push('<p>' + paragraph.map(inline).join('<br>') + '</p>');
      paragraph = [];
    }
    function closeList() { if (list) output.push('</' + list + '>'); list = null; }
    function endBlock() { flushParagraph(); closeList(); }
    function heading(title) {
      endBlock();
      title = title.replace(/^#{1,6}(?:\s+|(?=[\u3400-\u9fff]))/, '');
      output.push('<h3>' + inline(title) + '</h3>');
    }
    for (let index = 0; index < lines.length; index++) {
      const raw = lines[index], line = raw.trim();
      if (/^```/.test(line)) {
        endBlock();
        if (fenced) { output.push('<pre><code>' + escape(code.join('\n')) + '</code></pre>'); code = []; }
        fenced = !fenced;
        continue;
      }
      if (fenced) { code.push(raw); continue; }
      if (!line) { endBlock(); continue; }
      if (index + 1 < lines.length && line.includes('|') && tableSeparator(lines[index + 1])) {
        endBlock();
        const headers = cells(line);
        output.push('<div class="answer-table"><table><thead><tr>' + headers.map(c =>
          '<th>' + inline(c) + '</th>').join('') + '</tr></thead><tbody>');
        index++;
        while (index + 1 < lines.length && lines[index + 1].includes('|') && lines[index + 1].trim()) {
          const row = cells(lines[++index]);
          output.push('<tr>' + row.map(c => '<td>' + inline(c) + '</td>').join('') + '</tr>');
        }
        output.push('</tbody></table></div>');
        continue;
      }
      const markdownHeading = line.match(/^#{1,6}(?=\s|[\u3400-\u9fff])\s*(.+?)(?:\s+#+)?$/);
      if (markdownHeading) { heading(markdownHeading[1]); continue; }
      const boldHeading = line.match(/^\*\*([^*]{1,48})\*\*[：:]?$/);
      if (boldHeading) { heading(boldHeading[1].replace(/[：:]$/, '')); continue; }
      const plainLabel = line.match(label);
      if (plainLabel) {
        heading(plainLabel[1]);
        if (plainLabel[2]) paragraph.push(plainLabel[2]);
        continue;
      }
      if (/^[^：:]{2,24}[：:]$/.test(line)) { heading(line.slice(0, -1)); continue; }
      const ordered = line.match(/^(\d{1,3})[.)、](?:\s+(.+)|([^\d\s].*))$/);
      const bullet = line.match(/^[-*•]\s+(.+)$/);
      if (ordered || bullet) {
        flushParagraph();
        const type = ordered ? 'ol' : 'ul';
        if (list !== type) {
          closeList(); list = type;
          output.push('<' + type + (ordered ? ' start="' + Number(ordered[1]) + '"' : '') + '>');
          nextNumber = ordered ? Number(ordered[1]) : null;
        }
        const number = ordered ? Number(ordered[1]) : null;
        const value = ordered && number !== nextNumber ? ' value="' + number + '"' : '';
        output.push('<li' + value + '>' + inline(ordered ? ordered[2] || ordered[3] : bullet[1]) + '</li>');
        nextNumber = ordered ? number + 1 : null;
        continue;
      }
      closeList();
      paragraph.push(line);
    }
    endBlock();
    if (fenced) output.push('<pre><code>' + escape(code.join('\n')) + '</code></pre>');
    return output.join('');
  };
})();
