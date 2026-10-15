#!/usr/bin/env bash
# SessionStart hook — 進行中の案件と、前回 /done し忘れた形跡を表示する。
# ユーザーへの表示（systemMessage）と Claude への文脈（additionalContext）の両方を出す。
set -uo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo" || exit 0

# hook の入力（JSON）。手で実行したときは標準入力が端末なので読まない
hook_in=""
[ -t 0 ] || hook_in=$(cat)

# セッション登録: ダッシュボードの「実行中」判定用に .tmp/sessions/<session_id>.json を書く。
# PID は hook の親を辿って claude 本体を探す。SessionEnd hook が消す
HOOK_IN="$hook_in" HOOK_PID="$$" python3 - <<'EOF' 2>/dev/null
import json, os, subprocess, datetime
d = json.loads(os.environ.get("HOOK_IN") or "{}")
sid = d.get("session_id")
if sid:
    pid = int(os.environ["HOOK_PID"]); claude_pid = None
    for _ in range(8):
        out = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)], capture_output=True, text=True).stdout.split()
        if len(out) < 2: break
        ppid, comm = int(out[0]), out[1]
        if comm.endswith("claude"): claude_pid = pid; break
        pid = ppid
    os.makedirs(".tmp/sessions", exist_ok=True)
    json.dump({"session_id": sid, "pid": claude_pid, "cwd": d.get("cwd"), "transcript_path": d.get("transcript_path"),
               "started": datetime.datetime.now().isoformat(timespec="seconds"), "source": d.get("source")},
              open(f".tmp/sessions/{sid}.json", "w"))
EOF

# 進行中の案件（解析は tools/dashboard.py に集約。ダッシュボード HTML も同時に更新する）
dashboard="tools/dashboard.py"
rows=""
rc=1
if [ -f "$dashboard" ]; then
  rows=$(python3 "$dashboard" --text 2>/dev/null); rc=$?
  python3 "$dashboard" --html >/dev/null 2>&1
fi

msg=""
if [ "$rc" -ne 0 ]; then
  msg="■ 進行中の案件: 一覧の生成に失敗（python3 $dashboard --text を手で実行して確認）"$'\n'
elif [ -n "$rows" ]; then
  msg="■ 進行中の案件"$'\n'"${rows}"$'\n'
else
  msg="■ 進行中の案件はありません"$'\n'
fi

# 前回 /done し忘れの形跡
if [ -f .claude/pending ]; then
  msg="${msg}"$'\n'"■ 前回 /done していません: $(cat .claude/pending)"$'\n'
fi

# 未コミットの変更
dirty=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')
if [ "${dirty:-0}" -gt 0 ]; then
  msg="${msg}"$'\n'"■ 未コミットの変更が ${dirty} 件あります"$'\n'
fi

msg="${msg}"$'\n'"■ 終了時は /done <slug>"$'\n'"■ 迷ったら「〜はどうするんだっけ」と聞く（記録を探して出典付きで答える）。マニュアルは docs/manual.html、案件の一覧と未決は open .tmp/dashboard.html"

MSG="$msg" python3 -c '
import json, os
m = os.environ["MSG"]
print(json.dumps({
    "systemMessage": m,
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": (
            "agent-casebook の現在の状況:\n" + m +
            "\n\nユーザーの依頼が上記のいずれかの案件の続きなら、"
            "新しい案件を作らずその CASE.md を読んでから作業する。"
        ),
    },
}, ensure_ascii=False))
'
