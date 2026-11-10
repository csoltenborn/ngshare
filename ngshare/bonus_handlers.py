'''
    Bonus scheme API handlers (FHDW extension)
'''
import datetime
import json

from tornado.web import authenticated

try:
    from .database.database import BonusScheme
    from .ngshare import MyRequestHandler
    from .results import BonusSchemeError, default_bonus_scheme, normalize_bonus_scheme
except ImportError:
    from database.database import BonusScheme
    from ngshare import MyRequestHandler
    from results import BonusSchemeError, default_bonus_scheme, normalize_bonus_scheme


def load_bonus_scheme(db, course):
    'Return canonical scheme dict for course (default if none stored).'
    row = db.query(BonusScheme).filter(BonusScheme.course_id == course._id).one_or_none()
    if row is None:
        return default_bonus_scheme()
    try:
        return normalize_bonus_scheme(row.to_scheme())
    except BonusSchemeError:
        return default_bonus_scheme()


class BonusSchemeHandler(MyRequestHandler):
    '/api/bonus_scheme/<course_id>'

    @authenticated
    def get(self, course_id):
        'Get bonus scheme (instructors only)'
        course = self.find_course(course_id)
        self.check_course_instructor(course)
        self.json_success(bonus_scheme=load_bonus_scheme(self.db, course))

    @authenticated
    def post(self, course_id):
        'Set bonus scheme (instructors only). Form field "scheme" = JSON string.'
        course = self.find_course(course_id)
        self.check_course_instructor(course)
        raw = self.get_argument('scheme', None)
        if not raw:
            self.json_error(400, 'Please supply scheme')
        try:
            scheme = normalize_bonus_scheme(json.loads(raw))
        except ValueError as e:   # covers JSONDecodeError and BonusSchemeError
            self.json_error(400, str(e))
        row = self.db.query(BonusScheme).filter(BonusScheme.course_id == course._id).one_or_none()
        if row is None:
            row = BonusScheme(course._id)
            self.db.add(row)
        row.scheme_type = scheme['scheme_type']
        row.student_visibility = scheme['student_visibility']
        row.entries_json = json.dumps(scheme['entries'])
        row.updated_at = datetime.datetime.utcnow()
        row.updated_by = self.user.id if self.user else None
        self.db.commit()
        self.json_success(bonus_scheme=scheme)
