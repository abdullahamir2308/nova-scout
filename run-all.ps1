#Requires -Version 5.1
<#
.SYNOPSIS
    Runs prompts/NN-name.md tasks in order, each as its own fresh
    `claude -p --dangerously-skip-permissions` session.

.USAGE
    powershell -ExecutionPolicy Bypass -File .\run-all.ps1
    powershell -ExecutionPolicy Bypass -File .\run-all.ps1 -SkipPreflight
    powershell -ExecutionPolicy Bypass -File .\run-all.ps1 -TaskTimeoutMinutes 60

See PROMPT_QUEUE_README.md for the full layout and task-file format.
#>
[CmdletBinding()]
param(
    [switch]$SkipPreflight,
    [int]$TaskTimeoutMinutes = 120
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

$root        = $PSScriptRoot
$promptsDir  = Join-Path $root 'prompts'
$progressDir = Join-Path $root 'progress'
$logsDir     = Join-Path $root 'logs'
$preamblePath = Join-Path $promptsDir '_preamble.md'

foreach ($dir in @($promptsDir, $progressDir, $logsDir)) {
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
}

# Force UTF-8 everywhere so non-ASCII text (accented names, etc.) survives
# the trip through stdin/stdout and the log files.
try { & chcp 65001 > $null } catch {}
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
try { [Console]::InputEncoding  = [System.Text.Encoding]::UTF8 } catch {}
try { $OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

# ---------------------------------------------------------------------------
# Keep the host awake for the duration of the run, restore on exit.
# ---------------------------------------------------------------------------

Add-Type -Namespace NovaScoutRunner -Name Power -MemberDefinition @'
[DllImport("kernel32.dll", SetLastError = true)]
public static extern uint SetThreadExecutionState(uint esFlags);
'@ -ErrorAction SilentlyContinue

# Decimal, not hex: PowerShell parses 0x80000000 as Int32 by bit pattern
# (-2147483648), and casting a negative value to uint32 then throws.
$ES_CONTINUOUS       = [uint32]2147483648
$ES_SYSTEM_REQUIRED  = [uint32]1

function Set-KeepAwake {
    [NovaScoutRunner.Power]::SetThreadExecutionState($ES_CONTINUOUS -bor $ES_SYSTEM_REQUIRED) | Out-Null
}
function Clear-KeepAwake {
    [NovaScoutRunner.Power]::SetThreadExecutionState($ES_CONTINUOUS) | Out-Null
}

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

function Test-DockerRunning {
    $dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $dockerCmd) {
        Write-Host "  Docker:  CLI not found on PATH." -ForegroundColor Red
        return $false
    }
    & docker info *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  Docker:  not running (docker info failed). Start Docker Desktop." -ForegroundColor Red
        return $false
    }
    Write-Host "  Docker:  running." -ForegroundColor Green
    return $true
}

function Test-OllamaRunning {
    try {
        $null = Invoke-RestMethod -Uri 'http://localhost:11434/api/version' -TimeoutSec 3 -ErrorAction Stop
        Write-Host "  Ollama:  running." -ForegroundColor Green
        return $true
    } catch {
        Write-Host "  Ollama:  not responding on localhost:11434. Start the Ollama app." -ForegroundColor Red
        return $false
    }
}

function Invoke-Preflight {
    Write-Host "Preflight checks:"
    $dockerOk = Test-DockerRunning
    $ollamaOk = Test-OllamaRunning
    if (-not ($dockerOk -and $ollamaOk)) {
        Write-Host "Preflight failed. Fix the above, or re-run with -SkipPreflight." -ForegroundColor Red
        exit 1
    }
}

# ---------------------------------------------------------------------------
# Task discovery / parsing
# ---------------------------------------------------------------------------

function Get-Tasks {
    Get-ChildItem -Path $promptsDir -Filter '*.md' -File |
        Where-Object { $_.Name -match '^\d{2}-.+\.md$' } |
        Sort-Object Name
}

function Get-TaskPromptBody {
    param([Parameter(Mandatory)][string]$Path)

    $raw = Get-Content -Path $Path -Raw -Encoding UTF8
    if ($null -eq $raw) { $raw = '' }

    $nlIndex = $raw.IndexOf("`n")
    if ($nlIndex -lt 0) {
        $firstLine = $raw
        $rest = ''
    } else {
        $firstLine = $raw.Substring(0, $nlIndex).TrimEnd("`r")
        $rest = $raw.Substring($nlIndex + 1)
    }

    $model = 'sonnet'
    $body = $raw
    if ($firstLine -match '^\s*model\s*:\s*(opus|sonnet)\s*$') {
        $model = $Matches[1].ToLowerInvariant()
        $body = $rest -replace '^(\r?\n)', ''
    }

    [PSCustomObject]@{ Model = $model; Body = $body }
}

function Build-FullPrompt {
    param(
        [Parameter(Mandatory)][string]$TaskName,
        [Parameter(Mandatory)][AllowEmptyString()][string]$Preamble,
        [Parameter(Mandatory)][string]$Body
    )

    $sections = @()
    if ($Preamble.Trim().Length -gt 0) {
        $sections += $Preamble.Trim()
    }
    $sections += "Before doing anything else, read every file in the progress/ directory (if any exist) to learn what earlier tasks in this queue did and what you need to know before starting this one."
    $sections += $Body.Trim()
    $sections += @"
When you are finished, write your report to progress/$TaskName.md using exactly this structure:

STATUS: DONE
(or STATUS: BLOCKED, or STATUS: NEEDS-HUMAN -- as the first line, nothing else on that line)

## What was done

## Files changed

## Problems

## Human actions needed

## Next task should know

Keep the whole report under 300 words total. Never include secrets or personal email addresses in it.
"@

    return ($sections -join "`n`n---`n`n")
}

# ---------------------------------------------------------------------------
# Running a task
# ---------------------------------------------------------------------------

function Invoke-ClaudeTask {
    param(
        [Parameter(Mandatory)][string]$Prompt,
        [Parameter(Mandatory)][string]$Model,
        [Parameter(Mandatory)][string]$LogPath,
        [Parameter(Mandatory)][int]$TimeoutMinutes
    )

    # Driven via the raw Process class, not the Start-Process cmdlet: on this
    # PowerShell/.NET combo, Start-Process -PassThru's returned object never
    # populates .ExitCode (confirmed empirically), even after .Refresh().
    $claudeExe = (Get-Command claude).Source

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $claudeExe
    $psi.Arguments = "-p --dangerously-skip-permissions --model $Model"
    $psi.WorkingDirectory = $root
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    # StandardInputEncoding doesn't exist on this .NET Framework (confirmed
    # empirically) -- bytes are written straight to StandardInput.BaseStream
    # below instead, which sidesteps the StreamWriter's own encoding.
    $psi.StandardOutputEncoding = $Utf8NoBom
    $psi.StandardErrorEncoding = $Utf8NoBom

    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    $proc.Start() | Out-Null

    $stdoutTask = $proc.StandardOutput.ReadToEndAsync()
    $stderrTask = $proc.StandardError.ReadToEndAsync()

    $promptBytes = $Utf8NoBom.GetBytes($Prompt)
    $proc.StandardInput.BaseStream.Write($promptBytes, 0, $promptBytes.Length)
    $proc.StandardInput.BaseStream.Flush()
    $proc.StandardInput.Close()

    $finished = $proc.WaitForExit([int]($TimeoutMinutes * 60 * 1000))

    if (-not $finished) {
        & taskkill /PID $proc.Id /T /F *> $null
        try { $proc.WaitForExit(5000) } catch {}
        $stdoutSoFar = ''
        $stderrSoFar = ''
        try { if ($stdoutTask.Wait(2000)) { $stdoutSoFar = $stdoutTask.Result } } catch {}
        try { if ($stderrTask.Wait(2000)) { $stderrSoFar = $stderrTask.Result } } catch {}
        $content = $stdoutSoFar + "`n[run-all.ps1] TIMEOUT after $TimeoutMinutes minute(s) -- process tree killed.`n"
        if ($stderrSoFar) { $content += "--- stderr ---`n$stderrSoFar" }
        [System.IO.File]::WriteAllText($LogPath, $content, $Utf8NoBom)
        return 124
    }

    $stdout = $stdoutTask.Result
    $stderr = $stderrTask.Result
    $content = $stdout
    if ($stderr) { $content += "`n--- stderr ---`n$stderr" }
    [System.IO.File]::WriteAllText($LogPath, $content, $Utf8NoBom)

    return $proc.ExitCode
}

# ---------------------------------------------------------------------------
# Report validation / display
# ---------------------------------------------------------------------------

function Get-ReportStatus {
    param([Parameter(Mandatory)][string]$ReportPath)

    if (-not (Test-Path $ReportPath)) {
        return [PSCustomObject]@{ Exists = $false; Status = $null }
    }
    $firstLine = (Get-Content -Path $ReportPath -TotalCount 1 -Encoding UTF8)
    if ($firstLine -match '^STATUS:\s*(DONE|BLOCKED|NEEDS-HUMAN)\s*$') {
        return [PSCustomObject]@{ Exists = $true; Status = $Matches[1] }
    }
    return [PSCustomObject]@{ Exists = $true; Status = $null }
}

function Show-ReportSection {
    param(
        [Parameter(Mandatory)][string]$ReportPath,
        [Parameter(Mandatory)][string]$Header
    )
    if (-not (Test-Path $ReportPath)) { return '(report file not found)' }
    $raw = Get-Content -Path $ReportPath -Raw -Encoding UTF8
    # Built by concatenation, not interpolation: "$(.*?)" inside a
    # double-quoted string is parsed as a PowerShell subexpression
    # containing the command ".*?", not a literal regex capture group.
    $pattern = '(?ms)^##\s*' + [regex]::Escape($Header) + '\s*(.*?)(?=^##\s|\z)'
    if ($raw -match $pattern) {
        $text = $Matches[1].Trim()
        if ($text.Length -eq 0) { return '(section is empty)' }
        return $text
    }
    return '(no "## ' + $Header + '" section found)'
}

function Write-StopBanner {
    param(
        [Parameter(Mandatory)][string]$TaskName,
        [Parameter(Mandatory)][string]$Reason,
        [string]$ReportPath
    )
    Write-Host ""
    Write-Host ('=' * 70) -ForegroundColor Red
    Write-Host "STOPPED at task: $TaskName" -ForegroundColor Red
    Write-Host "Reason: $Reason" -ForegroundColor Red
    if ($ReportPath -and (Test-Path $ReportPath)) {
        Write-Host '-- Human actions needed (from the report) ------------------------' -ForegroundColor Yellow
        Write-Host (Show-ReportSection -ReportPath $ReportPath -Header 'Human actions needed')
    }
    Write-Host ('=' * 70) -ForegroundColor Red
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

$exitCode = 0
Set-KeepAwake
try {
    if (-not $SkipPreflight) {
        Invoke-Preflight
    } else {
        Write-Host "Preflight checks skipped (-SkipPreflight)."
    }

    $preamble = ''
    if (Test-Path $preamblePath) {
        $preamble = Get-Content -Path $preamblePath -Raw -Encoding UTF8
    }

    $tasks = Get-Tasks
    if (-not $tasks -or $tasks.Count -eq 0) {
        Write-Host "No tasks found in prompts/ (files must match NN-name.md)."
        return
    }

    $results = @()

    foreach ($task in $tasks) {
        $taskName = [System.IO.Path]::GetFileNameWithoutExtension($task.Name)
        $reportPath = Join-Path $progressDir "$taskName.md"

        $existing = Get-ReportStatus -ReportPath $reportPath
        if ($existing.Exists -and $existing.Status -eq 'DONE') {
            Write-Host "[$taskName] already STATUS: DONE -- skipping." -ForegroundColor DarkGray
            $results += [PSCustomObject]@{ Task = $taskName; Model = '(skipped)'; Start = ''; Finish = ''; Result = 'SKIPPED (already DONE)' }
            continue
        }

        $parsed = Get-TaskPromptBody -Path $task.FullName
        $model = $parsed.Model
        $fullPrompt = Build-FullPrompt -TaskName $taskName -Preamble $preamble -Body $parsed.Body

        $startTime = Get-Date
        Write-Host ""
        Write-Host "=== [$taskName] model=$model start=$($startTime.ToString('yyyy-MM-dd HH:mm:ss')) ===" -ForegroundColor Cyan

        $timestamp = $startTime.ToString('yyyyMMdd-HHmmss')
        $logPath = Join-Path $logsDir "$timestamp-$taskName.log"

        $procExit = Invoke-ClaudeTask -Prompt $fullPrompt -Model $model -LogPath $logPath -TimeoutMinutes $TaskTimeoutMinutes

        $finishTime = Get-Date
        Write-Host "=== [$taskName] finish=$($finishTime.ToString('yyyy-MM-dd HH:mm:ss')) exitCode=$procExit log=$logPath ===" -ForegroundColor Cyan

        $resultRow = [PSCustomObject]@{
            Task = $taskName; Model = $model
            Start = $startTime.ToString('HH:mm:ss'); Finish = $finishTime.ToString('HH:mm:ss')
            Result = ''
        }

        if ($procExit -ne 0) {
            $resultRow.Result = "FAILED (exit $procExit)"
            $results += $resultRow
            Write-StopBanner -TaskName $taskName -Reason "claude exited with code $procExit. See log: $logPath" -ReportPath $reportPath
            $exitCode = 1
            break
        }

        $report = Get-ReportStatus -ReportPath $reportPath
        if (-not $report.Exists) {
            $resultRow.Result = 'FAILED (no report)'
            $results += $resultRow
            Write-StopBanner -TaskName $taskName -Reason "progress/$taskName.md was not created."
            $exitCode = 1
            break
        }
        if (-not $report.Status) {
            $resultRow.Result = 'FAILED (no STATUS line)'
            $results += $resultRow
            Write-StopBanner -TaskName $taskName -Reason "progress/$taskName.md has no valid STATUS line." -ReportPath $reportPath
            $exitCode = 1
            break
        }
        if ($report.Status -ne 'DONE') {
            $resultRow.Result = "STATUS: $($report.Status)"
            $results += $resultRow
            Write-StopBanner -TaskName $taskName -Reason "task reported STATUS: $($report.Status)." -ReportPath $reportPath
            $exitCode = 1
            break
        }

        $resultRow.Result = 'DONE'
        $results += $resultRow
        Write-Host "[$taskName] DONE." -ForegroundColor Green
    }

    Write-Host ""
    Write-Host "Summary:" -ForegroundColor Cyan
    $results | Format-Table -AutoSize | Out-String | Write-Host

} catch {
    Write-Host "run-all.ps1 failed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host $_.ScriptStackTrace
    $exitCode = 1
} finally {
    Clear-KeepAwake
}

exit $exitCode
