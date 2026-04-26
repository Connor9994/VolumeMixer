# Volume Mixer

A compact Windows volume mixer with per-application volume control, device routing, and system tray integration. It provides fast access to application volumes and output device assignments without opening the built-in Windows mixer.

## Features

- Per-application volume sliders with an exponential mapping (exponent 2.0) for finer low‑volume control
- Assign each application to a different audio output device via a dropdown menu
- Master volume sliders for each render device, powered by NirSoft SoundVolumeView
- System tray icon to hide or show the mixer window
- Automatic detection of new audio sessions every 500 ms
- Configurable ignored device list to hide unwanted entries
- Lightweight, frameless window that positions itself at the bottom‑right corner of the screen

## Requirements

- **Windows** (tested on Windows 10 and 11)
- **Python 3.6** or newer
- **NirSoft SoundVolumeView.exe** – [download](https://www.nirsoft.net/utils/sound_volume_view.html) and place it in the same directory or add it to your system `PATH`
- Python dependencies listed in `requirements.txt`

## Installation

1. Clone the repository or download the script (`volume_mixer.py`).
2. Install the required Python packages:
   ```bash
   pip install -r requirements.txt
   ```
3. Place `SoundVolumeView.exe` next to the script (or ensure it is on the `PATH`).

A `requirements.txt` file might contain:

```
pystray
pillow
pycaw
comtypes
```

(The script also uses `ctypes`, `tkinter`, `threading`, `subprocess`, `tempfile`, `json`, `os`, and `sys` – all are part of the standard library.)

## Usage

Launch the mixer by running the script:

```bash
python volume_mixer.py
```

The window will appear at the bottom‑right corner of the primary monitor. If the window does not show, click the system tray icon and choose **Toggle Volume Mixer**.

### App Mixer tab

- Each running audio application gets a slider and a device dropdown.
- Move the slider to adjust the application volume. The scale is exponential, giving more resolution at lower volumes.
- Use the dropdown to route the application to a specific output device. Changes take effect immediately.

### Device Volumes tab

- Lists every render device (excluding those in `ignored_devices.txt`).
- Slide to change the master volume of a device. Updates are sent on release or continuously while dragging, with a short debounce.
- The "Default Windows Device" entry represents the system default output device.

### Tray icon

- **Left‑click** the tray icon (or right‑click and select **Toggle Volume Mixer**) to show or hide the window.
- Right‑click the tray icon and choose **Quit** to exit the application completely.

The mixer continuously polls for new audio sessions. Newly launched applications appear automatically after at most 500 milliseconds.

## Configuration

### `ignored_devices.txt`

Create a plain text file named `ignored_devices.txt` in the same directory. List the exact friendly names of devices you want to hide, one per line. For example:

```
Speakers (Realtek High Definition Audio)
CABLE Input (VB-Audio Virtual Cable)
```

Blank lines are ignored. After modifying the file, restart the program.

## How It Works

- **App volume** is controlled through the [pycaw](https://github.com/AndreMiras/pycaw) library, which wraps the Windows Core Audio API.
- **Device volumes** and **per‑app device routing** are performed by calling SoundVolumeView with JSON export and command‑line switches.
- The tray icon is built with [pystray](https://github.com/moses-palmer/pystray) and runs in a background thread.
- The window uses `tkinter` and positions itself using `ctypes` calls to `SystemParametersInfoW`, avoiding the taskbar.

## Known Limitations

- Some operations (especially routing applications to specific devices) may require **administrator privileges**. If routing fails, try running the script as an administrator.
- The mixer identifies applications by their executable name. Applications that stop playing audio may still appear until the program is restarted.
- The exponential volume exponent (2.0) is fixed in the code. Change the `EXPONENT` variable at the top of the script to modify the curve.
- SoundVolumeView must be accessible; if it is missing or its output format changes, the script may not populate the device list.

## License

MIT License. See `LICENSE` file for details.

---

*Uses [SoundVolumeView](https://www.nirsoft.net/utils/soundvolumeview.html) by Nir Sofer, [pycaw](https://github.com/AndreMiras/pycaw), [pystray](https://github.com/moses-palmer/pystray), and [Pillow](https://python-pillow.org/).*
