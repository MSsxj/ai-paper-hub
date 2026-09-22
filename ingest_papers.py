#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把论文记录写入资料库数据表 —— 供「AI 论文精读台」每日自动化复用。

用法：
  python3 ingest_papers.py <token> <database_id> <records.json>

records.json 是数组，每项为 map<字段名, PropertyValue>。
内部通过 stdin 首行传 token 给 batch_add_database_records.py。
"""

import json
import os
import subprocess
import sys

# 资料库 skill 脚本目录，可用环境变量 LIBRARY_SKILL_DIR 覆盖
SKILL_DIR = os.environ.get(
    "LIBRARY_SKILL_DIR",
    "/Applications/WorkBuddy.app/Contents/Resources/app.asar.unpacked/"
    "resources/plugins/workbuddy-builtin/skills/library",
)
SCRIPT = os.path.join(SKILL_DIR, "database", "batch_add_database_records.py")


def main():
    if len(sys.argv) < 4:
        sys.stderr.write("usage: ingest_papers.py <token> <database_id> <records.json>\n")
        return 2
    token, database_id, records_path = sys.argv[1], sys.argv[2], sys.argv[3]
    with open(records_path, encoding="utf-8") as f:
        records = json.load(f)
    if not isinstance(records, list) or not records:
        sys.stderr.write("records.json 必须是非空数组\n")
        return 2

    # 每批最多 100 条（脚本上限）
    ok, fail = 0, []
    for i in range(0, len(records), 100):
        batch = records[i:i + 100]
        cmd = [sys.executable, SCRIPT, "--token-stdin",
               "--database-id", database_id,
               "--records", json.dumps(batch, ensure_ascii=False)]
        p = subprocess.run(cmd, input=token + "\n", capture_output=True, text=True)
        out = (p.stdout or "").strip()
        try:
            data = json.loads(out)
        except Exception:
            sys.stderr.write("batch %d 无法解析输出: %s %s\n" % (i, out[:200], p.stderr[:200]))
            fail.append({"batch": i, "error": out[:200]})
            continue
        if "error" in data:
            fail.append({"batch": i, "error": data["error"]})
            continue
        for r in data.get("results", []):
            if r.get("success"):
                ok += 1
            else:
                fail.append(r)
    print(json.dumps({"added": ok, "failed": fail}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
