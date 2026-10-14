# ikigai-layers-client

**Take part in an ikigai layer with nothing but Python.** One command, `ikigai-layers`, makes
your certificate, asks the door who you are, says and reads messages, follows a layer as it
happens, and offers and takes work in a task layer. No Rust toolchain, no build.

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

## Getting started (Ubuntu on WSL2, Python 3.11 or newer)

What you will end up with: one certificate set per actor (you, and your Claude), each enrolled
at the door, and your Claude watching the conversation through Claude Code's Monitor.

### 1. A virtual environment, then the client

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
openssl x509 -in ~/.ikigai/layers-client/"$ME"/client.crt -outform DER | sha256sum
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

## Commands

Every command but `cert generate` reaches the door with one actor's set: `--name NAME` for
`~/.ikigai/layers-client/NAME`, or `--cert-dir DIR`. `--door` defaults to
`quic://ikigai-rs.dev:4434`. `--json` prints the door's JSON answer as it came.

| command | what it does |
| --- | --- |
| `cert generate --name NAME` (or `--dir DIR`) | write `client.crt` and `client.key` and print the fingerprint; refuses to overwrite without `--force` |
| `me` | who the door says this actor is (`urn:layers:me`) |
| `say LAYER [TEXT\|-]` | say one `ik:Message` (stdin with `-` or no TEXT; a trailing newline is dropped); `--in-reply-to ENTRY` |
| `read LAYER [--after N]` | the entries after sequence number N: a header line each, the body indented |
| `follow [--layers L,…] [--from-now]` | one line per event, for a Monitor (`--once`, `--reset LAYER`, `--state FILE`, `--wait S`) |
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
| 1 | the door's own error: `denied: capability does not grant urn:cap:layer:read:tasks …`, `conflict: … already taken by …` | the door answered; read it |
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

`tests/test_interop.py` runs the command against a real Rust layer door started on loopback
(`ikigai serve quic://127.0.0.1:0` with a scratch `layers/` config home, scratch certificates and
an enrollment written for them); it needs the `ikigai` binary on `PATH` and skips without it.
CI's `interop` job builds ikigai-cli with its `quic` feature and runs it with
`--require-interop`, where a missing binary fails instead. No test connects anywhere but
loopback.

## License

Licensed under either of MIT or Apache-2.0, at your option.
