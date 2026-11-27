# ChikChik!

**Find the moment. Trim it, crop it, export it.**

ChikChik! is a lightweight Windows video viewer with frame-by-frame scrubbing and quick export tools. Open a short .mp4 clip, ProRes 4444XQ render, a PNG sequence, or a feature-length MKV and start working directly in the viewer.

- **Pull a reference from a film.** Jump to the scene on the timeline, scrub frame by frame to find the cut, then trim and crop the clip and export it as an MP4, GIF, or PNG sequence.
- **Turn a render sequence into a preview.** Open any PNG in the sequence, set the frame rate, and export a smaller MP4 to share.

[Download ChikChik! for Windows](https://github.com/olegkulinich/chikchik/releases/latest)

https://github.com/user-attachments/assets/59ae083b-e37c-4e13-9830-47ca2c9894f6

## Find the frame you’re looking for

Open a feature-length MKV and jump to the scene on the timeline. Scroll over the timeline to zoom into that part of the film, then drag on the picture to scrub frame by frame.

## Trim, crop, and export

Use **I** and **O** to mark the range. Scroll over the picture to zoom in and press **C** to crop. Choose the export format, scale and frame rate, then hit Export. Use **Ctrl+C** when you just need the current frame.

## Keep a video reference in view 

Use **Pin on Top** and **Hide UI** to keep a clip above other windows. Enable **Loop** to keep it playing. Drag with the right mouse button to move the window.

https://github.com/user-attachments/assets/1e89c8df-1919-44f1-b2af-e53c1379f514

*Both demos use footage from The General (1926), directed by Buster Keaton and Clyde Bruckman. The [Wikimedia Commons copy](https://commons.wikimedia.org/wiki/File%3AThe_General_%281926%29.webm) runs 1 h 15 min 52 s.*

## Time input

Timecode starts at the beginning of the clip. **Ctrl+click** the hourglass to switch between timecode and frame numbers.

| Input         | Meaning                                                                                          |
| ------------- | ------------------------------------------------------------------------------------------------ |
| `00:01:12:14` | 1 minute, 12 seconds, and 14 frames                                                              |
| `1:30`        | 1 second and 30 frames, which becomes `00:00:02:05` at 25 fps                                    |
| `1.5`         | 1.5 seconds                                                                                      |
| `120f`        | Frame 120                                                                                        |
| `+N` / `-N`   | Move N frames forward or back. You can also append the offset to the value already in the field. |

Right-click anywhere in the viewer to find commands and open **Hotkeys**.

## Install

1. Download the Windows installer (`.exe`) from the [latest release](https://github.com/olegkulinich/chikchik/releases/latest).
2. Run the installer. Windows SmartScreen may show a warning because the installer is unsigned. The release includes a `.sha256` file you can use to verify the download.

ChikChik checks GitHub for updates once a day by default. You can turn off automatic checks or check manually in **Settings → Updates**.

## Build from source

On Windows, install 64-bit Python 3.14 and Inno Setup 6. Place `libmpv-2.dll` and `LICENSE.LGPL` from the linked libmpv LGPL build in the repository root. Copy `ffmpeg.exe`, `ffprobe.exe`, and `LICENSE` from the linked FFmpeg essentials build into `ffmpeg/`. See [THIRD-PARTY.txt](THIRD-PARTY.txt) for both downloads.

Run `build.bat` to create the installer in `dist/`. Windows packaging files are in [`packaging/windows/`](packaging/windows/), and app icons and fonts are in [`assets/`](assets/). The [GitHub Actions workflow](.github/workflows/build.yml) pins the versions and shows how the release build is assembled.

## License

ChikChik is licensed under the [GNU General Public License v3.0](LICENSE). See [THIRD-PARTY.txt](THIRD-PARTY.txt) for bundled components and their licenses.
