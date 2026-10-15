#!/usr/bin/env bash
# SessionEnd hook — /done し忘れを検知して .claude/pending に残す。
# この時点で Claude は動けないので、検知して次回の SessionStart に持ち越すだけ。
#
# 判定: 「未コミットの変更がある」= 作業した、かつ
#       「今日どの CASE.md も更新されていない」= 記録していない
# 読むだけのセッションでは変更が出ないので、余計な警告は出ない。
set -uo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo" || exit 0

# セッション登録（SessionStart が書いた .tmp/sessions/<session_id>.json）を消す
hook_in=""
[ -t 0 ] || hook_in=$(cat)
sid=$(printf '%s' "$hook_in" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("session_id",""))' 2>/dev/null)
[ -n "$sid" ] && rm -f ".tmp/sessions/$sid.json"

# private/ は .gitignore されているため git status に出ない。
# private のみで作業したセッションは検知できない（既知の限界）。
dirty=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')
[ "${dirty:-0}" -gt 0 ] || { rm -f .claude/pending; exit 0; }

today=$(date +%Y-%m-%d)
recorded=0
for f in cases/*/CASE.md private/*/CASE.md; do
  [ -f "$f" ] || continue
  if [ "$(date -r "$f" +%Y-%m-%d)" = "$today" ]; then
    recorded=1
    break
  fi
done

if [ "$recorded" -eq 1 ]; then
  rm -f .claude/pending
else
  printf '%s に未コミットの変更 %s 件があるまま終了（CASE.md 未更新）\n' "$today" "$dirty" > .claude/pending
fi

exit 0
