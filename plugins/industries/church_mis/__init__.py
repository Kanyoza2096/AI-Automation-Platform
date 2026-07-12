# Lazy import to avoid circular dependency
def get_plugin():
    from .plugin import ChurchMISPlugin
    return ChurchMISPlugin
