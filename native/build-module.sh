#!/usr/bin/env bash
# Build only this module against separately supplied, prepared C5 kernel trees.
set -euo pipefail
module_dir="$(cd "$(dirname "$0")/module" && pwd)"
: "${C5_KERNEL_SOURCE:?Supply the matching LG C5 kernel source tree}"
: "${C5_KERNEL_OBJECTS:?Supply the matching, already prepared C5 kernel output tree}"
test -f "$C5_KERNEL_OBJECTS/include/generated/asm-offsets.h"
python3 "$module_dir/../storage-preflight.py"
# build-id.h is the compatibility identity of this released source revision.
# Preserve it when rebuilding this version. A changed controller requires a new
# identity and fresh target validation; never replace a resident live module.
"${C5_MAKE:-make}" -C "$C5_KERNEL_SOURCE" O="$C5_KERNEL_OBJECTS" ARCH=arm64 \
  "CC=${C5_CC:-clang}" CLANG_FLAGS=--target=aarch64-linux-gnu \
  "LD=${C5_LD:-ld.lld}" "AR=${C5_AR:-llvm-ar}" "NM=${C5_NM:-llvm-nm}" \
  "OBJCOPY=${C5_OBJCOPY:-llvm-objcopy}" "HOSTCC=${C5_HOSTCC:-cc}" \
  "KCFLAGS=-ffile-prefix-map=$module_dir=. -fdebug-prefix-map=$module_dir=." \
  M="$module_dir" modules
