#!/usr/bin/env bash
# Casebook Launcher.app を ~/Applications/ に作り、casebook:// スキームを登録する。
# 再実行すると作り直す（launch.py を変えただけなら再実行は不要。アプリは URL を渡すだけ）。
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
app="$HOME/Applications/Casebook Launcher.app"
src="$here/CasebookLauncher.applescript"
tmp="$(mktemp -t launcher).applescript"

repo="$(cd "$here/../.." && pwd)"
sed -e "s|__LAUNCH_PY__|$here/launch.py|" -e "s|__LOG__|$repo/.tmp/launcher.log|" "$src" > "$tmp"
mkdir -p "$HOME/Applications"
rm -rf "$app"
osacompile -o "$app" "$tmp"
rm -f "$tmp"

python3 - "$app/Contents/Info.plist" <<'EOF'
import plistlib, sys
p = sys.argv[1]
with open(p, "rb") as f:
    info = plistlib.load(f)
info["CFBundleIdentifier"] = "local.agent-casebook.launcher"
info["CFBundleName"] = "Casebook Launcher"
info["CFBundleURLTypes"] = [{"CFBundleURLName": "agent-casebook dashboard", "CFBundleURLSchemes": ["casebook"]}]
info["LSUIElement"] = True  # Dock に出さない（このアプリは URL を渡すだけで UI を持たない）
# これが無いと macOS は Terminal への Apple Event を確認なしで拒否する（-1743）
info["NSAppleEventsUsageDescription"] = "ダッシュボードのボタンから Terminal を開くために使います。"
with open(p, "wb") as f:
    plistlib.dump(info, f)
EOF

# Launch Services に登録し直す
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$app" >/dev/null
echo "built: $app"
echo "試す: open 'casebook://case/<slug>?action=finder'"
