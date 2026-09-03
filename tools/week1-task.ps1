[CmdletBinding(SupportsShouldProcess)]
param([ValidateSet('Install','Repair','Status','Stop','Restart','Run','Recover')][string]$Action = 'Status')
$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$python = Join-Path $repo '.venv\Scripts\python.exe'
$runtime = Join-Path $repo '.runtime\week1'
$taskName = 'SmartBets-Week1-2026'
$recoveryName = $taskName + '-Recovery'
Set-Location -LiteralPath $repo
if (-not (Test-Path -LiteralPath $python)) { throw 'Create the project .venv and install requirements.txt first.' }

function Register-Week1Recovery {
    param($Principal)
    if (Get-ScheduledTask -TaskName $recoveryName -ErrorAction SilentlyContinue) {
        throw 'Recovery task already exists; inspect it rather than overwriting it.'
    }
    $arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $PSCommandPath + '" -Action Recover'
    $recoveryAction = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument $arguments -WorkingDirectory $repo
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName $recoveryName -Action $recoveryAction -Trigger $trigger -Settings $settings -Principal $Principal -Description 'Short no-op while Week1 is running; starts stopped worker only. No database/provider access.' | Out-Null
}

if ($Action -eq 'Recover') {
    . (Join-Path $PSScriptRoot 'week1-task-recovery.ps1')
    Invoke-SmartBetsTaskRecovery -TaskName $taskName
    exit 0
}

if ($Action -eq 'Repair' -and $PSCmdlet.ShouldProcess($taskName,'Separate periodic recovery from long-running worker; preserve active process')) {
    $task = Get-ScheduledTask -TaskName $taskName
    if ($task.Actions.Count -ne 1 -or $task.Actions[0].Arguments -notlike ('*"' + $PSCommandPath + '" -Action Run')) {
        throw 'Unexpected existing task action; refusing to alter it.'
    }
    if (Get-ScheduledTask -TaskName $recoveryName -ErrorAction SilentlyContinue) { throw 'Already repaired; use Status.' }
    New-Item -ItemType Directory -Path $runtime -Force | Out-Null
    Export-ScheduledTask -TaskName $taskName | Set-Content -LiteralPath (Join-Path $runtime ('task-before-repair-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'.xml')) -Encoding Unicode
    $triggers = @($task.Triggers | Where-Object { -not ($_.CimClass.CimClassName -eq 'MSFT_TaskTimeTrigger' -and $_.Repetition.Interval -eq 'PT5M') })
    if ($triggers.Count -ne 1 -or $triggers[0].CimClass.CimClassName -ne 'MSFT_TaskLogonTrigger') { throw 'Unexpected trigger layout; refusing to alter it.' }
    Register-Week1Recovery -Principal $task.Principal
    $task.Settings.RestartCount = 3
    $task.Settings.RestartInterval = 'PT1M'
    Set-ScheduledTask -TaskName $taskName -Trigger $triggers -Settings $task.Settings | Out-Null
    Write-Output 'Repaired without stopping worker. Native failure retry: 1 minute x 3; separate recovery/wake check: 5 minutes.'
    exit 0
}

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
    foreach ($name in @($taskName,$recoveryName)) {
        $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        if ($task) {
            $info = Get-ScheduledTaskInfo -TaskName $name
            $meaning = switch ($info.LastTaskResult) { 0 {'SUCCESS'} 267009 {'RUNNING'} 2147946720 {'LAUNCH_REQUEST_REFUSED'} default {'OTHER_RESULT'} }
            [pscustomobject]@{TaskName=$name;State=$task.State.ToString();LastRunTime=$info.LastRunTime.ToString('o');LastTaskResult=$info.LastTaskResult;ResultHex=('0x{0:X8}' -f $info.LastTaskResult);ResultMeaning=$meaning;NextRunTime=$(if ($info.NextRunTime) {$info.NextRunTime.ToString('o')} else {$null})} | ConvertTo-Json
        }
    }
    exit 0
}
if ($Action -in @('Stop','Restart')) {
    # Disable periodic/login launch first; do not terminate a DB transaction.
    if (Get-ScheduledTask -TaskName $recoveryName -ErrorAction SilentlyContinue) { Disable-ScheduledTask -TaskName $recoveryName | Out-Null }
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
    if (Get-ScheduledTask -TaskName $recoveryName -ErrorAction SilentlyContinue) { Enable-ScheduledTask -TaskName $recoveryName | Out-Null }
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
    # Long-running worker is not retriggered periodically. Recovery is a separate short task.
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    $principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $taskName -Action $taskAction -Trigger $login -Settings $settings -Principal $principal -Description 'Existing frozen Week1 capture/grade worker; local drafts only. Login required after reboot.' | Out-Null
    Register-Week1Recovery -Principal $principal
    Start-ScheduledTask -TaskName $taskName
    Write-Output 'Installed and started. After reboot, sign in once. Network and permitted Windows wake timers are required.'
}
