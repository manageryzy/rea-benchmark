#!/usr/bin/python3
import hashlib,json,os,pathlib,subprocess,sys
ROOT=pathlib.Path(__file__).resolve().parents[2]
BENCH=ROOT/'benchmarks'
sys.path.insert(0,str(BENCH/'grading'))
from oracle import vectors

def run(args, **kw): return subprocess.run(args,check=True,text=True,capture_output=True,**kw)
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    cases=vectors()
    (BENCH/'grading/vectors.json').write_text(json.dumps(cases,indent=2)+'\n')
    payload=''.join(c['hex']+'\n' for c in cases)
    expected=[c['expected'] for c in cases]
    for language in ('cpp','rust','go'):
        dest=BENCH/'public'/language; dest.mkdir(parents=True,exist_ok=True)
        target=dest/'target'
        env=os.environ.copy()
        if language=='cpp':
            source=BENCH/'authoring/cpp/case.cpp'
            command=['g++','-std=c++20','-O1','-g0','-fno-inline',str(source),'-o',str(target)]
            version=run(['g++','--version']).stdout.splitlines()[0]; selector='score_record'
        elif language=='rust':
            source=BENCH/'authoring/rust/case.rs'
            command=['rustc','--edition=2021','-C','opt-level=1','-C','debuginfo=0','-C','codegen-units=1','--remap-path-prefix',str(ROOT)+'=fixture',str(source),'-o',str(target)]
            version=run(['rustc','--version']).stdout.strip(); selector='score_record'
        else:
            source=BENCH/'authoring/go/case.go'
            env.update(GO111MODULE='off',GOPROXY='off',CGO_ENABLED='0',GOCACHE=str(ROOT/'.cache/benchmark-go'))
            command=['go','build','-trimpath','-gcflags=-l','-ldflags=-w','-o',str(target),str(source)]
            version=run(['go','version']).stdout.strip(); selector='main.score_record'
        run(command,env=env)
        run(['strip','--strip-debug',str(target)])
        result=run([str(target)],input=payload,timeout=20)
        actual=[int(x) for x in result.stdout.splitlines()]
        assert actual==expected, f'{language} differs from oracle'
        manifest={'case_id':f'record-parser-v1-{language}','language':language,'architecture':'x86_64','format':'ELF','target_function':selector,'binary_sha256':sha(target),'source_sha256':sha(source),'compiler':version,'build_command':command,'postprocess':['strip','--strip-debug'], 'original_tests_passed':len(cases),'debug_info':False,'symbols_retained':True}
        (dest/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
        prompt=f'''Analyze target with REA's Ghidra provider. The target function is {selector}.
Recover its behavior from the binary, pseudocode and assembly. Treat every
address and return value precisely, including validation order, integer widths,
byte order, loop bounds and wrapping arithmetic. For Go, translate the slice
calling convention into the C pointer/length interface below.

Return a short behavioral explanation, evidence addresses, and exactly one
```c code block defining this complete function:
int64_t reconstructed(const uint8_t *data, size_t len);
The caller supplies a valid pointer to at least len bytes, including for len=0.
Include stdint.h and stddef.h if needed. No main, I/O, external dependencies,
inline assembly, or filesystem access. The function must reproduce the original
for all byte buffers of length 0 through 300. You are graded on executable
behavior, not source similarity. Do not run the target or use another model.
Do not seek source, hidden tests or previous answers. Use only the supplied
REA tools. Open target with provider_id ghidra; close it before finishing.
There is a limit of 20 tool calls. Submit one final reconstruction; no grader
feedback is available. If evidence is incomplete, state the uncertainty.
'''
        (dest/'prompt.txt').write_text(prompt)
        print(f'{language}: built {target.stat().st_size} bytes; {len(cases)} oracle tests passed',flush=True)
    (BENCH/'grading/build-validation.json').write_text(json.dumps({'passed':True,'vectors':len(cases),'languages':['cpp','rust','go']},indent=2)+'\n')
if __name__=='__main__': main()
