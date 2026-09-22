#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 论文抓取脚本 —— 供「AI 论文精读台」每日自动化复用。

数据源：
  1. HuggingFace Daily Papers（社区热度排序，含 upvotes）
  2. arXiv API（cs.AI / cs.CL / cs.LG / cs.CV / stat.ML 最新提交）

用法：
  python3 fetch_papers.py [--days 2] [--per-cat 25] [--out papers.json]

输出：JSON 数组，每项含
  id, title, authors, affiliation, summary, url, published, source, upvotes
"""

import argparse
import json
import re
import ssl
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " \
     "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
CTX = ssl.create_default_context()
ARXIV_CATS = ["cs.AI", "cs.CL", "cs.LG", "cs.CV", "stat.ML"]
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"


def http_get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as resp:
        return resp.read().decode("utf-8", "ignore")


def clean(s):
    if not s:
        return ""
    return re.sub(r"\s+", " ", str(s)).strip()


def fetch_hf(days):
    """HuggingFace Daily Papers：按天拉取，带社区点赞数。"""
    out = []
    today = datetime.now(timezone(timedelta(hours=8))).date()
    # 先按天精确拉取；当天榜单尚未生成（400/404）时，回退到「当前热门榜」
    fallback_done = False
    for i in range(days):
        d = (today - timedelta(days=i)).isoformat()
        try:
            raw = http_get("https://huggingface.co/api/daily_papers?date=%s" % d)
            data = json.loads(raw)
        except Exception as e:
            sys.stderr.write("[hf %s] %s\n" % (d, e))
            if fallback_done:
                continue
            fallback_done = True
            try:
                raw = http_get("https://huggingface.co/api/daily_papers?limit=50")
                data = json.loads(raw)
            except Exception as e2:
                sys.stderr.write("[hf fallback] %s\n" % e2)
                continue
        for item in data:
            p = item.get("paper") or {}
            pid = clean(p.get("id"))
            if not pid:
                continue
            authors = [a.get("name", "") for a in (p.get("authors") or [])]
            out.append({
                "id": pid,
                "title": clean(p.get("title")),
                "authors": [a for a in authors if a],
                "affiliation": "",
                "summary": clean(p.get("summary"))[:1200],
                "url": "https://arxiv.org/abs/%s" % pid if pid.replace(".", "").isdigit()
                       else "https://huggingface.co/papers/%s" % pid,
                "published": clean(p.get("publishedAt"))[:10] or d,
                "source": "HuggingFace Daily Papers",
                "upvotes": int(p.get("upvotes") or 0),
            })
    return out


def fetch_arxiv(days, per_cat):
    """arXiv API：按分类拉取最近 N 天提交，按提交时间降序。"""
    seen = {}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days + 1)).date()
    for cat in ARXIV_CATS:
        q = urllib.parse.urlencode({
            "search_query": "cat:%s" % cat,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "max_results": per_cat,
        })
        try:
            raw = http_get("https://export.arxiv.org/api/query?%s" % q)
            root = ET.fromstring(raw)
        except Exception as e:
            sys.stderr.write("[arxiv %s] %s\n" % (cat, e))
            continue
        for entry in root.findall(ATOM + "entry"):
            eid = clean(entry.findtext(ATOM + "id"))
            pid = eid.rsplit("/", 1)[-1] if eid else ""
            if not pid or pid in seen:
                continue
            pub = clean(entry.findtext(ATOM + "published"))[:10]
            try:
                if pub and datetime.strptime(pub, "%Y-%m-%d").date() < cutoff:
                    continue
            except ValueError:
                pass
            authors = [clean(a.findtext(ATOM + "name")) for a in entry.findall(ATOM + "author")]
            affs = [clean(a.findtext(ARXIV_NS + "affiliation")) for a in entry.findall(ATOM + "author")]
            affs = [a for a in affs if a]
            cats = [c.get("term") for c in entry.findall(ATOM + "category")]
            seen[pid] = {
                "id": pid,
                "title": clean(entry.findtext(ATOM + "title")),
                "authors": [a for a in authors if a],
                "affiliation": affs[0] if affs else "",
                "summary": clean(entry.findtext(ATOM + "summary"))[:1200],
                "url": "https://arxiv.org/abs/%s" % pid,
                "published": pub,
                "source": "arXiv %s" % (",".join(cats[:2]) if cats else cat),
                "upvotes": 0,
            }
    return list(seen.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--per-cat", type=int, default=25)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    papers = fetch_hf(args.days) + fetch_arxiv(args.days, args.per_cat)

    # 合并去重（同一 arXiv id 优先保留带 upvotes 的那条）
    merged = {}
    for p in papers:
        key = p["id"]
        old = merged.get(key)
        if old is None or p.get("upvotes", 0) > old.get("upvotes", 0):
            if old:
                p["upvotes"] = max(p.get("upvotes", 0), old.get("upvotes", 0))
                if not p.get("affiliation"):
                    p["affiliation"] = old.get("affiliation", "")
            merged[key] = p

    result = sorted(merged.values(),
                    key=lambda x: (x.get("upvotes", 0), x.get("published", "")),
                    reverse=True)
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        sys.stderr.write("saved %d papers -> %s\n" % (len(result), args.out))
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
