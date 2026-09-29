from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DbSession

from app.api.v1.deps import current_session, db_session, owned_job
from app.core.config import settings
from app.core.ids import chat_id as new_chat_id
from app.db.models import ChatMessage, ChatSession, Session
from app.services.llm.analyst import ask
from app.services.llm.tools import REGISTRY

router = APIRouter(tags=["chat"])

SUGGESTIONS = [
    "Summarise this scene.",
    "Compare the models and explain the difference.",
    "What quality metrics are available, and why?",
    "Where is the reconstruction least certain?",
    "Run a vegetation analysis.",
    "What does SAM measure?",
]


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


@router.get("/chat/capabilities")
def capabilities() -> dict[str, Any]:
    return {
        "enabled": settings.llm_enabled,
        "provider": settings.LLM_PROVIDER if settings.llm_enabled else None,
        "mode": "llm" if settings.llm_enabled else "template",
        "notice": (None if settings.llm_enabled else
                   "No language model is configured, so the analyst answers with a direct "
                   "readout of the underlying data. Set LLM_API_KEY and LLM_PROVIDER in "
                   "your .env to enable written explanations."),
        "tools": [{"name": t["name"], "description": t["description"]} for t in REGISTRY.values()],
        "suggestions": SUGGESTIONS,
        "guarantee": ("The analyst may only state numbers returned by these tools. Every "
                      "answer lists the data it used."),
    }


@router.post("/jobs/{job_id}/chat")
def chat(payload: ChatRequest, job=Depends(owned_job), db: DbSession = Depends(db_session),
         sess: Session = Depends(current_session)) -> dict[str, Any]:
    cs = (db.query(ChatSession).filter(ChatSession.job_id == job.id,
                                       ChatSession.session_id == sess.id).first())
    if not cs:
        cs = ChatSession(id=new_chat_id(), session_id=sess.id, job_id=job.id)
        db.add(cs)
        db.commit()

    prior = (db.query(ChatMessage).filter(ChatMessage.chat_session_id == cs.id)
             .order_by(ChatMessage.created_at).all())
    history = [{"role": m.role, "content": m.content} for m in prior[-8:]
               if m.role in ("user", "assistant") and m.content]

    db.add(ChatMessage(chat_session_id=cs.id, role="user", content=payload.message))
    db.commit()

    result = ask(db, job, payload.message, history)

    db.add(ChatMessage(chat_session_id=cs.id, role="assistant", content=result["answer"],
                       tool_calls=[{"tool": t["tool"], "input": t["input"]}
                                   for t in result["tool_calls"]],
                       tool_results=[{"tool": t["tool"]} for t in result["tool_calls"]]))
    db.commit()

    return {"chat_session_id": cs.id, "answer": result["answer"],
            "llm_used": result["llm_used"],
            "degraded_reason": result.get("degraded_reason"),
            "tool_calls": result["tool_calls"], "provenance": result["provenance"]}


@router.get("/jobs/{job_id}/chat")
def history(job=Depends(owned_job), db: DbSession = Depends(db_session),
            sess: Session = Depends(current_session)) -> dict[str, Any]:
    cs = (db.query(ChatSession).filter(ChatSession.job_id == job.id,
                                       ChatSession.session_id == sess.id).first())
    if not cs:
        return {"messages": [], "suggestions": SUGGESTIONS}
    rows = (db.query(ChatMessage).filter(ChatMessage.chat_session_id == cs.id)
            .order_by(ChatMessage.created_at).all())
    return {"chat_session_id": cs.id, "suggestions": SUGGESTIONS,
            "messages": [{"role": m.role, "content": m.content,
                          "tool_calls": m.tool_calls,
                          "created_at": m.created_at.isoformat()} for m in rows]}
