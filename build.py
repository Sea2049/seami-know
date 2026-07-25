# -*- coding: utf-8 -*-
"""
毅冰业务课知识库 · 静态站点生成器
================================
数据源：e:\\BaiduNetdiskDownload\\01.毅冰业务课2025（新版）\\_转录与细节PDF\\transcripts\\**.json
输出：  site/ （纯静态，可直接部署或本地打开）

更新方法：
    python build.py          # 增量同步全部模块（转写有新增时重跑即可）
"""
from __future__ import annotations

import html
import json
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
TRANSCRIPTS = Path(r"e:\BaiduNetdiskDownload\01.毅冰业务课2025（新版）\_转录与细节PDF\transcripts")
STATE_FILE = Path(r"e:\BaiduNetdiskDownload\01.毅冰业务课2025（新版）\_转录与细节PDF\_state.json")
REFINED = ROOT / "refined"   # GLM 精炼结果（refine.py 产出）
SITE = ROOT / "site"
ASSETS = ROOT / "assets"

# ---------------------------------------------------------------- 模块元数据
# order, slug, 层级, 一句话导读（可自行编辑补充）
MODULE_META = [
    ("1.导学：按图索骥，建立专业化外贸体系", "m01-daoxue", "L0 底层思维",
     "为什么推倒 639 节旧课重来；专业化外贸体系究竟学什么、怎么学、如何落地。"),
    ("2.选品技巧+供应链剖析", "m02-xuanpin", "L1 供给能力",
     "选品逻辑与「2+1」思路，供应商识别、匹配与 Plan B，构建可控的供应链。"),
    ("3.学会多元化开发客户", "m03-kaifa", "L2 获客",
     "从宏观到微观的客户调研量化方法，Buying Team 运作机制与 Google 开发工具。"),
    ("4.Mail Group专业开发，必须跟原创者学", "m04-mailgroup", "L2 获客",
     "毅冰原创的邮件序列开发引擎：极简写法、价值传递与 Call to Action。"),
    ("5.五  改变思维定势", "m05-siwei", "L0 底层思维",
     "打破「等询盘、拼价格、靠平台」的默认路径，重建外贸新思维。"),
    ("6.六  Linkedln领英社媒开发", "m06-linkedin", "L2 获客",
     "领英获客全链路：账号打造、精准搜索、私信转化与个人品牌。"),
    ("7.七  细节化客户开发", "m07-xijie", "L3 转化",
     "邮件的专业气质、样品攻防战与展会全流程——细节决定成交。"),
    ("9.九  付款与风控", "m09-fengkong", "L5 资金",
     "信用证全解析、TT/OA/PayPal 等结算方式的选择与风险控制。"),
    ("10.十  专业订单处理", "m10-dingdan", "L4 交付",
     "全球认证检测地图、跟单监装、索赔危机公关与大买家验厂门道。"),
    ("11.十一  团队管理与产品选择", "m11-tuandui", "L6 经营",
     "离岸公司操作、高效团队打造、产品线战略与职业规划。"),
    ("12.十二  分层次市场开拓", "m12-shichang", "L6 经营",
     "美国、欧洲、中东、澳新、亚洲市场的分层打法与合规要点。"),
    ("13.十三  辅助技能提升", "m13-jineng", "L2 获客",
     "外贸英语口语电话、采购办视角、职场进阶与效率工具箱。"),
    ("14.十四  毅冰·Friends", "m14-friends", "L4 交付",
     "进口商、货代经理、风控专家等嘉宾视角，补齐单一讲师盲区。"),
    ("15.十五  毅冰年度公开课", "m15-gongkaike", "L0 底层思维",
     "历年公开课：疫情突围、汇率破局、管理思维、LinkedIn 出单实战。"),
    ("14.【米课内部资料】24年和20年出单手册等多个文件", "m16-neibu", "L6 经营",
     "内部资料课包：独立站、TikTok、外贸 0 到 1 全流程等增量渠道。"),
]

SHORT_OVERRIDES = {
    "m16-neibu": "米课内部资料 · 出单手册与增量渠道",
}

KEYWORDS = [
    "关键", "核心", "必须", "一定", "不要", "注意", "方法", "步骤", "原则",
    "首先", "其次", "总结", "重点", "策略", "技巧", "标准", "流程", "体系",
    "客户", "开发", "谈判", "报价", "样品", "信用证", "付款", "供应链", "选品",
    "LinkedIn", "邮件", "展会", "验厂", "索赔", "为什么", "怎么", "如何",
]


def esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def find_module_dir(name: str) -> Path | None:
    if not TRANSCRIPTS.exists():
        return None
    target = norm(name)
    for d in TRANSCRIPTS.iterdir():
        if not d.is_dir():
            continue
        n = norm(d.name)
        if n == target or target in n or n in target:
            return d
    m = re.match(r"^(\d+)\.", name.strip())
    if m:
        for d in TRANSCRIPTS.iterdir():
            if d.is_dir() and d.name.startswith(m.group(1) + "."):
                if ("内部" in name) == ("内部" in d.name):
                    return d
    return None


# 口语停顿词：用来把长段软切成可读短句
SOFT_BREAKS = re.compile(
    r"(?<=[。！？；!?])|"
    r"(?<=[，,])(?=(?:然后|所以|但是|不过|另外|其实|比如说|比如说|首先|其次|最后|对不对|对吧|是吧|好吧))"
)

FILLER = re.compile(
    r"(对不对|对吧|是吧|好吧|嗯|啊|呃|那个|就是说)+$"
)


def fmt_ts(seconds: float) -> str:
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h:d}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


def clean_chunk(text: str) -> str:
    t = re.sub(r"\s+", "", (text or "").strip())
    t = FILLER.sub("", t)
    return t


def soft_split_long(text: str, soft_max: int = 56) -> list[str]:
    """把没有句号的口语长串切成可读短句。"""
    t = clean_chunk(text)
    if not t:
        return []
    if len(t) <= soft_max:
        return [t]

    # 先按标点切
    pieces = [p for p in re.split(r"(?<=[。！？；!?;，,])", t) if p]
    out: list[str] = []
    buf = ""
    for p in pieces:
        if not buf:
            buf = p
        elif len(buf) + len(p) <= soft_max:
            buf += p
        else:
            out.append(buf)
            buf = p
        # 缓冲仍过长时，用停顿词继续切
        while len(buf) > soft_max * 1.6:
            # 在中间找停顿词
            mid = soft_max
            window = buf[soft_max // 2: soft_max + 20]
            cut = None
            for marker in ("然后", "所以", "但是", "另外", "其实", "对不对", "对吧", "比如说"):
                i = window.find(marker)
                if i >= 0:
                    cut = soft_max // 2 + i
                    break
            if cut is None:
                cut = soft_max
            out.append(buf[:cut])
            buf = buf[cut:]
    if buf:
        out.append(buf)
    return [x for x in out if x]


def segments_to_paragraphs(segments: list[dict], pause: float = 0.75, max_chars: int = 68) -> list[dict]:
    """按停顿/标点/长度把 whisper segments 合成可读段落。"""
    paras: list[dict] = []
    buf_texts: list[str] = []
    buf_start = 0.0
    buf_end = 0.0
    prev_end = None

    def flush():
        nonlocal buf_texts, buf_start, buf_end
        if not buf_texts:
            return
        joined = "".join(buf_texts)
        for piece in soft_split_long(joined, soft_max=max_chars):
            paras.append({"start": buf_start, "text": piece})
        buf_texts = []

    for seg in segments:
        text = clean_chunk(seg.get("text") or "")
        if not text:
            continue
        start = float(seg.get("start") or 0)
        end = float(seg.get("end") or start)
        gap = (start - prev_end) if prev_end is not None else 0
        ends_sentence = bool(re.search(r"[。！？!?]$", text))
        would = sum(len(x) for x in buf_texts) + len(text)

        if buf_texts and (gap >= pause or would > max_chars or ends_sentence and would > 40):
            flush()

        if not buf_texts:
            buf_start = start
        buf_texts.append(text)
        buf_end = end
        prev_end = end
        if ends_sentence and sum(len(x) for x in buf_texts) >= 28:
            flush()

    flush()
    return paras


def text_to_paragraphs(text: str) -> list[dict]:
    """无 segments 时，从纯文本切段。"""
    lines = [clean_chunk(ln) for ln in (text or "").splitlines()]
    lines = [ln for ln in lines if ln]
    if not lines:
        # 一整坨
        chunks = soft_split_long(text or "", soft_max=80)
        return [{"start": 0.0, "text": c} for c in chunks]
    # 按行合并
    paras: list[dict] = []
    buf = ""
    for ln in lines:
        if not buf:
            buf = ln
        elif len(buf) + len(ln) <= 80:
            buf += ln
        else:
            for piece in soft_split_long(buf, soft_max=80):
                paras.append({"start": 0.0, "text": piece})
            buf = ln
    if buf:
        for piece in soft_split_long(buf, soft_max=80):
            paras.append({"start": 0.0, "text": piece})
    return paras


def extract_keypoints(paragraphs: list[dict], max_points: int = 6) -> list[str]:
    def clip(s: str, n: int = 56) -> str:
        s = re.sub(r"(对不对|对吧|是吧)+", "", s)
        if len(s) <= n:
            return s
        # 尽量在标点处截断
        cut = max((s.rfind(ch, 0, n) for ch in "，。；、 "), default=-1)
        if cut < n // 2:
            cut = n
        return s[:cut].rstrip("，、； ") + "…"

    scored, seen = [], set()
    for p in paragraphs:
        raw = p["text"]
        if len(raw) < 12:
            continue
        if sum(raw.count(w) for w in ("对不对", "对吧", "是吧")) >= 2:
            continue
        s = clip(raw)
        key = s[:14]
        if key in seen:
            continue
        score = sum(1 for k in KEYWORDS if k.lower() in raw.lower())
        seen.add(key)
        scored.append((score * 10 + (40 - abs(len(s) - 36)) / 10, s, score))
    scored.sort(key=lambda x: -x[0])
    # 优先有关键词的
    points = [x[1] for x in scored if x[2] > 0][:max_points]
    if len(points) < 4:
        for _, s, _ in scored:
            if s not in points:
                points.append(s)
            if len(points) >= max_points:
                break
    if len(points) < 3 and paragraphs:
        step = max(1, len(paragraphs) // 5)
        for i in range(0, len(paragraphs), step):
            s = clip(paragraphs[i]["text"])
            if len(s) >= 12 and s not in points:
                points.append(s)
            if len(points) >= 5:
                break
    return points[:max_points]


def render_prose(paragraphs: list[dict]) -> str:
    """可读讲稿：每段一段，开头挂时间戳。"""
    if not paragraphs:
        return "<p class='empty'>（暂无转写）</p>"
    parts = []
    for p in paragraphs:
        ts = fmt_ts(p["start"])
        parts.append(
            f'<p class="para"><time datetime="{ts}">{ts}</time>'
            f'<span>{esc(p["text"])}</span></p>'
        )
    return "".join(parts)


def natural_key(s: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def load_lessons(mod_dir: Path) -> list[dict]:
    lessons = []
    refined_dir = REFINED / mod_dir.name
    for jp in sorted(mod_dir.glob("*.json"), key=lambda p: natural_key(p.stem)):
        try:
            data = json.loads(jp.read_text(encoding="utf-8"))
        except Exception:
            continue
        text = data.get("text") or ""
        title = data.get("title") or jp.stem
        duration = data.get("duration") or 0

        rp = refined_dir / (jp.stem + ".json")
        refined = None
        if rp.exists():
            try:
                r = json.loads(rp.read_text(encoding="utf-8"))
                if r.get("sections"):
                    refined = {
                        "summary": r.get("summary", ""),
                        "keypoints": [k for k in r.get("keypoints", []) if k.strip()],
                        "sections": [s for s in r.get("sections", [])
                                     if s.get("heading") and s.get("content")],
                    }
            except Exception:
                refined = None

        # 原始转写段落（作为“查看原始转写”折叠内容 / 未精炼时的回退）
        segs = data.get("segments") or []
        paras = segments_to_paragraphs(segs) if segs else text_to_paragraphs(text)

        lessons.append({
            "title": title,
            "duration": duration,
            "text": text,
            "paragraphs": paras,
            "refined": refined,
        })
    return lessons


# ---------------------------------------------------------------- HTML 模板

def page_shell(title: str, body: str, root_prefix: str = "") -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<link rel="stylesheet" href="{root_prefix}assets/style.css">
</head>
<body>
<nav class="topnav">
  <a href="{root_prefix}index.html" class="brand">毅冰业务课 · 知识库</a>
  <span class="nav-note">视频转写 · 结构化笔记 · 持续更新</span>
</nav>
{body}
<footer class="footer">
  <p>本知识库由课程视频语音转写自动构建 · 口语转写偶有同音字，不影响理解。</p>
  <p>更新方式：转写完成后运行 <code>python build.py</code> 重新生成。最后构建：{time.strftime("%Y-%m-%d %H:%M")}</p>
</footer>
</body>
</html>"""


def fmt_min(seconds: float) -> str:
    m = seconds / 60
    return f"{m:.0f} 分钟" if m >= 1 else "1 分钟"


def build_module_page(name: str, slug: str, layer: str, intro: str, lessons: list[dict]) -> str:
    total_min = sum(l["duration"] for l in lessons) / 60
    total_chars = sum(len(l["text"]) for l in lessons)

    toc_items = []
    for i, les in enumerate(lessons, 1):
        toc_items.append(
            f'<li><a href="#lesson-{i}">{esc(les["title"])}</a>'
            f'<span class="toc-min">{fmt_min(les["duration"])}</span></li>'
        )

    sections = []
    refined_count = 0
    for i, les in enumerate(lessons, 1):
        paras = les["paragraphs"]
        r = les["refined"]
        if r:
            refined_count += 1
            kps = r["keypoints"]
            kp_html = "".join(f"<li>{esc(k)}</li>" for k in kps) if kps else ""
            summary_html = (f'<p class="lesson-summary">{esc(r["summary"])}</p>'
                            if r.get("summary") else "")
            body_secs = "".join(
                f'<div class="note-sec"><h4>{esc(s["heading"])}</h4>'
                f'<p>{esc(s["content"])}</p></div>'
                for s in r["sections"]
            )
            kp_block = (f'<div class="keypoints"><h3>精华要点</h3><ul>{kp_html}</ul></div>'
                        if kps else "")
            raw_html = render_prose(paras)
            sections.append(f"""
<section class="lesson" id="lesson-{i}">
  <h2><span class="lesson-no">{i:02d}</span>{esc(les["title"])}</h2>
  <p class="lesson-meta">{fmt_min(les["duration"])} · {len(r["sections"])} 节整理 · {len(kps)} 要点</p>
  {summary_html}
  {kp_block}
  <div class="notes">{body_secs}</div>
  <details class="transcript">
    <summary>查看逐字原文（{len(paras)} 段）</summary>
    <div class="transcript-body">{raw_html}</div>
  </details>
</section>""")
        else:
            kps = extract_keypoints(paras)
            kp_html = "".join(f"<li>{esc(k)}</li>" for k in kps) if kps else "<li>本课以叙述为主，请直接阅读下方讲稿。</li>"
            prose_html = render_prose(paras)
            sections.append(f"""
<section class="lesson" id="lesson-{i}">
  <h2><span class="lesson-no">{i:02d}</span>{esc(les["title"])}</h2>
  <p class="lesson-meta">{fmt_min(les["duration"])} · {len(paras)} 段讲稿 · 约 {len(les["text"])} 字 · <span class="pending-tag">待精炼</span></p>
  <div class="keypoints">
    <h3>本课要点</h3>
    <ul>{kp_html}</ul>
  </div>
  <div class="prose">
    <h3>讲稿正文</h3>
    {prose_html}
  </div>
</section>""")

    display_name = re.sub(r"^[\d.]+", "", name).strip() or name
    if refined_count == len(lessons):
        refined_note = " · 已精炼整理"
    elif refined_count:
        refined_note = f" · 已精炼 {refined_count}/{len(lessons)}"
    else:
        refined_note = ""
    body = f"""
<main class="module-page">
  <header class="module-header">
    <p class="layer-tag">{esc(layer)}</p>
    <h1>{esc(display_name)}</h1>
    <p class="module-intro">{esc(intro)}</p>
    <p class="module-stats">{len(lessons)} 课 · 共约 {total_min:.0f} 分钟 · 转写 {total_chars:,} 字{refined_note}</p>
  </header>
  <aside class="toc">
    <h3>课时目录</h3>
    <ol>{"".join(toc_items)}</ol>
  </aside>
  <div class="lessons">{"".join(sections)}</div>
  <p class="backlink"><a href="../index.html">← 返回知识库首页</a></p>
</main>"""
    return page_shell(f"{name} · 毅冰知识库", body, root_prefix="../")


def build_index(modules: list[dict], search_entries: list[dict]) -> str:
    total_lessons = sum(m["count"] for m in modules)
    total_min = sum(m["minutes"] for m in modules)
    total_chars = sum(m["chars"] for m in modules)

    layers: dict[str, list[dict]] = {}
    for m in modules:
        layers.setdefault(m["layer"], []).append(m)

    layer_order = ["L0 底层思维", "L1 供给能力", "L2 获客", "L3 转化", "L4 交付", "L5 资金", "L6 经营"]
    shelf_html = []
    for layer in layer_order:
        mods = layers.get(layer)
        if not mods:
            continue
        cards = []
        for m in mods:
            status = f'{m["count"]} 课'
            cards.append(f"""
<a class="book" href="modules/{m["slug"]}.html">
  <span class="book-layer">{esc(layer)}</span>
  <span class="book-title">{esc(m["short"])}</span>
  <span class="book-desc">{esc(m["intro"])}</span>
  <span class="book-meta">{status} · 约 {m["minutes"]:.0f} 分钟</span>
</a>""")
        shelf_html.append(f"""
<section class="shelf">
  <h2>{esc(layer)}</h2>
  <div class="books">{"".join(cards)}</div>
</section>""")

    body = f"""
<main class="index-page">
  <header class="hero">
    <h1>毅冰业务课<br>知识库</h1>
    <p class="hero-sub">把 45GB 的课程视频，读成一座可检索、可回看的图书馆。</p>
    <p class="hero-essay">
      看完一门课，几周后还记得多少？大多数人只剩下几句模糊印象——不是你不认真，
      而是「听完即忘」是视频这种媒介的默认结局。这个知识库把全部课程逐字转写、
      提炼要点、按外贸成交价值链重新组织：底层思维 → 供给 → 获客 → 转化 → 交付 → 资金 → 经营。
      每一课都能检索、能回看原文、能对照时间戳回到视频。
    </p>
    <p class="hero-stats"><strong>{total_lessons}</strong> 课已转写 · <strong>{total_min/60:.0f}</strong> 小时课程 · <strong>{total_chars/10000:.0f}</strong> 万字全文</p>
  </header>

  <section class="search-box">
    <input type="search" id="search" placeholder="搜索全部课程标题与要点，如：信用证 / 样品费 / LinkedIn…" autocomplete="off">
    <div id="results"></div>
  </section>

  {"".join(shelf_html)}
</main>
<script>
const INDEX_URL = 'search-index.json';
let idx = null;
const input = document.getElementById('search');
const out = document.getElementById('results');
async function ensureIndex() {{
  if (!idx) idx = await (await fetch(INDEX_URL)).json();
  return idx;
}}
let t = null;
input.addEventListener('input', () => {{
  clearTimeout(t);
  t = setTimeout(run, 160);
}});
async function run() {{
  const q = input.value.trim().toLowerCase();
  if (q.length < 2) {{ out.innerHTML = ''; return; }}
  const data = await ensureIndex();
  const hits = [];
  for (const e of data) {{
    const hay = (e.t + ' ' + e.k).toLowerCase();
    if (hay.includes(q)) {{
      hits.push(e);
      if (hits.length >= 30) break;
    }}
  }}
  out.innerHTML = hits.length
    ? hits.map(e => `<a class="hit" href="modules/${{e.m}}.html#lesson-${{e.i}}"><span class="hit-mod">${{e.mn}}</span>${{e.t}}</a>`).join('')
    : '<p class="no-hit">没有找到，换个关键词试试。</p>';
}}
</script>"""
    return page_shell("毅冰业务课 · 知识库", body)


# ---------------------------------------------------------------- 主流程

def main():
    sys.stdout.reconfigure(encoding="utf-8")
    if not TRANSCRIPTS.exists():
        raise SystemExit(f"未找到转写目录: {TRANSCRIPTS}")

    SITE.mkdir(exist_ok=True)
    (SITE / "modules").mkdir(exist_ok=True)
    (SITE / "assets").mkdir(exist_ok=True)
    shutil.copy(ASSETS / "style.css", SITE / "assets" / "style.css")

    modules_out = []
    search_entries = []

    for name, slug, layer, intro in MODULE_META:
        mod_dir = find_module_dir(name)
        if not mod_dir:
            print(f"[跳过] 尚无转写: {name}")
            continue
        lessons = load_lessons(mod_dir)
        if not lessons:
            print(f"[跳过] 无课时: {name}")
            continue

        page = build_module_page(name, slug, layer, intro, lessons)
        (SITE / "modules" / f"{slug}.html").write_text(page, encoding="utf-8")

        short = SHORT_OVERRIDES.get(slug) or re.sub(r"^[\d.]+", "", name).strip()
        short = re.sub(r"^[一二三四五六七八九十]+\s*", "", short)
        modules_out.append({
            "name": name, "slug": slug, "layer": layer, "intro": intro,
            "short": short,
            "count": len(lessons),
            "minutes": sum(l["duration"] for l in lessons) / 60,
            "chars": sum(len(l["text"]) for l in lessons),
        })

        for i, les in enumerate(lessons, 1):
            if les["refined"]:
                kws = les["refined"]["keypoints"] + [
                    s["heading"] for s in les["refined"]["sections"]]
                if les["refined"].get("summary"):
                    kws.insert(0, les["refined"]["summary"])
            else:
                kws = extract_keypoints(les["paragraphs"], max_points=4)
            search_entries.append({
                "m": slug, "mn": short, "i": i,
                "t": les["title"], "k": " ".join(kws)[:400],
            })
        print(f"[完成] {name}: {len(lessons)} 课")

    (SITE / "search-index.json").write_text(
        json.dumps(search_entries, ensure_ascii=False), encoding="utf-8")
    (SITE / "index.html").write_text(build_index(modules_out, search_entries), encoding="utf-8")

    print(f"\n站点已生成: {SITE}")
    print(f"模块 {len(modules_out)} 个 · 课时 {sum(m['count'] for m in modules_out)} 节")
    print("本地预览: python -m http.server 8788 --directory site")


if __name__ == "__main__":
    main()
