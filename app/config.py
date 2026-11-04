from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import model_validator


class Settings(BaseSettings):
    app_name: str = "M.Y. Business Hotel HR"
    app_env: str = "development"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    database_url: str = "postgresql+psycopg://hr_app:CHANGE_ME@127.0.0.1:5432/my_business_hotel_hr"
    # Required: provide a unique random value through the SESSION_SECRET environment variable.
    session_secret: str

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def validate_session_secret(self):
        secret = self.session_secret.strip()
        if (
            len(secret) < 32
            or secret == "CHANGE_THIS_TO_A_LONG_RANDOM_SECRET"
            or secret.lower().startswith("change_me")
        ):
            raise ValueError(
                "SESSION_SECRET must be a unique random value of at least 32 characters; "
                "the example/default placeholder is not allowed."
            )
        self.session_secret = secret
        return self


settings = Settings()
