import tkinter as tk
from tkinter import ttk
import threading
import subprocess
import json
import tempfile
import os
import ctypes
from ctypes import wintypes
import pystray
from PIL import Image, ImageTk
import sounddevice as sd
import numpy as np


# --- Configuration ---
EXPONENT = 2.0
POLL_INTERVAL_MS = 2000
IGNORED_DEVICES_FILE = "ignored_devices.txt" 
IGNORED_APPS = "ignored_apps.txt"
VIRTUAL_CABLE_NAME = "1"  # Virtual audio cable device for audio duplication
DUPLICATE_BLOCKSIZE = 256                        # Audio buffer size for duplication (lower = less latency)

# --- Global Variables ---
root = None
mixer_frame = None          # frame holding the app list (inside tab 1)
device_tab_frame = None     # frame holding the device list (tab 2)
app_widgets = {}
sound_volume_view = "SoundVolumeView.exe"
device_list = []            # list of (display_name, device_id)
device_name_to_id = {}
ignored_device_set = set()
ignored_app_set = set()
app_device_name = {}        # app_name -> current device display name
app_explicit_device = {}
app_exe_name = {}           # app_name (SVV display name) -> actual executable filename (e.g. 'chrome.exe')
protected_apps = set()      # apps currently switching device, don't remove from GUI
# duplicate popup tracking
duplicate_widgets = {}       # app_name -> dict of widget references in duplicate tab
duplicator_threads = {}      # app_name -> AudioDuplicator instance
duplicator_protected = set() # apps with duplication enabled, don't remove from duplicate tab
duplicator_targets = {}      # app_name -> list of target device display names
app_original_device = {}    # app_name -> device display name before duplication started
app_icon_cache = {}         # exe_path -> PIL Image (cached app icons)
app_full_path = {}          # app_name -> full executable path


def load_ignored_devices():                    
    global ignored_device_set
    ignored_device_set = set()
    if os.path.exists(IGNORED_DEVICES_FILE):
        with open(IGNORED_DEVICES_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                name = line.strip()
                if name:
                    ignored_device_set.add(name)


def load_ignored_apps():
    global ignored_app_set
    ignored_app_set = set()
    if os.path.exists(IGNORED_APPS):
        with open(IGNORED_APPS, 'r', encoding='utf-8') as f:
            for line in f:
                name = line.strip()
                if name:
                    if name.lower().endswith('.exe'):
                        name = name[:-4]
                    ignored_app_set.add(name.lower())


def get_work_area(window):
    try:
        rect = wintypes.RECT()
        SPI_GETWORKAREA = 0x0030
        ctypes.windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0)
        return rect.left, rect.top, rect.right, rect.bottom
    except Exception:
        if window:
            return 0, 0, window.winfo_screenwidth(), window.winfo_screenheight()
        else:
            temp = tk.Tk()
            w = temp.winfo_screenwidth()
            h = temp.winfo_screenheight()
            temp.destroy()
            return 0, 0, w, h


def position_at_bottom_right(window):
    window.update_idletasks()
    left, top, right, bottom = get_work_area(window)
    win_width = window.winfo_width()
    win_height = window.winfo_height()
    x = right - win_width
    y = bottom - win_height
    window.geometry(f"+{x}+{y}")


def resize_and_position():
    if not root or not root.winfo_exists():
        return
    root.update_idletasks()
    req_width = root.winfo_reqwidth()
    req_height = root.winfo_reqheight()
    root.geometry(f"{req_width}x{req_height}")
    position_at_bottom_right(root)


# --------------------- Device helpers (SoundVolumeView) ---------------------
def refresh_device_data():
    global device_list, device_name_to_id
    load_ignored_devices()

    device_list = []
    device_name_to_id = {}
    volumes = {}

    try:
        with tempfile.NamedTemporaryFile(mode='w+', suffix='.json', delete=False) as tmp:
            tmp_path = tmp.name

        subprocess.run([sound_volume_view, "/sjson", tmp_path],
                       capture_output=True, check=True)

        with open(tmp_path, 'r', encoding='utf-16') as f:
            data = json.load(f)
        os.unlink(tmp_path)

        temp_device_list = []
        for item in data:
            if item.get("Type") == "Device" and "Render" in item.get("Command-Line Friendly ID", ""):
                name = item.get("Name", "Unknown Device")
                device_id = item.get("Command-Line Friendly ID")
                if name and device_id:
                    temp_device_list.append((name, device_id))
                    vol = _parse_device_volume(item)
                    if vol is not None:
                        volumes[name] = vol

        temp_device_list.append(("Default Windows Device", "DefaultRenderDevice"))
        device_list = [(name, dev_id) for name, dev_id in temp_device_list
                       if name not in ignored_device_set]
        device_name_to_id = {name: dev_id for name, dev_id in device_list}

    except Exception as e:
        print(f"Error refreshing device data: {e}")
        fallback = [("Default Windows Device", "DefaultRenderDevice")]
        device_list = [(n, d) for n, d in fallback if n not in ignored_device_set]
        device_name_to_id = {n: d for n, d in device_list}

    return volumes


def _parse_device_volume(item):
    raw = item.get("Volume Percent")
    try:
        s = str(raw)
        s = s.replace(',', '.')
        cleaned = ''.join(c for c in s if c in '0123456789.-')
        if cleaned:
            val = float(cleaned)
            if 0 <= val <= 100:
                return val
            return min(val, 100.0)
    except (ValueError, TypeError):
        pass
    return None


def set_device_volume(device_name, volume_val):
    try:
        subprocess.run(
            [sound_volume_view, "/SetVolume", device_name, str(volume_val)],
            capture_output=True,
            check=True
        )
    except subprocess.CalledProcessError as e:
        print(f"Failed to set volume for {device_name}: {e}")


def _get_default_render_device_id():
    try:
        with tempfile.NamedTemporaryFile(mode='w+', suffix='.json', delete=False) as tmp:
            tmp_path = tmp.name
        subprocess.run([sound_volume_view, "/sjson", tmp_path],
                       capture_output=True, check=True)
        with open(tmp_path, 'r', encoding='utf-16') as f:
            data = json.load(f)
        os.unlink(tmp_path)
        for item in data:
            if (item.get("Type") == "Device"
                    and "Render" in item.get("Direction", "")
                    and item.get("Default") == "Render"):
                return item.get("Command-Line Friendly ID", "")
    except Exception as e:
        print(f"Error finding default render device: {e}")
    return None


def set_app_device(app_name, device_id, device_display_name=None):
    if not device_id:
        return
    # Use the actual executable name from SVV's Process Path, not the display name + '.exe'
    exe_name = app_exe_name.get(app_name, f"{app_name}.exe")
    cmd = [sound_volume_view, "/SetAppDefault", device_id, "all", exe_name]
    try:
        subprocess.run(cmd, capture_output=True, check=True)
        print(cmd)
        print(f"Routed {app_name} to device ID {device_id}")
    except subprocess.CalledProcessError as e:
        print(f"Failed to route {app_name}: {e}")


# --------------------- SVV-based device detection ---------------------
def _get_app_device_map_svv(svv_data):
    """Parse SVV JSON data to determine which device each app is connected to.
    
    SVV reports each app with potentially multiple entries (one per audio endpoint).
    Resolution logic:
    1. If an app has entries on ONLY ONE unique device AND at least one is active:
       → show that device name (app was never explicitly routed elsewhere)
    2. If an app has entries on MULTIPLE unique devices with exactly one active:
       a. If the active device is the system default → "Default Windows Device"
          (the app reverted to the system default after explicit routing)
       b. Otherwise → show the active device name (explicitly routed there)
    3. Otherwise (no active entries, or multiple active devices):
       → "Default Windows Device"
    
    Returns: dict: app_name (SVV Name) -> device_short_name
    """
    # Build full device name -> short device name mapping
    # Device entries have: "Name" (short display name), "Device Name" (full hardware name)
    # Only use Render devices — Capture devices (microphones) share the same
    # "Device Name" and would overwrite the Render mapping
    full_to_short = {}
    for item in svv_data:
        if item.get("Type") == "Device" and "Render" in item.get("Command-Line Friendly ID", ""):
            short_name = item.get("Name", "")
            full_name = item.get("Device Name", "")
            if short_name and full_name:
                full_to_short[full_name] = short_name
            if short_name:
                full_to_short[short_name] = short_name

    def resolve_device_name(raw_name):
        """Convert a raw device name from an Application entry to the short display name."""
        if not raw_name:
            return "Default Windows Device"
        if raw_name in full_to_short:
            return full_to_short[raw_name]
        # Try prefix before '(' (e.g., "Speakers (Pebble V3)" -> "Speakers")
        prefix = raw_name.split('(')[0].strip()
        if prefix in full_to_short:
            return full_to_short[prefix]
        return raw_name

    # Determine the default render device short name
    default_device_short = None
    for item in svv_data:
        if item.get("Type") == "Device" and item.get("Default") == "Render":
            default_device_short = item.get("Name", None)
            break

    # Collect entries per app
    app_entries = {}  # app_name -> set of (device_short_name, is_active)
    for item in svv_data:
        if item.get("Type") != "Application":
            continue
        name = item.get("Name", "")
        if not name or name.lower() in ('system sounds', 'svchost.exe', 'taskhostw.exe'):
            continue

        raw_dev_name = item.get("Device Name", "")
        short_dev_name = resolve_device_name(raw_dev_name)
        state = item.get("Device State", "")
        is_active = (state.lower() == "active")

        if name not in app_entries:
            app_entries[name] = set()
        app_entries[name].add((short_dev_name, is_active))

    # Resolve to a single device per app
    result = {}
    for app_name, entries in app_entries.items():
        unique_devices = set(dev for dev, _ in entries)
        active_devices = set(dev for dev, active in entries if active)
        has_active = bool(active_devices)

        if len(unique_devices) == 1 and has_active:
            result[app_name] = next(iter(unique_devices))
        elif len(active_devices) == 1:
            active_dev = next(iter(active_devices))
            if default_device_short and active_dev == default_device_short:
                result[app_name] = "Default Windows Device"
            else:
                result[app_name] = active_dev
        else:
            result[app_name] = "Default Windows Device"

    return result


# --------------------- Volume control ---------------------
def exponential_volume(t):
    return t ** EXPONENT


def _read_svv_volume_by_app_and_device(app_name, device_display_name):
    try:
        with tempfile.NamedTemporaryFile(mode='w+', suffix='.json', delete=False) as tmp:
            tmp_path = tmp.name
        subprocess.run([sound_volume_view, "/sjson", tmp_path],
                       capture_output=True, check=True)
        with open(tmp_path, 'r', encoding='utf-16') as f:
            data = json.load(f)
        os.unlink(tmp_path)

        candidates = []
        for item in data:
            if item.get("Type") != "Application":
                continue
            svv_name = item.get("Name", "")
            name_match = (svv_name == app_name or
                          svv_name.lower() == app_name.lower() or
                          svv_name.lower() == app_name.lower().replace('.exe', '') or
                          svv_name.lower().replace('.exe', '') == app_name.lower())
            if not name_match:
                continue

            dev_name = item.get("Device Name", "")
            vol = _parse_device_volume(item)
            candidates.append((dev_name, vol))

        if not candidates:
            return None

        if device_display_name == "Default Windows Device":
            for item in data:
                if (item.get("Type") == "Device"
                        and "Render" in item.get("Direction", "")
                        and item.get("Default") == "Render"):
                    actual_dev_name = item.get("Device Name", "")
                    if actual_dev_name:
                        for dev_name, vol in candidates:
                            if dev_name == actual_dev_name and vol is not None:
                                return vol
                    break
        else:
            good_dev_id = device_name_to_id.get(device_display_name, "")
            for dev_name, vol in candidates:
                if vol is None:
                    continue
                if dev_name == device_display_name:
                    return vol
                if good_dev_id and good_dev_id.startswith(dev_name + "\\"):
                    return vol

        for _, vol in candidates:
            if vol is not None:
                return vol
    except Exception as e:
        print(f"Error reading SVV volume for {app_name}: {e}")
    return None


def _set_svv_app_volume(app_name, svv_volume):
    try:
        subprocess.run(
            [sound_volume_view, "/SetVolume", app_name, str(svv_volume)],
            capture_output=True, check=True
        )
    except subprocess.CalledProcessError as e:
        print(f"SVV SetVolume failed for {app_name}: {e}")


def _update_slider_from_session(app_name, slider):
    # Guard against the slider being destroyed (e.g. during device switch race condition)
    try:
        exists = slider.winfo_exists()
    except tk.TclError:
        exists = False
    if not exists:
        return
    dev_name = app_device_name.get(app_name, "Default Windows Device")
    svv_vol = _read_svv_volume_by_app_and_device(app_name, dev_name)
    if svv_vol is not None:
        slider_pos = (svv_vol / 100.0) ** (1 / EXPONENT) * 100
        try:
            slider.set(slider_pos)
        except tk.TclError:
            pass  # widget was destroyed between check and set


def set_app_volume(app_name, slider_value):
    t = float(slider_value) / 100.0
    amplitude = exponential_volume(t)
    svv_volume = amplitude * 100
    _set_svv_app_volume(app_name, svv_volume)


# --------------------- App Icon Extraction (Windows) ---------------------
def extract_app_icon(exe_path, size=16):
    """Extract icon from an executable file using Windows shell API.
    Returns a PIL Image or None on failure.
    """
    # Check cache first
    if exe_path in app_icon_cache:
        return app_icon_cache[exe_path]

    # Validate the executable exists
    if not exe_path or not os.path.isfile(exe_path):
        return None

    try:
        # Define SHFILEINFOW structure
        class SHFILEINFOW(ctypes.Structure):
            _fields_ = [
                ('hIcon', wintypes.HANDLE),
                ('iIcon', ctypes.c_int),
                ('dwAttributes', ctypes.c_ulong),
                ('szDisplayName', ctypes.c_wchar * 260),
                ('szTypeName', ctypes.c_wchar * 80),
            ]

        SHGFI_ICON = 0x100
        SHGFI_SMALLICON = 0x1
        SHGFI_LARGEICON = 0x2

        flags = SHGFI_ICON | (SHGFI_SMALLICON if size <= 16 else SHGFI_LARGEICON)

        shfi = SHFILEINFOW()
        # Set proper ctypes signatures for ALL GDI/user32 functions (prevents 64-bit overflow)
        ctypes.windll.shell32.SHGetFileInfoW.restype = ctypes.c_void_p
        ctypes.windll.user32.GetIconInfo.argtypes = [wintypes.HICON, ctypes.c_void_p]
        ctypes.windll.gdi32.GetObjectW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p]
        ctypes.windll.user32.GetDC.restype = wintypes.HDC
        ctypes.windll.gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        ctypes.windll.gdi32.CreateCompatibleDC.restype = wintypes.HDC
        ctypes.windll.gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, wintypes.HANDLE, ctypes.c_uint]
        ctypes.windll.gdi32.CreateDIBSection.restype = wintypes.HBITMAP
        ctypes.windll.gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        ctypes.windll.gdi32.SelectObject.restype = wintypes.HGDIOBJ
        ctypes.windll.user32.DrawIconEx.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.HICON, ctypes.c_int, ctypes.c_int, ctypes.c_uint, wintypes.HBRUSH, ctypes.c_uint]
        ctypes.windll.gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
        ctypes.windll.gdi32.GetDIBits.restype = ctypes.c_int
        ctypes.windll.gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        ctypes.windll.gdi32.DeleteDC.argtypes = [wintypes.HDC]
        ctypes.windll.user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        ctypes.windll.user32.DestroyIcon.argtypes = [wintypes.HICON]
        ret = ctypes.windll.shell32.SHGetFileInfoW(
            exe_path, 0, ctypes.byref(shfi), ctypes.sizeof(shfi), flags
        )

        if not ret or not shfi.hIcon:
            return None

        hicon = shfi.hIcon

        # Get icon info to extract the color bitmap
        class ICONINFO(ctypes.Structure):
            _fields_ = [
                ('fIcon', wintypes.BOOL),
                ('xHotspot', wintypes.DWORD),
                ('yHotspot', wintypes.DWORD),
                ('hbmMask', wintypes.HBITMAP),
                ('hbmColor', wintypes.HBITMAP),
            ]

        icon_info = ICONINFO()
        ctypes.windll.user32.GetIconInfo(hicon, ctypes.byref(icon_info))
        hbm_color = icon_info.hbmColor

        # Get bitmap dimensions
        class BITMAP(ctypes.Structure):
            _fields_ = [
                ('bmType', ctypes.c_long),
                ('bmWidth', ctypes.c_long),
                ('bmHeight', ctypes.c_long),
                ('bmWidthBytes', ctypes.c_long),
                ('bmPlanes', ctypes.c_ushort),
                ('bmBitsPixel', ctypes.c_ushort),
                ('bmBits', ctypes.c_void_p),
            ]

        bmp = BITMAP()
        ctypes.windll.gdi32.GetObjectW(hbm_color, ctypes.sizeof(bmp), ctypes.byref(bmp))
        width, height = abs(bmp.bmWidth), abs(bmp.bmHeight)

        if width == 0 or height == 0:
            ctypes.windll.user32.DestroyIcon(hicon)
            ctypes.windll.gdi32.DeleteObject(hbm_color)
            ctypes.windll.gdi32.DeleteObject(icon_info.hbmMask)
            return None

        # Create screen DC and memory DC
        hdc_screen = ctypes.windll.user32.GetDC(None)
        hdc_mem = ctypes.windll.gdi32.CreateCompatibleDC(hdc_screen)

        # Create a 32-bit DIBSection to receive pixel data
        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ('biSize', ctypes.c_uint),
                ('biWidth', ctypes.c_long),
                ('biHeight', ctypes.c_long),
                ('biPlanes', ctypes.c_ushort),
                ('biBitCount', ctypes.c_ushort),
                ('biCompression', ctypes.c_uint),
                ('biSizeImage', ctypes.c_uint),
                ('biXPelsPerMeter', ctypes.c_long),
                ('biYPelsPerMeter', ctypes.c_long),
                ('biClrUsed', ctypes.c_uint),
                ('biClrImportant', ctypes.c_uint),
            ]

        class BITMAPINFO(ctypes.Structure):
            _fields_ = [
                ('bmiHeader', BITMAPINFOHEADER),
                ('bmiColors', ctypes.c_uint * 3),
            ]

        bih = BITMAPINFOHEADER()
        bih.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bih.biWidth = width
        bih.biHeight = -height  # top-down bitmap
        bih.biPlanes = 1
        bih.biBitCount = 32
        bih.biCompression = 0  # BI_RGB

        bmi = BITMAPINFO()
        bmi.bmiHeader = bih

        pixel_size = width * height * 4
        pixels = (ctypes.c_ubyte * pixel_size)()

        hbmp_dib = ctypes.windll.gdi32.CreateDIBSection(
            hdc_mem, ctypes.byref(bmi), 0, None, None, 0
        )

        if not hbmp_dib:
            ctypes.windll.gdi32.DeleteDC(hdc_mem)
            ctypes.windll.user32.ReleaseDC(None, hdc_screen)
            ctypes.windll.user32.DestroyIcon(hicon)
            ctypes.windll.gdi32.DeleteObject(hbm_color)
            ctypes.windll.gdi32.DeleteObject(icon_info.hbmMask)
            return None

        # Select DIBSection into memory DC and draw the icon
        hbmp_old = ctypes.windll.gdi32.SelectObject(hdc_mem, hbmp_dib)
        ctypes.windll.user32.DrawIconEx(
            hdc_mem, 0, 0, hicon, width, height, 0, None, 0x0003  # DI_NORMAL
        )

        # Extract pixel data
        ctypes.windll.gdi32.GetDIBits(
            hdc_mem, hbmp_dib, 0, height, pixels,
            ctypes.byref(bmi), 0
        )

        # Convert to PIL Image
        img = Image.frombuffer('RGBA', (width, height), bytes(pixels), 'raw', 'BGRA', 0, 1)

        # Resize if needed
        if size != width:
            img = img.resize((size, size), Image.LANCZOS)

        # Cache the result
        app_icon_cache[exe_path] = img

        # Cleanup
        ctypes.windll.gdi32.SelectObject(hdc_mem, hbmp_old)
        ctypes.windll.gdi32.DeleteObject(hbmp_dib)
        ctypes.windll.gdi32.DeleteDC(hdc_mem)
        ctypes.windll.user32.ReleaseDC(None, hdc_screen)
        ctypes.windll.user32.DestroyIcon(hicon)
        ctypes.windll.gdi32.DeleteObject(hbm_color)
        if icon_info.hbmMask:
            ctypes.windll.gdi32.DeleteObject(icon_info.hbmMask)

        return img

    except Exception as e:
        print(f"Failed to extract icon for {exe_path}: {e}")
        return None


def get_render_device_names():
    """Get list of render device display names (excluding Default and virtual cable)."""
    return [name for name, _ in device_list
            if name != "Default Windows Device"
            and VIRTUAL_CABLE_NAME not in name]


# --------------------- Audio Duplicator (Virtual Cable Loopback) ---------------------
class AudioDuplicator:
    """Capture audio from a virtual cable's loopback and play to multiple output devices.

    Usage:
        d = AudioDuplicator()
        d.start(["Speakers", "Headphones"])   # begin capture/playback
        d.stop()                               # end capture/playback
    """

    def __init__(self):
        self._running = False
        self._thread = None
        self._input_stream = None
        self._output_streams = []
        self._target_device_names = []  # display names, in same order as output streams
        self._channel_modes = []  # per-output: 'both', 'left', 'right', 'muted'

    def start(self, target_device_names, channel_modes=None):
        """Start capturing from virtual cable loopback and playing to target devices.

        Args:
            target_device_names: List of device display names to play to.
            channel_modes: List of 'both', 'left', or 'right' — one per target device.
                           Defaults to 'both' for all targets.

        Returns True on success, False on failure.
        """
        if self._running:
            return False

        if channel_modes is None:
            channel_modes = ['both'] * len(target_device_names)
        self._target_device_names = list(target_device_names)
        self._channel_modes = list(channel_modes)

        devices = sd.query_devices()
        hostapis = sd.query_hostapis()

        # Find the WASAPI host API index
        wasapi_idx = None
        for i, ha in enumerate(hostapis):
            if "WASAPI" in ha["name"]:
                wasapi_idx = i
                break

        if wasapi_idx is None:
            print("AudioDuplicator: WASAPI host API not found")
            return False

        # Find the virtual cable's WASAPI loopback INPUT device
        # (it appears as an input-capable device under the WASAPI host API)
        cable_loopback_idx = None
        for i, d in enumerate(devices):
            if (d["hostapi"] == wasapi_idx
                    and VIRTUAL_CABLE_NAME in d["name"]
                    and d["max_input_channels"] > 0):
                cable_loopback_idx = i
                break

        if cable_loopback_idx is None:
            print(f"AudioDuplicator: virtual cable '{VIRTUAL_CABLE_NAME}' WASAPI loopback not found")
            return False

        # Find target output device indices (prefer WASAPI host API, fallback to any)
        target_indices = []
        synced_names = []
        synced_modes = []
        for idx, target_name in enumerate(target_device_names):
            # First, try to find a WASAPI output device matching the name
            found_idx = None
            for i, d in enumerate(devices):
                if (d["hostapi"] == wasapi_idx
                        and target_name in d["name"]
                        and d["max_output_channels"] > 0):
                    found_idx = i
                    break
            # Fallback: search all host APIs
            if found_idx is None:
                for i, d in enumerate(devices):
                    if target_name in d["name"] and d["max_output_channels"] > 0:
                        found_idx = i
                        break
            if found_idx is not None:
                target_indices.append(found_idx)
                synced_names.append(target_name)
                ch_mode = self._channel_modes[idx] if idx < len(self._channel_modes) else 'both'
                synced_modes.append(ch_mode)
            else:
                print(f"AudioDuplicator: target device '{target_name}' not found")

        if not target_indices:
            print("AudioDuplicator: no valid target devices")
            return False

        # Keep device lists in sync with output streams
        self._target_device_names = synced_names
        self._channel_modes = synced_modes

        samplerate = int(devices[cable_loopback_idx]["default_samplerate"])
        self._running = True

        # Start output streams (one per target device)
        try:
            for idx in target_indices:
                stream = sd.OutputStream(
                    device=idx,
                    samplerate=samplerate,
                    channels=2,
                    dtype="float32",
                    blocksize=DUPLICATE_BLOCKSIZE,
                )
                stream.start()
                self._output_streams.append(stream)
        except Exception as e:
            print(f"AudioDuplicator: failed to create output streams: {e}")
            self.stop()
            return False

        # Start input stream on the virtual cable's WASAPI loopback device
        try:
            self._input_stream = sd.InputStream(
                device=cable_loopback_idx,
                samplerate=samplerate,
                channels=2,
                dtype="float32",
                blocksize=DUPLICATE_BLOCKSIZE,
            )
            self._input_stream.start()
        except Exception as e:
            print(f"AudioDuplicator: failed to create input stream: {e}")
            self.stop()
            return False

        # Start worker thread: read from input, write to all outputs
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        print(f"AudioDuplicator: started, routing to {target_device_names}")
        return True

    def update_channel_mode(self, device_name, mode):
        """Update the channel mode for a specific device while running."""
        for i, name in enumerate(self._target_device_names):
            if name == device_name:
                self._channel_modes[i] = mode
                return

    def set_device_muted(self, device_name, muted):
        """Mute or unmute a specific output device while running."""
        for i, name in enumerate(self._target_device_names):
            if name == device_name:
                self._channel_modes[i] = 'muted' if muted else 'both'
                return

    def _run(self):
        """Worker thread body — blocking read -> write loop with per-device channel routing."""
        while self._running:
            try:
                data, overflowed = self._input_stream.read(DUPLICATE_BLOCKSIZE)
                if overflowed:
                    print("AudioDuplicator: input overflow")
                for i, s in enumerate(self._output_streams):
                    ch_mode = self._channel_modes[i] if i < len(self._channel_modes) else 'both'
                    if ch_mode == 'both':
                        s.write(data)
                    elif ch_mode == 'left':
                        # Left audio only through left speaker, right silent
                        out = np.zeros_like(data)
                        out[:, 0] = data[:, 0]
                        s.write(out)
                    elif ch_mode == 'right':
                        # Right audio only through right speaker, left silent
                        out = np.zeros_like(data)
                        out[:, 1] = data[:, 1]
                        s.write(out)
                    elif ch_mode == 'muted':
                        pass  # Device muted, skip write
                    else:
                        s.write(data)
            except Exception as e:
                if self._running:
                    print(f"AudioDuplicator: error in run loop: {e}")

    def stop(self):
        """Stop all streams and the worker thread."""
        self._running = False
        if self._input_stream is not None:
            try:
                self._input_stream.stop()
                self._input_stream.close()
            except Exception:
                pass
            self._input_stream = None
        for s in self._output_streams:
            try:
                s.stop()
                s.close()
            except Exception:
                pass
        self._output_streams.clear()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2)
            self._thread = None
        print("AudioDuplicator: stopped")


# --------------------- App list UI (Tab 1) ---------------------
def refresh_app_list():
    if not mixer_frame or not mixer_frame.winfo_exists():
        return

    for widget in mixer_frame.winfo_children():
        if isinstance(widget, ttk.Frame):
            widget.destroy()
    app_widgets.clear()

    refresh_device_data()
    load_ignored_apps()

    try:
        with tempfile.NamedTemporaryFile(mode='w+', suffix='.json', delete=False) as tmp:
            tmp_path = tmp.name
        subprocess.run([sound_volume_view, "/sjson", tmp_path],
                       capture_output=True, check=True)
        with open(tmp_path, 'r', encoding='utf-16') as f:
            data = json.load(f)
        os.unlink(tmp_path)
    except Exception as e:
        print(f"Error getting audio data: {e}")
        return

    # Build mapping from SVV display name -> actual executable filename
    global app_exe_name, app_full_path
    app_exe_name = {}
    app_full_path = {}
    for item in data:
        if item.get("Type") == "Application":
            name = item.get("Name", "")
            proc_path = item.get("Process Path", "")
            if name and proc_path:
                app_exe_name[name] = os.path.basename(proc_path)
                app_full_path[name] = proc_path

    # Only include apps with at least one active audio session
    app_names = set()
    for item in data:
        if item.get("Type") == "Application":
            name = item.get("Name")
            if name and name not in ['SystemSounds', 'svchost.exe', 'taskhostw.exe']:
                state = item.get("Device State", "")
                if state.lower() == "active":
                    app_names.add(name)

    # Use SVV data to determine which device each app is connected to
    svv_device_map = _get_app_device_map_svv(data)
    app_current_device = {}
    for name in app_names:
        dev = svv_device_map.get(name)
        if dev and dev in device_name_to_id:
            app_current_device[name] = dev
        else:
            app_current_device[name] = "Default Windows Device"

    filtered_apps = []
    for app in app_names:
        clean = app[:-4] if app.lower().endswith('.exe') else app
        if clean.lower() not in ignored_app_set:
            filtered_apps.append(app)

    if not filtered_apps:
        resize_and_position()
        return

    clean_names = [app[:-4] if app.lower().endswith('.exe') else app for app in filtered_apps]
    max_name_len = max((len(name) for name in clean_names), default=15)

    device_names = [name for name, _ in device_list]

    for app in sorted(filtered_apps):
        add_app_row(app, device_names, app_current_device, max_name_len)


def add_app_row(app, device_names, app_current_device, max_name_len=15):
    if app in app_widgets:
        return

    clean_name_app = app[:-4] if app.lower().endswith('.exe') else app
    frame = ttk.Frame(mixer_frame)
    frame.pack(fill=tk.X, padx=10, pady=5)

    label = ttk.Label(frame, text=clean_name_app, width=max_name_len, anchor='w')
    label.pack(side=tk.LEFT, padx=(0, 5))

    slider = ttk.Scale(frame, from_=0, to=100, orient=tk.HORIZONTAL)
    slider.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
    slider._debounce_after_id = None

    def make_app_update_live(a):
        def update_live(event, s=slider):
            if s._debounce_after_id is not None:
                s.after_cancel(s._debounce_after_id)
            s._debounce_after_id = s.after(100, lambda: (set_app_volume(a, s.get()),
                                                         setattr(s, '_debounce_after_id', None)))
        return update_live

    slider.bind("<B1-Motion>", make_app_update_live(app))
    slider.bind("<ButtonRelease-1>",
                lambda e, a=app, s=slider: (
                    s.after_cancel(s._debounce_after_id) if s._debounce_after_id else None,
                    set_app_volume(a, s.get())
                ))

    combo_width = max((len(name) for name in device_names), default=20) + 2
    device_var = tk.StringVar()

    # Determine correct device string
    # 1) Use user’s explicit choice if it exists and is still valid
    if app in app_explicit_device and app_explicit_device[app] in device_names:
        current_device = app_explicit_device[app]
    else:
        # 2) Fall back to heuristic from polling
        current_device = app_current_device.get(app)
        if not current_device or current_device not in device_names:
            current_device = "Default Windows Device"

    device_dropdown = ttk.Combobox(frame, textvariable=device_var,
                                   values=device_names, state='readonly',
                                   width=combo_width)
    # --- Popup button (gear icon) for duplication + channel routing ---
    dup_btn = ttk.Button(frame, text="⚙", width=3,
                         command=lambda a=app: open_duplicate_popup(a))
    dup_btn.pack(side=tk.RIGHT, padx=(2, 0))

    device_dropdown.pack(side=tk.RIGHT, padx=(5, 0))

    root.after(10, lambda dd=device_dropdown, v=current_device: dd.set(v))

    # Store the current device for volume reading
    app_device_name[app] = current_device

    def on_device_select(event, a=app, dv=device_var, s=slider):
        selected_name = dv.get()
        if selected_name in device_name_to_id:
            app_device_name[a] = selected_name
            # Remember user's explicit choice
            app_explicit_device[a] = selected_name
            set_app_device(a, device_name_to_id[selected_name], selected_name)
            # Protect this app from being removed during the brief device switch gap
            protected_apps.add(a)
            # Remove protection after 3s — enough time for the app to reappear
            root.after(3000, lambda a=a: protected_apps.discard(a))
            root.after(500, lambda a=a, s=s: _update_slider_from_session(a, s))

    device_dropdown.bind('<<ComboboxSelected>>', on_device_select)

    root.after(50, lambda a=app, s=slider: _update_slider_from_session(a, s))

    # --- App icon ---
    icon_img = None
    full_exe_path = app_full_path.get(app, "")
    if full_exe_path:
        pil_img = extract_app_icon(full_exe_path, size=16)
        if pil_img is not None:
            icon_img = ImageTk.PhotoImage(pil_img)
            icon_label = ttk.Label(frame, image=icon_img)
            icon_label.pack(side=tk.LEFT, padx=(0, 3))
        else:
            icon_label = ttk.Label(frame, text="", width=2)
            icon_label.pack(side=tk.LEFT, padx=(0, 3))
    else:
        icon_label = ttk.Label(frame, text="", width=2)
        icon_label.pack(side=tk.LEFT, padx=(0, 3))

    app_widgets[app] = {
        'frame': frame,
        'slider': slider,
        'dropdown': device_dropdown,
        'icon_img': icon_img,
    }


def poll_new_apps():
    if not mixer_frame or not mixer_frame.winfo_exists():
        return
    root.after(POLL_INTERVAL_MS, poll_new_apps)

    try:
        with tempfile.NamedTemporaryFile(mode='w+', suffix='.json', delete=False) as tmp:
            tmp_path = tmp.name
        subprocess.run([sound_volume_view, "/sjson", tmp_path],
                       capture_output=True, check=True)
        with open(tmp_path, 'r', encoding='utf-16') as f:
            data = json.load(f)
        os.unlink(tmp_path)

        # Refresh executable name mapping and full path
        for item in data:
            if item.get("Type") == "Application":
                name = item.get("Name", "")
                proc_path = item.get("Process Path", "")
                if name and proc_path:
                    app_exe_name[name] = os.path.basename(proc_path)
                    app_full_path[name] = proc_path

        current_apps = set()
        for item in data:
            if item.get("Type") == "Application":
                name = item.get("Name")
                if name and name not in ['SystemSounds', 'svchost.exe', 'taskhostw.exe']:
                    state = item.get("Device State", "")
                    if state.lower() == "active":
                        current_apps.add(name)

        # Removal of closed apps
        # Do NOT remove apps that are currently switching device (briefly inactive after routing)
        existing_apps = set(app_widgets.keys())
        # Protect both device-switching apps AND duplication-enabled apps from removal
        removed_apps = (existing_apps - current_apps) - protected_apps - duplicator_protected
        for app in removed_apps:
            if app in app_widgets:
                slider = app_widgets[app].get('slider')
                if slider and hasattr(slider, '_debounce_after_id') and slider._debounce_after_id:
                    slider.after_cancel(slider._debounce_after_id)
                app_widgets[app]['frame'].destroy()
                del app_widgets[app]
                if app in app_device_name:
                    del app_device_name[app]
            # Clean up popup/duplication state
            if app in duplicate_widgets:
                # Stop any active duplication
                if app in duplicator_threads:
                    _stop_duplication(app, None)
                dup_data = duplicate_widgets.pop(app, None)
                popup_win = dup_data.get('popup_window') if dup_data else None
                if popup_win and popup_win.winfo_exists():
                    try:
                        popup_win.destroy()
                    except tk.TclError:
                        pass
            if app in app_original_device:
                del app_original_device[app]
            if app in app_full_path:
                del app_full_path[app]
            if app in app_explicit_device:
                del app_explicit_device[app]
        if removed_apps:
            resize_and_position()

        new_apps = current_apps - set(app_widgets.keys())
        if not new_apps:
            return

        new_filtered = set()
        for app in new_apps:
            clean = app[:-4] if app.lower().endswith('.exe') else app
            if clean.lower() not in ignored_app_set:
                new_filtered.add(app)
        new_apps = new_filtered
        if not new_apps:
            return

        device_names = [name for name, _ in device_list]
        all_known = set(app_widgets.keys()) | new_apps
        clean_known = [a[:-4] if a.lower().endswith('.exe') else a for a in all_known]
        max_name_len = max((len(name) for name in clean_known), default=15)

        # Use SVV data to determine which device each new app is connected to
        svv_device_map = _get_app_device_map_svv(data)
        app_current_device = {}
        for proc_name in new_apps:
            dev = svv_device_map.get(proc_name)
            if dev and dev in device_name_to_id:
                app_current_device[proc_name] = dev
            else:
                app_current_device[proc_name] = "Default Windows Device"

        for app in sorted(new_apps):
            add_app_row(app, device_names, app_current_device, max_name_len)
        resize_and_position()

    except Exception as e:
        print(f"Error in poll_new_apps: {e}")


# --------------------- Device Volume tab (Tab 2) ---------------------
def build_device_tab():
    for widget in device_tab_frame.winfo_children():
        widget.destroy()

    volumes = refresh_device_data()
    inner_frame = ttk.Frame(device_tab_frame)
    inner_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

    for name, dev_id in device_list:
        row = ttk.Frame(inner_frame)
        row.pack(fill=tk.X, pady=3)
        ttk.Label(row, text=name, width=30, anchor='w').pack(side=tk.LEFT)

        init_vol = volumes.get(name, 50)
        slider = ttk.Scale(row, from_=0, to=100, orient=tk.HORIZONTAL, value=init_vol)
        slider.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(10, 0))

        slider._debounce_after_id = None

        def make_update_live(n):
            def update_live(event, s=slider):
                if s._debounce_after_id is not None:
                    s.after_cancel(s._debounce_after_id)
                s._debounce_after_id = s.after(100, lambda: (set_device_volume(n, s.get()),
                                                             setattr(s, '_debounce_after_id', None)))
            return update_live

        slider.bind("<B1-Motion>", make_update_live(name))
        slider.bind("<ButtonRelease-1>",
                    lambda e, n=name, s=slider: (
                        s.after_cancel(s._debounce_after_id) if s._debounce_after_id else None,
                        set_device_volume(n, s.get())
                    ))

    resize_and_position()


# --------------------- Duplication Popup ---------------------
def open_duplicate_popup(app_name):
    """Open a popup window for duplication and per-device channel routing, centered on the same monitor as the main GUI."""
    try:
        existing_popup = duplicate_widgets.get(app_name, {}).get('popup_window')
        if existing_popup and existing_popup.winfo_exists():
            existing_popup.lift()
            existing_popup.focus_force()
            return
    except Exception:
        pass

    clean_name = app_name[:-4] if app_name.lower().endswith('.exe') else app_name

    try:
        # Create popup
        popup = tk.Toplevel()
        popup.title(f"{clean_name} — Audio Routing")
        popup.minsize(420, 220)
        popup.resizable(False, False)
        popup.attributes("-topmost", True)

        # Define the cleanup function EARLY so it's available for the Close button
        def on_popup_close():
            """Clean up when popup is closed."""
            if app_name in duplicate_widgets:
                duplicate_widgets[app_name].pop('popup_window', None)
            try:
                popup.destroy()
            except tk.TclError:
                pass

        # --- Layout ---
        main_frame = ttk.Frame(popup, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Header
        ttk.Label(main_frame, text=f"{clean_name} — Duplication & Channel Routing",
                  font=('', 10, 'bold')).pack(anchor='w')

        ttk.Label(main_frame,
                  text=f"Route audio to multiple devices and choose left/right channel per device.\n"
                        f"Uses virtual cable \"{VIRTUAL_CABLE_NAME}\" — must be installed.",
                  wraplength=360, foreground='gray').pack(anchor='w', pady=(0, 10))

        # Duplicate toggle
        dup_var = tk.BooleanVar(value=app_name in duplicator_threads)
        enable_cb = ttk.Checkbutton(main_frame, text="Enable Duplication", variable=dup_var)
        enable_cb.pack(anchor='w', pady=(0, 10))

        # --- Per-device rows ---
        devices_frame = ttk.LabelFrame(main_frame, text="Output Devices", padding=5)
        devices_frame.pack(fill=tk.BOTH, expand=True)

        render_devices = get_render_device_names()
        device_vars = {}
        channel_vars = {}
        status_label = ttk.Label(main_frame, text="", foreground='gray')
        status_label.pack(anchor='w', pady=(5, 0))

        if not render_devices:
            ttk.Label(devices_frame, text="No output devices available.",
                      foreground='gray').pack(padx=10, pady=10)
        else:
            header_row = ttk.Frame(devices_frame)
            header_row.pack(fill=tk.X, pady=(0, 5))
            ttk.Label(header_row, text="Device", width=20, anchor='w').pack(side=tk.LEFT, padx=(0, 10))
            ttk.Label(header_row, text="Channel", width=12, anchor='w').pack(side=tk.LEFT)

            saved_targets = duplicator_targets.get(app_name, [])
            saved_modes = duplicate_widgets.get(app_name, {}).get('channel_modes', {})

            for dev in render_devices:
                row = ttk.Frame(devices_frame)
                row.pack(fill=tk.X, pady=2)

                dev_var = tk.BooleanVar(value=dev in saved_targets)
                cb = ttk.Checkbutton(row, text=dev, variable=dev_var, width=22)
                cb.pack(side=tk.LEFT)

                ch_var = tk.StringVar(value=saved_modes.get(dev, 'both'))
                ch_combo = ttk.Combobox(row, textvariable=ch_var,
                                        values=['both', 'left', 'right'],
                                        state='readonly', width=10)
                ch_combo.pack(side=tk.LEFT, padx=(5, 0))

                # Live update: device checkbox mutes/unmutes in real-time
                def make_device_toggle(d, dv, cv):
                    def on_device_toggle():
                        if app_name in duplicator_threads:
                            dup = duplicator_threads[app_name]
                            muted = not dv.get()
                            dup.set_device_muted(d, muted)
                            # Re-apply channel mode when unmuting
                            if not muted:
                                dup.update_channel_mode(d, cv.get())
                    return on_device_toggle
                cb.config(command=make_device_toggle(dev, dev_var, ch_var))

                # Live update: channel dropdown changes mode in real-time
                def make_channel_change(d, cv):
                    def on_channel_change(event=None):
                        if app_name in duplicator_threads:
                            dup = duplicator_threads[app_name]
                            dup.update_channel_mode(d, cv.get())
                    return on_channel_change
                ch_combo.bind('<<ComboboxSelected>>', make_channel_change(dev, ch_var))

                device_vars[dev] = dev_var
                channel_vars[dev] = ch_var

            # Select All / Clear All buttons
            btn_row = ttk.Frame(devices_frame)
            btn_row.pack(fill=tk.X, pady=(5, 0))

            def select_all():
                for v in device_vars.values():
                    v.set(True)
                if app_name in duplicator_threads:
                    for d, v in device_vars.items():
                        dup = duplicator_threads[app_name]
                        dup.set_device_muted(d, False)

            def clear_all():
                for v in device_vars.values():
                    v.set(False)
                if app_name in duplicator_threads:
                    for d, v in device_vars.items():
                        dup = duplicator_threads[app_name]
                        dup.set_device_muted(d, True)

            ttk.Button(btn_row, text="Select All", command=select_all).pack(side=tk.LEFT, padx=(0, 5))
            ttk.Button(btn_row, text="Clear All", command=clear_all).pack(side=tk.LEFT)

        # Restore status if already duplicating
        if app_name in duplicator_threads and duplicator_threads[app_name]._running:
            saved_targets = duplicator_targets.get(app_name, [])
            saved_modes = duplicate_widgets.get(app_name, {}).get('channel_modes', {})
            details = []
            for dev in saved_targets:
                mode = saved_modes.get(dev, 'both')
                details.append(f"{dev} ({mode})" if mode != 'both' else dev)
            status_label.config(text=f"Duplicating to: {', '.join(details)}", foreground='green')

        # --- Toggle Handler ---
        def on_toggle(*_):
            if dup_var.get():
                selected_devices = [d for d, v in device_vars.items() if v.get()]
                if not selected_devices:
                    selected_devices = list(device_vars.keys())
                    for v in device_vars.values():
                        v.set(True)

                channel_modes = [channel_vars[d].get() for d in selected_devices]

                duplicate_widgets.setdefault(app_name, {})['channel_modes'] = {
                    d: channel_vars[d].get() for d in device_vars
                }

                status_label.config(text="Starting...", foreground='gray')
                _start_duplication(app_name, selected_devices, channel_modes, status_label)
            else:
                status_label.config(text="Stopping...", foreground='gray')
                _stop_duplication(app_name, status_label)

        dup_var.trace_add('write', on_toggle)

        duplicate_widgets.setdefault(app_name, {})['popup_window'] = popup

        # Close button (now safe to reference on_popup_close)
        close_btn = ttk.Button(main_frame, text="Close", command=on_popup_close)
        close_btn.pack(anchor='e', pady=(5, 0))

        # ----- Center the popup on the monitor where the main GUI is -----
        if root.winfo_exists():
            popup.update_idletasks()
            pw = popup.winfo_reqwidth()
            ph = popup.winfo_reqheight()

            # Get monitor work area for the monitor that contains the root window
            m_left, m_top, m_right, m_bottom = get_work_area(root)

            center_x = m_left + (m_right - m_left) // 2
            center_y = m_top + (m_bottom - m_top) // 2

            px = center_x - pw // 2
            py = center_y - ph // 2

            # Keep inside the work area
            px = max(m_left, min(px, m_right - pw))
            py = max(m_top, min(py, m_bottom - ph))

            popup.geometry(f"+{px}+{py}")

        popup.lift()
        popup.focus_force()

        popup.protocol("WM_DELETE_WINDOW", on_popup_close)

    except Exception as e:
        print(f"Error opening popup for {app_name}: {e}")

def _get_duplicate_device_id(app_name):
    """Get the virtual cable device ID for routing an app's audio to the cable."""
    cable_id = device_name_to_id.get(VIRTUAL_CABLE_NAME)
    if cable_id:
        return cable_id
    # Fallback: find the virtual cable in SVV data
    try:
        with tempfile.NamedTemporaryFile(mode='w+', suffix='.json', delete=False) as tmp:
            tmp_path = tmp.name
        subprocess.run([sound_volume_view, "/sjson", tmp_path],
                       capture_output=True, check=True)
        with open(tmp_path, 'r', encoding='utf-16') as f:
            data = json.load(f)
        os.unlink(tmp_path)
        for item in data:
            if item.get("Type") == "Device" and VIRTUAL_CABLE_NAME in item.get("Name", ""):
                return item.get("Command-Line Friendly ID")
    except Exception as e:
        print(f"Error finding virtual cable device: {e}")
    return None

def _start_duplication(app_name, target_devices, channel_modes, status_label):
    """Route app to virtual cable and start audio capture/playback to target devices.
    
    channel_modes: list of 'both', 'left', or 'right' — one per target device.
    """
    try:
        # Save the original device BEFORE routing to virtual cable
        app_original_device[app_name] = app_device_name.get(app_name, "Default Windows Device")

        # Route the app to the virtual cable
        cable_id = _get_duplicate_device_id(app_name)
        if not cable_id:
            status_label.config(text="Virtual cable not found!", foreground='red')
            return

        set_app_device(app_name, cable_id, VIRTUAL_CABLE_NAME)

        # Protect from removal during the brief inactive period
        duplicator_protected.add(app_name)
        root.after(3000, lambda: duplicator_protected.discard(app_name) if app_name in duplicator_protected else None)

        # Start the audio duplicator
        dup = AudioDuplicator()
        success = dup.start(target_devices, channel_modes)
        if success:
            duplicator_threads[app_name] = dup
            duplicator_targets[app_name] = list(target_devices)
            target_details = []
            for i, dev in enumerate(target_devices):
                mode = channel_modes[i] if i < len(channel_modes) else 'both'
                if mode == 'both':
                    target_details.append(dev)
                else:
                    target_details.append(f"{dev} ({mode})")
            status_label.config(
                text=f"Duplicating to: {', '.join(target_details)}",
                foreground='green'
            )
        else:
            status_label.config(
                text="Failed to start (check virtual cable)", foreground='red'
            )
    except Exception as e:
        print(f"Error in _start_duplication for {app_name}: {e}")
        try:
            status_label.config(text=f"Error: {e}", foreground='red')
        except Exception:
            pass

def _stop_duplication(app_name, status_label):
    """Stop audio duplication and restore app to original device."""

    # Stop the audio duplicator
    dup = duplicator_threads.pop(app_name, None)
    duplicator_targets.pop(app_name, None)
    if dup:
        dup.stop()

    # Restore the app to the device it was on before duplication
    original_device = app_original_device.pop(app_name, None)
    if original_device and original_device in device_name_to_id:
        restore_id = device_name_to_id[original_device]
        set_app_device(app_name, restore_id, original_device)
    else:
        # Fallback to default Windows device
        default_id = device_name_to_id.get("Default Windows Device")
        if not default_id:
            default_id = _get_default_render_device_id()
        if default_id:
            set_app_device(app_name, default_id, "Default Windows Device")

    if status_label and status_label.winfo_exists():
        status_label.config(text="", foreground='gray')

    duplicator_protected.discard(app_name)


# --------------------- Tab change handling ---------------------
def on_tab_changed(event):
    resize_and_position()


# --------------------- Tray icon ---------------------
def create_tray_image():
    return Image.open("icon.png")


def toggle_gui(icon, item):
    if root and root.winfo_exists():
        state = root.state()
        if state == 'withdrawn':
            root.after(0, lambda: (
                position_at_bottom_right(root),
                root.deiconify(),
                root.lift(),
                root.focus_force()
            ))
        else:
            root.after(0, root.withdraw)
    else:
        root.after(0, create_mixer_window)


def quit_app_from_tray(icon, item):
    icon.stop()
    if root and root.winfo_exists():
        root.after(0, root.destroy)


def run_tray_icon():
    global _tray_icon_started
    if _tray_icon_started:
        return
    try:
        image = create_tray_image()
        menu = (
            pystray.MenuItem('Toggle Volume Mixer', toggle_gui, default=True),
            pystray.MenuItem('Quit', quit_app_from_tray),
        )
        tray_icon = pystray.Icon("volume_mixer", image, "Volume Mixer", menu)
        threading.Thread(target=tray_icon.run, daemon=True).start()
        _tray_icon_started = True
    except Exception as e:
        print(f"Tray icon error: {e}")


# --------------------- Main GUI ---------------------
def create_mixer_window():
    global root, mixer_frame, device_tab_frame
    root = tk.Tk()
    root.title("Volume Mixer")
    root.minsize(0, 0)
    root.wm_attributes("-topmost", 1)
    root.withdraw()
    root.overrideredirect(True)

    notebook = ttk.Notebook(root)
    notebook.pack(fill=tk.BOTH, expand=True)

    mixer_frame = ttk.Frame(notebook)
    notebook.add(mixer_frame, text="App Mixer")

    device_tab_frame = ttk.Frame(notebook)
    notebook.add(device_tab_frame, text="Device Volumes")

    notebook.bind("<<NotebookTabChanged>>", on_tab_changed)

    load_ignored_devices()
    load_ignored_apps()
    refresh_app_list()
    build_device_tab()
    root.after(POLL_INTERVAL_MS, poll_new_apps)
    root.after(200, resize_and_position)

    root.protocol("WM_DELETE_WINDOW", minimize_to_tray)

    def on_unmap(event):
        global _hiding_to_tray
        if not _hiding_to_tray and root and root.winfo_exists() and root.state() == 'iconic':
            _hiding_to_tray = True
            root.withdraw()
            _hiding_to_tray = False
    root.bind("<Unmap>", on_unmap)

    root.after(100, lambda: position_at_bottom_right(root))
    root.mainloop()


_hiding_to_tray = False
_tray_icon_started = False

def minimize_to_tray():
    global _hiding_to_tray
    _hiding_to_tray = True
    if root and root.winfo_exists():
        root.withdraw()
    _hiding_to_tray = False


# --------------------- Entry point ---------------------
if __name__ == "__main__":
    run_tray_icon()
    create_mixer_window()