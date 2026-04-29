"""
nightlight_poll.py — Polls the two Night Light registry keys every 2 seconds.
Prints the raw REG_BINARY hex so you can copy-paste after making changes.

Usage:
  python nightlight_poll.py

Then go toggle Night Light ON/OFF and move the strength slider in Windows Settings.
Copy the output and paste it back to me.
"""

import subprocess
import time

STATE_KEY  = r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\CloudStore\Store\DefaultAccount\Current\default$windows.data.bluelightreduction.bluelightreductionstate\windows.data.bluelightreduction.bluelightreductionstate"
SETTINGS_KEY = r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\CloudStore\Store\DefaultAccount\Current\default$windows.data.bluelightreduction.settings\windows.data.bluelightreduction.settings"

# Also check the Cloud path
CLOUD_STATE_KEY = r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\CloudStore\Store\DefaultAccount\Cloud\default$windows.data.bluelightreduction.bluelightreductionstate\windows.data.bluelightreduction.bluelightreductionstate"
CLOUD_SETTINGS_KEY = r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\CloudStore\Store\DefaultAccount\Cloud\default$windows.data.bluelightreduction.settings\windows.data.bluelightreduction.settings"


def read_reg(key):
    try:
        r = subprocess.run(["reg", "query", key, "/v", "Data"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return None
        for line in r.stdout.splitlines():
            if "REG_BINARY" in line:
                return line.split("REG_BINARY")[-1].strip()
        return None
    except Exception:
        return None


print("=" * 70)
print("  Night Light Registry Poller")
print("  Toggle Night Light ON/OFF and move the strength slider.")
print("  Copy the hex values printed below and paste them back to me.")
print("=" * 70)
print()

while True:
    state_hex = read_reg(STATE_KEY)
    settings_hex = read_reg(SETTINGS_KEY)
    cloud_state = read_reg(CLOUD_STATE_KEY)
    cloud_settings = read_reg(CLOUD_SETTINGS_KEY)

    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}]")
    print(f"  STATE (Current):    {state_hex or 'N/A'}")
    print(f"  SETTINGS (Current): {settings_hex or 'N/A'}")
    print(f"  STATE (Cloud):      {cloud_state or 'N/A'}")
    print(f"  SETTINGS (Cloud):   {cloud_settings or 'N/A'}")
    print()

    try:
        time.sleep(2)
    except KeyboardInterrupt:
        print("Stopped.")
        break
