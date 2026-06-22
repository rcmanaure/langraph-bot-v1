from typing import Literal

from pydantic import BaseModel


class TriageDecision(BaseModel):
    decision: Literal["rag", "catalog", "human", "off_topic"]
