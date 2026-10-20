#!/usr/bin/python3
"""Frozen native tasks through the installed ZCode CLI; no inference retries."""
import argparse
import concurrent.futures
import datetime
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / 'benchmarks'
NODE = ROOT / '.tools/node-v24.14.0-linux-x64/bin/node'
REA_NODE = ROOT / '.tools/node-v24.22.0-linux-x64/bin/node'
CLI = ROOT / '.tools/zcode-3.16.1/agent/zcode.cjs'
EVIDENCE_OVERLAY = BENCH / 'harnesses/zcode-evidence.cjs'
DENIED = 'ToolSearch DeferredToolCall Read Write Edit Bash Glob Grep WebFetch WebSearch TodoRead TodoWrite CronCreate CronList CronUpdate CronDelete OffPeakCreate OffPeakList EnterPlanMode ExitPlanMode AskUserQuestion SendMessage RespondToCoordinator submit_result escalate TaskOutput TaskStop ReadSessionContext ReplyToChannel Agent Task Skill js CreateWorkflow AmendWorkflow SaveWorkflow EvalWorkflowSnippet ListWorkflowRuns GetWorkflowRun ResumeWorkflowRun ResolveWorkflowQuestion FillWorkflowHole ListSavedWorkflows ListModels'


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def valid_output_ceiling(value, maximum):
    # ZCode can lower its output allowance to leave room for full input context.
    # The frozen value is a ceiling, not a requirement to overfill the model window.
    return type(value) is int and 0 < value <= maximum


def load(path):
    return json.loads(Path(path).read_text())


def dump(path, value, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x' if exclusive else 'w') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class Recorder(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, directory, item, credentials=None, fixture=False):
        super().__init__(('127.0.0.1', 0), Handler)
        self.directory, self.item, self.credentials, self.fixture = directory, item, credentials, fixture
        self.sequence = 0
        self.seen = set()
        self.observations = []
        self.violations = []
        self.requests = []
        self.lock = threading.Lock()
        self.tools = {'mcp__rea__' + t['name'] for t in load(Path(item['input_directory'])/'tools.json')['tools']}
        self.upstream_error = None


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_):
        pass

    def error_response(self, status, message):
        body = json.dumps({'error':{'message':message,'type':'benchmark_policy'}}).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        recorder = self.server
        try:
            body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            request = json.loads(body)
            names = {t.get('function', {}).get('name') for t in request.get('tools', [])}
            with recorder.lock:
                recorder.sequence += 1
                sequence = recorder.sequence
            dump(recorder.directory / f'wire/request-{sequence:03d}.json', request, True)
            wire_limit = request.get('max_tokens', request.get('max_completion_tokens'))
            metadata = {'sequence':sequence, 'model':request.get('model'), 'tool_names':sorted(names),
                        'reasoning_effort':request.get('reasoning_effort'), 'max_tokens':wire_limit,
                        'configured_output_ceiling':recorder.item['max_output_tokens'],
                        'started_at':now(), 'request_sha256':hashlib.sha256(body).hexdigest()}
            recorder.requests.append(metadata)
            if names != recorder.tools or request.get('model') != 'glm-5.3':
                recorder.violations.append(metadata)
                return self.error_response(400, 'Unexpected model or tool catalog; no upstream request made')
            if request.get('reasoning_effort') != 'high' or not valid_output_ceiling(wire_limit, recorder.item['max_output_tokens']):
                recorder.violations.append(metadata)
                return self.error_response(400, 'Reasoning or output ceiling violates frozen protocol; no upstream request made')
            if any(marker in body for marker in [b'Local binary-analysis environment', b'CODEGRAPH_START']):
                recorder.violations.append(metadata)
                return self.error_response(400, 'Workspace instructions leaked; no upstream request made')
            if metadata['request_sha256'] in recorder.seen:
                return self.error_response(409, 'Identical request retry denied')
            recorder.seen.add(metadata['request_sha256'])
            if recorder.fixture:
                return self.fixture_response(sequence, request)
            endpoint, token = recorder.credentials
            upstream = urllib.request.Request(endpoint.rstrip('/') + '/chat/completions', data=body,
                        headers={'Content-Type':'application/json','Authorization':'Bearer '+token})
            try:
                response = urllib.request.urlopen(upstream, timeout=300)
            except urllib.error.HTTPError as error:
                raw = error.read()
                # Provider messages may contain account data; retain only status/code/message with token redaction.
                text = raw.decode(errors='replace').replace(token, '[REDACTED]')
                (recorder.directory/f'wire/error-{sequence:03d}.json').write_text(text)
                recorder.upstream_error = {'http_status':error.code, 'sequence':sequence}
                metadata.update(http_status=error.code, finished_at=now())
                return self.error_response(error.code, 'Upstream provider rejected request; inspect redacted error receipt')
            with response:
                metadata['http_status'] = response.status
                self.send_response(response.status)
                self.send_header('Content-Type', response.headers.get('Content-Type', 'text/event-stream'))
                self.send_header('Connection', 'close')
                self.end_headers()
                path = recorder.directory/f'wire/response-{sequence:03d}.sse'
                raw = bytearray()
                with path.open('xb') as output:
                    while True:
                        chunk = response.read1(65536)
                        if not chunk:
                            break
                        output.write(chunk)
                        raw.extend(chunk)
                        self.wfile.write(chunk)
                        self.wfile.flush()
                metadata['finished_at'] = now()
                for line in bytes(raw).splitlines():
                    if not line.startswith(b'data:'):
                        continue
                    try:
                        event = json.loads(line[5:])
                    except (ValueError, UnicodeError):
                        continue
                    if event.get('usage'):
                        recorder.observations.append({'sequence':sequence, 'finished_at':now(), 'model':event.get('model'), 'usage':event['usage']})
                if 'application/json' in response.headers.get('Content-Type', ''):
                    event = json.loads(raw)
                    if event.get('usage'):
                        recorder.observations.append({'sequence':sequence, 'finished_at':now(), 'model':event.get('model'), 'usage':event['usage']})
                self.close_connection = True
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception as error:
            recorder.upstream_error = {'exception_type':type(error).__name__}
            try:
                self.error_response(502, 'Recording transport failed; private details suppressed')
            except OSError:
                pass

    def fixture_response(self, sequence, request):
        recorder = self.server
        steps = [('open_binary', {'path':'/etc/passwd'}), ('open_binary', {'path':'target','provider_id':'ghidra'})]
        if recorder.item['tool_count'] == 12:
            steps.append(('read_bytes', {'address':'0x0', 'length':4097}))
        steps.append(('close_binary', {}))
        if getattr(recorder, 'replay', None):
            steps = [(x['tool'], x.get('arguments', {})) for x in recorder.replay]
        if sequence <= len(steps):
            name, arguments = steps[sequence-1]
            delta = {'role':'assistant', 'tool_calls':[{'index':0,'id':f'fixture-{sequence}','type':'function',
                     'function':{'name':'mcp__rea__'+name,'arguments':json.dumps(arguments)}}]}
            finish = 'tool_calls'
        else:
            delta, finish = {'role':'assistant','content':'Fixture complete; no model inference was used.'}, 'stop'
        event = {'id':f'fixture-{sequence}', 'object':'chat.completion.chunk','created':int(time.time()),'model':'glm-5.3',
                 'choices':[{'index':0,'delta':delta,'finish_reason':None}]}
        end = {**event,'choices':[{'index':0,'delta':{},'finish_reason':finish}],
               'usage':{'prompt_tokens':100,'completion_tokens':10,'total_tokens':110,'prompt_tokens_details':{'cached_tokens':20}}}
        body = ('data: '+json.dumps(event)+'\n\ndata: '+json.dumps(end)+'\n\ndata: [DONE]\n\n').encode()
        (recorder.directory/f'wire/response-{sequence:03d}.sse').write_bytes(body)
        recorder.observations.append({'sequence':sequence,'finished_at':now(),'model':'glm-5.3','usage':end['usage']})
        self.send_response(200)
        self.send_header('Content-Type','text/event-stream')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def launch(directory, item, credentials=None, fixture=False, replay=None, cancel_event=None):
    if replay and not fixture:
        raise ValueError('Cached response replay is only available with the offline fixture')
    if sha(CLI) != load(BENCH/'config/evidence-delivery.json')['zcode']['upstream_bundle_sha256']:
        raise ValueError('ZCode bundle changed; benchmark overlay must be reviewed before launch')
    profile = item.get('environment_profile', {})
    if not fixture and not profile.get('files_sha256'):
        raise ValueError('Benchmark task lacks environment identity')
    for path, expected in profile.get('files_sha256', {}).items():
        if not path.startswith('benchmarks/') or sha(ROOT/path) != expected:
            raise ValueError('Benchmark adapter/config changed after task preparation')
    directory.mkdir(parents=True, exist_ok=True)
    recorder = Recorder(directory, item, credentials, fixture)
    recorder.replay = replay
    thread = threading.Thread(target=recorder.serve_forever, daemon=True)
    thread.start()
    started, started_at = time.monotonic(), now()
    timed_out = False
    with tempfile.TemporaryDirectory(prefix='rea-zcode-cell-', dir='/tmp') as temporary:
        isolated = Path(temporary)
        work, state = isolated/'work', isolated/'state'
        work.mkdir()
        state.mkdir(mode=0o700)
        frozen = Path(item['input_directory'])
        for name in ('target','manifest.json','prompt.txt'):
            shutil.copy2(frozen/name, work/name)
        runtime_config = {'features':{'compact':False,'rewind':False,'subagent':False,'memory':False,'skill':False,'mcp':True},
            'memory':{'use':False}, 'hooks':{'enabled':False}, 'plugins':{'enabled':False}, 'skills':{'enabled':False},
            'ui':{'locale':'en-US'}, 'toolConcurrency':{'maxConcurrency':1},
            'mcp':{'servers':{'rea':{'type':'stdio','command':str(REA_NODE),
              'args':[str(BENCH/'scripts/rea-gateway.mjs'),str(work/'target'),str(directory/'tool-calls.jsonl'),str(frozen/'gateway.json')],
              'enabled':True,'alwaysLoad':True,'timeoutMs':450000,'cwd':str(work)}}}}
        if replay:
            spec = {'tools': str(frozen/'tools.json'), 'steps': replay}
            dump(isolated/'replay.json', spec)
            runtime_config['mcp']['servers']['rea']['args'] = [str(BENCH/'scripts/replay-mcp.mjs'), str(isolated/'replay.json')]
        dump(work/'zcode.json', runtime_config)
        provider = {'schemaVersion':1,'config':{
          'providerConfigRules':{'providerRules':[{'providerId':'benchmark-glm','templateId':'zai-standard-api','providerName':'Benchmark GLM',
              'enabled':True,'config':{'group':'standard-personal','access':{'type':'api-key','apiKey':'LOCAL-RECORDER-ONLY'},
              'api':{'type':'openai-chat-completions','baseUrl':f'http://127.0.0.1:{recorder.server_port}/v4'},'personalModelIds':['glm-5.3']}}]},
          'modelConfigRules':{'providerModelRules':[{'providerId':'benchmark-glm','modelId':'glm-5.3',
              'config':{'enabled':True,'optionSpecs':{
                  'reasoningLevel':{'map':'{"thinking":{"type":"enabled"},"reasoning_effort":reasoningLevel}'},
                  'maxOutputTokens':{'max':item['max_output_tokens'],'map':'{"max_tokens":maxOutputTokens}'}}}}],
              'manualProviderModelRules':[]},
          'defaultModelSelection':{'providerId':'benchmark-glm','modelId':'glm-5.3','options':{'reasoningLevel':'high'}}}}
        dump(state/'.zcode/v2/provider_config.json', provider)
        dump(directory/'runtime-config.json', runtime_config)
        env = {'PATH':str(NODE.parent)+':/usr/bin:/bin','LANG':'C.UTF-8','NO_COLOR':'1',
               'ZCODE_DATA_BASE_DIR':str(state),'ZCODE_STORAGE_DIR':str(state/'cli'),
               'ZCODE_SESSION_DB_PATH':str(state/'cli/db/db.sqlite'),'ZCODE_LOG_DIR':str(state/'logs'),
               'ZCODE_MODEL_RETRY_MAX_RETRIES':'0'}
        if replay:
            env['REA_OFFLINE_REPLAY'] = '1'
            env['NODE_OPTIONS'] = '--require ' + str(BENCH/'scripts/offline-network.cjs')
        for key in ('USER','LOGNAME','SSL_CERT_FILE','SSL_CERT_DIR','HTTP_PROXY','HTTPS_PROXY','NO_PROXY'):
            if key in os.environ:
                env[key] = os.environ[key]
        command = [str(NODE),'--require',str(EVIDENCE_OVERLAY),str(CLI),'--prompt',(work/'prompt.txt').read_text(),'--cwd',str(work),
                   '--mode','yolo','--workflow-mode','disabled','--disallowed-tools',DENIED,'--output-format','stream-json','--no-color']
        with (directory/'zcode-events.jsonl').open('x') as output, (directory/'stderr.log').open('x') as errors:
            child = subprocess.Popen(command, cwd=work, env=env, stdout=output, stderr=errors, start_new_session=True)
            try:
                deadline = time.monotonic() + (180 if fixture else item['timeout_seconds'])
                while True:
                    if cancel_event and cancel_event.is_set():
                        raise InterruptedError('Batch cancelled; preserve attempt without retry')
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, 180 if fixture else item['timeout_seconds'])
                    try:
                        code = child.wait(timeout=min(1, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        pass
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    code = child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    code = child.wait()
            except BaseException:
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGTERM)
                    try:
                        child.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        os.killpg(child.pid, signal.SIGKILL)
                        child.wait()
                recorder.shutdown()
                recorder.server_close()
                dump(directory/'wire-index.json', {'requests':recorder.requests,
                    'usage_observations':recorder.observations,'interrupted':True})
                raise
        # Keep diagnostic files, never the temporary provider configuration or DB credentials.
        rollout = state/'cli/rollout'
        if rollout.exists():
            shutil.copytree(rollout, directory/'zcode-rollout', dirs_exist_ok=True)
    recorder.shutdown()
    recorder.server_close()
    dump(directory/'wire-index.json', {'requests':recorder.requests,'usage_observations':recorder.observations,
                                      'policy_violations':recorder.violations,'upstream_error':recorder.upstream_error})
    return {'started_at':started_at,'completed_at':now(),'exit_code':code,'timed_out':timed_out,
            'elapsed_seconds':time.monotonic()-started, 'requests':recorder.requests,
            'usage_observations_raw':recorder.observations,'policy_violations':recorder.violations,
            'upstream_error':recorder.upstream_error}


def collect_wire(directory):
    chunks = []
    provider_models = set()
    for path in sorted((directory/'wire').glob('response-*.sse')):
        response_chunks = []
        for line in path.read_text().splitlines():
            if not line.startswith('data:'):
                continue
            try:
                event = json.loads(line[5:])
            except ValueError:
                continue
            if event.get('model'):
                provider_models.add(event['model'])
            for choice in event.get('choices',[]):
                content = choice.get('delta',{}).get('content')
                if isinstance(content,str):
                    response_chunks.append(content)
        if response_chunks:
            chunks.append(''.join(response_chunks))
    answer = '\n\n'.join(chunks)
    (directory/'answer.md').write_text(answer)
    return answer, sorted(provider_models)


