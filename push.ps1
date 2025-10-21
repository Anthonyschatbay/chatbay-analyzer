# Generic Git push helper (no dates). Use: ./push.ps1 "your commit message"
param(
  [string]$Message = "update"
)

# Ensure we're in a git repo
git rev-parse --is-inside-work-tree 2>$null 1>$null
if ($LASTEXITCODE -ne 0) {
  Write-Error "Not a git repository. cd into your repo first."
  exit 1
}

# Current branch
$branch = git rev-parse --abbrev-ref HEAD
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($branch)) {
  Write-Error "Unable to determine current branch."
  exit 1
}

# Stage everything (new, modified, deleted)
git add -A

# If nothing changed, bail gracefully
$changes = git status --porcelain
if ([string]::IsNullOrWhiteSpace($changes)) {
  Write-Host "Nothing to commit. Branch '$branch' is up to date."
  exit 0
}

# Commit and push
git commit -m "$Message"
if ($LASTEXITCODE -ne 0) {
  Write-Error "Commit failed."
  exit 1
}

# Ensure 'origin' exists
git remote get-url origin 1>$null 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Error "No 'origin' remote found. Add one: git remote add origin <URL>"
  exit 1
}

git push origin "$branch"
if ($LASTEXITCODE -eq 0) {
  Write-Host "Pushed to origin/$branch ✔"
} else {
  Write-Error "Push failed."
  exit 1
}
