# herdr-session-sync

Auto-sync Claude Code, Codex & Agent session names across Herdr pane labels, PTY window titles, and mobile companion apps (Heeler).

## Rules
- Pure Python standard library (no external npm/pip dependencies).
- Herdr plugin manifest at `herdr-plugin.toml`.
- Run tests: `python3 -m unittest discover -s tests/`
- This repo is not what Herdr runs. After editing `src/sync.py`, copy it to
  `~/.config/herdr/local-plugins/session-sync/src/sync.py`, bump the manifest version, and
  `herdr server reload-config` — the two are separate copies, not a symlink.

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

Binding the wrong session is worse than binding none: a failed resume is something the user
fixes by hand, a wrong bind silently attaches a pane to someone else's conversation. When
unsure, report nothing.

`resolve_live_bg_session_by_name` is the one weak link — see its docstring for why the name
is a guess and which stronger keys were already ruled out.
