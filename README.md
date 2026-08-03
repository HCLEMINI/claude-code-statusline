# claude-code-statusline

为 **Claude Code** 定制的中文 LLM 状态栏 + 通知 hooks + 主题自愈工具包。专为通过 Anthropic 兼容代理接入的国产大模型（GLM / Kimi / DeepSeek）设计。配色统一 **One Dark Modern**。

## 功能概览

| 组件 | 文件 | 作用 |
|------|------|------|
| 状态栏 | `statusline.py` | 底栏 3 行：模型 / 上下文 / 花费 / provider 额度，One Dark truecolor 配色 |
| 统一通知 | `hooks/notify.py` | 回答完成 + 人工决策，**飞书 + Windows 系统通知双通道** |
| 主题自愈 | `hooks/theme_guard.py` | 每次启动检测并恢复 One Dark 主题（防被改回纯黑/内置主题） |
| 主题文件 | `themes/one-dark-modern.json` | Claude Code 自定义前景主题（复制到 `~/.claude/themes/`） |

---

## 状态栏（statusline.py）

三行显示：

```
GLM-5.2 | 上下文 8% (剩余92%) | ↑122.2K ↓1.7K/1.0M | ⚡max
花费¥197.52(含agent¥8.56) | 输入192.32M/输出0.81M | 缓存命中95% | 5h已用61%(2.4h)
全部session累计: ¥27358.46
```

### One Dark Modern 配色（24-bit truecolor）

与 `themes/one-dark-modern.json` 一致，底栏与 Claude Code UI 读作同一套主题：

| 元素 | 颜色 | 色值 |
|------|------|------|
| 模型名 | 蓝 | `#61afef` |
| 上下文占用% | 绿 `<50%` / 黄 `50–80%` / **红 `≥80%`** | `#98c379` / `#e5c07b` / `#e06c75` |
| `↑↓` 当前 token | 青 | `#56b6c2` |
| 花费（含 agent） | 黄 | `#e5c07b` |
| 输入 / 输出 M | 青 | `#56b6c2` |
| 缓存命中% | 紫 | `#c678dd` |
| 5h额度 / provider余额 / 全部累计 | 青 | `#56b6c2` |

> 需要 24-bit truecolor 终端（Windows Terminal / 现代终端均支持）。`STATUSLINE_NOCOLOR=1` 可关闭配色（管道输出时）。

### 按 provider 自动切换第2行末尾

| 会话模型 | 末尾显示 | 数据源 |
|---------|---------|--------|
| GLM (`glm-*`) | `5h已用30%(2.1h)` | 智谱 5h 额度 API |
| Kimi (`kimi-*`) | `余额¥1234.56` | Moonshot 余额 API |
| DeepSeek (`deepseek-*`) | `余额¥567.89` | DeepSeek 余额 API |

### 核心设计

1. **费用/缓存命中率**：直接解析 transcript JSONL（主链 + `subagents/`），按每条记录的 `model` 字段查单价。不依赖 `current_usage`（后者只存最近一次调用，会漏记绝大部分）。
2. **增量解析**：transcript 只增不重写，故按文件大小缓存 + 只读新增字节（O(新数据) 而非 O(全文件)），每 40 次增量强制全量重算兜底；uuid 去重防同一响应重复计数。解析耗时随会话增长不再恶化。
3. **健壮性**：`errors='replace'` 防非法 UTF-8 字节中断解析（否则 offset 不推进会重复计数）；缓存损坏自动回退全量重算，底栏不崩。
4. **瞬态防抖**：流式生成中 `used_percentage` 会闪到 0/null，用 state 文件缓存上一次有效值回退。
5. **余额/额度后台异步刷新**：主进程只读缓存（瞬时），缓存过期时 fire-and-forget 拉起 detached 后台进程刷新，底栏从不阻塞。默认 1 分钟 TTL，30 秒防重复拉取。
6. **上下文窗口**：模型名加 `[1m]` 后缀（小写）才认 1M 上下文，否则 Claude Code 默认 200K。

### 内置定价表（元/百万Token · 人民币）

编辑 `statusline.py` 顶部 `_PRICING_DATA` 增改：

| 模型 | 缓存命中 | 普通输入 | 输出 |
|------|---------|---------|------|
| GLM-5.1 / 5.2 | 1.30 | 6.00 | 24.00 |
| DeepSeek V4 Pro | 0.025 | 3.00 | 6.00 |
| DeepSeek V4 Flash | 0.02 | 1.00 | 2.00 |
| Kimi 2.6 | 1.10 | 6.50 | 27.00 |
| Kimi K3 | 2.00 | 20.00 | 100.00 |

---

## 通知 hooks

### `hooks/notify.py`（统一通知 · 飞书 + Windows 系统通知）

合并了原 `feishu_notify.py` + `done_notify.py`，按 `hook_event_name` 分发：

| 触发事件 | 文案 | 说明 |
|---------|------|------|
| `Stop` | `✅ 回答完成` | 一轮答完、等下一个问题时 |
| `PreToolUse(AskUserQuestion\|ExitPlanMode)` | `🔔 在等你做选择/确认计划` | 带问题原文 |
| `PermissionRequest` | `🔔 需要你审批` | 带命令/文件路径 |

**双通道**：每条通知同时发飞书机器人 + Windows 系统通知（toast）。

- **飞书**：webhook 从 env `FEISHU_WEBHOOK_URL` 读，回退 `~/.claude/.feishu_webhook` 文件（单行 URL）。网络抖动静默跳过，绝不阻塞工具调用。
- **Windows toast**：`win11toast`（封装 WinRT `Windows.UI.Notifications`，**不是**当年不可靠的 PowerShell NotifyIcon）在 **detached 子进程**里弹（`win11toast.toast()` 本身会阻塞等 dismissal，故分离子进程，hook 立即返回）；`win11toast` 不可用时 PowerShell WinRT toast 兜底。建议 `pip install win11toast`。

**Stop 过滤**（避免噪音）：

| 情况 | 行为 |
|------|------|
| `stop_hook_active=true`（hook 递归） | 不通知 |
| transcript 末条对话非 `assistant`（compact / `/clear` / resume） | 不通知 |
| **阶段性停止**：后台任务 / 定时任务 / 未返回的 agent 仍在跑 | 不通知 |

阶段判据：`background_tasks` / `session_crons` 字段（CC ≥2.1.x，非空=阶段停止），transcript 找 pending `Agent`/`Task`/`Workflow` 作兜底。

**调用日志**：每次被调写一行到 `~/.claude/notify_call.log`（`event=... sent/skip...`），用于确认 hook 是否被 Claude Code 真正触发（排查"改配置后旧 session 仍指向已删脚本"类问题）。

### `hooks/theme_guard.py`（SessionStart 主题自愈）

挂 `SessionStart`，每次启动检测三处，被改回时自动恢复 One Dark Modern：

| 检测项 | 偏离时动作 |
|--------|-----------|
| `settings.json` 的 `theme` 字段 | 非 `custom:one-dark-modern` → 改回 |
| 主题文件 `~/.claude/themes/one-dark-modern.json` | 丢失 → 按标准色板重建 |
| Windows Terminal 底色 | 纯黑/被删/非护眼色 → 改回 `#282c34` |

WT 底色有**护眼色阶白名单**（`#282c34` / `#1a1b26` / `#1e2127` / `#2c313a` / `#30323d` / `#1e1e2e` / `#2e3440` / `#282828`），白名单内尊重用户切换，纯黑等才改回。正确状态静默；修正时 stderr 提示。token/env/hooks/WT 其余字段完整无损（原子写）。

> ⚠️ 非显然行为：日后手动把 theme 改成 `dark`、或 WT 底色改纯黑，下次启动会被 theme_guard 自动改回——这是防回退设计，不是 bug。要永久换主题需先禁用此 hook。

---

## 安装

1. **复制文件**到 `~/.claude/`：
   ```
   statusline.py                       →  ~/.claude/statusline.py
   hooks/notify.py                     →  ~/.claude/hooks/notify.py
   hooks/theme_guard.py                →  ~/.claude/hooks/theme_guard.py
   themes/one-dark-modern.json         →  ~/.claude/themes/one-dark-modern.json
   ```

2. **配置飞书 webhook**（二选一，notify.py 优先 env 回退文件）：
   - env：`~/.claude/settings.json` 的 `env` 块加 `"FEISHU_WEBHOOK_URL": "https://open.feishu.cn/open-apis/bot/v2/hook/你的id"`
   - 文件：`~/.claude/.feishu_webhook` 写入单行 webhook URL（不入库，适合不想放 settings.json 的场景）

3. **（可选）Windows 系统通知**：`pip install win11toast`。不装则飞书仍正常，toast 退回 PowerShell 兜底或跳过。

4. **接线 statusLine + theme + hooks**：参考 `settings.example.json`，把对应字段合并进 `~/.claude/settings.json`（路径替换成自己的安装位置）。

5. **首次用自定义主题需重启一次 Claude Code** 才会加载 `~/.claude/themes/`；之后改 settings.json 也需重启或 `/hooks` 重载（hook 配置在 session 启动时加载，不热重载）。

---

## Token / 余额从哪读

`statusline.py` 的余额刷新和 hooks 都从 **当前工作目录的 `.claude/settings.json`** 读取 `ANTHROPIC_AUTH_TOKEN`（找不到再回退全局 `~/.claude/settings.json`），按 `ANTHROPIC_BASE_URL` 判断 provider。所以每个项目（如 `AI-GLM/`、`AI-KIMI/`、`AI-DeepSeek/）各自配各自的 key，状态栏自动用对应的。

---

## 安全

- **所有密钥/webhook 走环境变量或本地文件，本仓库不含任何私密信息。**
- `.gitignore` 已屏蔽运行时缓存、debug log、`.feishu_webhook`、本地配置（`*.local.json` / `settings.local.json` / `.env`）。
- 每次提交前扫描 webhook / API token / 用户路径，确认干净。

---

## 运行时产物（不入库）

| 文件 | 内容 |
|------|------|
| `~/.claude/statusline_state.json` | transcript 文件大小缓存 + 增量 offset + 上下文防抖值（按 session_id 隔离） |
| `~/.claude/quota_5h_cache.json` | GLM 5h 额度缓存 |
| `~/.claude/kimi_balance_cache.json` | Kimi 余额缓存 |
| `~/.claude/ds_balance_cache.json` | DeepSeek 余额缓存 |
| `~/.claude/all_sessions_cache.json` | 全部 session 累计花费缓存 |
| `~/.claude/statusline_debug.log` | statusline 异常调试日志 |
| `~/.claude/notify_call.log` | notify.py 每次调用记录（诊断 hook 是否触发） |
| `~/.claude/.feishu_webhook` | 飞书 webhook（若选文件方式存储） |

---

## License

MIT
