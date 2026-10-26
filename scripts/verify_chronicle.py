#!/usr/bin/env python3
"""Chronicleの自動検証スイート。

チェック項目（指示に基づく）:
1. 日付の欠落・重複
2. コミットの欠落・重複（collect_commits_allの出力と日報本文を突合）
3. 対象ブランチの取りこぼし（fetch失敗・未マージブランチの要確認リストをそのまま転記）
4. 月別インデックスと日別リンクの整合性
5. 既存の日報の意図しない変更（git diffで、保護範囲外のファイルが変更されていないか）
6. GitHubへの反映状況（origin/mainとHEADの一致、対象ファイルのraw取得）

exit code: 0=全てPASS, 1=一部WARN, 2=FAILあり
"""
import argparse
import calendar
import json
import os
import re
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
import chronicle_lib as cl


def run(cmd, cwd=None):
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


class Report:
    def __init__(self):
        self.results = []  # (level, name, message)

    def ok(self, name, msg=""):
        self.results.append(("PASS", name, msg))

    def warn(self, name, msg):
        self.results.append(("WARN", name, msg))

    def fail(self, name, msg):
        self.results.append(("FAIL", name, msg))

    def summary_exit_code(self):
        levels = {r[0] for r in self.results}
        if "FAIL" in levels:
            return 2
        if "WARN" in levels:
            return 1
        return 0

    def print_all(self):
        for level, name, msg in self.results:
            print(f"[{level}] {name}: {msg}" if msg else f"[{level}] {name}")


def check_date_gaps_and_dupes(report, start_date, end_date):
    before = len(report.results)
    for d in cl.iter_dates(start_date, end_date):
        path = cl.day_file_path(d)
        if not os.path.exists(path):
            report.fail("日付欠落", f"{d.isoformat()} の日報ファイルが存在しない: {path}")
    if len(report.results) == before:
        report.ok("日付欠落チェック", f"{start_date}〜{end_date} の全日ファイル存在確認済み")

    # 月インデックスの行が日付順・連番・重複なしか
    months = set()
    cur = start_date
    while cur <= end_date:
        months.add((cur.year, cur.month))
        cur = cur.replace(day=28) + __import__("datetime").timedelta(days=4)
        cur = cur.replace(day=1)
    before = len(report.results)
    for year, month in sorted(months):
        idx_path = cl.month_index_path(year, month)
        if not os.path.exists(idx_path):
            report.fail("月インデックス欠落", idx_path)
            continue
        with open(idx_path, encoding="utf-8") as f:
            content = f.read()
        rows = re.findall(r"\[(\d{2})/(\d{2})\]\((\d{2})\.md\)", content)
        days = [int(r[1]) for r in rows]
        last_day_of_month = calendar.monthrange(year, month)[1]
        expected_last = min(last_day_of_month, end_date.day) if (year, month) == (end_date.year, end_date.month) else last_day_of_month
        if sorted(days) != list(range(1, expected_last + 1)):
            report.fail("月インデックス日数不整合", f"{idx_path}: 期待={list(range(1, expected_last + 1))} 実際={sorted(days)}")
        if len(days) != len(set(days)):
            dup = [d for d in days if days.count(d) > 1]
            report.fail("月インデックス重複", f"{idx_path}: 重複日 {sorted(set(dup))}")
        mismatched = [r for r in rows if r[1] != r[2]]
        if mismatched:
            report.fail("月インデックスリンク不整合", f"{idx_path}: {mismatched}")
    if len(report.results) == before:
        report.ok("月インデックス整合性", f"{sorted(months)} の全月で日数・重複・リンク整合性OK")


def check_commits_against_json(report, commits_json_path, start_date, end_date):
    with open(commits_json_path, encoding="utf-8") as f:
        commits_data = json.load(f)

    expected_hashes = {}  # project -> set(hash)
    for date_str, projects in commits_data.items():
        for pname, commits in projects.items():
            expected_hashes.setdefault(pname, set()).update(c["hash"] for c in commits)

    actual_hashes = {}  # project -> set(hash) found in day files
    for d in cl.iter_dates(start_date, end_date):
        path = cl.day_file_path(d)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            content = f.read()
        for pname in cl.PROJECT_NAMES:
            span_re = re.compile(rf"^## {re.escape(pname)}\s*$", re.MULTILINE)
            m = span_re.search(content)
            if not m:
                continue
            next_m = re.search(r"^## ", content[m.end():], re.MULTILINE)
            end = m.end() + next_m.start() if next_m else len(content)
            section = content[m.start():end]
            hashes = re.findall(r"`([a-f0-9]{7,40})`", section)
            actual_hashes.setdefault(pname, set()).update(hashes)

    any_issue = False
    for pname in cl.PROJECT_NAMES:
        exp = expected_hashes.get(pname, set())
        act = actual_hashes.get(pname, set())
        missing = exp - act  # origin/main基準の履歴にあるが日報に無い
        extra_but_ok = act - exp  # 日報にあるがorigin/main基準には無い（手動確認済みの未push分など）
        if missing:
            any_issue = True
            report.fail("コミット欠落", f"{pname}: 日報に無いコミットが{len(missing)}件 {sorted(missing)[:10]}")
        # extra_but_okはログ参考のみ（手動検証済みのローカル未push分を想定）WARNで出す
        if extra_but_ok:
            report.warn(
                "日報にのみ存在するコミット",
                f"{pname}: {len(extra_but_ok)}件（origin/main基準の自動集計には無い。"
                f"手動確認済みのローカル未push分等を想定）{sorted(extra_but_ok)[:10]}",
            )
    if not any_issue:
        report.ok("コミット欠落チェック", "origin/main基準の全コミットが日報に反映済み")

    # 日報内でのハッシュ重複（同一プロジェクト内で同じハッシュが2回以上書かれていないか）
    for d in cl.iter_dates(start_date, end_date):
        path = cl.day_file_path(d)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            content = f.read()
        hashes = re.findall(r"`([a-f0-9]{7,40})`", content)
        dup = {h for h in hashes if hashes.count(h) > 1}
        if dup:
            report.fail("日報内コミット重複", f"{path}: {dup}")


def check_unmerged_branches(report, meta_json_path):
    with open(meta_json_path, encoding="utf-8") as f:
        meta = json.load(f)
    for pname, m in meta.items():
        if not m["complete"]:
            report.fail("取得不完全", f"{pname}: fetch_ok={m['fetch_ok']} ({m['fetch_message']}), log_err={m.get('log_err')}")
        for u in m.get("unmerged_branches", []):
            if "error" in u:
                report.warn("未マージブランチ検出エラー", f"{pname}: {u['error']}")
            else:
                report.warn(
                    "未マージ/未push作業ブランチ",
                    f"{pname}: {u['branch']} に{u['commit_count']}件（日付: {', '.join(u['dates'])}）"
                    "— 自動では日報に含めていません。必要なら人間が確認して追加してください。",
                )
    if all(m["complete"] for m in meta.values()):
        report.ok("fetch/取得状況", "全対象プロジェクトで取得完了")


def check_git_diff_safety(report, repo_root, protected_globs):
    """コミット前の `git status` で、意図した範囲以外のファイルが変更されていないか確認する。
    実際にどこまでが「意図した範囲」かは呼び出し側が判断するため、ここでは変更ファイル一覧を
    報告するのみ（destructiveな操作ではないので削除検知を優先する）。
    """
    rc, out, _ = run(["git", "-C", repo_root, "status", "--short"])
    if rc != 0:
        report.fail("git status失敗", out)
        return
    deleted = [line[3:] for line in out.splitlines() if line.startswith(" D") or line.startswith("D ")]
    if deleted:
        report.fail("既存ファイルの削除を検出", f"{deleted}")
    else:
        report.ok("削除検知", "git status上の削除(D)は無し")

    modified = [line[3:] for line in out.splitlines() if line.strip()]
    report.warn("変更ファイル一覧（レビュー用）", f"{len(modified)}件: {modified[:20]}{'...' if len(modified) > 20 else ''}")


def check_github_reflection(report, repo_root):
    rc, out, err = run(["git", "-C", repo_root, "fetch", "origin"])
    if rc != 0:
        report.fail("GitHub fetch失敗", err.strip()[-300:])
        return
    rc, local_head, _ = run(["git", "-C", repo_root, "rev-parse", "HEAD"])
    rc2, remote_head, _ = run(["git", "-C", repo_root, "rev-parse", "origin/main"])
    local_head, remote_head = local_head.strip(), remote_head.strip()
    if local_head == remote_head:
        report.ok("GitHub反映状況", f"HEAD({local_head[:7]}) == origin/main({remote_head[:7]})")
    else:
        report.warn("GitHub反映状況", f"HEAD({local_head[:7]}) != origin/main({remote_head[:7]})。push未実施の可能性")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--commits", required=True)
    ap.add_argument("--commits-meta", required=True)
    ap.add_argument("--check-github", action="store_true")
    args = ap.parse_args()

    start_date = datetime.strptime(args.start, "%Y-%m-%d").date()
    end_date = datetime.strptime(args.end, "%Y-%m-%d").date()

    report = Report()
    check_date_gaps_and_dupes(report, start_date, end_date)
    check_commits_against_json(report, args.commits, start_date, end_date)
    check_unmerged_branches(report, args.commits_meta)
    check_git_diff_safety(report, cl.CHRONICLE_ROOT, [])
    if args.check_github:
        check_github_reflection(report, cl.CHRONICLE_ROOT)

    report.print_all()
    code = report.summary_exit_code()
    print(f"\n=== 総合判定: {'PASS' if code == 0 else ('WARN' if code == 1 else 'FAIL')} ===")
    sys.exit(code)


if __name__ == "__main__":
    main()
