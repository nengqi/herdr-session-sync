# herdr-session-sync

Auto-sync Claude Code, Codex & Agent session names across Herdr pane labels, PTY window titles, and mobile companion apps (Heeler).

## Rules
- Pure Python standard library (no external npm/pip dependencies).
- Herdr plugin manifest at `herdr-plugin.toml`.
- Run tests: `python3 -m unittest discover -s tests/`
- Herdr links this repo directly (`herdr plugin list` shows `local:<this repo>`), so a saved
  `src/sync.py` is live on the next event. The manifest is cached in
  `~/.config/herdr/plugins.json` at link time: after editing `herdr-plugin.toml`, bump the
  version and run `herdr plugin link <this repo> --enabled`. The old copy under
  `~/.config/herdr/local-plugins/session-sync/` was never what ran and has been removed.

## Restart reattach for viewer panes
A viewer pane is bound to the background session it displays. On restart Herdr types
`claude --resume <that id>` into the pane's shell. The user's zsh launcher (`ca` in
`~/.zshrc`, the check lives inside the function body) rewrites that form (and `-a`/`attach` with a full or 8-character id) to `claude attach <job>`
whenever `${CLAUDE_CONFIG_DIR:-~/.claude}/jobs/<job>/state.json` names it, so both a running and an exited background
session reopen in place; a plain resume of an exited one would later `/bg` into a
"name (2)" copy. Where that launcher is not loaded, Claude refuses the resume of a running
session and prints `Run \`claude attach <job>\``, and the `--reattach` startup hook runs it.

Do not move this to a custom `resume_argv`: Herdr only accepts one from a source holding the
pane through `pane.report_agent`, and while held it stops reading the screen, so the pane's
status freezes (checked in a named test session, Herdr 0.9.4). More Herdr behaviors the
plugin depends on: a reference switches for a live occupant only when the report carries
`session_start_source` (otherwise it is acknowledged and ignored) — `startup` never replaces
an existing reference, `resume` does; every report needs a `seq` above the last one accepted
from that source (use `time.time_ns()`, millisecond values are silently dropped); Herdr
ignores `pane.release_agent` from official sources, and none of release / clear_agent_authority /
an empty report cleared a reference on a pane sitting at a shell; and Herdr drops the reference seconds after a refused resume is
back at the shell.

## Never persist a session Herdr cannot resume
Herdr stores a pane's `agent_session_id` in `~/.config/herdr/session.json` and reruns
`claude --resume <id>` from it after a restart. A UUID with no transcript on disk kills that
pane with `No conversation found with session ID`, so every path that reports a session must
pass `is_valid_cc_session()` first. Two situations produce one:

- Claude allocates the UUID at `SessionStart` but writes `~/.claude/projects/*/<id>.jsonl`
  only on the first user message. Skip it; `UserPromptSubmit` reports it once it is durable.
- A pane's foreground `interactive` process can be a viewer that owns the TTY and never
  writes a transcript, while a `kind: bg` process on another TTY holds the real conversation.
  Report the background one (`jobs/<id>/state.json` → `resumeSessionId`).

The two are indistinguishable from a missing transcript alone, so confirm the viewer from
`~/.claude/sessions/<pid>.json` first: a non-empty `parkedJobId` is the marker. Most live
interactive sessions do not have one, and redirecting without that check binds a brand-new
pane to whatever background session happens to share its title.

Do not use a signal-0 probe to decide a background session is alive: session JSON outlives
its process, so a recycled pid resurrects a dead session. Match the command and `procStart`
too — and note `procStart` is UTC while `ps -o lstart=` is local time, so compare instants,
not strings.

Binding the wrong session is worse than binding none: a failed resume is something the user
fixes by hand, a wrong bind silently attaches a pane to someone else's conversation. When
unsure, report nothing.

`resolve_live_bg_session_by_name` is the one weak link — see its docstring for why the name
is a guess and which stronger keys were already ruled out.

## Herdr's Claude hook stays Herdr's
`~/.claude/hooks/herdr-agent-state.sh` is Herdr's file (`herdr integration status` reads
`current`), so Herdr keeps it up to date. herdrdev/herdr#5261 (a background session inherits
the daemon's `HERDR_PANE_ID` and binds an unrelated pane) is covered by one line laid over
it: `[ -z "${CLAUDE_JOB_DIR:-}" ] || exit 0` after the shipped `HERDR_PANE_ID` check.
`ensure_hook_guard` adds it at startup and on every event, skips a file that already checks
`CLAUDE_JOB_DIR`, and leaves the file alone if the anchor line moved. Do not fork the hook
again; `~/Desktop/now/herdr-upstream/run_20261014/repro.sh --overlay` checks the whole setup
in isolation (prints FIXED).
