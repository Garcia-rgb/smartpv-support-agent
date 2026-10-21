const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { spawnSync } = require('node:child_process');
const context = {};
vm.createContext(context);
vm.runInContext(fs.readFileSync('static/time-format.js', 'utf8'), context);
const format = context.formatAgentTime;
const expected = '2026/10/15 14:12:08';
assert.equal(format('2026-10-15T06:12:08'), expected); // Old SQLite API response.
assert.equal(format('2026-10-15 06:12:08.123456'), expected);
assert.equal(format('2026-10-15T06:12:08Z'), expected); // Fixed API response.
assert.equal(format('2026-10-15T06:12:08+00:00'), expected);
assert.equal(format('2026-10-15T14:12:08+08:00'), expected); // Never add eight hours twice.
assert.equal(format('2026-10-15T20:30:00Z'), '2026/10/16 04:30:00');
assert.equal(format('invalid'), '时间未知');
assert.equal(format(null), '时间未知');
assert.equal(format(''), '时间未知');
if (!process.env.TIME_FORMAT_CHILD) {
  for (const zone of ['UTC', 'America/New_York', 'Asia/Shanghai']) {
    const result = spawnSync(process.execPath, [__filename], {
      env: { ...process.env, TZ: zone, TIME_FORMAT_CHILD: '1' }, encoding: 'utf8'
    });
    assert.equal(result.status, 0, result.stderr);
  }
  console.log('UTC、新旧时间格式、北京时间、跨日与不同系统时区：全部通过');
}
