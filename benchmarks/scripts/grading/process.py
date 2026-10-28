"""Bound both pipes while a sandboxed process runs; RLIMIT_FSIZE does not do this."""
import os
import selectors
import signal
import subprocess
import time


class OutputLimitExceeded(RuntimeError):
    pass


def run(command, *, input=b'', timeout=20, output_limit=4 * 1024**2, env=None, preexec_fn=None):
    if not isinstance(input, bytes):
        raise TypeError('Process input must be bytes')
    streams = {'stdout': bytearray(), 'stderr': bytearray()}
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=env, preexec_fn=preexec_fn, start_new_session=True)
    deadline = time.monotonic() + timeout
    position = 0
    try:
        with selectors.DefaultSelector() as selector:
            for name in streams:
                pipe = getattr(process, name)
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ, name)
            os.set_blocking(process.stdin.fileno(), False)
            if input:
                selector.register(process.stdin, selectors.EVENT_WRITE, 'stdin')
            else:
                process.stdin.close()
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                for key, _ in selector.select(min(remaining, 0.1)):
                    if key.data == 'stdin':
                        try:
                            position += os.write(key.fd, input[position:position + 65536])
                        except BrokenPipeError:
                            position = len(input)
                        if position == len(input):
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                        continue
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                        continue
                    if len(streams[key.data]) + len(chunk) > output_limit:
                        raise OutputLimitExceeded(f'{key.data} exceeded {output_limit} bytes')
                    streams[key.data].extend(chunk)
            return subprocess.CompletedProcess(command, process.wait(timeout=max(0.01, deadline - time.monotonic())),
                                               bytes(streams['stdout']), bytes(streams['stderr']))
    finally:
        # Also terminate descendants retaining inherited pipes after the leader exits.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        for pipe in (process.stdin, process.stdout, process.stderr):
            if not pipe.closed:
                pipe.close()
