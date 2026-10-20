#!/usr/bin/python3
"""Add the reviewed REA registration, preserving other Codex settings."""
import argparse
import hashlib
import os
from pathlib import Path
import stat
import tempfile
import tomllib

parser = argparse.ArgumentParser()
parser.add_argument('config', type=Path)
parser.add_argument('--platform', choices=('windows', 'linux'), required=True)
parser.add_argument('--apply', action='store_true')
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
fragment = (root / 'config' / f'codex-{args.platform}.toml').read_text()
expected = tomllib.loads(fragment)['mcp_servers']['rea']
target = args.config
if target.is_symlink():
    raise SystemExit('Refusing a symlink configuration path.')
before = target.read_bytes() if target.exists() else b''
parsed = tomllib.loads(before.decode())
existing = parsed.get('mcp_servers', {}).get('rea')
if existing is not None:
    if existing != expected:
        raise SystemExit('An existing REA registration differs; review it before replacing it.')
    print('REA registration already matches:', target)
    raise SystemExit(0)
after = before + (b'\n' if before and not before.endswith(b'\n') else b'') + b'\n' + fragment.encode()
verified = tomllib.loads(after.decode())
assert verified['mcp_servers'].pop('rea') == expected
if 'mcp_servers' not in parsed and not verified['mcp_servers']:
    verified.pop('mcp_servers')
assert verified == parsed, 'Unrelated settings changed.'
print('Planned addition to', target)
print(fragment)
if not args.apply:
    raise SystemExit(0)
target.parent.mkdir(parents=True, exist_ok=True)
if before:
    backup = target.with_name(target.name + '.rea-env.backup.' + hashlib.sha256(before).hexdigest()[:12])
    if backup.exists():
        assert backup.read_bytes() == before
    else:
        with backup.open('xb') as handle:
            os.chmod(backup, 0o600)
            handle.write(before)
    print('Backup:', backup)
if target.exists() and target.read_bytes() != before:
    raise SystemExit('Configuration changed during preparation; rerun.')
mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o600
fd, staged = tempfile.mkstemp(prefix='.rea-config-', dir=target.parent)
try:
    os.fchmod(fd, mode)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(after)
        handle.flush()
        os.fsync(handle.fileno())
    if target.exists() and target.read_bytes() != before:
        raise SystemExit('Configuration changed before publication; rerun.')
    os.replace(staged, target)
finally:
    if os.path.exists(staged):
        os.unlink(staged)
assert target.read_bytes() == after
print('Registered REA; restart Codex to load the server.')
