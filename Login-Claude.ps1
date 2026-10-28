# Sign in the Claude CLI used by MemoRelay (subscription account, opens the browser).
# Strips Claude/Anthropic env vars so the login is not tied to another Claude session.
Get-ChildItem env: | Where-Object { $_.Name -match '^(CLAUDE|ANTHROPIC)' } |
    ForEach-Object { Remove-Item -LiteralPath "env:$($_.Name)" -ErrorAction SilentlyContinue }
$exe = Get-ChildItem "$env:LOCALAPPDATA\Claude-Data\*\claude-code\*\*\claude.exe" |
    Sort-Object LastWriteTime | Select-Object -Last 1
& $exe.FullName auth login
& $exe.FullName auth status
