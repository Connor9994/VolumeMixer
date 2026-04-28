"""
delete_bluelight_keys.py — Deletes ALL BlueLightReduction keys from CloudStore.

Called by Repair-NightLight.ps1. Written as a separate file (not inline)
because PowerShell interprets $ in registry key paths as variables.
"""
import subprocess
import sys

def main():
    bases = [
        r"HKCU\Software\Microsoft\Windows\CurrentVersion\CloudStore\Store\DefaultAccount\Current",
        r"HKCU\Software\Microsoft\Windows\CurrentVersion\CloudStore\Store\Cache\DefaultAccount",
        r"HKCU\Software\Microsoft\Windows\CurrentVersion\CloudStore\Store\DefaultAccount",
    ]

    deleted_count = 0
    error_count = 0
    found_keys = []

    for base in bases:
        find = subprocess.run(
            ['reg', 'query', base, '/s', '/f', 'bluelight'],
            capture_output=True, text=True
        )

        for line in find.stdout.split('\n'):
            line = line.strip()
            if not line or 'ERROR' in line or 'End of search' in line:
                continue
            if not line.startswith('HKEY'):
                continue

            # Delete the key recursively
            result = subprocess.run(
                ['reg', 'delete', line, '/f'],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                deleted_count += 1
                found_keys.append(line)
            else:
                error_count += 1

    # Report found keys for logging
    for key in found_keys:
        short = key.split('\\')[-1] if '\\' in key else key
        if len(short) > 70:
            short = short[:67] + '...'
        print(f"  Deleted: {short}")

    print(f"  [INFO] Removed {deleted_count} keys ({error_count} errors)")
    return 0 if error_count == 0 else 1

if __name__ == '__main__':
    sys.exit(main())
