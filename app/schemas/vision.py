from pydantic import BaseModel


class VisionExtraction(BaseModel):
    is_legible: bool
    price_question: str | None = None


class VisionVerification(BaseModel):
    text_visible: bool
