#!/usr/bin/env python3
"""Build every NewsClaw brand icon from the checked-in source artwork.

The raw generated artwork is preserved for provenance. All product assets are
derived directly from that source so web, PWA, extension, desktop and docs
surfaces cannot silently drift to different logos.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "assets/brand/newsclaw-icon-source.png"
EXPECTED_SOURCE_SHA256 = "a639f641a808221a3da8547c4bcfe814bc98707dc03a4f5e916c1fbd970126db"
BRAND_REVISION = EXPECTED_SOURCE_SHA256[:8]
CANONICAL_SIZE = 1024
CREAM = (255, 247, 236, 255)


@dataclass(frozen=True)
class PngSpec:
    relative_path: str
    size: int
    opaque: bool = False
    scale: float = 1.0


PNG_SPECS = (
    PngSpec("assets/brand/newsclaw-icon.png", 1024),
    PngSpec("assets/brand/newsclaw-logo-application.png", 1024, opaque=True, scale=0.9),
    PngSpec("extension/icons/icon16.png", 16, opaque=True, scale=0.92),
    PngSpec("extension/icons/icon32.png", 32, opaque=True, scale=0.92),
    PngSpec("extension/icons/icon48.png", 48, opaque=True, scale=0.92),
    PngSpec("extension/icons/icon128.png", 128, opaque=True, scale=0.92),
    PngSpec("src/openbiliclaw/web/icon-32.png", 32, opaque=True, scale=0.92),
    PngSpec("src/openbiliclaw/web/icon-192.png", 192),
    PngSpec("src/openbiliclaw/web/icon-512.png", 512),
    PngSpec("src/openbiliclaw/web/icon-maskable-192.png", 192, opaque=True, scale=0.72),
    PngSpec("src/openbiliclaw/web/icon-maskable-512.png", 512, opaque=True, scale=0.72),
    PngSpec("src/openbiliclaw/web/apple-touch-icon.png", 180, opaque=True, scale=0.86),
    PngSpec("docs/images/newsclaw-icon-32.png", 32, opaque=True, scale=0.92),
    PngSpec("docs/images/newsclaw-icon-512.png", 512),
)

CONTAINER_PATHS = (
    "packaging/icon.ico",
    "packaging/icon.icns",
)
MANIFEST_PATH = "assets/brand/brand-assets.json"
MANAGED_PATHS = tuple(spec.relative_path for spec in PNG_SPECS) + CONTAINER_PATHS + (MANIFEST_PATH,)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _clean_hidden_rgb(image: Image.Image) -> Image.Image:
    """Zero fully transparent RGB values so future resizing cannot reveal a matte."""
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    transparent = alpha.point(lambda value: 255 if value == 0 else 0)
    black = Image.new("RGBA", rgba.size, (0, 0, 0, 0))
    rgba.paste(black, mask=transparent)
    return rgba


def _premultiplied_resize(image: Image.Image, size: int) -> Image.Image:
    """Resize RGBA artwork without creating bright or dark transparent fringes."""
    rgba = _clean_hidden_rgb(image)
    if rgba.size == (size, size):
        return rgba

    alpha = rgba.getchannel("A")
    resized_alpha = alpha.resize((size, size), Image.Resampling.LANCZOS)
    resized_premultiplied: list[Image.Image] = []
    for channel in rgba.split()[:3]:
        premultiplied = ImageChops.multiply(channel, alpha)
        resized_premultiplied.append(premultiplied.resize((size, size), Image.Resampling.LANCZOS))

    alpha_bytes = resized_alpha.tobytes()
    channel_bytes = [channel.tobytes() for channel in resized_premultiplied]
    unpremultiplied = [bytearray(len(alpha_bytes)) for _ in range(3)]
    for index, alpha_value in enumerate(alpha_bytes):
        if alpha_value == 0:
            continue
        for channel_index in range(3):
            value = channel_bytes[channel_index][index]
            unpremultiplied[channel_index][index] = min(
                255,
                (value * 255 + alpha_value // 2) // alpha_value,
            )

    channels = [Image.frombytes("L", (size, size), bytes(values)) for values in unpremultiplied]
    return Image.merge("RGBA", (*channels, resized_alpha))


def _render_variant(canonical: Image.Image, spec: PngSpec) -> Image.Image:
    if not spec.opaque:
        return _premultiplied_resize(canonical, spec.size)

    canvas = Image.new("RGBA", (spec.size, spec.size), CREAM)
    foreground_size = max(1, round(spec.size * spec.scale))
    foreground = _premultiplied_resize(canonical, foreground_size)
    offset = ((spec.size - foreground_size) // 2,) * 2
    canvas.alpha_composite(foreground, offset)
    return canvas


def _save_png(image: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG", optimize=True, compress_level=9)


def _build(output_root: Path) -> None:
    if not SOURCE_PATH.is_file():
        raise SystemExit(f"Missing source artwork: {SOURCE_PATH}")
    source_hash = _sha256(SOURCE_PATH)
    if source_hash != EXPECTED_SOURCE_SHA256:
        raise SystemExit(
            "Source artwork changed without a brand revision update: "
            f"expected {EXPECTED_SOURCE_SHA256}, got {source_hash}"
        )

    with Image.open(SOURCE_PATH) as source:
        source.load()
        if source.mode != "RGBA" or source.size != (1254, 1254):
            raise SystemExit(
                f"NewsClaw source artwork must stay RGBA 1254x1254; got {source.mode} {source.size}"
            )
        canonical = _premultiplied_resize(source, CANONICAL_SIZE)

    for spec in PNG_SPECS:
        _save_png(_render_variant(canonical, spec), output_root / spec.relative_path)

    ico_path = output_root / "packaging/icon.ico"
    ico_path.parent.mkdir(parents=True, exist_ok=True)
    canonical.save(
        ico_path,
        "ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )

    icns_path = output_root / "packaging/icon.icns"
    icns_path.parent.mkdir(parents=True, exist_ok=True)
    canonical.save(icns_path, "ICNS")

    asset_records = []
    for relative_path in (*tuple(spec.relative_path for spec in PNG_SPECS), *CONTAINER_PATHS):
        path = output_root / relative_path
        asset_records.append(
            {
                "path": relative_path.replace("\\", "/"),
                "sha256": _sha256(path),
            }
        )
    manifest = {
        "brand": "NewsClaw",
        "brand_revision": BRAND_REVISION,
        "generator": "scripts/build_brand_icons.py",
        "source": "assets/brand/newsclaw-icon-source.png",
        "source_sha256": source_hash,
        "assets": asset_records,
    }
    manifest_path = output_root / MANIFEST_PATH
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _check() -> int:
    with tempfile.TemporaryDirectory(prefix="newsclaw-brand-") as temp_dir:
        generated_root = Path(temp_dir)
        _build(generated_root)
        differences: list[str] = []
        for relative_path in MANAGED_PATHS:
            expected = ROOT / relative_path
            generated = generated_root / relative_path
            if not expected.is_file():
                differences.append(f"missing: {relative_path}")
            elif expected.read_bytes() != generated.read_bytes():
                differences.append(f"outdated: {relative_path}")

    if differences:
        print("Brand asset check failed:")
        for difference in differences:
            print(f"- {difference}")
        print("Run: python scripts/build_brand_icons.py --write")
        return 1
    print(f"NewsClaw brand assets are current (revision {BRAND_REVISION}).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="regenerate managed assets")
    mode.add_argument("--check", action="store_true", help="verify checked-in assets")
    args = parser.parse_args()

    if args.write:
        _build(ROOT)
        print(f"Wrote NewsClaw brand assets (revision {BRAND_REVISION}).")
        return 0
    return _check()


if __name__ == "__main__":
    raise SystemExit(main())
