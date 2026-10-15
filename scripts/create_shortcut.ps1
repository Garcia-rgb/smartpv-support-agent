param(
    [Parameter(Mandatory=$true)][string]$ShortcutPath,
    [Parameter(Mandatory=$true)][string]$TargetPath,
    [string]$TargetArguments = '',
    [string]$WorkingDirectory = '',
    [string]$IconPath = ''
)
$ErrorActionPreference = 'Stop'
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($ShortcutPath)
$shortcut.TargetPath = $TargetPath
$shortcut.Arguments = $TargetArguments
$shortcut.WorkingDirectory = $WorkingDirectory
$shortcut.WindowStyle = 1
if ($IconPath) { $shortcut.IconLocation = $IconPath + ',0' }
$shortcut.Save()
