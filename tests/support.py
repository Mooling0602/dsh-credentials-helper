"""Shared helpers for the plugin tests."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"
"""Directory holding documents captured from the harness writer."""


class TemporaryDocumentTest(unittest.TestCase):
    """Test case with one private temporary credential document."""

    def setUp(self) -> None:
        super().setUp()
        directory = Path(tempfile.mkdtemp(prefix="dsh-cred-"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        self.directory = directory
        self.path = directory / ".credentials.yaml"

    def write_document(self, text: str) -> Path:
        """Write the document text with owner-only permission.

        :param text: Complete document text.
        :type text: str
        :returns: The document path.
        :rtype: pathlib.Path
        """
        self.path.write_bytes(text.encode("utf-8"))
        os.chmod(self.path, 0o600)
        return self.path

    def read_document(self) -> str:
        """Return the current document text without newline translation.

        :returns: The document text.
        :rtype: str
        """
        return self.path.read_bytes().decode("utf-8")

    def lock_path(self) -> Path:
        """Return the writer lock path of the test document.

        :returns: The lock path.
        :rtype: pathlib.Path
        """
        return self.path.with_name(self.path.name + ".lock")
