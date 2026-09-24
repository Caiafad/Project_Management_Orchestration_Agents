"""
Per-user output isolation.

All users previously shared one output/ folder, so a concurrent run could
attribute one user's file to another, and read_output_file would list and read
across users.
"""

import json

import pytest

from config import OUTPUT_DIR, user_output_dir
from tools.document_generator import create_excel, create_word_document
from tools.file_reader import read_output_file


@pytest.fixture
def two_users(tmp_path):
    alice, bob = tmp_path / "alice", tmp_path / "bob"
    alice.mkdir(), bob.mkdir()
    return alice, bob


def test_each_user_gets_a_distinct_directory():
    assert user_output_dir("alice") != user_output_dir("bob")
    assert user_output_dir("alice").parent == OUTPUT_DIR


@pytest.mark.parametrize("hostile", ["../../etc/passwd", "..\\..\\windows", "a/b/c", "..."])
def test_username_cannot_escape_the_output_root(hostile):
    # Separators are replaced, so the result is always a single folder inside the root.
    resolved = user_output_dir(hostile).resolve()
    assert resolved.parent == OUTPUT_DIR.resolve()
    assert resolved != OUTPUT_DIR.resolve()


def test_anonymous_use_keeps_the_root_directory():
    assert user_output_dir(None) == OUTPUT_DIR
    assert user_output_dir("") == OUTPUT_DIR


def test_documents_are_written_to_the_callers_directory(two_users):
    alice, bob = two_users
    create_word_document("Alice Plan", [{"heading": "Intro", "content": "Alice content."}],
                         filename="Plan", output_dir=alice)
    create_word_document("Bob Plan", [{"heading": "Intro", "content": "Bob content."}],
                         filename="Plan", output_dir=bob)

    assert (alice / "Plan.docx").exists() and (bob / "Plan.docx").exists()
    # Same filename, different bytes — neither overwrote the other.
    assert (alice / "Plan.docx").read_bytes() != (bob / "Plan.docx").read_bytes()


def test_read_output_file_cannot_see_another_users_documents(two_users):
    alice, bob = two_users
    create_excel("Alice Budget", [{"name": "S", "headers": ["A"], "rows": [["1"]]}],
                 filename="Secret", output_dir=alice)

    result = json.loads(read_output_file("Secret.xlsx", output_dir=bob))
    assert "error" in result
    assert result["available_files"] == [], "another user's files must not be listed"


def test_read_output_file_reads_the_callers_own_document(two_users):
    alice, _ = two_users
    create_excel("Alice Budget", [{"name": "S", "headers": ["Item"], "rows": [["Widget"]]}],
                 filename="Budget", output_dir=alice)

    result = json.loads(read_output_file("Budget.xlsx", output_dir=alice))
    assert "error" not in result
    assert "Widget" in json.dumps(result)


def test_path_traversal_in_filename_is_rejected(two_users):
    alice, bob = two_users
    create_excel("Secret", [{"name": "S", "headers": ["A"], "rows": [["1"]]}],
                 filename="Secret", output_dir=bob)

    result = json.loads(read_output_file("../bob/Secret.xlsx", output_dir=alice))
    assert "error" in result


# ── filename hygiene (the .docx.docx bug) ────────────────────────────────────

def test_extension_is_not_doubled(two_users):
    alice, _ = two_users
    out = json.loads(create_word_document("P", [{"heading": "H", "content": "c"}],
                                          filename="Plan.docx", output_dir=alice))
    assert out["filename"] == "Plan.docx"
    assert (alice / "Plan.docx").exists()
    assert not (alice / "Plan.docx.docx").exists()


def test_filename_with_a_path_is_reduced_to_its_basename(two_users):
    alice, _ = two_users
    out = json.loads(create_word_document("P", [{"heading": "H", "content": "c"}],
                                          filename="../../escape", output_dir=alice))
    assert (alice / out["filename"]).exists()
    assert "escape" in out["filename"] and ".." not in out["filename"]


def test_empty_filename_falls_back_to_the_title(two_users):
    alice, _ = two_users
    out = json.loads(create_word_document("Quarterly Report",
                                          [{"heading": "H", "content": "c"}],
                                          filename="", output_dir=alice))
    assert out["filename"].startswith("Quarterly Report")
    assert out["filename"].endswith(".docx")
