#!/usr/bin/python3
"""Compile native reconstructions and compare their behavior outside a bwrap mount."""
import argparse, functools, hashlib, json, os, pathlib, re, resource, shutil, subprocess, tempfile
BENCH = pathlib.Path(__file__).resolve().parents[1]
LANGUAGES = {'c': ('c', 'candidate.c'), 'cpp': ('cpp|c\\+\\+', 'candidate.cpp'),
             'rust': ('rust|rs', 'candidate.rs'), 'go': ('go|golang', 'candidate.go')}
C_DRIVER = r'''
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "candidate.c"
int main(void) {
 char line[2048]; uint8_t bytes[1024];
 while(fgets(line,sizeof(line),stdin)) {
  size_t n=strcspn(line,"\r\n"), count=0;
  if(line[0]!='-') for(size_t i=0;i+1<n;i+=2) {char pair[3]={line[i],line[i+1],0};bytes[count++]=(uint8_t)strtoul(pair,0,16);}
  printf("%lld\n",(long long)reconstructed(bytes,count));
 }
 return 0;
}
'''
CPP_DRIVER = C_DRIVER.replace('#include "candidate.c"', '#include <span>\n#include "candidate.cpp"').replace('reconstructed(bytes,count)', 'reconstructed(std::span<const std::uint8_t>(bytes,count))')
RUST_DRIVER = r'''
use std::io::{self, BufRead};
include!("candidate.rs");
fn main() {
 for line in io::stdin().lock().lines() {
  let line=line.unwrap(); let line=line.trim();
  let bytes: Vec<u8> = if line=="-" { Vec::new() } else {
   (0..line.len()).step_by(2).map(|i|u8::from_str_radix(&line[i..i+2],16).unwrap()).collect()
  };
  println!("{}", reconstructed(&bytes));
 }
}
'''
GO_DRIVER = r'''
package main
import ("bufio"; "encoding/hex"; "fmt"; "os"; "strings")
func main() {
 scanner:=bufio.NewScanner(os.Stdin)
 for scanner.Scan() {
  line:=strings.TrimSpace(scanner.Text()); var data []byte
  if line!="-" { var err error; data,err=hex.DecodeString(line); if err!=nil {panic(err)} }
  fmt.Println(reconstructed(data))
 }
 if err:=scanner.Err(); err!=nil {panic(err)}
}
'''

@functools.lru_cache()
def toolchain(language):
    if language=='rust':
        root=subprocess.check_output(['rustc','--print','sysroot'],text=True).strip()
        return pathlib.Path(root).resolve()
    if language=='go':
        return pathlib.Path(subprocess.check_output(['go','env','GOROOT'],text=True).strip()).resolve()
    return None

def sandbox(work, command, language='c'):
    args=['/usr/bin/bwrap','--unshare-all','--die-with-parent','--new-session',
          '--ro-bind','/usr','/usr','--ro-bind','/lib','/lib','--ro-bind','/lib64','/lib64',
          '--symlink','usr/bin','/bin','--proc','/proc','--dev','/dev','--tmpfs','/tmp',
          '--bind',str(work),'/work','--chdir','/work']
    if language in ('rust','go'):
        args+=['--dir','/opt','--ro-bind',str(toolchain(language)),f'/opt/{language}']
    return args+command

def clean_environment():
    return {'PATH':'/opt/rust/bin:/opt/go/bin:/usr/bin:/bin','LANG':'C.UTF-8',
            'HOME':'/tmp','TMPDIR':'/tmp','GOROOT':'/opt/go','GOCACHE':'/work/go-cache',
            'GO111MODULE':'off','GOPROXY':'off','GOSUMDB':'off','CGO_ENABLED':'0',
            'GOTOOLCHAIN':'local','GOMEMLIMIT':'128MiB'}

def resource_limits(language, compiling=False):
    def apply():
        resource.setrlimit(resource.RLIMIT_CPU, (90,90) if compiling else (5,5))
        memory=(4 if compiling or language=='go' else 0.5)*1024**3
        resource.setrlimit(resource.RLIMIT_AS,(int(memory),int(memory)))
        size=(128 if compiling else 4)*1024**2
        resource.setrlimit(resource.RLIMIT_FSIZE,(size,size))
        resource.setrlimit(resource.RLIMIT_NOFILE,(256,256) if compiling else (64,64))
    return apply

def grade(run_dir, language=None):
    run_dir=pathlib.Path(run_dir).resolve()
    if language is None:
        result=run_dir/'result.json'
        language=json.loads(result.read_text()).get('output_language','c') if result.exists() else 'c'
    if (run_dir/'events.jsonl').exists():
        from collect_answer import collect
        collect(run_dir)
    fence,filename=LANGUAGES[language]
    answer=(run_dir/'answer.md').read_text() if (run_dir/'answer.md').exists() else ''
    all_blocks=re.findall(r'^```([^\n]*)\n(.*?)^```\s*$',answer,re.M|re.S)
    matching=[code for tag,code in all_blocks if re.fullmatch(fence,tag.strip(),re.I)]
    vector_path=BENCH/'grading/vectors.json';cases=json.loads(vector_path.read_text())
    receipt={'output_language':language,'compiled':False,'passed':0,'total':len(cases),
             'pass_rate':0.0,'failure':None,'categories':{},
             'vectors_sha256':hashlib.sha256(vector_path.read_bytes()).hexdigest(),
             'explanation_evidence_review':'pending manual review'}
    if len(matching)!=1 or len(all_blocks)!=1:
        receipt['failure']=f'Expected exactly one {language} code block; received {len(matching)} matching / {len(all_blocks)} total'
    else:
        code=matching[0]
        receipt['candidate_source_sha256']=hashlib.sha256(code.encode()).hexdigest()
        # The public contract forbids these: they bypass native behavior recovery.
        if language=='go' and re.search(r'"(?:unsafe|C)"',code):
            receipt['failure']='forbidden_unsafe_or_ffi'
        else:
            artifact=run_dir/'grade';artifact.mkdir(exist_ok=True)
            extension=filename.split('.')[-1]
            (run_dir/f'reconstructed.{extension}').write_text(code)
            with tempfile.TemporaryDirectory(prefix='rea-native-grade-') as tmp:
                work=pathlib.Path(tmp)
                (work/filename).write_text(code)
                driver,command={
                    'c':('driver.c',['gcc','-std=c11','-O2','-Wall','-Wextra','driver.c','-o','candidate']),
                    'cpp':('driver.cpp',['g++','-std=c++20','-O2','-Wall','-Wextra','driver.cpp','-o','candidate']),
                    'rust':('driver.rs',['/opt/rust/bin/rustc','--sysroot','/opt/rust','--edition=2021','-C','linker=/usr/bin/gcc','-F','unsafe_code','-C','opt-level=2','-C','debuginfo=0','-C','strip=symbols','driver.rs','-o','candidate']),
                    'go':('driver.go',['/opt/go/bin/go','build','-trimpath','-ldflags=-s -w','-o','candidate','driver.go','candidate.go']),
                }[language]
                (work/driver).write_text({'c':C_DRIVER,'cpp':CPP_DRIVER,'rust':RUST_DRIVER,'go':GO_DRIVER}[language])
                for path in [work/filename,work/driver]:shutil.copy2(path,artifact/path.name)
                receipt['compile_command']=command
                try:
                    compiled=subprocess.run(sandbox(work,command,language),env=clean_environment(),text=True,capture_output=True,timeout=120,preexec_fn=resource_limits(language,True))
                    (run_dir/'compile.log').write_text((compiled.stdout+compiled.stderr)[:262144])
                    if compiled.returncode:receipt['failure']='compile_error'
                    else:
                        receipt['compiled']=True
                        shutil.copy2(work/'candidate',artifact/'candidate')
                        shutil.rmtree(work/'go-cache',ignore_errors=True)
                        executed=subprocess.run(sandbox(work,['/work/candidate'],language),env=clean_environment(),input=''.join(c['hex']+'\n' for c in cases),text=True,capture_output=True,timeout=15,preexec_fn=resource_limits(language))
                        (run_dir/'candidate-stderr.log').write_text(executed.stderr[:65536])
                        actual=executed.stdout.splitlines()
                        if executed.returncode:receipt['failure']=f'candidate_exit_{executed.returncode}'
                        mismatches=[]
                        for i,c in enumerate(cases):
                            try:value=int(actual[i])
                            except (ValueError,IndexError):value=None
                            ok=value==c['expected'];receipt['passed']+=int(ok)
                            group=receipt['categories'].setdefault(c['category'],{'passed':0,'total':0});group['total']+=1;group['passed']+=int(ok)
                            if not ok and len(mismatches)<10:mismatches.append({'test_index':i,'expected':c['expected'],'actual':value,'category':c['category']})
                        receipt['first_mismatches']=mismatches;receipt['pass_rate']=receipt['passed']/len(cases)
                        if len(actual)!=len(cases):receipt['failure']=receipt['failure'] or 'wrong_output_count'
                except subprocess.TimeoutExpired:
                    receipt['failure']='candidate_timeout' if receipt['compiled'] else 'compile_timeout'
    (run_dir/'grade.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return receipt

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('run_dir',type=pathlib.Path);p.add_argument('--language',choices=LANGUAGES);a=p.parse_args();print(json.dumps(grade(a.run_dir,a.language),indent=2))
