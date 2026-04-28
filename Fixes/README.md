# Fixes — Night Light & Settings Repair Tools

This folder contains tools to fix Windows **Night Light** and **Settings > System > Display** crashes,
which commonly break when registry state gets corrupted (e.g. after running audio routing scripts,
f.lux, DisplayFusion, or recovering from a failed Windows Update).

## Quick Start

Run this **one script** to fix everything:

```powershell
# Right-click → "Run with PowerShell" (as Administrator)
powershell -ExecutionPolicy Bypass -File ".\Fixes\Repair-NightLight.ps1"
```

## What it fixes

| Symptom | Cause |
|---------|-------|
| Night Light toggle greyed out | Corrupted `BlueLightReduction` binary data in CloudStore |
| Settings > System > Display crashes | `STATUS_STACK_BUFFER_OVERRUN` → `abort()` from invalid CloudStore binary data |
| `ms-settings:*` pages crash | All Settings pages load the same corrupted state |

## How it works

1. **Kills** Settings, f.lux, DisplayFusion (which may lock registry keys)
2. **Deletes ALL BlueLightReduction keys** from ALL CloudStore paths using Python
   (PowerShell scripts routinely miss these because `$` in registry key names is
   interpreted as a variable prefix)
3. **Clears the Store Current** Data value to force regeneration
4. **Wipes the CloudStore Cache** entirely
5. **Clears Settings app user data** (local state, temp, caches)
6. **Re-registers** the `windows.immersivecontrolpanel` AppX package
7. **Clears old crash dumps** from `%LOCALAPPDATA%\CrashDumps\`

## Why previous fixes didn't work

The corrupted keys live at:
```
HKCU\...\CloudStore\Store\DefaultAccount\Current\
                                      ^^^^^^^
```
Most fixes (including earlier versions in this project) only cleaned:
- `Store\Cache\DefaultAccount\`  (ephemeral cache)
- `Store\DefaultAccount\` (root level, skipped `Current\` subkeys)

The `Current\` subkeys survive cache-only wipes. This script uses Python's
`subprocess.run()` to call `reg.exe` directly, bypassing PowerShell's `$` expansion,
so it can find and delete keys like:
- `default$windows.data.bluelightreduction.settings`
- `{GUID}$windows.data.bluelightreduction.bluelightreductionstateperdevice`

## Side effects (harmless, self-healing)

- CloudStore state for quiet hours / tiles resets (regenerates on next sign-in)
- Settings app preferences reset (regenerates on first launch)
- You may need to sign out/in for full regeneration
- A reboot is recommended after running

## If it still doesn't work

- Run **Windows Update** and install the latest Cumulative Update
- Update your **NVIDIA / GPU driver**
- Try a **new Windows user profile** to isolate profile-level corruption
- Last resort: **Windows Settings → Recovery → Reset this PC (Keep my files)**
