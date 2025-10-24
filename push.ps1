Param(
    [string]$Message = "Update: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
    [string]$Remote = "origin",
    [string]$Branch = "",
    [switch]$SetUpstream
)

function Die($msg) {
    Write-Error $msg
    exit 1
}

# Ensure git is available
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Die "git is not installed or not on PATH."
}

# Ensure we're inside a git repo
$inside = (& git rev-parse --is-inside-work-tree 2>$null)
if ($LASTEXITCODE -ne 0 -or $inside -ne "true") {
    Die "This folder is not a git repository. Run 'git init' first."
}

# Determine current branch if not provided
if (-not $Branch) {
    $Branch = (& git rev-parse --abbrev-ref HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $Branch) {
        Die "Unable to determine current branch. Create/switch a branch first: git checkout -b main"
    }
}

# Show current status
Write-Host "Repo: $(Get-Location)"
Write-Host "Branch: $Branch"
$remotes = (& git remote -v) | Out-String
Write-Host "Remotes:`n$remotes"

# Stage all changes
& git add -A
if ($LASTEXITCODE -ne 0) { Die "git add failed." }

# Check if there is anything to commit
$diff = (& git diff --cached --name-only)
if ($diff) {
    Write-Host "Staged changes:`n$diff"
    & git commit -m $Message
    if ($LASTEXITCODE -ne 0) { Die "git commit failed." }
} else {
    Write-Host "No staged changes to commit. (Nothing new to commit.)"
}

# Determine if remote branch has an upstream
$hasUpstream = $false
$upstream = (& git rev-parse --abbrev-ref --symbolic-full-name "@{u}" 2>$null)
if ($LASTEXITCODE -eq 0 -and $upstream) {
    $hasUpstream = $true
}

# Push
if ($hasUpstream -and -not $SetUpstream) {
    Write-Host "Pushing to existing upstream: $upstream"
    & git push
    if ($LASTEXITCODE -ne 0) { Die "git push failed." }
} else {
    Write-Host "Pushing with upstream to $Remote $Branch"
    & git push -u $Remote $Branch
    if ($LASTEXITCODE -ne 0) { Die "git push -u failed." }
}

Write-Host "✅ Push complete."

# Optional: show last commit
& git --no-pager log -1 --pretty=format:"%C(yellow)%h%Creset %s %Cgreen(%cr) %C(cyan)%an%Creset" | Write-Host
