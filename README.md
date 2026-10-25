# dsh-flow (Qoder port)

跨会话流程编排：统筹 / 预研 / 开发 / 测试四角色，任务卡为正本，走完
**派单 → 认领 → 回执 → 独立验收 → 终审 → 归档** 的闭环。

本仓库是 [KarBurKi/dsh-flow](https://github.com/KarBurKi/dsh-flow) v0.1.2（MIT）到 **Qoder Desktop** 的移植版。

## 为什么要移植

上游是一个 Cordis/DSH 宿主插件（Host 半）：`apply(ctx)` + `ctx.tools.register` + `agent.steer` + 等 `turn/end`。
Qoder Desktop 不是 Cordis 宿主，装不进去；但 Qoder Desktop **恰好原生提供了上游唯一依赖的那一层**——
跨会话投递原语（内置 `builtin` MCP server）。所以移植的思路是拆成两截：

| 上游 dsh-flow | 本移植版 | 说明 |
| --- | --- | --- |
| `flow_start` 等 6 个注册工具 | `flow.py` 同名子命令 | 卡片、YAML 头状态机、派单记录、验收段、SHA-256 归档，逐函数移植，零依赖 Python |
| `agent.steer` + 等 `turn/end`（同步） | `create_chat_session` / `send_message_to_chat_session` + `callbackMode:"supervise"`（异步回调） | 投递半交给编排 agent，脚本输出 `deliver` 指令 |
| `read_chat_session` / 回读 | 同名原语直接对应 | 回合结束回调后用 `read_chat_session` 查执行详情 |
| `flow_run` 同步循环推进 | `flow next` 每次一拍 | 等不到结论时返回 `wait-verify` / `wait-rework`，回调到达后再推进 |
| `session-` 前缀会话 id | 裸 uuid | Qoder 会话 id 无前缀 |
| 三重反引号防语音播报 | 保留为纯文本 | 本环境无语音播报场景 |
| `~/.dsh/profiles` 挂载 + junction + 重启 | 复制目录即装 | skill 无宿主加载过程，改脚本即时生效 |

三条不变量原样保留：**卡是正本**、**开发不自验**、**关闭要有通过的验收**（本移植版把第三条做成了
`close` 的默认硬检查，比上游更严）。

## 运行前提（重要）

- **产品形态**：Qoder Desktop 独立 App（含 Qoder CN）。**Qoder IDE（VSCode 系）不提供会话原语**——
  这是形态差异而非版本差异，升级 IDE 不解决问题。
- **原语检查**：会话内应能看到 `create_chat_session`、`send_message_to_chat_session`、`read_chat_session`、
  `wait_chat_sessions`、`respond_chat_session_interaction`、`stop_chat_session`、`list_chat_sessions`、`list_agents`。
  缺失则 spawn 与 relay 均不可用，本仓库只可当方法论参考。
- **Python**：3.8+，无第三方依赖。

已验证可用：Qoder CN Desktop `0.4.11`（2026-10-25 实测）。
上游报告的不适用样本：Qoder IDE `1.21.2`（VSCode 基线 1.106.3，构建 2026-08-02，扫描零命中）。

## 安装

1. 把本目录复制为用户级 skill（Qoder CN 路径示例）：

   ```
   %USERPROFILE%\.qoder-cn\skills\dsh-flow\
   ```

2. 准备配置（默认位置 `~/.qoder-flow/config.json`，也可 `--config` 或环境变量 `FLOW_CONFIG` 指定）：

   ```jsonc
   {
     "workspace": "D:\\my-workspace",        // 必填：本机工作区绝对路径
     "tasksDir":  "【文档区】/计划与任务",      // 任务卡在 workspace 下的相对目录
     "archiveDir": "【归档区】",
     "roles": {}                              // flow.py roles set 会写这里
   }
   ```

3. 登记四个角色的会话 id（`flow.py roles set --role 开发 --session <uuid>`）。
   角色会话不存在时，让统筹用 `create_chat_session` 建一个自包含任务会话再登记。

## 使用

流程纪律、角色分工、八拍操作顺序见 **[SKILL.md](SKILL.md)**。CLI 一览：

```
flow.py roles get|set                  登记/查看角色会话
flow.py start     --title …            建卡（pending）+ 投递指令
flow.py dispatch  --card … --to 开发|测试   派单（→processing）+ 投递指令
flow.py verify    --card … [--fail]    写验收结论；不通过给出投回开发的指令
flow.py close     --card …             done + 归档 + SHA-256 核对（默认要求「通过」验收）
flow.py next      --card …             按状态自动推进一拍
flow.py status    --card …             看卡：状态/最后验收/最后派单
```

所有命令输出 JSON；`deliver` 字段由编排 agent 照做（脚本不直接操作会话）。

## 验证边界（截至 2026-10-25）

**已实测**

- flow.py 全链路：建卡→派单→验收（不通过投回、重做后取最后结论）→归档；拒绝路径（无验收 close、归档后再 close、卡片名不唯一）；多段验收只认最后一段——与上游语义逐条对齐。
- Qoder CN 0.4.11 真实双会话冒烟：统筹会话 `create_chat_session` 派活 → 开发会话实现并回写卡片回执 → 监督回调 → 独立复核三条判据（含 SHA-256）→ 验收段 → 归档移动前后 SHA 一致。
- 上游 README 中「没验的：真实多会话闭环」一项，本移植版在双会话口径下补上了实证。

**未实测（先当不能）**

- 四角色四条会话各跑各的完整真人闭环（本次为统筹 + 开发两会话）。
- 跨工作区、同名卡并发、归档卡被 `next` 再次指到（现行为：明确报找不到，不静默推进）。

## 许可

MIT，Copyright (c) 2026 KarBurKi（沿用上游授权；本仓库为衍生移植，保留上游署名）。
