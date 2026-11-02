<#
  Local, read-only start of the Smart Move evidence-quality and news-review pages on the SAVED real report.
  No provider request, no scheduler, no write except a reviewer label appended to <report folder>\news_review\labels.jsonl.
    .\start_quality_review.ps1 -Report C:\Users\initi\SmartMoveRun\phase13_quality_report_real
  Then, in another window:  cd frontend ; npx vite --port 5191     and open  http://127.0.0.1:5191/smart-move/quality  (news review: /smart-move/news-review)
  To rebuild the report offline first:  python backend\smart_move_real_quality_report.py --out <NEW folder>
#>
param([Parameter(Mandatory = $true)][string]$Report)
if (-not (Test-Path (Join-Path $Report "quality_report.json"))) { throw "No quality_report.json in $Report" }
$env:SMART_MOVE_QUALITY_REPORT = (Join-Path $Report "quality_report.json")
$env:SMART_MOVE_NEWS_REVIEW_DIR = (Join-Path $Report "news_review")
Set-Location (Join-Path $PSScriptRoot "..\..\..\backend")
python -m uvicorn smart_move_local_server:app --host 127.0.0.1 --port 8000
