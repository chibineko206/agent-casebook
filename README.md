# agent-casebook

Case-based records of your AI coding sessions. Built for Claude Code.

AI コーディングエージェント（現状は Claude Code）との相談と作業を、**案件（case）単位**で 1 つのリポジトリに記録する仕組みです。
「あの作業どこで相談したっけ」「成果物どこだっけ」「以前決めた内容から更新したい」で迷子にならないことが目的です。
個人のローカル利用を前提にしています。Anthropic とは無関係の個人プロジェクトです。

## 何が入っているか

| 場所 | 役割 |
|---|---|
| `CLAUDE.md` | このリポでの運用ルール。Claude Code が毎回読む |
| `cases/<slug>/CASE.md` | 案件ごとの記録。メタ情報と、セッションごとの「やったこと / 判断 / 未決事項」 |
| `INDEX.md` | 案件一覧 |
| `private/` | 人事情報など push しない案件（gitignore 済み） |
| `.claude/commands/` | `/done`（セッションを記録して締める）、`/doc-check`（文書の点検） |
| `.claude/hooks/` | SessionStart（進行中の案件と前回の記録漏れを表示）、SessionEnd（記録漏れの検知） |
| `tools/dashboard.py` | `CASE.md` から単一 HTML のダッシュボードを生成（案件一覧、未決事項の横断、時系列、検索、セッション一覧、`CASE.md` ビューア） |
| `tools/launcher/` | ダッシュボードのボタンから Terminal や Claude Code を起動する macOS 用の小さなアプリ |
| `templates/` | `~/.claude/CLAUDE.md`（全リポ共通の作法）と `/writeback` コマンドの雛形 |
| `docs/manual.html` | 運用マニュアル（ブラウザで開く） |
| `docs/000-design.md` | 設計と見送った案 |
| `docs/001-maintenance.md` | 保守の運用ルール（このリポを正にして、日々使うリポへ取り込む流れ） |
| `tools/check-public.sh` | 公開前の点検（絶対パス、メール、手元で決めた禁止語） |

## セットアップ

前提: macOS、Claude Code、python3（ダッシュボードと hook が使う）。Launcher は AppleScript と Terminal.app を使うので macOS 専用です。

```bash
git clone <this repo> ~/agent-casebook
cd ~/agent-casebook
claude            # 初回起動で hook の実行を確認される。内容は .claude/hooks/ を読んで判断してください
```

任意の設定:

```bash
# 全リポ共通の作法（既存の ~/.claude/CLAUDE.md があれば内容を比べて取り込む）
cp templates/global-CLAUDE.md ~/.claude/CLAUDE.md

# 別リポで開いた Claude Code から記録を書き戻す /writeback コマンド（パスは ~/agent-casebook 前提。違えば書き換える）
mkdir -p ~/.claude/commands && cp templates/writeback.md ~/.claude/commands/writeback.md

# ダッシュボードのボタンから Terminal を開く Launcher（casebook:// URL スキームを登録する）
bash tools/launcher/build.sh
```

ダッシュボードは Claude Code の起動時と `/done` の最後に自動で作り直されます。手で見るときは次のとおりです。

```bash
python3 tools/dashboard.py --html --open
```

## 使い方の要点

- 何を始めるか言うだけでよく、案件の作成と特定は Claude が行う
- セッションの終わりに `/done <slug>` で `CASE.md` に追記する。中断するときも同じ
- 数週間後に戻るときはセッションを再開せず、新しいセッションで `CASE.md` を読ませる
- 詳細は `docs/manual.html`、ルールは `CLAUDE.md`

## 自動で実行されるものについて

- `.claude/hooks/` のスクリプトは Claude Code のセッション開始・終了のたびに実行されます。中身は `git status` と `CASE.md` の読み取り、ダッシュボードの生成だけです
- Launcher は `casebook://` の URL を受けると、案件の slug と動作名（Terminal を開く、Claude Code を起動する、Finder で開く、ダッシュボード再生成、セッション再開）だけを受け取って動きます。コマンド文字列は受け取りません。どの Web ページからも URL は開けるので、内容を確認してから入れてください
- `tools/dashboard.py` は Claude Code がローカルに残すセッション記録（`~/.claude/projects/`）を読みます。この形式は Claude Code の内部仕様で、予告なく変わることがあります。動作確認した Claude Code は 2.1.287 です

## ライセンス

MIT
