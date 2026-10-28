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
        # Match env.sh even when the coordinator is launched outside a login shell.
        pinned=BENCH.parent/'.tools/go1.23.2'
        if (pinned/'bin/go').is_file():
            return pinned
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
    if os.environ.get('REA_GRADING_WORKER') == '1':
        return json.loads(pathlib.Path('/environment/manifest.json').read_bytes())['manifest']['environment']
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
    """Versioned exact-byte grading; preserve any original grade and candidate."""
    import sys
    sys.path.insert(0, str(BENCH / 'scripts'))
    from grading.store import grade_entry
    return grade_entry(run_dir, language, 'basic')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('run_dir',type=pathlib.Path);p.add_argument('--language',choices=LANGUAGES);a=p.parse_args();print(json.dumps(grade(a.run_dir,a.language),indent=2))
