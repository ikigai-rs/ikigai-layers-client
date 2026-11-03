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
| Last reviewed | 2026-10-31 |

## What it emits that people read

All of it is terminal text from `src/ikigai_layers_client/`:

- **Answers** (`cli.py`, on stdout): `cert generate` prints where it wrote the pair, the
  fingerprint and the next steps in sentences; `say`, `post` and `out` print the new entry's IRI;
  `save` prints `saved <path> (<n> bytes, <digest>)`; `me` and `take` print indented JSON;
  `tuples` prints one line per tuple or receipt; `--json` prints the door's JSON as it came.
- **Enrollment** (`onboard.py`, on stdout): `onboard` labels the fingerprint, request,
  approval status, retry delay in seconds, granted capability IRIs, and saved configuration.
  Pending approval exits 3 and says to run again; revocation is a named refusal on stderr.
- **Entries** (`show.py`): `read` prints a header line per entry (sequence number, time, author,
  kind, IRI), then what its kind needs indented four spaces: a message's text, a brief's or an
  issue's title, severity, repository and body, a file's name, size, media type and digest with
  the command that saves it, never its content.
- **Event lines** (`follow.py`): `follow` prints one line per event, the door's six fields
  (layer, sequence number, time, kind, IRI, author), then the entry's kind and the first words
  of a message (72 characters at most) or a title; its own diagnostics are lines starting
  `ikigai-layers follow:`, on stdout too, so a watcher of that stream sees them.
- **Refusals** (`cli.py`, on stderr): every one starts `ikigai-layers: `; a usage problem says
  what to give instead (give the brief a `--title`, or start it with a `# Title` line), a trust
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

- **No color, no cursor movement.** Every line is a plain `print`; nothing redraws, so a screen
  reader or braille display reads the output as ordinary lines.
- **Meaning is in words.** A refusal says what was refused and, where this side can know, what to
  do (`save it under another name with --as NAME`, `Resynchronize with --reset <layer>`); the
  exit code repeats it and is never the only signal.
- **An outage is said once.** `follow` and `work run` report an outage on one line, says nothing while it
  retries, and says one line when the door answers again. `work run` says a skip once per offer per
  reason, not at every poll.
- **No offer's text reaches the terminal from `work`.** It may hold control sequences, and it is
  someone else's words; subjects and reasons only.
- **No file content reaches the terminal.** `read` and `follow` show a file's name, size, type and
  digest; `read --json` replaces the content with a note naming the command that saves it.

## How it is tested

- `tests/test_cli.py` and `tests/test_posts.py` (`test_usage_is_refused_before_dialing`) pin that a
  usage problem exits 2 with a line starting `ikigai-layers: `; `tests/test_certs.py`
  (`test_the_command_prints_the_fingerprint_and_the_next_steps`) pins the fingerprint line and the
  next steps.
- `tests/test_follow.py` pins the event lines (`test_lines_are_the_text_face_and_the_cursor_follows_them`),
  one line per outage and one when it ends (`test_an_outage_is_reported_once_then_its_end`), that a
  refusal names itself (`test_a_refusal_stops_with_4_and_names_it`), and that a description never
  carries a file's content (`test_describe_adds_the_kind_and_summary_never_the_content`).
- `tests/test_posts.py` pins what each kind shows (`test_each_kind_shows_what_a_reader_needs`,
  `test_show_never_prints_a_files_content`) and a refusal that names what fits
  (`test_a_file_over_the_bound_is_refused_naming_what_fits`).
- `tests/test_interop.py` and `tests/test_work.py`, against a real door on loopback (CI's `interop`
  job), pin refusals by name, among them `test_an_unenrolled_certificate_is_refused_naming_its_fingerprint`.
- `tests/test_agent_loop.py` pins `work`'s refusals by name and exit code
  (`test_a_refusal_keeps_its_first_words_and_its_code`, `test_a_bad_configuration_is_refused_by_name`),
  one line per outage (`test_the_loop_follows_the_feed_and_reports_an_outage_once`) and per skip
  (`test_a_skip_is_said_once_not_at_every_poll`), and that an offer's text and its escape sequences
  stay out of the output (`test_offer_text_never_reaches_this_commands_output`), and the stop
  file's lines (`test_work_run_once_with_the_stop_file_exits_cleanly_and_takes_nothing`).
- `tests/test_onboard.py` checks that pending approval and enrollment are named, local
  refusals keep the `ikigai-layers: ` prefix, and pairing codes and private keys stay out of
  output. These tests run on Linux in CI; native Windows output has not been evaluated.
- **No automated accessibility checker, browser or assistive technology has been used on this
  output.** No screen reader or braille display has read the command's output.

## Known limitations

Found by reading the source on 2026-10-31, not by testing with people or assistive technology:

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
refusal table with them. Say in the pull request what was tested, with what, and what was not.

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
