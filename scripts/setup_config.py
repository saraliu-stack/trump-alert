#!/usr/bin/env python3
"""
setup_config.py — Interactive setup for trump-alert delivery options.

Writes ~/.config/trump-alert/.env and optionally registers
a Windows Task Scheduler job.

Usage:
    python setup_config.py
    python setup_config.py --show          # print current config
    python setup_config.py --test-email    # send a test email
"""

import argparse
import os
import smtplib
import subprocess
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

CONFIG_DIR  = Path.home() / ".config" / "trump-alert"
CONFIG_FILE = CONFIG_DIR / ".env"
SKILL_SCRIPTS = Path(__file__).parent
PYTHON_EXE  = sys.executable

SMTP_PROVIDERS = {
    "gmail":   ("smtp.gmail.com",         587),
    "outlook": ("smtp-mail.outlook.com",  587),
    "yahoo":   ("smtp.mail.yahoo.com",    587),
    "office365": ("smtp.office365.com",   587),
}

GMAIL_HELP = """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  HOW TO CREATE A GMAIL APP PASSWORD
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  1. Go to: https://myaccount.google.com/apppasswords
     (you must have 2-Step Verification enabled)
  2. Click "Create app password"
  3. Name it "Trump Alert" and click Create
  4. Copy the 16-character password shown
  5. Paste it when prompted below

  Note: this is NOT your regular Gmail password.
  It is a one-time app-specific password that only
  works for SMTP. You can revoke it any time.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""


def load_config():
    config = {}
    if CONFIG_FILE.exists():
        for line in CONFIG_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                config[k.strip()] = v.strip()
    return config


def save_config(config: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Trump Alert — delivery config",
        f"# Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "# ── Schedule ──────────────────────────────────────────",
        f"SCHEDULE_ENABLED={config.get('SCHEDULE_ENABLED', 'false')}",
        f"SCHEDULE_TIME={config.get('SCHEDULE_TIME', '07:00')}",
        f"LOOKBACK_DAYS={config.get('LOOKBACK_DAYS', '30')}",
        "",
        "# ── Email ──────────────────────────────────────────────",
        f"EMAIL_ENABLED={config.get('EMAIL_ENABLED', 'false')}",
        f"SMTP_HOST={config.get('SMTP_HOST', 'smtp.gmail.com')}",
        f"SMTP_PORT={config.get('SMTP_PORT', '587')}",
        f"SMTP_USER={config.get('SMTP_USER', '')}",
        f"SMTP_PASS={config.get('SMTP_PASS', '')}",
        f"ALERT_TO={config.get('ALERT_TO', '')}",
        "",
        "# ── Filters ────────────────────────────────────────────",
        "# BUY_ONLY=false   # set true to only email on BUY signals",
        "# SKIP_PRICES=false",
    ]
    CONFIG_FILE.write_text("\n".join(lines), encoding="utf-8")
    print(f"  Config saved to {CONFIG_FILE}")


def register_windows_task(run_time: str, days: int, email_flag: str):
    """Register or update Windows Task Scheduler job."""
    script_path = SKILL_SCRIPTS / "run_daily.py"
    log_dir = Path.home() / "Documents" / "TrumpAlerts"
    log_dir.mkdir(parents=True, exist_ok=True)

    args = f'"{script_path}" --days={days} {email_flag}'

    ps_script = f"""
$action  = New-ScheduledTaskAction `
    -Execute '{PYTHON_EXE}' `
    -Argument '{args}' `
    -WorkingDirectory '{SKILL_SCRIPTS}'

$trigger  = New-ScheduledTaskTrigger -Daily -At "{run_time}"
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -StartWhenAvailable `
    -WakeToRun
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

$existing = Get-ScheduledTask -TaskName "TrumpAlertDaily" -ErrorAction SilentlyContinue
if ($existing) {{ Unregister-ScheduledTask -TaskName "TrumpAlertDaily" -Confirm:$false }}

Register-ScheduledTask `
    -TaskName    "TrumpAlertDaily" `
    -Action      $action `
    -Trigger     $trigger `
    -Settings    $settings `
    -Principal   $principal `
    -Description "Daily Trump company/stock alert digest."

Write-Output "OK"
"""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, timeout=30
        )
        if "OK" in result.stdout or "TrumpAlertDaily" in result.stdout:
            return True, None
        return False, result.stderr.strip()[:300]
    except Exception as e:
        return False, str(e)


def remove_windows_task():
    ps = "Unregister-ScheduledTask -TaskName 'TrumpAlertDaily' -Confirm:$false -ErrorAction SilentlyContinue; Write-Output 'OK'"
    subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   capture_output=True, timeout=15)


def send_test_email(config):
    smtp_host = config.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(config.get("SMTP_PORT", 587))
    smtp_user = config.get("SMTP_USER", "")
    smtp_pass = config.get("SMTP_PASS", "")
    alert_to  = config.get("ALERT_TO", smtp_user)

    if not smtp_user or not smtp_pass:
        print("  ✗ Email not configured. Run setup first.")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "✅ Trump Alert — Test Email"
    msg["From"]    = smtp_user
    msg["To"]      = alert_to
    body = "This is a test email from your Trump Alert setup.\n\nIf you received this, email delivery is working correctly."
    msg.attach(MIMEText(body, "plain"))
    msg.attach(MIMEText(f"<html><body><h2>✅ Trump Alert is working!</h2><p>{body}</p></body></html>", "html"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, alert_to, msg.as_string())
        print(f"  ✅ Test email sent to {alert_to}")
        return True
    except smtplib.SMTPAuthenticationError:
        print("  ✗ Authentication failed.")
        print("    For Gmail: make sure you used an App Password, not your regular password.")
        print("    Get one at: https://myaccount.google.com/apppasswords")
        return False
    except Exception as e:
        print(f"  ✗ Failed: {e}")
        return False


def prompt(text, default=None):
    suffix = f" [{default}]" if default else ""
    val = input(f"  {text}{suffix}: ").strip()
    return val if val else default


def yn(text, default="y"):
    suffix = "(Y/n)" if default == "y" else "(y/N)"
    val = input(f"  {text} {suffix}: ").strip().lower()
    if not val:
        return default == "y"
    return val.startswith("y")


def run_setup():
    print()
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  Trump Alert — Setup")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    config = load_config()

    # ── Option 1: Daily schedule ──────────────────────────
    print()
    print("  OPTION 1: Daily Scheduled Digest")
    print("  Runs automatically each morning and saves a digest")
    print("  file to ~/Documents/TrumpAlerts/")
    print()
    want_schedule = yn("Enable daily scheduled digest?",
                       "y" if config.get("SCHEDULE_ENABLED") == "true" else "n")

    run_time = config.get("SCHEDULE_TIME", "07:00")
    days     = config.get("LOOKBACK_DAYS", "30")

    if want_schedule:
        run_time = prompt("Run time (HH:MM, 24h)", run_time)
        days     = prompt("Look-back window in days", days)
        config["SCHEDULE_ENABLED"] = "true"
        config["SCHEDULE_TIME"]    = run_time
        config["LOOKBACK_DAYS"]    = days
    else:
        config["SCHEDULE_ENABLED"] = "false"
        remove_windows_task()
        print("  Daily schedule disabled.")

    # ── Option 2: Email notification ─────────────────────
    print()
    print("  OPTION 2: Email Notification")
    print("  Sends the digest to your inbox when it runs.")
    print()
    want_email = yn("Enable email notifications?",
                    "y" if config.get("EMAIL_ENABLED") == "true" else "n")

    if want_email:
        provider = prompt("Provider (gmail / outlook / yahoo / other)", "gmail").lower()
        if provider in SMTP_PROVIDERS:
            config["SMTP_HOST"] = SMTP_PROVIDERS[provider][0]
            config["SMTP_PORT"] = str(SMTP_PROVIDERS[provider][1])
        else:
            config["SMTP_HOST"] = prompt("SMTP host", config.get("SMTP_HOST", ""))
            config["SMTP_PORT"] = prompt("SMTP port", config.get("SMTP_PORT", "587"))

        if provider == "gmail":
            print(GMAIL_HELP)

        config["SMTP_USER"] = prompt("Your email address (sender)", config.get("SMTP_USER", ""))
        config["SMTP_PASS"] = prompt("App password", config.get("SMTP_PASS", ""))
        config["ALERT_TO"]  = prompt("Send alerts to (recipient)", config.get("ALERT_TO") or config.get("SMTP_USER", ""))
        config["EMAIL_ENABLED"] = "true"
    else:
        config["EMAIL_ENABLED"] = "false"
        print("  Email notifications disabled.")

    # ── Save config ───────────────────────────────────────
    print()
    save_config(config)

    # ── Register task if schedule enabled ─────────────────
    if want_schedule:
        email_flag = "--email" if want_email else ""
        print(f"\n  Registering Windows Task Scheduler job at {run_time}...")
        ok, err = register_windows_task(run_time, int(days), email_flag)
        if ok:
            print(f"  ✅ Task 'TrumpAlertDaily' registered — runs daily at {run_time}")
        else:
            print(f"  ✗ Task registration failed: {err}")
            print(f"  You can run it manually: python \"{SKILL_SCRIPTS / 'run_daily.py'}\" --days={days} {email_flag}")

    # ── Test email ────────────────────────────────────────
    if want_email and config.get("SMTP_PASS"):
        print()
        if yn("Send a test email now to verify?", "y"):
            send_test_email(config)

    # ── Summary ───────────────────────────────────────────
    print()
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  Setup complete!")
    print()
    if want_schedule:
        print(f"  ✅ Daily digest: {run_time} every day")
        print(f"     Saved to: ~/Documents/TrumpAlerts/")
    if want_email:
        print(f"  ✅ Email: {config.get('ALERT_TO')}")
    print()
    print("  Run a digest now:")
    print(f"    python \"{SKILL_SCRIPTS / 'run_daily.py'}\" --days=30{'  --email' if want_email else ''}")
    print()
    print("  Change settings anytime:")
    print(f"    python \"{SKILL_SCRIPTS / 'setup_config.py'}\"")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")


def show_config():
    config = load_config()
    if not config:
        print("No config found. Run: python setup_config.py")
        return
    print()
    print("━━━━━━━━━━━━━━━━ Current Config ━━━━━━━━━━━━━━━━")
    sched = config.get("SCHEDULE_ENABLED", "false") == "true"
    email = config.get("EMAIL_ENABLED",    "false") == "true"
    print(f"  Daily schedule: {'✅ ON at ' + config.get('SCHEDULE_TIME','?') if sched else '❌ OFF'}")
    print(f"  Email:          {'✅ ON → ' + config.get('ALERT_TO','?')       if email else '❌ OFF'}")
    print(f"  Lookback:       {config.get('LOOKBACK_DAYS','30')} days")
    print(f"  Config file:    {CONFIG_FILE}")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--show",       action="store_true", help="Print current config")
    parser.add_argument("--test-email", action="store_true", help="Send a test email")
    args = parser.parse_args()

    if args.show:
        show_config()
    elif args.test_email:
        send_test_email(load_config())
    else:
        run_setup()


if __name__ == "__main__":
    main()
