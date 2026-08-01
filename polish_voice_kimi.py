# -*- coding: utf-8 -*-
"""
用 Kimi Code CLI 凭证，把概述/要点/小节改成日常、准确、有洞见的表述。
======================================================================
数据：refined/**/*.json
字段：
  - summary / keypoints / sections  → 重写
  - voice_v = 1 标记已处理（可 --force 重跑）

用法：
    python polish_voice_kimi.py
    python polish_voice_kimi.py --module Mail --limit 5
    python polish_voice_kimi.py --force
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import threading
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REFINED = Path(__file__).parent / "refined"
CRED = Path.home() / ".kimi-code" / "credentials" / "kimi-code.json"
URL = "https://api.kimi.com/coding/v1/chat/completions"
MODEL = "k3"   # content 字段稳定；coding 模型常把答案放进 reasoning_content
VOICE_V = 2    # v2: 只稳改 summary+keypoints
WORKERS = 3
MAX_RETRY = 5

_lock = threading.Lock()
_counter = {"done": 0, "fail": 0, "skip": 0}
_token_cache = {"token": "", "exp": 0}


SYS = (
    "你是资深外贸培训编辑，文风像懂行的人跟同行聊天："
    "简体、口语但不随便、具体、有判断。"
    "禁止“本节课探讨了/本章将介绍/综上所述”这类套话。"
    "只输出合法 JSON，不要 markdown 代码块。"
)

PROMPT = """优化这节外贸课的概述和要点。只改 summary 和 keypoints，不要输出其它字段。

课名：{title}

原概述：
{summary}

原要点：
{keypoints}

补充信息（供你写准，不要整段照抄）：
{sections}

要求：
1. summary：一句中文（28–55字）。像懂行的人一句话点破本课真正解决什么；准确 + 有判断；日常说法。禁止“本节课探讨/本章介绍/综上所述”。
2. keypoints：5条左右；每条16–36字完整句；具体可执行或带判断；简体日常说法。
3. 忠于原意，不编造事实。

严格只输出一行 JSON（字段必须齐全）：
{{"summary":"...","keypoints":["...","..."]}}
"""


def refresh_token_via_kimi_cli() -> bool:
    """Kimi OAuth 约 15 分钟过期；用 kimi -p 触发 CLI 自动续期。"""
    try:
        kimi = Path.home() / ".kimi-code" / "bin" / "kimi.exe"
        cmd = [str(kimi) if kimi.exists() else "kimi", "-p", "ok", "--output-format", "text"]
        subprocess.run(cmd, capture_output=True, text=True, timeout=90, check=False)
        o = json.loads(CRED.read_text(encoding="utf-8"))
        ok = float(o.get("expires_at") or 0) > time.time() + 60
        if ok:
            _token_cache["token"] = o["access_token"]
            _token_cache["exp"] = float(o["expires_at"])
            print(f"[token] refreshed, valid ~{(_token_cache['exp']-time.time())/60:.0f} min", flush=True)
        return ok
    except Exception as e:
        print(f"[token] refresh failed: {e}", flush=True)
        return False


def load_token() -> str:
    now = time.time()
    if _token_cache["token"] and _token_cache["exp"] - 120 > now:
        return _token_cache["token"]
    o = json.loads(CRED.read_text(encoding="utf-8"))
    exp = float(o.get("expires_at") or 0)
    if exp - 120 <= now:
        with _lock:
            # 双重检查，避免多线程同时刷
            o = json.loads(CRED.read_text(encoding="utf-8"))
            exp = float(o.get("expires_at") or 0)
            if exp - 120 <= time.time():
                refresh_token_via_kimi_cli()
                o = json.loads(CRED.read_text(encoding="utf-8"))
                exp = float(o.get("expires_at") or 0)
    _token_cache["token"] = o["access_token"]
    _token_cache["exp"] = exp or (now + 3600)
    return _token_cache["token"]


def api_call(messages: list[dict], max_tokens: int = 1800) -> str:
    body = json.dumps({
        "model": MODEL,
        "messages": messages,
        "temperature": 1,
        "max_tokens": max_tokens,
    }).encode("utf-8")
    last = None
    for attempt in range(MAX_RETRY):
        try:
            token = load_token()
            req = urllib.request.Request(URL, data=body, headers={
                "Authorization": "Bearer " + token,
                "Content-Type": "application/json",
            })
            with urllib.request.urlopen(req, timeout=180) as r:
                data = json.loads(r.read().decode("utf-8"))
            msg = data["choices"][0]["message"]
            content = msg.get("content") or ""
            if isinstance(content, list):
                content = "".join(
                    (c.get("text") or "") for c in content if isinstance(c, dict)
                )
            # coding 模型偶发 content 为空，答案在 reasoning_content
            if not str(content).strip():
                reason = msg.get("reasoning_content") or ""
                m = re.search(r"\{[\s\S]*\}", reason)
                if m:
                    content = m.group(0)
            return content
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8")[:240]
            except Exception:
                detail = ""
            last = f"HTTP {e.code} {detail}"
            # refresh token on 401
            if e.code == 401:
                _token_cache["token"] = ""
                with _lock:
                    refresh_token_via_kimi_cli()
            time.sleep(1.5 * (attempt + 1))
        except Exception as e:
            last = str(e)
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(last or "api failed")


def parse_json(s: str) -> dict:
    s = (s or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    # 去掉可能的思考标签
    s = re.sub(r"<think>[\s\S]*?</think>", "", s, flags=re.I)
    candidates = [s]
    m = re.search(r"\{[\s\S]*\}", s)
    if m:
        candidates.append(m.group(0))
    last_err = None
    for cand in candidates:
        for text in (cand, cand.replace("\u201c", '"').replace("\u201d", '"').replace("'", '"')):
            try:
                return json.loads(text)
            except Exception as e:
                last_err = e
            # 尝试去掉尾逗号
            fixed = re.sub(r",\s*([}\]])", r"\1", text)
            try:
                return json.loads(fixed)
            except Exception as e:
                last_err = e
    raise last_err or ValueError("json parse failed")


def fmt_keypoints(kps: list) -> str:
    if not kps:
        return "（无）"
    return "\n".join(f"- {k}" for k in kps)


def fmt_sections(secs: list) -> str:
    if not secs:
        return "（无）"
    parts = []
    for s in secs[:10]:
        h = (s.get("heading") or "").strip()
        c = (s.get("content") or "").strip()
        if len(c) > 220:
            c = c[:220] + "…"
        parts.append(f"### {h}\n{c}")
    return "\n\n".join(parts)


def natural_key(s: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def needs_work(obj: dict, force: bool) -> bool:
    if force:
        return True
    if obj.get("voice_v") == VOICE_V and obj.get("summary"):
        return False
    return True


def collect(module_filter: str | None, force: bool):
    tasks = []
    for d in sorted(REFINED.iterdir()):
        if not d.is_dir():
            continue
        if module_filter and module_filter.lower() not in d.name.lower():
            continue
        for jp in sorted(d.glob("*.json"), key=lambda p: natural_key(p.stem)):
            try:
                obj = json.loads(jp.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not (obj.get("sections") or obj.get("summary") or obj.get("keypoints")):
                continue
            if needs_work(obj, force):
                tasks.append(jp)
            else:
                _counter["skip"] += 1
    return tasks


def rewrite_one(obj: dict) -> dict:
    title = obj.get("title") or ""
    raw = api_call([
        {"role": "system", "content": SYS},
        {"role": "user", "content": PROMPT.format(
            title=title,
            summary=obj.get("summary") or "（无）",
            keypoints=fmt_keypoints(obj.get("keypoints") or []),
            sections=fmt_sections(obj.get("sections") or []),
        )},
    ], max_tokens=900)
    if not (raw or "").strip():
        raise RuntimeError("empty api content")
    out = parse_json(raw)
    summary = (out.get("summary") or "").strip()
    kps = [str(k).strip() for k in (out.get("keypoints") or []) if str(k).strip()]
    if not summary or len(kps) < 3:
        raise RuntimeError(f"weak rewrite: {raw[:180]!r}")
    return {"summary": summary, "keypoints": kps[:8]}


def worker(jp: Path):
    try:
        obj = json.loads(jp.read_text(encoding="utf-8"))
        new = rewrite_one(obj)
        if "summary_prev" not in obj:
            obj["summary_prev"] = obj.get("summary")
        if "keypoints_prev" not in obj:
            obj["keypoints_prev"] = obj.get("keypoints")
        obj["summary"] = new["summary"]
        obj["keypoints"] = new["keypoints"]
        obj["voice_v"] = VOICE_V
        obj["voice_model"] = MODEL
        obj["voice_ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
        jp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
        with _lock:
            _counter["done"] += 1
            n = _counter["done"]
        return f"[完成 {n}] {jp.parent.name} / {obj.get('title') or jp.stem}"
    except Exception as e:
        with _lock:
            _counter["fail"] += 1
        return f"[失败] {jp.name}: {e}"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    if not CRED.exists():
        raise SystemExit("未找到 Kimi 凭证，请先运行: kimi login")
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--module", type=str, default=None)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    todo = collect(args.module, args.force)
    if args.limit:
        todo = todo[: args.limit]
    print(f"待优化 {len(todo)} · 已跳过 {_counter['skip']} · 模型 {MODEL}", flush=True)
    if not todo:
        print("没有待处理。")
        return

    start = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(worker, jp) for jp in todo]
        for i, fut in enumerate(as_completed(futs), 1):
            print(fut.result(), flush=True)
            if i % 20 == 0:
                rate = i / max(time.time() - start, 1) * 60
                left = (len(todo) - i) / max(rate, 0.1)
                print(f"  … {i}/{len(todo)} · {rate:.1f}/分 · 预计剩余 {left:.0f} 分", flush=True)

    print(f"\n结束：完成 {_counter['done']} · 失败 {_counter['fail']} · 用时 {(time.time()-start)/60:.1f} 分")


if __name__ == "__main__":
    main()
