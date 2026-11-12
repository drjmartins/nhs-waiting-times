<#
Scheduled refresh for the NHS waiting-times dashboards (run by Windows Task Scheduler).

Why this exists: NHS England's website blocks GitHub Actions' servers (since 2026-09-16),
but not ordinary connections. So this machine does the NHS fetch, and GitHub only
publishes what is committed (the workflow's skip_fetch path, which never contacts NHS).

What it does, in order:
  1. git pull (fast-forward only) so we build on top of the latest repo state
  2. run the tests
  3. python -m pipeline.run       (cancer: fetch new/revised NHS files, all gates)
  4. python -m pipeline_rtt.run   (RTT: fetch new/revised months, both gates)
  5. if any data changed (ignoring meta.json's build timestamp): commit, push, and
     start the publish workflow; otherwise discard the timestamp-only changes and exit
On any failure it stops, leaves the repo untouched (no commit/push), writes the log, and
(optionally) opens a GitHub issue so the failure is emailed to repo watchers.

Logs: logs\refresh-YYYY-MM-DD.log (gitignored).
Manual run:  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\scheduled_refresh.ps1
#>
param(
    [switch]$NoPublish,          # commit + push but don't start the publish workflow
    [switch]$NoIssueOnFailure    # don't open a GitHub issue when a run fails
)

$ErrorActionPreference = "Continue"   # native tools (git) write progress to stderr; judge by exit codes
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

# Python prints non-ASCII (e.g. "Δ"); force UTF-8 so redirected output can't crash it.
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

# Prefer the project's virtualenv if there is one, else whatever "python" is on PATH.
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { $Python = "python" }

$LogDir = Join-Path $RepoRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log = Join-Path $LogDir ("refresh-{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))

function Write-Log([string]$msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $msg
    Add-Content -Path $Log -Value $line -Encoding UTF8
}

function Invoke-Step([string]$label, [string]$exe, [string[]]$argv) {
    Write-Log ">> $label"
    & $exe @argv 2>&1 | ForEach-Object { "$_" } | Add-Content -Path $Log -Encoding UTF8
    if ($LASTEXITCODE -ne 0) { throw "$label failed (exit code $LASTEXITCODE)" }
}

$DataPaths = @("data/manifest.json", "data/processed", "site/cancer/data",
               "data_rtt/manifest.json", "site/rtt/data", "ods_classification.json")
$MetaFiles = @("site/cancer/data/meta.json", "site/rtt/data/meta.json")

Write-Log "==== Scheduled refresh started (python: $Python) ===="
Write-Host "Refreshing (takes ~4-5 minutes, no output here). Log: $Log"
$touched = $false   # becomes true once the pipelines start rewriting files
try {
    # A run that was interrupted (Ctrl+C, shutdown) can leave just the rebuilt meta.json
    # timestamps behind. Those carry no data, so restore them and carry on.
    $metaDirty = git status --porcelain -- $MetaFiles
    if ($metaDirty) {
        Write-Log "Restoring leftover meta.json timestamp changes from an interrupted run."
        git checkout -- $MetaFiles
    }
    # Anything else uncommitted in the data paths is real work: refuse rather than commit it blindly.
    $dirty = git status --porcelain -- $DataPaths
    if ($dirty) { throw "Uncommitted changes in data paths already present; resolve them first:`n$dirty" }

    Invoke-Step "git pull" "git" @("pull", "--ff-only")
    Invoke-Step "tests"    $Python @("-m", "pytest", "-q")
    $touched = $true
    Invoke-Step "cancer pipeline" $Python @("-m", "pipeline.run")
    Invoke-Step "RTT pipeline"    $Python @("-m", "pipeline_rtt.run")

    git add -- $DataPaths
    # Same rule as the workflow: a change to meta.json's build timestamp alone is not new data.
    $exclude = $MetaFiles | ForEach-Object { ":(exclude)$_" }
    git diff --cached --quiet -- . @exclude
    if ($LASTEXITCODE -eq 0) {
        Write-Log "No new data (only build timestamps changed). Discarding them."
        git reset -q -- $DataPaths
        git checkout -- $MetaFiles
    } else {
        $latest = (Get-Content "site/cancer/data/meta.json" -Raw | ConvertFrom-Json).months[-1]
        Invoke-Step "git commit" "git" @("commit", "-m", "Scheduled data refresh (cancer data to $latest)")
    }

    # Push + publish whenever we're ahead of GitHub: covers today's commit AND a commit from
    # an earlier run whose push failed (e.g. no network at that moment).
    $ahead = [int](git rev-list --count "@{u}..HEAD")
    if ($ahead -eq 0) {
        Write-Log "==== Done: nothing new to publish ===="
        exit 0
    }
    Invoke-Step "git push" "git" @("push")
    if ($NoPublish) {
        Write-Log "Pushed; -NoPublish set, so not starting the publish workflow."
    } else {
        Invoke-Step "start publish workflow" "gh" @("workflow", "run", "update-data.yml",
                                                    "-f", "skip_fetch=true")
        Write-Log "Publish workflow started; it rebuilds from the committed data and deploys (~2 min)."
    }
    $latest = (Get-Content "site/cancer/data/meta.json" -Raw | ConvertFrom-Json).months[-1]
    Write-Log "==== Done: published (cancer data to $latest) ===="
    exit 0
}
catch {
    Write-Log "FAILED: $($_.Exception.Message)"
    if ($touched) {
        # Leave the repo as it was: unstage and discard the partial rebuild (nothing was pushed).
        git reset -q -- $DataPaths 2>$null
        git checkout -- $DataPaths 2>$null
        git clean -fdq -- $DataPaths 2>$null
    }

    if (-not $NoIssueOnFailure -and (Get-Command gh -ErrorAction SilentlyContinue)) {
        # One open issue at a time: don't open a new one every day while a failure persists.
        $open = gh issue list --state open --search "Scheduled refresh failed in:title" --json number --jq "length" 2>$null
        if ($open -eq "0") {
            $tail = (Get-Content $Log -Tail 40) -join "`n"
            $body = "The scheduled refresh on $env:COMPUTERNAME failed on $(Get-Date -Format 'yyyy-MM-dd HH:mm').`n`n" +
                    "Error: $($_.Exception.Message)`n`nLast lines of the log:`n``````n$tail`n``````"
            gh issue create --title "Scheduled refresh failed ($(Get-Date -Format 'yyyy-MM-dd'))" --body $body 2>&1 |
                ForEach-Object { "$_" } | Add-Content -Path $Log -Encoding UTF8
        }
    }
    exit 1
}
