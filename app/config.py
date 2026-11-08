from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "M.Y. Business Hotel HR"
    app_env: str = "development"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    database_url: str
    session_secret: str

    report_email_to: str = ""
    report_smtp_host: str = ""
    report_smtp_port: int = 587
    report_smtp_username: str = ""
    report_smtp_password: str = ""
    report_smtp_starttls: bool = True

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def validate_security_settings(self):
        url = self.database_url.strip()
        if not url or "change_me" in url.lower():
            raise ValueError("DATABASE_URL must be explicitly configured; CHANGE_ME placeholders are forbidden.")
        if not url.startswith("postgresql+psycopg://"):
            raise ValueError("DATABASE_URL must use the installed PostgreSQL psycopg driver.")
        self.database_url = url
        secret = self.session_secret.strip()
        if len(secret) < 32 or secret == "CHANGE_THIS_TO_A_LONG_RANDOM_SECRET" or secret.lower().startswith("change_me"):
            raise ValueError("SESSION_SECRET must be a unique random value of at least 32 characters.")
        self.session_secret = secret
        return self


settings = Settings()
