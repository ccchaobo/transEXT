[CmdletBinding()]
param(
    [string]$PythonPath = '',
    [ValidateRange(1,65535)][int]$Port = 8765,
    [string]$DataDir = ''
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
if (-not $DataDir) { $DataDir = Join-Path $taskRoot 'var' }
$DataDir = [IO.Path]::GetFullPath($DataDir)
New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
if (-not $PythonPath) {
    $taskCandidates = @((Join-Path $taskRoot '.venv/Scripts/python.exe'), 'E:/anaconda/python.exe')
    $PythonPath = $taskCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $PythonPath) { $PythonPath = (Get-Command python -ErrorAction Stop).Source }
}
$taskUrl = "http://127.0.0.1:$Port"
$taskStatePath = Join-Path $DataDir 'reader-processes.json'
$taskState = $null
if (Test-Path -LiteralPath $taskStatePath) {
    try { $taskState = Get-Content -LiteralPath $taskStatePath -Raw | ConvertFrom-Json } catch { $taskState = $null }
}
$env:RR_DATA_DIR = $DataDir
$env:RR_PORT = [string]$Port
$taskStamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$taskHealth = $null
try { $taskHealth = Invoke-RestMethod "$taskUrl/health" -TimeoutSec 3 } catch { }
if ($taskHealth) {
    if ($taskHealth.status -ne 'ok' -or [IO.Path]::GetFullPath($taskHealth.data_dir) -ne $DataDir) {
        throw '该端口正在运行其他服务或数据目录，请更换端口。'
    }
} else {
    $taskApi = Start-Process -FilePath $PythonPath -ArgumentList '-m','rr_api' -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $DataDir "api-$taskStamp.log") -RedirectStandardError (Join-Path $DataDir "api-$taskStamp.err.log")
    for ($taskTry = 0; $taskTry -lt 20; $taskTry++) {
        try { $taskHealth = Invoke-RestMethod "$taskUrl/health" -TimeoutSec 1; break } catch { }
        $taskApi.Refresh()
        if ($taskApi.HasExited) { throw "阅读器启动失败，请查看 $DataDir/api-$taskStamp.err.log" }
        Start-Sleep -Milliseconds 300
    }
    if (-not $taskHealth -or $taskHealth.status -ne 'ok' -or [IO.Path]::GetFullPath($taskHealth.data_dir) -ne $DataDir) { throw '阅读器未就绪，请检查日志或端口。' }
}
$taskWorker = $null
if ($taskState -and $taskState.data_dir -eq $DataDir -and $taskState.worker) {
    try {
        $taskCandidate = Get-Process -Id $taskState.worker.pid -ErrorAction Stop
        $taskCommand = (Get-CimInstance Win32_Process -Filter "ProcessId=$($taskCandidate.Id)").CommandLine
        if ($taskCandidate.StartTime.ToUniversalTime().Ticks -eq ([datetimeoffset]$taskState.worker.started_utc).UtcDateTime.Ticks -and $taskCommand -match '-m\s+rr_worker(?:\s|$)') { $taskWorker = $taskCandidate }
    } catch { }
}
if (-not $taskWorker) {
    $taskWorker = Start-Process -FilePath $PythonPath -ArgumentList '-m','rr_worker','--poll','2' -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $DataDir "worker-$taskStamp.log") -RedirectStandardError (Join-Path $DataDir "worker-$taskStamp.err.log")
    Start-Sleep -Milliseconds 700
    $taskWorker.Refresh()
    if ($taskWorker.HasExited) { throw "后台任务启动失败，请查看 $DataDir/worker-$taskStamp.err.log" }
}
@{data_dir=$DataDir; port=$Port; worker=@{pid=$taskWorker.Id; started_utc=$taskWorker.StartTime.ToUniversalTime().ToString('o')}} | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $taskStatePath -Encoding utf8
Write-Host "阅读器和后台任务已就绪：$taskUrl"
Write-Host "现有文献：$($taskHealth.documents)；日志：$DataDir"
