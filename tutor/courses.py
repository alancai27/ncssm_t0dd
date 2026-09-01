"""Course catalog.

Currently a JSON seed file. This is the same content path RAG will need, so
it is deliberately loaded through one accessor rather than read inline: swap
the backing store here and nothing downstream changes.

The catalog is also the ALLOWLIST. A course id arriving from the browser is
never trusted; it is looked up here or rejected.
"""
import json
import os

PATH = os.environ.get("TODD_COURSES", os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "content", "courses.json"))


def _load():
    with open(PATH) as f:
        return json.load(f).get("courses", [])


def all_courses():
    return [{"id": c["id"], "name": c["name"]} for c in _load()]


def get(course_id):
    """Return the course dict, or None if the id is not in the catalog."""
    for c in _load():
        if c["id"] == course_id:
            return c
    return None


def default():
    return _load()[0]


def resolve(course_id):
    """Validated lookup with a safe fallback. Never trust a client-supplied id."""
    return get(course_id) or default()


def modules_text(course):
    return "\n".join(course.get("modules", []))
