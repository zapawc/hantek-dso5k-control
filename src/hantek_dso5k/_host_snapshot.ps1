$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$processes = @(Get-CimInstance -ClassName Win32_Process | ForEach-Object {
    [ordered]@{
        pid = [int]$_.ProcessId
        ppid = [int]$_.ParentProcessId
        name = $_.Name
        exe = $_.ExecutablePath
        created = if ($_.CreationDate) { $_.CreationDate.ToUniversalTime().ToString('o') } else { $null }
        command = $_.CommandLine
    }
})
if (-not $processes.Count) { throw 'Empty process census' }
$devices = @(Get-PnpDevice -PresentOnly | Where-Object {
    $_.InstanceId -match '^USB\\VID_049F&PID_505A\\'
} | ForEach-Object {
    $service = (Get-ItemProperty -LiteralPath ('HKLM:\SYSTEM\CurrentControlSet\Enum\' + $_.InstanceId)).Service
    [ordered]@{ instance = $_.InstanceId; service = $service; status = [string]$_.Status }
})
$redirector = Get-Command usbipd.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
$service = Get-Service usbipd -ErrorAction SilentlyContinue
$assignment = 'unknown'
if ($redirector) {
    $lines = @(& $redirector.Source list)
    if ($LASTEXITCODE -ne 0) { throw 'USB/IP assignment query failed' }
    $target = @($lines | Where-Object { $_ -match '\b049f:505a\b|VID_049F&PID_505A' })
    if ($target.Count -eq 1 -and $target[0] -match '^\s*\S+\s+049f:505a\s+.+\s+Not shared\s*$') {
        $assignment = 'not_shared'
    }
} elseif (-not $service -or $service.Status -ne 'Running') {
    $assignment = 'no_usbip_service'
}
[ordered]@{processes=$processes;devices=$devices;assignment=$assignment} | ConvertTo-Json -Depth 6 -Compress
