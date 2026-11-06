'''
    Point evaluation helpers for student results
'''

import json
import re
from typing import Dict, Iterable, List, Optional, Tuple


_META_SCORE_RE = re.compile(
    r'<meta\s+name=["\']nbgrader-score["\']\s+content=["\']([+-]?\d+(?:\.\d+)?)["\']',
    re.IGNORECASE,
)
_META_MAX_SCORE_RE = re.compile(
    r'<meta\s+name=["\']nbgrader-max-score["\']\s+content=["\']([+-]?\d+(?:\.\d+)?)["\']',
    re.IGNORECASE,
)
_HEADING_SCORE_RE = re.compile(
    r'\(\s*Score:\s*([+-]?\d+(?:\.\d+)?)\s*/\s*([+-]?\d+(?:\.\d+)?)\s*\)',
    re.IGNORECASE,
)


def _clean_round(value: float, ndigits: int = 2) -> float:
    rounded = round(float(value), ndigits)
    return 0.0 if rounded == 0 else rounded


def extract_notebook_max_score(ipynb_bytes: bytes) -> float:
    '''Return the sum of points across all grade and task cells in a notebook.'''
    try:
        nb = json.loads(ipynb_bytes.decode('utf-8'))
    except Exception:
        return 0.0

    if not isinstance(nb, dict):
        return 0.0

    cells = nb.get('cells', [])
    if not isinstance(cells, list):
        return 0.0

    total = 0.0
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        metadata = cell.get('metadata')
        if not isinstance(metadata, dict):
            continue
        nbg = metadata.get('nbgrader')
        if not isinstance(nbg, dict):
            continue
        if nbg.get('grade') or nbg.get('task'):
            try:
                pts = float(nbg.get('points', 0.0) or 0.0)
            except (TypeError, ValueError):
                pts = 0.0
            total += pts
    return total


def extract_feedback_score(html_bytes: bytes) -> Optional[Tuple[float, float]]:
    '''Extract (score, max_score) from an NbGrader generated feedback HTML file.'''
    try:
        text = html_bytes.decode('utf-8', errors='replace')
    except Exception:
        return None

    m_score = _META_SCORE_RE.search(text)
    m_max = _META_MAX_SCORE_RE.search(text)
    if m_score and m_max:
        try:
            return (float(m_score.group(1)), float(m_max.group(1)))
        except ValueError:
            pass

    m_heading = _HEADING_SCORE_RE.search(text)
    if m_heading:
        try:
            return (float(m_heading.group(1)), float(m_heading.group(2)))
        except ValueError:
            return None

    return None


def evaluate_student_results(
    counted_assignment_ids: Iterable[str],
    released_assignments: Dict[str, List[Tuple[str, bytes]]],
    student_submissions: Dict[str, List[Dict]],
) -> Dict:
    '''
    Compute point evaluation summary for a student in a course.

    Parameters
    ----------
    counted_assignment_ids:
        Assignment IDs marked as counted by an instructor.
    released_assignments:
        Mapping of released assignment_id -> list of (filename, content_bytes).
    student_submissions:
        Mapping of assignment_id -> list of submission dicts, each containing:
        - 'timestamp': comparable timestamp (e.g. datetime)
        - 'feedback_files': list of (filename, content_bytes)
    '''
    counted_released = sorted(
        set(counted_assignment_ids) & set(released_assignments.keys())
    )

    assignments_out = []
    raw_total_points = 0.0
    raw_total_max_points = 0.0

    for assignment_id in counted_released:
        assignment_files = released_assignments.get(assignment_id, [])
        max_points = 0.0
        for filename, content in assignment_files:
            if filename.lower().endswith('.ipynb'):
                max_points += extract_notebook_max_score(content)

        submissions = list(student_submissions.get(assignment_id, []))
        submissions.sort(key=lambda s: s['timestamp'], reverse=True)
        submitted = len(submissions) > 0

        points = 0.0
        feedback_max_sum = 0.0
        graded = False

        for sub in submissions:
            parsed_scores = []
            for filename, content in sub.get('feedback_files', []):
                if not filename.lower().endswith('.html'):
                    continue
                parsed = extract_feedback_score(content)
                if parsed is not None:
                    parsed_scores.append(parsed)
            if parsed_scores:
                points = sum(s for s, _ in parsed_scores)
                feedback_max_sum = sum(m for _, m in parsed_scores)
                graded = True
                break

        if max_points == 0.0 and feedback_max_sum > 0.0:
            max_points = feedback_max_sum

        clean_points = _clean_round(points)
        clean_max = _clean_round(max_points)

        raw_total_points += clean_points
        raw_total_max_points += clean_max

        assignments_out.append(
            {
                'assignment_id': assignment_id,
                'points': clean_points,
                'max_points': clean_max,
                'graded': graded,
                'submitted': submitted,
            }
        )

    total_points = _clean_round(raw_total_points)
    total_max_points = _clean_round(raw_total_max_points)
    percentage = (
        _clean_round((total_points / total_max_points) * 100.0)
        if total_max_points > 0.0
        else 0.0
    )

    return {
        'assignments': assignments_out,
        'total_points': total_points,
        'total_max_points': total_max_points,
        'percentage': percentage,
    }


def evaluate_course_results(
    counted_assignment_ids: Iterable[str],
    released_assignments: Dict[str, List[Tuple[str, bytes]]],
    all_student_submissions: Dict[str, Dict[str, List[Dict]]],
    student_ids: Iterable[str],
) -> Dict:
    '''
    Compute point evaluation summary across all students in a course.
    '''
    counted_assignments = sorted(
        set(counted_assignment_ids) & set(released_assignments.keys())
    )
    students_out = []
    course_total_max = 0.0

    for sid in sorted(student_ids):
        student_subs = all_student_submissions.get(sid, {})
        res = evaluate_student_results(
            counted_assignment_ids,
            released_assignments,
            student_subs,
        )
        if res['total_max_points'] > course_total_max:
            course_total_max = res['total_max_points']
        students_out.append(
            {
                'student_id': sid,
                'total_points': res['total_points'],
                'total_max_points': res['total_max_points'],
                'percentage': res['percentage'],
                'assignments': res['assignments'],
            }
        )

    if course_total_max == 0.0:
        for assignment_id in counted_assignments:
            for filename, content in released_assignments.get(assignment_id, []):
                if filename.lower().endswith('.ipynb'):
                    course_total_max += extract_notebook_max_score(content)
        course_total_max = _clean_round(course_total_max)

    return {
        'counted_assignments': counted_assignments,
        'total_max_points': course_total_max,
        'students': students_out,
    }


