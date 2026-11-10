"""Server endpoint tests: input validation surfaces as 400 (not 500 or a
hang), and the verification header tells the truth about what ran."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from conftest import build_synthetic_resume

from fastapi.testclient import TestClient

from server import app

client = TestClient(app)


@pytest.fixture(scope="module")
def resume_bytes(tmp_path_factory):
    path = tmp_path_factory.mktemp("srv") / "r.pdf"
    build_synthetic_resume(str(path))
    with open(str(path), "rb") as fh:
        return fh.read()


def _edit(resume_bytes, replacements, verify="1"):
    return client.post(
        "/api/edit?verify=%s" % verify,
        files={"file": ("r.pdf", resume_bytes, "application/pdf")},
        data={"replacements": json.dumps(replacements)},
    )


def test_health():
    r = client.get("/health")
    assert r.status_code == 200


def test_edit_ok_and_verification_header(resume_bytes):
    r = _edit(resume_bytes, {"EXPERIENCE": "EMPLOYMENT"})
    assert r.status_code == 200
    assert r.headers["X-Verification"] == "pass"
    assert r.headers["X-Replacements-Made"] == "1"


def test_skipped_verification_header_is_honest(resume_bytes):
    """verify=0 must report 'skipped', never claim a pass it didn't run."""
    r = _edit(resume_bytes, {"EXPERIENCE": "EMPLOYMENT"}, verify="0")
    assert r.status_code == 200
    assert r.headers["X-Verification"] == "skipped"


def test_null_replacement_value_is_400_not_500(resume_bytes):
    """A JSON null value used to raise TypeError deep in the engine and
    come back as a 500 with internal exception text."""
    r = _edit(resume_bytes, {"Engineer": None})
    assert r.status_code == 400


def test_empty_query_is_400_not_hang(resume_bytes):
    """An empty old-text key used to spin forever in find_in_lines."""
    r = _edit(resume_bytes, {"": "x"})
    assert r.status_code == 400


def test_bad_json_is_400(resume_bytes):
    r = client.post(
        "/api/edit?verify=1",
        files={"file": ("r.pdf", resume_bytes, "application/pdf")},
        data={"replacements": "{not json"},
    )
    assert r.status_code == 400


def test_non_pdf_rejected(resume_bytes):
    r = client.post(
        "/api/edit?verify=1",
        files={"file": ("r.txt", b"hello", "text/plain")},
        data={"replacements": "{}"},
    )
    assert r.status_code == 400
