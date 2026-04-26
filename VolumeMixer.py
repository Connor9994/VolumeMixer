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
from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume, IAudioEndpointVolume
from comtypes import CLSCTX_ALL

# --- Configuration ---
EXPONENT = 2.0
POLL_INTERVAL_MS = 500   # check for new audio apps every 500 ms
IGNORED_DEVICES_FILE = "ignored_devices.txt" 
IGNORED_APPS = "ignored_apps.txt"

# --- Global Variables ---
root = None
mixer_frame = None          # frame holding the app list (inside tab 1)
device_tab_frame = None     # frame holding the device list (tab 2)
app_widgets = {}
sound_volume_view = "SoundVolumeView.exe"
device_list = []            # list of (display_name, device_id)
device_name_to_id = {}      # mapping for quick lookup
device_sliders = {}         # slider widgets for device volumes
ignored_device_set = set()
ignored_app_set = set()     # set of app names (without .exe) to ignore


def load_ignored_devices():                    
    """Load ignored device names from IGNORED_DEVICES_FILE."""
    global ignored_device_set
    ignored_device_set = set()
    if os.path.exists(IGNORED_DEVICES_FILE):
        with open(IGNORED_DEVICES_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                name = line.strip()
                if name:
                    ignored_device_set.add(name)


def load_ignored_apps():
    """Load ignored app names (without .exe) from IGNORED_APPS."""
    global ignored_app_set
    ignored_app_set = set()
    if os.path.exists(IGNORED_APPS):
        with open(IGNORED_APPS, 'r', encoding='utf-8') as f:
            for line in f:
                name = line.strip()
                if name:
                    ignored_app_set.add(name)


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
    """Resize the window to fit its content and reposition at bottom-right."""
    if not root or not root.winfo_exists():
        return
    root.update_idletasks()
    req_width = root.winfo_reqwidth()
    req_height = root.winfo_reqheight()
    root.geometry(f"{req_width}x{req_height}")
    position_at_bottom_right(root)


# --------------------- Device helpers (SoundVolumeView) ---------------------
def refresh_device_data():
    """Read device list + volumes from SoundVolumeView JSON export.

    Returns dict {friendly_name: volume_percent (0-100)} for render devices.
    Also populates global device_list and device_name_to_id.
    """
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
    """Try to extract the volume percentage from a SoundVolumeView device item dict."""
    raw = item.get("Volume Percent")
    try:
        cleaned = ''.join(c for c in str(raw) if c in '0123456789.-')
        if cleaned:
            return float(cleaned)
    except (ValueError, TypeError):
        pass
    return None


def set_device_volume(device_name, volume_val):
    """
    Set master volume of a render device using SoundVolumeView.exe.
    volume_val : float in range 0–100 (from the slider)
    """
    try:
        subprocess.run(
            [sound_volume_view, "/SetVolume", device_name, str(volume_val)],
            capture_output=True,
            check=True
        )
    except subprocess.CalledProcessError as e:
        print(f"Failed to set volume for {device_name}: {e}")


def set_app_device(app_name, device_id):
    if not device_id:
        return
    cmd = [sound_volume_view, "/SetAppDefault", device_id, "all", f"{app_name}"]
    try:
        subprocess.run(cmd, capture_output=True, check=True)
        print(f"Routed {app_name} to device ID {device_id}")
    except subprocess.CalledProcessError as e:
        print(f"Failed to route {app_name}: {e}")
        try:
            subprocess.run([sound_volume_view, "/SetAppDefault", device_id, "0", f"{app_name}"],
                           capture_output=True, check=True)
            print(f"Routed {app_name} to device ID {device_id} (fallback type 0)")
        except Exception as ex:
            print(f"Fallback also failed: {ex}")


# --------------------- Volume control ---------------------
def exponential_volume(t):
    return t ** EXPONENT


def set_app_volume(app_name, slider_value):
    t = float(slider_value) / 100.0
    amplitude = exponential_volume(t)
    sessions = AudioUtilities.GetAllSessions()
    for session in sessions:
        if session.Process and session.Process.name() == app_name:
            volume = session._ctl.QueryInterface(ISimpleAudioVolume)
            volume.SetMasterVolume(amplitude, None)
            break


# --------------------- App list UI (Tab 1) ---------------------
def refresh_app_list():
    if not mixer_frame or not mixer_frame.winfo_exists():
        return

    for widget in mixer_frame.winfo_children():
        if isinstance(widget, ttk.Frame):
            widget.destroy()
    app_widgets.clear()

    refresh_device_data()
    load_ignored_apps()  # Reload ignored apps before building the list

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

    app_current_device = {}
    for item in data:
        if item.get("Type") == "Application":
            proc_name = item.get("Name")
            default_dev = item.get("Device Name")
            if proc_name and default_dev:
                if default_dev == "Default Render Device":
                    app_current_device[proc_name] = "Default Windows Device"
                    continue
                if default_dev in device_name_to_id:
                    app_current_device[proc_name] = default_dev
                    continue
                for friendly_name, device_id in device_name_to_id.items():
                    if device_id.startswith(default_dev + "\\"):
                        app_current_device[proc_name] = friendly_name
                        break

    sessions = AudioUtilities.GetAllSessions()
    apps = []
    for session in sessions:
        if session.Process and session.Process.name() not in [None, 'SystemSounds', 'svchost.exe']:
            apps.append(session.Process.name())
    apps = sorted(list(set(apps)))

    # Filter out ignored apps (check clean name without .exe)
    filtered_apps = []
    for app in apps:
        clean = app[:-4] if app.lower().endswith('.exe') else app
        if clean not in ignored_app_set:
            filtered_apps.append(app)

    if not filtered_apps:
        resize_and_position()
        return

    clean_names = [app[:-4] if app.lower().endswith('.exe') else app for app in filtered_apps]
    max_name_len = max((len(name) for name in clean_names), default=15)

    device_names = [name for name, _ in device_list]

    for app in filtered_apps:
        add_app_row(app, sessions, device_names, app_current_device, max_name_len)


def add_app_row(app, sessions, device_names, app_current_device, max_name_len=15):
    if app in app_widgets:
        return

    clean_name_app = app[:-4] if app.lower().endswith('.exe') else app
    frame = ttk.Frame(mixer_frame)
    frame.pack(fill=tk.X, padx=10, pady=5)

    label = ttk.Label(frame, text=clean_name_app, width=max_name_len, anchor='w')
    label.pack(side=tk.LEFT, padx=(0, 5))

    slider = ttk.Scale(frame, from_=0, to=100, orient=tk.HORIZONTAL,
                       command=lambda val, a=app: set_app_volume(a, val))
    slider.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))

    combo_width = max((len(name) for name in device_names), default=20) + 2
    device_var = tk.StringVar()
    current_device = app_current_device.get(clean_name_app)
    if not current_device or current_device not in device_names:
        current_device = "Default Windows Device"

    device_dropdown = ttk.Combobox(frame, textvariable=device_var,
                                   values=device_names, state='readonly',
                                   width=combo_width)
    device_dropdown.pack(side=tk.RIGHT, padx=(5, 0))

    root.after(10, lambda dd=device_dropdown, v=current_device: dd.set(v))

    def on_device_select(event, a=app, dv=device_var):
        selected_name = dv.get()
        if selected_name in device_name_to_id:
            set_app_device(a, device_name_to_id[selected_name])

    device_dropdown.bind('<<ComboboxSelected>>', on_device_select)

    for session in sessions:
        if session.Process and session.Process.name() == app:
            volume_interface = session._ctl.QueryInterface(ISimpleAudioVolume)
            current_vol = volume_interface.GetMasterVolume()
            slider_pos = (current_vol ** (1 / EXPONENT)) * 100
            slider.set(slider_pos)
            break

    app_widgets[app] = {'slider': slider, 'dropdown': device_dropdown}


def poll_new_apps():
    if not mixer_frame or not mixer_frame.winfo_exists():
        return

    try:
        sessions = AudioUtilities.GetAllSessions()
        current_apps = set()
        for session in sessions:
            if session.Process and session.Process.name() not in [None, 'SystemSounds', 'svchost.exe']:
                current_apps.add(session.Process.name())

        new_apps = current_apps - set(app_widgets.keys())
        if new_apps:
            # Filter out ignored apps
            new_filtered = set()
            for app in new_apps:
                clean = app[:-4] if app.lower().endswith('.exe') else app
                if clean not in ignored_app_set:
                    new_filtered.add(app)
            new_apps = new_filtered

            if not new_apps:
                return

            device_names = [name for name, _ in device_list]
            all_known = set(app_widgets.keys()) | new_apps
            clean_known = [a[:-4] if a.lower().endswith('.exe') else a for a in all_known]
            max_name_len = max((len(name) for name in clean_known), default=15)

            try:
                with tempfile.NamedTemporaryFile(mode='w+', suffix='.json', delete=False) as tmp:
                    tmp_path = tmp.name
                subprocess.run([sound_volume_view, "/sjson", tmp_path],
                               capture_output=True, check=True)
                with open(tmp_path, 'r', encoding='utf-16') as f:
                    data = json.load(f)
                os.unlink(tmp_path)

                app_current_device = {}
                for item in data:
                    if item.get("Type") == "Application":
                        proc_name = item.get("Name")
                        default_dev = item.get("Device Name")
                        if proc_name and default_dev and proc_name in new_apps:
                            if default_dev == "Default Render Device":
                                app_current_device[proc_name] = "Default Windows Device"
                                continue
                            if default_dev in device_name_to_id:
                                app_current_device[proc_name] = default_dev
                                continue
                            for friendly_name, device_id in device_name_to_id.items():
                                if device_id.startswith(default_dev + "\\"):
                                    app_current_device[proc_name] = friendly_name
                                    break

                for app in sorted(new_apps):
                    add_app_row(app, sessions, device_names, app_current_device, max_name_len)
                resize_and_position()
            except Exception as e:
                print(f"Error fetching device data for new apps: {e}")
                for app in sorted(new_apps):
                    add_app_row(app, sessions, device_names, {}, max_name_len)
                resize_and_position()
    except Exception as e:
        print(f"Error in poll_new_apps: {e}")

    if root and root.winfo_exists():
        root.after(POLL_INTERVAL_MS, poll_new_apps)


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
    """When a tab is selected, reposition the window to fit the content."""
    resize_and_position()


# --------------------- Tray icon ---------------------
def create_tray_image():
    # Use external icon file instead of generating one
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
    sys.exit(0)


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
    load_ignored_apps()       # load ignored apps at startup
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