# Volume Mixer

A compact Windows volume mixer with per-application volume control, device routing, system tray integration, a Sound Machine for ambient audio playback, Windows Night Light control, and real-time audio duplication to multiple output devices.

## Features

- Per-application volume sliders with exponential mapping (exponent 2.0) for finer low-volume control.
- Assign each application to a different audio output device via a dropdown menu.
- Master volume sliders for each render device, powered by NirSoft SoundVolumeView.
- Sound Machine tab: play ambient audio files (white noise, pink noise, brown noise, and custom clips) from the `Sounds/` folder with seamless crossfaded looping for hours-long playback.
- System tray icon (loaded from `icon.png`) to show or hide the mixer window.
- Automatic detection of new audio sessions every 2 seconds.
- Configurable ignored device list (`ignored_devices.txt`) to hide unwanted audio endpoints.
- Configurable ignored application list (`ignored_apps.txt`) to exclude specific programs from the mixer.
- Application icons are extracted from each executable and displayed next to the volume slider.
- Lightweight, frameless, always-on-top window that positions itself at the bottom-right corner of the work area (avoids the taskbar).
- Debounced device volume updates for smooth dragging.
- Windows Night Light control: toggle on/off and adjust color temperature strength.
- Audio duplication: route an application's audio to multiple output devices simultaneously with per-device volume, channel routing (left, right, or both), and mute.
- Sound Machine duplication: Sound Machine audio can be duplicated to multiple output devices internally without requiring a virtual audio cable.

## Download and Setup

There are two ways to run Volume Mixer.

### Option 1: Compiled executable (no Python required)

Download the `VolumeMixer.dist` directory. Inside it you will find `VolumeMixer.exe` along with all required dependencies bundled together. Launch `VolumeMixer.exe` directly -- no additional setup needed.

The executable is compiled from `VolumeMixer.py` using [Nuitka](https://nuitka.net/) with the following command:

```
py -m nuitka --standalone --product-name="Volume Mixer" --copyright="Copyright 2026 Homestead Harvest" --file-description="Volume Mixer" --windows-icon-from-ico=icon.ico --file-version="1.0.0.0" --product-version="1.0.0.0" --include-data-file=icon.png=icon.png --enable-plugin=tk-inter --include-data-file=ignored_devices.txt=ignored_devices.txt --include-data-file=ignored_apps.txt=ignored_apps.txt --include-data-file=SoundVolumeView.exe=SoundVolumeView.exe --include-data-dir=Sounds=Sounds --include-package=pystray --include-package=pycaw --include-package=comtypes --windows-console-mode=disable --follow-imports --assume-yes-for-downloads VolumeMixer.py
```

### Option 2: Run from source (Python required)

1. Clone the repository or download all source files.
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

The window appears at the bottom-right corner of the primary monitor, above the taskbar. The interface has four tabs: **App Mixer**, **Device Volumes**, **Sound Machine**, and **Misc**.

### App Mixer tab

- Each running audio application gets a slider and a device dropdown.
- An application icon (extracted from the executable) is shown next to each entry.
- Move the slider to adjust the application volume. The scale is exponential, giving more resolution at lower volumes.
- Use the dropdown to route the application to a specific output device. Changes take effect immediately.
- System processes (`SystemSounds`, `svchost.exe`, `taskhostw.exe`) and entries listed in `ignored_apps.txt` are hidden from the list.
- Each row has a gear button that opens the Audio Routing popup for duplication.

### Device Volumes tab

- Lists every render device (excluding those in `ignored_devices.txt`).
- Slide to change the master volume of a device. Updates are sent on release or continuously while dragging, with a 100-millisecond debounce.
- The "Default Windows Device" entry represents the system default output device.

### Sound Machine tab

- Plays ambient audio files from the `Sounds/` folder with seamless looping.
- Built-in sounds include white noise, pink noise, brown noise, and a speech blocker.
- Audio is crossfaded at loop boundaries to eliminate clicks and pops. Each loop is repeated and crossfaded into the next, creating a buffer long enough for approximately one hour of uninterrupted playback.
- The Play/Stop button toggles playback. Selecting a different file while playing stops the current playback automatically.
- The Sound Machine uses pygame for audio output. The mixer is initialized only when playback starts, so no audio session appears while idle.
- Sound Machine audio can be duplicated to multiple output devices using the gear button, just like any other application.

### Misc tab (Night Light)

The Misc tab provides controls for the Windows Night Light feature.

- **Enable Night Light** checkbox turns Night Light on or off.
- **Strength slider** adjusts the color temperature from 0% (coolest, 5000K) to 100% (warmest, 1000K).
- Current state is read from the system when the tab opens.
- Changes take effect immediately.

### Tray icon

- Left-click the tray icon (or right-click and select **Toggle Volume Mixer**) to show or hide the window.
- Right-click the tray icon and choose **Quit** to exit the application completely.

The mixer continuously polls for new audio sessions. Newly launched applications appear automatically within 2 seconds.

### Audio Duplication

Each application row in the App Mixer tab has a gear button that opens a popup window for audio duplication. This feature lets you send an application's audio to multiple output devices at the same time.

Requirements for standard application duplication:
- [VB-Audio Virtual Cable](https://vb-audio.com/Cable/) must be installed (free). The virtual cable acts as an intermediate audio endpoint.
- [Virtual Audio Cable](https://vac.muzychenko.net/en/download.htm) can also be used (not free).

The Sound Machine does not require a virtual cable for duplication. Its audio is duplicated internally using the pygame audio buffer, with no loopback or cable needed.

How it works:
1. Click the gear button on any application row to open the Audio Routing popup.
2. Select the output devices you want to send audio to.
3. For each device, choose a channel mode: **both** (stereo), **left** (left channel only), or **right** (right channel only).
4. Adjust the per-device volume slider for fine balance control.
5. Check **Enable Duplication** to start.

When duplication is active:
- Standard applications are routed to the virtual cable. Audio is captured from the virtual cable and replayed to each selected output device in real time.
- Sound Machine audio is duplicated internally from the stored audio buffer without using a virtual cable.
- The original app row is replaced with per-device volume rows in the App Mixer tab, letting you adjust each output independently.
- Select All and Clear All buttons let you quickly enable or disable all output devices.
- Uncheck **Enable Duplication** to stop. The application is restored to its original output device.

## Configuration

### `ignored_devices.txt`

List the exact friendly names of devices you want to hide, one per line. For example:

```
Speakers (Realtek High Definition Audio)
CABLE Input (VB-Audio Virtual Cable)
```

### `ignored_apps.txt`

List the application names (without the `.exe` extension) you want to hide, one per line. For example:

```
chrome
discord
spotify
```

## How It Works

- **App volume**, **device volumes**, and **per-app device routing** are all performed by calling SoundVolumeView with `/SetVolume`, `/SetAppDefault`, and JSON export command-line switches.
- **App routing** detects the current device assignment for each application from the SoundVolumeView JSON output and pre-selects the matching entry in the dropdown. The resolution logic handles apps with entries on multiple devices and correctly identifies apps reverted to the system default.
- **Application icons** are extracted at runtime using the Windows shell API (`SHGetFileInfoW`) and rendered alongside each app row.
- The tray icon is loaded from `icon.png` via [Pillow](https://github.com/python-pillow/Pillow) and runs in a background thread with [pystray](https://github.com/moses-palmer/pystray).
- The window uses `tkinter` with `overrideredirect(True)` for a frameless appearance and positions itself via `ctypes` calls to `SystemParametersInfoW`, avoiding the taskbar.
- **Sound Machine** uses `pygame` for audio playback. Audio files are loaded as numpy arrays, repeated with equal-power cosine/sine crossfades at each junction to eliminate loop boundary clicks, then played as a single long buffer. The pygame mixer is initialized only during playback and shut down when stopped so the application does not create an audio session while idle.
- **Night Light** control reads and writes Windows CloudStore registry keys directly via `nightlight_control.py`, manipulating the Blue Light Reduction state and color temperature settings without external dependencies. Binary blobs are constructed with LEB128 varint and ZigZag encoding to match the exact format written by Windows Settings.
- **Audio duplication** for standard applications works by routing the target application to a VB-Audio Virtual Cable, then using `sounddevice` (WASAPI loopback) to capture and replay the audio stream to multiple output devices simultaneously with per-device gain and channel routing. The Sound Machine uses internal buffer duplication instead, playing directly from the stored audio array to additional output streams without any virtual cable.

## Tools and Utilities

The repository includes two helper scripts in the `Tools/` directory:

- **`mynoise_capture.py`** -- Uses Playwright to capture live audio from the mynoise.net noise generator and save it as an MP3 file. Useful for creating custom Sound Machine audio clips.
- **`nightlight_poll.py`** -- Polls the Night Light CloudStore registry keys every 2 seconds and prints the raw REG_BINARY hex values. Useful for debugging changes to Night Light state.

The `Fixes/` directory contains backup and repair scripts for the Night Light feature, including registry backups in `NightLightBackups/`.

## License

This project is released under [The Unlicense](LICENSE). It is free and unencumbered software released into the public domain.

## Known Limitations

- Some operations (especially routing applications to specific devices) may require **administrator privileges**. If routing fails, try running the script as an administrator.
- Closing the window sends it to the system tray instead of quitting. Use the **Quit** tray menu option to fully exit.
- Audio duplication for standard applications **requires** a virtual audio driver [VB-Audio Virtual Cable](https://vb-audio.com/Cable/) / [Virtual Audio Cable](https://vac.muzychenko.net/en/download.htm) to be installed. Without it, the duplication feature will not function for non-Sound Machine apps.
