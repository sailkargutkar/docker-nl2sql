from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    db_host: str = "postgres_primary"
    db_port: int = 5432
    db_name: str = "tmt"
    db_username: str = "nl2sql_reader"
    db_password: str = ""

    # Optional overrides — when set, win over db_name/db_username/db_password.
    nl2sql_db_name: str = ""
    nl2sql_db_username: str = ""
    nl2sql_db_password: str = ""

    max_rows: int = 500
    statement_timeout_ms: int = 15000

    schema_file: str = "schema/tmt_schema.yml"
    history_db: str = "/data/history.db"

    # Local NL→SQL model (intent classifier) persisted here. Retrained from
    # /data/history.db successes + built-in seed examples.
    intent_model_path: str = "/data/intent.joblib"

    # NLTK corpora path — baked into the image at build time.
    nltk_data_path: str = "/opt/nltk_data"

    @property
    def effective_db_name(self) -> str:
        return self.nl2sql_db_name or self.db_name

    @property
    def effective_db_username(self) -> str:
        return self.nl2sql_db_username or self.db_username

    @property
    def effective_db_password(self) -> str:
        return self.nl2sql_db_password or self.db_password

    @property
    def db_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.effective_db_username}:{self.effective_db_password}"
            f"@{self.db_host}:{self.db_port}/{self.effective_db_name}"
        )

    @property
    def registry_db(self) -> str:
        """Registry shares the history SQLite file — one less moving part."""
        return self.history_db


settings = Settings()
