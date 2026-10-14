"""``ikigai-layers follow``: one line per layer event, for Claude Code's Monitor.

The behavior and exit codes of the hub's ``layers-follow``, over this client's own QUIC
connection instead of a cli subprocess. Each pass is ONE long poll,
``source urn:layers:feed after=<cursor vector> wait=<s> as=application/json``: the door
answers as soon as any named layer has an event past the cursor, or after ``wait`` seconds
with nothing. Every event is printed in the door's text-face words::

    talk 7 2026-10-11T20:28:51.871Z say urn:layer:talk:entry:01m4… urn:collab:participant:alice

(layer, seq, time, kind, IRI, who), so each line is one notification in a Monitor-watched
session. The cursor vector (``talk:7,tasks:40``) is kept in a state file written atomically
AFTER the lines it covers are printed: a crash between the two replays an event (harmless)
and never skips one. A replayed sequence number is not printed twice.

Bounded everywhere, and nothing polls: ``wait`` is at most 60 s (the door's own bound), each
call is abandoned after ``wait`` + 30 s, an unreachable door is retried with a backoff that
doubles to 5 min and is reported ONCE per outage (plus one line when it recovers), and a
stale cursor or a refusal stops the follower with a non-zero exit instead of looping,
because neither fixes itself:

    exit 0  --once read fine, or --max-reads reached
    exit 2  usage, the state file is unreadable, or the cert set is (named)
    exit 3  the cursor is past the door's feed (a restored door, or another host): --reset <layer>
    exit 4  the door refused: a layer (no `feed` grant, or no such layer) or this certificate
    exit 5  --once, and the door could not be reached
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from collections.abc import Callable

import ikigai
from ikigai import quic

MAX_WAIT = 60  # the door's MAX_WAIT_SECS: a larger wait= is refused there
CALL_SLACK = 30  # seconds past `wait` before a call that has not answered is abandoned
BACKOFF_MAX = 300
FULL_READ = 1000  # the door's MAX_EVENTS: a read this long may have more behind it

_LAYER = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,22}[a-z0-9])?$")


def parse_cursor(text: str) -> dict[str, int]:
    """``talk:7,tasks:40`` as ``{"talk": 7, "tasks": 40}``; ``ValueError`` naming the bad
    part.

    >>> parse_cursor("talk:7, tasks:40")
    {'talk': 7, 'tasks': 40}
    """
    cursor: dict[str, int] = {}
    for part in (p.strip() for p in text.split(",")):
        if not part:
            continue
        layer, sep, seq = part.rpartition(":")
        if not sep or not _LAYER.match(layer) or not seq.isdigit():
            raise ValueError(f"`{part}` is not `layer:seq`")
        if layer in cursor:
            raise ValueError(f"layer `{layer}` is named twice")
        cursor[layer] = int(seq)
    return cursor


def format_cursor(cursor: dict[str, int]) -> str:
    """The door's spelling of a cursor vector, layers in name order.

    >>> format_cursor({"tasks": 40, "talk": 7})
    'talk:7,tasks:40'
    """
    return ",".join(f"{layer}:{seq}" for layer, seq in sorted(cursor.items()))


def read_state(path: str) -> dict[str, int]:
    """The cursor vector in ``path``; empty when the file does not exist yet."""
    try:
        with open(path) as f:
            return parse_cursor(f.read())
    except FileNotFoundError:
        return {}


def write_state(path: str, cursor: dict[str, int]) -> None:
    """Write ``cursor`` atomically: a temporary file beside ``path``, renamed over it."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".follow-", dir=directory)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(format_cursor(cursor) + "\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class Refused(Exception):
    """A read the follower must stop on: ``kind`` is ``stale``, ``refused`` or ``local``."""

    def __init__(self, kind: str, message: str):
        """``kind`` picks the exit code; ``message`` is the door's (or this side's) words."""
        super().__init__(message)
        self.kind = kind


class Unreachable(Exception):
    """A read that may succeed later: the door could not be reached or did not answer."""


def classify(error: Exception) -> Exception:
    """A read's failure as :class:`Refused` (stop) or :class:`Unreachable` (back off)."""
    if isinstance(error, quic.CertSetError):
        return Refused("local", str(error))
    if isinstance(error, quic.TrustError | ikigai.ProtocolError):
        return Refused("refused", str(error))
    if isinstance(error, ikigai.ConflictError) and "past" in error.message:
        return Refused("stale", error.display())
    if isinstance(error, ikigai.EndpointError):
        if error.transient:
            return Unreachable(error.display())
        return Refused("refused", error.display())
    if isinstance(error, ikigai.ConnectionLost):
        return Unreachable(str(error))
    raise error


class Follower:
    """One follower: the layers it follows, the cursor, and the connection it reads on.

    ``connect(timeout)`` returns a door (anything with ``source``); ``say`` prints a line;
    ``sleep`` waits. Injected so the loop can be tested without a door or a clock."""

    def __init__(
        self,
        connect: Callable,
        *,
        layers: list[str],
        state: str,
        wait: int,
        say: Callable[[str], None],
        sleep: Callable[[float], None] = time.sleep,
        backoff: float = 5.0,
    ):
        """``wait`` is each read's long poll in seconds (1..60); ``backoff`` the first retry
        delay after a failed read, doubling to :data:`BACKOFF_MAX`."""
        self._connect = connect
        self._door = None
        self.layers = layers
        self.state = state
        self.wait = wait
        self.say = say
        self.sleep = sleep
        self.backoff = backoff
        self.cursor: dict[str, int] = {}

    def read(self, cursor: dict[str, int], wait: int) -> list[tuple[str, int, str]]:
        """One long poll: ``(layer, seq, line)`` per event, or :class:`Refused` /
        :class:`Unreachable`."""
        try:
            if self._door is None:
                self._door = self._connect(wait + CALL_SLACK)
            answer = self._door.source(
                "urn:layers:feed",
                after=format_cursor(cursor),
                wait=str(wait),
                **{"as": "application/json"},
            )
        except Exception as e:  # noqa: BLE001 - every failure is classified, or re-raised
            raise classify(e) from e
        try:
            events = json.loads(answer.data)["events"]
            return [
                (
                    e["layer"],
                    int(e["seq"]),
                    f"{e['layer']} {e['seq']} {e['time']} {e['kind']} {e['iri']} {e['by']}",
                )
                for e in events
            ]
        except (ValueError, KeyError, TypeError) as e:
            raise Refused("refused", f"the door's feed answer is not the JSON face: {e}") from e

    def run(
        self,
        *,
        from_now: bool = False,
        reset: list[str] = (),
        once: bool = False,
        max_reads: int = 0,
    ) -> int:
        """Follow until ``once``/``max_reads`` or a refusal; the exit code (module doc)."""
        try:
            self.cursor = read_state(self.state)
        except (OSError, ValueError) as e:
            self.say(f"ikigai-layers follow: cannot read the cursor in {self.state}: {e}")
            return 2
        fresh = [layer for layer in self.layers if layer not in self.cursor]
        for layer in self.layers:
            self.cursor.setdefault(layer, 0)
        for layer in reset:
            self.cursor[layer] = 0

        def view(layers):
            return {layer: self.cursor[layer] for layer in layers}

        if from_now and fresh:
            # Read the history without printing it, while a read comes back full.
            while True:
                try:
                    events = self.read(view(fresh), 1)
                except Refused as e:
                    return self._stop(e)
                except Unreachable as e:
                    self.say(f"ikigai-layers follow: --from-now could not read the feed: {e}")
                    return 5
                for layer, seq, _ in events:
                    if layer in fresh:
                        self.cursor[layer] = max(self.cursor[layer], seq)
                if len(events) < FULL_READ:
                    break
        write_state(self.state, self.cursor)

        reads, delay, outage = 0, self.backoff, None
        while True:
            try:
                events = self.read(view(self.layers), self.wait)
            except Refused as e:
                return self._stop(e)
            except Unreachable as e:
                if once:
                    self.say(f"ikigai-layers follow: the door did not answer: {e}")
                    return 5
                if outage is None:
                    outage = time.monotonic()
                    self.say(
                        f"ikigai-layers follow: the door is unreachable ({e}); retrying, backing "
                        f"off to {BACKOFF_MAX}s, and saying nothing more until it answers"
                    )
                self.sleep(delay)
                delay = min(delay * 2, BACKOFF_MAX)
                continue
            if outage is not None:
                self.say(
                    "ikigai-layers follow: the door answers again after "
                    f"{int(time.monotonic() - outage)}s"
                )
                outage, delay = None, self.backoff
            advanced = False
            for layer, seq, line in events:
                if layer not in self.layers or seq <= self.cursor[layer]:
                    continue  # a replay, or a layer this follower did not ask for
                self.say(line)
                self.cursor[layer] = seq
                advanced = True
            if advanced:
                write_state(self.state, self.cursor)
            reads += 1
            if once or (max_reads and reads >= max_reads):
                return 0

    def _stop(self, refusal: Refused) -> int:
        if refusal.kind == "stale":
            self.say(
                f"ikigai-layers follow: STOPPED, the cursor {format_cursor(self.cursor)} is past "
                f"the door's feed (a restored door, or another host): {refusal}. Resynchronize "
                "with --reset <layer> (events are replayed from 0)."
            )
            return 3
        if refusal.kind == "local":
            self.say(f"ikigai-layers follow: STOPPED: {refusal}")
            return 2
        self.say(
            f"ikigai-layers follow: STOPPED, the door refused: {refusal}. Check this actor's "
            "grant and its certificate with the door's operator."
        )
        return 4

    def close(self) -> None:
        """Close the connection, if one was opened."""
        if self._door is not None:
            self._door.close()
            self._door = None
