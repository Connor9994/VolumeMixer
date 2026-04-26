import tkinter as tk
from tkinter import ttk
import threading
import subprocess
import sys
import json
import tempfile
import os
import pystray
from PIL import Image, ImageDraw
from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume
from comtypes import CLSCTX_ALL

# --- Configuration ---
EXPONENT = 2.0
POLL_INTERVAL_MS = 500   # check for new audio apps every 500 ms

# --- Global Variables ---
root = None          # main Tk window
mixer_window = None  # scrollable frame that holds the app list
app_widgets = {}
sound_volume_view = "SoundVolumeView.exe"   # make sure it's in PATH or set full path
device_list = []                            # list of (display_name, device_id)
device_name_to_id = {}                      # mapping for quick lookup


# --------------------- Device helpers (SoundVolumeView) ---------------------
def refresh_device_list():
    """Use SoundVolumeView /sjson to get a fresh list of playback devices."""
    global device_list, device_name_to_id

    device_list = []
    device_name_to_id = {}

    try:
        # Create a temporary file for the JSON output
        with tempfile.NamedTemporaryFile(mode='w+', suffix='.json', delete=False) as tmp:
            tmp_path = tmp.name

        # Run SoundVolumeView and export to JSON
        subprocess.run([sound_volume_view, "/sjson", tmp_path],
                       capture_output=True, check=True)

        # Read and parse the JSON (UTF‑16 encoding)
        with open(tmp_path, 'r', encoding='utf-16') as f:
            data = json.load(f)

        # Remove the temporary file
        os.unlink(tmp_path)

        # Filter only playback devices (Render)
        for item in data:
            if (item.get("Type") == "Device" and
                "Render" in item.get("Command-Line Friendly ID", "")):
                name = item.get("Name", "Unknown Device")
                device_id = item.get("Command-Line Friendly ID")
                if name and device_id:
                    if name not in device_name_to_id:   # avoid duplicates
                        device_list.append((name, device_id))
                        device_name_to_id[name] = device_id

        # Add the special "DefaultRenderDevice" alias
        device_list.insert(0, ("Default Windows Device", "DefaultRenderDevice"))
        device_name_to_id["Default Windows Device"] = "DefaultRenderDevice"

    except Exception as e:
        print(f"Error refreshing device list: {e}")
        # Provide a fallback
        device_list = [("Default Windows Device", "DefaultRenderDevice")]
        device_name_to_id = {"Default Windows Device": "DefaultRenderDevice"}


def set_app_device(app_name, device_id):
    """
    Route the given application to a specific output device using
    SoundVolumeView's /SetAppDefault command.
    """
    if not device_id:
        return

    # Use 'all' to set console, multimedia and communications at once
    cmd = [sound_volume_view, "/SetAppDefault", device_id, "all", f"{app_name}"]
    try:
        subprocess.run(cmd, capture_output=True, check=True)
        print(f"Routed {app_name} to device ID {device_id}")
    except subprocess.CalledProcessError as e:
        print(f"Failed to route {app_name}: {e}")
        # Fallback: try with default type 0 (console) if 'all' fails
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


# --------------------- App list UI ---------------------
def refresh_app_list():
    if not mixer_window or not mixer_window.winfo_exists():
        return

    # Clear existing rows
    for widget in mixer_window.winfo_children():
        if isinstance(widget, ttk.Frame):
            widget.destroy()
    app_widgets.clear()

    # 1. Update the global device list (so we always have the latest)
    refresh_device_list()

    # 2. Fetch full data from SoundVolumeView (contains apps & their current device)
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

    # 3. Build a mapping: app_name -> friendly device name (key from device_name_to_id)
    app_current_device = {}
    for item in data:
        if item.get("Type") == "Application":
            proc_name = item.get("Name")
            default_dev = item.get("Device Name")
            if proc_name and default_dev:
                # SoundVolumeView may say "Default Render Device" → treat as Default Windows Device
                if default_dev == "Default Render Device":
                    app_current_device[proc_name] = "Default Windows Device"
                    continue

                # Direct match (friendly name equals the Device Name)
                if default_dev in device_name_to_id:
                    app_current_device[proc_name] = default_dev
                    continue

                # Otherwise, iterate over device IDs: the first segment of the ID
                # (e.g., "Pebble V3") must match the reported Device Name
                for friendly_name, device_id in device_name_to_id.items():
                    if device_id.startswith(default_dev + "\\"):
                        app_current_device[proc_name] = friendly_name
                        break

    # 4. Get the list of running audio apps from pycaw
    sessions = AudioUtilities.GetAllSessions()
    apps = []
    for session in sessions:
        if session.Process and session.Process.name() not in [None, 'SystemSounds', 'svchost.exe']:
            apps.append(session.Process.name())
    apps = sorted(list(set(apps)))

    # 5. Build the UI for each app
    device_names = [name for name, _ in device_list]

    for app in apps:
        add_app_row(app, sessions, device_names, app_current_device)


def add_app_row(app, sessions, device_names, app_current_device):
    """Add a single app row to the mixer window without disturbing existing rows."""
    if app in app_widgets:
        return

    clean_name_app = app[:-4] if app.lower().endswith('.exe') else app
    frame = ttk.Frame(mixer_window)
    frame.pack(fill=tk.X, padx=10, pady=5)

    # App label
    label = ttk.Label(frame, text=clean_name_app, width=30, anchor='w')
    label.pack(side=tk.LEFT, padx=(0, 10))

    # Volume slider
    slider = ttk.Scale(frame, from_=0, to=100, orient=tk.HORIZONTAL,
                       command=lambda val, a=app: set_app_volume(a, val))
    slider.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
    
    device_var = tk.StringVar()
    current_device = app_current_device.get(clean_name_app)
    if not current_device or current_device not in device_names:
        current_device = "Default Windows Device"

    device_dropdown = ttk.Combobox(frame, textvariable=device_var,
                                   values=device_names, state='readonly',
                                   width=35)
    device_dropdown.pack(side=tk.RIGHT, padx=(5, 0))

    root.after(10, lambda dd=device_dropdown, v=current_device: dd.set(v))

    def on_device_select(event, a=app, dv=device_var):
        selected_name = dv.get()
        if selected_name in device_name_to_id:
            set_app_device(a, device_name_to_id[selected_name])

    device_dropdown.bind('<<ComboboxSelected>>', on_device_select)

    # Set initial slider volume from the actual session
    for session in sessions:
        if session.Process and session.Process.name() == app:
            volume_interface = session._ctl.QueryInterface(ISimpleAudioVolume)
            current_vol = volume_interface.GetMasterVolume()
            slider_pos = (current_vol ** (1 / EXPONENT)) * 100
            slider.set(slider_pos)
            break

    app_widgets[app] = {'slider': slider, 'dropdown': device_dropdown}


def poll_new_apps():
    """Check for new audio sessions and add them without disturbing existing widgets."""
    if not mixer_window or not mixer_window.winfo_exists():
        return

    try:
        sessions = AudioUtilities.GetAllSessions()
        current_apps = set()
        for session in sessions:
            if session.Process and session.Process.name() not in [None, 'SystemSounds', 'svchost.exe']:
                current_apps.add(session.Process.name())

        # Find new apps that don't have widgets yet
        new_apps = current_apps - set(app_widgets.keys())

        if new_apps:
            device_names = [name for name, _ in device_list]

            # Get fresh SoundVolumeView data to determine default device for new apps
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
                    add_app_row(app, sessions, device_names, app_current_device)

            except Exception as e:
                print(f"Error fetching device data for new apps: {e}")
                # Still add the app row with default device
                for app in sorted(new_apps):
                    add_app_row(app, sessions, device_names, {})

    except Exception as e:
        print(f"Error in poll_new_apps: {e}")

    # Schedule the next poll
    if root and root.winfo_exists():
        root.after(POLL_INTERVAL_MS, poll_new_apps)


# --------------------- Tray icon ---------------------
def create_tray_image():
    """Create a speaker icon with a blue circle background for visibility."""
    image = Image.new('RGB', (64, 64), (0, 120, 215))  # Windows-blue background
    draw = ImageDraw.Draw(image)

    # Draw a slightly darker circle as background
    draw.ellipse([2, 2, 62, 62], fill=(0, 100, 190))

    # Speaker box (left rectangular body)
    draw.rectangle([14, 22, 26, 42], fill='white')
    # Speaker cone (triangle flaring to the right)
    draw.polygon([(26, 20), (26, 44), (40, 50), (40, 14)], fill='white')
    # Inner sound wave arc
    draw.arc([38, 18, 52, 34], -55, 55, fill='white', width=3)
    # Outer sound wave arc
    draw.arc([46, 10, 60, 42], -55, 55, fill='white', width=3)

    return image


def position_at_bottom_right(window):
    """Place the given window at the bottom-right corner of the primary monitor."""
    window.update_idletasks()
    screen_width = window.winfo_screenwidth()
    screen_height = window.winfo_screenheight()
    win_width = window.winfo_width()
    win_height = window.winfo_height()
    x = screen_width - win_width
    y = screen_height - win_height
    window.geometry(f"+{x}+{y}")


def show_gui_from_tray(icon, item):
    """Callback for left-click on tray icon — show GUI at bottom-right."""
    icon.stop()
    if root and root.winfo_exists():
        root.after(0, lambda: (
            position_at_bottom_right(root),
            root.deiconify(),
            root.lift(),
            root.focus_force()
        ))
    else:
        root.after(0, create_mixer_window)


def quit_app_from_tray(icon, item):
    """Callback for tray 'Quit' — cleanly exit the app."""
    icon.stop()
    if root and root.winfo_exists():
        root.after(0, root.destroy)
    sys.exit(0)


def run_tray_icon():
    try:
        image = create_tray_image()
        menu = (
            pystray.MenuItem('Show Volume Mixer', show_gui_from_tray, default=True),
            pystray.MenuItem('Quit', quit_app_from_tray),
        )
        tray_icon = pystray.Icon("volume_mixer", image, "Volume Mixer", menu)
        threading.Thread(target=tray_icon.run, daemon=True).start()
    except Exception as e:
        print(f"Tray icon error: {e}")


# --------------------- Main GUI ---------------------
def create_mixer_window():
    global root, mixer_window
    root = tk.Tk()
    root.title("Per‑App Audio Router & Volume Mixer")
    root.geometry("800x500")
    root.minsize(450, 350)

    root.withdraw()

    # --- Scrollable area for app rows ---
    main_frame = ttk.Frame(root)
    main_frame.pack(fill=tk.BOTH, expand=True)

    canvas = tk.Canvas(main_frame)
    scrollbar = ttk.Scrollbar(main_frame, orient=tk.VERTICAL, command=canvas.yview)
    scrollable_frame = ttk.Frame(canvas)
    scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    def on_mousewheel(event):
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
    canvas.bind_all("<MouseWheel>", on_mousewheel)

    mixer_window = scrollable_frame   # app rows go here

    # Initial population
    refresh_app_list()

    # Start periodic polling for new audio apps
    root.after(POLL_INTERVAL_MS, poll_new_apps)

    # Handle window close → minimise to tray
    root.protocol("WM_DELETE_WINDOW", minimize_to_tray)

    # Handle minimize button → hide to tray instead of taskbar
    def on_unmap(event):
        if not _hiding_to_tray and root and root.winfo_exists() and root.state() == 'iconic':
            root.after(50, minimize_to_tray)
    root.bind("<Unmap>", on_unmap)

    # Position at bottom-right of primary monitor
    root.after(100, lambda: position_at_bottom_right(root))

    root.mainloop()


_hiding_to_tray = False

def minimize_to_tray():
    """Hide main window and show tray icon."""
    global _hiding_to_tray
    _hiding_to_tray = True
    if root and root.winfo_exists():
        root.withdraw()
    run_tray_icon()
    _hiding_to_tray = False


# --------------------- Entry point ---------------------
if __name__ == "__main__":
    # Start the tray icon immediately so it's always visible in the system tray
    run_tray_icon()
    create_mixer_window()