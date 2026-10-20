#!/usr/bin/python3
import pathlib,sys,json,subprocess,tempfile
BENCH=pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0,str(BENCH/'scripts'))
from grade import grade,sandbox,clean_environment
root=BENCH.parent/'.cache/native-grader-selftest'
correct={
'cpp':r'''#include <cstdint>
#include <span>
std::int64_t reconstructed(std::span<const std::uint8_t> b){
 if(b.size()<6)return -1;if(b[0]!=0x52||b[1]!=0x45)return -2;
 std::uint32_t n=b[3];if(n>32||b.size()!=6+n)return -3;
 std::uint32_t a=b[4]|((std::uint32_t)b[5]<<8);
 for(std::uint32_t i=0;i<n;i++)a=a*33u^((std::uint32_t)b[6+i]+i);
 switch(b[2]){case 0:return a&65535u;case 1:return ((a&1)?a>>1:a+17u)&65535u;case 2:return (a^0xa5a5u)&65535u;default:return -4;}}
''',
'rust':r'''pub fn reconstructed(b: &[u8]) -> i64 {
 if b.len()<6{return -1} if b[0]!=0x52||b[1]!=0x45{return -2}
 let n=b[3] as usize; if n>32||b.len()!=n+6{return -3}
 let mut a=b[4] as u32|((b[5] as u32)<<8);
 for i in 0..n {a=a.wrapping_mul(33)^((b[6+i] as u32)+(i as u32));}
 match b[2] {0=>(a&65535) as i64,1=>((if a&1!=0 {a>>1}else{a.wrapping_add(17)})&65535) as i64,2=>((a^0xa5a5)&65535) as i64,_=>-4}
}
''',
'go':r'''package main
func reconstructed(b []byte) int64 {
 if len(b)<6{return -1};if b[0]!=0x52||b[1]!=0x45{return -2}
 n:=uint32(b[3]);if n>32||len(b)!=int(n)+6{return -3}
 a:=uint32(b[4])|(uint32(b[5])<<8)
 for i:=uint32(0);i<n;i++ {a=a*33^(uint32(b[6+i])+i)}
 switch b[2] {case 0:return int64(a&65535);case 1:if a&1!=0{return int64((a>>1)&65535)};return int64((a+17)&65535);case 2:return int64((a^0xa5a5)&65535);default:return -4}
}
'''}
wrong={
 'cpp':'#include <span>\n#include <cstdint>\nstd::int64_t reconstructed(std::span<const std::uint8_t> b){return 0;}',
 'rust':'pub fn reconstructed(_: &[u8]) -> i64 {0}',
 'go':'package main\nfunc reconstructed(b []byte) int64 {return 0}',
}
checks=[]
for lang in correct:
 for kind,code in [('correct',correct[lang]),('incorrect',wrong[lang])]:
  d=root/lang/kind;d.mkdir(parents=True,exist_ok=True);(d/'answer.md').write_text('```'+lang+'\n'+code+'\n```\n')
  receipt=grade(d,lang)
  assert receipt['compiled'],(lang,kind,receipt,(d/'compile.log').read_text())
  assert (receipt['pass_rate']==1.0)==(kind=='correct'),receipt
  print(lang,kind,receipt['passed'],'/',receipt['total'],flush=True)
  checks.append({'language':lang,'candidate':kind,'passed':receipt['passed'],'total':receipt['total']})
 with tempfile.TemporaryDirectory(prefix='rea-isolation-check-') as tmp:
  hidden=[str(BENCH/'suites/record-parser/grading/oracle.py'),str(BENCH/'suites/record-parser/authoring'),str(BENCH/'runs'),str(BENCH.parent/'.harness/.credentials.yaml')]
  command=['/bin/sh','-c',' && '.join('test ! -e '+p for p in hidden)]
  subprocess.run(sandbox(pathlib.Path(tmp),command,lang),env=clean_environment(),check=True)
# A mismatched output language must not silently fall back to C.
d=root/'wrong-language';d.mkdir(parents=True,exist_ok=True);(d/'answer.md').write_text('```c\n'+wrong['rust']+'\n```\n')
assert grade(d,'rust')['failure'].startswith('Expected exactly one rust')
(BENCH/'config/native-grader-validation.json').write_text(json.dumps({'passed':True,'checks':checks,'source_credentials_and_prior_runs_hidden':True,'wrong_language_rejected':True},indent=2)+'\n')
print('PASS: native compilers, correct/wrong behavior, language selection and isolated mounts.')
