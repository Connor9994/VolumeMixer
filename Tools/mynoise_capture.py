"""
mynoise_capture.py — Pull live audio from the mynoise.net "Brownish White" noise
generator and save it as an MP3 file.

Uses Playwright to open the page, taps into the Web Audio API graph, records
via MediaRecorder (WebM/Opus), then converts to MP3 with ffmpeg.

Requires:
  pip install playwright
  python -m playwright install chromium
  ffmpeg              (must be on PATH — used for WebM → MP3 conversion)

Usage:
  # Record 30 seconds (default) to Sounds/brownish_noise.mp3
  python mynoise_capture.py

  # Record 2 minutes
  python mynoise_capture.py --duration 120

  # Custom output path
  python mynoise_capture.py -o my_noise.mp3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# ── Configuration ───────────────────────────────────────────────────────────
# Change this URL to record from a different mynoise generator.
MYNOISE_URL = (
    "https://mynoise.net/NoiseMachines/whiteNoiseGenerator.php"
)

# The exact text of the preset button to click on the mynoise page.
# Common values: "White", "Pink", "Brown", "Speech Blocker", "Grey", "Blue", "Violet"
PRESET_NAME = "Speech Blocker"

# ── JavaScript injected into the page to capture audio ─────────────────────
# Taps the existing Web Audio graph *after* masterGain, routes it to a
# MediaStreamDestination, and pushes chunks back to Python via the
# `push_audio_chunk` callback exposed by Playwright.
_INJECT_CAPTURE_JS = """
const captureDest = context.createMediaStreamDestination();
masterGain.connect(captureDest);

const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
    ? 'audio/webm;codecs=opus'
    : MediaRecorder.isTypeSupported('audio/webm')
        ? 'audio/webm'
        : '';

window.__recorder = new MediaRecorder(captureDest.stream, {
    mimeType: mimeType,
    audioBitsPerSecond: 128000,
});

window.__recorder.ondataavailable = async (event) => {
    if (event.data && event.data.size > 0) {
        const buffer = await event.data.arrayBuffer();
        push_audio_chunk(new Uint8Array(buffer));
    }
};

// Record in 1-second slices for low-latency streaming
window.__recorder.start(1000);
window.__captureActive = true;
"""


async def record_mynoise(
    output_path: str,
    duration: float = 30,
    gain: float = 0.5,
    headless: bool = True,
    preset_name: str = PRESET_NAME,
) -> None:
    """Open the mynoise page, capture audio, and save as MP3.

    Parameters
    ----------
    output_path : str
        Final MP3 file path.
    duration : float
        Recording duration in seconds (default 30).
    gain : float
        Master gain (0.0–1.0).
    headless : bool
        Whether to hide the browser window.
    preset_name : str
        The exact text of the preset button to click (e.g. "White", "Pink").
    """
    from playwright.async_api import async_playwright

    chunk_queue: asyncio.Queue[bytes] = asyncio.Queue()
    stop_event = asyncio.Event()

    # We record to a temporary WebM file first, then convert to MP3
    tmp_webm = tempfile.NamedTemporaryFile(suffix=".webm", delete=False)
    tmp_webm_path = tmp_webm.name
    tmp_webm.close()  # we'll reopen it for writing

    # ── callback the browser JS calls ──────────────────────────────────
    async def push_audio_chunk(data) -> None:
        await chunk_queue.put(bytes(data))

    # ── background writer ──────────────────────────────────────────────
    async def writer() -> None:
        with open(tmp_webm_path, "wb") as f:
            while not stop_event.is_set() or not chunk_queue.empty():
                try:
                    chunk = await asyncio.wait_for(
                        chunk_queue.get(), timeout=1.0
                    )
                    f.write(chunk)
                except asyncio.TimeoutError:
                    continue

    # ── main browser logic ─────────────────────────────────────────────
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=headless,
            args=[
                "--autoplay-policy=no-user-gesture-required",
                "--no-sandbox",
            ],
        )
        context = await browser.new_context(bypass_csp=True)
        page = await context.new_page()

        await page.expose_function("push_audio_chunk", push_audio_chunk)

        print("  Opening mynoise.net …", file=sys.stderr)
        await page.goto(
            MYNOISE_URL,
            wait_until="domcontentloaded",
            timeout=60000,
        )

        print("  Waiting for audio engine …", file=sys.stderr)
        await page.wait_for_function(
            "typeof context !== 'undefined' "
            "&& typeof masterGain !== 'undefined' "
            "&& context.state === 'running'",
            timeout=30000,
        )

        # Let audio buffers finish loading and reach full volume
        await asyncio.sleep(5)

        # Click the selected preset to switch noise colour
        print(f"  Switching to {preset_name} preset …", file=sys.stderr)
        preset_btn = page.locator(f'span.actionlink:text-is("{preset_name}")')
        if await preset_btn.count() > 0:
            await preset_btn.click()
            await asyncio.sleep(0.5)  # let the change take effect
        else:
            print(f"  ⚠  Could not find '{preset_name}' preset button.", file=sys.stderr)

        # Apply user-specified master gain
        await page.evaluate(f"masterGain.gain.value = {gain};")

        print("  Injecting audio capture …", file=sys.stderr)
        await page.evaluate(_INJECT_CAPTURE_JS)

        active = await page.evaluate("window.__captureActive === true")
        if not active:
            print("  ⚠  Capture may not have started.", file=sys.stderr)

        # ── start writer and wait ──────────────────────────────────────
        writer_task = asyncio.create_task(writer())

        print(f"  Recording {duration}s …", file=sys.stderr)

        try:
            await asyncio.sleep(duration)
        except asyncio.CancelledError:
            pass
        finally:
            await page.evaluate("window.__recorder?.stop?.()")
            await asyncio.sleep(2)
            stop_event.set()
            await writer_task
            await browser.close()

    # ── convert WebM → MP3 via ffmpeg ──────────────────────────────────
    print("  Converting to MP3 …", file=sys.stderr)
    _webm_to_mp3(tmp_webm_path, output_path)

    # Clean up temp file
    try:
        os.unlink(tmp_webm_path)
    except OSError:
        pass

    print(f"  Done — saved to {output_path}", file=sys.stderr)


def _webm_to_mp3(webm_path: str, mp3_path: str) -> None:
    """Run ffmpeg to convert a WebM/Opus file to uniformly-normalized MP3.

    Uses a two-pass EBU R128 loudness normalisation (``loudnorm``) with
    minimum loudness range (LRA=1), making every part of the clip the same
    perceived volume — no fade-in/out, no quiet sections.
    """

    def _ffmpeg(args: list[str]) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                ["ffmpeg", "-y", "-i", webm_path] + args,
                check=True,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            print(
                "  ✗ ffmpeg not found. Install ffmpeg or place it on your"
                " PATH.\n"
                f"  Raw WebM saved at: {webm_path}",
                file=sys.stderr,
            )
            raise
        except subprocess.CalledProcessError as exc:
            print(
                "  ✗ ffmpeg failed (exit code %d).\n"
                "    ffmpeg stderr:\n%s" % (exc.returncode, exc.stderr or "(none)"),
                file=sys.stderr,
            )
            raise

    # ── Pass 1: measure integrated loudness ────────────────────────────
    print("  Measuring loudness …", file=sys.stderr)
    try:
        result = _ffmpeg([
            "-af",
            "loudnorm=I=-16:print_format=json",
            "-f", "null",
            "NUL",
        ])
    except subprocess.CalledProcessError:
        # If two-pass loudnorm fails, fall back to simple loudnorm
        print("  Fallback: single-pass loudnorm …", file=sys.stderr)
        _ffmpeg([
            "-vn",
            "-af", "loudnorm=I=-16:LRA=1:TP=-1.5",
            "-acodec", "libmp3lame",
            "-ab", "192k",
            "-ar", "48000",
            "-ac", "2",
            "-loglevel", "error",
            mp3_path,
        ])
        return

    # Parse measured values from stderr
    lines = result.stderr.strip().splitlines()
    measured: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if ":" in line:
            k, v = line.split(":", 1)
            measured[k.strip().strip('"')] = v.strip().strip('",')

    # ── Pass 2: apply normalisation with measured params ───────────────
    print("  Applying uniform loudness normalisation …", file=sys.stderr)
    loudnorm_args = (
        "loudnorm=I=-16:LRA=1:TP=-1.5"
        f":measured_I={measured.get('input_i', '-16')}"
        f":measured_LRA={measured.get('input_lra', '0')}"
        f":measured_TP={measured.get('input_tp', '-1.5')}"
        f":measured_thresh={measured.get('input_thresh', '-31')}"
        f":offset={measured.get('target_offset', '0')}"
    )

    try:
        _ffmpeg([
            "-vn",
            "-af", loudnorm_args,
            "-acodec", "libmp3lame",
            "-ab", "192k",
            "-ar", "48000",
            "-ac", "2",
            "-loglevel", "error",
            mp3_path,
        ])
    except subprocess.CalledProcessError:
        # If two-pass loudnorm crashes, fall back to simple single-pass
        print(
            "  Fallback: two-pass loudnorm failed, retrying with"
            " single-pass …",
            file=sys.stderr,
        )
        _ffmpeg([
            "-vn",
            "-af", "loudnorm=I=-16:LRA=1:TP=-1.5",
            "-acodec", "libmp3lame",
            "-ab", "192k",
            "-ar", "48000",
            "-ac", "2",
            "-loglevel", "error",
            mp3_path,
        ])


# ── CLI entry point ────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Capture audio from mynoise.net 'Brownish White' noise generator"
            " and save as MP3."
        ),
    )
    parser.add_argument(
        "-o",
        "--output",
        default="Sounds/brownish_noise.mp3",
        help="Output MP3 file path (default: Sounds/brownish_noise.mp3).",
    )
    parser.add_argument(
        "-d",
        "--duration",
        type=float,
        default=30,
        help="Recording duration in seconds (default: 30).",
    )
    parser.add_argument(
        "--gain",
        type=float,
        default=0.5,
        help="Master gain (0.0–1.0).  Default 0.5.",
    )
    parser.add_argument(
        "--preset",
        default=PRESET_NAME,
        help=(
            f"Preset button text to click on the mynoise page"
            f" (default: {PRESET_NAME})."
        ),
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=True,
        help="Run browser headlessly (default).",
    )
    parser.add_argument(
        "--visible",
        action="store_false",
        dest="headless",
        help="Show the browser window.",
    )
    args = parser.parse_args()

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out = str(out)

    try:
        asyncio.run(
            record_mynoise(
                output_path=out,
                duration=args.duration,
                gain=args.gain,
                headless=args.headless,
                preset_name=args.preset,
            )
        )
    except KeyboardInterrupt:
        print("\n  Interrupted.", file=sys.stderr)
        sys.exit(0)
    except subprocess.CalledProcessError:
        sys.exit(1)
    except FileNotFoundError:
        sys.exit(1)


if __name__ == "__main__":
    main()
