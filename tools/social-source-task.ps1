param(
    [ValidateSet('Install','Run','Status','Disable')]
    [string]$Action = 'Status'
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$taskName = 'SmartBets Social Source Sync'
$python = Join-Path $repo '.venv\Scripts\python.exe'
$weekDb = Join-Path $repo 'backtesting\market_capture\nfl_2026_reg1_v1.db'

if ($Action -eq 'Run') {
    & $python -m backtesting.social_marketing remote-sync --week-db $weekDb
    exit $LASTEXITCODE
}

if ($Action -eq 'Status') {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if (-not $task) {
        [pscustomobject]@{ installed = $false; task = $taskName } | ConvertTo-Json
        exit 0
    }
    $info = Get-ScheduledTaskInfo -TaskName $taskName
    [pscustomobject]@{
        installed = $true
        state = $task.State.ToString()
        last_run = $info.LastRunTime
        last_result = $info.LastTaskResult
        next_run = $info.NextRunTime
    } | ConvertTo-Json
    exit 0
}

if ($Action -eq 'Disable') {
    Disable-ScheduledTask -TaskName $taskName | Out-Null
    [pscustomobject]@{ disabled = $true; task = $taskName } | ConvertTo-Json
    exit 0
}

if (-not (Test-Path -LiteralPath $python)) { throw 'Project virtual environment is missing.' }
if (-not (Test-Path -LiteralPath $weekDb)) { throw 'Frozen Week 1 experiment database is missing.' }
& $python -m backtesting.social_marketing preview-week | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Seven-day social preview failed; task was not installed.' }

$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$script = Join-Path $repo 'tools\social-source-task.ps1'
$arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $script + '" -Action Run'
$taskAction = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument $arguments -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Daily -At '8:30 AM'
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $taskAction -Trigger $trigger -Settings $settings -Principal $principal -Description 'Uploads signed aggregate SmartBets evidence before the 9 AM ET cloud social cron. Never publishes and never writes prediction databases.' -Force | Out-Null
[pscustomobject]@{ installed = $true; task = $taskName; schedule = '08:30 local daily'; publishes = $false } | ConvertTo-Json
