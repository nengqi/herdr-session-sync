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

The `attach <id>` in argv goes stale too: from the agents list (e.g. after `claude respawn`
kicks the viewer back to it) the same process can open another session, and its
`claude agents` child leaves no `sessions/<pid>.json`. So when the live screen title differs
from the argv session's name and exactly one live background session claims it, that session
wins. Only for `attach` (a plain `--resume` cannot switch), and pass the screen title only —
never the pane label, which may echo an earlier bind.

## Herdr's Claude hook stays Herdr's
`~/.claude/hooks/herdr-agent-state.sh` is Herdr's file (`herdr integration status` reads
`current`), so Herdr keeps it up to date. herdrdev/herdr#5261 (a background session inherits
the daemon's `HERDR_PANE_ID` and binds an unrelated pane) is covered by one line laid over
it: `[ -z "${CLAUDE_JOB_DIR:-}" ] || exit 0` after the shipped `HERDR_PANE_ID` check.
`ensure_hook_guard` adds it at startup and on every event, skips a file that already checks
`CLAUDE_JOB_DIR`, and leaves the file alone if the anchor line moved. Do not fork the hook
again; `~/Desktop/now/herdr-upstream/run_20261014/repro.sh --overlay` checks the whole setup
in isolation (prints FIXED).

## Moshi pane bindings (workaround, remove when Moshi fixes it)
Moshi (moshi-hook 0.4.37) maps a Herdr pane to a session through
`~/Library/Application Support/Moshi/claude-sessions/<sid>.json`: a matching `herdrPane` + `herdrTabId`
scores 200, a matching `herdrTabId` alone scores 120. A viewer pane (`claude attach`) never
gets `herdrPane` from moshi-hook (its shell-PID lookup misses the daemon-run conversation),
so another active session in the tab wins it in Chat View, or the pane shows no session when
that winner is parked. `reconcile_moshi_claude_sessions` stamps every pane's session and
clears `herdr*` fields from sessions no pane holds.

- Writes keep the file's mode (Moshi uses 0600) and every other field; moshi-hook reads before it
  writes, so it keeps our fields.
- A session any pane holds is never cleared, even without a transcript yet (moshi-hook
  binds it at SessionStart). The transcript check only gates creating a file for it.
- Moshi has no public tracker; reported via in-app Support
  (draft: `~/Desktop/now/herdr-upstream/run_20261026/moshi-report.md`).
- Remove when a viewer pane resolves to its own session in Moshi with this code disabled.
  Check: for each pane in `pane.list`, run `HERDR_ENV=1 HERDR_PANE_ID=<id> HERDR_TAB_ID=<tab> moshi-hook context`
  and confirm the output names the pane's `agent_session`.

## 事故背景：标题狂跳与幽灵 session（2026-10-30 自 memory 迁入）
压缩自 memory `incidents.md` 的 9-02 与 9-17 两条。

- 9-02（v0.1.4）：后台子进程继承父窗格的 `HERDR_PANE_ID`，各自上报 `agent_session_id` 互相抢占，pane 标题狂跳。修法：以 PTY `terminal_title` 为准、不再用 `pane.report_metadata` 反写、hook 加前台 TTY 守卫。副作用到 9-17 才暴露：「占前台 TTY」不等于「在写 transcript」，守卫认下了不写盘的查看器进程。
- 9-17：重启后 9 个 pane 报 `No conversation found`。两层原因：transcript 懒写入（`SessionStart` 只分配 UUID，`.jsonl` 到第一条用户消息才落盘，全机 898 个 `session-env/` 里 569 个没有）；同一 pane 的前台查看器进程从不写盘，真身是另一 TTY 上的 `kind:bg` 进程。
- 修复 v0.1.12（hook 与 `sync.py` 对称）：`is_valid_cc_session()` 校验；查看器改报 `jobs/<id>/state.json` 的 `resumeSessionId`（日志 `REDIRECTED_TO_BG`）；拿不准不写（`SKIPPED_UNWRITTEN`）。
- 已排除的联结键：`parkedJobId` 会失效（job 目录常被删，是死指针）；viewer argv 无 `attach <id>`；两进程不同 TTY 无 socket 直连；`--spawned-by` 与每个活 bg session 共享同一 socket，无区分力。
- 首版修复引入误绑，被 codex 当天抓出：把「无 transcript」直接当「是查看器」，新开未输入的会话也走名字兜底（16 个 interactive 里 12 个没有 `parkedJobId`）。补了 `parkedJobId` 门禁；`_pid_alive` 换成 `_is_live_claude_process`。`procStart` 是 UTC，`ps lstart` 是本地时间，要解析成时刻再比。
- 名字兜底：用户拍板先保留现状，有自我确认回环（`sync_pane` 自己写 label）。发现某 pane resume 到错误会话，就删掉这条分支。
- 教训：先查谁在写盘再编机制，联结键先证明有区分力（memory `debug-evidence-discipline.md` #12）；改完让 codex 审一遍（/codex:rescue），或者开一个独立的 Opus 子 agent 审；两个真 bug 都是 codex 抓的。
