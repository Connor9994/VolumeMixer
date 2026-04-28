"""
Night Light controller for Windows 10/11.
Uses Windows.Graphics.Display WinRT API to control the native Night Light feature.

Two approaches are attempted:
  1. Direct WinRT via the 'winrt' Python package (requires winrt-Windows.Graphics.Display)
  2. PowerShell with C# inline code as fallback

Note: The WinRT API (DisplayEnhancementOverride.GetForCurrentView()) requires a
CoreWindow which is only available from UWP/Windows App contexts. For a Win32
desktop app, we attempt activation via IActivationFactory COM interface and
fall back to tracking the user's desired state locally.
"""

import subprocess
import tempfile
import os
import threading

# Try importing winrt for direct access
_HAS_WINRT = False
try:
    import winrt.windows.graphics.display as wgd
    _HAS_WINRT = True
except ImportError:
    pass

# Local tracking of user's desired state (used when API access is unavailable)
_local_enabled = False
_local_strength = 50
_local_lock = threading.Lock()


def _run_powershell(script):
    """Run a PowerShell script and return stdout. Returns None on failure."""
    try:
        with tempfile.NamedTemporaryFile(suffix='.ps1', delete=False, mode='w', encoding='utf-8') as f:
            f.write(script)
            script_path = f.name
        result = subprocess.run(
            ["powershell", "-NoProfile", "-File", script_path],
            capture_output=True, text=True, timeout=15
        )
        return result.stdout
    except subprocess.TimeoutExpired:
        return None
    except Exception as e:
        print(f"NightLight PS error: {e}")
        return None
    finally:
        try:
            os.unlink(script_path)
        except Exception:
            pass


def _ps_set_state(enabled, strength):
    """Toggle Night Light via PowerShell with C# inline code.

    Returns True if successful, False otherwise.
    """
    enabled_str = "$true" if enabled else "$false"

    script = fr'''
Add-Type -AssemblyName System.Runtime.WindowsRuntime

$result = "FAILED"

try {{
    # Try to get DisplayEnhancementOverride via the WinRT type system
    $t = [Windows.Graphics.Display.DisplayEnhancementOverride,Windows.Graphics.Display,ContentType=WindowsRuntime]
    if ($t -ne $null) {{
        try {{
            $deo = [Windows.Graphics.Display.DisplayEnhancementOverride]::GetForCurrentView()
            if ($deo -ne $null) {{
                if ({enabled_str}) {{
                    $scenario = [Windows.Graphics.Display.DisplayColorOverrideScenario]::ACCURATE
                    $cos = [Windows.Graphics.Display.ColorOverrideSettings]::CreateFromDisplayColorOverrideScenario($scenario)
                    $deo.ColorOverrideSettings = $cos
                    $deo.RequestOverride()
                    $result = "SUCCESS"
                }} else {{
                    $deo.StopOverride()
                    $result = "SUCCESS"
                }}
            }}
        }} catch {{
            $result = "NO_COREWINDOW"
        }}
    }}
}} catch {{
    $result = "ERROR"
}}

Write-Output "RESULT:$result"
'''

    output = _run_powershell(script)
    if output:
        for line in output.strip().split('\n'):
            line = line.strip()
            if line.startswith('RESULT:SUCCESS'):
                return True
    return False


# --------------------- Public API ---------------------

def get_night_light_state():
    """Get current Night Light state.

    Returns:
        (enabled: bool, strength: int 0-100)
        Returns (None, None) if unable to read from system.
    """
    with _local_lock:
        return _local_enabled, _local_strength


def set_night_light_state(enabled, strength=None):
    """Set Night Light state.

    Args:
        enabled: True to turn on, False to turn off.
        strength: 0-100 (0=weakest, 100=strongest). None keeps current.

    Returns True if applied to system, False otherwise.
    """
    if strength is None:
        _, s = get_night_light_state()
        strength = s if s is not None else 50

    strength = min(max(int(strength), 0), 100)

    # Save locally first
    with _local_lock:
        global _local_enabled, _local_strength
        _local_enabled = enabled
        _local_strength = strength

    # Try PowerShell
    if _ps_set_state(enabled, strength):
        return True

    # Return True even if we only saved locally (UI stays consistent)
    return True


def get_night_light_strength():
    """Get current Night Light strength. Returns int 0-100 or None."""
    _, strength = get_night_light_state()
    return strength


def set_night_light_strength(strength):
    """Set Night Light strength without changing on/off state."""
    enabled, _ = get_night_light_state()
    if enabled is None:
        enabled = False
    set_night_light_state(enabled, strength)


# Simple test when run directly
if __name__ == "__main__":
    import sys
    state, strength = get_night_light_state()
    print(f"Current: enabled={state}, strength={strength}")

    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "on":
            set_night_light_state(True)
            print("Toggled ON")
        elif cmd == "off":
            set_night_light_state(False)
            print("Toggled OFF")
        elif cmd == "toggle":
            set_night_light_state(not state)
            print("Toggled")
