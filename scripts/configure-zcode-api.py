#!/usr/bin/env python3
"""Configure the local ZCode prepaid provider without exposing its API key."""
import argparse
import getpass
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / '.zcode-state/.zcode/v2/provider_config.json'
PROVIDER = 'zai-prepaid'
ENDPOINT = 'https://api.z.ai/api/paas/v4'
DEFAULT_MODEL = 'glm-5.3'


def environment_credentials(environ=None, profile=None):
    values = os.environ if environ is None else environ
    names = ('GLM_BASE_URL', 'GLM_AUTH_TOKEN')
    credentials = {name: values.get(name, '').strip() for name in names}
    if not all(credentials.values()):
        profile = Path.home() / '.profile' if profile is None else Path(profile)
        if profile.exists():
            probe = ('import json, os; print(json.dumps({k: os.environ.get(k, "") '
                     'for k in ("GLM_BASE_URL", "GLM_AUTH_TOKEN")}))')
            # Read only these two variables; suppress all profile output and traces.
            result = subprocess.run(
                ['/bin/bash', '--noprofile', '--norc', '-c',
                 'set +x; set -a; . "$1" >/dev/null 2>&1; set +x; '
                 'exec /usr/bin/python3 -c "$2"',
                 'zcode-glm-profile', str(profile), probe],
                env=dict(values), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, timeout=15, check=False)
            if result.returncode:
                raise ValueError('Could not load the GLM variables from the WSL profile.')
            loaded = json.loads(result.stdout)
            for name in names:
                if not credentials[name]:
                    credentials[name] = (loaded.get(name) or '').strip()
    if not any(credentials.values()):
        return None
    if not all(credentials.values()):
        raise ValueError('Both GLM_BASE_URL and GLM_AUTH_TOKEN must be set.')
    return credentials['GLM_BASE_URL'], credentials['GLM_AUTH_TOKEN']


def validate_endpoint(endpoint):
    endpoint = endpoint.strip().rstrip('/')
    parsed = urlsplit(endpoint)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or
            parsed.password or parsed.query or parsed.fragment or
            any(c.isspace() for c in endpoint)):
        raise ValueError('GLM_BASE_URL must be an HTTPS base URL without credentials or a query.')
    return endpoint


def load_config(path):
    if not path.exists():
        return {'schemaVersion': 1, 'config': {
            'providerConfigRules': {'providerRules': []},
            'modelConfigRules': {'providerModelRules': [], 'manualProviderModelRules': []}}}
    value = json.loads(path.read_text())
    if value.get('schemaVersion') != 1:
        raise ValueError('Unsupported provider configuration version; existing file preserved.')
    return value


def configure(path, model, key=None, endpoint=ENDPOINT, select=True):
    endpoint = validate_endpoint(endpoint)
    value = load_config(path)
    config = value['config']
    rules = config['providerConfigRules']['providerRules']
    rule = next((r for r in rules if r['providerId'] == PROVIDER), None)
    if rule is None:
        rule = {'providerId': PROVIDER, 'config': {}}
        rules.append(rule)
    access = rule['config'].get('access') or {}
    saved_key = access.get('apiKey')
    if key is not None:
        saved_key = key.strip()
        if not saved_key or any(c.isspace() for c in saved_key):
            raise ValueError('The API key must be nonempty and contain no whitespace.')
    rule.update(templateId='zai-standard-api', providerName='Z.ai prepaid API', enabled=True)
    rule['config'].update(group='standard-personal',
        access={'type': 'api-key', 'apiKey': saved_key},
        api={'type': 'openai-chat-completions', 'baseUrl': endpoint})
    ids = rule['config'].setdefault('personalModelIds', [])
    if model not in ids:
        ids.append(model)
    models = config['modelConfigRules']
    manual = any(r['providerId'] == PROVIDER and r['modelId'] == model
                 for r in models['manualProviderModelRules'])
    if not manual:
        model_rules = models['providerModelRules']
        selected = next((r for r in model_rules if r['providerId'] == PROVIDER and r['modelId'] == model), None)
        if selected is None:
            selected = {'providerId': PROVIDER, 'modelId': model, 'config': {}}
            model_rules.append(selected)
        selected['config']['enabled'] = True
    if select:
        config['defaultModelSelection'] = {
            'providerId': PROVIDER, 'modelId': model, 'options': {'reasoningLevel': 'high'}}
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    descriptor, temporary = tempfile.mkstemp(prefix='.provider-', suffix='.json', dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(value, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return value


def status(path):
    config = load_config(path)['config']
    rule = next((r for r in config['providerConfigRules']['providerRules'] if r['providerId'] == PROVIDER), None)
    selection = config.get('defaultModelSelection', {})
    api = (rule['config'].get('api') or {}) if rule else {}
    return {'provider': PROVIDER, 'base_url': api.get('baseUrl'),
        'model': selection.get('modelId'),
        'api_key_present': bool(rule and (rule['config'].get('access') or {}).get('apiKey')),
        'config_path': str(path), 'model_requests_made': 0}


def main():
    parser = argparse.ArgumentParser(description='Set up Z.ai prepaid API access for this workspace.')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--prepare', action='store_true', help='Prepare the route without prompting for a key.')
    mode.add_argument('--status', action='store_true', help='Show nonsecret configuration status only.')
    mode.add_argument('--from-env', action='store_true', help='Use GLM_BASE_URL and GLM_AUTH_TOKEN, falling back to ~/.profile.')
    mode.add_argument('--sync-env', action='store_true', help='Refresh environment credentials if present, preserving model selection.')
    parser.add_argument('--quiet', action='store_true', help='Suppress successful configuration output.')
    parser.add_argument('--model', default=DEFAULT_MODEL, help='Model API ID (default: glm-5.3).')
    args = parser.parse_args()
    if args.status:
        print(json.dumps(status(CONFIG), indent=2))
        return
    try:
        key = None
        endpoint = ENDPOINT
        if args.from_env or args.sync_env:
            credentials = environment_credentials()
            if credentials is None:
                if args.sync_env:
                    return
                raise ValueError('The GLM environment variables are missing.')
            endpoint, key = credentials
        elif not args.prepare:
            if not sys.stdin.isatty():
                parser.error('Run in your WSL terminal to enter the API key with hidden input.')
            print(f'Z.ai prepaid API · {ENDPOINT} · {args.model}')
            key = getpass.getpass('Z.ai API key (hidden): ')
        configure(CONFIG, args.model, key, endpoint=endpoint, select=not args.sync_env)
    except (ValueError, KeyError, TypeError, OSError, subprocess.TimeoutExpired):
        print('Configuration could not be saved. Existing configuration is preserved; no key was printed.', file=sys.stderr)
        raise SystemExit(1)
    if args.quiet:
        return
    value = status(CONFIG)
    print('Prepaid API route configured.' if value['api_key_present'] else 'Prepaid API route prepared; API key still required.')
    print('Model:', value['model'])
    print('Base URL:', value['base_url'])
    print('Config:', CONFIG)
    print('No model request was made.')


if __name__ == '__main__':
    main()
