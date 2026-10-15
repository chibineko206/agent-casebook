#!/usr/bin/env bash
# 公開前の点検。リポ内（.git と .tmp を除く）に次が残っていないかを調べ、1 件でもあれば終了コード 1。
#   - 絶対パス（/Users/<名前>、/home/<名前>）
#   - メールアドレス（GitHub の noreply 形式と noreply@anthropic.com は除く）
#   - 禁止語の一覧（~/.config/agent-casebook/forbidden-words。1 行 1 語、# で始まる行は無視）
# 禁止語の一覧はリポの外に置く。公開リポに入れると、隠したい語がそこに並ぶため。
set -uo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo" || exit 2
words="${AGENT_CASEBOOK_FORBIDDEN_WORDS:-$HOME/.config/agent-casebook/forbidden-words}"
rc=0

grep_repo() { grep -rnI -E "$1" --exclude-dir=.git --exclude-dir=.tmp --exclude-dir=node_modules . 2>/dev/null; }

report() {  # $1: 見出し, $2: ヒット行（パイプの中で呼ぶと rc が子シェルに閉じるため、引数で渡す）
  [ -n "$2" ] || return 0
  printf '■ %s\n%s\n\n' "$1" "$2" | cut -c1-200
  rc=1
}

report "絶対パス" "$(grep_repo '/(Users|home)/[A-Za-z0-9._-]+')"
report "メールアドレス" "$(grep_repo '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' \
  | grep -v -E '[0-9]+\+[A-Za-z0-9-]+@users\.noreply\.github\.com|noreply@anthropic\.com')"

if [ -f "$words" ]; then
  pat=$(grep -v '^#' "$words" | sed '/^[[:space:]]*$/d' | paste -sd'|' -)
  [ -n "$pat" ] && report "禁止語（${words}）" "$(grep_repo "$pat")"
else
  printf '（禁止語の一覧 %s が無いので、絶対パスとメールだけ点検した）\n' "$words"
fi

[ "$rc" -eq 0 ] && echo "check-public: 問題なし"
exit "$rc"
