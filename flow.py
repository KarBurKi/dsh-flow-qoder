#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dsh-flow (Qoder port) — 跨会话流程编排引擎，零依赖，宿主无关

移植自 KarBurKi/dsh-flow v0.1.2（MIT，Copyright (c) 2026 KarBurKi），
原作为 Cordis/DSH 宿主插件（Host 半），本移植版把它拆成两截：

  - 文件半（本脚本）：任务卡、YAML 头状态机、派单记录、验收段、SHA-256 归档。
  - 投递半（交给编排 agent）：用 Qoder Desktop 的会话原语执行——
    create_chat_session / send_message_to_chat_session + callbackMode "supervise"，
    回读用 read_chat_session / wait_chat_sessions。

本脚本每步都输出 JSON，其中 `deliver` 字段给出「该投给谁、投什么」，
由编排 agent（通常是「统筹」会话）照做。脚本自己不碰网络、不碰会话。

状态流转：pending → processing → done。验收结论写在卡里的「## 验收」段，
归档前核对文件 SHA-256，移动前后一致才算成。

不变量（与上游一致）：
  1. 卡是正本：状态、派单记录、验收结论都在卡上。
  2. 开发不自验：做的人不判自己。
  3. 关闭要有通过的验收：没有验收的 done 是耍赖（close 只改状态，
     流程纪律由 SKILL.md 要求 close 前必须存在「通过」验收段）。
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
from pathlib import Path

ROLES = ["统筹", "预研", "开发", "测试"]
DISPATCH_TARGETS = ["开发", "测试"]
HEAD_ORDER = ["from", "to", "type", "ticket", "status", "created"]

DEFAULT_CONFIG = {
    # workspace 留空 = 未配置；首次使用前必须指到本机工作区（见 README「配置」）
    "workspace": "",
    "tasksDir": "【文档区】/计划与任务",
    "archiveDir": "【归档区】",
    "roles": {},
}


# ---------- 配置 ----------

def config_path(args):
    p = args.config or os.environ.get("FLOW_CONFIG")
    if p:
        return Path(p).expanduser().resolve()
    return Path.home() / ".qoder-flow" / "config.json"


def load_config(args):
    file = config_path(args)
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raw = {}
    except (OSError, ValueError):
        raw = {}
    roles = raw.get("roles") if isinstance(raw.get("roles"), dict) else {}
    cfg = {**DEFAULT_CONFIG, **raw, "roles": dict(roles)}
    cfg["_file"] = str(file)
    return cfg


def save_config(cfg, patch):
    file = Path(cfg["_file"])
    public = {k: v for k, v in cfg.items() if k != "_file"}
    public.update(patch)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(public, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return public


# ---------- 小工具（与上游同名同义） ----------

def sha256(file):
    return hashlib.sha256(Path(file).read_bytes()).hexdigest()


def now():
    return dt.datetime.now()


def stamp(d=None):
    d = d or now()
    return "{0}-{1:02d}-{2:02d} {3:02d}:{4:02d}".format(d.year, d.month, d.day, d.hour, d.minute)


def ymd(d=None):
    d = d or now()
    return "{0}{1:02d}{2:02d}".format(d.year, d.month, d.day)


def yyyymm(d=None):
    d = d or now()
    return "{0}-{1:02d}".format(d.year, d.month)


def sanitize(text, fallback="NA"):
    cleaned = str(text or "")
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]', " ", cleaned)
    cleaned = cleaned.replace("_", " ").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or fallback


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def force_utf8_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


# ---------- 任务卡：YAML 头与正文 ----------

def parse_card(text):
    m = re.match(r"^---\r?\n([\s\S]*?)\r?\n---\r?\n?", text)
    if not m:
        return {"head": {}, "body": text, "hasHead": False}
    head = {}
    for line in m.group(1).splitlines():
        kv = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$", line)
        if not kv:
            continue
        head[kv.group(1)] = kv.group(2).strip().strip("\"'")
    return {"head": head, "body": text[m.end():], "hasHead": True}


def render_head(head):
    lines = ["---"]
    for key in HEAD_ORDER:
        val = head.get(key)
        if val not in (None, ""):
            lines.append("{0}: {1}".format(key, val))
    lines.append("---")
    return "\n".join(lines) + "\n"


def set_status(text, status):
    if not parse_card(text)["hasHead"]:
        raise SystemExit("这张卡没有 YAML 头，不敢改状态")
    return re.sub(r"^(status:\s*).*$", lambda m: m.group(1) + status, text, count=1, flags=re.M)


def last_verdict(text):
    idx = text.rfind("## 验收")
    if idx < 0:
        return ""
    m = re.search(r"结论：(\S+)", text[idx:])
    return m.group(1) if m else ""


def tasks_dir(cfg):
    return Path(cfg["workspace"]) / cfg["tasksDir"]


def archive_dir(cfg):
    return Path(cfg["workspace"]) / cfg["archiveDir"]


def resolve_card(cfg, ref):
    if not ref:
        raise SystemExit("要给卡片名或路径")
    direct = Path(ref) if os.path.isabs(ref) else tasks_dir(cfg) / ref
    if direct.is_file():
        return direct
    try:
        files = [p.name for p in tasks_dir(cfg).iterdir() if p.suffix == ".md"]
    except OSError:
        files = []
    hits = [name for name in files if ref in name]
    if len(hits) == 1:
        return tasks_dir(cfg) / hits[0]
    if len(hits) > 1:
        raise SystemExit("卡片名不唯一，请写全：" + "、".join(hits))
    raise SystemExit("找不到卡片「{0}」（在 {1} 里没搜到）".format(ref, tasks_dir(cfg)))


def role_session(cfg, role):
    sid = cfg["roles"].get(role)
    if not sid:
        raise SystemExit("还没登记「{0}」的会话，先 flow.py roles set --role {0} --session <id>".format(role))
    return str(sid)


def deliver_for(cfg, role, text):
    """投递指令：本脚本不发会话消息，交给编排 agent 用原语执行。"""
    sid = cfg["roles"].get(role)
    return {
        "role": role,
        "sessionId": sid or None,
        "text": text,
        "how": "send_message_to_chat_session(prompt=text)；若未登记会话则先 create_chat_session",
        "monitor": 'callbackMode: "supervise"；回合结束后 read_chat_session 回读',
        "registered": bool(sid),
    }


# ---------- 子命令 ----------

def cmd_roles(args):
    cfg = load_config(args)
    if args.op == "get":
        roles = cfg["roles"]
        emit({"roles": roles, "text": "\n".join("{0}：{1}".format(r, roles.get(r) or "（未登记）") for r in ROLES)})
        return
    if args.role not in ROLES:
        raise SystemExit("role 只能是：" + " / ".join(ROLES))
    if not args.session:
        raise SystemExit("set 需要 --session <conversationId>")
    roles = {**cfg["roles"], args.role: args.session}
    save_config(cfg, {"roles": roles})
    emit({"roles": roles, "text": "已登记 {0} → {1}".format(args.role, args.session)})


def cmd_start(args):
    cfg = load_config(args)
    if not cfg["workspace"]:
        raise SystemExit("config.workspace 未配置，首次使用前必须指到本机工作区")
    to = args.to or "统筹"
    from_role = args.getattr_from or "预研"
    ticket = args.ticket or "NA"
    title = sanitize(args.title, "未命名")
    summary = sanitize(args.summary or "", "NA")
    d = now()
    file_name = "任务卡_{0}_{1}_{2}_{3}.md".format(title, summary, sanitize(ticket, "NA"), ymd(d))

    dir_ = tasks_dir(cfg)
    dir_.mkdir(parents=True, exist_ok=True)
    file = dir_ / file_name
    if file.exists():
        raise SystemExit("卡片已存在：{0}".format(file))

    requirement = read_text_arg(args.requirement, args.requirement_file)
    criteria = read_text_arg(args.criteria, args.criteria_file)
    head = {
        "from": from_role,
        "to": to,
        "type": "任务卡",
        "ticket": ticket,
        "status": "pending",
        "created": "{0}-{1:02d}-{2:02d}".format(d.year, d.month, d.day),
    }
    body = "\n".join([
        "# 任务卡 · {0}".format(title),
        "",
        "**交给：{0}** ｜ 出单：{1} ｜ {2}".format(to, from_role, stamp(d)),
        "",
        "## 要求",
        "",
        requirement or "（待补）",
        "",
        "## 验收标准",
        "",
        criteria or "（待补）",
        "",
        "## 回执",
        "",
        "（执行方填写）",
        "",
    ])
    file.write_text(render_head(head) + "\n" + body, encoding="utf-8", newline="\n")

    emit({
        "file": str(file),
        "fileName": file_name,
        "status": "pending",
        "to": to,
        "deliver": deliver_for(cfg, to, "【{0}】新任务卡：{1}。请拆解并派单，看卡。".format(from_role, file_name)),
    })


def read_text_arg(inline, file):
    if inline:
        return inline
    if file:
        return Path(file).read_text(encoding="utf-8")
    return None


def do_dispatch(cfg, file, role, note):
    """派单的公共部分：改卡 + 返回投递指令。上游 flow_dispatch / runDispatch 的移植。"""
    text = Path(file).read_text(encoding="utf-8")
    head = parse_card(text)["head"]
    if head.get("status") == "done":
        raise SystemExit("这张卡已经 done，别再派单")
    if head.get("status") != "processing":
        text = set_status(text, "processing")
    line = "\n\n> 派单 → {0}（{1}）{2}\n".format(role, stamp(), "：" + note if note else "")
    text = text.rstrip("\n") + line
    Path(file).write_text(text, encoding="utf-8", newline="\n")
    deliver = deliver_for(cfg, role, "【统筹】派单：{0}，请认领。".format(Path(file).name))
    return {"file": Path(file).name, "to": role, "status": "processing", "deliver": deliver}


def cmd_dispatch(args):
    cfg = load_config(args)
    file = resolve_card(cfg, args.card)
    if args.to not in DISPATCH_TARGETS:
        raise SystemExit("派单目标只能是：开发 / 测试")
    emit(do_dispatch(cfg, file, args.to, args.note))


def cmd_verify(args):
    cfg = load_config(args)
    file = resolve_card(cfg, args.card)
    text = Path(file).read_text(encoding="utf-8")
    head = parse_card(text)["head"]
    passed = not args.fail
    section_lines = [
        "",
        "## 验收（{0}）".format(stamp()),
        "",
        "- 结论：{0}".format("通过" if passed else "不通过"),
        "- 报告：{0}".format(args.report or "（无）"),
    ]
    if args.note:
        section_lines.append("- 备注：{0}".format(args.note))
    section_lines.append("")
    text = text.rstrip("\n") + "\n" + "\n".join(section_lines)
    Path(file).write_text(text, encoding="utf-8", newline="\n")

    out = {"file": Path(file).name, "pass": passed, "status": head.get("status")}
    if not passed:
        out["deliver"] = deliver_for(
            cfg, "开发",
            "【测试】{0} 验收不通过，请重做。报告：{1}".format(Path(file).name, args.report or "见卡"),
        )
    emit(out)


def do_close(cfg, file):
    text = Path(file).read_text(encoding="utf-8")
    head = parse_card(text)["head"]
    if head.get("status") == "done":
        raise SystemExit("这张卡已经是 done 了：{0}".format(Path(file).name))
    text = set_status(text, "done")
    Path(file).write_text(text, encoding="utf-8", newline="\n")

    type_ = sanitize(head.get("type") or "任务卡", "任务卡")
    dest_dir = archive_dir(cfg) / type_ / yyyymm()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / Path(file).name
    if dest.exists():
        raise SystemExit("归档目标已存在，不覆盖：{0}".format(dest))

    before = sha256(file)
    os.rename(file, dest)
    after = sha256(dest)
    if before != after:
        raise SystemExit("SHA 不一致（{0} ≠ {1}），文件已移到 {2}，请人工核对".format(before, after, dest))
    return {"file": Path(file).name, "archivedTo": str(dest), "status": "done", "sha256": after}


def cmd_close(args):
    cfg = load_config(args)
    file = resolve_card(cfg, args.card)
    if not args.force and last_verdict(file_text(cfg, file)) != "通过":
        raise SystemExit("卡里没有「通过」的验收段，关闭要有通过的验收——先走 flow.py verify（或补一轮开发再验）")
    emit(do_close(cfg, file))


def file_text(cfg, file):
    return Path(file).read_text(encoding="utf-8")


def last_dispatch_role(text):
    matches = re.findall(r"^> 派单 → (\S+?)（", text, flags=re.M)
    return matches[-1] if matches else ""


def cmd_next(args):
    """一键推进的移植（flow_run）：每次调用走一步，投递动作放在返回的 deliver 里。
    与上游的差异：上游 say() 同步等对方回合结束；这里投递是异步的（supervise 回调），
    所以「等验收写进卡」这类等待变成明确的 wait_* 动作，由编排 agent 收到回调后再调 next。"""
    cfg = load_config(args)
    file = resolve_card(cfg, args.card)
    text = file_text(cfg, file)
    head = parse_card(text)["head"]
    status = head.get("status")
    name = Path(file).name

    if status == "done":
        emit({"step": "done", "file": name})
        return
    if status == "pending":
        out = do_dispatch(cfg, file, "开发", "")
        emit({"step": "dispatch", **out})
        return
    # processing
    verdict = last_verdict(text)
    if verdict == "通过":
        emit({"step": "close", **do_close(cfg, file)})
        return
    if verdict == "不通过":
        emit({"step": "wait-rework", "file": name, "hint": "验收不通过，等开发重做并回执后再请测试验"})
        return
    if last_dispatch_role(text) == "测试":
        emit({"step": "wait-verify", "file": name, "hint": "已请测试验收，等结论写进卡（flow.py verify）再 next"})
        return
    emit({"step": "verify-request", **do_dispatch(cfg, file, "测试", "请独立验收")})


def cmd_status(args):
    cfg = load_config(args)
    file = resolve_card(cfg, args.card)
    text = file_text(cfg, file)
    card = parse_card(text)
    emit({
        "file": str(file),
        "head": card["head"],
        "verdict": last_verdict(text),
        "lastDispatch": last_dispatch_role(text),
        "hasReceipt": ("（执行方填写）" not in text) and ("## 回执" in text),
    })


def main(argv=None):
    force_utf8_stdio()
    parser = argparse.ArgumentParser(prog="flow.py", description="dsh-flow（Qoder 移植版）任务卡状态机，零依赖")
    parser.add_argument("--config", help="config.json 路径（默认为 ~/.qoder-flow/config.json，可用 FLOW_CONFIG 环境变量）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("roles", help="登记/查看四角色会话 id")
    p.add_argument("op", choices=["get", "set"])
    p.add_argument("--role", choices=ROLES)
    p.add_argument("--session", help="目标会话的 conversationId（uuid，Qoder 无 session- 前缀）")
    p.set_defaults(fn=cmd_roles)

    p = sub.add_parser("start", help="建任务卡（status=pending）并给出投递给接手角色的文本")
    p.add_argument("--title", required=True)
    p.add_argument("--summary")
    p.add_argument("--from", dest="getattr_from", help="发起角色，默认 预研")
    p.add_argument("--to", help="交给谁，默认 统筹")
    p.add_argument("--ticket")
    p.add_argument("--requirement", help="要求正文")
    p.add_argument("--requirement-file")
    p.add_argument("--criteria", help="验收标准")
    p.add_argument("--criteria-file")
    p.set_defaults(fn=cmd_start)

    p = sub.add_parser("dispatch", help="派单：pending→processing、记派单行、给投递指令")
    p.add_argument("--card", required=True)
    p.add_argument("--to", required=True)
    p.add_argument("--note")
    p.set_defaults(fn=cmd_dispatch)

    p = sub.add_parser("verify", help="测试回执：把结论写进卡；不通过给出投回开发的指令")
    p.add_argument("--card", required=True)
    p.add_argument("--fail", action="store_true", help="验收不通过")
    p.add_argument("--report")
    p.add_argument("--note")
    p.set_defaults(fn=cmd_verify)

    p = sub.add_parser("close", help="归档：done、移到 <归档区>/<类型>/<YYYY-MM>、前后核对 SHA-256")
    p.add_argument("--card", required=True)
    p.add_argument("--force", action="store_true", help="跳过「须有通过的验收」检查（默认拒绝无验收关闭）")
    p.set_defaults(fn=cmd_close)

    p = sub.add_parser("next", help="按状态自动推进一拍（flow_run 的异步移植）")
    p.add_argument("--card", required=True)
    p.set_defaults(fn=cmd_next)

    p = sub.add_parser("status", help="看卡：YAML 头、最后验收结论、最后派单对象")
    p.add_argument("--card", required=True)
    p.set_defaults(fn=cmd_status)

    args = parser.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
