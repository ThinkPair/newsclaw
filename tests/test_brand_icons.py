from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
REVISION = "a639f641"
SOURCE_SHA256 = "a639f641a808221a3da8547c4bcfe814bc98707dc03a4f5e916c1fbd970126db"
CREAM = (255, 247, 236)

PNG_ASSETS = {
    "assets/brand/newsclaw-icon-source.png": ((1254, 1254), False),
    "assets/brand/newsclaw-icon.png": ((1024, 1024), False),
    "assets/brand/newsclaw-logo-application.png": ((1024, 1024), True),
    "extension/icons/icon16.png": ((16, 16), True),
    "extension/icons/icon32.png": ((32, 32), True),
    "extension/icons/icon48.png": ((48, 48), True),
    "extension/icons/icon128.png": ((128, 128), True),
    "src/openbiliclaw/web/icon-32.png": ((32, 32), True),
    "src/openbiliclaw/web/icon-192.png": ((192, 192), False),
    "src/openbiliclaw/web/icon-512.png": ((512, 512), False),
    "src/openbiliclaw/web/icon-maskable-192.png": ((192, 192), True),
    "src/openbiliclaw/web/icon-maskable-512.png": ((512, 512), True),
    "src/openbiliclaw/web/apple-touch-icon.png": ((180, 180), True),
    "docs/images/newsclaw-icon-32.png": ((32, 32), True),
    "docs/images/newsclaw-icon-512.png": ((512, 512), False),
}


def test_brand_generator_matches_every_checked_in_derivative() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/build_brand_icons.py"), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"revision {REVISION}" in result.stdout


def test_brand_source_and_manifest_preserve_provenance() -> None:
    source = ROOT / "assets/brand/newsclaw-icon-source.png"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == SOURCE_SHA256
    assert not (ROOT / "assets/brand/openbiliclaw-icon.png").exists()

    manifest = json.loads((ROOT / "assets/brand/brand-assets.json").read_text(encoding="utf-8"))
    assert manifest["brand"] == "NewsClaw"
    assert manifest["brand_revision"] == REVISION
    assert manifest["source"] == "assets/brand/newsclaw-icon-source.png"
    assert manifest["source_sha256"] == SOURCE_SHA256
    assert manifest["assets"]


def test_brand_png_assets_have_expected_dimensions_and_alpha_contract() -> None:
    for relative_path, (expected_size, opaque) in PNG_ASSETS.items():
        with Image.open(ROOT / relative_path) as icon:
            assert icon.format == "PNG", relative_path
            assert icon.size == expected_size, relative_path
            rgba = icon.convert("RGBA")
            corner_alpha = rgba.getpixel((0, 0))[3]
            assert corner_alpha == (255 if opaque else 0), relative_path
            center = (expected_size[0] // 2, expected_size[1] // 2)
            assert rgba.getpixel(center)[3] >= 250, relative_path


def test_maskable_icons_keep_essential_foreground_inside_safe_circle() -> None:
    for relative_path in (
        "src/openbiliclaw/web/icon-maskable-192.png",
        "src/openbiliclaw/web/icon-maskable-512.png",
    ):
        with Image.open(ROOT / relative_path) as icon:
            rgb = icon.convert("RGB")
            center = (rgb.width - 1) / 2
            safe_radius = rgb.width * 0.4
            for y in range(rgb.height):
                for x in range(rgb.width):
                    if (x - center) ** 2 + (y - center) ** 2 <= safe_radius**2:
                        continue
                    color = rgb.getpixel((x, y))
                    difference = max(abs(color[index] - CREAM[index]) for index in range(3))
                    assert difference < 72, (relative_path, x, y, color)


def test_desktop_icon_containers_include_required_sizes_and_transparency() -> None:
    with Image.open(ROOT / "packaging/icon.ico") as windows_icon:
        assert windows_icon.format == "ICO"
        required = {
            (16, 16),
            (24, 24),
            (32, 32),
            (48, 48),
            (64, 64),
            (128, 128),
            (256, 256),
        }
        assert required.issubset(windows_icon.ico.sizes())
        for size in required:
            frame = windows_icon.ico.getimage(size).convert("RGBA")
            assert frame.getpixel((0, 0))[3] == 0, size

    with Image.open(ROOT / "packaging/icon.icns") as macos_icon:
        assert macos_icon.format == "ICNS"
        assert macos_icon.size == (1024, 1024)
        assert macos_icon.convert("RGBA").getpixel((0, 0))[3] == 0


def test_current_newsclaw_surfaces_reference_the_new_brand_contract() -> None:
    popup = (ROOT / "extension/popup/popup.html").read_text(encoding="utf-8")
    desktop = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    setup = (ROOT / "src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")
    mobile_html = (ROOT / "src/openbiliclaw/web/index.html").read_text(encoding="utf-8")
    mobile_js = (ROOT / "src/openbiliclaw/web/js/app.js").read_text(encoding="utf-8")
    manifest = json.loads((ROOT / "src/openbiliclaw/web/manifest.json").read_text(encoding="utf-8"))
    notifications = (ROOT / "extension/src/background/notifications.ts").read_text(encoding="utf-8")
    installer = (ROOT / "packaging/openbiliclaw.iss").read_text(encoding="utf-8")
    desktop_entry = (ROOT / "packaging/entry.py").read_text(encoding="utf-8")

    assert '<img class="brand-mark" src="../icons/icon48.png"' in popup
    assert f'src="/m/icon-192.png?v={REVISION}"' in desktop
    assert f'src="/m/icon-192.png?v={REVISION}"' in setup
    assert f'src="icon-192.png?v={REVISION}"' in mobile_js
    assert f'href="icon-32.png?v={REVISION}"' in mobile_html
    assert f'href="apple-touch-icon.png?v={REVISION}"' in mobile_html
    assert all(f"?v={REVISION}" in item["src"] for item in manifest["icons"])
    assert 'chrome.runtime.getURL("icons/icon128.png")' in notifications
    assert "SetupIconFile=packaging\\icon.ico" in installer
    assert 'pystray.Icon("NewsClaw"' in desktop_entry
    assert 'pystray.MenuItem("退出 NewsClaw"' in desktop_entry
    assert "assets/brand/newsclaw-icon.png" in (ROOT / "README.md").read_text(encoding="utf-8")
    assert "assets/brand/newsclaw-icon.png" in (ROOT / "README_EN.md").read_text(encoding="utf-8")


def test_active_newsclaw_brand_files_do_not_reference_the_upstream_logo_asset() -> None:
    current_files = (
        "README.md",
        "README_EN.md",
        "NOTICE",
        "packaging/make_splash.py",
        "src/openbiliclaw/web/index.html",
        "src/openbiliclaw/web/setup/index.html",
        "src/openbiliclaw/web/desktop/index.html",
        "src/openbiliclaw/web/js/app.js",
        "src/openbiliclaw/web/manifest.json",
        "docs/modules/extension.md",
        "docs/modules/runtime.md",
    )
    for relative_path in current_files:
        text = (ROOT / relative_path).read_text(encoding="utf-8").lower()
        assert "openbiliclaw-icon" not in text, relative_path
