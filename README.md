# Chronicle

複数プロジェクトの開発日報アーカイブ。2026-03-01 から継続（2026-08 より対象を全プロジェクトへ拡張）。

## 対象プロジェクト

- imasugu
- sales-os
- medipre
- personal-tax-os

その日に動きがあったプロジェクトのみ、その日のファイルにセクションが追加される。

## 目的

- 何をやったか / なぜ逸れたかを後から辿れるようにする
- commit log だけでは分からない文脈を残す

## ディレクトリ構成

```
daily/
  index.md          ← 月別リンク一覧
  2026/
    03/
      index.md      ← 3月の日別リンク
      01.md
      02.md
      ...
    04/
      index.md
      ...
    05/
      index.md
      ...
```

1日1ファイル。ファイル内はプロジェクトごとに `## project-name` セクションで分割する。

```
# YYYY-MM-DD

## imasugu
### 最初にやろうとしてたこと
...
### 実際にやったこと
...

## sales-os
### 実際にやったこと
...
```

## 閲覧方法

### GitHub Web / GitHub Mobile
- このリポジトリをブラウザで開く
- `daily/index.md` → 月 → 日と辿る
- GitHub Mobile アプリでも Markdown がそのまま表示される

### Obsidian (PC / スマホ)
1. このリポジトリを `git clone` する
2. Obsidian で「フォルダを Vault として開く」→ `chronicle` を選択
3. `daily/index.md` を起点に内部リンクで移動できる

### スマホブラウザ
- GitHub Mobile アプリ推奨（Markdown レンダリングあり）
- または GitHub Web をブラウザで開く

## 記録フォーマット

各日ファイルはプロジェクトごとに以下のセクションで構成:

- 最初にやろうとしてたこと
- 実際にやったこと
- 横道に逸れたこと / なぜ逸れたか
- 反省 / 学んだこと / 備忘
- commit / deploy

コミットもセッション証跡も無い日は「コミット記録なし（作業状況未確認）」、
コミットは無いがClaude Codeセッション履歴で実作業が確認できた日は
「`<project>`（コミットなし・セッション記録で作業を確認）」とし、
「確認できた事実」のみを記載する。「作業なし」と断定しない。

## 更新方法（自動化）

`scripts/` に、対象プロジェクトのgit履歴とClaude Codeセッション履歴から
日報を更新するスクリプト一式がある。既存の日報は上書きせず、無い部分だけ
追加する。使い方は `.claude/skills/chronicle-update/SKILL.md` を参照。

- `scripts/collect_commits_all.py` — origin/main基準でコミットを収集
  （fetch失敗時は不完全として明示。未マージ/未push作業ブランチは自動では
  含めず「要確認」として報告のみ）
- `scripts/collect_sessions.py` — Claude Codeセッション履歴から実作業の痕跡を収集
- `scripts/update_daily.py` — 日報へ非破壊的に反映（dry-run → `--apply`）
- `scripts/verify_chronicle.py` — 日付欠落・コミット欠落・重複・インデックス整合性・
  GitHub反映状況などを検証
