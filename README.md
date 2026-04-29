# Volume Mixer

A compact Windows volume mixer with per-application volume control, device routing, system tray integration, Windows Night Light control, and real-time audio duplication to multiple output devices.

<img width="441" height="162" alt="image" src="https://github.com/user-attachments/assets/b26231d9-b341-4f13-985e-bff59d18a71f" />

## Features

- Per-application volume sliders with exponential mapping (exponent 2.0) for finer low-volume control
- Assign each application to a different audio output device via a dropdown menu
- Master volume sliders for each render device, powered by NirSoft SoundVolumeView
- System tray icon (loaded from `icon.png`) to show or hide the mixer window
- Automatic detection of new audio sessions every 500 milliseconds
- Configurable ignored device list (`ignored_devices.txt`) to hide unwanted audio endpoints
- Configurable ignored application list (`ignored_apps.txt`) to exclude specific programs from the mixer
- Lightweight, frameless, always-on-top window that positions itself at the bottom-right corner of the work area (avoids the taskbar)
- Debounced device volume updates for smooth dragging
- Windows Night Light control: toggle on/off and adjust color temperature strength
- Audio duplication: route an application's audio to multiple output devices simultaneously with per-device volume, channel routing (left, right, or both), and mute

## Download and Setup

There are two ways to run Volume Mixer.

### Option 1: Compiled executable (no Python required)

Download the `VolumeMixer.dist` directory. Inside it you will find `VolumeMixer.exe` along with all required dependencies bundled together. Launch `VolumeMixer.exe` directly -- no additional setup needed.

The executable is compiled from `VolumeMixer.py` using [Nuitka](https://nuitka.net/) with the following command:

```
py -m nuitka --standalone --product-name="Volume Mixer" --copyright="Copyright 2026 Homestead Harvest" --file-description="Volume Mixer" --windows-icon-from-ico=icon.ico --file-version="1.0.0.0" --product-version="1.0.0.0" --include-data-file=icon.png=icon.png --enable-plugin=tk-inter --include-data-file=ignored_devices.txt=ignored_devices.txt --include-data-file=ignored_apps.txt=ignored_apps.txt --include-data-file=SoundVolumeView.exe=SoundVolumeView.exe --include-package=pystray --include-package=pycaw --include-package=comtypes --windows-console-mode=disable --follow-imports --assume-yes-for-downloads VolumeMixer.py
```

### Option 2: Run from source (Python required)

1. Clone the repository or download `VolumeMixer.py`.
2. Install the required Python packages:

   ```
   pip install -r requirements.txt
   ```

3. Place `SoundVolumeView.exe` and `icon.png` next to the script (or ensure `SoundVolumeView.exe` is on the `PATH`).

## Usage

Launch the mixer by running the script:

```
python VolumeMixer.py
```

The window appears at the bottom-right corner of the primary monitor, above the taskbar. If the window does not show, click the system tray icon and choose **Toggle Volume Mixer**.

### App Mixer tab

- Each running audio application gets a slider and a device dropdown.
- Move the slider to adjust the application volume. The scale is exponential, giving more resolution at lower volumes.
- Use the dropdown to route the application to a specific output device. Changes take effect immediately.
- System processes (`SystemSounds`, `svchost.exe`) and entries listed in `ignored_apps.txt` are hidden from the list.

<img width="440" height="203" alt="image" src="https://github.com/user-attachments/assets/3726fd64-b9c2-450d-b6f0-b265bbd12ea1" />

### Device Volumes tab

- Lists every render device (excluding those in `ignored_devices.txt`).
- Slide to change the master volume of a device. Updates are sent on release or continuously while dragging, with a 100-millisecond debounce.
- The "Default Windows Device" entry represents the system default output device.

<img width="438" height="165" alt="image" src="https://github.com/user-attachments/assets/356b102c-1339-4299-ba31-112d9bef60cf" />

### Tray icon

- Left-click the tray icon (or right-click and select **Toggle Volume Mixer**) to show or hide the window.
- Right-click the tray icon and choose **Quit** to exit the application completely.

The mixer continuously polls for new audio sessions. Newly launched applications appear automatically after at most 500 milliseconds.

<img width="320" height="81" alt="image" src="https://github.com/user-attachments/assets/66d99567-1767-4a03-9d1d-ed61a09453d5" />

### Misc tab (Night Light)

The Misc tab provides controls for the Windows Night Light feature.

- **Enable Night Light** checkbox turns Night Light on or off.
- **Strength slider** adjusts the color temperature from 0% (warmest, 1200K) to 100% (coolest, 6500K).
- Current state is read from the system when the tab opens.
- Changes take effect immediately.

![Screenshot of the Misc tab showing the Night Light section with an enable checkbox and strength slider.]

### Audio Duplication

Each application row in the App Mixer tab has a gear button that opens a popup window for audio duplication. This feature lets you send an application's audio to multiple output devices at the same time.

Requirements:
- [VB-Audio Virtual Cable](https://vb-audio.com/Cable/) must be installed (free). The virtual cable acts as an intermediate audio endpoint.

How it works:
1. Click the gear button on any application row to open the Audio Routing popup.
2. Select the output devices you want to send audio to.
3. For each device, choose a channel mode: **both** (stereo), **left** (left channel only), or **right** (right channel only).
4. Adjust the per-device volume slider for fine balance control.
5. Check **Enable Duplication** to start.

When duplication is active:
- The application is routed to the virtual cable.
- Audio is captured from the virtual cable and replayed to each selected output device in real time.
- The original app row is replaced with per-device volume rows in the App Mixer tab, letting you adjust each output independently.
- Select All and Clear All buttons let you quickly enable or disable all output devices.
- Uncheck **Enable Duplication** or close the popup to stop. The application is restored to its original output device.

![Screenshot of the Audio Routing popup showing the duplication toggle, per-device checkboxes, channel dropdowns, and volume sliders.]

![Screenshot of the App Mixer tab with duplication active, showing per-device volume rows for a duplicated application.]

## Configuration

### `ignored_devices.txt`

Create a plain text file named `ignored_devices.txt` in the same directory. List the exact friendly names of devices you want to hide, one per line. For example:

```
Speakers (Realtek High Definition Audio)
CABLE Input (VB-Audio Virtual Cable)
```

### `ignored_apps.txt`

Create a plain text file named `ignored_apps.txt` in the same directory. List the application names (without the `.exe` extension) you want to hide, one per line. For example:

```
chrome
discord
spotify
```

## How It Works

- **App volume** is controlled through the [pycaw](https://github.com/AndreMiras/pycaw) library, which wraps the Windows Core Audio API.
- **Device volumes** and **per-app device routing** are performed by calling SoundVolumeView with JSON export and command-line switches. A fallback routing attempt uses a different type parameter if the primary call fails.
- **App routing** detects the current device assignment for each application from the SoundVolumeView JSON output and pre-selects the matching entry in the dropdown.
- The tray icon is loaded from `icon.png` via [Pillow](https://github.com/python-pillow/Pillow) and runs in a background thread with [pystray](https://github.com/moses-palmer/pystray).
- The window uses `tkinter` with `overrideredirect(True)` for a frameless appearance and positions itself via `ctypes` calls to `SystemParametersInfoW`, avoiding the taskbar.
- **Night Light** control reads and writes Windows CloudStore registry keys directly via `nightlight_control.py`, manipulating the Blue Light Reduction state and color temperature settings without external dependencies.
- **Audio duplication** works by routing the target application to a VB-Audio Virtual Cable, then using `sounddevice` (WASAPI loopback) to capture and replay the audio stream to multiple output devices simultaneously with per-device gain and channel routing.

## Known Limitations

- Some operations (especially routing applications to specific devices) may require **administrator privileges**. If routing fails, try running the script as an administrator.
- The mixer identifies applications by their executable name. Applications that stop playing audio may still appear until the program is restarted.
- The exponential volume exponent (2.0) is fixed in the code. Change the `EXPONENT` variable at the top of the script to modify the curve.
- SoundVolumeView must be accessible; if it is missing or its output format changes, the script may not populate the device list.
- Closing the window sends it to the system tray instead of quitting. Use the **Quit** tray menu option to fully exit.
- Audio duplication requires [VB-Audio Virtual Cable](https://vb-audio.com/Cable/) to be installed. Without it, the duplication feature will not function.
