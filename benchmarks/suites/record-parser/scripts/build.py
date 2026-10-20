#!/usr/bin/python3
import hashlib,json,os,pathlib,subprocess,sys
ROOT=pathlib.Path(__file__).resolve().parents[4]
BENCH=ROOT/'benchmarks'
sys.path.insert(0,str(BENCH/'suites/record-parser/grading'))
from oracle import vectors

def run(args, **kw): return subprocess.run(args,check=True,text=True,capture_output=True,**kw)
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    cases=vectors()
    (BENCH/'suites/record-parser/grading/vectors.json').write_text(json.dumps(cases,indent=2)+'\n')
    payload=''.join(c['hex']+'\n' for c in cases)
    expected=[c['expected'] for c in cases]
    for language in ('cpp','rust','go'):
        dest=BENCH/'suites/record-parser/public'/language; dest.mkdir(parents=True,exist_ok=True)
        target=dest/'target'
        env=os.environ.copy()
        if language=='cpp':
            source=BENCH/'suites/record-parser/authoring/cpp/case.cpp'
            command=['g++','-std=c++20','-O1','-g0','-fno-inline',str(source),'-o',str(target)]
            version=run(['g++','--version']).stdout.splitlines()[0]; selector='score_record'
        elif language=='rust':
            source=BENCH/'suites/record-parser/authoring/rust/case.rs'
            command=['rustc','--edition=2021','-C','opt-level=1','-C','debuginfo=0','-C','codegen-units=1','--remap-path-prefix',str(ROOT)+'=fixture',str(source),'-o',str(target)]
            version=run(['rustc','--version']).stdout.strip(); selector='score_record'
        else:
            source=BENCH/'suites/record-parser/authoring/go/case.go'
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
        print(f'{language}: built {target.stat().st_size} bytes; {len(cases)} oracle tests passed',flush=True)
    from prompts import write_prompts
    write_prompts()
    (BENCH/'suites/record-parser/grading/build-validation.json').write_text(json.dumps({'passed':True,'vectors':len(cases),'languages':['cpp','rust','go']},indent=2)+'\n')
if __name__=='__main__': main()
