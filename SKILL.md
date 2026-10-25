---
name: dsh-flow
description: 跨会话流程编排：统筹/预研/开发/测试四角色，任务卡为正本，派单→认领→回执→独立验收→终审→归档闭环。flow.py 管卡片与状态机，投递唤醒由本会话用 Qoder Desktop 会话原语执行。适用于多会话协作走完一条活并留档。
---

# dsh-flow（Qoder 移植版）

四个角色（统筹 / 预研 / 开发 / 测试）跨会话走完一条活的闭环。**卡片与状态机交给 `flow.py`，投递与回读由你（编排会话）用会话原语执行**——脚本不碰会话，原语不懂流程。

## 前置要求（先验证再承诺）

本 skill 是说明书，不是引擎。用之前确认当前会话能看到这 8 个原语：
`create_chat_session`、`list_chat_sessions`、`send_message_to_chat_session`、`read_chat_session`、
`wait_chat_sessions`、`respond_chat_session_interaction`、`stop_chat_session`、`list_agents`。

- 已验证可用：Qoder Desktop 独立 App（Qoder CN 0.4.11，2026-10-25 实测，内置 `builtin` MCP server 提供）。
- 已验证不可用：Qoder IDE（VSCode 系，1.21.2 全安装扫描零命中）。这是**产品形态差异**，不是版本新旧。
- 若原语缺失：spawn 与 relay 都跑不起来，本 skill 只能当方法论参考，跨会话协作退回磁盘中转。

`flow.py` 需要 Python 3.8+（零第三方依赖）。

## 三条不变量（换平台也不能改）

1. **卡是正本**：状态、派单记录、验收结论都在卡上；会话历史只是视图。
2. **开发不自验**：做的人不判自己。自测通过 ≠ 验收通过。
3. **关闭要有通过的验收**：`flow.py close` 默认拒绝无「通过」验收段的关闭（`--force` 才越权，须向用户说明）。

## 五个坎

派单 → 认领 → 回执 → 独立验收 → 终审。少一个，这条活不算闭环。

```
预研 ─提需求/选型─► 统筹 ─建卡、拆解、派单─► 开发 ─实现、自测、回执─┐
                     │                                            │
                     └──派验收──► 测试 ─独立验收、报告、报边界──► 统筹 ─比对、终审、归档
                                                                     │
                        ◄──────────── 回到起点（下一轮）◄─────────────┘
```

## 分工：脚本管卡，你管投递

| 动作 | 谁做 | 怎么做 |
| --- | --- | --- |
| 建卡/派单/验收/归档/推进 | `flow.py`（经 Bash） | 改卡文件并输出 JSON，`deliver` 字段告诉你该投给谁、投什么 |
| 投递唤醒 | 你 | 按 `deliver.sessionId` 用 `send_message_to_chat_session`，`callbackMode:"supervise"` |
| 角色会话不存在 | 你 | `create_chat_session`（prompt 自包含 + supervise），再 `flow.py roles set` 登记返回的 uuid |
| 回读结果 | 你 | supervise 回调到达后 `read_chat_session` 看执行详情，验收须独立复核产物 |

推进不确定时先 `flow.py status --card <名>`，或 `flow.py next --card <名>` 走一拍。

## 标准流程（统筹视角）

1. **登记角色**：`flow.py roles set --role 开发 --session <uuid>`（四个角色各一次；`roles get` 看现状）。Qoder 会话 id 是裸 uuid，**不要**加 DSH 的 `session-` 前缀。
2. **接单建卡**：`flow.py start --title … --summary … --requirement … --criteria …` → 按返回的 `deliver` 投给统筹（可能就是本会话）。
3. **拆解派单**：`flow.py dispatch --card <名> --to 开发 --note …` → 照 `deliver` 投递。
4. **等回执**：开发完成后回执写进卡（这是开发会话的职责，指令里写清卡路径与回执格式）。
5. **请测试独立验收**：`flow.py dispatch --card <名> --to 测试 --note "请独立验收"`。给测试的 prompt 必须注明：**只看卡的验收标准，不看开发回执结论**。
6. **写结论**：`flow.py verify --card <名> [--fail] --report <路径> --note …`。不通过会自动给出投回开发的 `deliver`。
7. **终审后归档**：需求比对、把关通过 → `flow.py close --card <名>`（done + 移到 `<归档区>/<类型>/<YYYY-MM>` + 移动前后 SHA-256 核对）。
8. **下一轮**回到第 2 步。

首次使用先配 `config.json` 的 `workspace`（见 README），否则 `start` 会拒绝。

## 诚实边界（截至 2026-10-25）

- **已实测**：flow.py 全链路（start/dispatch/verify 通过与不通过/close/next 各拍/拒绝路径/多段验收取最后结论/SHA 归档）；Qoder CN 0.4.11 上真实双会话冒烟（统筹本会话 + 开发工人会话，supervise 回调、独立复核、归档一致）。
- **未实测**：四角色四条会话各跑各的完整真人闭环；跨工作区；同名卡并发；归档后再被 next 指到（现行为：报找不到，不静默）。
- 上游 dsh-flow 的「say() 同步等待」语义在本环境改为 supervise 异步回调：`next` 不再阻塞等回话，等不到结论时返回 `wait-verify`/`wait-rework`，由编排 agent 收到回调后再推进。
