import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import assert from "node:assert/strict";

const [sourcePath, packedPath] = process.argv.slice(2);
const source = readFileSync(sourcePath);
const packed = readFileSync(packedPath);
const payloads = [];
let offset = 0;
let frames = 0;
let hd = false;
while (offset < packed.length) {
  assert.ok(offset + 8 <= packed.length, "truncated IEC header");
  if (packed.readUInt32LE(offset) !== 0x4e1ff872) {
    const header = Buffer.from(packed.subarray(offset, offset + 12)).swap16();
    assert.equal(header.readUInt32BE(0), 0x7ffe8001, "missing DTS or IEC sync");
    const samples = (((header.readUInt16BE(4) >> 2) & 127) + 1) * 32;
    const size = ((header.readUIntBE(5, 3) >> 4) & 0x3fff) + 1;
    assert.equal(size, samples * 4, "invalid preambleless DTS period");
    assert.ok(offset + size <= packed.length, "truncated preambleless DTS frame");
    payloads.push(Buffer.from(packed.subarray(offset, offset + size)).swap16());
    offset += size;
    ++frames;
    continue;
  }
  const pc = packed.readUInt16LE(offset + 4);
  const type = pc & 0x1f;
  const lengthCode = packed.readUInt16LE(offset + 6);
  let burstSize, payloadSize, payloadOffset;
  if (type === 0x11) {
    hd = true;
    const period = [512, 1024, 2048, 4096, 8192, 16384][(pc >> 8) & 7];
    assert.ok(period, "unsupported DTS-HD period");
    burstSize = period * 4;
    const raw = Buffer.from(packed.subarray(offset + 8, offset + 20)).swap16();
    assert.deepEqual(raw.subarray(0, 10),
      Buffer.from([1, 0, 0, 0, 0, 0, 0, 0, 0xfe, 0xfe]), "HD start code");
    payloadSize = raw.readUInt16BE(10);
    payloadOffset = 20;
    assert.ok(lengthCode >= payloadSize + 12, "HD burst length mismatch");
  } else {
    assert.ok(type >= 0x0b && type <= 0x0d, "not a DTS burst");
    burstSize = { 11: 2048, 12: 4096, 13: 8192 }[type];
    payloadSize = Math.ceil(lengthCode / 8);
    payloadOffset = 8;
  }
  assert.ok(offset + burstSize <= packed.length, "truncated IEC burst");
  assert.ok(payloadSize + payloadOffset <= burstSize, "payload overflow");
  const length = (payloadSize + 1) & ~1;
  const payload = Buffer.from(
    packed.subarray(offset + payloadOffset, offset + payloadOffset + length)
  ).swap16().subarray(0, payloadSize);
  payloads.push(payload);
  offset += burstSize;
  ++frames;
}
assert.ok(frames > 0, "no DTS frames");
const recovered = Buffer.concat(payloads);
assert.deepEqual(recovered, source, "source DTS bytes lost, altered, or repeated");
const sha256 = createHash("sha256").update(recovered).digest("hex");
console.log(JSON.stringify({ frames, hd, bytes: recovered.length, sha256, identity: true }));
