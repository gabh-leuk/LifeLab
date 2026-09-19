<#
.SYNOPSIS
  LifeLab 上线常驻任务安装脚本（可重复执行）。
.DESCRIPTION
  安装四个任务计划程序项：
    LifeLab Server       登录时启动后端；异常退出自动重启（最多 3 次）。
    LifeLab Backup       每日 03:03 备份数据库（pg_dump -Fc，保留 14 份）。
    LifeLab Demo Reset   每日 03:17 重置 demo 账号（保留 AI 产物）。
    LifeLab Auto Review  每日 04:07 自动生成复盘（昨天日复盘；周一追加上周周复盘；
                         每月 1 号追加上月月复盘）。
  备份刻意排在重置之前：当天那份 dump 捕获的是「重置前」状态，含全部 AI 产物。
  自动复盘排最后：等库重置完再跑，且它本来就是幂等的，错过的日子靠
  StartWhenAvailable 补跑。
  目标文件不存在的任务会被跳过并告警 —— 所以任何一步单独提交后本脚本都能跑。
.EXAMPLE
  .\install_tasks.ps1
  安装（或更新）四个任务。
.EXAMPLE
  .\install_tasks.ps1 -Uninstall
  卸载四个任务。不影响脚本文件与数据。
.NOTES
  本机实测无需提权：四个任务的 Principal 都是 Interactive + Limited（跑在你当前登录
  会话里，不是 SYSTEM/提权身份）。若仍报「拒绝访问」，用管理员身份的 PowerShell 重跑一次。
#>
[CmdletBinding()]
param([switch]$Uninstall)

$ErrorActionPreference = "Stop"

$TaskNames = @(
    "LifeLab Server",
    "LifeLab Backup",
    "LifeLab Demo Reset",
    "LifeLab Auto Review"
)
$ScriptDir = $PSScriptRoot
$Backend = Split-Path -Parent $ScriptDir
$Python = Join-Path $Backend ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { $Python = "python" }
$UserId = "$env:USERDOMAIN\$env:USERNAME"

if ($Uninstall) {
    foreach ($name in $TaskNames) {
        if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
            Write-Host "已卸载：$name"
        } else {
            Write-Host "未安装：$name"
        }
    }
    return
}

function New-LifeLabTask {
    param(
        [string]$Name,
        [string]$Target,
        [string]$Arguments,
        [object]$Trigger,
        [string]$Description,
        [int]$RestartCount = 0,
        [string]$WorkingDirectory,
        # 可选：用这个程序去执行 $Target（例：wscript.exe 跑 .vbs）。给了它，就把
        # $Target 当命令行参数、把 $Launcher 当可执行文件。
        [string]$Launcher
    )
    if (-not (Test-Path $Target)) {
        Write-Warning "跳过 $Name：目标不存在 —— $Target"
        return
    }
    # 工作目录默认取目标文件所在目录；但直接调 python 的任务必须显式给 backend 目录，
    # 否则 cwd 落到 .venv\Scripts（python.exe 所在处）。serve.cmd / backup_db.cmd
    # 自己会 cd，或用不到 cwd，所以它们不需要这个参数。
    if (-not $WorkingDirectory) { $WorkingDirectory = Split-Path -Parent $Target }
    # -Argument 在 PowerShell 5.1 上有 ValidateNotNullOrEmpty：无参任务（serve.cmd）
    # 必须整个省掉这个参数，传 "" 会在参数绑定阶段就抛错，一个任务也装不上。
    if ($Launcher) {
        # Launcher 分支的 Argument 就是 $Target（必然非空），无 ValidateNotNull 之虞。
        $actionArgs = @{
            Execute          = $Launcher
            Argument         = "`"$Target`""
            WorkingDirectory = $WorkingDirectory
        }
    } else {
        $actionArgs = @{
            Execute          = $Target
            WorkingDirectory = $WorkingDirectory
        }
        if (-not [string]::IsNullOrWhiteSpace($Arguments)) { $actionArgs.Argument = $Arguments }
    }
    $action = New-ScheduledTaskAction @actionArgs
    $settingsArgs = @{
        AllowStartIfOnBatteries     = $true
        DontStopIfGoingOnBatteries  = $true
        StartWhenAvailable          = $true
        # Task Scheduler 这组设置的默认值是 StopOnIdleEnd=true：任务照常启动，但机器
        # 一旦"不再空闲"（你一碰键鼠）就被终止。它跟 RunOnlyIfIdle 是两回事，后者为
        # false 也不管用 —— 实测服务登录后活不过几十秒，退出码 0xC000013A。
        DontStopOnIdleEnd           = $true
        # 服务任务必须无超时，否则默认 72 小时后被强杀
        ExecutionTimeLimit          = [TimeSpan]::Zero
    }
    if ($RestartCount -gt 0) {
        $settingsArgs.RestartCount = $RestartCount
        $settingsArgs.RestartInterval = (New-TimeSpan -Minutes 1)
    }
    $settings = New-ScheduledTaskSettingsSet @settingsArgs
    # Interactive：任务跑在当前登录会话里 —— 需要用户的 ~/.lifelab、Ollama、Docker Desktop
    $principal = New-ScheduledTaskPrincipal -UserId $UserId -LogonType Interactive -RunLevel Limited

    try {
        Register-ScheduledTask -TaskName $Name -Action $action -Trigger $Trigger `
            -Settings $settings -Principal $principal -Description $Description -Force | Out-Null
    } catch {
        Write-Error "安装 $Name 失败：$($_.Exception.Message)`n若为「拒绝访问」，请用管理员 PowerShell 重跑本脚本。"
        return
    }
    Write-Host "已安装：$Name"
}

# 经 serve_silent.vbs 启动，而不是直接跑 serve.cmd：交互式登录下直接跑 .cmd 会弹出
# 一个控制台窗口，而 serve.cmd 把输出全重定向进日志 —— 于是那个窗口看着是「空的」。
# 关掉它 = 给 uvicorn 发 Ctrl+C（退出码 0xC000013A），服务每次登录后活不过几分钟。
# wscript + WScript.Shell.Run(..., 0, True) 让窗口完全隐藏（等待子进程，保住 RestartCount）。
New-LifeLabTask `
    -Name "LifeLab Server" `
    -Target (Join-Path $ScriptDir "serve_silent.vbs") `
    -Launcher (Join-Path $env:SystemRoot "System32\wscript.exe") `
    -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $UserId) `
    -Description "LifeLab 后端服务（uvicorn :8000，无 --reload）。登录时静默启动；异常退出自动重启。" `
    -RestartCount 3

New-LifeLabTask `
    -Name "LifeLab Backup" `
    -Target (Join-Path $ScriptDir "backup_db.cmd") `
    -Trigger (New-ScheduledTaskTrigger -Daily -At "03:03") `
    -Description "LifeLab 数据库每日备份（pg_dump -Fc → ~/.lifelab/backups，保留 14 份）。"

# 直接调 python 而非 reset_demo.cmd：那个 .cmd 末尾有 pause，在无控制台的
# 计划任务里会永久挂住。必须显式给 -WorkingDirectory $Backend：默认的工作目录会是
# python.exe 所在的 .venv\Scripts —— 那已经不再是问题（config.py 现在锚定
# backend/.env），但脚本按 backend 目录运行才是它文档里写明的用法。
New-LifeLabTask `
    -Name "LifeLab Demo Reset" `
    -Target $Python `
    -Arguments "-u `"$ScriptDir\reset_demo.py`"" `
    -WorkingDirectory $Backend `
    -Trigger (New-ScheduledTaskTrigger -Daily -At "03:17") `
    -Description "LifeLab 每日重置 demo 演示账号（保留 AI 产物：日报/period_reviews/memory_items）。"

# 排在 Backup（03:03）和 Demo Reset（03:17）之后：复盘要读实验与记忆数据，等库重置完
# 再跑最省事；刻意的非整点，避免和别的任务抢 IO。同样直接调 python 而不是包 .cmd
# （理由见上面 Demo Reset 那段）。脚本自身幂等，且 --date/--dry-run 可手工验证。
New-LifeLabTask `
    -Name "LifeLab Auto Review" `
    -Target $Python `
    -Arguments "-u `"$ScriptDir\auto_review.py`"" `
    -WorkingDirectory $Backend `
    -Trigger (New-ScheduledTaskTrigger -Daily -At "04:07") `
    -Description "LifeLab 每日自动复盘（昨天日复盘；周一追加上周周复盘；每月 1 号追加上月月复盘）。"

Write-Host "`n查看：Get-ScheduledTask -TaskName 'LifeLab*' | Format-Table TaskName,State"
Write-Host "立即起服务：Start-ScheduledTask -TaskName 'LifeLab Server'"
Write-Host "立即跑复盘：Start-ScheduledTask -TaskName 'LifeLab Auto Review'（日志 ~/.lifelab/auto_review.log）"
