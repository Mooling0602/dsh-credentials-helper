"""Tests for the writer lock shared with the harness."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from unittest import mock

from support import TemporaryDocumentTest

from dsh_credentials_helper import _atomic
from dsh_credentials_helper.credentials import CredentialsError, CredentialsStore

DOCUMENT = "version: 1\nrefs:\n  ALPHA: old\n"


class LockTests(TemporaryDocumentTest):
    def test_a_write_takes_and_releases_the_harness_lock(self):
        self.write_document(DOCUMENT)
        self.assertTrue(CredentialsStore(self.path).set_reference("ALPHA", "new"))
        self.assertFalse(self.lock_path().exists())
        self.assertEqual(self.read_document(), 'version: 1\nrefs:\n  ALPHA: "new"\n')

    def test_a_lock_whose_holder_exited_is_taken_over(self):
        self.write_document(DOCUMENT)
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        self.lock_path().write_text(f"{dead.pid}\n", encoding="ascii")
        self.assertTrue(CredentialsStore(self.path).set_reference("ALPHA", "new"))
        self.assertFalse(self.lock_path().exists())
        leftovers = [
            item.name for item in self.directory.iterdir() if ".takeover-" in item.name
        ]
        self.assertEqual(leftovers, [])

    def test_a_live_lock_times_out_without_writing(self):
        self.write_document(DOCUMENT)
        self.lock_path().write_text(f"{os.getpid()}\n", encoding="ascii")
        with (
            mock.patch.object(_atomic, "LOCK_WAIT_MS", 150),
            self.assertRaises(CredentialsError) as caught,
        ):
            CredentialsStore(self.path).set_reference("ALPHA", "blocked")
        self.assertIn("writer lock", str(caught.exception))
        self.assertIn("ALPHA: old", self.read_document())
        self.assertTrue(self.lock_path().exists())

    def test_the_lock_is_released_when_the_write_fails(self):
        self.write_document("version: 1\nrefs: {ALPHA: x}\n")
        with self.assertRaises(CredentialsError):
            CredentialsStore(self.path).set_reference("ALPHA", "new")
        self.assertFalse(self.lock_path().exists())

    def test_a_missing_directory_is_reported_as_a_lock_error(self):
        store = CredentialsStore(self.directory / "missing" / ".credentials.yaml")
        with self.assertRaises(CredentialsError) as caught:
            store.set_reference("ALPHA", "new")
        self.assertIn("writer lock", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
