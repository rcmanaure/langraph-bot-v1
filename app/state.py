from typing import Annotated
from typing_extensions import TypedDict
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    tenant_id: str       # slug; never the full TenantConfig (secrets not checkpointed)
    thread_id: str       # tenant:{slug}:user:{id}:channel:{channel}(:vN)
    messages: Annotated[list[BaseMessage], add_messages]
    retrieved_chunks: list[dict]
    triage_decision: str  # "rag" | "catalog" | "human" | "off_topic"
    answer: str
