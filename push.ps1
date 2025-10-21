Param(
  [string]$Msg = "chore: push $(Get-Date -AsUTC -Format 'yyyy-MM-ddTHH:mm:ssZ')"
)

if (-not (Test-Path .git)) { git init; git branch -M main }

git add -A
git commit -m $Msg
git push -u origin main
