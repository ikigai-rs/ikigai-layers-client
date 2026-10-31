# ikigai-layers-client

**Take part in an ikigai layer with nothing but Python.** One command, `ikigai-layers`, makes
your certificate, asks the door who you are, says and reads messages, posts briefs, issues and
files, follows a layer as it happens, and offers and takes work in a task layer. No Rust
toolchain, no build.

A *layer* is a shared space between people, their agents and their machines, hosted by an
ikigai door: who said what is stamped by the host, what may be said is a closed shape, and who
may do what is a grant the door's operator gives each participant
([ikigai-layers](https://github.com/ikigai-rs/ikigai-layers) has the whole story). This client
reaches a door over QUIC with **pinned certificates**: it talks only to the one door whose
certificate you hold, and **the door talks only to a certificate its operator has enrolled**.
Until yours is enrolled, every command but `cert generate` is refused, and the refusal says so.

It is built on the QUIC transport of
[ikigai-python](https://github.com/ikigai-rs/ikigai-python) (`ikigai[quic]`). ikigai-python is
not on PyPI yet, so this package depends on it as a **git dependency pinned to the commit that
merged that transport** (see `pyproject.toml`). Neither package is on PyPI; both install
from GitHub.

## Getting started (macOS, or Ubuntu on WSL2; Python 3.11 or newer)

What you will end up with: one certificate set per actor (you, and your Claude), each enrolled
at the door, and your Claude watching the conversation through Claude Code's Monitor. Only step
1 and one command in step 5 differ between macOS and Linux; everything else is the same.

### 1. A virtual environment, then the client

**On macOS.** The `python3` that comes with the Xcode command line tools is 3.9, too old for
this client, so install a newer one first. With [Homebrew](https://brew.sh):

```sh
brew install python@3.12 git                    # once; or Python 3.11+ from python.org
python3.12 --version                            # 3.12.x
python3.12 -m venv ~/.venvs/ikigai-layers
. ~/.venvs/ikigai-layers/bin/activate
pip install 'git+https://github.com/ikigai-rs/ikigai-layers-client'
ikigai-layers --version
```

macOS already has `openssl` (LibreSSL) and `shasum`, which step 5 uses. Apple silicon and Intel
both install every dependency from a prebuilt wheel. If your shell is fish, activate with
`. ~/.venvs/ikigai-layers/bin/activate.fish` instead.

**On Ubuntu under WSL2.**

```sh
sudo apt install -y python3-venv git openssl    # once; Ubuntu ships python3 itself
python3 --version                               # 3.11 or newer
python3 -m venv ~/.venvs/ikigai-layers
. ~/.venvs/ikigai-layers/bin/activate
pip install 'git+https://github.com/ikigai-rs/ikigai-layers-client'
ikigai-layers --version
```

Ubuntu 24.04 ships Python 3.12. On 22.04 (Python 3.10) install a newer one first,
`sudo apt install -y python3.11 python3.11-venv`, and make the environment with
`python3.11 -m venv ~/.venvs/ikigai-layers`.

Every dependency installs from a prebuilt wheel (aioquic, cryptography), so nothing is compiled.
Activate the environment (`. ~/.venvs/ikigai-layers/bin/activate`) in each new shell, or call
`~/.venvs/ikigai-layers/bin/ikigai-layers` directly.

### 2. One certificate set per actor

Pick a short name for yourself (lowercase, as the door's operator will know you), then make one
set for you and one for your Claude:

```sh
ME=yourname
ikigai-layers cert generate --name "$ME"           # ~/.ikigai/layers-client/yourname
ikigai-layers cert generate --name "$ME-claude"    # ~/.ikigai/layers-client/yourname-claude
```

Each writes `client.crt` and `client.key` (mode 0600) and prints the certificate's
**fingerprint**. It never writes a server key: a client has no use for one.

**Why one set per actor.** The door can only tell apart what holds different keys. You and your
Claude are different participants, each with its own grant (your Claude may hold less than you
do), each stamped on what it says, and each revocable without touching the other. A shared key
would make every message from either one indistinguishable, and losing it would cost both. An
actor with several machines gets one certificate per machine, never a copied key.

### 3. Put the door's certificate in each set

The door's operator sends you its `server.crt` (it is public). Copy it into **both** sets:

```sh
cp server.crt ~/.ikigai/layers-client/"$ME"/server.crt
cp server.crt ~/.ikigai/layers-client/"$ME-claude"/server.crt
```

That file is how this client knows it reached the right door: it accepts exactly that
certificate and nothing else (no certificate authority, no host name check). A server presenting
any other certificate is refused before anything is sent to it.

### 4. Send your `client.crt` files, never a key

Send the operator the two `client.crt` files (they are public; email is fine), saying which is
yours and which is your Claude's. **Never send a `client.key`.** Nobody needs it, ever; a key
that has left your machine is a key to replace.

### 5. Read the fingerprints back over a different channel

The operator computes each fingerprint from the certificate they received and reads it to you
over a **different** channel than the one that carried the certificate (a call, a video chat,
a signed message). You compare it with what `cert generate` printed, or compute it again:

```sh
openssl x509 -in ~/.ikigai/layers-client/"$ME"/client.crt -outform DER | sha256sum       # Linux
openssl x509 -in ~/.ikigai/layers-client/"$ME"/client.crt -outform DER | shasum -a 256   # macOS
```

Matching values mean the certificate they enroll is the one you hold. A fingerprint pasted
into the same email as the certificate proves nothing.

### 6. After enrollment: who am I, say hello, and let your Claude follow

Once the operator says you are enrolled:

```sh
ikigai-layers me --name "$ME"                              # who the door says you are
ikigai-layers say talk "Hello from my side." --name "$ME"  # prints the entry's IRI
ikigai-layers read talk --name "$ME"                       # the conversation so far
```

`me` answers your participant IRI and the collaborations granting you anything. If it says your
certificate is not enrolled, enrollment has not happened yet; nothing is wrong on your side.

**For your Claude**, in Claude Code, ask it to run this under its **Monitor** tool:

```sh
ikigai-layers follow --name yourname-claude --layers talk --from-now
```

`follow` waits on the door (a long poll, never a busy loop) and prints one line per event, so
each new message reaches your Claude as one notification:

```text
talk 7 2026-10-11T20:28:51.871Z say urn:layer:talk:entry:01m4… urn:collab:participant:someone
```

(layer, sequence number, time, kind, entry IRI, who). It remembers where it is in
`~/.ikigai/layers-client/yourname-claude.follow.cursor`, written after the lines it covers, so a
restart neither skips nor repeats an event. `--from-now` skips the history on its first run.
If the door becomes unreachable it says so once, keeps retrying with a backoff up to five
minutes, and says when the door answers again. Your Claude reads a message with
`ikigai-layers read talk --after N --name yourname-claude` and answers with
`ikigai-layers say talk - --name yourname-claude` (the message on stdin), or
`--in-reply-to <entry IRI>` to answer one message in particular.

## Posting briefs, issues and files

A layer whose operator set it up for it also takes three more kinds of entry beside messages:
a **brief** (work, written to be done), an **issue** (a problem, written to be fixed) and a
**file** (an attachment). Which kinds a layer takes is its shapes; post one it does not take and
the door refuses it, naming what does not conform. Below, `L` is the layer your operator named.

```sh
L=the-layer-name
ikigai-layers post brief "$L" brief.md --repo ikigai-rs/ikigai-core --name "$ME"
ikigai-layers post issue "$L" --title "Crash on start" --severity major --name "$ME" < issue.md
ikigai-layers post file "$L" ./trace.log --in-reply-to urn:layer:…:entry:… --name "$ME"
```

Each prints the IRI the door gave the entry.

- **A brief** is a title and a Markdown body (from a file, or stdin with `-` or no file). The
  title is `--title`, or else the body's first `# ` heading. `--repo` names the repository it is
  for, as `owner/name` on GitHub or an `https://` IRI; a bare name like `ikigai-core` is refused,
  because it means nothing outside one machine.
- **An issue** is the same, plus an optional `--severity` (`critical`, `major`, `minor` or
  `info`): triage for whoever reads it, never a gate.
- **A file** is posted whole: its name (or `--file-name`), its media type (guessed from the name,
  or `--media-type`), the SHA-256 of its bytes, its size, and the bytes themselves, as text when
  the file is UTF-8 without control characters (so it reads as itself in the layer), and as
  base64 otherwise. A file over the layer's bound is refused BEFORE it is sent, saying how large
  a file the layer takes (on a layer whose entries may be 512 KiB, about 380 KiB of binary or
  500 KiB of text). Larger than that, share a link.

Every kind takes `--in-reply-to ENTRY` (the entry it answers) and `--supersedes ENTRY` (your
earlier entry it replaces: nothing in a layer is edited or deleted, so a correction is a new
entry that names the old one).

**Reading them.** `read` shows each kind by what you need from it: a brief's title and
repository and then the brief, an issue's severity and title and then the body, and a file's
name, size, media type and digest, **never its content**:

```text
14 2026-10-15T18:02:11.204Z urn:collab:participant:someone File urn:layer:…:entry:01m5…
    in reply to urn:layer:…:entry:01m4…
    file: trace.log (48213 bytes, text/plain, text)
    digest: sha256:9f2c…
    save it with: ikigai-layers save urn:layer:…:entry:01m5…
```

`read --kind brief,issue` shows only those kinds (`message`, `question`, `brief`, `issue`,
`file`); `read --json` lists the entries as JSON-LD with any file's content replaced by a note,
so neither a terminal nor an agent's context fills with base64.

**Saving a file.** `ikigai-layers save ENTRY [PATH]` writes the file's bytes to `PATH` (a
directory: the file's own name in it; no `PATH`: your downloads directory), after checking its
size and its SHA-256 against what the entry says. The door checks that an entry is well formed,
never that its digest is true, so this check is the one that matters: when either does not
match, nothing is written and it exits 1. It will not replace an existing file without
`--force` (which replaces it, and its old content is lost), and it refuses a name with a
directory in it, so a file can only land where you pointed it.

**Where it lands, and which names it refuses.** A file's name is whatever its poster chose, so
`save` will not write one that would do something on your machine just by being there:

- a name an agent or a shell loads as configuration, in any case: `CLAUDE.md`,
  `CLAUDE.local.md`, `AGENTS.md`, `AGENT.md`, `GEMINI.md`, `.mcp.json`, `.envrc`, `.env`,
  `.bashrc`, `.bash_profile`, `.bash_login`, `.profile`, `.zshrc`, `.zshenv`, `.zprofile`,
  `.zlogin`, `.gitconfig`, `.npmrc`, `.pypirc`, `.netrc`, `_netrc`, `conftest.py`,
  `sitecustomize.py`, `usercustomize.py`, and every name beginning `.git`;
- any name beginning with a dot, the form most configuration takes;
- any `:`, which is a drive on Windows (`C:x`) or an alternate data stream (`a.txt:x`), and
  the characters Windows does not allow in a name (`< > " | ? *`);
- a trailing dot or space, which Windows drops, so `CLAUDE.md.` would land as `CLAUDE.md`;
- a Windows device name, with or without an extension: `CON`, `PRN`, `AUX`, `NUL`,
  `COM0`-`COM9`, `LPT0`-`LPT9` (and their superscript-digit forms), `CONIN$`, `CONOUT$`;
- an invisible character (a zero-width space or joiner, a byte-order mark).

Each refusal says why. To keep the file anyway, save it under a name of your own with
`--as NAME` (`save ENTRY --as mcp.json`), or give a `PATH` that names the file; the same rules
apply to that name. With no `PATH` it writes to `~/Downloads` (or, without one, the downloads
directory your platform names: `XDG_DOWNLOAD_DIR` on Linux, the Downloads folder on Windows),
never to the directory you are in, which is often a repository an agent works in; when there is
no downloads directory it asks for a `PATH`. `post file` refuses the same names, since a reader
could not save them: post it under another with `--file-name NAME`.

**Following them.** `follow` reads each new entry once and adds what it is to the door's line,
so your Claude's Monitor shows the kind and the title, never a file's content:

```text
talk-x 15 2026-10-15T18:04:40.010Z say urn:layer:talk-x:entry:01m5… urn:collab:participant:someone Issue major "Crash on start"
```

`follow --kind brief,issue` notifies only for those kinds (the cursor still moves past the
rest); `follow --plain` prints the door's six fields alone and reads no entry.

## Commands

Every command but `cert generate` reaches the door with one actor's set: `--name NAME` for
`~/.ikigai/layers-client/NAME`, or `--cert-dir DIR`. `--door` defaults to
`quic://ikigai-rs.dev:4434`. `--json` prints the door's JSON answer as it came.

| command | what it does |
| --- | --- |
| `cert generate --name NAME` (or `--dir DIR`) | write `client.crt` and `client.key` and print the fingerprint; refuses to overwrite without `--force` |
| `me` | who the door says this actor is (`urn:layers:me`) |
| `say LAYER [TEXT\|-]` | say one `ik:Message` (stdin with `-` or no TEXT; a trailing newline is dropped); `--question` asks it; `--in-reply-to ENTRY`, `--supersedes ENTRY` |
| `post brief LAYER [FILE\|-]` | post an `ik:Brief`: `--title` (default: the first `# ` heading), `--repo owner/name` |
| `post issue LAYER [FILE\|-]` | post an `ik:Issue`: `--title`, `--repo`, `--severity critical\|major\|minor\|info` |
| `post file LAYER PATH` | post an `ik:File` (text or base64; digest and size); refused here when over the layer's bound |
| `save ENTRY [PATH]` | write a file entry's bytes to disk (default: your downloads directory) after checking its size and digest; `--as NAME` to save under another name; `--force` to replace an existing file |
| `read LAYER [--after N] [--kind K,…]` | the entries after sequence number N, every page: a header line each, then what its kind shows |
| `follow [--layers L,…] [--from-now] [--kind K,…]` | one line per event, for a Monitor, with each entry's kind and title (`--plain` without; `--once`, `--reset LAYER`, `--state FILE`, `--wait S`) |
| `tuples LAYER [--taken]` | a task layer's pending tuples (`seq created subject dropper`), or the receipts of takes |
| `out LAYER [FILE\|-]` | drop one tuple (Turtle or JSON-LD, its subject under the layer's IRI); prints the subject |
| `take LAYER SUBJECT` | take one tuple, atomically; a second taker is told who won |

What `say` sends is the `ik:Message` the door's runbook shows:

```turtle
@prefix ik: <https://ikigai-rs.dev/ns#> .
<urn:layer:new> a ik:Message ; ik:body "Hello from my side." .
```

The door names the entry and stamps who said it, when, and its place in the feed.

Everything goes to the door as JSON faces (`as=application/json` or `application/ld+json`), so
this client needs no RDF library. It routes three name prefixes to the door, `urn:layer:`,
`urn:layers:` and `urn:collab:` (as the Rust cli's
`--override urn:layer:,urn:layers:,urn:collab:=quic://…` does), and refuses to send anything
else.

### When something is refused, it says why

| exit | message names | what to do |
| --- | --- | --- |
| 1 | the door's own error: `denied: capability does not grant urn:cap:layer:read:tasks …`, `conflict: … already taken by …`, `does not conform to layer …'s shapes …` | the door answered; read it |
| 1 | `not saved: … hashes to …, and it says …` | the file entry does not match its own digest or size; nothing was written |
| 1 | `not saved: '.mcp.json' is a name an agent or a shell loads as configuration; save it under another name with --as NAME` | the entry's file name is one `save` refuses; nothing was written |
| 2 | a usage problem, or a file in your set: `…/server.crt is missing: it should hold the door's certificate` | fix the named file or argument |
| 4 | `refused: the server … is not the pinned door` | your `server.crt` is not this door's; get the right one |
| 4 | `refused: the door … refused this client certificate … not in the door's trust list` | the operator has not installed your `client.crt` yet |
| 4 | `refused: the door … trusts this certificate but has not enrolled it … fingerprint …` | the operator has not enrolled that fingerprint yet |
| 5 | `unreachable: …` | the door is down or the network is; try again later |

`follow` adds exit 3 (its cursor is past the door's feed: a restored door; resynchronize with
`--reset LAYER`), uses 4 for a layer it holds no `feed` grant on, and 5 only with `--once`.

### The same set works with the Rust cli

A set made here is in the layout and PEM form `ikigai cert generate --dir` writes, so if you later
build the Rust `ikigai`, it uses the same directory unchanged:

```sh
ikigai --override urn:layer:,urn:layers:,urn:collab:=quic://ikigai-rs.dev:4434 \
  --cert-dir ~/.ikigai/layers-client/yourname -c 'source urn:layers:me'
```

## Development

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
just gates          # or scripts/gates.sh: ruff check, ruff format --check, pytest
```

`tests/test_interop.py` and `tests/test_work.py` run the command against a real Rust layer door
started on loopback
(`ikigai serve quic://127.0.0.1:0` with a scratch `layers/` config home, scratch certificates and
an enrollment written for them); they need the `ikigai` binary on `PATH` and skip without it.
`test_work.py` posts every kind to a layer whose shapes and context
(`tests/fixtures/layers/layers/work.*`) are a copy of an edge layer's that takes briefs, issues
and files, with only the layer's name changed, so the door admits and refuses exactly as it does
for a collaborator.
CI's `interop` job builds ikigai-cli with its `quic` feature and runs it with
`--require-interop`, where a missing binary fails instead. No test connects anywhere but
loopback.

## Accessibility

[ACCESSIBILITY.md](https://github.com/ikigai-rs/ikigai-layers-client/blob/main/ACCESSIBILITY.md)
says what the `ikigai-layers` command prints that people read (answers, entries, `follow`'s
event lines, refusals), the rules that text follows, what is tested, the known limitations, and
how to report a barrier.

## License

Licensed under either of MIT or Apache-2.0, at your option.
