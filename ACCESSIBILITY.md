# Accessibility

What `ikigai-layers-client` emits that people read (the `ikigai-layers` command's terminal text:
its answers, its refusals and the `follow` event lines), the rules that output follows, how it is
tested, and how to report a barrier. It is not a conformance claim. Adapted from the
[ACCESSIBILITY.md template](https://github.com/mgifford/ACCESSIBILITY.md) (MIT).

| Field | Value |
|---|---|
| Target | WCAG 2.2 Level AA, for any page that presents this output; this repository writes terminal text only, no HTML or SVG |
| Status | Evaluation in progress. No conformance claim. |
| Owner | the maintainers of this repository |
| Report a barrier | [a GitHub issue](https://github.com/ikigai-rs/ikigai-layers-client/issues/new?labels=accessibility) with the `accessibility` label; anything security-sensitive through [private vulnerability reporting](https://github.com/ikigai-rs/ikigai-layers-client/security/advisories/new) |
| Last reviewed | 2026-11-12 |

## What it emits that people read

All of it is terminal text from `src/ikigai_layers_client/`:

- **Answers** (`cli.py`, on stdout): `cert generate` prints where it wrote the pair, the
  fingerprint and the next steps in sentences; `say`, `announce`, `post` and `out` print the new entry's IRI;
  `save` prints `saved <path> (<n> bytes, <digest>)`; `me` and `take` print indented JSON;
  `tuples` prints one line per tuple or receipt; `--json` generally prints the door's JSON
  as it came (entry listings elide file content).
- **Enrollment** (`onboard.py`, on stdout): `onboard` labels the fingerprint, request,
  approval status, retry delay in seconds, granted capability IRIs, and saved configuration.
  Pending approval exits 3 and says to run again; revocation is a named refusal on stderr.
- **Public feeds** (`join.py`, `feeds.py`): `join` labels the identity fingerprint, granted capability IRIs and the
  saved configuration; `feeds` labels each feed, indents its newest entry, and says when
  there are no feeds or entries. `feeds FEED --scheme` prints a labeled concept tree with
  full IRIs and cycles named in words. `ask` prints its posted question's IRI or a refusal
  naming the missing writable channel or required `--layer` selection.
- **Entries** (`show.py`): `read` prints a header line per entry (sequence number, time, author,
  kind, IRI), then what its kind needs indented four spaces: a message's text, a brief's or an
  issue's title, severity, repository and body, a file's name, size, media type and digest with
  the command that saves it, never its content.
- **Event lines** (`follow.py`): `follow` prints one line per event, the door's six fields
  (layer, sequence number, time, kind, IRI, author), then, when entry descriptions are
  requested, the entry's kind and the first words of a message (72 characters at most) or a title; its own diagnostics are lines starting
  `ikigai-layers follow:`, on stdout too, so a watcher of that stream sees them.
- **Client-handled refusals** (`cli.py`, on stderr): each starts `ikigai-layers: `; a usage
  problem says what to give instead (give the brief a `--title`, or start it with a `# Title` line), a trust
  refusal starts `refused:`, an unreachable door `unreachable:`, and a door's own error is its
  display form (`denied: …`, `conflict: …`). The exit code says the same thing as a number
  (0, 1, 2, 4, 5; `follow` adds 3; `work run --once` adds 3 and 6), and the README's table lists each with what to do.
- **The agent loop** (`work.py`, on stdout): `work run` prints one line per thing it does, each
  starting `ikigai-layers work:` (a take, a skip with its reason, the agent's start, a report owed,
  an outage), durations in words (`1 hour 1 minute`), and `--once` ends on a line saying how the
  offer ended. The stop file is named when the loop starts and again when it stops
  (`stopping: <path> exists (remove it to run again)`). `work plan` prints `would take <subject>`
  or `would skip <subject>: <reason>` per offer, and says when the stop file is there. Neither
  ever prints an offer's title or brief.
- **Help** (`--help`): argparse's usage text, with the descriptions and exit codes in words.

Not this repository's: the door's error sentences, which are printed as the door wrote them, and
the text inside an entry, which is whatever its author posted.

## The rules that output follows

- **No client-added color or cursor movement.** Client-authored output uses printed lines
  without redraws. Entry text and door messages are passed through; this is not a guarantee
  that their content contains no terminal control sequences. Reading order with assistive
  technology has not been evaluated.
- **Meaning is in words.** A refusal says what was refused and, where this side can know, what to
  do (`save it under another name with --as NAME`, `Resynchronize with --reset <layer>`); the
  exit code repeats it and is never the only signal.
- **An outage is said once.** `follow` and `work run` report an outage on one line, say nothing while they
  retry, and say one line when the door answers again. `work run` says a skip once per offer per
  reason, not at every poll.
- **No offer's text reaches the terminal from `work`.** It may hold control sequences, and it is
  someone else's words; subjects and reasons only.
- **No file content reaches the terminal.** `read` and `follow` show a file's name, size, type and
  digest; `read --json` replaces the content with a note naming the command that saves it.

## How it is tested

Every named check has a result: Pass, Fail or Blocked, with evidence. Pass covers only the
stated check, not overall accessibility or WCAG conformance. The automated checks below ran
on 2026-11-12 against main commit `16f66f1d3f205afec7081942ff53fb04265d3cc5`, before this
documentation change, using Python 3.13.18, pytest 9.2.0, Ruff 0.17.5 and
ikigai-cli 0.4.1 on macOS 27.2.
`PATH="$PWD/.interop-build/tools/bin:$PATH" scripts/gates.sh` runs
`.venv/bin/python -m ruff check .`,
`.venv/bin/python -m ruff format --check .` and `.venv/bin/python -m pytest -q`.
The pinned door was built by
`.venv/bin/python -m pytest -q --require-interop tests/test_interop.py tests/test_work.py`
(45 passed), which calls `scripts/install-interop-door.sh`. The test-file rows below refer
to the full gates invocation. Tests inspect captured strings and behavior; they do not establish how a terminal or assistive technology presents output.

| Check | Result | Evidence |
|---|---|---|
| Usage refusals and certificate instructions | Pass | `tests/test_cli.py` and `tests/test_posts.py`, `test_usage_is_refused_before_dialing`, check exit 2 and the `ikigai-layers: ` prefix; `tests/test_certs.py`, `test_the_command_prints_the_fingerprint_and_the_next_steps`, checks the fingerprint and next steps. No human comprehension evaluation. |
| Follow event lines, outage messages and named refusals | Pass | `tests/test_follow.py`, including `test_lines_are_the_text_face_and_the_cursor_follows_them`, `test_an_outage_is_reported_once_then_its_end` and `test_a_refusal_stops_with_4_and_names_it`. Uses a fake door; no real terminal or network evaluation. |
| Entry summaries, file-content elision and size refusal | Pass | `tests/test_posts.py`, `test_each_kind_shows_what_a_reader_needs`, `test_show_never_prints_a_files_content` and `test_a_file_over_the_bound_is_refused_naming_what_fits`; `tests/test_follow.py`, `test_describe_adds_the_kind_and_summary_never_the_content`. Checks fixtures, not arbitrary entry content. |
| Agent-loop refusals, outage/skip repetition, stop-file lines and exclusion of offer text | Pass | `tests/test_agent_loop.py`, including `test_a_refusal_keeps_its_first_words_and_its_code`, `test_a_bad_configuration_is_refused_by_name`, `test_the_loop_follows_the_feed_and_reports_an_outage_once`, `test_a_skip_is_said_once_not_at_every_poll`, `test_offer_text_never_reaches_this_commands_output` and `test_work_run_once_with_the_stop_file_exits_cleanly_and_takes_nothing`. Fake-door and subprocess checks, not assistive technology tests. |
| Enrollment status, pending approval, refusal prefix and exclusion of pairing codes/private keys | Pass | `tests/test_onboard.py`, including `test_generate_post_resume_and_configure_without_external_programs` and `test_current_revocation_never_writes_config`. Local lobby fixtures; no native Windows evaluation. |
| Feed labels, concept-tree indentation/cycles, join and question refusals, and exclusion of private keys | Pass | `tests/test_feeds.py`, including `test_feeds_list_only_feed_definitions_and_the_newest_entry`, `test_scheme_prints_concept_labels_and_hierarchy`, `test_scheme_cycles_terminate_and_are_named` and `test_join_creates_an_identity_pins_the_door_and_joins_all_public_feeds`. Fake door and descriptor; no real public service. |
| Announcement output and concept refusals before sending | Pass | `tests/test_feeds.py`, `test_announce_posts_a_titled_announcement_with_scheme_concepts` and `test_announce_refuses_unknown_concepts_without_sending`. Captured output and fake-door calls only. |
| Lint, formatting and local regression suite | Pass | `scripts/gates.sh`, with the tool versions above. 402 passed, 107 subtests passed, no skipped tests. Code hygiene and regression checks, not an automated accessibility checker. |
| Real-door output and named access refusals | Pass | `tests/test_interop.py` and `tests/test_work.py` with ikigai-cli 0.4.1, both in the required interop command above and the full gates. Includes `test_an_unenrolled_certificate_is_refused_naming_its_fingerprint`, public-feed reading through loopback HTTPS discovery, and entry/file output. Uses a scratch loopback door, not a production service or assistive technology. CI repeats these files in its `interop` job with `--require-interop`. |
| Native Windows terminal output | Blocked | No Windows terminal session was available; local checks ran on macOS, and CI uses Linux. |
| Browser presentation of output and documentation | Blocked | No browser evaluation was performed. The command emits terminal text, not HTML or SVG; rendering by GitHub or another consumer has not been checked. |
| Automated accessibility checker | Blocked | No axe or other accessibility checker was configured or run on the output or rendered documentation. Pytest string assertions are not such a checker. |
| Manual keyboard use | Blocked | No person performed a keyboard pass in a terminal for this evaluation; command tests do not evaluate terminal navigation. |
| Screen reader | Blocked | No screen reader was used on this output; no screen reader session was available. |
| Screen magnifier | Blocked | No magnifier was used on this output; no magnifier session was available. |
| Speech input | Blocked | No speech-input software was used on this output; no speech-input session was available. |
| Switch access | Blocked | No switch-access software was used on this output; no switch-access session was available. |
| Braille display | Blocked | No braille display was used on this output; no braille display was available. |

No historical assistive technology or browser run is claimed as a Pass. Any historical run
that CI does not repeat must be recorded as Blocked with its date and missing evidence.

## Known limitations

Found by source inspection on 2026-10-31 and rechecked on 2026-11-12 in
`show.py`, `follow.py` and `cli.py`, not by testing with people or assistive technology:

- The fields of a `read` header line, a `follow` event line and a `tuples` line are separated by
  spaces with no labels, so their meaning depends on their position; read aloud, a line is a run
  of numbers, a timestamp and IRIs.
- Times are printed as the door sends them, ISO 8601 in UTC, not in the reader's time zone or in
  words.
- `follow` cuts a message to 72 characters and a file's digest to 19, each ending in `…`; the
  whole of either is only in `read`.
- `me` and `take` print JSON, with no plain-text form.

## Contributing

Read this file before changing any output listed above: an answer, an entry's lines, an event
line, a refusal or the help. Keep meaning in words and never in color, keep a refusal's first
words (`ikigai-layers:`, `refused:`, `unreachable:`) and its exit code, and update the README's
refusal table with them. In the pull request, give every check a `Check | Result | Evidence`
table row: Pass, Fail or Blocked, with the test or command and tool version, or why it could not run. Include unrun
assistive technology checks as Blocked. Date historical runs that CI does not repeat and mark
them Blocked, not Pass.

## Reporting a barrier

[Open an issue](https://github.com/ikigai-rs/ikigai-layers-client/issues/new?labels=accessibility)
with the `accessibility` label (the link sets it when GitHub lets you add labels; otherwise a
maintainer adds it): what you were doing, what happened, and your terminal and any assistive
technology, if you are comfortable sharing them. Anything security-sensitive goes through
[private vulnerability reporting](https://github.com/ikigai-rs/ikigai-layers-client/security/advisories/new).
**We acknowledge a report within 7 days**, and promise nothing further.

## Change history

| Date | Change |
|---|---|
| 2026-10-31 | First version. |
| 2026-11-02 | Added lobby enrollment status output and its tests. |
| 2026-11-03 | Added the `work` agent loop's lines, refusals and tests, with its stop file's lines and its refusal of a prompt passed to a shell. |
| 2026-11-08 | Added public feed joining, listings, scheme trees and question routing, with fake-door output and refusal tests. |
| 2026-11-12 | Recorded current automated checks and unrun evaluations in a Pass / Fail / Blocked evidence table; clarified the limits of terminal-output checks. |
