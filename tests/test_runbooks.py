"""Every alert has a runbook section (Telegram links to docs/runbooks/alerts.md#<alertname lower case>)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_every_alert_has_a_runbook_section():
    alerts = set()
    for f in (ROOT / "deploy" / "monitoring" / "prometheus" / "rules").glob("*.yml"):
        alerts |= set(re.findall(r"^\s*- alert: (\w+)", f.read_text(), re.MULTILINE))
    sections = set(re.findall(r"^## (\w+)$", (ROOT / "docs" / "runbooks" / "alerts.md").read_text(), re.MULTILINE))
    assert alerts, "no alerts found"
    assert not alerts - sections, f"alerts without a runbook section: {sorted(alerts - sections)}"
    assert not sections - alerts, f"runbook sections for alerts that no longer exist: {sorted(sections - alerts)}"


def test_telegram_links_to_the_runbook():
    tmpl = (ROOT / "deploy" / "monitoring" / "alertmanager" / "templates" / "telegram.tmpl").read_text()
    assert "docs/runbooks/alerts.md#{{ .Labels.alertname | toLower }}" in tmpl
