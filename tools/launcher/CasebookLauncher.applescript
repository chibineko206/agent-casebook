-- Casebook Launcher: casebook:// の URL を受けて launch.py に渡すだけのアプリ。
-- build.sh がプレースホルダ（LAUNCH_PY）を launch.py の実パスに置き換えてコンパイルし、Info.plist に URL スキームを登録する。
-- 判断や検証、Terminal の起動はすべて launch.py 側で行う。このアプリにはロジックを置かない
-- （このアプリから Apple Event やダイアログを出すと、URL スキーム起動時に落ちて -609 になった）。

on open location theURL
	set launchPy to "__LAUNCH_PY__"
	try
		do shell script "PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH python3 " & quoted form of launchPy & " " & quoted form of theURL
	end try
end open location

on run
	display dialog "Casebook Launcher は casebook:// の URL を受けて動くアプリです。ダッシュボード（.tmp/dashboard.html）のボタンから使います。" buttons {"OK"} default button "OK"
end run
