#!/usr/bin/env python3
"""Chronicle daily更新本体。

方針（過去の不具合を踏まえて明文化）:
- 既存の日報ファイルは、プロジェクトセクション単位で「すでにあれば触らない」。
  （既存の手書き/生成済み内容を優先し、上書き・削除しない）
- コミットが見つかったプロジェクトは、原則そのプロジェクトのセクションを追加する。
  ただし「セッション記録のみ」で仮に作られたセクションは、本物のコミットが
  見つかった時点でアップグレード（置換）してよい自分自身の生成物として扱う。
- コミットもセッション証跡も無いプロジェクトは、その日は何も書かない
  （現状の「動きがあったプロジェクトだけ記載する」慣習を維持）。
- 日全体でプロジェクトセクションが1つも無い場合のみ、日全体のプレースホルダ
  （作業なし相当）を入れる。セッション証跡があれば「セッション記録で作業を確認」、
  無ければ「作業状況未確認」。
- 月インデックス・トップインデックスは日ファイルから要約を再構築する。
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
import chronicle_lib as cl

SESSION_ONLY_SUFFIX = "（コミットなし・セッション記録で作業を確認）"
DAY_NO_COMMIT = "コミット記録なし（作業状況未確認）"
DAY_SESSION_EVIDENCE = "コミット記録なし（セッション記録で作業を確認）"
DAY_LEGACY_NO_WORK = "作業なし"


def build_evidence_facts(project_name, info):
    facts = []
    if info.get("edited"):
        files = "、".join(info["edited"][:6])
        more = "" if len(info["edited"]) <= 6 else f" 等{len(info['edited'])}件"
        facts.append(f"{project_name}: {files}{more} を編集（Claude Codeセッション記録で確認。コミットなし）")
    elif info.get("read"):
        files = "、".join(info["read"][:6])
        more = "" if len(info["read"]) <= 6 else f" 等{len(info['read'])}件"
        facts.append(f"{project_name}: {files}{more} を確認・調査（Claude Codeセッション記録で確認。コミットなし）")
    elif info.get("bash"):
        facts.append(f"{project_name}: ディレクトリで{len(info['bash'])}件のコマンドを実行（Claude Codeセッション記録で確認。作業内容の詳細は不明、コミットなし）")
    return facts


def project_section_regex(project_name):
    # "## imasugu" または "## imasugu（コミットなし・セッション記録で作業を確認）"
    escaped = re.escape(project_name)
    return re.compile(rf"^## {escaped}(（コミットなし・セッション記録で作業を確認）)?\s*$", re.MULTILINE)


def find_section_span(content, header_regex):
    m = header_regex.search(content)
    if not m:
        return None
    start = m.start()
    next_m = re.search(r"^## ", content[m.end():], re.MULTILINE)
    end = m.end() + next_m.start() if next_m else len(content)
    return start, end, m.group(0)


def day_header_block(d):
    return f"# {d.isoformat()}\n\n[← 月インデックス](index.md)\n\n"


def plan_day(d, content, commits_by_project, session_by_project):
    """1日分の変更計画を作る。(new_content, changes: list[str]) を返す。
    contentがNoneなら新規作成。
    """
    changes = []
    if content is None:
        content = day_header_block(d)
        is_new = True
    else:
        is_new = False

    existing_sections = set(re.findall(r"^## (.+?)(?:（コミットなし・セッション記録で作業を確認）)?\s*$", content, re.MULTILINE))

    # 日全体プレースホルダ（本文付きも含む）をセクション単位で剥がす
    # （実際のプロジェクトセクションが見つかった時点で置き換えるため）
    had_day_placeholder = False
    day_placeholder_re = re.compile(
        r"^## (" + "|".join(re.escape(p) for p in (DAY_LEGACY_NO_WORK, DAY_NO_COMMIT, DAY_SESSION_EVIDENCE)) + r")\s*$",
        re.MULTILINE,
    )
    span = find_section_span(content, day_placeholder_re)
    if span:
        start, end, _ = span
        content = content[:start].rstrip() + "\n" + content[end:].lstrip("\n")
        content = content.rstrip() + "\n" if content.strip() else content
        had_day_placeholder = True

    for project in cl.PROJECT_NAMES:
        canonical_re = re.compile(rf"^## {re.escape(project)}\s*$", re.MULTILINE)
        session_re = re.compile(rf"^## {re.escape(project)}{re.escape(SESSION_ONLY_SUFFIX)}\s*$", re.MULTILINE)

        has_canonical = bool(canonical_re.search(content))
        session_span = find_section_span(content, session_re)

        commits_today = commits_by_project.get(project, [])
        evidence_today = session_by_project.get(project)

        if has_canonical:
            continue  # 既存の本物セクションは絶対に触らない

        if commits_today:
            section_md = cl.build_project_section(project, commits_today) + "\n"
            if session_span:
                start, end, _ = session_span
                content = content[:start] + section_md + content[end:]
                changes.append(f"{project}: セッション記録のみのセクションをコミット{len(commits_today)}件で更新")
            else:
                content = content.rstrip() + "\n\n" + section_md
                changes.append(f"{project}: コミット{len(commits_today)}件のセクションを新規追加")
            continue

        if session_span:
            continue  # 既にセッション証跡セクションがあり、新規コミットも無い→そのまま

        if evidence_today:
            facts = build_evidence_facts(project, evidence_today)
            if facts:
                section_md = (
                    f"## {project}{SESSION_ONLY_SUFFIX}\n\n### 確認できた事実\n"
                    + "\n".join(f"- {f}" for f in facts) + "\n"
                )
                content = content.rstrip() + "\n\n" + section_md
                changes.append(f"{project}: セッション証跡のみのセクションを新規追加")

    # 日全体にプロジェクトセクションが1つも無ければプレースホルダを入れる
    any_section = bool(re.search(r"^## ", content, re.MULTILINE))
    if not any_section:
        any_evidence = any(session_by_project.get(p) for p in cl.PROJECT_NAMES)
        header = DAY_SESSION_EVIDENCE if any_evidence else DAY_NO_COMMIT
        if any_evidence:
            lines = [f"## {header}", "", "### 確認できた事実"]
            for p in cl.PROJECT_NAMES:
                if session_by_project.get(p):
                    lines.extend(f"- {f}" for f in build_evidence_facts(p, session_by_project[p]))
            content = content.rstrip() + "\n\n" + "\n".join(lines) + "\n"
        else:
            content = content.rstrip() + "\n\n" + f"## {header}\n"
        if is_new:
            changes.append(f"新規作成: {header}")
        elif had_day_placeholder:
            pass  # プレースホルダ文言が変わっていなければ後段のdiffで無変更判定される
        else:
            changes.append(f"プレースホルダを設定: {header}")

    return content.rstrip() + "\n", changes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--commits", required=True, help="collect_commitsの出力JSON")
    ap.add_argument("--sessions", required=True, help="collect_sessionsの出力JSON")
    ap.add_argument("--apply", action="store_true", help="指定しない場合は変更予定のみ表示（書き込みしない）")
    ap.add_argument("--report", default=None, help="変更計画をJSONで保存するパス")
    args = ap.parse_args()

    start_date = datetime.strptime(args.start, "%Y-%m-%d").date()
    end_date = datetime.strptime(args.end, "%Y-%m-%d").date()

    with open(args.commits, encoding="utf-8") as f:
        commits_data = json.load(f)  # date -> project -> [{hash, subject}, ...] 時系列昇順
    with open(args.sessions, encoding="utf-8") as f:
        sessions_data = json.load(f)["data"]  # date -> project -> {edited, read, bash, sources}

    report = []
    for d in cl.iter_dates(start_date, end_date):
        ds = d.isoformat()
        path = cl.day_file_path(d)
        content = None
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                content = f.read()

        commits_by_project = commits_data.get(ds, {})
        session_by_project = sessions_data.get(ds, {})

        new_content, changes = plan_day(d, content, commits_by_project, session_by_project)

        if content == new_content:
            continue  # 無変更

        report.append({"date": ds, "path": path, "changes": changes})
        print(f"{ds}: {'; '.join(changes) if changes else '(本文差分のみ)'}")

        if args.apply:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(new_content)

    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"\n変更対象の日数: {len(report)} / {args.apply and '適用済み' or 'dry-run（--applyで反映）'}")


if __name__ == "__main__":
    main()
