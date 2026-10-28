"""Reader-facing client names without rewriting saved execution identities."""


def route_label(model, info=None):
    """Name the client/provider route; historical adapter metadata stays intact."""
    identity = str(model).lower()
    if identity.startswith('gpt-'):
        return 'Codex'
    if identity.startswith('deepseek-'):
        return 'DeepSeek Harness'
    if identity.startswith('glm-'):
        return 'ZCode'
    return (info or {}).get('harness') or 'Harness unspecified'


def route_key(model, info=None):
    return route_label(model, info).lower().replace(' ', '-')
