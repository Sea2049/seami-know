# 毅冰业务课 · 知识库（seami-know）

在线阅读：[https://sea2049.github.io/seami-know/](https://sea2049.github.io/seami-know/)

参考 [andymatuschak.org/books](https://andymatuschak.org/books/) 的阅读型排版，把课程视频转写整理成可检索、可持续更新的静态知识库。

## 内容结构

- `site/` — 静态站点（GitHub Pages 直接发布这一目录）
- `refined/` — GLM 精炼后的书面笔记缓存
- `build.py` — 从转写/精炼结果生成站点
- `refine.py` — 口语转写 → 书面笔记 + 精华要点（需智谱 API Key）
- `assets/` — 样式源文件

## 本地预览

```powershell
python -m http.server 8788 --directory site
# 打开 http://localhost:8788
```

## 更新站点

```powershell
# 1) 有新转写时先精炼（需设置 Z_AI_API_KEY）
$env:Z_AI_API_KEY = "你的智谱Key"
python refine.py

# 2) 重建静态页
python build.py

# 3) 提交并推送（会自动触发 GitHub Pages 部署）
git add site refined
git commit -m "update knowledge site"
git push
```

## 部署

推送到 `main` 后，`.github/workflows/pages.yml` 会把 `site/` 发布到 GitHub Pages。
