#!/usr/bin/env bash
# Build this project's test adapter using existing target-matched prerequisites.
# Never install or preload it in an application or supervisor.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
test -n "${C5_CLOCK_OUTPUT:-}"
test -d "$C5_CLOCK_OUTPUT"
python3 "$root/native/storage-preflight.py"
"${C5_ZIG:-$root/toolchain/zig}" cc -target arm-linux-gnueabi \
  -mcpu=cortex_a9 -mfloat-abi=softfp -fPIC -O1 -nostdlib \
  -I "$root/sysroot/usr/include" -I "$root/sysroot/usr/include/arm-linux-gnueabi" \
  -Wall -Wextra -Werror -shared -Wl,--no-undefined \
  "$root/native/test-clock-isolation.c" -L "$root/sysroot/usr/lib" \
  -l:libc.so.6 -o "$C5_CLOCK_OUTPUT/clock-isolation.so"
