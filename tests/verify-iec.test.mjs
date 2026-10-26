import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';

// Synthetic transport payloads; no recorded audio or vendor fixtures.
const source = Buffer.from([0x7f, 0xfe, 0x80, 1, 2, 3, 4, 5]);
function burst(hd = false) {
  const packed = Buffer.alloc(2048);
  packed.writeUInt32LE(0x4e1ff872);
  packed.writeUInt16LE(hd ? 0x11 : 0x0b, 4);
  packed.writeUInt16LE(hd ? source.length + 12 : source.length * 8, 6);
  if (hd) {
    const header = Buffer.from([1, 0, 0, 0, 0, 0, 0, 0, 0xfe, 0xfe, 0, source.length]);
    header.swap16().copy(packed, 8);
  }
  Buffer.from(source).swap16().copy(packed, hd ? 20 : 8);
  return packed;
}
function verify(expected, packed) {
  const dir = mkdtempSync(join(tmpdir(), 'iec-verifier-'));
  try {
    const input = join(dir, 'source.dts');
    const output = join(dir, 'packed.spdif');
    writeFileSync(input, expected);
    writeFileSync(output, packed);
    return spawnSync(process.execPath,
      [new URL('../verify-iec.mjs', import.meta.url).pathname, input, output],
      { encoding: 'utf8' });
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}
for (const hd of [false, true]) {
  test(`preserves synthetic ${hd ? 'HD' : 'core'} payload identity`, () => {
    const result = verify(source, burst(hd));
    assert.equal(result.status, 0, result.stderr);
    assert.equal(JSON.parse(result.stdout).identity, true);
    assert.equal(JSON.parse(result.stdout).hd, hd);
  });
}
test('rejects changed source identity', () => {
  assert.notEqual(verify(Buffer.alloc(source.length), burst()).status, 0);
});
test('rejects truncated burst', () => {
  assert.notEqual(verify(source, burst().subarray(0, 100)).status, 0);
});
test('rejects empty output', () => {
  assert.notEqual(verify(source, Buffer.alloc(0)).status, 0);
});
test('rejects repeated frames', () => {
  assert.notEqual(verify(source, Buffer.concat([burst(), burst()])).status, 0);
});
