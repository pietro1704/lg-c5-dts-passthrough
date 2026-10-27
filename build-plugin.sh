#!/usr/bin/env bash
set -euo pipefail
task_dir="$(cd "$(dirname "$0")" && pwd)"
zig="${C5_ZIG:-$task_dir/toolchain/zig}"
native_mode="${DTS_NATIVE_SESSION:-0}"
case "$native_mode" in 0|1) ;; *) exit 64;; esac
output_dir="${DTS_OUTPUT_DIR:-$task_dir}"
if [[ "$native_mode" = 1 && -z "${DTS_OUTPUT_DIR:-}" ]]; then output_dir="$task_dir/native"; fi
test -d "$output_dir"
includes=(
  "-I$task_dir/sysroot/usr/include"
  "-I$task_dir/sysroot/usr/include/arm-linux-gnueabi"
  "-I$task_dir/sysroot/usr/include/glib-2.0"
  "-I$task_dir/sysroot/usr/lib/arm-linux-gnueabi/glib-2.0/include"
  "-I$task_dir/sysroot/usr/include/gstreamer-1.0"
  "-I$task_dir/ffmpeg-5.0"
)
"$zig" cc -target arm-linux-gnueabi -mcpu=cortex_a9 -mfpu=neon -mfloat-abi=softfp -fPIC -O1 -g -nostdlib "${includes[@]}" "-DDTS_NATIVE_SESSION=$native_mode" "-ffile-prefix-map=$task_dir=." -DGLIB_VERSION_MIN_REQUIRED=GLIB_VERSION_2_72 -DGLIB_VERSION_MAX_ALLOWED=GLIB_VERSION_2_72 -Wall -Wextra -Werror=implicit-function-declaration -c "$task_dir/src/gstdtspassthrough.c" -o "$output_dir/gstdtspassthrough.o"
"$zig" cc -target arm-linux-gnueabi -nostdlib -shared -Wl,--no-undefined -Wl,--allow-shlib-undefined -Wl,-soname,libgstdtspassthrough.so "$output_dir/gstdtspassthrough.o" "-L$task_dir/sysroot/usr/lib" -l:libgstreamer-1.0.so.0 -l:libgobject-2.0.so.0 -l:libglib-2.0.so.0 -l:libavformat.so.59 -l:libavcodec.so.59 -l:libavutil.so.57 -l:libc.so.6 -o "$output_dir/libgstdtspassthrough.so"
file "$output_dir/libgstdtspassthrough.so"
