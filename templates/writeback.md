---
description: 別リポやリポ外で開いた Code セッションの結果を ~/agent-casebook の案件 CASE.md に書き戻す
argument-hint: <案件slug>
allowed-tools: Bash(python3:*), Bash(git:*), Bash(ls:*), Bash(cp:*), Bash(mv:*), Read, Write, Edit
---

このセッションの結果を `~/agent-casebook` の案件 `$1` に書き戻す。
`/done` と同じ記録を、**別のディレクトリで開いた Code セッションから**残すためのコマンド。作業中のリポジトリには何も書かない。
決定的な処理は記録リポ側の `tools/casebook.py` が行う。スクリプトは自分の置き場所から記録リポを見つけるので、
どのディレクトリから呼んでも `~/agent-casebook` に対して動く。

（記録リポの場所が違う場合は、このファイルの `~/agent-casebook` を自分の場所に書き換えて `~/.claude/commands/writeback.md` に置く）

## 1. 現状を出す

```
python3 ~/agent-casebook/tools/casebook.py start $1
```

案件の所在、ヘッダ、最新エントリ、前回の未決事項、記録リポの git の状態が出る。

- 引数が空のときは `start` を引数なしで実行し、一覧からこのセッションの内容に合う案件を推定する。一意に決まらなければユーザーに確認する
- 案件が無ければ新規。`python3 ~/agent-casebook/tools/casebook.py new <slug> --title "<案件名>" --category <分類> [--private] [--artifacts "<成果物>"] [--related "<関連>"]`

## 2. 成果物の置き場所を決める

このセッションで作ったファイルは、記録リポの `CLAUDE.md` の「リポジトリ外のファイルを参照したとき」の表で判定する。

- 作業中のリポジトリが保守対象なら、ファイルは動かさず、エントリに対象リポ / ブランチ / PR を書く
- 使い捨て（検証コード・下書き・クエリなど）なら `~/agent-casebook/cases/<slug>/` へ移す。移動は元の場所からファイルが消えるので、許可を得てから行う

## 3. 前回の未決事項を閉じる（問答）

`start` が出した前回の未決事項を 1 件ずつ列挙し、「やった / やらない / 残す」を推定して添え、ユーザーに確認する。
閉じ方と未決事項の定義は記録リポの `.claude/commands/done.md` の 3 節・4 節と同じ。

## 4. エントリを書く

`~/agent-casebook/.tmp/writeback-entry.md` に、次の形で書く。セッション中の実際の作業に基づき、推測で埋めない。

```markdown
## <YYYY-MM-DD> [Code] <このセッションの要約>

作業場所: `<リポジトリ名またはディレクトリのパス>`

### やったこと
### 判断
### 未決事項
```

- `作業場所` は `/done` のエントリには無い行。別の場所で作業した記録として必ず書く
- 3 節の書き方と未決事項の規則は `/done` と同じ。未決の各行は `- 決める: ` `- やる: ` `- 確かめる: ` のどれかで始め、無ければ `- なし`

## 5. 締める

```
python3 ~/agent-casebook/tools/casebook.py finish $1 --entry ~/agent-casebook/.tmp/writeback-entry.md [--state <進行中|保留|完了>] [--artifacts "<成果物>"]
```

スクリプトが書式を検査して追記し、ヘッダと INDEX を更新し、ダッシュボードを再生成し、
記録リポで**この案件のファイルだけ**をコミットする（他案件の未コミット変更は巻き込まない）。
`private/` の案件はコミット対象が無いのが正しい。push は行わない。

## 6. 報告する

スクリプトの出力をもとに、`CASE.md` のパス、追記したエントリの見出し、コミットハッシュを 1 行ずつ示す。
失敗した手順があれば明示する。
