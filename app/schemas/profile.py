from pydantic import BaseModel


class ProfileExtraction(BaseModel):
    display_name: str | None = None
    new_topic: str | None = None
