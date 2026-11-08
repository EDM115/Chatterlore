from chatterlore.config import Settings


def test_blank_optional_dotenv_values_use_defaults(tmp_path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("RAW_MESSAGE_RETENTION_DAYS=\nKNOWLEDGE_RETENTION_DAYS=\n", encoding="utf-8")

    settings = Settings(_env_file=dotenv)

    assert settings.raw_message_retention_days is None
    assert settings.knowledge_retention_days is None
