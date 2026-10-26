#!/usr/bin/env bash
set -euo pipefail
task_dir="$(cd "$(dirname "$0")" && pwd)"
zig="$task_dir/toolchain/zig"
includes=(
  "-I$task_dir/sysroot/usr/include"
  "-I$task_dir/sysroot/usr/include/arm-linux-gnueabi"
  "-I$task_dir/sysroot/usr/include/glib-2.0"
  "-I$task_dir/sysroot/usr/lib/arm-linux-gnueabi/glib-2.0/include"
  "-I$task_dir/sysroot/usr/include/gstreamer-1.0"
  "-I$task_dir/ffmpeg-5.0"
)
"$zig" cc -target arm-linux-gnueabi -mcpu=cortex_a9 -mfpu=neon -mfloat-abi=softfp -fPIC -O1 -g -nostdlib "${includes[@]}" -DGLIB_VERSION_MIN_REQUIRED=GLIB_VERSION_2_72 -DGLIB_VERSION_MAX_ALLOWED=GLIB_VERSION_2_72 -Wall -Wextra -Werror=implicit-function-declaration -c "$task_dir/src/gstdtspassthrough.c" -o "$task_dir/gstdtspassthrough.o"
"$zig" cc -target arm-linux-gnueabi -nostdlib -shared -Wl,--no-undefined -Wl,--allow-shlib-undefined -Wl,-soname,libgstdtspassthrough.so "$task_dir/gstdtspassthrough.o" "-L$task_dir/sysroot/usr/lib" -l:libgstreamer-1.0.so.0 -l:libgobject-2.0.so.0 -l:libglib-2.0.so.0 -l:libavformat.so.59 -l:libavcodec.so.59 -l:libavutil.so.57 -l:libc.so.6 -o "$task_dir/libgstdtspassthrough.so"
file "$task_dir/libgstdtspassthrough.so"
