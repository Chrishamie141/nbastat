[CmdletBinding(SupportsShouldProcess)]
param([ValidateSet('Install','Status','Stop','Restart','Run')][string]$Action = 'Status')
$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$python = Join-Path $repo '.venv\Scripts\python.exe'
$runtime = Join-Path $repo '.runtime\week1'
$taskName = 'SmartBets-Week1-2026'
Set-Location -LiteralPath $repo
if (-not (Test-Path -LiteralPath $python)) { throw 'Create the project .venv and install requirements.txt first.' }

if ($Action -eq 'Run') {
    New-Item -ItemType Directory -Path $runtime -Force | Out-Null
    $env:DATABASE_URL = 'sqlite:///' + ((Join-Path $repo 'backtesting\market_capture\nfl_2026_reg1_v1.db') -replace '\\','/')
    $env:DRY_RUN = 'true'
    $env:SOCIAL_AUTO_PUBLISH = 'false'
    $env:PYTHONUNBUFFERED = '1'
    $log = Join-Path $runtime ('worker-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
    # Native stdout and stderr remain local. Do not print environment variables.
    & $python -m backtesting.week1_worker run *> $log
    exit $LASTEXITCODE
}
if ($Action -eq 'Status') {
    & $python -m backtesting.week1_worker status
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue | Select-Object TaskName,@{Name='State';Expression={$_.State.ToString()}} | ConvertTo-Json
    Get-ScheduledTaskInfo -TaskName $taskName -ErrorAction SilentlyContinue | Select-Object LastRunTime,LastTaskResult,NextRunTime | ConvertTo-Json
    exit 0
}
if ($Action -in @('Stop','Restart')) {
    # Disable periodic/login launch first; do not terminate a DB transaction.
    $existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existingTask) { Disable-ScheduledTask -TaskName $taskName | Out-Null }
    & $python -m backtesting.week1_worker stop
    if ($LASTEXITCODE -ne 0) { throw 'Graceful stop request failed.' }
    $deadline = (Get-Date).AddSeconds(120)
    do {
        $status = & $python -m backtesting.week1_worker status | ConvertFrom-Json
        if ($LASTEXITCODE -ne 0) { throw 'Cannot safely verify worker stopped.' }
        $taskState = (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue).State
        if (-not $status.worker_running -and $taskState -ne 'Running') { break }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    if ($status.worker_running -or $taskState -eq 'Running') { throw 'Worker is still draining; inspect status/logs. It was NOT forcibly terminated.' }
    if ($Action -eq 'Stop') { Write-Output 'Stopped gracefully; task disabled until Restart.'; exit 0 }
    Enable-ScheduledTask -TaskName $taskName | Out-Null
    Start-ScheduledTask -TaskName $taskName
    exit 0
}
if ($Action -eq 'Install' -and $PSCmdlet.ShouldProcess($taskName,'Register current-user login and recovery task (X disabled)')) {
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        throw 'Task already exists. Use Status/Restart; do not silently overwrite its settings.'
    }
    & $python -m backtesting.week1_worker setup
    if ($LASTEXITCODE -ne 0) { throw 'Offline preflight/backup/draft setup failed; task not installed.' }
    # Restrict local signing material/logs to this Windows user and SYSTEM.
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    & icacls.exe $runtime /inheritance:r /grant:r "*${sid}:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot secure local runtime directory.' }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $script = Join-Path $repo 'tools\week1-task.ps1'
    $arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $script + '" -Action Run'
    $taskAction = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument $arguments -WorkingDirectory $repo
    $login = New-ScheduledTaskTrigger -AtLogOn -User $identity
    # Wake/recovery trigger every 5 minutes. IgnoreNew plus the OS file lock prevent duplication.
    $recovery = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
    $principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $taskName -Action $taskAction -Trigger @($login,$recovery) -Settings $settings -Principal $principal -Description 'Existing frozen Week1 capture/grade worker; local drafts only. Login required after reboot.' | Out-Null
    Start-ScheduledTask -TaskName $taskName
    Write-Output 'Installed and started. After reboot, sign in once. Network and permitted Windows wake timers are required.'
}
