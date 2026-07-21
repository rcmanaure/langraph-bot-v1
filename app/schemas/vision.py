from pydantic import BaseModel


class VisionExtraction(BaseModel):
    is_legible: bool
    procedure_name: str | None = None  # bare literal term as written, e.g. "IGRA", "zapatilla talla 42" — verified against this
    price_question: str | None = None  # customer-facing formatted question, e.g. "¿Cuánto cuesta un examen de IGRA?"


class VisionVerification(BaseModel):
    text_visible: bool
