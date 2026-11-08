"""Tests for the resource paths the plugin resolves."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dsh_credentials_helper import paths


class HomeResolutionTests(unittest.TestCase):
    def test_an_explicit_home_wins(self):
        with mock.patch.dict(os.environ, {"DSH_HOME": "/tmp/custom-harness-home"}):
            self.assertEqual(paths.resolve_dsh_home(), Path("/tmp/custom-harness-home"))

    def test_a_relative_home_is_rejected(self):
        with (
            mock.patch.dict(os.environ, {"DSH_HOME": "relative-home"}),
            self.assertRaises(paths.CredentialsPathError),
        ):
            paths.resolve_dsh_home()

    def test_the_document_sits_under_the_home(self):
        with mock.patch.dict(os.environ, {"DSH_HOME": "/tmp/custom-harness-home"}):
            self.assertEqual(
                paths.resolve_credentials_path(),
                Path("/tmp/custom-harness-home/.credentials.yaml"),
            )

    def test_the_install_layout_derives_the_home(self):
        directory = Path(tempfile.mkdtemp(prefix="cred-paths-"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        install = directory / ".dsh" / "profiles" / "web" / "mloader" / "plugins"
        module = (
            install / "dsh-credentials-helper" / "dsh_credentials_helper" / "paths.py"
        )
        with (
            mock.patch.dict(os.environ, {"DSH_HOME": ""}),
            mock.patch.object(paths, "__file__", str(module)),
        ):
            self.assertEqual(paths.resolve_dsh_home(), directory / ".dsh")

    def test_an_unrelated_location_falls_back_to_the_default_home(self):
        with (
            mock.patch.dict(os.environ, {"DSH_HOME": ""}),
            mock.patch.object(paths, "_home_from_install_location", return_value=None),
        ):
            self.assertEqual(paths.resolve_dsh_home(), Path.home() / ".dsh")


if __name__ == "__main__":
    unittest.main()
