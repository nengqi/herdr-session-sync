<div align="center">

# herdr-session-sync

**Auto-sync Claude Code, Codex & Agent session names across Herdr pane labels, PTY window titles, and mobile companion apps (Heeler).**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Herdr Plugin](https://img.shields.io/badge/herdr--plugin-compatible-0D96F6)](https://herdr.dev/plugins)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)](#)

English | [简体中文](./README-zh.md)

</div>

---

## Why

When running multi-agent workflows in [Herdr](https://herdr.dev) (e.g. 4-column splits with multiple Claude Code or Codex sessions), default window titles and mobile companion apps like [Heeler](https://testflight.apple.com/join/aXSxRn4r) often suffer from:
1. **Empty / Fragmented Titles**: Newly created panes show generic fallback prompts or empty titles on mobile cards.
2. **Title Desynchronization**: Renaming a pane on desktop Herdr does not update the underlying PTY xterm title or mobile companion views.
3. **Title Thrashing**: Naive hooks overwrite titles on every single conversational turn (like "ok", "continue").

`herdr-session-sync` runs out-of-band as a native Herdr plugin to continuously extract real Claude Code / Codex session names and synchronize them seamlessly across:
- 🖥️ **Desktop Herdr**: Pane borders & tab headers
- 📱 **Mobile Heeler**: Native subheadline task titles
- ⚡ **Zero Terminal Pollution**: All operations happen via Unix Socket RPC and slave PTY signals with zero stdout/stderr noise.

---

## Architecture & Features

```text
       Herdr Lifecycle Events (pane.agent_status_changed / pane.created)
                                 │
                                 ▼
                     herdr-session-sync Engine
                                 │
         ┌───────────────────────┴────────────────────────┐
         ▼                                                ▼
 1. Real-time Session Mapping                      2. Multi-tier Smart Extraction
 Sourced from live Claude statusline & hooks      ├─ Top: Authoritative custom-title.json (/rename)
                                                  ├─ Fast: Transcript customTitle / agentName
                                                  ├─ Smart: Cleaned first-turn intent & Slash Commands
                                                  └─ Fallback: Active Git workspace/repo
                                 │
                                 ▼
                    【Tri-Sync Pipeline】
      ┌──────────────────────────┼──────────────────────────┐
      ▼                          ▼                          ▼
 [Herdr Pane Border]        [PTY Window Title]         [Heeler iOS Card]
 (herdr pane.rename)       (Safe OSC 0;Title\007)      (Clean task subtitle)
```

### Key Highlights

* 🎯 **Instant `/rename` Synchronization**: Authoritative `custom-title.json` inspection detects `/rename` commands instantly across panes.
* 🛡️ **PTY Escape Injection Protection**: Sanitizes all non-printable C0/C1 control characters and ANSI escape codes before writing to slave TTYs.
* 🧠 **Skill & Multi-agent Parsing**: Intelligently cleans XML command tags (`<command-name>`, `<command-args>`), Grok handoff headers (`【从 Grok 接管任务：...】`), and filters out internal harness metadata.
* ⚡ **Bounded Fast Lookups**: High-performance metadata caching with zero unbounded disk scans or polling loops.
* 📱 **Heeler iOS Companion Ready**: Works out-of-the-box with [Heeler](https://testflight.apple.com/join/aXSxRn4r) on iPhone/iPad over direct SSH.

---

## Installation

Install directly into Herdr via the official plugin manager:

```bash
herdr plugin install nengqi/herdr-session-sync
```

Or clone and link locally:

```bash
git clone https://github.com/nengqi/herdr-session-sync.git
herdr plugin link ./herdr-session-sync
```

Reload Herdr configuration:

```bash
herdr server reload-config
```

---

## Actions & CLI Commands

Manual actions available in Herdr:

* **Sync All Panes**:
  ```bash
  herdr plugin action invoke session-sync.sync-all
  ```
* **Sync Current Pane**:
  ```bash
  herdr plugin action invoke session-sync.sync-now
  ```
* **Inspect Status**:
  ```bash
  herdr plugin action invoke session-sync.status
  ```

---

## Requirements

* **Herdr** >= `0.7.5`
* **Python 3.8+** (Standard library only; zero external packages)
* macOS or Linux

---

## License

[MIT](LICENSE) © [nengqi](https://github.com/nengqi)
