"""Chronicle共通ライブラリ。

対象プロジェクトのgit履歴取得・Claude Codeセッション証跡の走査・
daily/*.md生成に使う共通処理をまとめる。

設計上の注意点（過去の不具合を踏まえた決定）:
- 各リポジトリは `git fetch` 後、`git log --all` でローカル/リモートの
  全ブランチを対象にする。HEADやlocal mainだけを見ると、origin/mainに
  マージ済みだがローカルに取り込まれていないコミットを取り落とす
  （2026-10-06のimasuguで実際に発生した不具合）。
- コミット日時は常にJST（Asia/Tokyo）で日付へ変換する。`--since`/`--until`
  もタイムゾーン付きISO8601文字列で渡し、コミット側のauthor timezoneに
  依存しない。
- 同一コミットのハッシュはgit logの性質上、`--all`でも1回しか現れない
  （複数ブランチから到達可能でも1コミット=1回）。
"""

import json
import os
import re
import subprocess
from datetime import date, datetime, timedelta

CHRONICLE_ROOT = "/Users/taro/projects/chronicle"
DAILY_ROOT = os.path.join(CHRONICLE_ROOT, "daily")

# 対象プロジェクト（表示順 = 日報内のセクション順）
PROJECTS = [
    {"name": "imasugu", "path": "/Users/taro/projects/imasugu", "remote": "origin"},
    {"name": "sales-os", "path": "/Users/taro/projects/sales-os", "remote": "origin"},
    {"name": "medipre", "path": "/Users/taro/projects/medipre", "remote": "origin"},
    {"name": "personal-tax-os", "path": "/Users/taro/Documents/ChatGPT/personal-tax-os", "remote": None},
]
PROJECT_NAMES = [p["name"] for p in PROJECTS]
PROJECT_PATHS = {p["name"]: p["path"] for p in PROJECTS}

# Claude Codeセッションログの探索対象（~/.claude/projects/配下は全ディレクトリを走査する。
# 固定リストにはしない。cwdがどのプロジェクト配下であれば一致とみなす。
CLAUDE_PROJECTS_DIR = "/Users/taro/.claude/projects"

NO_COMMIT_HEADER = "## コミット記録なし（作業状況未確認）"
SESSION_EVIDENCE_HEADER = "## コミット記録なし（セッション記録で作業を確認）"

PREFIX_RE = re.compile(
    r"^(feat|fix|chore|docs|data|style|refactor|test|security|ci|perf)(\([^)]*\))?:\s*",
    re.IGNORECASE,
)


def strip_prefix(subject):
    return PREFIX_RE.sub("", subject, count=1).strip()


def truncate(s, limit=26):
    return s if len(s) <= limit else s[:limit] + "…"


def jst_bounds(start_date, end_date):
    """date型のstart/end(両端含む)をJSTタイムゾーン付きISO8601文字列に変換。
    gitの--since/--untilはuntilが排他的なので end_date の翌日0時を使う。
    """
    since = f"{start_date.isoformat()}T00:00:00+09:00"
    until_date = end_date + timedelta(days=1)
    until = f"{until_date.isoformat()}T00:00:00+09:00"
    return since, until


def run(cmd, cwd=None, timeout=60):
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as e:
        return 124, "", f"timeout after {timeout}s: {e}"
    except FileNotFoundError as e:
        return 127, "", str(e)


def fetch_remote(project):
    """git fetchを実行し (ok, message) を返す。okはreturncodeのみで判定する
    （このマシンではcredential helperの無害な警告が stderr に出るため、
    文字列マッチでの成否判定はしない）。
    """
    path = project["path"]
    remote = project["remote"]
    if not os.path.isdir(os.path.join(path, ".git")):
        return False, f"{path} はgitリポジトリではない"
    if remote is None:
        return True, "remote未設定（ローカルのみ対象）"
    rc, out, err = run(["git", "-C", path, "fetch", remote, "--prune"], timeout=60)
    if rc != 0:
        return False, f"git fetch {remote} 失敗 (rc={rc}): {err.strip()[-500:]}"
    return True, "fetch成功"


FIELD_SEP = "\x1f"
ENTRY_SEP = "\x1e"


def _git_log(path, ref, since, until, env):
    cmd = [
        "git", "-C", path, "log", ref,
        f"--since={since}", f"--until={until}",
        "--date=format-local:%Y-%m-%d",
        f"--pretty=format:%ad{FIELD_SEP}%h{FIELD_SEP}%s{ENTRY_SEP}",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    commits = []
    if proc.returncode == 0:
        for chunk in proc.stdout.split(ENTRY_SEP):
            chunk = chunk.strip()
            if not chunk:
                continue
            d, h, s = chunk.split(FIELD_SEP)
            commits.append({"date": d, "hash": h, "subject": s})
    return proc.returncode, commits, proc.stderr


def determine_main_ref(project):
    """集計の基準refを決める。
    - remoteがある場合: origin/<デフォルトブランチ>（通常main）。
      存在しなければ `origin/HEAD` の指す先を使う。
    - remoteがない場合: ローカルの main、無ければ現在のHEAD。
    戻り値: (ref, description)
    """
    path = project["path"]
    if project["remote"]:
        rc, _, _ = run(["git", "-C", path, "rev-parse", "--verify", "origin/main"])
        if rc == 0:
            return "origin/main", "origin/main（リモート追跡、マージ済みPRを含む）"
        rc, out, _ = run(["git", "-C", path, "symbolic-ref", "refs/remotes/origin/HEAD"])
        if rc == 0:
            ref = out.strip().replace("refs/remotes/", "")
            return ref, f"{ref}（origin/HEADが指すデフォルトブランチ）"
        return "HEAD", "origin/mainが見つからずHEADにフォールバック（要確認）"
    rc, _, _ = run(["git", "-C", path, "rev-parse", "--verify", "main"])
    if rc == 0:
        return "main", "main（remote未設定のためローカルブランチ）"
    return "HEAD", "mainブランチが無いためHEADにフォールバック（要確認）"


def detect_unmerged_branch_activity(project, main_ref, start_date, end_date):
    """origin/*の中で main_ref から到達できないコミットが対象期間にあるブランチを
    報告専用でリストアップする（自動では日報に混ぜない。night-pivotのような
    長期並行ブランチの大量混入を避けるため、人間の判断を挟む）。
    """
    path = project["path"]
    if not project["remote"]:
        return []
    since, until = jst_bounds(start_date, end_date)
    env = dict(os.environ)
    env["TZ"] = "Asia/Tokyo"
    rc, out, _ = run(["git", "-C", path, "branch", "-r"])
    if rc != 0:
        return []
    branches = []
    for line in out.splitlines():
        b = line.strip()
        if not b or "->" in b:
            continue
        name = b[len("origin/"):] if b.startswith("origin/") else b
        if f"origin/{name}" == main_ref or b == main_ref:
            continue
        branches.append(b)

    findings = []
    for b in branches:
        rc, commits, _ = _git_log(path, f"{main_ref}..{b}", since, until, env)
        if rc == 0 and commits:
            dates = sorted({c["date"] for c in commits})
            findings.append({
                "branch": b,
                "commit_count": len(commits),
                "dates": dates,
            })
    return findings


def detect_local_unpushed(project, main_ref, start_date, end_date):
    """現在チェックアウト中のローカルブランチに、mainにもorigin上のどのブランチにも
    無いコミットが対象期間にないか報告する（94589e4のような「ローカルのみ・未push」
    の実作業を取り落とさないための通知）。自動では日報に混ぜない。
    """
    path = project["path"]
    rc, out, _ = run(["git", "-C", path, "branch", "--show-current"])
    if rc != 0 or not out.strip():
        return []
    current = out.strip()
    since, until = jst_bounds(start_date, end_date)
    env = dict(os.environ)
    env["TZ"] = "Asia/Tokyo"
    rc, commits, _ = _git_log(path, f"{main_ref}..{current}", since, until, env)
    if rc != 0 or not commits:
        return []
    # originに同ハッシュが存在するものは除外（push済みなら detect_unmerged_branch_activity 側で捕捉される）
    local_only = []
    for c in commits:
        rc2, out2, _ = run(["git", "-C", path, "branch", "-r", "--contains", c["hash"]])
        if rc2 == 0 and not out2.strip():
            local_only.append(c)
    if not local_only:
        return []
    return [{
        "branch": f"{current}（ローカルのみ、未push）",
        "commit_count": len(local_only),
        "dates": sorted({c["date"] for c in local_only}),
    }]


def collect_commits(project, start_date, end_date):
    """project辞書, 日付範囲(両端含む)を受け取り、
    (commits: list[{date, hash, subject}], meta: dict) を返す。

    基準は origin/main（相当）のみ。マージ済みPRのコミットは
    origin/mainの祖先に含まれるため自動的に拾われる。
    まだmainに未マージのブランチは自動混入させず、
    meta['unmerged_branches'] に候補として報告するのみ（人間の確認が必要）。
    """
    path = project["path"]
    fetch_ok, fetch_message = fetch_remote(project)

    since, until = jst_bounds(start_date, end_date)
    env = dict(os.environ)
    env["TZ"] = "Asia/Tokyo"

    if not os.path.isdir(os.path.join(path, ".git")):
        return [], {
            "fetch_ok": False,
            "fetch_message": fetch_message,
            "ref_mode": "none",
            "complete": False,
            "unmerged_branches": [],
        }

    main_ref, ref_desc = determine_main_ref(project)
    rc, commits, err = _git_log(path, main_ref, since, until, env)

    unmerged = []
    if rc == 0:
        try:
            unmerged = detect_unmerged_branch_activity(project, main_ref, start_date, end_date)
            unmerged += detect_local_unpushed(project, main_ref, start_date, end_date)
        except Exception as e:  # 報告専用の付加情報。失敗しても主系統の結果は返す。
            unmerged = [{"error": str(e)}]

    complete = (rc == 0) and (project["remote"] is None or fetch_ok)
    meta = {
        "fetch_ok": fetch_ok,
        "fetch_message": fetch_message,
        "ref_mode": ref_desc,
        "log_rc": rc,
        "log_err": err.strip() if rc != 0 else "",
        "complete": complete,
        "unmerged_branches": unmerged,
    }
    return commits, meta


def iter_dates(start_date, end_date):
    cur = start_date
    while cur <= end_date:
        yield cur
        cur += timedelta(days=1)


def day_file_path(d):
    return os.path.join(DAILY_ROOT, f"{d.year}", f"{d.month:02d}", f"{d.day:02d}.md")


def month_index_path(year, month):
    return os.path.join(DAILY_ROOT, f"{year}", f"{month:02d}", "index.md")


def build_project_section(project_name, commits_chrono):
    """commits_chrono: [{hash, subject}, ...] 日付内で時刻昇順。"""
    lines = [f"## {project_name}", "", "### 最初にやろうとしてたこと", "- 未確認", "",
              "### 実際にやったこと"]
    seen = set()
    for c in commits_chrono:
        item = strip_prefix(c["subject"])
        if item not in seen:
            seen.add(item)
            lines.append(f"- {item}")
    lines += ["", "### 横道に逸れたこと", "- 未確認", "",
              "### なぜ逸れたか", "- 未確認", "",
              "### 反省", "- 未確認", "",
              "### 学んだこと", "- 未確認", "",
              "### 備忘", "- 未確認", "",
              "### commit"]
    for c in commits_chrono:
        lines.append(f"- `{c['hash']}` {c['subject']}")
    lines += ["", "### deploy", "- 未確認", ""]
    return "\n".join(lines)


def build_session_evidence_section(facts):
    lines = [SESSION_EVIDENCE_HEADER, "", "### 確認できた事実"]
    for f in facts:
        lines.append(f"- {f}")
    return "\n".join(lines)


def existing_project_sections(content):
    """day fileの中に存在する '## <project>' セクション名の集合を返す。"""
    return set(re.findall(r"^## (.+)$", content, re.MULTILINE))


def is_known_placeholder(content):
    body = content.split("[← 月インデックス](index.md)", 1)[-1].strip()
    return body in ("## 作業なし", NO_COMMIT_HEADER, SESSION_EVIDENCE_HEADER) or \
        body.startswith(NO_COMMIT_HEADER) or body.startswith(SESSION_EVIDENCE_HEADER)
