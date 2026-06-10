# Orchestration systeme (roadmap Step 15) — enregistre le collecteur et le pipeline EOD
# dans le PLANIFICATEUR DE TACHES WINDOWS (equivalent cron/systemd sur ce poste).
#
#   .\scripts\schedule_collector.ps1            -> cree/maj les 2 taches
#   .\scripts\schedule_collector.ps1 -Remove    -> les supprime
#
# Taches creees :
#   VolInfra-Collector : jours ouvres 09:05 (heure locale Paris) — collecte en seance EUREX
#   VolInfra-EOD       : jours ouvres 17:45 — pipeline EOD (snapshots -> analytics -> QC)
#
# PREREQUIS (documente, voir docs/runbooks.md) : le gateway IBKR Client Portal doit etre
# lance ET authentifie (login navigateur https://localhost:5000). Sans session, le
# collecteur s'arrete proprement et l'echec est visible dans collector_status.json +
# la tache renvoie un code non nul (detection de panne par le scheduler).
param([switch]$Remove)

$root   = Split-Path -Parent $PSScriptRoot
$python = (Get-Command python).Source

if ($Remove) {
    foreach ($n in "VolInfra-Collector", "VolInfra-EOD") {
        try { Unregister-ScheduledTask -TaskName $n -Confirm:$false -ErrorAction Stop
              Write-Host "Supprimee : $n" } catch { Write-Host "Absente : $n" }
    }
    exit 0
}

$days = "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"

# 1) Collecteur — tourne en continu pendant la seance (le trigger le lance a 09:05).
$a1 = New-ScheduledTaskAction -Execute $python -Argument "run_collector.py" -WorkingDirectory $root
$t1 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At 09:05
Register-ScheduledTask -TaskName "VolInfra-Collector" -Action $a1 -Trigger $t1 `
    -Description "Collecteur vol EURO STOXX 50 (gateway IBKR requis)" -Force | Out-Null
Write-Host "Enregistree : VolInfra-Collector (lun-ven 09:05)"

# 2) Pipeline EOD — apres la cloture EUREX actions (~17:30).
$eod = "-c `"from src.orchestration.jobs import run_eod_pipeline; from datetime import date; run_eod_pipeline(date.today())`""
$a2 = New-ScheduledTaskAction -Execute $python -Argument $eod -WorkingDirectory $root
$t2 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At 17:45
Register-ScheduledTask -TaskName "VolInfra-EOD" -Action $a2 -Trigger $t2 `
    -Description "Pipeline EOD vol infra (snapshots -> analytics -> QC)" -Force | Out-Null
Write-Host "Enregistree : VolInfra-EOD (lun-ven 17:45)"
