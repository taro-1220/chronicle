#!/usr/bin/env python3
"""4対象プロジェクト全てについてgit履歴を収集し、
date -> project -> [{hash, subject}, ...]（日内は時系列昇順）のJSONと、
fetch状況・未マージブランチ候補のメタレポートを出力する。

リモートfetch失敗や取得不能があれば complete=False として明示し、
呼び出し側（update_daily.py / verify_chronicle.py）がそれを「未確定」として
扱えるようにする。
"""
import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
import chronicle_lib as cl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", required=True, help="date->project->commits のJSON出力先")
    ap.add_argument("--meta-out", required=True, help="fetch状況・未マージブランチ候補のJSON出力先")
    args = ap.parse_args()

    start_date = datetime.strptime(args.start, "%Y-%m-%d").date()
    end_date = datetime.strptime(args.end, "%Y-%m-%d").date()

    by_date = {}
    meta_report = {}
    any_incomplete = False

    for project in cl.PROJECTS:
        name = project["name"]
        commits, meta = cl.collect_commits(project, start_date, end_date)
        meta_report[name] = meta
        if not meta["complete"]:
            any_incomplete = True

        # git log出力は新しい→古いの順。日付でグルーピングしつつ元の順序を保持し、
        # 各日のリストを反転させれば日内は古い→新しい（時系列昇順）になる。
        grouped = {}
        for c in commits:
            grouped.setdefault(c["date"], []).append(c)
        for d, lst in grouped.items():
            lst.reverse()
            by_date.setdefault(d, {})[name] = [
                {"hash": c["hash"], "subject": c["subject"]} for c in lst
            ]

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(by_date, f, ensure_ascii=False, indent=2)
    with open(args.meta_out, "w", encoding="utf-8") as f:
        json.dump(meta_report, f, ensure_ascii=False, indent=2)

    print("=== fetch / 取得状況 ===")
    for name, m in meta_report.items():
        status = "OK" if m["complete"] else "**不完全**"
        print(f"{name}: {status} | fetch: {m['fetch_ok']} ({m['fetch_message']}) | ref: {m['ref_mode']}")
        if m.get("log_err"):
            print(f"  log error: {m['log_err']}")
        for u in m.get("unmerged_branches", []):
            if "error" in u:
                print(f"  [unmerged検出エラー] {u['error']}")
            else:
                print(f"  [要確認] {u['branch']}: {u['commit_count']}件 (日付: {', '.join(u['dates'])})")

    total_commits = sum(len(v) for day in by_date.values() for v in day.values())
    print(f"\n総コミット数: {total_commits}")
    print(f"対象日数（コミットがあった日）: {len(by_date)}")
    if any_incomplete:
        print("\n**警告: 一部プロジェクトの取得が不完全です。この結果を確定扱いしないでください。**")
        sys.exit(2)


if __name__ == "__main__":
    main()
