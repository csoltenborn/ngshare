import re
import urllib.parse
from typing import Any

_MEMORY_MULTIPLIERS = {
    '': 1,
    'K': 1000,
    'M': 1000 ** 2,
    'G': 1000 ** 3,
    'T': 1000 ** 4,
    'KI': 1024,
    'MI': 1024 ** 2,
    'GI': 1024 ** 3,
    'TI': 1024 ** 4,
}

VALID_ROLES = {'admin', 'instructor', 'student'}
VALID_CATEGORIES = {'cpu', 'memory'}
VALID_LIMIT_KEYS = {'limit', 'guarantee'}
MAX_MEMORY_BYTES = 1024 ** 4  # 1 TiB (covers 1T = 10^12 and 1Ti = 2^40)
MAX_CPU = 64.0


class ValidationError(Exception):
    def __init__(self, message: str, errors: dict[str, str] | None = None):
        super().__init__(message)
        self.message = message
        self.errors = errors if isinstance(errors, dict) else {}


def parse_cpu(value: Any) -> float | None:
    if value in (None, ''):
        return None
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_memory_to_bytes(value: Any) -> int | None:
    if value in (None, ''):
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        return int(float(value))

    text = str(value).strip()
    match = re.fullmatch(r'(?i)\s*([0-9]+(?:\.[0-9]+)?)\s*([kmgt]i?|)\s*', text)
    if not match:
        return None

    number_text, suffix = match.groups()
    factor = _MEMORY_MULTIPLIERS.get((suffix or '').upper())
    if factor is None:
        return None

    return int(float(number_text) * factor)


def validate_course_id(course_id: Any) -> str:
    if not isinstance(course_id, str):
        raise ValidationError("Course id must be a string", {'id': "Must be a string"})
    course_id = course_id.strip()
    if not re.match(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$', course_id):
        raise ValidationError(
            "Course id must match ^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$",
            {'id': "Must match ^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$ without . or /"},
        )
    return course_id


def validate_display_name(display_name: Any) -> str | None:
    if display_name is None:
        return None
    if not isinstance(display_name, str):
        raise ValidationError("Display name must be a string", {'display_name': "Must be a string"})
    trimmed = display_name.strip()
    if not trimmed:
        return None
    if len(trimmed) > 100:
        raise ValidationError("Display name exceeds 100 characters", {'display_name': "Must be ≤ 100 characters"})
    if any(ord(c) < 32 or (127 <= ord(c) <= 159) for c in trimmed):
        raise ValidationError(
            "Display name contains control characters",
            {'display_name': "Must not contain control characters"},
        )
    return trimmed


def validate_git_url(url: Any, allowed_git_hosts: list[str] | tuple[str, ...] | None = None) -> str | None:
    if url is None:
        return None
    if not isinstance(url, str):
        raise ValidationError("Git URL must be a string", {'git.url': "Must be a string"})
    url = url.strip()
    if not url:
        return None
    if len(url) > 500:
        raise ValidationError("Git URL exceeds 500 characters", {'git.url': "Must be ≤ 500 characters"})

    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https':
        raise ValidationError("Git URL must use https:// scheme", {'git.url': "Must use https:// scheme"})
    if not parsed.hostname:
        raise ValidationError("Git URL must include a hostname", {'git.url': "Must include a hostname"})
    if parsed.username or parsed.password or '@' in parsed.netloc:
        raise ValidationError("Git URL must not contain user credentials", {'git.url': "Must not contain credentials"})
    if parsed.query:
        raise ValidationError("Git URL must not contain query parameters", {'git.url': "Must not contain query parameters"})
    if parsed.fragment:
        raise ValidationError("Git URL must not contain a fragment", {'git.url': "Must not contain a fragment"})

    if allowed_git_hosts:
        allowed = [h.lower().strip() for h in allowed_git_hosts if isinstance(h, str) and h.strip()]
        if allowed and parsed.hostname.lower() not in allowed:
            raise ValidationError(
                f"Git host '{parsed.hostname}' is not in allowed_git_hosts",
                {'git.url': f"Host '{parsed.hostname}' not allowed"},
            )
    return url


def validate_git_branch(branch: Any) -> str:
    if branch is None or branch == '':
        return 'main'
    if not isinstance(branch, str):
        raise ValidationError("Git branch must be a string", {'git.branch': "Must be a string"})
    branch = branch.strip()
    if not re.match(r'^[A-Za-z0-9._/-]{1,100}$', branch):
        raise ValidationError("Invalid git branch name format", {'git.branch': "Must match ^[A-Za-z0-9._/-]{1,100}$"})
    if branch.startswith('-') or branch.startswith('/'):
        raise ValidationError("Branch name cannot start with '-' or '/'", {'git.branch': "Cannot start with '-' or '/'"})
    if '..' in branch or '//' in branch:
        raise ValidationError("Branch name cannot contain '..' or '//'", {'git.branch': "Cannot contain '..' or '//'"})
    if branch.endswith('/') or branch.endswith('.lock'):
        raise ValidationError("Branch name cannot end with '/' or '.lock'", {'git.branch': "Cannot end with '/' or '.lock'"})
    return branch


def validate_git_username(username: Any) -> str:
    if username is None or username == '':
        return 'x-access-token'
    if not isinstance(username, str):
        raise ValidationError("Git username must be a string", {'git.username': "Must be a string"})
    username = username.strip()
    if not re.match(r'^[A-Za-z0-9._-]{1,100}$', username):
        raise ValidationError("Invalid git username format", {'git.username': "Must match ^[A-Za-z0-9._-]{1,100}$"})
    return username


def validate_git_token(token: Any) -> str | None:
    if token is None:
        return None
    if not isinstance(token, str):
        raise ValidationError("Git token must be a string", {'git.token': "Must be a string"})
    if len(token) < 1 or len(token) > 512:
        raise ValidationError("Git token must be between 1 and 512 characters", {'git.token': "Length must be 1-512"})
    if any(c.isspace() or ord(c) < 32 or (127 <= ord(c) <= 159) for c in token):
        raise ValidationError(
            "Git token cannot contain whitespace or control characters",
            {'git.token': "Cannot contain whitespace or control characters"},
        )
    return token


def validate_instructors(instructors: Any, is_create: bool = False) -> list[str]:
    if not isinstance(instructors, list):
        raise ValidationError("Instructors must be a list of usernames", {'instructors': "Must be a list"})
    if is_create and len(instructors) == 0:
        raise ValidationError("At least one instructor is required", {'instructors': "Must have at least one instructor"})
    cleaned = []
    for idx, inst in enumerate(instructors):
        if not isinstance(inst, str) or not inst.strip():
            raise ValidationError(
                f"Instructor at index {idx} must be a non-empty string",
                {f'instructors[{idx}]': "Must be a non-empty string"},
            )
        cleaned.append(inst.strip())
    return cleaned


def validate_enabled(enabled: Any) -> bool:
    if not isinstance(enabled, bool):
        raise ValidationError("Enabled must be a boolean", {'enabled': "Must be boolean"})
    return enabled


def validate_allowed_git_hosts(hosts: Any) -> list[str]:
    if not isinstance(hosts, list):
        raise ValidationError("allowed_git_hosts must be a list", {'allowed_git_hosts': "Must be a list"})
    if len(hosts) > 50:
        raise ValidationError("allowed_git_hosts cannot exceed 50 entries", {'allowed_git_hosts': "≤ 50 entries allowed"})

    host_regex = re.compile(r'^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$')
    result = []
    errors = {}
    for idx, h in enumerate(hosts):
        if not isinstance(h, str) or not h.strip():
            errors[f'allowed_git_hosts[{idx}]'] = "Must be a non-empty string"
            continue
        h_clean = h.strip().lower()
        if len(h_clean) > 253 or not host_regex.match(h_clean):
            errors[f'allowed_git_hosts[{idx}]'] = f"Invalid hostname format '{h}'"
            continue
        result.append(h_clean)

    if errors:
        raise ValidationError("Invalid git hosts in list", errors)
    return result


def validate_resource_limits(limits: Any) -> dict:
    if limits is None:
        return {}
    if not isinstance(limits, dict):
        raise ValidationError("Resource limits must be an object", {'resource_limits': "Must be an object"})

    errors = {}
    cleaned = {}

    for role, role_val in limits.items():
        if role not in VALID_ROLES:
            errors[f'resource_limits.{role}'] = f"Unknown role '{role}', must be one of {sorted(VALID_ROLES)}"
            continue
        if role_val is None:
            continue
        if not isinstance(role_val, dict):
            errors[f'resource_limits.{role}'] = "Role configuration must be an object"
            continue

        cleaned_role = {}
        for category, cat_val in role_val.items():
            if category not in VALID_CATEGORIES:
                errors[f'resource_limits.{role}.{category}'] = (
                    f"Unknown category '{category}', must be one of {sorted(VALID_CATEGORIES)}"
                )
                continue
            if cat_val is None:
                continue
            if not isinstance(cat_val, dict):
                errors[f'resource_limits.{role}.{category}'] = "Category configuration must be an object"
                continue

            cleaned_cat = {}
            for key, val in cat_val.items():
                if key not in VALID_LIMIT_KEYS:
                    errors[f'resource_limits.{role}.{category}.{key}'] = (
                        f"Unknown limit key '{key}', must be one of {sorted(VALID_LIMIT_KEYS)}"
                    )
                    continue

                if val is None:
                    cleaned_cat[key] = None
                    continue

                field_path = f'resource_limits.{role}.{category}.{key}'

                if category == 'cpu':
                    if isinstance(val, bool) or not isinstance(val, (int, float)):
                        errors[field_path] = "CPU value must be a number or null"
                    else:
                        cpu_f = float(val)
                        if cpu_f <= 0 or cpu_f > MAX_CPU:
                            errors[field_path] = f"CPU value must be between 0 and {MAX_CPU}"
                        else:
                            cleaned_cat[key] = cpu_f
                elif category == 'memory':
                    if isinstance(val, bool) or not isinstance(val, (str, int, float)):
                        errors[field_path] = "Memory value must be a string or number or null"
                    else:
                        val_str = str(val).strip()
                        match = re.fullmatch(r'(?i)\s*([0-9]+(?:\.[0-9]+)?)\s*([kmgt]i?|)\s*', val_str)
                        bytes_val = parse_memory_to_bytes(val_str)
                        if not match or bytes_val is None or bytes_val <= 0 or bytes_val > MAX_MEMORY_BYTES:
                            errors[field_path] = "Memory must match ^[0-9]+(\\.[0-9]+)?([KMGT]i?)?$ and be > 0 and ≤ 1T"
                        else:
                            cleaned_cat[key] = val_str

            # Check guarantee <= limit
            if category == 'cpu':
                limit_cpu = cleaned_cat.get('limit')
                guar_cpu = cleaned_cat.get('guarantee')
                if limit_cpu is not None and guar_cpu is not None and guar_cpu > limit_cpu:
                    errors[f'resource_limits.{role}.cpu'] = "CPU guarantee cannot exceed limit"
            elif category == 'memory':
                limit_mem = cleaned_cat.get('limit')
                guar_mem = cleaned_cat.get('guarantee')
                if limit_mem is not None and guar_mem is not None:
                    l_b = parse_memory_to_bytes(limit_mem)
                    g_b = parse_memory_to_bytes(guar_mem)
                    if l_b is not None and g_b is not None and g_b > l_b:
                        errors[f'resource_limits.{role}.memory'] = "Memory guarantee cannot exceed limit"

            if cleaned_cat:
                cleaned_role[category] = cleaned_cat

        if cleaned_role:
            cleaned[role] = cleaned_role

    if errors:
        raise ValidationError("Resource limits validation failed", errors)
    return cleaned


ALLOWED_CLASS_KEYS = {'id', 'display_name', 'instructors', 'enabled', 'git', 'resource_limits'}
ALLOWED_GIT_KEYS = {'url', 'branch', 'username', 'token'}


def validate_class_payload(
    data: Any,
    is_create: bool = True,
    allowed_git_hosts: list[str] | tuple[str, ...] | None = None,
) -> dict:
    if not isinstance(data, dict):
        raise ValidationError("Request body must be a JSON object", {'body': "Must be an object"})

    errors = {}
    cleaned = {}

    # Check unknown top-level keys
    for k in data.keys():
        if k not in ALLOWED_CLASS_KEYS:
            errors[k] = f"Unknown field '{k}'"

    # ID
    if is_create:
        if 'id' not in data:
            errors['id'] = "Class id is required"
        else:
            try:
                cleaned['id'] = validate_course_id(data['id'])
            except ValidationError as e:
                errors.update(e.errors)
    else:
        if 'id' in data:
            # Let caller check if id matches existing class
            cleaned['id'] = str(data['id'])

    # Display name
    if 'display_name' in data:
        try:
            cleaned['display_name'] = validate_display_name(data['display_name'])
        except ValidationError as e:
            errors.update(e.errors)

    # Enabled
    if 'enabled' in data:
        try:
            cleaned['enabled'] = validate_enabled(data['enabled'])
        except ValidationError as e:
            errors.update(e.errors)
    elif is_create:
        cleaned['enabled'] = True

    # Instructors
    if 'instructors' in data:
        try:
            cleaned['instructors'] = validate_instructors(data['instructors'], is_create=is_create)
        except ValidationError as e:
            errors.update(e.errors)
    elif is_create:
        errors['instructors'] = "At least one instructor is required"

    # Git
    if 'git' in data:
        git_val = data['git']
        if git_val is None:
            cleaned['git'] = None
        elif not isinstance(git_val, dict):
            errors['git'] = "git must be an object"
        else:
            cleaned_git = {}
            for k in git_val.keys():
                if k not in ALLOWED_GIT_KEYS:
                    errors[f'git.{k}'] = f"Unknown git field '{k}'"

            if 'url' in git_val:
                try:
                    cleaned_git['url'] = validate_git_url(git_val['url'], allowed_git_hosts)
                except ValidationError as e:
                    errors.update(e.errors)

            if 'branch' in git_val:
                try:
                    cleaned_git['branch'] = validate_git_branch(git_val['branch'])
                except ValidationError as e:
                    errors.update(e.errors)
            elif is_create and cleaned_git.get('url'):
                cleaned_git['branch'] = 'main'

            if 'username' in git_val:
                try:
                    cleaned_git['username'] = validate_git_username(git_val['username'])
                except ValidationError as e:
                    errors.update(e.errors)
            elif is_create and cleaned_git.get('url'):
                cleaned_git['username'] = 'x-access-token'

            if 'token' in git_val:
                # token can be null or string
                token_raw = git_val['token']
                if token_raw is None or token_raw == '':
                    cleaned_git['token'] = ''  # indicates clear
                else:
                    try:
                        cleaned_git['token'] = validate_git_token(token_raw)
                    except ValidationError as e:
                        errors.update(e.errors)

            cleaned['git'] = cleaned_git

    # Resource limits
    if 'resource_limits' in data:
        try:
            cleaned['resource_limits'] = validate_resource_limits(data['resource_limits'])
        except ValidationError as e:
            errors.update(e.errors)

    if errors:
        raise ValidationError("Validation failed", errors)
    return cleaned
