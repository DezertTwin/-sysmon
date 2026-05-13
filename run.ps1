$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
Start-Process -FilePath "cmd.exe" -ArgumentList "/k", "py `"$dir\sysmon.py`"" -WorkingDirectory $dir
