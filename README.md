# LG C5 DTS passthrough experiment

Experimental GStreamer bridge for investigating original DTS bitstream transport on LG C5 / webOS. It parses and packs DTS with FFmpeg; it does not decode or downmix audio. Every packed payload is checked against its source bytes to reject core-only fallback.

**DTS-HD MA output over eARC is unresolved. This is not an installable fix.** Native DTS playback has only partial receiver/channel confirmation. Full surround, Nuvio, Dolby regression and reboot persistence remain unverified. The element registers with rank NONE and blocks native HD output by default.

## Contents and local prerequisites

- `src/gstdtspassthrough.c`: experimental bridge, including diagnostic modes.
- `build-plugin.sh`: cross-build using a locally supplied, prebuilt Zig 0.13.0 at `toolchain/zig`, target-matched headers/libraries in `sysroot/`, and FFmpeg 5.0 headers in `ffmpeg-5.0/`. Those dependencies are not distributed here. The script builds only the plugin; obtain compatible dependencies separately.
- `verify-iec.mjs`: offline byte-identity verifier for DTS/IEC-61937 artifacts.
- `test-runtime.py`: ARM/GStreamer lifecycle harness; requires separately supplied DTS fixtures in `/tmp/codex-dts-passthrough/fixtures`, the plugin, and an isolated GStreamer registry on the target. Output goes to appsink. It exercises partial packets, flush/seek, track identity, cancellation/restart, invalid input and the native-HD block.

There is no automatic device deployment or boot installation. Diagnostic marker files under `/tmp/codex-dts-passthrough` can override output routing and the HD block. Do not enable diagnostic modes on a live audio route without reviewing the code. Application-generated silence is not a hardware mute guarantee.

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
