from __future__ import annotations

import unittest
from io import BytesIO
from uuid import uuid4
from zipfile import ZipFile

from fastapi.testclient import TestClient

from meeting_service.app.application.docx_export import render_minutes_docx
from meeting_service.app.main import app


class MinutesExportTests(unittest.TestCase):
    def test_renderer_returns_docx_zip(self) -> None:
        content = render_minutes_docx(
            {"meeting": {"title": "Họp demo", "started_at": None}, "summary": [{"content": "Kết luận", "source_segment_ids": ["s1"]}], "topics": []},
            official=False,
        )
        self.assertTrue(content.startswith(b"PK"))
        with ZipFile(BytesIO(content)) as archive:
            self.assertIn("word/document.xml", archive.namelist())
            self.assertIn("DỰ THẢO", archive.read("word/document.xml").decode("utf-8"))

    def test_approved_minutes_can_be_exported_and_downloaded(self) -> None:
        meeting_id = uuid4()
        with TestClient(app) as client:
            created = client.post(f"/internal/v1/meetings/{meeting_id}/runtime", json={"meeting": {"status": "APPROVED"}})
            self.assertEqual(created.status_code, 201)
            stopped = client.post(f"/internal/v1/runtimes/{created.json()['runtime_session_id']}/stop")
            self.assertEqual(stopped.status_code, 200)
            approved = client.put(
                f"/internal/v1/meetings/{meeting_id}/minutes",
                json={"base_revision": 0, "status": "APPROVED", "document": {"schema_version": 1, "meeting": {"title": "Demo", "started_at": None}, "summary": [], "topics": [], "source_segment_ids": []}},
            )
            self.assertEqual(approved.status_code, 200)
            exported = client.post(f"/internal/v1/meetings/{meeting_id}/minutes/exports/docx", json={})
            self.assertEqual(exported.status_code, 201)
            export_id = exported.json()["id"]
            metadata = client.get(f"/internal/v1/meetings/{meeting_id}/minutes/exports/{export_id}/metadata")
            self.assertEqual(metadata.status_code, 200)
            downloaded = client.get(f"/internal/v1/meetings/{meeting_id}/minutes/exports/{export_id}")
            self.assertEqual(downloaded.status_code, 200)
            self.assertTrue(downloaded.content.startswith(b"PK"))


if __name__ == "__main__":
    unittest.main()
