from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


@dataclass(frozen=True)
class Settings:
    intervals_api_key: str
    intervals_athlete_id: str
    data_dir: Path
    tz: str
    turso_url: str = ""
    turso_token: str = ""

    @property
    def db_path(self) -> Path:
        return self.data_dir / "rx.db"

    @property
    def garmin_token_dir(self) -> Path:
        return self.data_dir / "garmin_tokens"


def settings() -> Settings:
    data_dir = Path(os.getenv("RX_DATA_DIR", "./data")).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        intervals_api_key=os.getenv("INTERVALS_API_KEY", "").strip(),
        intervals_athlete_id=os.getenv("INTERVALS_ATHLETE_ID", "0").strip() or "0",
        data_dir=data_dir,
        tz=os.getenv("RX_TZ", "America/Los_Angeles"),
        turso_url=os.getenv("TURSO_DATABASE_URL", "").strip(),
        turso_token=os.getenv("TURSO_AUTH_TOKEN", "").strip(),
    )
