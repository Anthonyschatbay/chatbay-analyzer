# push.ps1
Set-Location -Path "$PSScriptRoot"

git add openapi.json app.py templates/ Procfile requirements.txt runtime.txt .gitignore
if (-not (git diff --cached --quiet)) {
  git commit -m "Update OpenAPI 5.3.2 + Actions; CSV endpoints verified"
} else {
  Write-Host "No staged changes." -ForegroundColor Yellow
}

git push
