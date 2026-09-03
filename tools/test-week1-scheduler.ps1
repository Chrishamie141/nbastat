# Disposable Windows integration probe. No app imports, databases, providers, or secrets.
[CmdletBinding()]
param([ValidateSet('Audit','Hold','FailOnce','Recover')][string]$Action='Audit',[string]$Marker)
$ErrorActionPreference='Stop'
if ($Action -eq 'Recover') {
    if ($Marker -notmatch '^SmartBets-Scheduler-Audit-[a-f0-9]{32}$') { throw 'Only disposable audit tasks may be targeted.' }
    . (Join-Path $PSScriptRoot 'week1-task-recovery.ps1')
    Invoke-SmartBetsTaskRecovery -TaskName $Marker
    exit 0
}
if ($Action -eq 'Hold') { Start-Sleep -Seconds 20; exit 0 }
if ($Action -eq 'FailOnce') {
    if (-not (Test-Path -LiteralPath $Marker)) {
        New-Item -ItemType File -Path $Marker | Out-Null
        exit 7
    }
    exit 0
}
$name='SmartBets-Scheduler-Audit-'+[Guid]::NewGuid().ToString('N')
$recoveryName=$name+'-Recovery'
$repo=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$dir=Join-Path $repo '.runtime\scheduler-audit'
New-Item -ItemType Directory -Path $dir -Force | Out-Null
$markerFile=Join-Path $dir ($name+'.marker')
$identity=[Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal=New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 3)
$exe="$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$base='-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+$PSCommandPath+'" -Action '
try {
    $hold=New-ScheduledTaskAction -Execute $exe -Argument ($base+'Hold') -WorkingDirectory $repo
    Register-ScheduledTask -TaskName $name -Action $hold -Principal $principal -Settings $settings | Out-Null
    Start-ScheduledTask -TaskName $name
    Start-Sleep -Seconds 3
    $before=Get-ScheduledTaskInfo -TaskName $name
    Start-ScheduledTask -TaskName $name
    Start-Sleep -Seconds 2
    $during=Get-ScheduledTaskInfo -TaskName $name
    [pscustomobject]@{phase='duplicate_launch';state=(Get-ScheduledTask -TaskName $name).State.ToString();before=$before.LastTaskResult;after=$during.LastTaskResult;hex=('0x{0:X8}' -f $during.LastTaskResult)} | ConvertTo-Json
    Start-Sleep -Seconds 18
    [pscustomobject]@{phase='normal_completion';result=(Get-ScheduledTaskInfo -TaskName $name).LastTaskResult} | ConvertTo-Json
    $failure=New-ScheduledTaskAction -Execute $exe -Argument ($base+'FailOnce -Marker "'+$markerFile+'"') -WorkingDirectory $repo
    $settings.RestartCount=3
    $settings.RestartInterval='PT1M'
    Set-ScheduledTask -TaskName $name -Action $failure -Settings $settings | Out-Null
    Start-ScheduledTask -TaskName $name
    Start-Sleep -Seconds 4
    [pscustomobject]@{phase='intentional_failure';result=(Get-ScheduledTaskInfo -TaskName $name).LastTaskResult;marker=(Test-Path -LiteralPath $markerFile)} | ConvertTo-Json
    # Short waits keep diagnostic output responsive; production task is untouched.
    for ($attempt=0; $attempt -lt 14; $attempt++) {
        Start-Sleep -Seconds 5
        $info=Get-ScheduledTaskInfo -TaskName $name
        if ($info.LastTaskResult -eq 0) { break }
    }
    [pscustomobject]@{phase='native_failure_restart';result=$info.LastTaskResult;state=(Get-ScheduledTask -TaskName $name).State.ToString()} | ConvertTo-Json
    $recover=New-ScheduledTaskAction -Execute $exe -Argument ($base+'Recover -Marker '+$name) -WorkingDirectory $repo
    $recoverySettings=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 1)
    $trigger=New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds(7)
    Register-ScheduledTask -TaskName $recoveryName -Action $recover -Trigger $trigger -Principal $principal -Settings $recoverySettings | Out-Null
    for ($attempt=0; $attempt -lt 12; $attempt++) {
        Start-Sleep -Seconds 3
        $recoveryInfo=Get-ScheduledTaskInfo -TaskName $recoveryName
        $info=Get-ScheduledTaskInfo -TaskName $name
        if ($info.LastTaskResult -eq 0 -and $recoveryInfo.LastTaskResult -eq 0) { break }
    }
    [pscustomobject]@{phase='scheduled_recovery_after_failure';worker_result=$info.LastTaskResult;recovery_result=$recoveryInfo.LastTaskResult} | ConvertTo-Json
    if ($during.LastTaskResult -ne 2147946720 -or $info.LastTaskResult -ne 0 -or $recoveryInfo.LastTaskResult -ne 0) { throw 'Scheduler probe did not match expected behavior; inspect evidence.' }
} finally {
    # Only the unique, disposable task created above is removed. Marker evidence is retained.
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $recoveryName -Confirm:$false -ErrorAction SilentlyContinue
}
