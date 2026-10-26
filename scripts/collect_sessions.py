#!/usr/bin/env python3
"""Claude Codeセッション履歴から、対象プロジェクトへの実作業の痕跡を収集する。

コミットがない日でも、ファイル編集・テスト・調査などの実作業が
確認できれば記録するための材料を作る（推測はしない。ツール呼び出しの
事実のみを集める）。

出力: JSON（date -> project -> {"edited": [...], "read": [...], "bash": [...]}）
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))
import chronicle_lib as cl

JST = timezone(timedelta(hours=9))
CLAUDE_PROJECTS_DIR = "/Users/taro/.claude/projects"
SKIP_DIR_SUBSTRINGS = ["mem-observer"]


def to_jst_date(ts):
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None
    return dt.astimezone(JST).date().isoformat()


def match_project(path):
    if not path:
        return None
    for name, root in cl.PROJECT_PATHS.items():
        if path == root or path.startswith(root + "/"):
            return name, os.path.relpath(path, root)
    return None


def iter_jsonl_files(exclude_session_ids=None):
    """exclude_session_ids: このchronicle更新作業自体のセッションID集合。
    トップレベルの <sid>.jsonl と、その下の <sid>/subagents/agent-*.jsonl
    （このセッションが生成したサブエージェントの記録）を両方除外する。
    他の無関係なセッション（同日に別件で行われた本物の作業）は除外しない。
    """
    exclude_session_ids = exclude_session_ids or set()
    for root, dirs, files in os.walk(CLAUDE_PROJECTS_DIR):
        if any(s in root for s in SKIP_DIR_SUBSTRINGS):
            dirs[:] = []
            continue
        base = os.path.basename(root)
        if base in exclude_session_ids:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d not in exclude_session_ids]
        for f in files:
            if not f.endswith(".jsonl"):
                continue
            if any(f.startswith(sid) for sid in exclude_session_ids):
                continue
            yield os.path.join(root, f)


def collect(start_date, end_date, exclude_session_ids=None):
    result = {}
    start_s, end_s = start_date.isoformat(), end_date.isoformat()
    files_scanned = 0
    lines_scanned = 0
    for fpath in iter_jsonl_files(exclude_session_ids):
        files_scanned += 1
        try:
            with open(fpath, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    lines_scanned += 1
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    ts = obj.get("timestamp")
                    if not ts:
                        continue
                    jdate = to_jst_date(ts)
                    if not jdate or not (start_s <= jdate <= end_s):
                        continue
                    cwd = obj.get("cwd")
                    cwd_match = match_project(cwd)
                    msg = obj.get("message") or {}
                    content = msg.get("content")
                    if not isinstance(content, list):
                        continue
                    for block in content:
                        if not isinstance(block, dict) or block.get("type") != "tool_use":
                            continue
                        name = block.get("name", "")
                        inp = block.get("input") or {}
                        fp = inp.get("file_path")

                        if name in ("Edit", "Write", "NotebookEdit", "Read"):
                            # 編集/読み取りは file_path が対象プロジェクト配下かどうかで
                            # 厳密に判定する。/private/tmp のスクラッチパッドや
                            # ~/.claude/ 配下のメモ等、cwdが対象プロジェクトでも
                            # 実際のファイルがプロジェクト外なら「プロジェクトへの
                            # 作業」として扱わない（誤帰属を避ける）。
                            proj_match = match_project(fp)
                        else:
                            # Bash等はfile_pathを持たないため、cwdか、コマンド文字列に
                            # 対象パスが含まれるかで弱い証拠として判定する。
                            proj_match = cwd_match
                            if not proj_match:
                                cmd = inp.get("command", "")
                                for pname, proot in cl.PROJECT_PATHS.items():
                                    if proot in cmd:
                                        proj_match = (pname, None)
                                        break
                        if not proj_match:
                            continue
                        pname, relpath = proj_match
                        display = relpath or fp
                        if display in (None, "", "."):
                            display = "?"
                        bucket = result.setdefault(jdate, {}).setdefault(
                            pname, {"edited": set(), "read": set(), "bash": set(), "sources": set()}
                        )
                        bucket["sources"].add(os.path.basename(fpath))
                        if name in ("Edit", "Write", "NotebookEdit"):
                            bucket["edited"].add(display)
                        elif name == "Read":
                            bucket["read"].add(display)
                        elif name == "Bash":
                            cmd = (inp.get("command") or "")[:120]
                            bucket["bash"].add(cmd)
        except Exception as e:
            print(f"WARN: {fpath} の読み込みに失敗: {e}", file=sys.stderr)

    # set -> sorted list に変換
    for d in result:
        for p in result[d]:
            for k in ("edited", "read", "bash", "sources"):
                result[d][p][k] = sorted(result[d][p][k])
    return result, {"files_scanned": files_scanned, "lines_scanned": lines_scanned}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument(
        "--exclude-session", action="append", default=[],
        help="除外するセッションID（このchronicle更新作業自体の自己混入を防ぐため、"
             "実行中セッションのCLAUDE_CODE_SESSION_IDは自動的に除外される）",
    )
    args = ap.parse_args()
    start_date = datetime.strptime(args.start, "%Y-%m-%d").date()
    end_date = datetime.strptime(args.end, "%Y-%m-%d").date()
    exclude = set(args.exclude_session)
    current_session = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if current_session:
        exclude.add(current_session)
    if exclude:
        print(f"除外セッション: {sorted(exclude)}", file=sys.stderr)
    result, stats = collect(start_date, end_date, exclude)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"stats": stats, "data": result}, f, ensure_ascii=False, indent=2)
    print(f"scanned {stats['files_scanned']} files / {stats['lines_scanned']} lines")
    print(f"matched dates: {len(result)}")
    print(f"written to {args.out}")


if __name__ == "__main__":
    main()
