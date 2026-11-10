'''
    Unit tests for bonus scheme helpers and models
'''
import math
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

try:
    from .results import (
        BonusSchemeError,
        default_bonus_scheme,
        normalize_bonus_scheme,
        is_bonus_scheme_active,
        compute_bonus,
        build_student_bonus_view,
        evaluate_course_results,
    )
    from .database.database import Base, Course, BonusScheme
except ImportError:
    from results import (
        BonusSchemeError,
        default_bonus_scheme,
        normalize_bonus_scheme,
        is_bonus_scheme_active,
        compute_bonus,
        build_student_bonus_view,
        evaluate_course_results,
    )
    from database.database import Base, Course, BonusScheme


def test_normalize_valid_percentage():
    raw = {
        'scheme_type': 'percentage',
        'student_visibility': 'full',
        'entries': [
            {'threshold': 50.5, 'bonus': 1},
            {'threshold': 90, 'bonus': 5},
            {'threshold': 80.0, 'bonus': 4.0},
        ],
    }
    normalized = normalize_bonus_scheme(raw)
    assert normalized['scheme_type'] == 'percentage'
    assert normalized['student_visibility'] == 'full'
    assert normalized['entries'] == [
        {'threshold': 90, 'bonus': 5},
        {'threshold': 80, 'bonus': 4},
        {'threshold': 50.5, 'bonus': 1},
    ]
    # Check types: 90 and 80 are ints, 50.5 is float
    assert isinstance(normalized['entries'][0]['threshold'], int)
    assert isinstance(normalized['entries'][1]['threshold'], int)
    assert isinstance(normalized['entries'][2]['threshold'], float)
    assert all(isinstance(e['bonus'], int) for e in normalized['entries'])


def test_normalize_defaults():
    normalized = normalize_bonus_scheme({})
    assert normalized == default_bonus_scheme()
    assert normalized == {
        'scheme_type': 'none',
        'student_visibility': 'full',
        'entries': [],
    }


def test_normalize_errors():
    with pytest.raises(BonusSchemeError, match='dict'):
        normalize_bonus_scheme('invalid')

    with pytest.raises(BonusSchemeError, match='Invalid scheme type'):
        normalize_bonus_scheme({'scheme_type': 'unknown'})

    with pytest.raises(BonusSchemeError, match='Invalid student visibility'):
        normalize_bonus_scheme({'student_visibility': 'invalid'})

    with pytest.raises(BonusSchemeError, match='list'):
        normalize_bonus_scheme({'entries': 'not a list'})

    with pytest.raises(BonusSchemeError, match='At most 50 entries'):
        normalize_bonus_scheme(
            {'entries': [{'threshold': i, 'bonus': i} for i in range(51)]}
        )

    with pytest.raises(BonusSchemeError):
        normalize_bonus_scheme({'entries': ['not a dict']})

    with pytest.raises(BonusSchemeError):
        normalize_bonus_scheme({'entries': [{'threshold': 10}]})

    # Threshold type errors
    with pytest.raises(BonusSchemeError, match='Threshold'):
        normalize_bonus_scheme({'entries': [{'threshold': True, 'bonus': 1}]})

    with pytest.raises(BonusSchemeError, match='Threshold'):
        normalize_bonus_scheme(
            {'entries': [{'threshold': '90', 'bonus': 1}]}
        )

    with pytest.raises(BonusSchemeError, match='finite'):
        normalize_bonus_scheme(
            {'entries': [{'threshold': float('nan'), 'bonus': 1}]}
        )

    with pytest.raises(BonusSchemeError, match='non-negative'):
        normalize_bonus_scheme(
            {'entries': [{'threshold': -1, 'bonus': 1}]}
        )

    with pytest.raises(BonusSchemeError, match='2 decimal places'):
        normalize_bonus_scheme(
            {'entries': [{'threshold': 10.125, 'bonus': 1}]}
        )

    with pytest.raises(BonusSchemeError, match='at most 100'):
        normalize_bonus_scheme(
            {
                'scheme_type': 'percentage',
                'entries': [{'threshold': 100.01, 'bonus': 1}],
            }
        )

    # Bonus errors
    with pytest.raises(BonusSchemeError, match='Bonus points'):
        normalize_bonus_scheme(
            {'entries': [{'threshold': 50, 'bonus': True}]}
        )

    with pytest.raises(BonusSchemeError, match='whole numbers'):
        normalize_bonus_scheme(
            {'entries': [{'threshold': 50, 'bonus': 1.5}]}
        )

    with pytest.raises(BonusSchemeError, match='non-negative'):
        normalize_bonus_scheme(
            {'entries': [{'threshold': 50, 'bonus': -2}]}
        )

    # Duplicate threshold (after rounding)
    with pytest.raises(BonusSchemeError, match='Duplicate threshold: 80'):
        normalize_bonus_scheme(
            {
                'entries': [
                    {'threshold': 80, 'bonus': 2},
                    {'threshold': 80.0, 'bonus': 3},
                ]
            }
        )


def test_normalize_absolute_allows_over_100():
    res = normalize_bonus_scheme(
        {
            'scheme_type': 'absolute',
            'entries': [{'threshold': 150.5, 'bonus': 10}],
        }
    )
    assert res['scheme_type'] == 'absolute'
    assert res['entries'] == [{'threshold': 150.5, 'bonus': 10}]


def test_normalize_none_keeps_entries():
    res = normalize_bonus_scheme(
        {
            'scheme_type': 'none',
            'entries': [{'threshold': 90, 'bonus': 5}],
        }
    )
    assert res['scheme_type'] == 'none'
    assert res['entries'] == [{'threshold': 90, 'bonus': 5}]


def test_compute_bonus():
    scheme = {
        'scheme_type': 'percentage',
        'student_visibility': 'full',
        'entries': [
            {'threshold': 90, 'bonus': 5},
            {'threshold': 80, 'bonus': 4},
            {'threshold': 50, 'bonus': 1},
        ],
    }
    assert compute_bonus(scheme, 84, 84) == 4
    assert compute_bonus(scheme, 90, 90) == 5
    assert compute_bonus(scheme, 89.99, 89.99) == 4
    assert compute_bonus(scheme, 49.99, 49.99) == 0
    assert compute_bonus(scheme, 100, 100) == 5

    # Non-monotonic entries: max of matches
    non_monotonic = {
        'scheme_type': 'percentage',
        'student_visibility': 'full',
        'entries': [
            {'threshold': 90, 'bonus': 1},
            {'threshold': 80, 'bonus': 3},
        ],
    }
    assert compute_bonus(non_monotonic, 95, 95) == 3

    # Inactive schemes -> None
    none_type_scheme = dict(scheme, scheme_type='none')
    assert compute_bonus(none_type_scheme, 95, 95) is None

    empty_entries_scheme = dict(scheme, entries=[])
    assert compute_bonus(empty_entries_scheme, 95, 95) is None
    assert compute_bonus(None, 95, 95) is None

    # Absolute scheme uses total_points
    abs_scheme = {
        'scheme_type': 'absolute',
        'student_visibility': 'full',
        'entries': [
            {'threshold': 120, 'bonus': 10},
            {'threshold': 60, 'bonus': 5},
        ],
    }
    assert compute_bonus(abs_scheme, total_points=70, percentage=100) == 5
    assert compute_bonus(abs_scheme, total_points=120, percentage=50) == 10
    assert compute_bonus(abs_scheme, total_points=40, percentage=100) == 0


def test_evaluate_course_results():
    scheme = {
        'scheme_type': 'percentage',
        'student_visibility': 'full',
        'entries': [{'threshold': 0, 'bonus': 1}],
    }
    # Minimal inputs
    res = evaluate_course_results(
        counted_assignment_ids=[],
        released_assignments={},
        all_student_submissions={},
        student_ids=['a'],
        bonus_scheme=scheme,
    )
    assert res['bonus_scheme'] == scheme
    assert len(res['students']) == 1
    assert res['students'][0]['student_id'] == 'a'
    assert res['students'][0]['bonus_points'] == 1

    # Inactive scheme
    inactive_scheme = {'scheme_type': 'none', 'student_visibility': 'full', 'entries': []}
    res_inactive = evaluate_course_results(
        counted_assignment_ids=[],
        released_assignments={},
        all_student_submissions={},
        student_ids=['a'],
        bonus_scheme=inactive_scheme,
    )
    assert res_inactive['bonus_scheme'] is None
    assert res_inactive['students'][0]['bonus_points'] is None


def test_build_student_bonus_view():
    scheme = {
        'scheme_type': 'percentage',
        'student_visibility': 'hidden',
        'entries': [{'threshold': 80, 'bonus': 2}],
    }
    # Hidden for student -> None
    assert build_student_bonus_view(scheme, 85, 85, viewer_is_instructor=False) is None

    # Hidden for instructor -> full view
    inst_view = build_student_bonus_view(scheme, 85, 85, viewer_is_instructor=True)
    assert inst_view is not None
    assert inst_view['visibility'] == 'full'
    assert inst_view['bonus_points'] == 2
    assert inst_view['entries'] == [{'threshold': 80, 'bonus': 2}]

    # Thresholds visibility
    thresh_scheme = dict(scheme, student_visibility='thresholds')
    stud_view = build_student_bonus_view(thresh_scheme, 85, 85, viewer_is_instructor=False)
    assert stud_view is not None
    assert stud_view['visibility'] == 'thresholds'
    assert stud_view['bonus_points'] is None
    assert stud_view['entries'] == [{'threshold': 80, 'bonus': 2}]


def test_bonus_scheme_db_model():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    course = Course('c1')
    db.add(course)
    db.commit()

    bs = BonusScheme(course._id)
    bs.scheme_type = 'percentage'
    bs.student_visibility = 'full'
    bs.entries_json = '[{"threshold": 90, "bonus": 5}]'
    db.add(bs)
    db.commit()

    loaded = db.query(BonusScheme).filter_by(course_id=course._id).one()
    scheme = loaded.to_scheme()
    assert scheme['scheme_type'] == 'percentage'
    assert scheme['student_visibility'] == 'full'
    assert scheme['entries'] == [{'threshold': 90, 'bonus': 5}]

    # Test Course.delete removes BonusScheme
    course.delete(db)
    db.commit()
    assert db.query(BonusScheme).filter_by(course_id=course._id).one_or_none() is None
