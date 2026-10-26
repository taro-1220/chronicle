---
name: chronicle-update
description: Chronicle（複数プロジェクトの開発日報アーカイブ）を最新まで更新する。対象プロジェクトのgit履歴とClaude Codeセッション履歴から日報を再生成・差分検証し、確認後にコミット・pushする。「chronicleを更新して」「日報を最新化して」等で使う。
---

# Chronicle更新スキル

このスキルは `/Users/taro/projects/chronicle` の日報を安全に最新化する。
過去に発生した不具合（git集計がHEADの作業ブランチ基準になっていて
origin/mainのマージ済みコミットを取り落とした、「作業なし」という
断定表現がコミット不在と無根拠に同一視されていた等）を踏まえ、
**事実の確認なしに断定しない・既存の日報を壊さない**ことを最優先する。

## 対象プロジェクト（固定）

- imasugu (`/Users/taro/projects/imasugu`, remote: origin)
- sales-os (`/Users/taro/projects/sales-os`, remote: origin)
- medipre (`/Users/taro/projects/medipre`, remote: origin)
- personal-tax-os (`/Users/taro/Documents/ChatGPT/personal-tax-os`, remote: 無し)

recruit・reprechexは廃止済みのため対象外（2026-10-20に decommission-backups/ へ退避済み）。
対象プロジェクトを変えたい場合は `scripts/chronicle_lib.py` の `PROJECTS` を編集する。

## 手順

### 1. 対象期間を決める
`daily/index.md` と各月の `index.md` から、最後に記録済みの日付を確認する。
今日までの期間を `--start`（翌日）・`--end`（今日、JST）として使う。

### 2. git履歴を収集する（スクリプトが自動でfetchする）

```bash
cd /Users/taro/projects/chronicle/scripts
python3 collect_commits_all.py --start <YYYY-MM-DD> --end <YYYY-MM-DD> \
  --out /tmp/chronicle_commits.json --meta-out /tmp/chronicle_commits_meta.json
```

- 各プロジェクトで `git fetch` → `origin/main`（相当）を基準に集計する。
  マージ済みPRのコミットはorigin/mainの祖先として自動的に含まれる。
- fetchが失敗した場合やリポジトリが見つからない場合は `complete: false` として
  明示され、スクリプトは終了コード2を返す。**この場合は結果を確定扱いにせず、
  ユーザーに状況を報告してから先に進むか判断を仰ぐ。**
- 出力の「要確認」行（未マージの作業ブランチ、ローカル未pushコミット）は
  自動では日報に混ぜない。night-pivotのような長期並行ブランチを無差別に
  混ぜるとノイズになるため、**含めるかどうかは必ず人間に確認する**
  （2026-10-26の運用判断: 「origin/main＋マージ済みPRの出所ブランチのみ」が
  デフォルト。それ以外はレポートするだけ）。

### 3. Claude Codeセッション履歴から実作業の痕跡を収集する

```bash
python3 collect_sessions.py --start <YYYY-MM-DD> --end <YYYY-MM-DD> \
  --out /tmp/chronicle_sessions.json
```

- `~/.claude/projects/` 配下の全セッションを走査し、対象プロジェクト配下への
  Edit/Write/Read/Bashの事実のみを集める。**推測は行わない。**
- 実行中セッション自身（`CLAUDE_CODE_SESSION_ID`）とそのサブエージェントは
  自動的に除外される（このchronicle更新作業自体が「作業証跡」として
  混入する自己参照を防ぐため）。
- 他の無関係なセッション（同日に別件で行われた本物の作業）は除外しない。

### 4. dry-runで変更計画を確認する（必ず--applyなしで先に実行）

```bash
python3 update_daily.py --start <YYYY-MM-DD> --end <YYYY-MM-DD> \
  --commits /tmp/chronicle_commits.json --sessions /tmp/chronicle_sessions.json \
  --report /tmp/chronicle_plan.json
```

- 既存のプロジェクトセクション（`## imasugu` 等、本物のコミット由来）は
  **絶対に上書きしない**。無いものだけ追加する。
- コミットが無くセッション証跡のみの日は
  `## <project>（コミットなし・セッション記録で作業を確認）` として追加し、
  「### 確認できた事実」に具体的な確認内容だけを書く。
  「最初にやろうとしてたこと」「横道」「反省」「学び」は書かない
  （根拠が無いものを埋めない）。
- 日全体で何も見つからない日は `## コミット記録なし（作業状況未確認）` を
  維持する（「作業なし」と断定しない）。
- **出力された変更予定をユーザーに提示し、想定外の変更が無いか確認する。**
  特に以下は必ず目で見る:
  - 既存の「作業なし」系プレースホルダが、本物のコミット/セッション証跡で
    正しくアップグレードされているか
  - 追加されるセクションの内容が具体的で、事実に基づいているか

### 5. 問題なければ適用する

```bash
python3 update_daily.py --start <YYYY-MM-DD> --end <YYYY-MM-DD> \
  --commits /tmp/chronicle_commits.json --sessions /tmp/chronicle_sessions.json \
  --apply --report /tmp/chronicle_plan_applied.json
```

### 6. 検証スイートを実行する

```bash
python3 verify_chronicle.py --start <YYYY-MM-DD> --end <YYYY-MM-DD> \
  --commits /tmp/chronicle_commits.json --commits-meta /tmp/chronicle_commits_meta.json \
  --check-github
```

チェック内容: 日付の欠落、月インデックスの日数・重複・リンク整合性、
origin/main基準のコミット欠落・日報内重複、fetch/取得の完全性、
`git status` 上の意図しない削除、GitHubへの反映状況（pushを伴う場合）。

**FAILが1件でもあれば止めて報告する。WARNは個別に内容を確認し、
既知の想定内（手動確認済みのローカル未pushコミット等）かどうかを判断する。**

### 7. コミット・push

検証がPASS（またはWARNが全て説明可能）であることをユーザーに報告し、
明示的な同意を得てからコミット・pushする。安全性が確認できない場合や
エラー・未確認事項がある場合は、コミット・pushを行わずに報告だけで止める。

## 既知の制約

- セッション履歴の検索対象は `~/.claude/projects/` 配下のみ。他マシン・
  他ツールでの作業は追跡できない。
- Bashコマンドの証跡は「cwdが対象プロジェクト配下」または「コマンド文字列に
  対象パスを含む」場合のみプロジェクトに帰属させる。file_path系ツール
  （Edit/Write/Read）は file_path が対象プロジェクト配下の場合のみ帰属させる
  （cwdだけでは帰属させない。/private/tmp のスクラッチパッドや
  `~/.claude/` 配下のメモを誤って「プロジェクトへの編集」と表示しないため）。
- 同一内容が別ハッシュで複数ブランチに存在するケース（cherry-pick等）は
  ハッシュ単位の重複排除では解消できず、`### commit` に複数ハッシュとして
  残る。要約の「実際にやったこと」側は文面で重複除去されるため目立たない。
- 未マージの長期並行ブランチ（night-pivot等）や、ローカルのみで未pushの
  コミットは自動では日報に含まれない。`verify_chronicle.py` のWARNで
  都度報告されるので、必要に応じて人間が判断して個別に追記する。
