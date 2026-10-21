/* API timestamps are UTC; legacy SQLite responses omitted the zone suffix. */
function formatAgentTime(value) {
  if (typeof value !== 'string' || !value.trim()) return '时间未知';
  let timestamp = value.trim().replace(' ', 'T');
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/.test(timestamp)) {
    timestamp += 'Z';
  }
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return '时间未知';
  return date.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false });
}
