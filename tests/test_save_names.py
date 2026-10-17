"""What ``save`` will write, and where, without a door (ledger #1748).

A file entry's name is the poster's choice, so ``save`` refuses every name that would arm
something on the reader's machine: agent and shell configuration (``CLAUDE.md``, ``.mcp.json``,
``.envrc``), any dotfile, Windows device names, drives and alternate data streams. With no PATH
it writes to the reader's downloads directory, never to the directory they happen to be in,
which is often a repository an agent works in.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from ikigai_layers_client import door, files
from ikigai_layers_client.cli import main

ENTRY = "urn:layer:work:entry:1"


def file_node(name: str, data: bytes = b"hello\n") -> dict:
    encoding, content = files.encode(data)
    return {
        "@id": ENTRY,
        "@type": "File",
        "fileName": name,
        "encoding": encoding,
        "content": content,
        "byteSize": str(len(data)),
        "contentHash": files.digest(data),
    }


class FakeDoor:
    """A door that answers one Source with one file entry, and counts the dials."""

    def __init__(self, node: dict):
        self.node = node
        self.dials = 0

    @contextlib.contextmanager
    def connect(self, *args, **kwargs):
        self.dials += 1
        answer = json.dumps({"@graph": [self.node]}).encode()
        yield SimpleNamespace(source=lambda iri, **kw: SimpleNamespace(data=answer))


class SafeNameTest(unittest.TestCase):
    def refused(self, name: str, because: str) -> None:
        with self.subTest(name=name):
            with self.assertRaises(files.UnsafeName) as caught:
                files.safe_name(name)
            self.assertIn(because, str(caught.exception))

    def test_a_name_an_agent_or_shell_loads_as_configuration_is_refused(self):
        listed = [
            "CLAUDE.md",
            "CLAUDE.local.md",
            "AGENTS.md",
            ".mcp.json",
            ".envrc",
            ".env",
            ".bashrc",
            ".bash_profile",
            ".profile",
            ".zshrc",
            ".zshenv",
            ".zprofile",
            ".gitconfig",
            ".npmrc",
            ".pypirc",
            ".netrc",
            "_netrc",
            "conftest.py",
        ]
        for name in listed:
            self.assertIn(name.casefold(), files.CONFIG_NAMES)
            self.refused(name, "loads as configuration")

    def test_configuration_names_match_case_insensitively(self):
        for name in ("claude.md", "Claude.MD", "agents.MD", ".MCP.JSON", ".EnvRC", "CONFTEST.PY"):
            self.refused(name, "loads as configuration")

    def test_every_git_name_is_refused(self):
        for name in (".git", ".gitattributes", ".gitignore", ".git-credentials", ".GITMODULES"):
            self.refused(name, "loads as configuration")

    def test_a_trailing_dot_or_space_or_a_lookalike_does_not_slip_past_the_list(self):
        # Windows drops a trailing dot or space, so CLAUDE.md. lands as CLAUDE.md there.
        for name in ("CLAUDE.md.", "AGENTS.md ", "CLAUDE.md. .", "ＣＬＡＵＤＥ.md"):
            self.refused(name, "loads as configuration")

    def test_an_invisible_character_is_refused(self):
        # HFS+ ignored some of these when comparing names (git's CVE-2014-9390).
        for name in ("CLAUDE‌.md", "notes​.md", "a b", "﻿x.txt"):
            self.refused(name, "invisible character")

    def test_any_leading_dot_is_refused(self):
        for name in (".hidden", ".claude", "..notes", "._x", ".cursorrules"):
            self.refused(name, "begins with a dot")

    def test_windows_device_names_are_refused_with_or_without_an_extension(self):
        for name in ("CON", "con", "PRN", "AUX", "Nul.txt", "COM1", "com9.log", "LPT1", "lpt0"):
            self.refused(name, "Windows device name")
        for name in ("COM¹", "LPT³.txt", "CONIN$", "aux .txt", "nul.tar.gz"):
            self.refused(name, "Windows device name")

    def test_a_drive_letter_is_refused(self):
        for name in ("C:x", "c:evil", "Z:"):
            self.refused(name, "Windows drive")

    def test_any_colon_is_refused_as_an_alternate_data_stream(self):
        for name in ("a.txt:ads", "notes:Zone.Identifier", "10:30.txt", "a.txt::$DATA"):
            self.refused(name, "alternate data stream")

    def test_a_character_windows_does_not_allow_is_refused(self):
        for name in ("a?b", "a*b", 'a"b', "a|b", "a<b", "a>b"):
            self.refused(name, "Windows does not allow")

    def test_a_trailing_dot_or_space_is_refused(self):
        for name in ("notes.", "notes ", "a.txt.."):
            self.refused(name, "ends in a dot or a space")

    def test_a_directory_or_control_character_is_still_refused(self):
        for name in ("", ".", "..", "a/b", "a\\b", "x\ny", "\x00"):
            self.refused(name, "not a bare file name")
        self.refused("x" * 256, "over 255 characters")

    def test_an_ordinary_name_is_allowed(self):
        for name in (
            "notes.md",
            "trace.log",
            "README.md",
            "report 2026-10-16.pdf",
            "claude-notes.md",
            "my.CLAUDE.md",
            "agents.md.txt",
            "console.txt",
            "COM10",
            "conx.txt",
            "données.csv",
            "a.b.c",
            "x" * 255,
        ):
            with self.subTest(name=name):
                self.assertEqual(files.safe_name(name), name)


class DownloadsDirTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="home-"))
        self.addCleanup(shutil.rmtree, self.home)

    def test_downloads_under_home_when_it_exists(self):
        (self.home / "Downloads").mkdir()
        self.assertEqual(files.downloads_dir(self.home, "darwin"), self.home / "Downloads")
        self.assertEqual(
            files.downloads_dir(self.home, "win32", lambda: None), self.home / "Downloads"
        )

    def test_the_xdg_download_dir_otherwise(self):
        (self.home / ".config").mkdir()
        (self.home / "Téléchargements").mkdir()
        (self.home / ".config" / "user-dirs.dirs").write_text(
            '# written by xdg-user-dirs-update\nXDG_DESKTOP_DIR="$HOME/Desktop"\n'
            'XDG_DOWNLOAD_DIR="$HOME/Téléchargements"\n',
            encoding="utf-8",
        )
        self.assertEqual(files.downloads_dir(self.home, "linux"), self.home / "Téléchargements")

    def test_an_xdg_download_dir_that_is_home_means_none(self):
        (self.home / ".config").mkdir()
        (self.home / ".config" / "user-dirs.dirs").write_text('XDG_DOWNLOAD_DIR="$HOME/"\n')
        self.assertIsNone(files.downloads_dir(self.home, "linux"))

    def test_the_windows_known_folder_otherwise(self):
        elsewhere = self.home / "D" / "Downloads2"
        elsewhere.mkdir(parents=True)
        self.assertEqual(files.downloads_dir(self.home, "win32", lambda: str(elsewhere)), elsewhere)
        self.assertIsNone(files.downloads_dir(self.home, "win32", lambda: None))

    def test_none_when_there_is_no_downloads_directory(self):
        self.assertIsNone(files.downloads_dir(self.home, "darwin"))
        self.assertIsNone(files.downloads_dir(self.home, "linux"))


class SaveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="save-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.downloads = self.tmp / "Downloads"
        self.downloads.mkdir()
        self.cwd = self.tmp / "repo"
        self.cwd.mkdir()
        here = os.getcwd()
        os.chdir(self.cwd)
        self.addCleanup(os.chdir, here)

    def save(self, node: dict, *argv: str, no_downloads: bool = False):
        fake = FakeDoor(node)
        out, err = io.StringIO(), io.StringIO()
        found = None if no_downloads else self.downloads
        with (
            mock.patch.object(door, "connect", fake.connect),
            mock.patch.object(files, "downloads_dir", lambda: found),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = main(["save", ENTRY, *argv, "--name", "a"])
        return code, out.getvalue(), err.getvalue(), fake.dials

    def test_with_no_path_it_saves_to_downloads_not_here(self):
        code, out, err, _ = self.save(file_node("notes.md"))
        self.assertEqual(code, 0, err)
        self.assertEqual((self.downloads / "notes.md").read_bytes(), b"hello\n")
        self.assertFalse((self.cwd / "notes.md").exists())
        self.assertIn(str(self.downloads / "notes.md"), out)

    def test_with_no_downloads_directory_it_asks_for_a_path_before_dialing(self):
        code, _, err, dials = self.save(file_node("notes.md"), no_downloads=True)
        self.assertEqual((code, dials), (2, 0))
        self.assertIn("no downloads directory found", err)
        self.assertIn("give a PATH", err)
        self.assertEqual(list(self.cwd.iterdir()), [])

    def test_an_entry_named_as_configuration_is_not_saved_and_says_how(self):
        for name in (".mcp.json", "CLAUDE.md", ".envrc", "AGENTS.md", "CON", "C:evil", "a.txt:ads"):
            with self.subTest(name=name):
                code, _, err, _ = self.save(file_node(name), str(self.cwd))
                self.assertEqual(code, 1)
                self.assertIn("not saved", err)
                self.assertIn("--as NAME", err)
                self.assertEqual(list(self.cwd.iterdir()), [])

    def test_as_saves_under_another_name(self):
        code, _, err, _ = self.save(file_node(".mcp.json"), str(self.cwd), "--as", "mcp.json")
        self.assertEqual(code, 0, err)
        self.assertEqual((self.cwd / "mcp.json").read_bytes(), b"hello\n")
        self.assertFalse((self.cwd / ".mcp.json").exists())
        code, _, err, _ = self.save(file_node(".envrc"), "--as", "envrc.txt")
        self.assertEqual(code, 0, err)
        self.assertTrue((self.downloads / "envrc.txt").exists())

    def test_as_is_held_to_the_same_rules_before_dialing(self):
        for name in (".envrc", "CLAUDE.md", "nul", "x:y"):
            with self.subTest(name=name):
                code, _, err, dials = self.save(file_node("ok.txt"), str(self.cwd), "--as", name)
                self.assertEqual((code, dials), (2, 0))
                self.assertIn("choose another --as NAME", err)

    def test_a_path_naming_a_file_is_held_to_the_same_rules_before_dialing(self):
        for name in ("CLAUDE.md", ".zshenv", "AUX.txt"):
            with self.subTest(name=name):
                target = str(self.cwd / name)
                code, _, err, dials = self.save(file_node("ok.txt"), target)
                self.assertEqual((code, dials), (2, 0))
                self.assertIn("give PATH another name", err)
        code, _, err, _ = self.save(file_node(".envrc"), str(self.cwd / "envrc.txt"))
        self.assertEqual(code, 0, err)
        self.assertTrue((self.cwd / "envrc.txt").exists())

    def test_as_with_a_path_that_is_not_a_directory_is_a_usage_error(self):
        code, _, err, dials = self.save(file_node("ok.txt"), str(self.cwd / "x.txt"), "--as", "y")
        self.assertEqual((code, dials), (2, 0))
        self.assertIn("not a directory", err)

    def test_force_replaces_an_existing_file_and_nothing_else_does(self):
        (self.cwd / "notes.md").write_bytes(b"mine\n")
        code, _, err, _ = self.save(file_node("notes.md"), str(self.cwd))
        self.assertEqual(code, 2)
        self.assertIn("--force to replace it", err)
        self.assertEqual((self.cwd / "notes.md").read_bytes(), b"mine\n")
        code, _, err, _ = self.save(file_node("notes.md"), str(self.cwd), "--force")
        self.assertEqual(code, 0, err)
        self.assertEqual((self.cwd / "notes.md").read_bytes(), b"hello\n")

    def test_help_says_force_replaces_an_existing_file(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            main(["save", "--help"])
        text = " ".join(out.getvalue().split())
        self.assertIn("replace an existing file at the target; its old content is lost", text)
        self.assertIn("--as NAME", text)
        self.assertIn("downloads directory", text)


class PostNameTest(unittest.TestCase):
    def test_a_file_a_reader_could_not_save_is_not_posted_and_says_how(self):
        with tempfile.TemporaryDirectory() as tmp:
            dotfile = Path(tmp) / ".gitignore"
            dotfile.write_text("target/\n")
            err = io.StringIO()
            with (
                mock.patch.object(door, "connect", side_effect=AssertionError("dialed")),
                contextlib.redirect_stderr(err),
            ):
                code = main(["post", "file", "work", str(dotfile), "--name", "a"])
            self.assertEqual(code, 2)
            self.assertIn("loads as configuration", err.getvalue())
            self.assertIn("--file-name NAME", err.getvalue())


if __name__ == "__main__":
    unittest.main()
