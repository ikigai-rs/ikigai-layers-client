"""Files as layer entries: what ``post file`` writes and what ``save`` checks.

An ``ik:File`` entry carries a file whole: its bare name (``ik:fileName``), its media type
(``ik:mediaType``), the SHA-256 of its bytes (``ik:contentHash``, spelled ``sha256:<hex>``),
its size (``ik:byteSize``) and the bytes themselves (``ik:content``), either as the text
(``ik:encoding "text"``) or as base64 (``ik:encoding "base64"``).

**Text when it can be, base64 when it must.** A file that is UTF-8 with no control character
but tab, newline and carriage return travels as its text, so a reader of the layer's graph
can read it; anything else travels as base64. Either way the digest and the size are of the
file's BYTES, and :func:`decode` checks both before anything is written: the door checks the
entry's shape, but it cannot check that a digest matches its content.

**Bounded before it is sent.** A layer that takes files bounds ``ik:content`` in its shapes
(``sh:maxLength``); :func:`content_bound` reads that bound from the shapes the door serves,
and ``post file`` refuses a file whose encoded content is over it, saying how large a file
the layer takes, instead of sending it to be refused.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import mimetypes
import os
import re
import sys
import unicodedata
from pathlib import Path

from .message import entry, literal

# What a file layer's shapes require of a media type (tests/fixtures/…/work.shapes.ttl): lowercase
# `type/subtype`, no parameters.
_MEDIA = re.compile(r"^[a-z0-9][a-z0-9!$&^_.+-]*/[a-z0-9][a-z0-9!$&^_.+-]*$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
# The property shape bounding ik:content in a layer's shapes. The door serves its shapes with
# every blank node named (`<urn:layer:{layer}:shapes:n45> sh:path ik:content ; …`), one subject's
# statements possibly in several places; a shapes FILE writes it as `[ sh:path ik:content ; … ]`.
_CONTENT = r"(?:ik:content|<https://ikigai-rs\.dev/ns#content>)"
_CONTENT_SUBJECT = re.compile(r"(<[^>\s]+>)\s+sh:path\s+" + _CONTENT + r"\s*[;.]")
_CONTENT_SHAPE = re.compile(r"\[\s*sh:path\s+" + _CONTENT + r"\s*;[^\]]*\]")
# One statement's predicate-object list up to sh:maxLength, never past its closing `.` (an IRI
# or a string may hold a dot; nothing else in a shape does).
_UP_TO_MAX = r'(?:[^"<.]|<[^>]*>|"(?:[^"\\]|\\.)*")*?sh:maxLength\s+"?(\d+)'
_MAX_LENGTH = re.compile(r"sh:maxLength\s+\"?(\d+)")
_TARGETS_FILE = re.compile(r"sh:targetClass\s+(?:ik:File|<https://ikigai-rs\.dev/ns#File>)\b")
_ALLOWED_CONTROLS = {"\t", "\n", "\r"}


class IntegrityError(Exception):
    """A file entry whose content does not match its own digest or size, or cannot be read."""


def digest(data: bytes) -> str:
    """The digest an ``ik:File`` names: ``sha256:`` and the lowercase hex of ``data``'s SHA-256.

    >>> digest(b"")
    'sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
    """
    return "sha256:" + hashlib.sha256(data).hexdigest()


def encode(data: bytes) -> tuple[str, str]:
    """``("text", the text)`` when ``data`` is UTF-8 with no control character but tab,
    newline and carriage return, else ``("base64", its base64)``.

    >>> encode(b"two\\nlines\\n")
    ('text', 'two\\nlines\\n')
    >>> encode(b"\\x89PNG\\r\\n")
    ('base64', 'iVBORw0K')
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    if text is not None and not any(
        (ord(c) < 0x20 and c not in _ALLOWED_CONTROLS) or ord(c) == 0x7F for c in text
    ):
        return "text", text
    return "base64", base64.b64encode(data).decode("ascii")


class UnsafeName(ValueError):
    """A file name ``save`` will not write (and ``post file`` will not send), and why."""


#: Names an agent, a shell or a tool loads as configuration, or as credentials, from the
#: directory it runs in or from home, compared case-insensitively. Most begin with a dot, and
#: the leading-dot rule refuses every one of those anyway; they are listed so the reason a
#: reader sees is the specific one, and so a port of this rule carries the whole list. Every
#: name beginning ``.git`` is refused too (``.gitconfig``, ``.gitattributes``, ``.git``).
CONFIG_NAMES = frozenset(
    n.casefold()
    for n in (
        # agent instructions and tool servers
        "CLAUDE.md",
        "CLAUDE.local.md",
        "AGENTS.md",
        "AGENT.md",
        "GEMINI.md",
        ".mcp.json",
        # shells and direnv
        ".envrc",
        ".env",
        ".bashrc",
        ".bash_profile",
        ".bash_login",
        ".profile",
        ".zshrc",
        ".zshenv",
        ".zprofile",
        ".zlogin",
        # credentials read by git, npm, pip and curl (``_netrc`` is the Windows spelling)
        ".gitconfig",
        ".npmrc",
        ".pypirc",
        ".netrc",
        "_netrc",
        # Python code that pytest and the interpreter import on their own
        "conftest.py",
        "sitecustomize.py",
        "usercustomize.py",
    )
)

#: Windows device names, reserved with or without an extension (``CON``, ``con.txt``),
#: as Microsoft's naming rules list them (``COM0``-``COM9``, ``LPT0``-``LPT9`` and the
#: superscript-digit ports included).
WINDOWS_DEVICES = frozenset(
    {"con", "prn", "aux", "nul", "conin$", "conout$"}
    | {f"{port}{n}" for port in ("com", "lpt") for n in "0123456789\u00b9\u00b2\u00b3"}
)
_WINDOWS_FORBIDDEN = set('<>"|?*')
_DRIVE = re.compile(r"^[A-Za-z]:")


def safe_name(name: str) -> str:
    """``name`` if it is a bare file name a reader can save without leaving the directory it
    chose and without arming anything there, else :class:`UnsafeName` saying why.

    Refused: a name with a directory in it, a control or invisible character, a name an agent
    or a shell loads as configuration (:data:`CONFIG_NAMES`, and every ``.git`` name), any
    name beginning with a dot, any ``:`` (a Windows drive, ``C:x``, or an alternate data
    stream, ``a.txt:x``), a character Windows does not allow, a trailing dot or space (Windows
    drops it, so ``CLAUDE.md.`` would land as ``CLAUDE.md``), and a Windows device name with
    or without an extension (``CON``, ``nul.txt``).

    >>> safe_name("notes.md")
    'notes.md'
    >>> try:
    ...     safe_name(".envrc")
    ... except UnsafeName as e:
    ...     print(e)
    '.envrc' is a name an agent or a shell loads as configuration
    """
    if (
        not name
        or name in (".", "..")
        or "/" in name
        or "\\" in name
        or any(ord(c) < 0x20 or ord(c) == 0x7F for c in name)
    ):
        raise UnsafeName(f"{name!r} is not a bare file name")
    if len(name) > 255:
        raise UnsafeName(f"{name[:40]!r}… is over 255 characters")
    hidden = [c for c in name if unicodedata.category(c) in ("Cf", "Zl", "Zp")]
    if hidden:
        raise UnsafeName(f"{name!r} holds an invisible character (U+{ord(hidden[0]):04X})")
    # As a case-insensitive file system, and Windows, would compare it.
    key = unicodedata.normalize("NFKC", name).rstrip(". ").casefold()
    if key in CONFIG_NAMES or key.startswith(".git"):
        raise UnsafeName(f"{name!r} is a name an agent or a shell loads as configuration")
    if name.startswith("."):
        raise UnsafeName(f"{name!r} begins with a dot: a hidden file, the form configuration takes")
    if _DRIVE.match(name):
        raise UnsafeName(f"{name!r} begins with a Windows drive ({name[:2]})")
    if ":" in name:
        raise UnsafeName(f"{name!r} holds ':', which Windows reads as an alternate data stream")
    bad = sorted(set(name) & _WINDOWS_FORBIDDEN)
    if bad:
        raise UnsafeName(f"{name!r} holds {bad[0]!r}, which Windows does not allow in a name")
    if name[-1] in ". ":
        raise UnsafeName(f"{name!r} ends in a dot or a space, which Windows drops from a name")
    if name.split(".")[0].rstrip(" ").casefold() in WINDOWS_DEVICES:
        raise UnsafeName(f"{name!r} is a Windows device name ({name.split('.')[0]})")
    return name


def downloads_dir(
    home: Path | None = None,
    platform: str | None = None,
    registry=None,
) -> Path | None:
    """Where ``save`` writes when it is given no PATH: ``~/Downloads`` when it is a
    directory, else the platform's own downloads directory (the ``XDG_DOWNLOAD_DIR`` of
    ``~/.config/user-dirs.dirs``; on Windows, the Downloads known folder), else ``None``.
    Never home itself: a downloads directory set to home is the XDG spelling of "none".

    ``registry`` stands in for the Windows lookup (a callable answering the folder's path or
    ``None``), so a test can take the Windows branch on any platform.
    """
    home = Path.home() if home is None else home
    platform = sys.platform if platform is None else platform

    def usable(candidate: Path | None) -> Path | None:
        if candidate is None or not candidate.is_dir():
            return None
        try:
            if candidate.resolve() == home.resolve():
                return None
        except OSError:
            return None
        return candidate

    found = usable(home / "Downloads")
    if found is not None:
        return found
    if platform == "win32":
        lookup = _windows_downloads if registry is None else registry
        value = lookup()
        return usable(Path(value)) if value else None
    try:
        dirs = (home / ".config" / "user-dirs.dirs").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    for line in dirs.splitlines():
        m = re.match(r'^\s*XDG_DOWNLOAD_DIR\s*=\s*"([^"]*)"\s*$', line)
        if m is None:
            continue
        value = m.group(1)
        if value.startswith("$HOME/"):
            return usable(home / value[len("$HOME/") :])
        if value.startswith("/"):
            return usable(Path(value))
        return None
    return None


def _windows_downloads() -> str | None:
    """The Downloads known folder, from the user's shell folders in the registry."""
    try:
        import winreg  # type: ignore[import-not-found]

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "{374DE290-123F-4565-9164-39C4925E467B}")
    except (ImportError, OSError):
        return None
    return os.path.expandvars(value) if isinstance(value, str) else None


def media_type(path: Path, encoding: str) -> str:
    """The media type of ``path`` by its extension, lowercased; when the extension says
    nothing, ``text/plain`` for text and ``application/octet-stream`` for anything else.

    >>> media_type(Path("brief.md"), "text")
    'text/markdown'
    """
    guessed, _ = mimetypes.guess_type(path.name, strict=False)
    if path.suffix.lower() in (".md", ".markdown"):
        guessed = "text/markdown"  # not in every platform's table
    if guessed is None:
        guessed = "text/plain" if encoding == "text" else "application/octet-stream"
    return checked_media_type(guessed.lower())


def checked_media_type(value: str) -> str:
    """``value`` if the layer's shapes take it as a media type (lowercase ``type/subtype``,
    no parameters), else ``ValueError``."""
    if not _MEDIA.match(value) or len(value) > 127:
        raise ValueError(f"`{value}` is not a media type a layer takes (lowercase type/subtype)")
    return value


def takes_files(shapes: str) -> bool:
    """Whether a layer's shapes (Turtle, as ``urn:layer:{layer}:shapes`` serves them) admit
    an ``ik:File``."""
    return bool(_TARGETS_FILE.search(shapes))


def content_bound(shapes: str) -> int | None:
    """The ``sh:maxLength`` the shapes put on ``ik:content``, or ``None`` when they put none.

    >>> content_bound('[ sh:path ik:content ; sh:maxCount 1 ; sh:maxLength 520192 ] .')
    520192
    >>> content_bound('''<urn:s:n45> sh:path ik:content ;
    ...     sh:datatype xsd:string .
    ... <urn:s:n45> sh:maxLength 520192 .''')
    520192
    """
    subject = _CONTENT_SUBJECT.search(shapes)
    if subject is not None:
        for statement in re.finditer(re.escape(subject.group(1)) + r"\s" + _UP_TO_MAX, shapes):
            return int(statement.group(1))
        return None
    shape = _CONTENT_SHAPE.search(shapes)
    if shape is None:
        return None
    bound = _MAX_LENGTH.search(shape.group(0))
    return int(bound.group(1)) if bound else None


def too_large(name: str, size: int, encoding: str, content: str, bound: int) -> str | None:
    """Why the encoded ``content`` is over ``bound``, naming the largest file the layer takes;
    ``None`` when it fits. Measured in UTF-8 bytes of the Turtle literal, which is never less
    than the characters ``sh:maxLength`` counts, so a file that fits here fits the door's
    entry bound too."""
    encoded = len(literal(content).encode("utf-8")) - 2
    if encoded <= bound:
        return None
    binary = bound // 4 * 3
    return (
        f"{name} is {size} bytes, {encoded} bytes as {encoding}; this layer takes at most "
        f"{bound} bytes of content, which is a file of about {binary} bytes of binary or "
        f"{bound} bytes of plain text. Share a link to it instead."
    )


def file_entry(
    name: str,
    data: bytes,
    *,
    media: str | None = None,
    in_reply_to: str | None = None,
    supersedes: str | None = None,
) -> tuple[str, str, str]:
    """``(turtle, encoding, content)`` for one ``ik:File`` holding ``data`` under ``name``
    (its media type guessed from the name unless ``media`` says it).

    >>> turtle, encoding, _ = file_entry("hi.txt", b"hello\\n")
    >>> encoding, "ik:byteSize 6" in turtle, 'ik:encoding "text"' in turtle
    ('text', True, True)
    """
    safe_name(name)
    encoding, content = encode(data)
    kind = checked_media_type(media.lower()) if media else media_type(Path(name), encoding)
    said = [
        f"ik:fileName {literal(name)}",
        f"ik:mediaType {literal(kind)}",
        f"ik:contentHash {literal(digest(data))}",
        f"ik:byteSize {len(data)}",
        f"ik:encoding {literal(encoding)}",
        f"ik:content {literal(content)}",
    ]
    return entry("File", said, in_reply_to=in_reply_to, supersedes=supersedes), encoding, content


def one(value):
    """A JSON-LD value as one plain value: the first of a list, a ``@value`` unwrapped."""
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        value = value.get("@value", value.get("@id"))
    return value


def decode(node: dict) -> bytes:
    """The bytes of a file entry (a node of the layer's JSON-LD face), after checking its
    size and its digest against them; :class:`IntegrityError` naming what does not match."""
    encoding, content = one(node.get("encoding")), one(node.get("content"))
    expected, size = one(node.get("contentHash")), one(node.get("byteSize"))
    if content is None or encoding not in ("text", "base64"):
        raise IntegrityError(f"{node.get('@id')} carries no content this client can read")
    if encoding == "text":
        data = str(content).encode("utf-8")
    else:
        try:
            data = base64.b64decode(str(content), validate=True)
        except (binascii.Error, ValueError) as e:
            raise IntegrityError(f"{node.get('@id')}: its base64 does not decode: {e}") from e
    if size is None or int(size) != len(data):
        raise IntegrityError(
            f"{node.get('@id')}: its content is {len(data)} bytes, and it says {size}"
        )
    if not isinstance(expected, str) or not _DIGEST.match(expected):
        raise IntegrityError(f"{node.get('@id')}: its digest {expected!r} is not sha256:<hex>")
    if digest(data) != expected:
        raise IntegrityError(
            f"{node.get('@id')}: its content hashes to {digest(data)}, and it says {expected}"
        )
    return data
