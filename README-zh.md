<div align="center">

# herdr-session-sync

**自动将 Claude Code、Codex 等 Agent 会话名称同步至 Herdr 窗格边框、PTY 窗口标题与移动端配套 App（Heeler）。**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Herdr Plugin](https://img.shields.io/badge/herdr--plugin-compatible-0D96F6)](https://herdr.dev/plugins)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)](#)

[English](./README.md) | 简体中文

</div>

---

## 为什么需要

在 [Herdr](https://herdr.dev) 中进行多 Agent 并发开发（如 4 列对称分屏、并发运行多个 Claude Code / Codex）时，默认的窗口标题与手机配套 App（如 [Heeler](https://testflight.apple.com/join/aXSxRn4r)）经常遇到以下问题：
1. **标题缺失与碎片词**：新开窗格未命名时，手机端卡片只显示琐碎的终端底栏提示词（如 `focus`、`bypass permissions`）。
2. **多端状态脱节**：在 Mac 电脑端修改了窗格名字，底层终端 PTY 与手机 Heeler 无法同步。
3. **对话冲刷（Title Thrashing）**：常规的 Prompt Hook 会在日常对话（如“好的”、“继续”）时不断覆盖原本清晰的业务标题。

`herdr-session-sync` 作为 Herdr 的原生插件，通过带外后台通道无感知提取真实的 Claude Code / Codex 会话名字，并全自动同步到：
- 🖥️ **电脑端 Herdr**：窗格边框与 Tab 标签
- 📱 **手机端 Heeler**：卡片第二行核心任务标题
- ⚡ **零终端污染**：全程通过 Herdr Unix Socket 与 PTY 通道通信，屏幕零字符泄漏。

---

## 核心架构与特性

```text
       Herdr 运行时事件触发（pane.agent_status_changed / pane.created）
                                 │
                                 ▼
                     Session-Sync 智能命名引擎
                                 │
         ┌───────────────────────┴────────────────────────┐
         ▼                                                ▼
 1. 实时 Session 管道映射                          2. 多层级智能提取 Session 名字
 实时接入 Claude Code 状态栏与 Hook 管道          ├─ 最高优先：权威 custom-title.json（/rename 秒级生效）
                                                  ├─ 次优先：转录本 customTitle / agentName
                                                  ├─ 智能解析：首轮真实意图与 Slash Command / Grok
                                                  └─ 兜底：当前 Git 仓库与业务目录名
                                 │
                                 ▼
                    【三端统一写入通道】
      ┌──────────────────────────┼──────────────────────────┐
      ▼                          ▼                          ▼
 [Herdr 窗格顶部边框]        [底层 PTY 窗口标题]        [手机 Heeler 卡片]
 (herdr pane.rename)       (安全 OSC 0;Title\007)      (副标题清晰显示任务名)
```

### 核心亮点

* 🎯 **`/rename` 秒级实时对齐**：直读 Claude Code 权威 `custom-title.json`，在任意窗格敲 `/rename` 毫秒级同步至所有终端与手机端。
* 🛡️ **PTY 终端防注入安全**：向 Slave PTY 写入转义序列前严格清洗 ANSI Escape 码与 C0/C1 控制字符，杜绝终端光标反射与字符污染。
* 🧠 **Skill 与 Multi-Agent 结构化解析**：自动解析 `<command-name>`/`<command-args>`（如 `/chat-catchup`）与 Grok 接管头（`【从 Grok 接管任务：...】`），彻底过滤内部 Harness 元数据噪声。
* ⚡ **高性能零全盘扫描**：全内存/有限文件范围检索，杜绝多窗格并发时的阻塞式磁盘全量扫描。
* 📱 **完美适配 iOS Heeler 伴侣**：配合 iPhone/iPad 端的 [Heeler](https://testflight.apple.com/join/aXSxRn4r) 原生卡片呈现精准的任务副标题。

---

## 一键安装

直接通过 Herdr 官方插件命令安装：

```bash
herdr plugin install nengqi/herdr-session-sync
```

或者本地开发链接：

```bash
git clone https://github.com/nengqi/herdr-session-sync.git
herdr plugin link ./herdr-session-sync
```

热重载 Herdr 配置：

```bash
herdr server reload-config
```

---

## 支持的手动指令

除了后台自动监听状态变更外，还支持以下手动操作：

* **全量同步所有窗格**：
  ```bash
  herdr plugin action invoke session-sync.sync-all
  ```
* **同步当前窗格**：
  ```bash
  herdr plugin action invoke session-sync.sync-now
  ```
* **查看映射与锁定状态**：
  ```bash
  herdr plugin action invoke session-sync.status
  ```

---

## 环境要求

* **Herdr** >= `0.7.5`
* **Python 3.8+**（仅需标准库，零第三方依赖）
* macOS 或 Linux

---

## 开源协议

[MIT](LICENSE) © [nengqi](https://github.com/nengqi)
