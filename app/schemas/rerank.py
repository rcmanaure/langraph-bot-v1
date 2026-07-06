from pydantic import BaseModel


class RerankResult(BaseModel):
    ranked_indices: list[int]
