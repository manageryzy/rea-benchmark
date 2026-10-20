#!/usr/bin/python3
"""Compile and execute the candidate in a source/credential-free bwrap mount."""
import argparse,json,pathlib,re,resource,subprocess
BENCH=pathlib.Path(__file__).resolve().parents[1]
WRAPPER=r'''
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

def sandbox(work, command):
    return ['bwrap','--unshare-all','--die-with-parent','--new-session','--ro-bind','/usr','/usr','--ro-bind','/lib','/lib','--ro-bind','/lib64','/lib64','--symlink','usr/bin','/bin','--proc','/proc','--dev','/dev','--tmpfs','/tmp','--bind',str(work),'/work','--chdir','/work','--setenv','PATH','/usr/bin:/bin',*command]

def limits():
    resource.setrlimit(resource.RLIMIT_CPU,(5,5));resource.setrlimit(resource.RLIMIT_AS,(512*1024*1024,512*1024*1024));resource.setrlimit(resource.RLIMIT_FSIZE,(4*1024*1024,4*1024*1024));resource.setrlimit(resource.RLIMIT_NOFILE,(64,64))

def grade(run_dir):
    answer=(run_dir/'answer.md').read_text() if (run_dir/'answer.md').exists() else ''
    blocks=re.findall(r'```(?:c|cpp|c\+\+)\s*\n(.*?)```',answer,re.S|re.I)
    cases=json.loads((BENCH/'grading/vectors.json').read_text())
    receipt={'compiled':False,'passed':0,'total':len(cases),'pass_rate':0.0,'failure':None,'categories':{},'explanation_evidence_review':'pending manual review'}
    if len(blocks)!=1:
        receipt['failure']=f'Expected exactly one C code block; received {len(blocks)}'
    else:
        work=run_dir/'grade';work.mkdir(exist_ok=True)
        (run_dir/'reconstructed.c').write_text(blocks[0])
        (work/'candidate.c').write_text(blocks[0]);(work/'driver.c').write_text(WRAPPER)
        compiled=subprocess.run(sandbox(work,['gcc','-std=c11','-O2','-Wall','-Wextra','driver.c','-o','candidate']),text=True,capture_output=True,timeout=30)
        (run_dir/'compile.log').write_text(compiled.stdout+compiled.stderr)
        if compiled.returncode: receipt['failure']='compile_error'
        else:
            receipt['compiled']=True
            try:
                executed=subprocess.run(sandbox(work,['/work/candidate']),input=''.join(c['hex']+'\n' for c in cases),text=True,capture_output=True,timeout=15,preexec_fn=limits)
                (run_dir/'candidate-stderr.log').write_text(executed.stderr[:65536])
                actual=executed.stdout.splitlines()
                if executed.returncode: receipt['failure']=f'candidate_exit_{executed.returncode}'
                mismatches=[]
                for i,c in enumerate(cases):
                    try: value=int(actual[i])
                    except (ValueError,IndexError): value=None
                    ok=value==c['expected']; receipt['passed']+=int(ok)
                    group=receipt['categories'].setdefault(c['category'],{'passed':0,'total':0});group['total']+=1;group['passed']+=int(ok)
                    if not ok and len(mismatches)<10: mismatches.append({'test_index':i,'expected':c['expected'],'actual':value,'category':c['category']})
                receipt['first_mismatches']=mismatches
                receipt['pass_rate']=receipt['passed']/len(cases)
                if len(actual)!=len(cases): receipt['failure']=receipt['failure'] or 'wrong_output_count'
            except subprocess.TimeoutExpired: receipt['failure']='candidate_timeout'
    (run_dir/'grade.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return receipt
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('run_dir',type=pathlib.Path);a=p.parse_args();print(json.dumps(grade(a.run_dir),indent=2))
