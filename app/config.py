from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "M.Y. Business Hotel HR"
    app_env: str = "development"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    database_url: str = "postgresql+psycopg://hr_app:CHANGE_ME@127.0.0.1:5432/my_business_hotel_hr"
    session_secret: str = "CHANGE_THIS_TO_A_LONG_RANDOM_SECRET"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
