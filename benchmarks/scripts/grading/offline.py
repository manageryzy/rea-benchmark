"""An offline measurement may invoke compilers/sandboxes, never a model harness."""
from contextlib import contextmanager
from pathlib import Path
import socket
import subprocess
import shutil
from unittest.mock import patch


@contextmanager
def guard():
    from .native import isolation
    native = isolation()
    compilers = {Path(shutil.which(name)).resolve() for name in ('gcc', 'g++')}
    compilers |= {(native.toolchain(language) / 'bin' / name).resolve() for language, name in [('rust', 'rustc'), ('go', 'go')]}
    # toolchain() may itself query the rustup/go launcher; only version/root probes are allowed.
    compilers |= {Path(shutil.which(name)).resolve() for name in ('rustc', 'go') if shutil.which(name)}
    original = subprocess.Popen
    def checked(command, *args, **kwargs):
        if isinstance(command, (str, bytes)) or kwargs.get('shell'):
            raise RuntimeError('Shell processes forbidden during offline measurement')
        executable = Path(command[0])
        from .runtime import permitted_command
        arguments = list(command[1:])
        sandbox = str(executable) == '/usr/bin/bwrap'
        probe = executable.resolve() in compilers and arguments in [['--version'], ['version'], ['--print', 'sysroot'], ['env', 'GOROOT']]
        helper_root = Path(__file__).resolve().parents[3] / '.cache/benchmark-runtime/go-imports'
        helper = executable.is_absolute() and executable.parent.resolve() == helper_root.resolve() and len(arguments) == 0
        if not (sandbox or probe or helper or permitted_command(command)):
            raise RuntimeError('Process refused during offline measurement: ' + str(executable))
        return original(command, *args, **kwargs)
    with patch.object(socket.socket, 'connect', side_effect=RuntimeError('Network forbidden during offline measurement')), \
         patch.object(socket.socket, 'connect_ex', side_effect=RuntimeError('Network forbidden during offline measurement')), \
         patch.object(subprocess, 'Popen', side_effect=checked):
        yield
