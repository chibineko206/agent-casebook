# 配布をテンプレートリポジトリ方式に変える

分類: 運用
状態: 進行中
成果物: なし
関連: なし

---

## 2026-11-26 [Code] 配布を GitHub テンプレート方式に変更。開発記録を docs/changelog.md へ移し、取り込みスクリプトを追加

### やったこと

- `cases/casebook-ops/` を `docs/changelog.md` に要約して移し、`cases/` は `.gitkeep` だけ、`INDEX.md` は案件の行が無い表にした
- `README.md` のセットアップを「Use this template で非公開リポジトリを作る」に書き換え、仕組みの更新の取り込み手順を足した。`docs/manual.html` は構造図と「引き換えに失うもの」を直し、「仕組みの更新」の節を足した。`docs/000-design.md` と `docs/001-maintenance.md` もテンプレート前提に直した
- `tools/update-from-upstream.py` を追加した。配布元を浅く clone して仕組みのファイルだけを置き換え、取り込んだ版を `.claude/upstream.json` に記録する。`--dry-run`、`--ref`、`--repo` あり
- 模擬の配布元と利用側（利用側の `cases/`、`.claude/rules/`、`session-start.local.sh`、独自変更した `CLAUDE.md`、削除したコマンド、不要になった `tools/` のファイルを置いたもの）で動作を確認した。仕組みのファイルだけが更新・追加・削除され、利用側のファイルは残った。未コミットの変更があると止まる
- `gh api -X PATCH repos/chibineko206/agent-casebook -f is_template=true` で Template repository に設定した（ユーザーの確認済み）。結果は `is_template: true`
- `tools/check-public.sh` は問題なし。ただし禁止語の一覧（`~/.config/agent-casebook/forbidden-words`）がこの環境に無く、絶対パスとメールだけの点検

### 判断

- 配布の型はテンプレートリポジトリ（ユーザー決定済み）。履歴が配布元とつながらないので、`origin` の外し忘れが構造上起きない
- 開発記録の置き場は `docs/changelog.md`（ユーザー確認済み）。この案件の `CASE.md` は改修中の記録として使い、PR の最後のコミットで `cases/` から外す。テンプレートに他人の案件を載せないため
- 取り込み対象は依頼の 5 項目に、既存の保守文書が対象としていた `templates/`、`docs/manual.html`、`docs/chat-writeback-prompt.md` を足した。マニュアルが古いまま残ると `CLAUDE.md` の参照先がずれるため
- ディレクトリ単位の対象は配布元と同じ内容に揃え、利用側が足したファイルは消す。ただし `*.local.sh` と `__pycache__` は残す。独自の追加は `.claude/rules/` と `session-start.local.sh` に書く規則とした
- 取り込みは自動コミットしない。差分を見てから利用者がコミットする
- `/done` の `finish` は `INDEX.md` に行を足すので、この案件では使わず手で追記した。テンプレートの `INDEX.md` は空の表で配るため

### 未決事項

- やる: PR を作るために push する（ユーザーの確認が要る）。自分、確認が取れ次第
- やる: PR の最後のコミットで `cases/dev-template-distribution/` を外す。自分、PR 作成の直前
- 確かめる: 設定済みのテンプレートから実際にリポジトリを作り、`update-from-upstream.py` が通るか。自分、PR のマージ後
- 確かめる: Template 設定はマージ前に済ませたため、マージまでの間は旧構成（`cases/casebook-ops/` あり）がテンプレートから作られる。マージを急ぐか、設定を一時的に戻すか。自分、今日中
- やる: 禁止語の一覧を置いて `check-public.sh` を通し直す。自分、push の前
