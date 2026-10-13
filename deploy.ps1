[CmdletBinding()]
param(
    [ValidateSet('Start','Stop','Status','Logs','Backup','Update')][string]$Action='Start',
    [ValidateRange(0,65535)][int]$Port=0,
    [string]$Project=''
)
$ErrorActionPreference='Stop'
$taskRoot=$PSScriptRoot
Push-Location $taskRoot
$taskPreviousPort=$env:RR_HTTP_PORT
$taskPreviousProject=$env:RR_COMPOSE_PROJECT
function Invoke-ReaderCompose {
    & docker compose --env-file .env.docker -f compose.yaml @args
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose 操作失败（退出码 $LASTEXITCODE）。" }
}
function Backup-Reader {
    $taskBackups=Join-Path $taskRoot 'backups'
    New-Item -ItemType Directory -Path $taskBackups -Force | Out-Null
    $taskStamp=[DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ')
    Invoke-ReaderCompose stop worker api
    try {
        Invoke-ReaderCompose run --rm --no-deps -T --user 0:0 --cap-add CHOWN --cap-add DAC_OVERRIDE -v "${taskBackups}:/backup" api python deploy/archive.py create /data "/backup/reader-$taskStamp.tar.gz"
    } finally {
        Invoke-ReaderCompose up -d --wait --wait-timeout 360 api worker
    }
}
try {
    Get-Command docker -ErrorAction Stop | Out-Null
    & docker info *> $null
    if ($LASTEXITCODE -ne 0) { throw 'Docker 引擎未就绪，请先启动 Docker Desktop 的 Linux 容器引擎。' }
    & docker compose version
    if ($LASTEXITCODE -ne 0) { throw '需要 Docker Compose v2.20 或更新版本。' }
    if (-not (Test-Path -LiteralPath .env.docker)) { Copy-Item -LiteralPath deploy/docker.env.example -Destination .env.docker }
    if ($Port) { $env:RR_HTTP_PORT=[string]$Port }
    if ($Project) { $env:RR_COMPOSE_PROJECT=$Project }
    Invoke-ReaderCompose config --quiet
    switch ($Action) {
        Start { Invoke-ReaderCompose up -d --build --wait --wait-timeout 360; Invoke-ReaderCompose ps; Invoke-ReaderCompose port api 8765 }
        Stop { Invoke-ReaderCompose down }
        Status { Invoke-ReaderCompose ps }
        Logs { Invoke-ReaderCompose logs --tail=150 -f }
        Backup { Backup-Reader }
        Update { Backup-Reader; Invoke-ReaderCompose up -d --build --wait --wait-timeout 360 }
    }
    if ($Action -in @('Start','Update')) { Write-Host '部署完成。打开 api 映射的本机端口，在“翻译设置”中配置服务。' }
} finally {
    $env:RR_HTTP_PORT=$taskPreviousPort
    $env:RR_COMPOSE_PROJECT=$taskPreviousProject
    Pop-Location
}
