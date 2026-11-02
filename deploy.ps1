# ============================================================================
# One-shot GCP setup + deploy for the incident app (Windows PowerShell 5+).
# Run from the incident_app folder:
#   powershell -ExecutionPolicy Bypass -File deploy.ps1
# Requires: gcloud CLI installed and authenticated (gcloud auth login).
#
# Note: gcloud prints progress to stderr, which PowerShell can mistake for
# errors. This script never uses ErrorActionPreference=Stop; it checks exit
# codes explicitly and uses cmd /c for silent probes.
# ============================================================================
$ErrorActionPreference = "Continue"
$PROJECT = "capital-processor"
$REGION  = "us-central1"
$SERVICE = "incidents"
$BUCKET  = "incidents-capital-processor"
$FS_COLLECTION = "incident_reports"

function Fail($msg) { Write-Host $msg -ForegroundColor Red; exit 1 }

Write-Host "== Project: $PROJECT / Region: $REGION / Service: $SERVICE" -ForegroundColor Cyan

# --- 1) GCS bucket (create if missing) --------------------------------------
cmd /c "gcloud storage buckets describe gs://$BUCKET --project $PROJECT >nul 2>&1"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Bucket not found - creating gs://$BUCKET ..." -ForegroundColor Yellow
    cmd /c "gcloud storage buckets create gs://$BUCKET --project $PROJECT --location $REGION --uniform-bucket-level-access"
    if ($LASTEXITCODE -ne 0) { Fail "Bucket creation failed - see message above." }
    Write-Host "Bucket created." -ForegroundColor Green
} else { Write-Host "Bucket gs://$BUCKET exists." -ForegroundColor Green }

# --- 2) Firestore (create default DB if missing) -----------------------------
cmd /c "gcloud firestore databases describe --project $PROJECT >nul 2>&1"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Firestore not found - creating (native mode) ..." -ForegroundColor Yellow
    cmd /c "gcloud firestore databases create --project $PROJECT --location $REGION --type firestore-native"
    if ($LASTEXITCODE -ne 0) { Fail "Firestore creation failed - see message above." }
    Write-Host "Firestore created." -ForegroundColor Green
} else { Write-Host "Firestore database exists." -ForegroundColor Green }

# --- 3) Deploy from source ----------------------------------------------------
# max-instances 4 (was 1): storage is durable now, so instances are disposable.
# With one instance a single slow LibreOffice conversion or a crafted upload
# takes the whole service down; with headroom other reporters keep working.
# Note the login throttle counts per instance, so a lockout is now per instance
# rather than global - see F12 in the security review.
Write-Host "Deploying $SERVICE (first build takes 5-10 minutes) ..." -ForegroundColor Yellow
cmd /c "gcloud run deploy $SERVICE --source . --project $PROJECT --region $REGION --allow-unauthenticated --max-instances 4 --memory 1Gi --timeout 300"
if ($LASTEXITCODE -ne 0) { Fail "Deploy failed - see the build output above." }

$URL = (cmd /c "gcloud run services describe $SERVICE --project $PROJECT --region $REGION --format ""value(status.url)"" 2>nul" | Select-Object -Last 1).Trim()
if (-not $URL) { Fail "Could not read the service URL." }
Write-Host "Service URL: $URL" -ForegroundColor Green

# --- 4) Env vars: read local .env, override cloud-specific values -------------
if (-not (Test-Path ".env")) { Fail ".env not found - run this from the incident_app folder." }
$envmap = [ordered]@{}
foreach ($line in Get-Content ".env") {
    if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
        $envmap[$Matches[1]] = $Matches[2].Trim()
    }
}
foreach ($k in @("LOCAL_STORAGE","SQLITE_PATH")) { [void]$envmap.Remove($k) }
$envmap["BASE_URL"] = $URL
$envmap["GCS_BUCKET"] = $BUCKET
$envmap["FIRESTORE_COLLECTION"] = $FS_COLLECTION

$lines = @()
foreach ($e in $envmap.GetEnumerator()) {
    if ($e.Value -ne "") {
        $v = $e.Value -replace '"', '\"'
        $lines += ('{0}: "{1}"' -f $e.Key, $v)
    }
}
$yamlPath = Join-Path $env:TEMP "incidents_env.yaml"
Set-Content -Path $yamlPath -Value ($lines -join "`n") -Encoding UTF8

Write-Host "Applying env vars from .env (plus cloud overrides) ..." -ForegroundColor Yellow
cmd /c "gcloud run services update $SERVICE --project $PROJECT --region $REGION --env-vars-file ""$yamlPath"""
$envOk = ($LASTEXITCODE -eq 0)
Remove-Item $yamlPath -Force
if (-not $envOk) { Fail "Setting env vars failed - see message above." }

# --- 5) Health check -----------------------------------------------------------
Start-Sleep -Seconds 5
try {
    $null = Invoke-RestMethod "$URL/healthz" -TimeoutSec 30
    Write-Host "Health check: OK" -ForegroundColor Green
} catch { Write-Host "Health check failed - check Cloud Run logs." -ForegroundColor Red }

Write-Host ""
Write-Host "================ DONE ================" -ForegroundColor Cyan
Write-Host "Submit page (share with property staff):  $URL"
Write-Host "Reviewer dashboard:                       $URL/admin?token=<your ADMIN_TOKEN>"
