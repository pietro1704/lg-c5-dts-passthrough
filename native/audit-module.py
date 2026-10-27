from pathlib import Path
import subprocess,struct,re,json,hashlib
import argparse
parser=argparse.ArgumentParser(description='Offline C5 module ABI/export audit; no device access')
parser.add_argument('--image',type=Path,required=True)
parser.add_argument('--reference-module',type=Path,required=True)
parser.add_argument('--module',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--llvm',type=Path,required=True)
args=parser.parse_args()
image=args.image.read_bytes()
module=args.module
stock=args.reference_module
llvm=args.llvm
def run(*args):return subprocess.check_output(args,text=True)
def abi(path):
 sections=run(str(llvm/'llvm-readelf'),'-SW',str(path))
 line=next(s for s in sections.splitlines() if '.gnu.linkonce.this_module ' in s and 'RELA' not in s)
 size=int(line.split('PROGBITS')[1].split()[2],16)
 info=run(str(llvm/'llvm-readelf'),'-p','.modinfo',str(path))
 magic=next(l.split('vermagic=')[1] for l in info.splitlines() if 'vermagic=' in l)
 rel=run(str(llvm/'llvm-readelf'),'-rW',str(path)).split("Relocation section '.rela.gnu.linkonce.this_module'")[1]
 offsets={name:int(re.search(r'^([0-9a-f]+).* '+name+r' \+',rel,re.M)[1],16) for name in ['init_module','cleanup_module']}
 return dict(size=size,vermagic=magic,**offsets)
assert abi(module)==abi(stock),(abi(module),abi(stock))
imports=[s.split()[-1] for s in run(str(llvm/'llvm-nm'),'-u',str(module)).splitlines()]
positions={}
for name in imports:
 needle=b'\0'+name.encode()+b'\0';p=0
 while True:
  p=image.find(needle,p)
  if p<0:break
  positions[p+1]=name;p+=1
found={}
for i,(offset,) in enumerate(struct.iter_unpack('<i',image[:len(image)//4*4])):
 at=i*4;name=positions.get(at+offset)
 if name and at>=4 and at+8<=len(image):
  value=at-4+struct.unpack_from('<i',image,at-4)[0]
  namespace=at+4+struct.unpack_from('<i',image,at+4)[0]
  if 0<=value<len(image) and 0<=namespace<len(image) and image[namespace]==0:
   found[name]=dict(entry_offset=hex(at-4),runtime=hex(0xffffffc010088000+value))
missing=set(imports)-set(found)
assert not missing,sorted(missing)
report=dict(module_sha256=hashlib.sha256(module.read_bytes()).hexdigest(),abi=abi(module),exports=found,status='PASS',limits='Offline ABI/export audit only. Not loaded; vendor struct layouts, BRK dispatch, routing, DSP and receiver unverified.')
args.output.write_text(json.dumps(report,indent=2)+'\n')
print('PASS: native module ABI markers match and all',len(imports),'imports resolve in captured C5 kernel PREL32 exports')
print('Module SHA-256:',report['module_sha256'])
