"""Bubble-pop audio playback sourced from gentle real-world bubble samples."""

from __future__ import annotations

import math
import random
import struct
import tempfile
import wave
from pathlib import Path
from time import monotonic

from PyQt6.QtCore import QObject, QUrl

try:
    from PyQt6.QtMultimedia import QSoundEffect
except Exception:  # pragma: no cover - gracefully degrade when multimedia is unavailable
    QSoundEffect = None


class BubblePopAudio(QObject):
    """Play short, soft bubble-pop samples with gentle size-aware scaling."""

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.enabled = QSoundEffect is not None
        self._pools: dict[str, list[QSoundEffect]] = {}
        self._pool_cursor: dict[str, int] = {}
        self._sample_paths: dict[str, list[Path]] = {}
        self._last_played_at = 0.0

        if not self.enabled:
            return

        self._sample_paths = self._ensure_sample_library()
        self._build_effect_pools()

    def _asset_dir(self) -> Path:
        """Return the bundled project asset directory."""
        return Path(__file__).resolve().parent.parent / "assets"

    def _audio_cache_dir(self) -> Path:
        """Return the writable cache directory used for generated WAV files."""
        cache_dir = Path(tempfile.gettempdir()) / "plr000_cca001_bubble_audio"
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir

    def _ensure_sample_library(self) -> dict[str, list[Path]]:
        """Prefer trimmed bundled samples and fall back to procedural sounds."""
        soap_source = self._asset_dir() / "bubble_pop_soft_small.wav"
        bundled_specs = {
            "small": (
                {
                    "source": soap_source,
                    "clip_ms": 62.0,
                    "pre_roll_ms": 9.0,
                    "target_peak": 0.34,
                    "decay_strength": 3.9,
                    "soften_mix": 0.66,
                },
                {
                    "source": soap_source,
                    "clip_ms": 70.0,
                    "pre_roll_ms": 11.0,
                    "target_peak": 0.36,
                    "decay_strength": 3.55,
                    "soften_mix": 0.61,
                },
                {
                    "source": soap_source,
                    "clip_ms": 78.0,
                    "pre_roll_ms": 12.0,
                    "target_peak": 0.39,
                    "decay_strength": 3.25,
                    "soften_mix": 0.56,
                },
            ),
            "medium": (
                {
                    "source": soap_source,
                    "clip_ms": 72.0,
                    "pre_roll_ms": 10.0,
                    "target_peak": 0.37,
                    "decay_strength": 3.45,
                    "soften_mix": 0.6,
                },
                {
                    "source": soap_source,
                    "clip_ms": 82.0,
                    "pre_roll_ms": 12.0,
                    "target_peak": 0.4,
                    "decay_strength": 3.1,
                    "soften_mix": 0.55,
                },
                {
                    "source": soap_source,
                    "clip_ms": 92.0,
                    "pre_roll_ms": 13.0,
                    "target_peak": 0.44,
                    "decay_strength": 2.82,
                    "soften_mix": 0.5,
                },
            ),
            "large": (
                {
                    "source": soap_source,
                    "clip_ms": 82.0,
                    "pre_roll_ms": 11.0,
                    "target_peak": 0.39,
                    "decay_strength": 3.0,
                    "soften_mix": 0.55,
                },
                {
                    "source": soap_source,
                    "clip_ms": 94.0,
                    "pre_roll_ms": 13.0,
                    "target_peak": 0.43,
                    "decay_strength": 2.72,
                    "soften_mix": 0.49,
                },
                {
                    "source": soap_source,
                    "clip_ms": 106.0,
                    "pre_roll_ms": 14.0,
                    "target_peak": 0.47,
                    "decay_strength": 2.45,
                    "soften_mix": 0.44,
                },
            ),
        }

        if all(Path(spec["source"]).exists() for variants in bundled_specs.values() for spec in variants):
            return self._build_trimmed_asset_library(bundled_specs)

        return self._build_procedural_library()

    def _build_trimmed_asset_library(
        self,
        specs: dict[str, tuple[dict[str, object], ...]],
    ) -> dict[str, list[Path]]:
        """Create compact cached pop snippets from bundled bubble WAV assets."""
        cache_dir = self._audio_cache_dir()
        sample_paths: dict[str, list[Path]] = {}

        for name, variants in specs.items():
            variant_paths: list[Path] = []
            for index, spec in enumerate(variants, start=1):
                source_path = Path(spec["source"])
                output_path = cache_dir / f"bubble_pop_{name}_soap_trim_v{index}.wav"
                if (
                    not output_path.exists()
                    or output_path.stat().st_mtime < source_path.stat().st_mtime
                ):
                    self._write_trimmed_asset_wav(
                        source_path=source_path,
                        output_path=output_path,
                        clip_ms=float(spec["clip_ms"]),
                        pre_roll_ms=float(spec["pre_roll_ms"]),
                        target_peak=float(spec["target_peak"]),
                        decay_strength=float(spec["decay_strength"]),
                        soften_mix=float(spec["soften_mix"]),
                    )
                variant_paths.append(output_path)
            sample_paths[name] = variant_paths

        return sample_paths

    def _build_procedural_library(self) -> dict[str, list[Path]]:
        """Create a small procedural fallback library when bundled assets are unavailable."""
        sample_specs = {
            "small": {"duration": 0.048, "base_frequency": 520.0, "secondary_frequency": 860.0},
            "medium": {"duration": 0.064, "base_frequency": 390.0, "secondary_frequency": 650.0},
            "large": {"duration": 0.082, "base_frequency": 290.0, "secondary_frequency": 510.0},
        }
        cache_dir = self._audio_cache_dir()
        sample_paths: dict[str, list[Path]] = {}
        for name, spec in sample_specs.items():
            output_path = cache_dir / f"bubble_pop_{name}.wav"
            if not output_path.exists():
                self._write_procedural_bubble_wav(
                    output_path=output_path,
                    duration=float(spec["duration"]),
                    base_frequency=float(spec["base_frequency"]),
                    secondary_frequency=float(spec["secondary_frequency"]),
                )
            sample_paths[name] = [output_path]
        return sample_paths

    def _read_source_wav(self, source_path: Path) -> tuple[int, list[float]]:
        """Read a PCM WAV file and downmix it to mono float samples."""
        with wave.open(str(source_path), "rb") as wav_file:
            frame_count = wav_file.getnframes()
            num_channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            sample_rate = wav_file.getframerate()
            compression = wav_file.getcomptype()
            raw_frames = wav_file.readframes(frame_count)

        if compression != "NONE":
            raise ValueError(f"Unsupported WAV compression for {source_path.name}: {compression}")

        samples = self._decode_pcm_frames(raw_frames, sample_width)
        if num_channels <= 1:
            return sample_rate, samples

        mono_samples: list[float] = []
        for index in range(0, len(samples), num_channels):
            channel_slice = samples[index:index + num_channels]
            if not channel_slice:
                continue
            mono_samples.append(sum(channel_slice) / len(channel_slice))
        return sample_rate, mono_samples

    def _decode_pcm_frames(self, raw_frames: bytes, sample_width: int) -> list[float]:
        """Decode raw PCM bytes into normalized floating-point samples."""
        if sample_width == 1:
            return [((byte - 128) / 128.0) for byte in raw_frames]

        if sample_width == 2:
            sample_count = len(raw_frames) // 2
            return [
                sample / 32768.0
                for sample in struct.unpack(f"<{sample_count}h", raw_frames)
            ]

        if sample_width == 3:
            samples: list[float] = []
            for offset in range(0, len(raw_frames), 3):
                chunk = raw_frames[offset:offset + 3]
                if len(chunk) < 3:
                    continue
                sign_byte = b"\xff" if chunk[2] & 0x80 else b"\x00"
                integer = int.from_bytes(chunk + sign_byte, "little", signed=True)
                samples.append(integer / 8388608.0)
            return samples

        if sample_width == 4:
            sample_count = len(raw_frames) // 4
            return [
                sample / 2147483648.0
                for sample in struct.unpack(f"<{sample_count}i", raw_frames)
            ]

        raise ValueError(f"Unsupported WAV sample width: {sample_width}")

    def _detect_onset_index(self, samples: list[float], sample_rate: int) -> int:
        """Find the first meaningful transient so long tails are trimmed away."""
        if not samples:
            return 0

        absolute = [abs(sample) for sample in samples]
        probe_frames = max(24, min(len(absolute), int(sample_rate * 0.02)))
        noise_floor = sum(absolute[:probe_frames]) / max(1, probe_frames)
        threshold = max(0.02, noise_floor * 5.5)
        window = max(8, sample_rate // 800)
        running = 0.0

        for index, amplitude in enumerate(absolute):
            running += amplitude
            if index >= window:
                running -= absolute[index - window]
                level = running / window
                if level >= threshold:
                    return max(0, index - window // 2)

        peak_index = max(range(len(absolute)), key=absolute.__getitem__)
        return max(0, peak_index - max(1, int(sample_rate * 0.004)))

    def _write_trimmed_asset_wav(
        self,
        source_path: Path,
        output_path: Path,
        clip_ms: float,
        pre_roll_ms: float,
        target_peak: float,
        decay_strength: float,
        soften_mix: float,
    ) -> None:
        """Extract a short, softened pop transient from a longer source WAV."""
        sample_rate, samples = self._read_source_wav(source_path)
        onset_index = self._detect_onset_index(samples, sample_rate)
        start_index = max(0, onset_index - int(sample_rate * (pre_roll_ms / 1000.0)))
        clip_frames = max(1, int(sample_rate * (clip_ms / 1000.0)))
        end_index = min(len(samples), start_index + clip_frames)
        clipped = samples[start_index:end_index]

        if not clipped:
            clipped = samples[:clip_frames]

        fade_in_frames = max(1, int(sample_rate * 0.003))
        fade_out_frames = max(1, int(sample_rate * 0.04))
        shaped = self._shape_clip(
            clipped,
            fade_in_frames=fade_in_frames,
            fade_out_frames=fade_out_frames,
            target_peak=target_peak,
            decay_strength=decay_strength,
            soften_mix=soften_mix,
        )
        self._write_pcm_wav(output_path, sample_rate, shaped)

    def _shape_clip(
        self,
        samples: list[float],
        fade_in_frames: int,
        fade_out_frames: int,
        target_peak: float,
        decay_strength: float,
        soften_mix: float,
    ) -> list[float]:
        """Apply a gentle envelope so the pop feels tight and pleasing."""
        if not samples:
            return [0.0]

        smoothed = self._soften_transient(samples, soften_mix=soften_mix)
        total = len(samples)
        shaped: list[float] = []
        for index, sample in enumerate(smoothed):
            normalized = index / max(1, total - 1)
            envelope = math.exp(-normalized * decay_strength)

            if index < fade_in_frames:
                envelope *= index / max(1, fade_in_frames)

            tail_index = total - 1 - index
            if tail_index < fade_out_frames:
                envelope *= tail_index / max(1, fade_out_frames)

            softened = math.tanh(sample * 0.72)
            shaped.append(softened * envelope)

        peak = max(abs(sample) for sample in shaped) if shaped else 0.0
        if peak <= 1e-6:
            return shaped

        scale = min(1.0, target_peak / peak)
        return [max(-1.0, min(1.0, sample * scale)) for sample in shaped]

    def _soften_transient(self, samples: list[float], soften_mix: float) -> list[float]:
        """Blur the transient slightly so the pop feels more soapy than percussive."""
        if not samples:
            return samples

        soften_mix = max(0.0, min(0.75, soften_mix))
        if soften_mix <= 0.0:
            return list(samples)

        smoothed: list[float] = []
        previous = 0.0
        for sample in samples:
            filtered = (sample * (1.0 - soften_mix)) + (previous * soften_mix)
            previous = filtered
            smoothed.append(filtered)

        reverse_smoothed: list[float] = []
        previous = 0.0
        for sample in reversed(smoothed):
            filtered = (sample * (1.0 - soften_mix * 0.72)) + (previous * soften_mix * 0.72)
            previous = filtered
            reverse_smoothed.append(filtered)

        reverse_smoothed.reverse()
        return reverse_smoothed

    def _write_pcm_wav(self, output_path: Path, sample_rate: int, samples: list[float]) -> None:
        """Write normalized mono float samples to a 16-bit PCM WAV file."""
        frames = bytearray()
        for sample in samples:
            clipped = max(-1.0, min(1.0, sample))
            frames.extend(struct.pack("<h", int(clipped * 32767)))

        with wave.open(str(output_path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(frames)

    def _write_procedural_bubble_wav(
        self,
        output_path: Path,
        duration: float,
        base_frequency: float,
        secondary_frequency: float,
    ) -> None:
        """Render one soft underwater pop sample to a PCM WAV file."""
        sample_rate = 22050
        frame_count = max(1, int(sample_rate * duration))
        frames = bytearray()
        random_phase = random.uniform(0.0, math.tau)

        for index in range(frame_count):
            t = index / sample_rate
            normalized = t / duration

            attack = 1.0 - math.exp(-normalized * 32.0)
            decay = math.exp(-normalized * 6.6)
            envelope = attack * decay

            downward_glide = 1.0 - (normalized * 0.22)
            main_tone = math.sin((2.0 * math.pi * base_frequency * downward_glide * t) + random_phase)
            body_tone = math.sin((2.0 * math.pi * secondary_frequency * (1.0 - normalized * 0.1) * t))
            click_tone = math.sin(2.0 * math.pi * (secondary_frequency * 1.6) * t)

            noise = (random.random() * 2.0) - 1.0
            click_envelope = math.exp(-normalized * 26.0)
            airy_noise = noise * math.exp(-normalized * 11.0)

            sample = (
                (main_tone * 0.52 + body_tone * 0.23) * envelope
                + click_tone * click_envelope * 0.055
                + airy_noise * envelope * 0.11
            )
            sample = max(-1.0, min(1.0, sample * 0.64))
            frames.extend(struct.pack("<h", int(sample * 32767)))

        with wave.open(str(output_path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(frames)

    def _build_effect_pools(self) -> None:
        """Preload a tiny pool per sample size so pops can overlap cleanly."""
        for name, sample_paths in self._sample_paths.items():
            effect_pool: list[QSoundEffect] = []
            for sample_path in sample_paths:
                for _ in range(2):
                    effect = QSoundEffect(self)
                    effect.setLoopCount(1)
                    effect.setVolume(0.06)
                    effect.setSource(QUrl.fromLocalFile(str(sample_path)))
                    effect_pool.append(effect)
            self._pools[name] = effect_pool
            self._pool_cursor[name] = 0

    def _sample_name_for_radius(self, radius: float) -> str:
        """Map a bubble radius to one of the prepared pop samples."""
        if radius < 3.2:
            return "small"
        if radius < 6.6:
            return "medium"
        return "large"

    def play_pop(self, radius: float) -> None:
        """Play one soft bubble-pop sound scaled to the visible bubble size."""
        if not self.enabled:
            return

        now = monotonic()
        minimum_interval = 0.072 if radius < 3.4 else 0.058
        if now - self._last_played_at < minimum_interval:
            return

        sample_name = self._sample_name_for_radius(radius)
        effect_pool = self._pools.get(sample_name)
        if not effect_pool:
            return

        available_players = [player for player in effect_pool if not player.isPlaying()]
        effect = random.choice(available_players) if available_players else None
        if effect is None:
            cursor = self._pool_cursor.get(sample_name, 0)
            effect = effect_pool[cursor % len(effect_pool)]
            self._pool_cursor[sample_name] = (cursor + 1) % len(effect_pool)
            effect.stop()

        normalized = max(0.0, min(1.0, (radius - 1.0) / 10.5))
        volume = 0.022 + normalized * 0.04 + random.uniform(-0.004, 0.003)
        effect.setVolume(max(0.016, min(0.072, volume)))
        self._last_played_at = now
        effect.play()

    def play_pop_for_bubble(self, bubble: dict[str, object]) -> None:
        """Convenience wrapper for calling from the bubble overlay."""
        self.play_pop(float(bubble.get("radius", 2.0)))
