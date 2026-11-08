# ikigai-layers-client

**Take part in an ikigai layer with nothing but Python.** One command, `ikigai-layers`, makes
your certificate, asks the door who you are, says and reads messages, posts briefs, issues and
files, follows a layer as it happens, offers and takes work in a task layer, and runs your agent
on the offers routed to it. No Rust toolchain, no build.

A *layer* is a shared space between people, their agents and their machines, hosted by an
ikigai door: who said what is stamped by the host, what may be said is a closed shape, and who
may do what is a grant the door's operator gives each participant
([ikigai-layers](https://github.com/ikigai-rs/ikigai-layers) has the whole story). This client
reaches a door over QUIC with **pinned certificates**: it talks only to the one door whose
certificate you hold, and **the door talks only to a certificate its operator has enrolled**.
A door with the enrollment lobby enabled also accepts `onboard` before enrollment.

It is built on the QUIC transport of
[ikigai-python](https://github.com/ikigai-rs/ikigai-python) (`ikigai[quic]`). ikigai-python is
not on PyPI yet, so this package depends on it as a **git dependency pinned to the commit that
merged that transport** (see `pyproject.toml`). Neither package is on PyPI; both install
from GitHub.

## Enroll with an invite (including Windows and WSL2)

With Python 3.11+ and Git installed, install the client in a virtual environment as below.
On native Windows, use `py -3 -m venv .venv`, activate with
`.venv\Scripts\Activate.ps1` in PowerShell, then
`python -m pip install 'git+https://github.com/ikigai-rs/ikigai-layers-client'`.

If your operator provided an invite for a door with the lobby enabled, enroll with one command
(the example fits PowerShell, macOS and WSL2 shells; replace the example values):

```sh
ikigai-layers onboard --name mini --door quic://door.example.org:4434 --server-cert server.crt --pin SHA256 --invite INVITE_ID --code PAIRING_CODE
```

The door's certificate must match the SHA-256 fingerprint your operator supplied. Colons and
uppercase hex are accepted. The client generates its certificate and private key locally using
Python's existing dependencies, posts an `onb:EnrollmentRequest` with the starter's exact request
format and HMAC-SHA256 proof, and polls for controller approval. No gonk, starter, Rust, openssl
command, or symlink permission is required. The key stays in the identity directory; the pairing
code is never saved or printed by the client.

The default directory is `~/.ikigai/layers-client/mini` (`~` is your user home on Windows too).
It contains `client.crt`, `client.key`, the pinned `server.crt`, `onboard.json` recording the
posted request, and, after approval, `config.toml` with the door URL. Files are created with mode
0600 and a new identity directory with 0700 on POSIX; Windows uses the directory's inherited
access controls. Keep it in your private user profile. Use `--cert-dir DIR` instead of `--name`
for another directory. An existing matching client pair is reused, including with `--force`.

`--once` posts if needed and checks approval once, exiting **3** while pending. Otherwise polling
backs off from 5 seconds to 300 seconds, for up to `--max-wait SECONDS` (default 86400). Rerun the
same command to resume without reposting; `--server-cert` may be omitted once the pinned file is
present. A different invite or door posts a new request. A differing server certificate or
configuration is kept unless you give `--force`. Pin validation still applies with `--force`.

Approval requires a current entitlement for this certificate with `onb:layerGrant` capability
IRIs such as `urn:cap:layer:take:tasks`. The client reads every page of grants, honors
`ik:supersedes`, ignores expired entitlements, and refuses a current revocation or an entitlement
that grants only gonk access. The door continues to enforce every capability.

After approval, normal commands use the saved door (`--door` overrides it):

```sh
ikigai-layers me --name mini
ikigai-layers tuples tasks --name mini
ikigai-layers take tasks urn:layer:tasks:tuple:EXAMPLE --name mini
```

These commands offer and take task tuples by hand; [`work`](#running-an-agent-on-offers-routed-to-it)
runs an agent on them for you. For doors without a lobby, use the manual certificate exchange
below.

## Public read-only feeds

A door that offers public feed joining needs no invite or controller approval. Start with
an HTTPS host and a new named certificate set:

```sh
ikigai-layers join https://ikigai-rs.dev --name reader
ikigai-layers join https://ikigai-rs.dev releases features --name reader
ikigai-layers feeds --name reader
ikigai-layers feeds releases --scheme --name reader
ikigai-layers entries releases --concept urn:release:feature --name reader
ikigai-layers follow --layers releases --concept urn:release:feature --name reader
```

The host defaults to `https://ikigai-rs.dev`; `join releases --name reader` selects one
feed there, and `join --name reader` selects every advertised public read-only feed.
The actor name defaults to the machine's short host name; `--cert-dir DIR` selects an
explicit directory. Joining generates a client pair only when neither file exists and
reuses a complete, matching pair. A partial pair is refused. A different saved door or
server certificate is refused with instructions to use a separate name. There is no
implicit replacement of an existing identity or pin.

Discovery uses system-trusted HTTPS, including HTTPS-only redirects, to fetch
`/.well-known/ikigai/layers.json`. The client checks the advertised server certificate's
SHA-256 **over its DER bytes**, the same fingerprint convention as `onboard --pin`,
before generating an identity or opening QUIC. It then posts one `onb:JoinRequest` to
the advertised lobby and prints the identity fingerprint and the synchronous answer's
granted capability IRIs.
Only advertised public read-only layers typed `ik:Feed` may be requested; a response
that grants writes or unrelated layers is refused. The private key stays local.
Public feed membership needs no roster read: `feeds` discovers readable layers from
the door's capability-scoped catalog and their definitions.

`feeds` shows each feed with its newest entry, or says `no entries`. `feeds FEED --scheme`
prints its SKOS concept tree with labels and full IRIs. A concept with several parents
can appear under each; cycles are labeled. Both `entries` (an alias of `read`) and
`follow` accept repeatable `--concept IRI` options. The selections are combined as a
union, each including every descendant in the layer's current SKOS scheme. The door
applies the filter; this client does not keep a second scheme cache. `--kind` can narrow
the result further. Filters are refused on non-feed layers. `follow --plain` still
filters but omits entry descriptions. Its cursor advances past excluded events, just
as with `--kind`; use a separate `--state FILE` or `--reset FEED` to replay with a different
filter. Listing the newest entry pages through the feed's entries.

To ask about a feed entry, use a certificate that also has its own writable Talk channel:

```sh
ikigai-layers ask urn:layer:releases:entry:EXAMPLE 'What changed for me?' --name me
printf '%s\n' 'Does this affect our setup?' | ikigai-layers ask urn:layer:releases:entry:EXAMPLE - --name me
```

`ask` checks the readable entry belongs to a feed, then posts an `ik:Question` with
`dcterms:subject` naming that entry into the caller's sole writable non-feed channel.
When several qualify, choose `--layer NAME`; a feed, tuplespace, or ungranted channel
cannot be selected. A joined public reader with no writable channel gets a named refusal
and nothing is posted. The destination channel's shapes must admit questions with
`dcterms:subject`.

### Discovery and join wire contract

The open-join host and feed-layer support are companion work. These commands expect the
following descriptor (relative certificate URLs resolve against the descriptor URL):

```json
{
  "door": "quic://example.org:4434",
  "server_cert": {"url": "server.crt", "sha256": "64 hexadecimal digits"},
  "join": "urn:layer:join:say",
  "public_layers": [
    {"name": "releases", "type": "ik:Feed", "read_only": true}
  ]
}
```

`type` also accepts `https://ikigai-rs.dev/ns#Feed`. Discovery and certificate reads have
30-second timeouts and a 1 MiB size limit. The request is Turtle with type
`onb:JoinRequest`, `onb:certificate` (client PEM), `onb:fingerprint`, and one or more
`onb:layer` IRIs. The lobby answers JSON with `granted`, an array of
`urn:cap:layer:read:NAME` and/or `urn:cap:layer:feed:NAME` capabilities for the requested
feeds. After success, `config.toml` records the door for subsequent commands.

Definitions and `urn:layer:FEED:scheme` use the door's Turtle face; the client reads
named-node statements, prefixes, predicate/object lists, and string labels with optional
language or datatype suffixes. Unsupported Turtle syntax is refused. Definitions type
the layer as `ik:Feed`; writable channels use `ik:layerModel "channel"`. Entries use
`application/ld+json`, with `concept=IRI,IRI` passed to the feed's `entries` endpoint.
During following, each new entry event is checked against that same filtered endpoint,
and the cursor also covers events that do not match.

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

## Running an agent on offers routed to it

`ikigai-layers work run` is the loop for a collaborator without gonk: it follows a task layer,
takes an offer routed to this agent, runs Claude Code, Codex or Hermes in a fresh clone of the
repository the offer names, and posts a report back to the offerer with the pull request and its
CI state. It follows `tools/worker.py` from
[ikigai-gonk-starter](https://github.com/ikigai-rs/ikigai-gonk-starter), with the layer in the
ledger's place. It needs macOS, Linux or WSL2, `git`, and `gh` signed in as the agent's account.

**Read this before you run it:**

- **Listing a principal in `routers` lets it run code under this Unix account, with that account's
  credentials.** An offer's brief is the prompt of an agent that runs commands. Add only principals
  you would give a shell on this account.
- **Run the loop as a dedicated account,** holding nothing but what the agent should use. Nothing
  checks this for you: a loop under your own account puts your own keys and tokens within the
  agent's reach.
- **The environment scrub removes publishing tokens only.** `GH_TOKEN`, cloud credentials and
  everything else in the environment stay, and so does every file in the account's home.
- **The agent's report file is posted to the layer.** Its lines are bounded in length and not
  filtered, so it must never contain secrets. The agent's brief says so, but nothing checks it.

Enroll the agent with `onboard` (or the manual exchange above), then write `worker.toml` in its
cert set:

```toml
layer   = "tasks"
actor   = "urn:collab:participant:yourname-agent"  # who the door must say this certificate is
routers = ["urn:collab:participant:yourname"]      # whose offers this agent may run
orgs    = ["ikigai-rs"]                            # or [repos."owner/name"] for one repository
# exclude = ["ikigai-rs/not-this-one"]
# labels = ["agent:claude"]   # only offers carrying all of these
# kinds  = ["bug"]            # only offers of one of these kinds
# budget = "2h"               # the agent's wall-clock; also check_every, grace, ci_wait, ci_poll
# work_root = "~/work"        # must be under this account's home
# keep = false                # keep the clone after a hand-back

[agent]
preset = "claude"             # or codex, hermes; or command = ["my-agent", "{prompt_file}"]
```

```sh
ikigai-layers work plan --name yourname-agent        # what it would take, and why not the rest
ikigai-layers work run --once --name yourname-agent  # at most one offer, then exit
ikigai-layers work run --name yourname-agent         # follow the layer until stopped
```

An unattended agent also needs its own permission settings (Claude Code's allowlist or permission
mode, Codex's sandbox and approval policy); this command does not choose them for you.

There are two ways to stop it:

- **Between offers:** `touch ~/.ikigai/layers-client/NAME.work/stop`. A running agent finishes and
  reports, then the loop exits 0 before taking another offer. An idle loop notices within a minute,
  or within five minutes while the door is unreachable. The file stays until you remove it, and
  until then the loop stops as soon as it starts. A report it still owes stays in
  `NAME.work/offers.json` for the next run that reaches the door.
- **Now:** SIGTERM or Ctrl-C stops the running agent and releases the offer.

A command's placeholders are `{prompt}`, `{prompt_file}`, `{workdir}`, `{branch}`, `{offer}`,
`{repo}`, `{report_file}` and `{profile}`. A command without `{prompt}` gets the prompt on stdin.
Prefer that or `{prompt_file}`, since other local users can read a process's arguments. The `claude`
and `codex` presets use stdin. Hermes' `-z` only takes the prompt as an argument, so the `hermes`
presets put it there. The prompt file and the log are readable by this account alone. A command that
passes `{prompt}` to a shell (`sh -c`, `bash -c`, also through `env`) is refused, because the shell
would run the offer's text as commands.

**Which offers it takes.** All of these, checked before the take and again on the tuple as taken:

- `routers` names who dropped it. The door stamps that, so nobody else's offer reaches your agent.
  It is required, and it is the only routing an offer from gonk's mirror has so far: those offers
  are closed to a title, a brief and a repository.
- `labels` and `kinds`, when you set them, must match the offer's `label`, `labels` and `kind`
  terms. An offer that carries none does not match, so set them only for a layer whose shapes and
  context admit those terms.
- An offer with `addressedTo` must name this actor.
- It names one `https://github.com/owner/name` repository that `orgs` or `[repos]` admit, and `gh`
  says that repository is public, not archived, still under that name, and that this account may
  push to it.

`work plan` lists every pending offer and the reason for each skip. `run` says a skip once, naming
subjects and reasons only: an offer's title and brief go to the agent, never to this output.

**What comes back.** Addressed to the offerer, named from what it says, so sending one twice is one
record:

| report | when | carries |
| --- | --- | --- |
| `ik:Progress` | when the agent starts, then once a day | a note that an agent is working |
| `ik:Handback` | a pull request, or an accepted URL on the report file's first line | `ik:evidence` and a note with each pull request's CI state |
| `ik:Release` | no evidence, budget spent, stopped, or no clone | a note saying why; any clone is kept |

Evidence is what the controller accepts, a pull request URL or a commit URL in the offered
repository. A note is at most 2,000 characters and this command adds no local path to it, but it
quotes what the agent wrote in its report file as written, so that file must never contain secrets.
The controller treats all of this as data: it checks the evidence
and decides what to do. A report the door did not answer is kept in `NAME.work/offers.json` beside
the cert set and sent again, and a loop that restarts releases an offer it was still holding.

**The boundaries.**

- **No merge, no publish.** The command runs neither, and the agent's brief forbids both. The
  agent's environment loses publishing tokens (`CARGO_REGISTRY_TOKEN`, `TWINE_*`, `NPM_TOKEN` and
  the like), and a merge made before it exited is called out in the report. This is not enforcement
  against a determined agent: a credential file in the account's home stays readable, and only
  branch protection can stop a merge. Give the account nothing it should not use.
- **As the agent's account.** Run it under a Unix account of its own. It refuses root, a
  `client.key` that another account owns or others can read, a work root outside this home, and a
  certificate the door says is not `actor`.
- **One clone per offer.** No offer's agent ever gets a clone another agent had, and the loop runs
  no git command in a clone once its agent has started: removing it is deleting it. The loop's own
  git also runs with hooks and fsmonitor off. That closes the `.git` directory as a way to run code
  outside an agent's budget. It does not contain an agent under this account, which can still edit
  `~/.gitconfig` or leave a process running after it exits. Only a separate account or a container
  limits that. Each offer clones the whole repository, so a large one costs time and disk per offer.
- **Never above the agent's grant.** The door enforces the grant, and this command holds one
  certificate. A denial stops the loop with the door's words. While the agent runs, a fresh
  connection reads the take's receipt every `check_every`; a refusal stops the agent and keeps its
  work, and an outage does not. The agent's own process can read the certificate, so that
  certificate's grant is the limit: never point `work` at your own set.

What it does not carry over from `tools/worker.py`: the disk floor, the pruning of kept clones,
the usage hold, pools and status items. One agent per repository holds on this machine only; the
layer has no shared claim on a repository to read.

`work run --once` exits **0** when handed back with evidence or stopped, **3** with nothing to take, and **6**
when the offer was taken and not handed back (released, or its report is still owed). Otherwise the
codes below hold.

## Commands

Commands other than `cert generate`, `onboard` and `join` reach the door with one actor's set: `--name NAME` for
`~/.ikigai/layers-client/NAME`, or `--cert-dir DIR`. `--door` defaults to
the enrolled set's saved door, otherwise
`quic://ikigai-rs.dev:4434`. `--json` prints the door's JSON answer as it came.

| command | what it does |
| --- | --- |
| `cert generate --name NAME` (or `--dir DIR`) | write `client.crt` and `client.key` and print the fingerprint; refuses to overwrite without `--force` |
| `onboard --door URL --pin SHA256 --invite ID --code CODE` | generate an identity, pin the lobby, request enrollment, poll approval, and save config; `--server-cert FILE`, `--name NAME`, `--once` |
| `join [HTTPS_HOST] [FEED …]` | join advertised public read-only feeds with no invite; `--name NAME` or `--cert-dir DIR` |
| `feeds [FEED] [--scheme]` | readable feeds with newest entries, or a feed's SKOS concept tree |
| `ask ENTRY [TEXT\|-] [--layer NAME]` | ask about a feed entry in your own writable non-feed channel |
| `me` | who the door says this actor is (`urn:layers:me`) |
| `say LAYER [TEXT\|-]` | say one `ik:Message` (stdin with `-` or no TEXT; a trailing newline is dropped); `--question` asks it; `--in-reply-to ENTRY`, `--supersedes ENTRY` |
| `post brief LAYER [FILE\|-]` | post an `ik:Brief`: `--title` (default: the first `# ` heading), `--repo owner/name` |
| `post issue LAYER [FILE\|-]` | post an `ik:Issue`: `--title`, `--repo`, `--severity critical\|major\|minor\|info` |
| `post file LAYER PATH` | post an `ik:File` (text or base64; digest and size); refused here when over the layer's bound |
| `save ENTRY [PATH]` | write a file entry's bytes to disk (default: your downloads directory) after checking its size and digest; `--as NAME` to save under another name; `--force` to replace an existing file |
| `read LAYER [--after N] [--kind K,…] [--concept IRI]` (alias `entries`) | the entries after sequence number N, every page: a header line each, then what its kind shows |
| `follow [--layers L,…] [--from-now] [--kind K,…] [--concept IRI]` | one line per event, for a Monitor, with each entry's kind and title (`--plain` without; `--once`, `--reset LAYER`, `--state FILE`, `--wait S`) |
| `tuples LAYER [--taken]` | a task layer's pending tuples (`seq created subject dropper`), or the receipts of takes |
| `out LAYER [FILE\|-]` | drop one tuple (Turtle or JSON-LD, its subject under the layer's IRI); prints the subject |
| `take LAYER SUBJECT` | take one tuple, atomically; a second taker is told who won |
| `work run\|plan` | take offers routed to this agent, run it, and report back ([above](#running-an-agent-on-offers-routed-to-it)); `--once`, `--config FILE` (default `worker.toml` in the set) |

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
| 1 | `denied: current entitlement grants no layer capabilities …` | ask the controller about revocation or a layer membership invite |
| 3 | `not enrolled yet: …` (`onboard`) | await controller approval, then rerun the same command to resume |
| 3 | `nothing to take` (`work run --once`) | nothing is routed here now; `work plan` says why |
| 6 | `released …`, `report owed …` (`work run --once`) | the offer was taken and not handed back; the lines before it say why |
| 4 | `refused: the door says this certificate is … and the configuration's actor is …` (`work`) | use the agent's own set, or correct `actor` |
| 4 | `refused: … is readable by others`, `refused: the loop does not run as root` (`work`) | fix the key's mode, or run as the agent's account |
| 2 | `no writable non-feed channel for this identity` (`ask`) | use an identity granted its own Talk channel; public feed joining grants reading only |
| 2 | `several writable channels: …; choose --layer LAYER` (`ask`) | select your destination channel explicitly |
| 2 | `… differs: kept it; use a separate --name for this door` (`join`) | keep this identity's pin and use another named set |
| 4 | `refused: server.crt does not match the descriptor sha256: nothing was sent` (`join`) | get a corrected discovery descriptor and server certificate from the operator |
| 2 | a usage problem, or a file in your set: `…/server.crt is missing: it should hold the door's certificate` | fix the named file or argument |
| 2 | `[agent] command passes {prompt} to sh, which would run an offer's text as shell commands` (`work`) | leave `{prompt}` out to get the prompt on stdin, or use `{prompt_file}` |
| 4 | `refused: … does not match --pin: nothing was sent` | get the door certificate and fingerprint from the operator again |
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
`tests/test_agent_loop.py` runs `work` with a fake layer, a fake `gh` and a fake agent, over real
git; `tests/test_interop.py` also runs it against the real door, whose `offers` fixture
has the offer and report shapes of ikigai-layers' own tests.
`test_work.py` posts every kind to a layer whose shapes and context
(`tests/fixtures/layers/layers/work.*`) are a copy of an edge layer's that takes briefs, issues
and files, with only the layer's name changed, so the door admits and refuses exactly as it does
for a collaborator.
CI's `interop` job builds ikigai-cli with its `quic` feature and runs it with
`--require-interop`, where a missing binary fails instead (a newer `ikigai` needs a `--layers-key`
that `tests/layer_door.py` does not pass yet, so those tests fail locally against it; use 0.1.62). `tests/test_onboard.py` uses an in-process fake lobby, real Python-generated certificates,
and the starter's shared proof/request fixture. CI runs the full gates on Linux. The enrollment implementation avoids subprocess and symlink
requirements for Windows and WSL2; native Windows execution has not been tested. The public-feed tests in `tests/test_feeds.py` extend the existing fake event door and
supply a fake HTTPS descriptor, including a mismatched certificate SHA-256. They cover
identity reuse, selected and all-feed joins, filtering with descendants, newest-entry
pagination, scheme trees, and question routing/refusals. Feed/join interoperability
against a real host has not been tested; the companion host must implement the wire
contract above. No test connects anywhere but loopback.

## Accessibility

[ACCESSIBILITY.md](https://github.com/ikigai-rs/ikigai-layers-client/blob/main/ACCESSIBILITY.md)
says what the `ikigai-layers` command prints that people read (answers, entries, `follow`'s
event lines, refusals), the rules that text follows, what is tested, the known limitations, and
how to report a barrier.

## License

Licensed under either of MIT or Apache-2.0, at your option.
