# LG C5 DTS passthrough experiment

Experimental GStreamer bridge for investigating original DTS bitstream transport on LG C5 / webOS. It parses and packs DTS with FFmpeg; it does not decode or downmix audio. Every packed payload is checked against its source bytes to reject core-only fallback.

**This is an experimental transport, not an installable TV fix.** The element registers with rank NONE and blocks native HD output by default. The C5 ES diagnostic path supports big-endian carrier words; native routing and kernel callback protection must be provided separately. Nuvio, Dolby regression and reboot persistence remain unverified.

## Contents and local prerequisites

- `src/gstdtspassthrough.c`: experimental bridge, including diagnostic modes.
- `build-plugin.sh`: cross-build using a locally supplied, prebuilt Zig 0.13.0 at `toolchain/zig`, target-matched headers/libraries in `sysroot/`, and FFmpeg 5.0 headers in `ffmpeg-5.0/`. Those dependencies are not distributed here. The script builds only the plugin; obtain compatible dependencies separately.
- `verify-iec.mjs`: offline byte-identity verifier for DTS/IEC-61937 artifacts.
- `test-runtime.py`: ARM/GStreamer lifecycle harness; requires separately supplied `stereo.dts`, `surround.dts` and `hd.dts` fixtures in `/tmp/codex-dts-passthrough/fixtures`, the plugin, and an isolated GStreamer registry on the target. Set `DTS_TEST_ROOT` to use another fixture/artifact directory. Output goes to appsink. It exercises partial packets, flush/seek, track identity, cancellation/restart, invalid input, the native-HD block and HD ES byte order.

There is no automatic device deployment or boot installation. Diagnostic marker files under `/tmp/codex-dts-passthrough` can override output routing and the HD block. Do not enable diagnostic modes on a live audio route without reviewing the code. Application-generated silence is not a hardware mute guarantee.

## HD ES carrier words

The tested C5 native ES path consumes big-endian 16-bit carrier words. Feeding the muxer's little-endian output into that path reverses the IEC-61937 sync words at the eARC DMA buffer. The `probe-es-be` diagnostic marker selects a word swap for HD when `SILENT_PROBE` and `probe-es-buffers` are also active. DTS core and ordinary file-test output retain their existing format.

The plugin checks the complete original DTS packet, including its MA extension, before changing transport byte order. The runtime harness checks the BE output against its canonical LE form and writes `hd-es-be-canonical.spdif`; use `verify-iec.mjs` to compare that artifact with the original `hd.dts` bytes. The native-HD block remains active outside explicit diagnostic or file-test modes.

## Checks

Run with Python 3 and Node.js 22 or newer:

```sh
python3 scripts/check-publication.py
bash -n build-plugin.sh
node --check verify-iec.mjs
node --test tests/verify-iec.test.mjs
```

These checks validate publication boundaries, script syntax and the offline verifier with synthetic data. They do not compile or execute the ARM plugin, reproduce the target lifecycle tests, or establish HDMI/receiver compatibility.

With privately obtained matching source and packed artifacts:

```sh
node verify-iec.mjs /path/to/source.dts /path/to/output.spdif
```

## Publication policy

The `.gitignore` denies files by default and permits only individually reviewed public files. CI separately checks the tracked-file list and common credential/private-address patterns. Git ignore rules do not protect files added with `git add -f`; review every staged diff before publishing.

Firmware, proprietary binaries and libraries, sysroots, toolchains, media/fixtures, device dumps, logs, network addresses, access helpers, credentials and private investigation notes must remain outside Git. This repository intentionally excludes the local investigation package and its handoff.

The C source carries `LGPL-2.1-or-later`. Third-party runtime dependencies and media are not included; their respective licenses still apply.

## Related projects

- <https://github.com/lgstreamer/dts_restore>
- <https://github.com/dakenf/lg-g5-dts>

Firmware-specific findings from other models must not be applied to a C5 without validation.
