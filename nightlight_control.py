"""
nightlight_control.py — Control Windows Night Light via CloudStore registry.

Reads and writes the two REG_BINARY keys that store Night Light state and
color temperature (strength).  Template-based binary construction that
exactly matches what Windows Settings writes.

Usage:
  python nightlight_control.py status          # Show current state
  python nightlight_control.py on              # Turn ON
  python nightlight_control.py off             # Turn OFF
  python nightlight_control.py strength 50     # Set strength 0-100
  python nightlight_control.py set 70          # Turn ON + set strength
  python nightlight_control.py dump            # Export .reg backup
"""

import subprocess, sys, os
from datetime import datetime, timezone

# ── Registry paths ───────────────────────────────────────────────────────────
_BASE = (r"HKCU\Software\Microsoft\Windows\CurrentVersion"
         r"\CloudStore\Store\DefaultAccount")

_CUR_STATE  = (_BASE + r"\Current\default$windows.data.bluelightreduction"
               r".bluelightreductionstate"
               r"\windows.data.bluelightreduction.bluelightreductionstate")
_CUR_SETTINGS = (_BASE + r"\Current\default$windows.data.bluelightreduction"
                 r".settings"
                 r"\windows.data.bluelightreduction.settings")
_CLOUD_STATE  = (_BASE + r"\Cloud\default$windows.data.bluelightreduction"
                 r".bluelightreductionstate"
                 r"\windows.data.bluelightreduction.bluelightreductionstate")
_CLOUD_SETTINGS = (_BASE + r"\Cloud\default$windows.data.bluelightreduction"
                   r".settings"
                   r"\windows.data.bluelightreduction.settings")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _unix_ts():
    return int(datetime.now(timezone.utc).timestamp())


def _filetime():
    """Windows FILETIME: 100-ns intervals since 1601-01-01 UTC."""
    epoch = datetime(1601, 1, 1, tzinfo=timezone.utc)
    return int((datetime.now(timezone.utc) - epoch).total_seconds() * 10_000_000)


def _varint(v):
    """Encode non-negative integer as LEB128 varint."""
    buf = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            b |= 0x80
        buf.append(b)
        if not v:
            break
    return bytes(buf)


def _zigzag(v):
    """ZigZag encode a non-negative int → varint bytes."""
    return _varint(v * 2)


def _unzigzag_varint(data, offset=0):
    """Decode a ZigZag varint → (value, bytes_consumed)."""
    v = 0; shift = 0; consumed = 0
    while offset < len(data):
        b = data[offset]; consumed += 1
        v |= (b & 0x7F) << shift; shift += 7; offset += 1
        if not (b & 0x80):
            break
    return (v >> 1), consumed


def _read_hex(key):
    """Read REG_BINARY from registry → hex string or None."""
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


def _write(key, blob):
    """Write bytes to registry key."""
    hex_str = blob.hex().upper()
    r = subprocess.run(
        ["reg", "add", key, "/v", "Data", "/t", "REG_BINARY",
         "/d", hex_str, "/f"],
        capture_output=True, text=True, timeout=5)
    return r.returncode == 0


# ── Blob construction — templates match Windows exactly ─────────────────────

def _cloud_blob():
    """Minimal Cloud-path timestamp blob."""
    return b"\x43\x42\x01\x00\x0A\x00\x26" + _varint(_unix_ts()) + b"\x00"


def _state_blob(enabled):
    """Full Current-path state blob — ON or OFF."""
    unix_ts = _unix_ts()
    ft = _filetime()
    unix_var = _varint(unix_ts)
    ft_var = _varint(ft)

    if enabled:
        # ON: CB header + 1000 prefix sets field0 present = ON
        inner = (b"\x43\x42\x01\x00"
                 b"\x10\x00\xD0\x0A\x02\xC6\x14" + ft_var + b"\x00")
    else:
        # OFF: CB header + no 1000 prefix = OFF
        inner = (b"\x43\x42\x01\x00"
                 b"\xD0\x0A\x02\xC6\x14" + ft_var + b"\x00")

    return (b"\x43\x42\x01\x00\x0A\x02\x01\x00\x2A\x06" +
            unix_var +
            b"\x2A\x2B\x0E" + _varint(len(inner)) +
            inner +
            b"\x00\x00\x00")


def _kelvin_for_strength(strength):
    """Convert 0-100 strength → color temperature in Kelvin."""
    return 6500 - int((strength / 100.0) * (6500 - 1200))


def _settings_blob(strength):
    """Full Current-path settings blob with given strength (0-100)."""
    strength = max(0, min(100, strength))
    kelvin = _kelvin_for_strength(strength)
    ct_varint = _zigzag(kelvin)

    inner = (b"\x43\x42\x01\x00"           # CB header for inner payload
             b"\xCA\x14\x0E\x15\x00"       # field 20 STRUCT(start) hour=21
             b"\xCA\x1E\x0E\x07\x00"       # field 30 STRUCT(end) hour=7
             b"\xCF\x28" + ct_varint +      # field 40 INT16 color temp
             b"\xCA\x32\x00"                # field 50 STRUCT(sunset) empty
             b"\xCA\x3C\x00"                # field 60 STRUCT(sunrise) empty
             b"\x00\x00\x00\x00\x00")       # trailing STOPs (part of list)

    return (b"\x43\x42\x01\x00\x0A\x02\x01\x00\x2A\x06" +
            _varint(_unix_ts()) +
            b"\x2A\x2B\x0E\x19" +
            inner +
            b"\x00\x00\x00")


# ── Status parser ───────────────────────────────────────────────────────────

def _parse_state_inner(hex_str):
    """Parse the state blob → dict with 'enabled' key."""
    result = {'enabled': None}
    if not hex_str:
        return result
    try:
        data = bytes.fromhex(hex_str)
    except ValueError:
        return result
    idx = data.find(b"\x43\x42\x01\x00", 4)
    if idx < 0:
        return result
    inner = data[idx + 4:]
    if len(inner) >= 2 and inner[:2] == b"\x10\x00":
        result['enabled'] = True
    elif len(inner) >= 1 and inner[0] == 0xD0:
        result['enabled'] = False
    return result


def _parse_settings_inner(hex_str):
    """Parse the settings blob → dict with 'strength' and 'kelvin'."""
    result = {'strength': None, 'kelvin': None}
    if not hex_str:
        return result
    try:
        data = bytes.fromhex(hex_str)
    except ValueError:
        return result
    idx = data.find(b"\x43\x42\x01\x00", 4)
    if idx < 0:
        return result
    inner = data[idx + 4:]
    pos = inner.find(b"\xCF\x28")
    if pos >= 0:
        kelvin, _ = _unzigzag_varint(inner, pos + 2)
        result['kelvin'] = kelvin
        if 1200 <= kelvin <= 6500:
            raw = 100 - ((kelvin - 1200) / (6500 - 1200) * 100)
            result['strength'] = max(0, min(100, round(raw)))
    return result


# ── Public API ───────────────────────────────────────────────────────────────

def get_status():
    """Return dict with current night light state."""
    s = {}
    state_hex = _read_hex(_CUR_STATE)
    settings_hex = _read_hex(_CUR_SETTINGS)
    if state_hex:
        s.update(_parse_state_inner(state_hex))
    if settings_hex:
        s.update(_parse_settings_inner(settings_hex))
    s['state_hex'] = state_hex or 'N/A'
    s['settings_hex'] = settings_hex or 'N/A'
    return s


def set_enabled(on):
    """Turn Night Light ON (True) or OFF (False). Both Current + Cloud keys."""
    ok1 = _write(_CUR_STATE, _state_blob(on))
    ok2 = _write(_CLOUD_STATE, _cloud_blob())
    return ok1 and ok2


def set_strength(v):
    """Set Night Light strength 0-100. Both Current + Cloud keys."""
    v = max(0, min(100, v))
    ok1 = _write(_CUR_SETTINGS, _settings_blob(v))
    ok2 = _write(_CLOUD_SETTINGS, _cloud_blob())
    return ok1 and ok2


def set_both(v):
    """Turn ON and set strength in one call."""
    return set_enabled(True) and set_strength(v)


def dump():
    """Export both keys as .reg files."""
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "Fixes", "NightLightBackups",
                     f"NightLightBackup_{datetime.now():%Y%m%d_%H%M%S}")
    os.makedirs(d, exist_ok=True)
    for nm, kp in [("bluelightreductionstate", _CUR_STATE),
                   ("bluelightreduction.settings", _CUR_SETTINGS)]:
        o = os.path.join(d, f"{nm}.reg")
        r = subprocess.run(["reg", "export", kp, o, "/y"],
                           capture_output=True, text=True)
        print(f"  {'OK' if r.returncode == 0 else 'FAIL'} {o}")
    print(f"\nSaved: {d}")


def print_status():
    s = get_status()
    en = s.get('enabled')
    st = s.get('strength')
    kv = s.get('kelvin')
    print("=" * 50)
    print("  Windows Night Light — Status")
    print("=" * 50)
    if en is True:
        print(f"\n  State:     ON")
    elif en is False:
        print(f"\n  State:     OFF")
    else:
        print(f"\n  State:     UNKNOWN")
    if st is not None:
        print(f"  Strength:  {st}%  ({kv}K)")
    print()


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    if cmd == "status":
        print_status()
    elif cmd == "on":
        if set_enabled(True):
            print("Night Light: ON")
        else:
            print("FAILED")
            sys.exit(1)
    elif cmd == "off":
        if set_enabled(False):
            print("Night Light: OFF")
        else:
            print("FAILED")
            sys.exit(1)
    elif cmd == "strength":
        if len(sys.argv) < 3:
            print("Usage: python nightlight_control.py strength <0-100>")
            sys.exit(1)
        v = int(sys.argv[2])
        if set_strength(v):
            print(f"Night Light strength: {v}%")
        else:
            print("FAILED")
            sys.exit(1)
    elif cmd == "set":
        if len(sys.argv) < 3:
            print("Usage: python nightlight_control.py set <0-100>")
            sys.exit(1)
        v = int(sys.argv[2])
        if set_both(v):
            print(f"Night Light: ON at {v}%")
        else:
            print("FAILED")
            sys.exit(1)
    elif cmd == "dump":
        dump()
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
