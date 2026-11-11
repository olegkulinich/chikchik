from __future__ import annotations

import json
import math
import os
import re
import tempfile
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field, replace
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


# ------------------------------------------------------------ sequences (T36)

SEQUENCE_EXTENSIONS = (".png", ".jpg", ".jpeg")
SEQUENCE_FPS = 25.0
_TRAILING_NUMBER = re.compile(r"^(.*?)(\d+)$")


@dataclass(frozen=True)
class ImageSequence:
    """PNG/JPG frames numbered in the name. Frame i has number `first + i`; a missing
    number is None and plays as a black frame — the timing stays honest (the program
    doesn't decide that a gap is a cut)."""
    files: tuple[Path | None, ...]
    first: int                     # number of frame 0
    prefix: str                    # name before the number ("shot_")
    digits: int                    # width of the number in the first file
    suffix: str                    # ".png"
    placeholder: Path | None = None   # black frame for the gaps (made by probe_sequence)

    @property
    def numbers(self) -> tuple[int, ...]:
        return tuple(range(self.first, self.first + len(self.files)))

    @property
    def display_name(self) -> str:
        """shot_####.png — how compositing apps show a sequence."""
        return f"{self.prefix}{'#' * self.digits}{self.suffix}"

    @property
    def stem(self) -> str:
        """Base for export names: "shot_" → "shot" (an all-number name → "sequence")."""
        return self.prefix.rstrip(" ._-") or "sequence"

    def frame(self, index: int) -> Path:
        """The file of frame `index`, the black placeholder for a gap."""
        path = self.files[index]
        return path if path is not None else (self.placeholder or self.first_file)

    @property
    def first_file(self) -> Path:
        return next(f for f in self.files if f is not None)

    def missing(self) -> list[tuple[int, int]]:
        """Gaps as (first, last) number ranges."""
        gaps: list[tuple[int, int]] = []
        for i, path in enumerate(self.files):
            if path is not None:
                continue
            number = self.first + i
            if gaps and gaps[-1][1] == number - 1:
                gaps[-1] = (gaps[-1][0], number)
            else:
                gaps.append((number, number))
        return gaps


def find_sequence(path: Path) -> ImageSequence:
    """Any frame → the whole sequence: same folder, prefix and extension, a number at
    the end of the name (any start, any zero padding). A name without a number → one
    frame."""
    suffix = path.suffix
    match = _TRAILING_NUMBER.match(path.stem)
    if not match:
        return ImageSequence((path,), 0, path.stem, 0, suffix)
    prefix = match.group(1)
    found: list[tuple[int, Path]] = []
    pattern = re.compile(re.escape(prefix) + r"(\d+)$", re.IGNORECASE)
    try:
        entries = list(os.scandir(path.parent))
    except OSError:
        entries = []
    for entry in entries:
        name, ext = os.path.splitext(entry.name)
        if ext.lower() != suffix.lower() or not entry.is_file():
            continue
        m = pattern.match(name)
        if m:
            found.append((int(m.group(1)), Path(entry.path)))
    if not found:
        found = [(int(match.group(2)), path)]
    by_number = dict(found)
    first, last = min(by_number), max(by_number)
    return ImageSequence(
        files=tuple(by_number.get(n) for n in range(first, last + 1)), first=first,
        prefix=prefix, digits=len(match.group(2)), suffix=suffix,
    )


def is_image(path: Path) -> bool:
    return path.suffix.lower() in SEQUENCE_EXTENSIONS


def format_ranges(ranges: list[tuple[int, int]], limit: int = 3) -> str:
    """1012–1015, 1020 (+2 more)"""
    parts = [f"{a}–{b}" if b > a else f"{a}" for a, b in ranges[:limit]]
    more = len(ranges) - limit
    return ", ".join(parts) + (f" (+{more} more)" if more > 0 else "")


ALPHA_PIX_FMTS = ("yuva", "rgba", "bgra", "argb", "abgr", "gbrap", "ya")


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
    sequence: ImageSequence | None = None      # T36: PNG/JPG frames instead of a file

    @property
    def alpha(self) -> bool:
        """Transparency (PNG sequences, ProRes 4444 / 4444 XQ, QuickTime Animation…):
        checkerboard in the view, black in .mp4/.gif, kept in .png and Copy Frame."""
        fmt = self.pix_fmt
        if fmt.startswith(ALPHA_PIX_FMTS):
            return True
        return self.sequence is not None and fmt == "pal8"    # PNG palette with tRNS

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
    subtitle: SubtitleTrack | None = None
    scale: float = 1.0
    fps: float | None = None          # output frame rate; None = keep the source rate
    kind: str = "mp4"                 # mp4 | copy | gif | png
    start_number: int = 0             # PNG only: number of the first frame (source frame of In)
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


def probe_sequence(sequence: ImageSequence, fps: float = SEQUENCE_FPS) -> MediaInfo:
    """T36: size/pixel format from the first frame; length = frames / fps."""
    result = _run([
        executable("ffprobe"), "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,pix_fmt,codec_name", "-of", "json",
        str(sequence.first_file),
    ])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "FFprobe could not open the image")
    streams = json.loads(result.stdout).get("streams", [])
    if not streams:
        raise RuntimeError("No image found")
    image = streams[0]
    width, height = int(image.get("width") or 0), int(image.get("height") or 0)
    pix_fmt = str(image.get("pix_fmt") or "")
    if any(f is None for f in sequence.files):
        sequence = replace(sequence, placeholder=_black_frame(sequence, width, height, pix_fmt))
    return MediaInfo(
        duration=len(sequence.files) / fps, width=width, height=height, fps=fps,
        codec=str(image.get("codec_name") or "png"), video_index=0,
        pix_fmt=pix_fmt, color_transfer="", color_primaries="",
        color_space="", color_range="", sequence=sequence,
    )


def _black_frame(sequence: ImageSequence, width: int, height: int, pix_fmt: str) -> Path:
    """An opaque black frame in the sequence's own format and pixel format (concat and
    mpv's mf want one codec and one size)."""
    handle, name = tempfile.mkstemp(prefix="chikchik-gap-", suffix=sequence.suffix.lower())
    os.close(handle)
    fmt = pix_fmt if pix_fmt and sequence.suffix.lower() == ".png" else ""
    chain = f"format={fmt}" if fmt else "null"
    result = _run([
        executable("ffmpeg"), "-v", "error", "-f", "lavfi",
        "-i", f"color=c=black:s={width}x{height}", "-frames:v", "1",
        "-vf", chain, "-y", name,
    ])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Could not make a frame for the gaps")
    return Path(name)


def with_fps(info: MediaInfo, fps: float) -> MediaInfo:
    """The same sequence at another rate."""
    return replace(info, fps=fps, duration=len(info.sequence.files) / fps)


def _concat_escape(path: Path) -> str:
    return str(path).replace("\\", "/").replace("'", "'\\''")


def sequence_list(sequence: ImageSequence, first: int, count: int, fps: float) -> Path:
    """FFmpeg concat list of frames [first, first + count) — the exact frames, glued
    over gaps, each 1/fps long. (The usual trick of repeating the last file adds a
    frame; the output is cut with -frames:v instead.)"""
    lines = []
    for index in range(first, min(len(sequence.files), first + count)):
        lines.append(f"file '{_concat_escape(sequence.frame(index))}'")
        lines.append(f"duration {1.0 / fps:.9f}")
    handle, name = tempfile.mkstemp(prefix="chikchik-seq-", suffix=".txt")
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        stream.write("ffconcat version 1.0\n" + "\n".join(lines) + "\n")
    return Path(name)


def mpv_list(sequence: ImageSequence) -> Path:
    """mpv mf://@list — one frame path per line."""
    handle, name = tempfile.mkstemp(prefix="chikchik-mf-", suffix=".txt")
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        stream.write("\n".join(str(sequence.frame(i)) for i in range(len(sequence.files)))
                     + "\n")
    return Path(name)


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


def clamp_crop(crop: CropRect, frame_width: int, frame_height: int) -> CropRect:
    """Clamp only — exact pixels for RGB outputs (PNG, GIF, Copy Frame), which
    don't need the even geometry of yuv420p."""
    x = max(0, min(crop.x, frame_width - 1))
    y = max(0, min(crop.y, frame_height - 1))
    width = max(1, min(crop.width, frame_width - x))
    height = max(1, min(crop.height, frame_height - y))
    return CropRect(x=x, y=y, width=width, height=height)


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
                  fps: float | None = None, gif: bool = False,
                  keep_alpha: bool = False) -> tuple[list[str], list[str], str]:
    """Return (filters, color tag args, warning). `keep_alpha` — PNG and Copy Frame keep
    transparency; .mp4/.gif get it over black."""
    filters: list[str] = []
    if info.alpha and not keep_alpha:
        # T36: transparent frames over black — premultiplied color is black where alpha is 0
        filters += ["premultiply=inplace=1", "format=rgb24"]
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
    if job.info.sequence is not None:
        command += _input_args(job)
    else:                                    # unchanged for files (frame-exact, checked)
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

    if job.info.sequence is not None and not job.fps:
        command += ["-r", format_fps(job.info.fps)]     # constant rate from the frame list
    if suffix in (".mp4", ".m4v", ".mov"):
        command += ["-movflags", "+faststart"]

    command += ["-y", str(job.temporary_output)]
    return command, warning


def sequence_range(info: MediaInfo, start: float, duration: float) -> tuple[int, int]:
    """(first frame index, frame count) of a time range in a sequence."""
    total = len(info.sequence.files)
    first = max(0, min(total - 1, int(math.floor(start * info.fps + 0.5))))
    count = max(1, min(total - first, int(round(duration * info.fps))))
    return first, count


def _input_args(job: ExportJob) -> list[str]:
    if job.info.sequence is not None:
        # T36: exactly the frames of the range, no seeking
        first, count = sequence_range(job.info, job.start, job.duration)
        listing = sequence_list(job.info.sequence, first, count, job.info.fps)
        return ["-f", "concat", "-safe", "0", "-i", str(listing)]
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


def png_pattern(folder: Path, stem: str, frame_count: int) -> Path:
    """T19b: name_0300.png … — at least 4 digits, more for long sources."""
    digits = max(4, len(str(max(0, frame_count))))
    return folder / f"{stem}_%0{digits}d.png"


def build_png_command(job: ExportJob) -> tuple[list[str], str]:
    """PNG sequence straight into the target folder (frames written so far stay on cancel).
    `job.final_output` is the pattern (…/name_%04d.png)."""
    if job.duration <= 0:
        raise RuntimeError("Out must be later than In")
    filters, _tags, warning = video_filters(job.info, job.crop, job.scale, job.fps, gif=True,
                                            keep_alpha=True)
    filters.append("format=rgba" if job.info.alpha else "format=rgb24")
    command = [
        executable("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error",
        "-progress", "pipe:1", "-nostats",
        *_input_args(job),
        "-map", f"0:{job.info.video_index}", "-an", "-sn",
        "-vf", ",".join(filters),
        "-start_number", str(job.start_number),
        *(["-fps_mode", "passthrough"] if job.info.sequence is not None else []),
        "-f", "image2", "-y", str(job.final_output),
    ]
    return command, warning


def build_frame_command(source: Path, info: MediaInfo, time: float,
                        crop: CropRect | None, scale: float) -> list[str]:
    """T19a: one exact frame as PNG on stdout (Copy Frame)."""
    filters, _tags, _warning = video_filters(info, crop, scale, None, gif=True, keep_alpha=True)
    filters.append("format=rgba" if info.alpha else "format=rgb24")
    if info.sequence is not None:          # T36: the frame's own file
        total = len(info.sequence.files)
        index = max(0, min(total - 1, int(math.floor(time * info.fps + 0.75))))
        seek = ["-i", str(info.sequence.frame(index))]
    else:
        seek = ["-ss", seconds(time), "-i", str(source)]
    return [
        executable("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error",
        *seek,
        "-map", f"0:{info.video_index}", "-an", "-sn",
        "-vf", ",".join(filters), "-frames:v", "1",
        "-f", "image2pipe", "-c:v", "png", "-",
    ]


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


def commit_output(temporary: Path, target: Path) -> None:
    """Move the verified file into place. Raises if it fails; temp file is kept."""
    replace_with_retry(temporary, target)


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
