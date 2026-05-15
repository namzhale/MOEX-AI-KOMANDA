from arena_bot.config import BotConfig, load_env_file


def test_config_reads_arenago_api_key(monkeypatch):
    monkeypatch.delenv("ARENAGO_API_TOKEN", raising=False)
    monkeypatch.delenv("SANDBOX_API_KEY", raising=False)
    monkeypatch.delenv("POLZA_API_KEY", raising=False)
    monkeypatch.setenv("ARENAGO_API_KEY", "arenago-token")

    config = BotConfig.from_env()

    assert config.api_token == "arenago-token"


def test_sandbox_api_key_is_supported_for_cloud(monkeypatch):
    monkeypatch.delenv("ARENAGO_API_KEY", raising=False)
    monkeypatch.delenv("ARENAGO_API_TOKEN", raising=False)
    monkeypatch.delenv("POLZA_API_KEY", raising=False)
    monkeypatch.setenv("SANDBOX_API_KEY", "sandbox-token")

    config = BotConfig.from_env()

    assert config.api_token == "sandbox-token"


def test_polza_key_is_not_used_as_trading_token(monkeypatch):
    monkeypatch.delenv("ARENAGO_API_KEY", raising=False)
    monkeypatch.delenv("ARENAGO_API_TOKEN", raising=False)
    monkeypatch.delenv("SANDBOX_API_KEY", raising=False)
    monkeypatch.setenv("POLZA_API_KEY", "polza-token")

    config = BotConfig.from_env()

    assert config.api_token is None


def test_load_env_file_sets_missing_values_without_overriding_existing(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "ARENAGO_API_KEY=from-file\n"
        "BOT_NAME=FromFileBot\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("ARENAGO_API_KEY", raising=False)
    monkeypatch.setenv("BOT_NAME", "ExistingBot")

    load_env_file(env_file)

    assert BotConfig.from_env().api_token == "from-file"
    assert BotConfig.from_env().bot_name == "ExistingBot"
