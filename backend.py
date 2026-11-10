from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QThread, Signal


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

HDR_TRANSFERS = ("smpte2084", "arib-std-b67")
PRECISE_CONTAINERS = (".mp4", ".mkv", ".mov", ".m4v")
SCALES = (1.0, 0.75, 0.5, 1 / 3, 0.25)
SUB_CODECS = {
    "subrip": "srt", "ass": "ass", "ssa": "ssa", "mov_text": "text", "webvtt": "vtt",
    "hdmv_pgs_subtitle": "pgs", "dvd_subtitle": "vobsub", "dvb_subtitle": "dvb",
}


# ---------------------------------------------------------------- data


@dataclass(frozen=True)
class AudioTrack:
    index: int          # absolute stream index in the file
    number: int         # 1-based number among audio tracks (= mpv "aid")
    codec: str
    channels: int
    language: str
    title: str
    default: bool

    def label(self) -> str:
        parts = [str(self.number)]
        if self.language and self.language != "und":
            parts.append(self.language)
        parts.append(self.codec)
        if self.channels:
            parts.append(
                {1: "mono", 2: "stereo", 6: "5.1", 8: "7.1"}.get(
                    self.channels, f"{self.channels}ch"
                )
            )
        text = " · ".join(parts)
        if self.title:
            text += f" — {self.title}"
        return text


@dataclass(frozen=True)
class SubtitleTrack:
    index: int          # absolute stream index in the file
    number: int         # 1-based number among subtitle tracks (= mpv "sid")
    codec: str
    language: str
    title: str
    default: bool
    forced: bool

    def label(self) -> str:
        parts = [str(self.number)]
        if self.language and self.language != "und":
            parts.append(self.language)
        parts.append(SUB_CODECS.get(self.codec, self.codec))
        if self.forced:
            parts.append("forced")
        text = " · ".join(parts)
        if self.title:
            text += f" — {self.title}"
        return text


@dataclass(frozen=True)
class MediaInfo:
    duration: float
    width: int            # frame size AFTER rotation (what crop works on)
    height: int
    fps: float
    codec: str
    video_index: int
    pix_fmt: str
    color_transfer: str
    color_primaries: str
    color_space: str
    color_range: str
    audio: tuple[AudioTrack, ...] = field(default_factory=tuple)
    subtitles: tuple[SubtitleTrack, ...] = field(default_factory=tuple)

    @property
    def hdr(self) -> bool:
        return self.color_transfer in HDR_TRANSFERS

    @property
    def frame_duration(self) -> float:
        return 1.0 / self.fps if self.fps > 0 else 0.0

    def default_audio(self) -> AudioTrack | None:
        for track in self.audio:
            if track.default:
                return track
        return self.audio[0] if self.audio else None


@dataclass(frozen=True)
class CropRect:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class ExportJob:
    source: Path
    info: MediaInfo
    temporary_output: Path
    final_output: Path
    start: float
    duration: float
    crop: CropRect | None
    fast: bool
    crf: int
    preset: str
    audio: AudioTrack | None
    backup_original: bool
    subtitle: SubtitleTrack | None = None
    scale: float = 1.0
    fps: float | None = None          # output frame rate; None = keep the source rate
    kind: str = "mp4"                 # mp4 | copy | gif
    palette: Path | None = None       # GIF only: temporary palette next to the output


# ------------------------------------------------------------- helpers


def _bundle_dirs() -> list[Path]:
    """Folders that may hold our own ffmpeg\\ copy: install dir, PyInstaller dir, source dir."""
    dirs: list[Path] = []
    if getattr(sys, "frozen", False):
        dirs.append(Path(sys.executable).resolve().parent)
        bundle = getattr(sys, "_MEIPASS", None)
        if bundle:
            dirs.append(Path(bundle))
    dirs.append(Path(__file__).resolve().parent)
    return dirs


@lru_cache(maxsize=None)
def executable(name: str) -> str:
    """Bundled ffmpeg\\<name>.exe first (known build with zscale), PATH only as a fallback."""
    filename = name + (".exe" if os.name == "nt" else "")
    for directory in _bundle_dirs():
        candidate = directory / "ffmpeg" / filename
        if candidate.is_file():
            return str(candidate)
    result = shutil.which(name)
    if not result:
        raise RuntimeError(f"{name} not found (neither bundled nor in PATH)")
    return result


def seconds(value: float) -> str:
    return f"{max(0.0, value):.6f}"


def _run(command: list[str], timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
    )


def _float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _fps(value: str | None) -> float:
    if not value or value == "0/0":
        return 0.0
    try:
        if "/" in value:
            numerator, denominator = value.split("/", 1)
            return float(numerator) / float(denominator)
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def _rotation(stream: dict) -> int:
    for item in stream.get("side_data_list", []) or []:
        if "rotation" in item:
            return int(_float(item["rotation"]))
    return int(_float((stream.get("tags") or {}).get("rotate")))


def _known(value) -> str:
    value = str(value or "")
    return "" if value in ("", "unknown", "unspecified", "reserved") else value


@lru_cache(maxsize=None)
def ffmpeg_has_filter(name: str) -> bool:
    try:
        result = _run([executable("ffmpeg"), "-hide_banner", "-filters"])
    except Exception:
        return False
    return any(
        len(line.split()) > 1 and line.split()[1] == name
        for line in result.stdout.splitlines()
    )


# --------------------------------------------------------------- probe


def probe_media(path: Path) -> MediaInfo:
    result = _run([
        executable("ffprobe"),
        "-v", "error",
        "-show_format",
        "-show_streams",
        "-of", "json",
        str(path),
    ])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "FFprobe could not open the file")

    data = json.loads(result.stdout)
    streams = data.get("streams", [])

    # Skip cover art (attached pictures) — it is also a "video" stream.
    video = next(
        (
            s for s in streams
            if s.get("codec_type") == "video"
            and not (s.get("disposition") or {}).get("attached_pic")
        ),
        None,
    )
    if video is None:
        raise RuntimeError("No video stream found")

    duration = _float(data.get("format", {}).get("duration"))
    if duration <= 0:
        duration = _float(video.get("duration"))
    if duration <= 0:
        raise RuntimeError("Could not determine video duration")

    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    if abs(_rotation(video)) % 180 == 90:
        width, height = height, width

    audio = []
    for stream in streams:
        if stream.get("codec_type") != "audio":
            continue
        tags = stream.get("tags") or {}
        audio.append(
            AudioTrack(
                index=int(stream.get("index", 0)),
                number=len(audio) + 1,
                codec=str(stream.get("codec_name") or "?"),
                channels=int(stream.get("channels") or 0),
                language=str(tags.get("language") or ""),
                title=str(tags.get("title") or ""),
                default=bool((stream.get("disposition") or {}).get("default")),
            )
        )

    subtitles = []
    for stream in streams:
        if stream.get("codec_type") != "subtitle":
            continue
        tags = stream.get("tags") or {}
        disposition = stream.get("disposition") or {}
        subtitles.append(
            SubtitleTrack(
                index=int(stream.get("index", 0)),
                number=len(subtitles) + 1,
                codec=str(stream.get("codec_name") or "?"),
                language=str(tags.get("language") or ""),
                title=str(tags.get("title") or ""),
                default=bool(disposition.get("default")),
                forced=bool(disposition.get("forced")),
            )
        )

    return MediaInfo(
        duration=duration,
        width=width,
        height=height,
        fps=_fps(video.get("avg_frame_rate")) or _fps(video.get("r_frame_rate")),
        codec=str(video.get("codec_name") or "unknown"),
        video_index=int(video.get("index", 0)),
        pix_fmt=str(video.get("pix_fmt") or ""),
        color_transfer=_known(video.get("color_transfer")),
        color_primaries=_known(video.get("color_primaries")),
        color_space=_known(video.get("color_space")),
        color_range=_known(video.get("color_range")),
        audio=tuple(audio),
        subtitles=tuple(subtitles),
    )


def validate_video(path: Path) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError("FFmpeg did not create the output file")

    result = _run([
        executable("ffprobe"),
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_name",
        "-of", "json",
        str(path),
    ])
    if result.returncode != 0:
        raise RuntimeError("Output file failed FFprobe check:\n" + result.stderr.strip())
    try:
        streams = json.loads(result.stdout).get("streams", [])
    except json.JSONDecodeError as exc:
        raise RuntimeError("Invalid FFprobe response") from exc
    if not streams:
        raise RuntimeError("Output file has no video stream")


# ---------------------------------------------------------------- crop


def normalize_crop(crop: CropRect, frame_width: int, frame_height: int) -> CropRect:
    """Clamp crop to the frame and make geometry even (yuv420p)."""
    max_width = frame_width - frame_width % 2
    max_height = frame_height - frame_height % 2
    if max_width < 2 or max_height < 2:
        raise RuntimeError("Frame is too small to crop")

    x = max(0, min(crop.x, max_width - 2)) // 2 * 2
    y = max(0, min(crop.y, max_height - 2)) // 2 * 2
    width = max(2, min(crop.width, max_width - x)) // 2 * 2
    height = max(2, min(crop.height, max_height - y)) // 2 * 2
    return CropRect(x=x, y=y, width=max(2, width), height=max(2, height))


def scaled_size(width: int, height: int, scale: float) -> tuple[int, int]:
    """Output size after scaling, rounded to even numbers (yuv420p)."""
    def even(value: float) -> int:
        return max(2, int(round(value / 2)) * 2)
    if scale == 1.0:
        return width // 2 * 2, height // 2 * 2
    return even(width * scale), even(height * scale)


# ------------------------------------------------------------- command


def format_fps(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def video_filters(info: MediaInfo, crop: CropRect | None, scale: float = 1.0,
                  fps: float | None = None, gif: bool = False) -> tuple[list[str], list[str], str]:
    """Return (filters, color tag args, warning)."""
    filters: list[str] = []
    if fps:
        filters.append(f"fps={format_fps(fps)}")   # first: fewer frames for everything after
    if crop:
        filters.append(f"crop={crop.width}:{crop.height}:{crop.x}:{crop.y}")
    if scale != 1.0:
        base_w, base_h = (crop.width, crop.height) if crop else (info.width, info.height)
        width, height = scaled_size(base_w, base_h, scale)
        filters.append(f"scale={width}:{height}:flags=lanczos")

    warning = ""
    tags: list[str] = []

    if info.hdr:
        if ffmpeg_has_filter("zscale") and ffmpeg_has_filter("tonemap"):
            filters.extend([
                "zscale=t=linear:npl=100",
                "format=gbrpf32le",
                "zscale=p=bt709",
                "tonemap=tonemap=hable:desat=0",
                "zscale=t=bt709:m=bt709:r=tv",
            ])
            tags = [
                "-colorspace", "bt709",
                "-color_primaries", "bt709",
                "-color_trc", "bt709",
                "-color_range", "tv",
            ]
        else:
            warning = (
                "HDR source: this FFmpeg build has no zscale filter, "
                "colors will look washed out"
            )
    else:
        if info.color_space:
            tags += ["-colorspace", info.color_space]
        if info.color_primaries:
            tags += ["-color_primaries", info.color_primaries]
        if info.color_transfer:
            tags += ["-color_trc", info.color_transfer]
        if info.color_range:
            tags += ["-color_range", info.color_range]

    if gif:
        return filters, [], warning
    filters.append("format=yuv420p")
    # Also protects H.264/yuv420p from odd source dimensions.
    filters.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")
    return filters, tags, warning


def build_ffmpeg_command(job: ExportJob) -> tuple[list[str], str]:
    """Return (command, warning)."""
    if job.duration <= 0:
        raise RuntimeError("Out must be later than In")
    if job.fast and job.crop is not None:
        raise RuntimeError("Crop is not possible in Copy mode")
    if job.fast and job.scale != 1.0:
        raise RuntimeError("Scale is not possible in Copy mode")
    if job.fast and job.fps:
        raise RuntimeError("Changing fps is not possible in Copy mode")

    command = [
        executable("ffmpeg"),
        "-hide_banner",
        "-nostdin",
        "-loglevel", "error",
        "-progress", "pipe:1",
        "-nostats",
    ]
    if job.start > 0:
        command += ["-ss", seconds(job.start)]
    command += ["-i", str(job.source), "-t", seconds(job.duration)]

    command += ["-map", f"0:{job.info.video_index}"]
    if job.audio is not None:
        command += ["-map", f"0:{job.audio.index}"]

    warning = ""
    suffix = job.final_output.suffix.lower()

    if job.fast:
        if suffix == ".mkv" and job.subtitle is not None:
            # Only the subtitle track picked in the menu + attached fonts (anime releases).
            command += [
                "-map", f"0:{job.subtitle.index}", "-map", "0:t?",
                "-disposition:s:0", "default",
            ]
        command += [
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            "-map_metadata", "0",
            "-map_chapters", "-1",
            "-ignore_unknown",
        ]
    else:
        filters, tags, warning = video_filters(job.info, job.crop, job.scale, job.fps)
        channels = job.audio.channels if job.audio else 2
        command += [
            "-vf", ",".join(filters),
            "-c:v", "libx264",
            "-preset", job.preset,
            "-crf", str(job.crf),
            *tags,
            "-c:a", "aac",
            "-b:a", "384k" if channels > 2 else "192k",
            "-map_chapters", "-1",
        ]

    if suffix in (".mp4", ".m4v", ".mov"):
        command += ["-movflags", "+faststart"]

    command += ["-y", str(job.temporary_output)]
    return command, warning


def _input_args(job: ExportJob) -> list[str]:
    args = ["-ss", seconds(job.start)] if job.start > 0 else []
    return args + ["-t", seconds(job.duration), "-i", str(job.source)]


def build_gif_commands(job: ExportJob) -> tuple[list[list[str]], str]:
    """Two-pass GIF (palettegen → paletteuse). Return ([pass1, pass2], warning).

    Pass 1 also writes a null output: palettegen emits its single frame only at the
    end, so without it FFmpeg would report no progress for the whole first pass.
    """
    if job.duration <= 0:
        raise RuntimeError("Out must be later than In")
    if job.palette is None:
        raise RuntimeError("No palette path for the GIF export")
    filters, _tags, warning = video_filters(job.info, job.crop, job.scale, job.fps, gif=True)
    chain = ",".join(filters) if filters else "null"
    base = [
        executable("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error",
        "-progress", "pipe:1", "-nostats",
    ]
    video = f"0:{job.info.video_index}"
    first = base + _input_args(job) + [
        "-filter_complex", f"[{video}]{chain},split[a][b];[a]palettegen[p]",
        "-map", "[p]", "-update", "1", "-frames:v", "1", "-y", str(job.palette),
        "-map", "[b]", "-f", "null", "-",
    ]
    second = base + _input_args(job) + [
        "-i", str(job.palette),
        "-filter_complex", f"[{video}]{chain}[x];[x][1:v]paletteuse",
        "-an", "-f", "gif", "-y", str(job.temporary_output),
    ]
    return [first, second], warning


def palette_path(target: Path) -> Path:
    return unique_path(target.with_name(f"{target.stem}.palette-tmp.png"))


# --------------------------------------------------------------- files


def temporary_output(target: Path) -> Path:
    return target.with_name(f".{target.stem}.qtc-{uuid.uuid4().hex[:8]}.tmp{target.suffix}")


def same_path(first: Path, second: Path) -> bool:
    return os.path.normcase(os.path.abspath(first)) == os.path.normcase(os.path.abspath(second))


def unique_path(path: Path) -> Path:
    candidate = path
    counter = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}-{counter}{path.suffix}")
        counter += 1
    return candidate


def unique_backup_path(source: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return unique_path(source.with_name(f"{source.stem}.backup-{timestamp}{source.suffix}"))


def replace_with_retry(source: Path, target: Path, attempts: int = 15, delay: float = 0.2) -> None:
    """os.replace that waits for players / antivirus / Dropbox to release the file."""
    for attempt in range(attempts):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)


def commit_output(temporary: Path, target: Path, backup_original: bool) -> Path | None:
    """Move the verified file into place. Raises if it fails; temp file is kept."""
    if not backup_original:
        replace_with_retry(temporary, target)
        return None

    backup = unique_backup_path(target)
    replace_with_retry(target, backup)
    try:
        replace_with_retry(temporary, target)
    except Exception as original_error:
        try:
            replace_with_retry(backup, target)
        except Exception as rollback_error:
            raise RuntimeError(
                "Could not install the new file or restore the original.\n"
                f"Original is saved here: {backup}\n"
                f"Restore error: {rollback_error}"
            ) from rollback_error
        raise original_error
    return backup


# -------------------------------------------------------------- worker


class ExportWorker(QThread):
    progress_changed = Signal(int, int, int)   # stage (0-based), stage count, percent
    completed = Signal(bool, str)

    def __init__(self, commands: list[list[str]], duration: float, parent=None):
        super().__init__(parent)
        self.commands = commands
        self.duration = max(0.001, duration)
        self._process: subprocess.Popen | None = None
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True
        process = self._process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

    @staticmethod
    def _parse_out_time(value: str) -> float:
        try:
            hours, minutes, secs = value.split(":")
            return int(hours) * 3600 + int(minutes) * 60 + float(secs)
        except (ValueError, TypeError):
            return 0.0

    def run(self) -> None:
        try:
            total = len(self.commands)
            for stage, command in enumerate(self.commands):
                if self._cancelled:
                    self.completed.emit(False, "")
                    return
                ok, error = self._run_stage(command, stage, total)
                if not ok:
                    self.completed.emit(False, "" if self._cancelled else error)
                    return
            self.completed.emit(True, "")
        except Exception as exc:
            self.completed.emit(False, str(exc))
        finally:
            self._process = None

    def _run_stage(self, command: list[str], stage: int, total: int) -> tuple[bool, str]:
        log_lines: list[str] = []
        last_percent = -1
        self.progress_changed.emit(stage, total, 0)
        self._process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=CREATE_NO_WINDOW,
        )
        if self._cancelled:
            self.cancel()

        assert self._process.stdout is not None
        for raw_line in self._process.stdout:
            line = raw_line.strip()
            if not line:
                continue

            current = None
            if line.startswith("out_time_ms="):
                # Despite the name, FFmpeg reports microseconds here.
                try:
                    current = int(line.split("=", 1)[1]) / 1_000_000
                except ValueError:
                    pass
            elif line.startswith("out_time="):
                current = self._parse_out_time(line.split("=", 1)[1])

            if current is not None:
                percent = max(0, min(99, round(current / self.duration * 100)))
                if percent > last_percent:
                    last_percent = percent
                    self.progress_changed.emit(stage, total, percent)
                continue

            if "=" in line and line.split("=", 1)[0].replace("_", "").isalnum() \
                    and " " not in line.split("=", 1)[0]:
                continue  # other -progress keys
            log_lines.append(line)

        return_code = self._process.wait()
        self._process = None
        if self._cancelled:
            return False, ""
        if return_code != 0:
            details = "\n".join(log_lines[-25:])
            return False, details or f"FFmpeg exited with code {return_code}"
        return True, ""
