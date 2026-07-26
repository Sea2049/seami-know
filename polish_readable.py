# -*- coding: utf-8 -*-
"""
通顺讲稿生成（简体、达意）
========================
在已有 refined/*.json 上追加 readable 字段：把口语转写改写成可读的简体书面讲稿。
可断点续跑；已有 readable 的跳过。

用法：
    set Z_AI_API_KEY=...
    python polish_readable.py
    python polish_readable.py --module Linked --limit 5
    python polish_readable.py --force
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
MODEL = "glm-4-flash"

SRC = Path(r"e:\BaiduNetdiskDownload\01.毅冰业务课2025（新版）\_转录与细节PDF\transcripts")
REFINED = Path(__file__).parent / "refined"

CHUNK_CHARS = 2800
MAX_CHUNKS = 20
WORKERS = 6
MAX_RETRY = 4

_lock = threading.Lock()
_counter = {"done": 0, "fail": 0, "skip": 0}

SYS = (
    "你是中文教材编辑。把外贸课程的语音转写改写成通顺、可直接阅读的简体中文讲稿。"
    "忠实原意，不编造；修正错别字与繁简混用；去掉口头禅和无意义重复；保留例子与关键步骤。"
)

PROMPT = """下面是课《{title}》语音转写的第 {idx}/{total} 段。请改写成通顺的简体中文讲稿段落。

要求：
1. 只用简体中文；加标点；按意思分成若干自然段。
2. 删除「对不对/是吧/那个/就是说」等口水词，删除同义反复。
3. 保留完整教学信息（步骤、原则、例子、英文关键词可保留原文并附简短中文说明）。
4. 不要小标题、不要列表编号、不要总结套话；只写正文段落。

只输出 JSON：{{"paragraphs":["段落1","段落2"]}}

内容：
{body}"""


def api_call(messages: list[dict], max_tokens: int = 2200) -> str:
    if not API_KEY:
        raise RuntimeError("未设置 Z_AI_API_KEY / ZHIPU_API_KEY / GLM_API_KEY")
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
                detail = e.read().decode("utf-8")[:220]
            except Exception:
                detail = ""
            last = f"HTTP {e.code} {detail}"
            if e.code in (429, 500, 502, 503, 529) or e.code == 400:
                time.sleep(2 * (attempt + 1) + 1)
                continue
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
        if not m:
            raise
        return json.loads(m.group(0))


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
        chunks = [text[i:i + size] for i in range(0, max(len(text), 1), size)] or [""]
    return chunks


def polish(title: str, text: str) -> list[str]:
    chunks = chunk_text(text)[:MAX_CHUNKS]
    paras: list[str] = []
    for i, ch in enumerate(chunks, 1):
        raw = api_call([
            {"role": "system", "content": SYS},
            {"role": "user", "content": PROMPT.format(
                title=title, idx=i, total=len(chunks), body=ch)},
        ])
        try:
            obj = parse_json(raw)
            for p in obj.get("paragraphs") or []:
                p = re.sub(r"\s+", "", str(p).strip()) if False else str(p).strip()
                p = re.sub(r"[ \t]+", " ", p)
                if len(p) >= 8:
                    paras.append(p)
        except Exception:
            continue
    return paras


def natural_key(s: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def has_readable(obj: dict) -> bool:
    r = obj.get("readable")
    return isinstance(r, list) and len(r) >= 1 and all(isinstance(x, str) and x.strip() for x in r)


def collect(module_filter: str | None, force: bool):
    tasks = []
    for d in sorted(SRC.iterdir()):
        if not d.is_dir():
            continue
        if module_filter and module_filter.lower() not in d.name.lower():
            continue
        for jp in sorted(d.glob("*.json"), key=lambda p: natural_key(p.stem)):
            out = REFINED / d.name / (jp.stem + ".json")
            if out.exists() and not force:
                try:
                    if has_readable(json.loads(out.read_text(encoding="utf-8"))):
                        continue
                except Exception:
                    pass
            tasks.append((jp, out))
    return tasks


def worker(jp: Path, out: Path):
    try:
        data = json.loads(jp.read_text(encoding="utf-8"))
    except Exception as e:
        with _lock:
            _counter["fail"] += 1
        return f"[读失败] {jp.name}: {e}"

    title = data.get("title") or jp.stem
    text = (data.get("text") or "").strip()
    if len(text) < 40:
        paras = [text] if text else ["（本课转写过短）"]
    else:
        try:
            paras = polish(title, text)
            if not paras:
                raise RuntimeError("empty paragraphs")
        except Exception as e:
            with _lock:
                _counter["fail"] += 1
            return f"[讲稿失败] {jp.name}: {e}"

    # merge into existing refined json or create stub
    if out.exists():
        try:
            obj = json.loads(out.read_text(encoding="utf-8"))
        except Exception:
            obj = {}
    else:
        obj = {
            "summary": "",
            "keypoints": [],
            "sections": [{"heading": "讲稿", "content": "\n\n".join(paras[:3])}],
            "title": title,
            "duration": data.get("duration") or 0,
        }
    obj["readable"] = paras
    obj["readable_model"] = MODEL
    obj["readable_ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    obj.setdefault("title", title)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    with _lock:
        _counter["done"] += 1
        n = _counter["done"]
    return f"[完成 {n}] {jp.parent.name} / {title} · {len(paras)} 段"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--module", type=str, default=None)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    todo = collect(args.module, args.force)
    if args.limit:
        todo = todo[: args.limit]
    print(f"待生成通顺讲稿 {len(todo)} · 模型 {MODEL}", flush=True)
    if not todo:
        print("没有待处理课时。")
        return

    start = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(worker, jp, out) for jp, out in todo]
        for i, fut in enumerate(as_completed(futs), 1):
            print(fut.result(), flush=True)
            if i % 20 == 0:
                rate = i / max(time.time() - start, 1) * 60
                left = (len(todo) - i) / max(rate, 0.1)
                print(f"  … {i}/{len(todo)} · {rate:.1f}/分 · 预计剩余 {left:.0f} 分", flush=True)

    print(f"\n结束：完成 {_counter['done']} · 失败 {_counter['fail']} · 用时 {(time.time()-start)/60:.1f} 分")


if __name__ == "__main__":
    main()
