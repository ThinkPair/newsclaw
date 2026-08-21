from pathlib import Path

from scripts.prepare_newsclaw_runtime import prepare_runtime


def test_prepare_runtime_copies_only_llm_and_disables_legacy_sources(tmp_path: Path) -> None:
    template = tmp_path / "template.toml"
    template.write_text(
        """[general]
data_dir = "data"

[api]
host = "0.0.0.0"
port = 8420

[llm]
default_chain = ["template"]

[llm.instances.template]
api_key = ""

[network]
mode = "system"

[discovery]
unified_keyword_planner_enabled = true
inspiration_search_enabled = true

[sources.bilibili]
enabled = true

[sources.browser]
enabled = true
command = "agent-browser"
""",
        encoding="utf-8",
    )
    source = tmp_path / "source.toml"
    source.write_text(
        """[llm]
default_chain = ["deepseek"]

[llm.instances.deepseek]
api_key = "secret-value"

[network]
mode = "custom"
""",
        encoding="utf-8",
    )

    target = prepare_runtime(
        template_path=template,
        source_config_path=source,
        runtime_root=tmp_path / "runtime",
        port=8431,
    )
    rendered = target.read_text(encoding="utf-8")

    assert 'host = "127.0.0.1"' in rendered
    assert "port = 8431" in rendered
    assert 'default_chain = ["deepseek"]' in rendered
    assert 'api_key = "secret-value"' in rendered
    assert "[sources.bilibili]\nenabled = false" in rendered
    assert "[sources.browser]\nenabled = false" in rendered
    assert "unified_keyword_planner_enabled = false" in rendered
    assert "inspiration_search_enabled = false" in rendered
    assert 'mode = "custom"' not in rendered
