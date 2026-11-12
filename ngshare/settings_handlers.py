import datetime
import json
import logging
import urllib.parse
from tornado.web import authenticated

from .database.database import (
    Course,
    User,
    InstructorAssociation,
    StudentAssociation,
    CourseSettings,
    GlobalSetting,
    SettingsAudit,
)
from .ngshare import MyRequestHandler
from .secrets import encrypt, decrypt, get_token_status
from .settings import (
    GLOBAL_SETTINGS_REGISTRY,
    get_registered_keys,
    get_default_setting,
    is_admin_only_read,
    validate_global_setting,
)
from .settings_validation import (
    ValidationError,
    validate_class_payload,
)

logger = logging.getLogger(__name__)


def check_json_content_type(handler: MyRequestHandler):
    ct = handler.request.headers.get("Content-Type", "")
    if ct.split(";")[0].strip().lower() != "application/json":
        handler.json_error(415, "Content-Type must be application/json")


def parse_json_body(handler: MyRequestHandler) -> dict:
    if not handler.request.body:
        return {}
    try:
        body = json.loads(handler.request.body.decode('utf-8'))
    except Exception:
        handler.json_error(400, "Request body must be valid JSON")
    if not isinstance(body, dict):
        handler.json_error(400, "Request body must be a JSON object")
    return body


def is_service(handler: MyRequestHandler) -> bool:
    service_token = handler.settings.get('api_token')
    return bool(handler.auth_token and service_token and handler.auth_token == service_token)


def is_admin_caller(handler: MyRequestHandler) -> bool:
    return is_service(handler) or handler.is_admin()


def actor_name(handler: MyRequestHandler) -> str:
    if is_service(handler):
        return "hub-service"
    if handler.user:
        return handler.user.id
    return "unknown"


def get_allowed_git_hosts_from_db(db) -> list[str]:
    row = db.query(GlobalSetting).filter(GlobalSetting.key == 'allowed_git_hosts').one_or_none()
    if row and row.value_json:
        try:
            val = json.loads(row.value_json)
            if isinstance(val, list):
                return val
        except Exception:
            pass
    return []


SYSTEM_USERS = {'hub'}


def get_existing_users(db) -> list[dict[str, str]]:
    users = db.query(User).filter(~User.id.in_(SYSTEM_USERS)).order_by(User.id.asc()).all()
    user_names: dict[str, tuple[str, str]] = {}

    inst_assocs = (
        db.query(InstructorAssociation)
        .filter(
            (InstructorAssociation.first_name.isnot(None))
            | (InstructorAssociation.last_name.isnot(None))
        )
        .all()
    )
    for a in inst_assocs:
        fn = (a.first_name or "").strip()
        ln = (a.last_name or "").strip()
        if fn or ln:
            user_names[a.left_id] = (fn, ln)

    stud_assocs = (
        db.query(StudentAssociation)
        .filter(
            (StudentAssociation.first_name.isnot(None))
            | (StudentAssociation.last_name.isnot(None))
        )
        .all()
    )
    for a in stud_assocs:
        if a.left_id not in user_names:
            fn = (a.first_name or "").strip()
            ln = (a.last_name or "").strip()
            if fn or ln:
                user_names[a.left_id] = (fn, ln)

    result = []
    for u in users:
        fn, ln = user_names.get(u.id, ("", ""))
        if fn and ln:
            full_name = f"{fn} {ln}"
        elif fn or ln:
            full_name = fn or ln
        elif "." in u.id:
            parts = [p.capitalize() for p in u.id.split(".") if p]
            full_name = " ".join(parts)
        elif "_" in u.id:
            parts = [p.capitalize() for p in u.id.split("_") if p]
            full_name = " ".join(parts)
        else:
            full_name = ""

        if full_name and full_name.lower() != u.id.lower():
            display = f"{full_name} ({u.id})"
        else:
            display = u.id

        result.append({
            "id": u.id,
            "name": full_name,
            "display": display,
        })
    result.sort(key=lambda x: x["display"].lower())
    return result


def build_class_view(course: Course, handler: MyRequestHandler) -> dict:
    cs: CourseSettings | None = course.settings
    service_token = handler.settings.get('api_token')

    is_adm = is_admin_caller(handler)
    is_inst = bool(handler.user and course in handler.user.teaching)

    if cs:
        display_name = cs.display_name
        enabled = bool(cs.enabled)
        git_url = cs.git_url
        if git_url:
            git_branch = cs.git_branch or 'main'
            git_username = cs.git_username or 'x-access-token'
        else:
            git_branch = None
            git_username = None
        has_token = bool(cs.git_token_enc)
        token_status = get_token_status(cs.git_token_enc, key_material=service_token)
        try:
            resource_limits = json.loads(cs.resource_limits_json) if cs.resource_limits_json else {}
        except Exception:
            resource_limits = {}
    else:
        display_name = None
        enabled = True
        git_url = None
        git_branch = None
        git_username = None
        has_token = False
        token_status = 'none'
        resource_limits = {}

    git_dict = {
        'url': git_url,
        'branch': git_branch,
        'username': git_username,
        'has_token': has_token,
        'token_status': token_status,
    }

    instructors = sorted([u.id for u in course.instructors])
    student_count = len(course.students)

    permissions = {
        'edit_basic': is_adm or is_inst,
        'edit_git': is_adm or is_inst,
        'edit_limits': is_adm,
        'edit_instructors': is_adm,
        'enable_disable': is_adm,
    }

    return {
        'id': course.id,
        'display_name': display_name,
        'enabled': enabled,
        'git': git_dict,
        'resource_limits': resource_limits,
        'instructors': instructors,
        'student_count': student_count,
        'permissions': permissions,
    }


class GlobalSettingsHandler(MyRequestHandler):
    '/services/ngshare/settings/global'

    @authenticated
    def get(self):
        is_adm = is_admin_caller(self)
        is_inst = bool(self.user and len(self.user.teaching) > 0)

        if not (is_adm or is_inst):
            self.json_error(403, "Permission denied")

        # Load settings from DB
        db_rows = {row.key: row.value_json for row in self.db.query(GlobalSetting).all()}
        result = {}

        for key in get_registered_keys():
            if not is_adm and is_admin_only_read(key):
                continue

            if key in db_rows:
                try:
                    result[key] = json.loads(db_rows[key])
                except Exception:
                    result[key] = get_default_setting(key)
            else:
                result[key] = get_default_setting(key)

        self.json_success(settings=result)

    @authenticated
    def put(self):
        check_json_content_type(self)
        if not is_admin_caller(self):
            self.json_error(403, "Permission denied")

        body = parse_json_body(self)

        # Validate all keys belong to registry
        registered_keys = set(get_registered_keys())
        unknown_keys = [k for k in body.keys() if k not in registered_keys]
        if unknown_keys:
            self.json_error(
                400,
                f"Unknown global setting key(s): {unknown_keys}",
                errors={k: "Unknown setting key" for k in unknown_keys},
            )

        # Validate values (all-or-nothing)
        validated_values = {}
        errors = {}
        for k, v in body.items():
            try:
                validated_values[k] = validate_global_setting(k, v)
            except ValidationError as e:
                errors.update(e.errors)

        if errors:
            self.json_error(400, "Global settings validation failed", errors=errors)

        # Update in database in one transaction
        actor = actor_name(self)
        now = datetime.datetime.utcnow()
        changes = {}

        for k, val in validated_values.items():
            val_json = json.dumps(val)
            row = self.db.query(GlobalSetting).filter(GlobalSetting.key == k).one_or_none()
            if row:
                row.value_json = val_json
                row.updated_at = now
                row.updated_by = actor
            else:
                row = GlobalSetting(k, val_json, updated_by=actor)
                row.updated_at = now
                self.db.add(row)
            changes[k] = "<updated>"

        audit = SettingsAudit(
            actor=actor,
            scope="global",
            action="update",
            changes_json=json.dumps(changes),
            timestamp=now,
        )
        self.db.add(audit)
        self.db.commit()

        # Build full settings result
        full_settings = {}
        all_rows = {r.key: r.value_json for r in self.db.query(GlobalSetting).all()}
        for k in get_registered_keys():
            if k in all_rows:
                try:
                    full_settings[k] = json.loads(all_rows[k])
                except Exception:
                    full_settings[k] = get_default_setting(k)
            else:
                full_settings[k] = get_default_setting(k)

        self.json_success(settings=full_settings)


class ClassesHandler(MyRequestHandler):
    '/services/ngshare/classes'

    @authenticated
    def get(self):
        is_adm = is_admin_caller(self)
        if is_adm:
            courses = self.db.query(Course).order_by(Course.id.asc()).all()
        elif self.user:
            courses = (
                self.db.query(Course)
                .filter(Course.instructors.contains(self.user))
                .order_by(Course.id.asc())
                .all()
            )
        else:
            courses = []

        classes_view = [build_class_view(c, self) for c in courses]
        self.json_success(classes=classes_view)

    @authenticated
    def post(self):
        check_json_content_type(self)
        if not is_admin_caller(self):
            self.json_error(403, "Permission denied")

        body = parse_json_body(self)
        allowed_hosts = get_allowed_git_hosts_from_db(self.db)

        try:
            cleaned = validate_class_payload(body, is_create=True, allowed_git_hosts=allowed_hosts)
        except ValidationError as e:
            self.json_error(400, e.message, errors=e.errors)

        course_id = cleaned['id']
        if self.db.query(Course).filter(Course.id == course_id).one_or_none():
            self.json_error(409, f"Class '{course_id}' already exists")

        # Atomic creation
        actor = actor_name(self)
        now = datetime.datetime.utcnow()
        service_token = self.settings.get('api_token')

        instructors = [self.find_or_create_user(u) for u in cleaned['instructors']]
        course = Course(course_id, instructors)
        self.db.add(course)

        git_data = cleaned.get('git') or {}
        git_url = git_data.get('url')
        git_branch = git_data.get('branch', 'main')
        git_username = git_data.get('username', 'x-access-token')
        raw_token = git_data.get('token')

        token_enc = encrypt(raw_token, key_material=service_token) if raw_token else None

        res_limits = cleaned.get('resource_limits')
        res_limits_json = json.dumps(res_limits) if res_limits else None

        cs = CourseSettings(
            course_id=course_id,
            display_name=cleaned.get('display_name'),
            enabled=cleaned.get('enabled', True),
            git_url=git_url,
            git_branch=git_branch,
            git_username=git_username,
            git_token_enc=token_enc,
            resource_limits_json=res_limits_json,
            updated_by=actor,
        )
        cs.updated_at = now
        self.db.add(cs)

        # Audit record
        audit_changes = {
            'id': course_id,
            'display_name': cleaned.get('display_name'),
            'enabled': cleaned.get('enabled', True),
            'instructors': [u.id for u in instructors],
            'git': {
                'url': git_url,
                'branch': git_branch,
                'username': git_username,
                'token': "<set>" if raw_token else None,
            },
            'resource_limits': res_limits,
        }
        audit = SettingsAudit(
            actor=actor,
            scope="class",
            course_id=course_id,
            action="create",
            changes_json=json.dumps(audit_changes),
            timestamp=now,
        )
        self.db.add(audit)
        self.db.commit()

        self.set_status(201)
        self.json_success(**build_class_view(course, self))


class ClassHandler(MyRequestHandler):
    '/services/ngshare/classes/<id>'

    @authenticated
    def get(self, course_id):
        course = self.db.query(Course).filter(Course.id == course_id).one_or_none()
        if not course:
            self.json_error(404, f"Class '{course_id}' not found")

        is_adm = is_admin_caller(self)
        is_inst = bool(self.user and course in self.user.teaching)
        if not (is_adm or is_inst):
            self.json_error(403, "Permission denied")

        self.json_success(**build_class_view(course, self))

    @authenticated
    def patch(self, course_id):
        check_json_content_type(self)
        course = self.db.query(Course).filter(Course.id == course_id).one_or_none()
        if not course:
            self.json_error(404, f"Class '{course_id}' not found")

        is_adm = is_admin_caller(self)
        is_inst = bool(self.user and course in self.user.teaching)
        if not (is_adm or is_inst):
            self.json_error(403, "Permission denied")

        body = parse_json_body(self)

        # Disallow changing ID
        if 'id' in body and str(body['id']) != course.id:
            self.json_error(400, "Class id cannot be changed")

        # Field permission checks for instructor (must not edit limits, instructors, enabled)
        if not is_adm:
            forbidden_fields = {'resource_limits', 'instructors', 'enabled'}
            attempted_forbidden = forbidden_fields.intersection(body.keys())
            if attempted_forbidden:
                self.json_error(
                    403,
                    f"Permission denied to edit fields: {sorted(attempted_forbidden)}",
                )

        allowed_hosts = get_allowed_git_hosts_from_db(self.db)
        try:
            cleaned = validate_class_payload(body, is_create=False, allowed_git_hosts=allowed_hosts)
        except ValidationError as e:
            self.json_error(400, e.message, errors=e.errors)

        cs = course.settings
        if cs is None:
            cs = CourseSettings(course_id=course.id)
            self.db.add(cs)

        actor = actor_name(self)
        now = datetime.datetime.utcnow()
        service_token = self.settings.get('api_token')

        changes = {}
        token_cleared = False
        action = "update"

        # Check enable / disable
        if 'enabled' in cleaned:
            new_enabled = cleaned['enabled']
            if cs.enabled != new_enabled:
                action = "enable" if new_enabled else "disable"
                changes['enabled'] = {'before': cs.enabled, 'after': new_enabled}
                cs.enabled = new_enabled

        # Check display_name
        if 'display_name' in cleaned:
            new_name = cleaned['display_name']
            if cs.display_name != new_name:
                changes['display_name'] = {'before': cs.display_name, 'after': new_name}
                cs.display_name = new_name

        # Check instructors
        if 'instructors' in cleaned:
            new_instructors = cleaned['instructors']
            if not new_instructors:
                self.json_error(400, "Instructors list cannot be empty")
            old_inst_ids = sorted([u.id for u in course.instructors])
            if old_inst_ids != sorted(new_instructors):
                changes['instructors'] = {'before': old_inst_ids, 'after': sorted(new_instructors)}
                course.instructors.clear()
                for inst_name in new_instructors:
                    course.instructors.append(self.find_or_create_user(inst_name))

        # Check resource_limits
        if 'resource_limits' in cleaned:
            new_limits = cleaned['resource_limits']
            old_limits = json.loads(cs.resource_limits_json) if cs.resource_limits_json else {}
            if old_limits != new_limits:
                changes['resource_limits'] = {'before': old_limits, 'after': new_limits}
                cs.resource_limits_json = json.dumps(new_limits) if new_limits else None

        # Check git settings
        if 'git' in cleaned:
            git_data = cleaned['git']
            if git_data is None:
                if cs.git_url is not None:
                    changes['git.url'] = {'before': cs.git_url, 'after': None}
                    cs.git_url = None
                if cs.git_branch is not None:
                    changes['git.branch'] = {'before': cs.git_branch, 'after': None}
                    cs.git_branch = None
                if cs.git_username is not None:
                    changes['git.username'] = {'before': cs.git_username, 'after': None}
                    cs.git_username = None
                if cs.git_token_enc is not None:
                    changes['git.token'] = "<cleared>"
                    cs.git_token_enc = None
                    if action == "update":
                        action = "clear_token"
            else:
                old_url = cs.git_url
                new_url = git_data.get('url', old_url)

                # Check if url changed host
                url_host_changed = False
                if 'url' in git_data:
                    cs.git_url = git_data['url']
                    changes['git.url'] = {'before': old_url, 'after': git_data['url']}
                    if old_url and git_data['url']:
                        old_host = urllib.parse.urlsplit(old_url).netloc.lower()
                        new_host = urllib.parse.urlsplit(git_data['url']).netloc.lower()
                        if old_host != new_host:
                            url_host_changed = True

                if 'branch' in git_data:
                    changes['git.branch'] = {'before': cs.git_branch, 'after': git_data['branch']}
                    cs.git_branch = git_data['branch']

                if 'username' in git_data:
                    changes['git.username'] = {'before': cs.git_username, 'after': git_data['username']}
                    cs.git_username = git_data['username']

                # Token rules
                if 'token' in git_data:
                    token_val = git_data['token']
                    if token_val == '':  # clear
                        cs.git_token_enc = None
                        changes['git.token'] = "<cleared>"
                        if action == "update":
                            action = "clear_token"
                    else:  # set
                        cs.git_token_enc = encrypt(token_val, key_material=service_token)
                        changes['git.token'] = "<set>"
                        if action == "update":
                            action = "set_token"
                elif url_host_changed and cs.git_token_enc is not None:
                    # Url host changed without new token -> clear token automatically
                    cs.git_token_enc = None
                    token_cleared = True
                    changes['git.token'] = "<cleared_due_to_host_change>"
                    if action == "update":
                        action = "clear_token"

        cs.updated_at = now
        cs.updated_by = actor

        if changes:
            audit = SettingsAudit(
                actor=actor,
                scope="class",
                course_id=course.id,
                action=action,
                changes_json=json.dumps(changes),
                timestamp=now,
            )
            self.db.add(audit)
        self.db.commit()

        resp = build_class_view(course, self)
        if token_cleared:
            resp['token_cleared'] = True
        self.json_success(**resp)


class ServiceUserClassesHandler(MyRequestHandler):
    '/services/ngshare/service/user-classes/<user_id>'

    @authenticated
    def get(self, user_id):
        if not is_service(self):
            self.json_error(403, "Permission denied (service token required)")

        user = self.db.query(User).filter(User.id == user_id).one_or_none()
        if not user:
            self.json_success(classes=[])

        teaching_courses = set(user.teaching)
        taking_courses = set(user.taking)
        all_courses = sorted(teaching_courses.union(taking_courses), key=lambda c: c.id)

        result = []
        for course in all_courses:
            role = "instructor" if course in teaching_courses else "student"
            cs: CourseSettings | None = course.settings
            enabled = cs.enabled if cs else True
            display_name = cs.display_name if cs else None
            has_repo = bool(cs and cs.git_url)

            result.append({
                'id': course.id,
                'display_name': display_name,
                'role': role,
                'enabled': enabled,
                'has_repo': has_repo,
            })

        self.json_success(classes=result)


class ServiceSpawnConfigHandler(MyRequestHandler):
    '/services/ngshare/service/spawn-config/<course_id>'

    @authenticated
    def get(self, course_id):
        if not is_service(self):
            self.json_error(403, "Permission denied (service token required)")

        course = self.db.query(Course).filter(Course.id == course_id).one_or_none()
        if not course:
            self.json_error(404, f"Course '{course_id}' not found")

        cs: CourseSettings | None = course.settings
        service_token = self.settings.get('api_token')

        # Global defaults
        g_row = (
            self.db.query(GlobalSetting)
            .filter(GlobalSetting.key == 'default_resource_limits')
            .one_or_none()
        )
        default_limits = {}
        if g_row and g_row.value_json:
            try:
                default_limits = json.loads(g_row.value_json)
            except Exception:
                default_limits = {}

        # Class limits
        resource_limits = {}
        if cs and cs.resource_limits_json:
            try:
                resource_limits = json.loads(cs.resource_limits_json)
            except Exception:
                resource_limits = {}

        # Plaintext token
        token_plaintext = decrypt(cs.git_token_enc, key_material=service_token) if cs and cs.git_token_enc else None

        git_dict = {
            'url': cs.git_url if cs else None,
            'branch': cs.git_branch if (cs and cs.git_branch) else 'main',
            'username': cs.git_username if (cs and cs.git_username) else 'x-access-token',
            'token': token_plaintext,
        }

        self.json_success(
            course={'id': course.id, 'enabled': cs.enabled if cs else True},
            default_resource_limits=default_limits,
            resource_limits=resource_limits,
            git=git_dict,
        )


class SettingsAuditHandler(MyRequestHandler):
    '/services/ngshare/settings/audit'

    @authenticated
    def get(self):
        if not is_admin_caller(self):
            self.json_error(403, "Permission denied")

        course_id = self.get_argument('course_id', None)
        try:
            limit = min(int(self.get_argument('limit', '100')), 500)
        except ValueError:
            limit = 100

        query = self.db.query(SettingsAudit)
        if course_id:
            query = query.filter(SettingsAudit.course_id == course_id)

        entries = query.order_by(SettingsAudit._id.desc()).limit(limit).all()
        self.json_success(audit=[a.dump() for a in entries])


class UsersHandler(MyRequestHandler):
    '/services/ngshare/users'

    @authenticated
    def get(self):
        if not is_admin_caller(self):
            self.json_error(403, "Permission denied")

        users = get_existing_users(self.db)
        self.json_success(users=users)


def get_ui_template_namespace(handler):
    prefix = getattr(handler.application, 'prefix', '/services/ngshare/')
    if not prefix.endswith('/'):
        prefix += '/'
    user_name = actor_name(handler) or "anonymous"
    is_adm = is_admin_caller(handler)
    return {
        'prefix': prefix,
        'current_user_name': user_name,
        'is_admin': is_adm,
    }


def render_ui_error(handler, code: int, title: str, message: str):
    handler.set_status(code)
    ns = get_ui_template_namespace(handler)
    ns.update({
        'status_code': code,
        'error_title': title,
        'error_message': message,
    })
    handler.render('templates/error.html', **ns)


class UISettingsHandler(MyRequestHandler):
    '/services/ngshare/ui/settings'

    @authenticated
    def get(self):
        if not is_admin_caller(self):
            render_ui_error(
                self,
                403,
                "Permission Denied",
                "You must be an administrator to access Global Settings.",
            )
            return

        gs = (
            self.db.query(GlobalSetting)
            .filter(GlobalSetting.key == 'default_resource_limits')
            .one_or_none()
        )
        default_limits = json.loads(gs.value_json) if gs else {}
        allowed_hosts = get_allowed_git_hosts_from_db(self.db)

        ns = get_ui_template_namespace(self)
        ns.update({
            'default_resource_limits': default_limits,
            'allowed_git_hosts': allowed_hosts,
        })
        self.render('templates/settings.html', **ns)


class UIClassesHandler(MyRequestHandler):
    '/services/ngshare/ui/classes'

    @authenticated
    def get(self):
        is_adm = is_admin_caller(self)
        is_inst = bool(self.user and self.user.teaching)

        ns = get_ui_template_namespace(self)
        if not is_adm and not is_inst:
            ns.update({
                'classes': [],
                'is_instructor': False,
                'empty_message': "You don't manage any classes.",
            })
            self.render('templates/classes.html', **ns)
            return

        if is_adm:
            courses = self.db.query(Course).order_by(Course.id.asc()).all()
        else:
            courses = (
                self.db.query(Course)
                .filter(Course.instructors.contains(self.user))
                .order_by(Course.id.asc())
                .all()
            )

        classes_view = []
        for c in courses:
            view = build_class_view(c, self)
            view['student_count'] = len(c.students)
            classes_view.append(view)

        ns.update({
            'classes': classes_view,
            'is_instructor': is_inst,
            'empty_message': None,
        })
        self.render('templates/classes.html', **ns)


class UIClassNewHandler(MyRequestHandler):
    '/services/ngshare/ui/classes/new'

    @authenticated
    def get(self):
        if not is_admin_caller(self):
            render_ui_error(
                self,
                403,
                "Permission Denied",
                "Only administrators can create new classes.",
            )
            return

        existing_users = get_existing_users(self.db)
        allowed_hosts = get_allowed_git_hosts_from_db(self.db)
        ns = get_ui_template_namespace(self)
        ns.update({
            'allowed_git_hosts': allowed_hosts,
            'existing_users': existing_users,
        })
        self.render('templates/class_new.html', **ns)


class UIClassEditHandler(MyRequestHandler):
    '/services/ngshare/ui/classes/<id>'

    @authenticated
    def get(self, course_id):
        course = self.db.query(Course).filter(Course.id == course_id).one_or_none()
        if not course:
            render_ui_error(
                self,
                404,
                "Not Found",
                f"Class '{course_id}' was not found.",
            )
            return

        is_adm = is_admin_caller(self)
        is_inst = bool(self.user and course in self.user.teaching)
        if not (is_adm or is_inst):
            render_ui_error(
                self,
                403,
                "Permission Denied",
                f"You do not have permission to manage class '{course_id}'.",
            )
            return

        class_view = build_class_view(course, self)
        audit_list = []
        if is_adm:
            audits = (
                self.db.query(SettingsAudit)
                .filter(SettingsAudit.course_id == course_id)
                .order_by(SettingsAudit._id.desc())
                .limit(20)
                .all()
            )
            audit_list = [a.dump() for a in audits]

        existing_users = get_existing_users(self.db) if is_adm else []
        allowed_hosts = get_allowed_git_hosts_from_db(self.db)
        ns = get_ui_template_namespace(self)
        ns.update({
            'cls': class_view,
            'is_instructor': is_inst,
            'audit': audit_list,
            'allowed_git_hosts': allowed_hosts,
            'existing_users': existing_users,
        })
        self.render('templates/class_edit.html', **ns)

