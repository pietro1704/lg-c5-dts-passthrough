from pathlib import Path
import shutil
root=Path(__file__).resolve().parent
# Delete only explicitly retired, reproducible object output, never sources/evidence.
retired=root/'objects.retired'
if retired.is_dir() and not retired.is_symlink() and (retired/'.reproducible-build-output').is_file():
 shutil.rmtree(retired)
free=shutil.disk_usage(root).free
required=5*1024**3
print(f'Storage preflight: {free//1024**3} GiB free; require {required//1024**3} GiB')
if free<required:raise SystemExit('Not enough free space; storage guard preserved')
