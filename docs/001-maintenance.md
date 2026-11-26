# 保守と配布の運用ルール

このリポ（agent-casebook）は、GitHub の Template repository として仕組みを配布する。利用者は「Use this template」で自分の非公開リポジトリを作り、日々の記録はそちらに書く。
読み手は保守する本人。主題は「仕組みの変更をどこで行い、どう配り、何を記録するか」の 1 点。

## 結論

**仕組みはこのリポでしか変えず、利用側のリポは取り込むだけにする。** 変更の流れを一方向に固定すると、二重管理と食い違いが起きない。

## 1. 役割の固定

| | agent-casebook（このリポ。テンプレート） | 利用側のリポ（テンプレートから作る。非公開） |
|---|---|---|
| 役割 | 仕組みの正。配布元 | 日々の利用。案件の記録 |
| 変えるもの | `CLAUDE.md`、`.claude/`、`tools/`、`templates/`、`docs/` | `cases/`、`INDEX.md`、`private/`、`.claude/rules/` |
| 記録の置き場 | 仕組みの開発記録は `docs/changelog.md`。`cases/` は空で配る | 案件は `cases/<slug>/CASE.md`。取り込んだ版は `.claude/upstream.json` が持つ |
| git の履歴 | 配布元の履歴 | 配布元とつながらない（テンプレートの性質） |

テンプレートは `cases/` を空（`.gitkeep`）、`INDEX.md` を案件の行が無い表にして配る。開発記録を `cases/` に置くと、利用者のリポジトリに他人の案件が入ってしまう。

**改修の作業は、案件の記録を置く場所が要る。** 改修のセッション中は `cases/dev-<題材>/CASE.md` に書いてよい（`/done` が使える）。ただしテンプレートに載せないので、改修を締めるときに判断と未決を `docs/changelog.md` へ要約し、`cases/dev-<題材>/` は main に入れない（PR の最後のコミットで外す）。

## 2. 変更の流れ

1. 仕組みの改修はこのリポでブランチを切って行い、動作確認して PR にする。互換性を壊す変更はメジャーを上げてタグを打つ（`v0.1.0` から）
2. push の前に `tools/check-public.sh` を通す（4 章）
3. 利用者は `tools/update-from-upstream.py` で取り込む。履歴がつながらないので `git pull` は使えない。取り込んだ版は `.claude/upstream.json` に残る
4. 利用側で不具合に気付いたら、利用側で直さず、このリポで直して取り込み直す。急ぎで手元を直した場合は、その日のうちに同じ変更をこちらに入れる

取り込み対象は `tools/update-from-upstream.py` の `MANAGED` が正で、README の記述と合わせる。

```
CLAUDE.md
.claude/settings.json
.claude/hooks/        （*.local.sh を除く）
.claude/commands/
tools/
templates/
docs/manual.html
docs/chat-writeback-prompt.md
```

取り込みの対象外は、`cases/`、`private/`、`INDEX.md`、`.claude/rules/`、`.claude/settings.local.json`、`.claude/hooks/*.local.sh`、`.claude/upstream.json`。ディレクトリ単位の対象は配布元と同じ内容に揃えるので、利用側が足したファイルは消える。

submodule や subtree は使わない。取り込み対象は 20 ファイル程度なので、コピーで足りる。

## 3. 配置を揃える

利用側の配置はこのリポと同じにする。スクリプトは `tools/`、URL スキームは `casebook://`、Launcher は `Casebook Launcher.app`。配置が違うと取り込みのたびに差分が出る。

`tools/` のスクリプトは、`CLAUDE.md` と `.claude/` を持つ最も近い親ディレクトリをリポとみなすので、置き場所を変えても動く。

## 4. 公開前の点検

push の前に `tools/check-public.sh` を通し、0 件でなければ止める。点検するのは次の 3 つ。

- 絶対パス（`/Users/<名前>`、`/home/<名前>`）
- メールアドレス（GitHub の noreply 形式を除く）
- 手元で決めた禁止語（個人名、会社名、製品名など）。語の一覧はリポの外（`~/.config/agent-casebook/forbidden-words`）に置く。一覧そのものを公開リポに入れると、隠したい語がそこに並ぶため

このリポは公開なので、業務や個人の案件は書かない。

## 5. アカウントを混ぜない

- `git config user.name` / `user.email` はリポごとに設定する。`~/.gitconfig` に次を書いておくと、このディレクトリでは自動で個人アカウントの作者になり、切り替え忘れを防げる

  ```
  [includeIf "gitdir:~/code/agent-casebook/"]
      path = ~/.gitconfig-personal
  ```

  `~/.gitconfig-personal` に `[user] name = ... / email = <ID>+<ユーザー名>@users.noreply.github.com` を書く
- `gh` は push のときだけ `gh auth switch --user <個人アカウント>` で切り替え、終わったら戻す

## 6. 判断の置き場

同じ話を両方のリポに書くと後で食い違う。線引きは次のとおり。

- **仕組みの判断**（書式、hook の挙動、ダッシュボードの項目、見送った案）→ このリポの `docs/changelog.md` と `docs/000-design.md`
- **運用の判断**（取り込んだ版、手元で困ったこと、こちらへ出す改善提案）→ 利用側のリポの運用案件

## 7. 依存先の記録

`tools/dashboard.py` が読むセッション記録（`~/.claude/projects/`）と、hook が受け取る入力は Claude Code の内部仕様で、予告なく変わる。壊れたら直す前提とし、README の「動作確認した Claude Code のバージョン」を更新する。

## 8. テンプレート設定（公開側の操作）

GitHub のリポジトリ設定の「Template repository」を有効にする。公開側の設定変更なので、実行前に保守者が確認する。

```bash
gh api -X PATCH repos/chibineko206/agent-casebook -f is_template=true
```
