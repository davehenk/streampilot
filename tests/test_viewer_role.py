import os
import sqlite3
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "streampilot"))
from streampilot import server


class ViewerRoleTests(unittest.TestCase):
    def setUp(self):
        self.session = {}
        self.app = server.App()
        self.session_patch = patch.object(server.cherrypy, "session", self.session, create=True)
        self.session_patch.start()
        self.addCleanup(self.session_patch.stop)

    def login(self, username, password):
        with self.assertRaises(server.cherrypy.HTTPRedirect) as result:
            self.app.login_post(username, password)
        url = urlsplit(result.exception.urls[0])
        return url.path + ("?" + url.query if url.query else "")

    def test_login_roles_and_admin_precedence(self):
        env = {"SP_USER": "admin", "SP_PASSWORD": "admin-pass",
               "SP_VIEWER_USER": "viewer", "SP_VIEWER_PASSWORD": "viewer-pass"}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(self.login(" viewer ", "viewer-pass"), "/")
            self.assertEqual(self.session, {"username": "viewer", "role": "viewer"})
            self.assertEqual(self.login("admin", "admin-pass"), "/")
            self.assertEqual(self.session, {"username": "admin", "role": "admin"})
            self.assertEqual(self.login("viewer", "wrong"), "/login?msg=error")
            self.assertEqual(self.session["role"], "admin")
        with patch.dict(os.environ, {"SP_VIEWER_USER": "admin", "SP_VIEWER_PASSWORD": "admin"}, clear=True):
            self.assertEqual(self.login("admin", "admin"), "/")
            self.assertEqual(self.session["role"], "admin")

    def test_viewer_requires_both_environment_variables(self):
        for env in ({}, {"SP_VIEWER_USER": "viewer"},
                    {"SP_VIEWER_PASSWORD": "viewer-pass"},
                    {"SP_VIEWER_USER": "viewer", "SP_VIEWER_PASSWORD": ""}):
            with self.subTest(env=env), patch.dict(os.environ, env, clear=True):
                self.assertEqual(self.login("viewer", "viewer-pass"), "/login?msg=error")
                self.assertNotIn("username", self.session)

    def test_session_list_controls_for_viewer_and_admin(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.execute("""CREATE TABLE live_session (
            id INTEGER, device_id TEXT, device_host TEXT, input_key TEXT,
            input_index INTEGER, input_identifier TEXT, input_display_name TEXT,
            started_at TEXT, ended_at TEXT, title TEXT)""")
        db.executemany("INSERT INTO live_session VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [
            (1, "device", "host", "key", 1, "input", "name", "2026-01-01T00:00:00", None, "live"),
            (2, "device", "host", "key", 1, "input", "name", "2026-01-01T00:00:00", "2026-01-01T01:00:00", "ended"),
        ])
        response = SimpleNamespace(headers={})
        with patch.object(server, "connect_db", return_value=db), \
             patch.object(server.cherrypy, "response", response):
            for role in ("viewer", "admin", None):
                with self.subTest(role=role):
                    self.session.clear()
                    self.session["username"] = "user"
                    if role:
                        self.session["role"] = role
                    body = self.app.logs_ui().decode()
                    for sid in (1, 2):
                        self.assertIn(f'href="/log_view?session_id={sid}"', body)
                    for path in ("/log_download?", "/log_download_csv?", "/log_download_geojson?",
                                 'action="/log_stop"', 'action="/log_delete"',
                                 'href="/log_import"', 'action="/log_purge"'):
                        self.assertEqual(path in body, role != "viewer", path)
                    self.assertIn('href="/log_pdf?session_id=2"', body)

    def test_dashboard_management_controls_for_viewer_and_admin(self):
        device = {"id": 1, "name": "Device", "host": "host", "port": 443}
        cases = (
            ("dashboard.html", {"devices": []}, "Add device", "/logs_ui"),
            ("dashboard.html", {"devices": [device]}, "Manage devices", "/logs_ui"),
            ("gateway_dashboard.html", {"srt_gateways": []}, "Manage devices", "/gateway_logs"),
            ("gateway_dashboard.html", {"srt_gateways": [device]}, "Manage devices", "/gateway_logs"),
        )
        for role in ("viewer", "admin", None):
            self.session.clear()
            if role:
                self.session["role"] = role
            for template, context, button, logs_url in cases:
                with self.subTest(role=role, template=template, context=context):
                    body = server.render(template, page_title="Dashboard", **context).decode()
                    for item in (button, 'href="/devices"', 'href="/settings"'):
                        self.assertEqual(item in body, role != "viewer", item)
                    self.assertIn(f'href="{logs_url}"', body)

if __name__ == "__main__":
    unittest.main()
