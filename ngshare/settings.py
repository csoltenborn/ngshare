from .settings_validation import (
    validate_resource_limits,
    validate_allowed_git_hosts,
    ValidationError,
)

GLOBAL_SETTINGS_REGISTRY = {
    'default_resource_limits': {
        'default': {},
        'validator': validate_resource_limits,
        'admin_only_read': False,
    },
    'allowed_git_hosts': {
        'default': [],
        'validator': validate_allowed_git_hosts,
        'admin_only_read': True,
    },
}


def get_registered_keys() -> list[str]:
    return list(GLOBAL_SETTINGS_REGISTRY.keys())


def get_default_setting(key: str):
    if key not in GLOBAL_SETTINGS_REGISTRY:
        raise KeyError(f"Unknown setting key: {key}")
    return GLOBAL_SETTINGS_REGISTRY[key]['default']


def is_admin_only_read(key: str) -> bool:
    if key not in GLOBAL_SETTINGS_REGISTRY:
        return True
    return GLOBAL_SETTINGS_REGISTRY[key].get('admin_only_read', False)


def validate_global_setting(key: str, value: any):
    if key not in GLOBAL_SETTINGS_REGISTRY:
        raise ValidationError(
            f"Unknown global setting key '{key}'",
            {key: f"Unknown setting key '{key}'"},
        )
    validator = GLOBAL_SETTINGS_REGISTRY[key]['validator']
    return validator(value)
