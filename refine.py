# -*- coding: utf-8 -*-
"""
GLM 批量精炼管线
================
把每节课的口语转写（无标点、同音错别字、繁简混排）用 GLM 改写成：
  - summary   本课主旨（一句话）
  - keypoints 精华要点（数条）
  - sections  书面化、分小节的可读正文

输出缓存到 refined/<模块目录名>/<课时>.json，可断点续跑。
用法：
    python refine.py                # 全量（增量，已完成的跳过）
    python refine.py --limit 5      # 只跑前 5 个待处理课时（试跑）
    python refine.py --module Mail  # 只跑匹配名称的模块
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import threading
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

API_KEY = (
    os.environ.get("Z_AI_API_KEY")
    or os.environ.get("ZHIPU_API_KEY")
    or os.environ.get("GLM_API_KEY")
    or ""
)
URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
MODEL = "glm-4-flash"          # 免费、快；如需更强改为 glm-4-air / glm-4.5

SRC = Path(r"e:\BaiduNetdiskDownload\01.毅冰业务课2025（新版）\_转录与细节PDF\transcripts")
REFINED = Path(__file__).parent / "refined"
STATUS = Path(__file__).parent / "_refine_status.json"

CHUNK_CHARS = 3200
MAX_CHUNKS = 16               # 超长课时最多处理的分块数（防跑飞）
WORKERS = 6
MAX_RETRY = 4

_lock = threading.Lock()
_counter = {"done": 0, "fail": 0, "skip": 0}

SYS_PROMPT = (
    "你是资深外贸培训教材编辑。用户给你的是一节外贸课程视频的语音转写，"
    "特点是口语化、几乎没有标点、可能有同音错别字、繁简体混排、口头禅和重复。"
    "你的任务是把它整理成书面化、结构清晰、忠实于原意的学习笔记，"
    "绝不能编造原文没有的信息，也不要空泛套话。"
)

ONE_SHOT = """下面是一节课《{title}》的完整语音转写。请整理成规范书面语的学习笔记。

要求：
1. 纠正明显的同音错别字，统一为简体中文，删除口头禅（对不对/是吧/那个/就是说等）、重复和跑题内容。
2. 把内容归并成 2-6 个小节，每节一个 6-16 字的小标题，正文用通顺连贯的书面语（可多句）。
3. 提炼 4-8 条“精华要点”，每条是一句完整、具体、可执行或有洞见的书面语（12-30 字），不要照抄原句。
4. 给出一句话主旨 summary（40 字内）。

只输出如下 JSON，不要任何解释或代码块标记：
{{"summary":"...","keypoints":["...","..."],"sections":[{{"heading":"...","content":"..."}}]}}

转写内容：
{body}"""

CHUNK_SECTIONS = """这是课《{title}》转写的第 {idx}/{total} 部分。请把这部分整理成书面语小节。

要求：纠正错别字、统一简体、删除口头禅与重复；归并成 1-4 个小节，每节 6-16 字小标题 + 通顺书面正文。忠于原意，不编造。

只输出 JSON：{{"sections":[{{"heading":"...","content":"..."}}]}}

内容：
{body}"""

SYNTH = """下面是课《{title}》各小节的标题与摘要。请据此提炼全课精华。

要求：summary 一句话主旨（40 字内）；keypoints 提炼 5-8 条精华要点，每条 12-30 字完整书面语、具体有洞见、不要照抄。

只输出 JSON：{{"summary":"...","keypoints":["..."]}}

小节概要：
{body}"""


def api_call(messages: list[dict], max_tokens: int = 1800) -> str:
    if not API_KEY:
        raise RuntimeError(
            "未设置 API Key。请设置环境变量 Z_AI_API_KEY / ZHIPU_API_KEY / GLM_API_KEY"
        )
    body = json.dumps({
        "model": MODEL,
        "messages": messages,
        "temperature": 0.2,
        "top_p": 0.8,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    last = None
    for attempt in range(MAX_RETRY):
        try:
            req = urllib.request.Request(URL, data=body, headers={
                "Authorization": "Bearer " + API_KEY,
                "Content-Type": "application/json",
            })
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.loads(r.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8")[:200]
            except Exception:
                detail = ""
            last = f"HTTP {e.code} {detail}"
            if e.code in (429, 500, 502, 503, 529):
                time.sleep(2 * (attempt + 1) + 1)
                continue
            # 其他错误也重试一次
            time.sleep(1.5)
        except Exception as e:
            last = str(e)
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"API failed: {last}")


def parse_json(s: str) -> dict:
    s = s.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    try:
        return json.loads(s)
    except Exception:
        m = re.search(r"\{[\s\S]*\}", s)
        if m:
            return json.loads(m.group(0))
        raise


def chunk_text(text: str, size: int = CHUNK_CHARS) -> list[str]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    chunks, buf = [], ""
    for ln in lines:
        if len(buf) + len(ln) + 1 > size and buf:
            chunks.append(buf)
            buf = ln
        else:
            buf = buf + "\n" + ln if buf else ln
    if buf:
        chunks.append(buf)
    if not chunks:
        chunks = [text[i:i + size] for i in range(0, len(text), size)]
    return chunks


def refine_lesson(title: str, text: str) -> dict:
    chunks = chunk_text(text)
    if len(chunks) == 1:
        raw = api_call([
            {"role": "system", "content": SYS_PROMPT},
            {"role": "user", "content": ONE_SHOT.format(title=title, body=chunks[0])},
        ])
        obj = parse_json(raw)
        return {
            "summary": obj.get("summary", ""),
            "keypoints": obj.get("keypoints", []),
            "sections": obj.get("sections", []),
        }

    # 多块：逐块出小节，再综合
    chunks = chunks[:MAX_CHUNKS]
    truncated = len(chunk_text(text)) > MAX_CHUNKS
    sections = []
    for i, ch in enumerate(chunks, 1):
        raw = api_call([
            {"role": "system", "content": SYS_PROMPT},
            {"role": "user", "content": CHUNK_SECTIONS.format(
                title=title, idx=i, total=len(chunks), body=ch)},
        ])
        try:
            obj = parse_json(raw)
            for sec in obj.get("sections", []):
                if sec.get("heading") and sec.get("content"):
                    sections.append(sec)
        except Exception:
            continue

    outline = "\n".join(
        f"- {s['heading']}：{s['content'][:60]}" for s in sections[:24]
    )
    summary, keypoints = "", []
    try:
        raw = api_call([
            {"role": "system", "content": SYS_PROMPT},
            {"role": "user", "content": SYNTH.format(title=title, body=outline)},
        ], max_tokens=700)
        obj = parse_json(raw)
        summary = obj.get("summary", "")
        keypoints = obj.get("keypoints", [])
    except Exception:
        keypoints = [s["heading"] for s in sections[:6]]

    if truncated:
        summary = (summary + "（本课内容较长，已提炼主要部分）").strip()
    return {"summary": summary, "keypoints": keypoints, "sections": sections}


def natural_key(s: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def collect_tasks(module_filter: str | None):
    tasks = []
    for d in sorted(SRC.iterdir()):
        if not d.is_dir():
            continue
        if module_filter and module_filter.lower() not in d.name.lower():
            continue
        for jp in sorted(d.glob("*.json"), key=lambda p: natural_key(p.stem)):
            out = REFINED / d.name / (jp.stem + ".json")
            tasks.append((jp, out))
    return tasks


def is_done(out: Path) -> bool:
    if not out.exists():
        return False
    try:
        o = json.loads(out.read_text(encoding="utf-8"))
        return bool(o.get("sections"))
    except Exception:
        return False


def worker(jp: Path, out: Path):
    try:
        data = json.loads(jp.read_text(encoding="utf-8"))
    except Exception as e:
        with _lock:
            _counter["fail"] += 1
        return f"[读取失败] {jp.name}: {e}"
    title = data.get("title") or jp.stem
    text = (data.get("text") or "").strip()
    if len(text) < 40:
        result = {"summary": title, "keypoints": [], "sections":
                  [{"heading": "内容", "content": text}]}
    else:
        try:
            result = refine_lesson(title, text)
        except Exception as e:
            with _lock:
                _counter["fail"] += 1
            return f"[精炼失败] {jp.name}: {e}"
    result.update({
        "title": title,
        "duration": data.get("duration") or 0,
        "model": MODEL,
        "chars": len(text),
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    with _lock:
        _counter["done"] += 1
        n = _counter["done"]
    return f"[完成 {n}] {jp.parent.name} / {title} · {len(result['sections'])}节 {len(result['keypoints'])}要点"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--module", type=str, default=None)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    all_tasks = collect_tasks(args.module)
    todo = [(jp, out) for jp, out in all_tasks if args.force or not is_done(out)]
    _counter["skip"] = len(all_tasks) - len(todo)
    if args.limit:
        todo = todo[:args.limit]

    print(f"总课时 {len(all_tasks)} · 已完成 {_counter['skip']} · 本次处理 {len(todo)} · 模型 {MODEL}")
    if not todo:
        print("没有待处理课时。")
        return

    start = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(worker, jp, out) for jp, out in todo]
        for i, fut in enumerate(as_completed(futs), 1):
            msg = fut.result()
            print(msg, flush=True)
            if i % 20 == 0:
                el = time.time() - start
                rate = i / el * 60
                left = (len(todo) - i) / max(rate, 0.1)
                STATUS.write_text(json.dumps({
                    "processed": i, "total": len(todo),
                    "done": _counter["done"], "fail": _counter["fail"],
                    "rate_per_min": round(rate, 1),
                    "eta_min": round(left, 1),
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                }, ensure_ascii=False, indent=1), encoding="utf-8")
                print(f"  … {i}/{len(todo)} · {rate:.1f}/分 · 预计剩余 {left:.0f} 分", flush=True)

    print(f"\n结束：完成 {_counter['done']} · 失败 {_counter['fail']} · 用时 {(time.time()-start)/60:.1f} 分")


if __name__ == "__main__":
    main()
