# Shared by the production short recovery task and disposable scheduler integration test.
function Invoke-SmartBetsTaskRecovery {
    param([Parameter(Mandatory)][string]$TaskName)
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    if ($task.State -eq 'Disabled') { return 'WORKER_DISABLED_NO_ACTION' }
    if ($task.State -in @('Running','Queued')) { return 'WORKER_ACTIVE_NO_ACTION' }
    Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    return 'WORKER_START_REQUESTED'
}
