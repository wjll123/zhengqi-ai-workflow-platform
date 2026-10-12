-- 政企存量平台 · 独立启动（脱离父进程）
-- 用法：先在「脚本编辑器」打开本文件，或在本目录执行：
--     osascript 运行平台.applescript
--
-- ⚠️ projPath 会自动取「本脚本所在目录」，无需手工改。
--    若在某些环境下取不到，可手工改成平台的绝对路径。

set projPath to ""

try
	-- 取脚本自身所在目录（去掉文件名）
	set myPath to POSIX path of (path to me)
	set AppleScript's text item delimiters to "/"
	set pathParts to text items of myPath
	if (count of pathParts) > 1 then
		set projPath to (items 1 thru -3 of pathParts) as string
		set AppleScript's text item delimiters to "/"
		set projPath to "/" & projPath
	end if
	set AppleScript's text item delimiters to ""
end try

if projPath is "" then
	display dialog "无法自动识别平台目录，请手工编辑本脚本中的 projPath。" buttons {"好"} default button 1
	return
end if

set cmd to "cd '" & projPath & "' && " & ¬
	"pkill -f app.web_app 2>/dev/null; " & ¬
	"nohup ./.venv/bin/python3 -m app.web_app > data/logs/server.log 2>&1 & " & ¬
	"sleep 3 && open http://127.0.0.1:8848"

do shell script cmd
