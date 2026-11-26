# agent-casebook

Case-based records of your AI coding sessions. Built for Claude Code.

AI コーディングエージェント（現状は Claude Code）との相談と作業を、**案件（case）単位**で 1 つのリポジトリに記録する仕組みです。
「あの作業どこで相談したっけ」「成果物どこだっけ」「以前決めた内容から更新したい」で迷子にならないことが目的です。
GitHub のテンプレートリポジトリとして配布しています。個人のローカル利用を前提にしています。Anthropic とは無関係の個人プロジェクトです。

## 何が入っているか

| 場所 | 役割 |
|---|---|
| `CLAUDE.md` | 運用ルール（毎回効かせる制約だけ）。Claude Code が毎回読む。解説は `docs/manual.html` |
| `.claude/rules/` | 利用側の固有の規則を置く場所（更新で上書きされない）。初期状態では空 |
| `cases/<slug>/CASE.md` | 案件ごとの記録。メタ情報と、セッションごとの「やったこと / 判断 / 未決事項」 |
| `INDEX.md` | 案件一覧 |
| `private/` | 人事情報など push しない案件（gitignore 済み） |
| `.claude/commands/` | `/done`（セッションを記録して締める）、`/doc-check`（文書の点検。自動実行では書き方の修正まで適用し、ユーザーが打ったときは報告だけで止める） |
| `.claude/hooks/` | SessionStart（進行中の案件と前回の記録漏れを表示）、SessionEnd（記録漏れの検知） |
| `tools/casebook.py` | `/done` の決定的な処理（案件の特定、前回の未決の抽出、追記と書式検査、INDEX 更新、コミット） |
| `tools/dashboard.py` | `CASE.md` から単一 HTML のダッシュボードを生成（案件一覧、未決事項の横断、時系列、検索、セッション一覧、`CASE.md` ビューア） |
| `tools/launcher/` | ダッシュボードのボタンから Terminal や Claude Code を起動する macOS 用の小さなアプリ |
| `templates/` | `~/.claude/CLAUDE.md`（全リポ共通の作法）と `/writeback` コマンドの雛形 |
| `docs/manual.html` | 運用マニュアル（ブラウザで開く） |
| `docs/000-design.md` | 設計と見送った案 |
| `docs/001-maintenance.md` | 保守の運用ルール（テンプレートの配布と、利用側への更新の流れ） |
| `docs/changelog.md` | この仕組み自体の変更履歴と開発の判断 |
| `tools/update-from-upstream.py` | 配布元から仕組みのファイルだけを取り込む（利用側の記録には触れない） |
| `tools/check-public.sh` | 公開前の点検（絶対パス、メール、手元で決めた禁止語） |

## セットアップ

前提: macOS、Claude Code、python3（ダッシュボードと hook が使う）。Launcher は AppleScript と Terminal.app を使うので macOS 専用です。

1. このリポジトリの **Use this template → Create a new repository** で、自分のリポジトリを作る。**Private にする**（案件の記録が入るため）
2. 作ったリポジトリを clone して Claude Code を起動する

```bash
git clone <自分のリポジトリの URL> ~/agent-casebook
cd ~/agent-casebook
claude            # 初回起動で hook の実行を確認される。内容は .claude/hooks/ を読んで判断してください
```

テンプレートから作ったリポジトリは、このリポジトリと履歴がつながりません。`origin` は最初から自分のリポジトリで、外す作業は要りません。`cases/` は空、`INDEX.md` は案件の行が無い状態で始まります。`private/` は gitignore されているので、push しても出ません。

### 仕組みの更新を取り込む

履歴がつながっていないので `git pull` は使えません。更新があったら、取り込みスクリプトを使います。

```bash
python3 tools/update-from-upstream.py --dry-run   # 何が変わるかを確認する
python3 tools/update-from-upstream.py             # 取り込む（コミットはしない）
git diff                                          # 差分を確認してコミットする
```

置き換わるのは仕組みのファイルだけです（`CLAUDE.md`、`.claude/settings.json`、`.claude/hooks/`、`.claude/commands/`、`tools/`、`templates/`、`docs/manual.html`、`docs/chat-writeback-prompt.md`）。`cases/`、`private/`、`INDEX.md`、`.claude/rules/`、`.claude/hooks/session-start.local.sh` は触りません。仕組みのディレクトリは配布元と同じ内容に揃えるため、自分で足したファイルは消えます。独自の規則や表示は `.claude/rules/` と `session-start.local.sh` に書いてください。取り込んだ版は `.claude/upstream.json` に記録されます。

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
- `tools/dashboard.py` は Claude Code がローカルに残すセッション記録（`~/.claude/projects/`）を読みます。この形式は Claude Code の内部仕様で、予告なく変わることがあります。動作確認した Claude Code は 2.1.287、Python は macOS 標準の 3.9.6 です

## ライセンス

MIT
