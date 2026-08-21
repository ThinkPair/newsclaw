"""Prepare an isolated NewsClaw runtime without printing credentials.

Only the upstream-compatible ``[llm...]`` section is copied from an existing
local OpenBiliClaw configuration. News source credentials stay outside Git and
are resolved by the news adapters from their documented environment variables.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
from pathlib import Path


_SECTION_RE = re.compile(r"^\s*\[([^]]+)]\s*$", re.MULTILINE)


def _section_span(text: str, prefix: str) -> tuple[int, int] | None:
    matches = list(_SECTION_RE.finditer(text))
    start_index: int | None = None
    end_index = len(text)
    for index, match in enumerate(matches):
        section = match.group(1).strip().strip('"')
        if section == prefix or section.startswith(f"{prefix}."):
            if start_index is None:
                start_index = match.start()
            continue
        if start_index is not None:
            end_index = match.start()
            break
    if start_index is None:
        return None
    return start_index, end_index


def _replace_section_group(template: str, source: str, prefix: str) -> str:
    source_span = _section_span(source, prefix)
    template_span = _section_span(template, prefix)
    if source_span is None or template_span is None:
        raise ValueError(f"missing [{prefix}] configuration section")
    source_block = source[source_span[0] : source_span[1]].rstrip() + "\n\n"
    return template[: template_span[0]] + source_block + template[template_span[1] :]


def _replace_api_value(text: str, key: str, value: str) -> str:
    api_span = _section_span(text, "api")
    if api_span is None:
        raise ValueError("missing [api] configuration section")
    start, end = api_span
    block = text[start:end]
    pattern = re.compile(rf"(?m)^(\s*{re.escape(key)}\s*=\s*).*$")
    if pattern.search(block) is None:
        raise ValueError(f"missing [api].{key} configuration field")
    block = pattern.sub(rf"\g<1>{value}", block, count=1)
    return text[:start] + block + text[end:]


def _disable_legacy_sources(text: str) -> str:
    """Disable every inherited social/video source in the isolated runtime."""

    matches = list(_SECTION_RE.finditer(text))
    output: list[str] = []
    cursor = 0
    for index, match in enumerate(matches):
        section = match.group(1).strip().strip('"')
        if not section.startswith("sources."):
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        output.append(text[cursor : match.start()])
        block = text[match.start() : end]
        block = re.sub(r"(?m)^(\s*enabled\s*=\s*).*$", r"\g<1>false", block, count=1)
        output.append(block)
        cursor = end
    output.append(text[cursor:])
    return "".join(output)


def _disable_legacy_discovery(text: str) -> str:
    """Disable upstream keyword/web discovery; NewsClaw owns its source loop."""

    discovery_span = _section_span(text, "discovery")
    if discovery_span is None:
        return text
    start, end = discovery_span
    block = text[start:end]
    for key in ("unified_keyword_planner_enabled", "inspiration_search_enabled"):
        block = re.sub(
            rf"(?m)^(\s*{re.escape(key)}\s*=\s*).*$",
            r"\g<1>false",
            block,
            count=1,
        )
    return text[:start] + block + text[end:]


def prepare_runtime(
    *,
    template_path: Path,
    source_config_path: Path,
    runtime_root: Path,
    port: int,
) -> Path:
    template = template_path.read_text(encoding="utf-8")
    source = source_config_path.read_text(encoding="utf-8")
    rendered = _replace_section_group(template, source, "llm")
    rendered = _replace_api_value(rendered, "host", '"127.0.0.1"')
    rendered = _replace_api_value(rendered, "port", str(port))
    rendered = _disable_legacy_sources(rendered)
    rendered = _disable_legacy_discovery(rendered)

    runtime_root.mkdir(parents=True, exist_ok=True)
    config_path = runtime_root / "config.toml"
    if config_path.exists():
        shutil.copy2(config_path, runtime_root / "config.last-good.toml")
    temporary = runtime_root / "config.toml.tmp"
    temporary.write_text(rendered, encoding="utf-8")
    os.replace(temporary, config_path)
    return config_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--source-config", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8431)
    args = parser.parse_args()
    prepare_runtime(
        template_path=args.template,
        source_config_path=args.source_config,
        runtime_root=args.runtime_root,
        port=args.port,
    )


if __name__ == "__main__":
    main()
