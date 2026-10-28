"""DSH loopback transport: validate final bytes before any HTTPS forwarding."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import http.client
import json
import re
from pathlib import Path
import ssl
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

import request_contract

ROOT = Path(__file__).resolve().parents[2]
NODE = ROOT / '.tools/node-v24.22.0-linux-x64/bin/node'
# Use the same attested Node binary as DSH, rather than an additional host
# decompressor. The installed Codex SDK sends zstd even for small SSE requests.
DECOMPRESS_ZSTD = """
const fs=require('node:fs'),zlib=require('node:zlib');
process.stdout.write(zlib.zstdDecompressSync(fs.readFileSync(0),
  {maxOutputLength:Number(process.argv[1])}));
"""


def decoded_body(raw, encoding, contract, remaining):
    if encoding in (None, 'identity'):
        return raw
    if encoding != 'zstd' or contract['protocol'] != 'openai-codex-responses':
        raise request_contract.Violation('unsupported_request_content_encoding')
    try:
        result = subprocess.run([str(NODE), '-e', DECOMPRESS_ZSTD,
            str(contract['max_request_bytes'])], input=raw, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, check=True, timeout=max(0.1, min(5, remaining)),
            env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
    except (subprocess.SubprocessError, OSError):
        raise request_contract.Violation('invalid_or_oversized_compressed_request') from None
    if not 0 < len(result.stdout) <= contract['max_request_bytes']:
        raise request_contract.Violation('invalid_or_oversized_compressed_request')
    return result.stdout


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


class PreSendHTTPSConnection(http.client.HTTPSConnection):
    """Recover only TCP/TLS establishment; never replay an HTTP request.

    No HTTP bytes, headers, or credentials are sent in connect(). The gate is
    charged once immediately before send(), including uncertain partial writes.
    """
    connect_attempts = 3
    connect_timeout = 10.0

    def __init__(self, host, *, gate, row, **options):
        self.gate, self.row = gate, row
        super().__init__(host, **options)

    def connect(self):
        if self.row.get('forwarded'):
            raise ConnectionAbortedError('http_request_replay_denied')
        if self._tunnel_host:
            raise OSError('unexpected_proxy_tunnel')
        for number in range(1, self.connect_attempts + 1):
            remaining = self.gate.remaining()
            if remaining <= 0:
                raise TimeoutError('connection_deadline_reached')
            self.timeout = min(self.connect_timeout, remaining)
            started = time.monotonic()
            attempt = {'sequence': number, 'http_bytes_sent': 0}
            self.row.setdefault('connection_attempts', []).append(attempt)
            try:
                self.row['transport_phase'] = 'tcp_connect'
                http.client.HTTPConnection.connect(self)
                attempt['tcp_seconds'] = round(time.monotonic() - started, 6)
                self.row['transport_phase'] = 'tls_handshake'
                self.sock.settimeout(max(0.001, min(self.connect_timeout, self.gate.remaining())))
                self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host,
                                                      do_handshake_on_connect=False)
                handshake = time.monotonic()
                self.sock.do_handshake()
                attempt['tls_seconds'] = round(time.monotonic() - handshake, 6)
                attempt['complete'] = True
                self.sock.settimeout(max(0.001, self.gate.remaining()))
                return
            except OSError as error:
                attempt['complete'] = False
                attempt['diagnostic'] = transport_diagnostic(error, self.row['transport_phase'])
                # No HTTP write has occurred. Verification failures, protocol
                # errors, and unknown failures are deliberately not retried.
                transient = isinstance(error, (ssl.SSLEOFError, ConnectionResetError,
                                               ConnectionAbortedError, TimeoutError))
                # HTTPConnection.close() also clears the pending HTTP request
                # state. Only discard the socket: the request has not been sent.
                if self.sock is not None:
                    self.sock.close()
                    self.sock = None
                if not transient or number == self.connect_attempts:
                    raise
                delay = 0.1 * number
                if self.gate.remaining() <= delay:
                    raise
                attempt['retry_backoff_seconds'] = delay
                time.sleep(delay)
            finally:
                attempt['elapsed_seconds'] = round(time.monotonic() - started, 6)

    def send(self, data):
        if self.sock is None:
            self.connect()
        self.row['transport_phase'] = 'http_request_send'
        if not self.row.get('forwarded'):
            self.gate.forward(self.row)
            self.row['http_send_started'] = True
        # Any exception here means delivery may have occurred: do not reconnect.
        super().send(data)

    def getresponse(self):
        self.row['transport_phase'] = 'http_response_headers'
        return super().getresponse()


class PreSendHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, request):
        gate, row = getattr(request, '_rea_gate', None), getattr(request, '_rea_row', None)
        if gate is None or row is None:
            return super().https_open(request)
        def connection(host, **options):
            return PreSendHTTPSConnection(host, gate=gate, row=row,
                context=self._context, **options)
        return self.do_open(connection, request)


def direct_opener(cafile):
    # Explicit direct egress; no inherited proxy or route fallback. TLS uses the
    # frozen trust bundle, hostname verification, SNI and HTTP/1.1 ALPN.
    context = ssl.create_default_context(cafile=cafile)
    context.set_alpn_protocols(['http/1.1'])
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
        PreSendHTTPSHandler(context=context))


def transport_diagnostic(error, phase):
    """Allowlisted metadata only: exception text may contain URLs or secrets."""
    cause = getattr(error, 'reason', error)
    if not isinstance(cause, BaseException):
        cause = error
    result = {'phase': phase, 'exception': type(error).__name__, 'cause': type(cause).__name__}
    for key in ('errno', 'verify_code'):
        value = getattr(cause, key, None)
        if isinstance(value, int):
            result[key] = value
    return result


def normalize_tool_hex(arguments):
    """Only translate unquoted hexadecimal integer values to exact JSON integers.

    Do not guess missing quotes/braces, decode strings, merge objects, or repair
    truncation. Restrict integers to the client's exactly representable range.
    """
    changed = False
    def replace(match):
        nonlocal changed
        token = match.group()
        if token.startswith('"'):
            return token
        before = arguments[:match.start()].rstrip()
        after = arguments[match.end():].lstrip()
        if not before or before[-1] not in ':[' + ',' or not after or after[0] not in ',}]':
            return token
        integer = int(token, 16)
        if abs(integer) > 9007199254740991:
            return token
        changed = True
        return str(integer)
    converted = re.sub(r'"(?:[^"\\]|\\.)*"|-?0[xX][0-9a-fA-F]+', replace, arguments)
    if not changed:
        return None
    try:
        value = json.loads(converted, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, RecursionError):
        return None
    return converted if isinstance(value, dict) else None


class ToolJSONRelay:
    """Buffer tool argument deltas only; preserve all other provider SSE frames."""
    def __init__(self, directory, row):
        self.directory, self.row = directory, row
        self.pending = bytearray()
        self.frame = bytearray()
        self.blocks = {}

    def event(self, frame):
        lines = frame.splitlines()
        data = [line[5:].strip() for line in lines if line.startswith(b'data:')]
        if len(data) != 1:
            return frame
        try:
            value = json.loads(data[0])
        except ValueError:
            return frame
        if not isinstance(value, dict):
            return frame
        kind, index = value.get('type'), value.get('index')
        if kind == 'content_block_start' and value.get('content_block', {}).get('type') == 'tool_use':
            self.blocks[index] = {'frames': [], 'parts': [], 'bytes': 0}
        block = self.blocks.get(index)
        delta = value.get('delta', {})
        if block is not None and kind == 'content_block_delta' and delta.get('type') == 'input_json_delta':
            part = delta.get('partial_json')
            if not isinstance(part, str):
                raise request_contract.Violation('invalid_tool_json_delta')
            block['frames'].append(frame)
            block['parts'].append(part)
            block['bytes'] += len(frame)
            if sum(x['bytes'] for x in self.blocks.values()) > 67108864:
                raise request_contract.Violation('provider_event_too_large')
            return b''
        if block is not None and kind == 'content_block_stop':
            del self.blocks[index]
            original = ''.join(block['parts'])
            converted = normalize_tool_hex(original)
            if converted is not None:
                number = len(self.row.setdefault('tool_json_normalizations', [])) + 1
                name = 'tool-json-' + str(self.row['sequence']) + '-' + str(number) + '.private.json'
                proof = {'rule': 'exact-hex-integer-v1', 'block_index': index,
                         'original': original, 'normalized': converted}
                path = self.directory / name
                # Private evidence is never linked into public reports.
                with path.open('x') as output:
                    path.chmod(0o600)
                    json.dump(proof, output, ensure_ascii=False)
                self.row['tool_json_normalizations'].append({'rule': proof['rule'],
                    'block_index': index, 'evidence_file': name,
                    'original_sha256': hashlib.sha256(original.encode()).hexdigest(),
                    'normalized_sha256': hashlib.sha256(converted.encode()).hexdigest()})
                event = {'type': 'content_block_delta', 'index': index,
                         'delta': {'type': 'input_json_delta', 'partial_json': converted}}
                return b'event: content_block_delta\ndata: ' + json.dumps(event).encode() + b'\n\n' + frame
            return b''.join(block['frames']) + frame
        return frame

    def feed(self, chunk):
        self.pending.extend(chunk)
        output = bytearray()
        while b'\n' in self.pending:
            end = self.pending.index(b'\n') + 1
            line = bytes(self.pending[:end]); del self.pending[:end]
            self.frame.extend(line)
            if line in (b'\n', b'\r\n'):
                output.extend(self.event(bytes(self.frame)))
                self.frame.clear()
        if len(self.frame) + len(self.pending) > 67108864:
            raise request_contract.Violation('provider_event_too_large')
        return bytes(output)

    def end(self):
        if self.blocks or self.frame or self.pending:
            raise request_contract.Violation('incomplete_tool_json_stream')


class Transport(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, directory, task, *, fixture_upstream=None):
        self.directory = Path(directory)
        self.gate = request_contract.Gate(request_contract.from_task(task))
        if self.gate.contract['protocol'] not in ('anthropic-messages', 'openai-responses', 'openai-codex-responses'):
            raise request_contract.Violation('unsupported_dsh_transport_route')
        self.responses, self.responses_lock = {}, threading.Lock()
        provider = task['runtime_attestation']['provider']
        endpoint = provider['base_url']
        parsed = urlsplit(endpoint)
        if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise request_contract.Violation('invalid_frozen_provider_endpoint')
        suffix = {'anthropic-messages': '/v1/messages', 'openai-responses': '/responses',
                  'openai-codex-responses': '/codex/responses'}[self.gate.contract['protocol']]
        if endpoint.rstrip('/').endswith('/v1') and suffix.startswith('/v1/'):
            suffix = suffix[3:]
        self.endpoint = endpoint.rstrip('/') + suffix
        self.fixture_upstream = fixture_upstream
        self.opener = direct_opener(task['runtime_attestation']['runtime']['manifest']['environment']['SSL_CERT_FILE'])
        super().__init__(('127.0.0.1', 0), Handler)

    @property
    def base_url(self):
        return f'http://127.0.0.1:{self.server_port}/v1'

    def save(self):
        with self.gate.lock:
            value = request_contract.decode(request_contract.canonical(self.gate.summary()))
        path = self.directory / 'request-budget.json'
        with path.open('x') as stream:
            json.dump(value, stream, sort_keys=True, indent=2)
            stream.write('\n')
        return value

    def interrupt(self):
        with self.responses_lock:
            for response, row in self.responses.items():
                if 'finished_at' not in row:
                    row['local_cleanup_requested'] = True
                try:
                    response.fp.raw._sock.shutdown(socket.SHUT_RDWR)
                except (AttributeError, OSError):
                    pass

    @property
    def deadline_ms(self):
        return int(time.time() * 1000 + self.gate.remaining() * 1000)


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_args):
        pass

    def reject(self, status, category):
        raw = json.dumps({'error': {'type': 'benchmark_request_contract', 'message': category}}).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        self.wfile.write(raw)

    def do_GET(self):
        self.reject(405, 'unsupported_request_method')

    def do_POST(self):
        server, row = self.server, None
        response_started, response = False, None
        phase = 'read_client_request'
        try:
            lengths = self.headers.get_all('Content-Length', [])
            if len(lengths) != 1 or not lengths[0].isdigit() or self.headers.get('Transfer-Encoding'):
                return self.reject(400, 'invalid_request_length')
            length = int(lengths[0])
            if not 0 < length <= server.gate.contract['max_request_bytes']:
                return self.reject(413, 'request_body_too_large')
            self.connection.settimeout(max(0.1, min(30, server.gate.remaining())))
            raw = self.rfile.read(length)
            if len(raw) != length:
                return self.reject(400, 'incomplete_request_body')
            if self.headers.get_content_type() != 'application/json':
                raise request_contract.Violation('invalid_request_content_type')
            encodings = self.headers.get_all('Content-Encoding', [])
            if len(encodings) > 1:
                raise request_contract.Violation('unsupported_request_content_encoding')
            encoding = encodings[0].strip().lower() if encodings else None
            decoded = decoded_body(raw, encoding, server.gate.contract, server.gate.remaining())
            row, _request = server.gate.begin(self.path, decoded)
            if 'requested_cap' in row:
                row['original_wire_sha256'] = hashlib.sha256(raw).hexdigest()
                raw = decoded = request_contract.canonical(_request)
                encoding = None
            row.update(wire_sha256=hashlib.sha256(raw).hexdigest(),
                       wire_encoding=encoding or 'identity', wire_bytes=len(raw),
                       decoded_bytes=len(decoded))
            names = ('Authorization', 'x-api-key', 'anthropic-version', 'anthropic-beta')
            if server.gate.contract['protocol'] == 'openai-codex-responses':
                if not self.headers.get('Authorization') or not self.headers.get('chatgpt-account-id'):
                    raise request_contract.Violation('missing_codex_account_headers')
                names = ('Authorization', 'chatgpt-account-id', 'originator', 'OpenAI-Beta',
                         'session-id', 'x-client-request-id', 'User-Agent')
            headers = {name: self.headers[name] for name in names
                       if self.headers.get(name)}
            headers['Content-Type'] = 'application/json'
            headers['Accept'] = 'text/event-stream' if row['stream'] else 'application/json'
            if encoding == 'zstd':
                headers['Content-Encoding'] = encoding
            request = urllib.request.Request(server.endpoint, data=raw, headers=headers, method='POST')
            # Only the offline control injects a fake upstream callable. It does
            # not relax URL validation or send dummy credentials to a real host.
            phase = 'upstream_connect_and_headers'
            if server.fixture_upstream is None:
                request._rea_gate, request._rea_row = server.gate, row
                response = server.opener.open(request, timeout=max(0.1, server.gate.remaining()))
            else:
                server.gate.forward(row, fixture=True)
                response = server.fixture_upstream(request, row)
            with server.responses_lock:
                server.responses[response] = row
            with response:
                row['http_status'] = response.status
                phase = 'downstream_headers'
                self.send_response(response.status)
                self.send_header('Content-Type', response.headers.get('Content-Type', 'text/event-stream'))
                self.send_header('Connection', 'close')
                self.end_headers()
                response_started = True
                observer = request_contract.ResponseObserver(server.gate, row)
                relay = request_contract.ResponseRelay(observer)
                tool_json = (ToolJSONRelay(server.directory, row)
                             if row['stream'] and server.gate.contract['protocol'] == 'anthropic-messages' else None)
                while True:
                    if server.gate.remaining() <= 0:
                        raise request_contract.Violation('wall_limit_reached')
                    try:
                        response.fp.raw._sock.settimeout(max(0.1, server.gate.remaining()))
                    except AttributeError:
                        pass  # The in-memory offline response has no socket.
                    phase = 'upstream_response_read'
                    chunk = response.read1(65536)
                    if not chunk:
                        break
                    ready = relay.feed(chunk)
                    if tool_json is not None:
                        ready = tool_json.feed(ready)
                    if ready:
                        phase = 'downstream_response_write'
                        self.wfile.write(ready)
                        self.wfile.flush()
                complete, tail = relay.end()
                if tool_json is not None:
                    tail = tool_json.feed(tail)
                    if complete:
                        tool_json.end()
                ending = ('wall_limit_reached' if response.status == 200 and server.gate.remaining() <= 0
                          else 'incomplete_provider_stream')
                server.gate.finish(row, status=response.status, complete=complete,
                                   error=None if complete else ending)
                if tail:
                    phase = 'downstream_terminal_write'
                    self.wfile.write(tail)
                    self.wfile.flush()
        except urllib.error.HTTPError as error:
            # Do not echo provider error bodies, which can contain account data.
            if row:
                server.gate.finish(row, status=error.code, complete=False, error='provider_http_error')
            self.reject(502, 'provider_http_error')
        except request_contract.Violation as error:
            if row:
                if not row['forwarded']:
                    row['rejected'] = str(error)
                server.gate.finish(row, complete=False, error=str(error))
            if not response_started:
                self.reject(400, str(error))
        except (OSError, TimeoutError, http.client.RemoteDisconnected, http.client.IncompleteRead) as error:
            eligible = False
            if row:
                row['transport_diagnostic'] = transport_diagnostic(error, row.get('transport_phase', phase) if phase == 'upstream_connect_and_headers' else phase)
                cause = row['transport_diagnostic']['cause']
                recovery = server.gate.contract.get('transport_recovery')
                eligible = bool(recovery and cause in recovery['allowed_causes']
                                and not row.get('local_cleanup_requested') and server.gate.remaining() > 0)
                row['transport_retry_eligible'] = eligible
                category = ('wall_limit_reached' if response_started and server.gate.remaining() <= 0 else
                            'local_execution_cleanup' if row.get('local_cleanup_requested') else
                            'provider_transport_interrupted')
                server.gate.finish(row, complete=False, error=category)
            if not response_started:
                try:
                    # Recognized by the installed SDK's TRANSPORT recovery.
                    # The gate still authorizes only the same payload, exact
                    # allowlisted cause, bounded retries and remaining budget.
                    self.reject(502, 'provider connection interrupted' if eligible else 'provider_transport_interrupted')
                except OSError:
                    pass
        except Exception as error:
            if row:
                row['transport_diagnostic'] = transport_diagnostic(error, row.get('transport_phase', phase) if phase == 'upstream_connect_and_headers' else phase)
                server.gate.finish(row, complete=False, error='invalid_provider_response')
            if not response_started:
                self.reject(502, 'invalid_provider_response')
        finally:
            if response is not None:
                with server.responses_lock:
                    server.responses.pop(response, None)
            if row and 'finished_at' not in row:
                server.gate.finish(row, complete=False, error='provider_transport_interrupted')
            self.close_connection = True


@contextmanager
def transport(directory, task, **options):
    server = Transport(directory, task, **options)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.interrupt()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        server.save()
