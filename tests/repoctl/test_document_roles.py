from tools.repoctl.document_roles import DocumentRole, classify_document_role, declared_document_role
from tools.repoctl.context_chunks import chunk_markdown_text
from pathlib import Path


def test_location_labels_are_navigation_categories():
    assert classify_document_role("docs/PRD.md") is DocumentRole.PRODUCT_AUTHORITY
    assert classify_document_role("repos/app/docs/PRD.md", repository_path="repos/app") is DocumentRole.PRODUCT_AUTHORITY
    assert classify_document_role("docs/research/notes.md") is DocumentRole.REFERENCE
    assert classify_document_role("docs/tasks/TEMPLATE.md") is DocumentRole.TEMPLATE


def test_prose_and_examples_do_not_change_document_categories():
    path = "docs/specs/payments/requirements.md"
    for body in ("Status: proposed", "Status: accepted", "Status: rejected", "~~~yaml\nStatus: draft\n~~~"):
        text = "# Requirements\n\n" + body + "\n\nKeep request identity stable.\n"
        assert declared_document_role(text, path=path) is DocumentRole.PRODUCT_AUTHORITY
        chunks = chunk_markdown_text(Path("."), path, text)
        assert chunks
        assert all(chunk.document_role is DocumentRole.PRODUCT_AUTHORITY for chunk in chunks)
