from pydantic import BaseModel


class AnswerResponse(BaseModel):
    answer: str
    sources: list[str] = []  # populated in T9 for LangSmith trace links
