import tkinter as tk
from tkinter import ttk
import threading
import subprocess
import sys
import json
import tempfile
import os
import ctypes
from ctypes import wintypes
import pystray
from PIL import Image, ImageDraw


# --- Configuration ---
EXPONENT = 2.0
POLL_INTERVAL_MS = 2000
IGNORED_DEVICES_FILE = "ignored_devices.txt" 
IGNORED_APPS = "ignored_apps.txt"

# --- Global Variables ---
root = None
mixer_frame = None          # frame holding the app list (inside tab 1)
device_tab_frame = None     # frame holding the device list (tab 2)
app_widgets = {}
sound_volume_view = "SoundVolumeView.exe"
device_list = []            # list of (display_name, device_id)
device_name_to_id = {}
device_sliders = {}
ignored_device_set = set()
ignored_app_set = set()
app_device_name = {}        # app_name -> current device display name
app_explicit_device = {}
app_exe_name = {}           # app_name (SVV display name) -> actual executable filename (e.g. 'chrome.exe')
protected_apps = set()      # apps currently switching device, don't remove from GUI


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
    global app_exe_name
    app_exe_name = {}
    for item in data:
        if item.get("Type") == "Application":
            name = item.get("Name", "")
            proc_path = item.get("Process Path", "")
            if name and proc_path:
                app_exe_name[name] = os.path.basename(proc_path)

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

    label = ttk.Label(frame, text=clean_name_app.capitalize(), width=max_name_len, anchor='w')
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

    app_widgets[app] = {
        'frame': frame,
        'slider': slider,
        'dropdown': device_dropdown
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

        # Refresh executable name mapping
        for item in data:
            if item.get("Type") == "Application":
                name = item.get("Name", "")
                proc_path = item.get("Process Path", "")
                if name and proc_path:
                    app_exe_name[name] = os.path.basename(proc_path)

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
        removed_apps = (existing_apps - current_apps) - protected_apps
        for app in removed_apps:
            if app in app_widgets:
                slider = app_widgets[app].get('slider')
                if slider and hasattr(slider, '_debounce_after_id') and slider._debounce_after_id:
                    slider.after_cancel(slider._debounce_after_id)
                app_widgets[app]['frame'].destroy()
                del app_widgets[app]
                if app in app_device_name:
                    del app_device_name[app]
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
    device_sliders.clear()

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

        device_sliders[name] = slider

    resize_and_position()


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