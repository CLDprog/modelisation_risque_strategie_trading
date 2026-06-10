"""
Routage d'alertes externe (roadmap Step 15) — webhook (Slack-compatible) et/ou email SMTP.

Configuration : section `alerting:` de configs/qc.yaml. Tant que ni webhook_url ni SMTP ne
sont renseignés, le routage est INACTIF (les alertes restent écrites dans data/alerts.json,
qui demeure la source locale de vérité). Le mot de passe SMTP vient de l'environnement
(VOL_SMTP_PASSWORD), jamais de la config — règle « secrets hors dépôt » (Step 1).

Échecs d'envoi : journalisés, jamais bloquants pour le collecteur.
"""
from __future__ import annotations

import json
import os
import smtplib
import urllib.request
from email.message import EmailMessage
from typing import List, Optional

from loguru import logger


def _format_text(alerts: List[dict]) -> str:
    lines = [f"[VolInfra] {len(alerts)} alerte(s) QC/connectivité :"]
    for a in alerts[:20]:
        lines.append(f"  {a.get('level', '?')} {a.get('status', '')} "
                     f"{a.get('check', '')} · {a.get('target', '')} · "
                     f"{a.get('reason', '')} (owner={a.get('owner', '?')}, "
                     f"SLA {a.get('sla_minutes', '?')} min)")
    if len(alerts) > 20:
        lines.append(f"  … et {len(alerts) - 20} de plus (voir data/alerts.json)")
    return "\n".join(lines)


def _send_webhook(url: str, alerts: List[dict]) -> bool:
    payload = json.dumps({"text": _format_text(alerts)}).encode("utf-8")
    req = urllib.request.Request(url, data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return 200 <= resp.status < 300


def _send_email(smtp_cfg: dict, alerts: List[dict]) -> bool:
    host = smtp_cfg.get("host")
    sender = smtp_cfg.get("sender")
    recipients = smtp_cfg.get("recipients") or []
    if not (host and sender and recipients):
        return False
    msg = EmailMessage()
    msg["Subject"] = f"[VolInfra] {len(alerts)} alerte(s) QC"
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.set_content(_format_text(alerts))
    password = os.environ.get("VOL_SMTP_PASSWORD", "")
    with smtplib.SMTP(host, int(smtp_cfg.get("port", 587)), timeout=15) as s:
        s.starttls()
        if password:
            s.login(sender, password)
        s.send_message(msg)
    return True


def route_alerts(alerts: List[dict], alerting_cfg: Optional[dict]) -> dict:
    """
    Route les alertes vers les canaux configurés. Retourne un compte-rendu
    {"webhook": bool|None, "email": bool|None} (None = canal non configuré).
    """
    report = {"webhook": None, "email": None}
    if not alerts or not alerting_cfg:
        return report

    url = (alerting_cfg.get("webhook_url") or "").strip()
    if url:
        try:
            report["webhook"] = _send_webhook(url, alerts)
        except Exception as exc:  # jamais bloquant
            logger.warning(f"alert_router webhook: {exc}")
            report["webhook"] = False

    smtp_cfg = alerting_cfg.get("smtp") or {}
    if (smtp_cfg.get("host") or "").strip():
        try:
            report["email"] = _send_email(smtp_cfg, alerts)
        except Exception as exc:
            logger.warning(f"alert_router email: {exc}")
            report["email"] = False

    return report
