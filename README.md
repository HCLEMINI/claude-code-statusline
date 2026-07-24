# claude-code-statusline

为 **Claude Code** 定制的中文 LLM 状态栏 + 飞书通知 hooks 工具包。专为通过 Anthropic 兼容代理接入的国产大模型（GLM / Kimi / DeepSeek）设计。

## 状态栏（statusline.py）两行显示

```
GLM-5.2 | 上下文 12% (剩余88%) | ↑122.2K ↓1.7K/1.0M | ⚡max
花费¥12.34(含agent¥5.67) | 缓存命中90% | 5h已用30%(2.1h)
```

| 字段 | 说明 |
|------|------|
| 模型 · 上下文占用% · 当前 token · 难度 | 实时状态（第1行） |
| 花费(含agent) · 缓存命中% · provider 余额 | 会话累计（第2行） |

**按 provider 自动切换第2行末尾**：

| 会话模型 | 末尾显示 | 数据源 |
|---------|---------|--------|
| GLM (`glm-*`) | `5h已用30%(2.1h)` | 智谱 5h 额度 API |
| Kimi (`kimi-*`) | `余额¥1234.56` | Moonshot 余额 API |
| DeepSeek (`deepseek-*`) | `余额¥567.89` | DeepSeek 余额 API |

### 核心设计

1. **费用/缓存命中率**：直接解析 transcript JSONL（主链 + `subagents/`），不依赖 `current_usage`（后者只存最近一次调用，会漏记绝大部分）。按每条记录的 `model` 字段查单价。
2. **瞬态防抖**：流式生成中 `used_percentage` 会闪到 0/null，用 state 文件缓存上一次有效值回退。
3. **余额/额度后台异步刷新**：主进程只读缓存（瞬时），缓存过期时 fire-and-forget 拉起 detached 后台进程刷新，底栏从不阻塞。默认 1 分钟 TTL，30 秒防重复拉取。
4. **上下文窗口**：模型名加 `[1m]` 后缀（小写）才认 1M 上下文，否则 Claude Code 默认 200K。

### 内置定价表（元/百万Token · 人民币）

编辑 `statusline.py` 顶部 `_PRICING_DATA` 增改：

| 模型 | 缓存命中 | 普通输入 | 输出 |
|------|---------|---------|------|
| GLM-5.1 / 5.2 | 1.30 | 6.00 | 24.00 |
| DeepSeek V4 Pro | 0.025 | 3.00 | 6.00 |
| DeepSeek V4 Flash | 0.02 | 1.00 | 2.00 |
| Kimi 2.6 | 1.10 | 6.50 | 27.00 |
| Kimi K3 | 2.00 | 20.00 | 100.00 |

## 通知 hooks（hooks/）

飞书机器人推送，让你离开终端时也能被叫回。

| 文件 | 触发事件 | 作用 |
|------|---------|------|
| `feishu_notify.py` | `PermissionRequest` / `PreToolUse(AskUserQuestion, ExitPlanMode)` | Claude 需要你审批/做选择时推送 |
| `done_notify.py` | `Stop` | 回答完成推送；**区分「阶段性停止」（后台仍有 agent/任务在跑）vs「完全结束」** |

`done_notify.py` 利用新版 Claude Code 的 `background_tasks` 字段判断停止类型：
- 完全结束 → `✅ 回答完成，等待你的下一个问题`
- 阶段性停止 → `⏳ 阶段性停止（后台 2个子agent、1个工作流 执行中，对话未结束）`

## 安装

1. **复制文件**到 `~/.claude/`：
   ```
   statusline.py      →  ~/.claude/statusline.py
   hooks/*.py         →  ~/.claude/hooks/
   ```

2. **配置 webhook（环境变量，切勿硬编码）**：在 `~/.claude/settings.json` 的 `env` 块加：
   ```json
   "env": {
     "FEISHU_WEBHOOK_URL": "https://open.feishu.cn/open-apis/bot/v2/hook/你的webhook-id"
   }
   ```

3. **接线 statusLine + hooks**：参考 `settings.example.json`，把对应字段合并进 `~/.claude/settings.json`（路径替换成你自己的安装位置）。

4. **重启 Claude Code** 生效。

## Token / 余额从哪读

`statusline.py` 的余额刷新模式和 hooks 都从 **当前工作目录的 `.claude/settings.json`** 读取 `ANTHROPIC_AUTH_TOKEN`（找不到再回退到全局 `~/.claude/settings.json`），按 `ANTHROPIC_BASE_URL` 判断 provider。所以每个项目（如 `AI-GLM/`、`AI-KIMI/`、`AI-DeepSeek/`）各自配各自的 key，状态栏自动用对应的。

## 安全

- **所有密钥/webhook 走环境变量或本地 settings.json，本仓库不含任何私密信息。**
- `.gitignore` 已屏蔽运行时缓存（`*_cache.json`、`statusline_state.json`、debug log）和本地配置。
- 首次提交前已全量扫描 webhook / API token / 用户路径，确认干净。

## 运行时产物（不入库）

| 文件 | 内容 |
|------|------|
| `~/.claude/statusline_state.json` | transcript 文件大小缓存 + 上下文防抖值（按 session_id 隔离） |
| `~/.claude/quota_5h_cache.json` | GLM 5h 额度缓存 |
| `~/.claude/kimi_balance_cache.json` | Kimi 余额缓存 |
| `~/.claude/ds_balance_cache.json` | DeepSeek 余额缓存 |
| `~/.claude/hooks/stop_input_debug.log` | Stop hook 输入调试日志 |

## License

MIT
