"""Original build identities from verified saved actions, never a current compiler probe."""
import hashlib
import json
from pathlib import Path
import re

import action_cache
from . import elf

ROOT = action_cache.ROOT


class SavedBuilds:
    def __init__(self, read_bytes=Path.read_bytes, *, root=ROOT):
        self.root, self.read_bytes = Path(root).resolve(), read_bytes
        self.builds, self.compilers = {}, {}

    def raw(self, name):
        path = self.root.joinpath(*action_cache.relative(name).parts)
        if not path.resolve().is_relative_to(self.root) or any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError('Original build metadata path escapes its saved root')
        if path.stat().st_size > 32 * 1024**2:
            raise ValueError('Original build metadata exceeds its declared bound')
        return self.read_bytes(path)

    def blob(self, value):
        action_cache.valid_digest(value)
        raw = self.raw('.cache/benchmark-actions/v3/cas/' + value['hash'])
        if action_cache.digest(raw) != value:
            raise ValueError('Original build CAS metadata changed')
        return raw

    def compiler(self, identity):
        if identity not in self.compilers:
            if not isinstance(identity, str) or not re.fullmatch('[0-9a-f]{64}', identity):
                raise ValueError('Invalid original compiler identity')
            record = json.loads(self.raw('.cache/benchmark-grading/v1/profiles/' + identity + '.json'))
            if record['id'] != identity or action_cache.digest(action_cache.canonical(record['manifest']))['hash'] != identity:
                raise ValueError('Original compiler manifest changed')
            compiler = record['manifest']['compiler']
            filename = compiler['command'].removeprefix('/')
            if record['manifest']['files'][filename]['sha256'] != compiler['executable_sha256']:
                raise ValueError('Original compiler executable binding changed')
            version = compiler['version'].splitlines()[0]
            if not 1 <= len(version) <= 200:
                raise ValueError('Unbounded original compiler version')
            self.compilers[identity] = {'compiler_image_id': identity, 'compiler_version': version,
                'compiler_executable_sha256': compiler['executable_sha256'],
                'compiler_family': 'clang' if 'clang' in version.lower() else 'gcc' if version.startswith(('gcc ', 'g++ '))
                                   else 'rustc' if version.startswith('rustc ') else 'gc' if version.startswith('go version ') else 'unknown'}
        return self.compilers[identity]

    def load(self, identity):
        if identity not in self.builds:
            if not isinstance(identity, str) or not re.fullmatch('[0-9a-f]{64}', identity):
                raise ValueError('Invalid original build identity')
            result = json.loads(self.raw('.cache/benchmark-actions/v3/ac/' + identity))
            action = result['action']
            if (result.get('format') != action_cache.FORMAT or result.get('exit_code') != 0
                    or action_cache.digest(action_cache.canonical(action))['hash'] != identity
                    or action.get('kind') not in ('capability_target_build_v1', 'capability_abi_original_v1', 'capability_pointer_original_v1', 'capability_variant_object_original_v1', 'capability_indirect_call_original_v1', 'capability_optimized_location_build_v1', 'capability_native_language_abi_build_v1', 'capability_baseline_target_build_v1')):
                raise ValueError('Original build action identity mismatch')
            name = 'build.json' if action['kind'] == 'capability_abi_original_v1' else 'result.json'
            record = json.loads(self.blob(result['output_files'][name]['digest']))
            command = json.loads(self.blob(action['command_digest']))
            platform = command['platform']
            compiler = self.compiler(platform['compiler_image'])
            public = bytes.fromhex(record['public_hex'])
            matched = action['kind'] == 'capability_baseline_target_build_v1'
            original = platform.get('compiler_id') if matched else None
            if matched and original not in ('clang20', 'rust', 'go'):
                raise ValueError('Unregistered matched original compiler')
            if matched and original == 'clang20':
                extension = platform['compiler_extension']
                action_cache.valid_digest({'hash': extension, 'size_bytes': 0})
                manifest = json.loads(self.raw('.cache/benchmark-capabilities/baseline-clang20-tooling-v1/' + extension + '.json'))
                item = manifest['compiler']
                if (action_cache.digest(action_cache.canonical(manifest))['hash'] != extension
                        or manifest['base_image_id'] != platform['compiler_image']
                        or item['command'] != '/usr/bin/clang++-20' or 'clang version 20.' not in item['version']
                        or manifest['files'][item['command'].removeprefix('/')]['sha256'] != item['executable_sha256']):
                    raise ValueError('Original Clang compiler extension differs')
                compiler = {'compiler_image_id': extension, 'compiler_version': item['version'].splitlines()[0],
                            'compiler_executable_sha256': item['executable_sha256'], 'compiler_family': 'clang'}
            native = action['kind'] == 'capability_native_language_abi_build_v1' or matched and original in ('rust', 'go')
            language = original if matched and native else platform['language'] if native else 'cpp'
            if native:
                from .native_abi import images
                _, stripped = images.validate(bytes.fromhex(record['private_hex']), public, language)
            else:
                _, stripped = elf.stripped_matches(bytes.fromhex(record['private_hex']), public)
            commands = record.get('commands') or [record['compiler_command']]
            if not isinstance(commands, list) or not 1 <= len(commands) <= 16:
                raise ValueError('Invalid original compiler command inventory')
            flags = [value for argv in commands for value in argv if isinstance(value, str) and value.startswith('-')]
            standards = {value.removeprefix('-std=') for value in flags if value.startswith('-std=')}
            optimizations = {value for value in flags if re.fullmatch('-O[0123sgz]', value)}
            if native:
                if language == 'rust' and compiler['compiler_family'] == 'rustc':
                    editions = {flag.removeprefix('--edition=') for argv in commands for flag in argv if flag.startswith('--edition=')}
                    levels = {flag.removeprefix('opt-level=') for argv in commands for flag in argv if flag.startswith('opt-level=')}
                    if len(editions) != 1 or levels != {'2'}:
                        raise ValueError('Ambiguous Rust edition or optimization')
                    standards, optimizations = {'rust' + next(iter(editions))}, {'-O2'}
                elif language == 'go' and compiler['compiler_family'] == 'gc':
                    if (not any(argv[:2] == ['/opt/go/bin/go', 'build'] for argv in commands)
                            or {arg for argv in commands for arg in argv if arg.startswith('-gcflags')} != {'-gcflags=command-line-arguments=-S'}):
                        raise ValueError('Missing original Go compiler command')
                    standards, optimizations = {'go'}, {'compiler_default'}
                else:
                    raise ValueError('Native language and compiler identity disagree')
            elif len(standards) != 1 or len(optimizations) != 1:
                raise ValueError('Ambiguous original language standard or optimization')
            self.builds[identity] = {**compiler, 'build_id': identity, 'binary_sha256': hashlib.sha256(public).hexdigest(),
                'source_language': language, 'architecture': 'x86_64', 'standard': next(iter(standards)),
                'optimization': next(iter(optimizations)), 'strip_mode': 'strip_all_verified',
                'standard_library': 'rust_std' if language == 'rust' else 'go_runtime' if language == 'go' else 'none_freestanding' if '-nodefaultlibs' in flags else 'compiler_default_libstdc++',
                'link_mode': 'dynamic' if any(program[0] == 3 for program in stripped.programs) else 'static',
                'compiler_commands_sha256': hashlib.sha256(action_cache.canonical(commands)).hexdigest(),
                'compiler_commands': commands, 'binutils_image_id': platform['binutils_image']}
        return self.builds[identity]

    def target(self, case):
        value = self.load(case['build_id'])
        if value['binary_sha256'] != case['binary_sha256'] or value['source_language'] != case['source_language']:
            raise ValueError('Original build differs from its frozen task target')
        return value


def public_projection(value, public_manifest):
    # Measured convention/presence macros, linker symbols, source paths and
    # original object names remain private even in the post-run report.
    names = ('build_id', 'binary_sha256', 'compiler_family', 'compiler_version', 'compiler_image_id',
             'compiler_executable_sha256', 'source_language', 'architecture', 'standard', 'optimization',
             'strip_mode', 'standard_library', 'link_mode', 'binutils_image_id')
    result = {name: value[name] for name in names}
    flags = []
    for command in value['compiler_commands']:
        for flag in command[1:]:
            if (re.fullmatch(r'-std=c\+\+[0-9]+|-O[0123sgz]|-g|-gdwarf-[0-9]|-Wall|-Wextra', flag)
                    or re.fullmatch(r'-f(?:no-)?[a-z0-9-]+(?:=(?:none|generic|x86-64))?', flag)
                    or re.fullmatch(r'-m(?:no-)?[a-z0-9-]+(?:=(?:generic|x86-64))?', flag)
                    or flag in ('-static', '-pie', '-no-pie', '-nostdlib', '-nodefaultlibs', '-Wl,--build-id=none', '-Wl,--no-undefined')):
                if flag not in flags:
                    flags.append(flag)
    result['flag_summary'] = flags
    result['flag_summary_sha256'] = hashlib.sha256(action_cache.canonical(flags)).hexdigest()
    if public_manifest.get('condition') == 'native_abi_recover' or public_manifest.get('original_compiler') in ('rust', 'go'):
        allowed = (['--edition=2021', 'opt-level=2', 'panic=abort', 'lto=off', 'codegen-units=1']
                   if value['source_language'] == 'rust' else ['-trimpath', '-buildvcs=false', '-ldflags=-buildid='])
        arguments = {arg for argv in value['compiler_commands'] for arg in argv}
        flags = [arg for arg in allowed if arg in arguments]
        result['flag_summary'] = flags
        result['flag_summary_sha256'] = hashlib.sha256(action_cache.canonical(flags)).hexdigest()
    if any('-flto=1' in argv for argv in value['compiler_commands']):
        if '-flto=1' not in result['flag_summary']:
            result['flag_summary'].append('-flto=1')
        result['flag_summary_sha256'] = hashlib.sha256(action_cache.canonical(result['flag_summary'])).hexdigest()
    result['abi'] = ('rust_native' if value['source_language'] == 'rust' else 'go_abi_internal') if public_manifest.get('condition') == 'native_abi_recover' else (
        'go_abi_internal' if public_manifest.get('original_compiler') == 'go' else public_manifest.get('known_abi', 'measured_not_disclosed' if public_manifest.get('condition') == 'abi_recover' else 'linux_x86_64_sysv'))
    return result
