import os
from typing import Optional
from datetime import datetime, timezone
from fastapi import FastAPI, BackgroundTasks, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import resend
from models import ProspectCreate, ProspectUpdate, Prospect, ProspectListResponse, ProspectStats
from database import get_db
from research import run_research

load_dotenv()

app = FastAPI(title="ProspectAI API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.environ.get("CORS_ORIGIN", "*")],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/prospects", response_model=Prospect, status_code=201)
def create_prospect(body: ProspectCreate, background_tasks: BackgroundTasks):
    db = get_db()
    result = db.table("prospects").insert({
        "name": body.name,
        "linkedin_url": body.linkedin_url,
        "x_url": body.x_url,
        "email": body.email,
        "notes": body.notes,
        "reference_links": body.reference_links,
        "linkedin_mode": body.linkedin_mode,
        "status": "pending",
    }).execute()

    prospect = result.data[0]
    background_tasks.add_task(run_research, prospect["id"], body.model_dump())
    return prospect


@app.get("/prospects/stats", response_model=ProspectStats)
def get_stats():
    db = get_db()
    result = db.table("prospects").select("status").execute()
    counts = {"all": 0, "pending": 0, "researching": 0, "complete": 0, "failed": 0}
    for row in result.data:
        counts["all"] += 1
        s = row["status"]
        if s in counts:
            counts[s] += 1
    return counts


@app.get("/prospects", response_model=ProspectListResponse)
def list_prospects(
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
):
    db = get_db()
    query = db.table("prospects").select("*", count="exact")
    if status and status != "all":
        query = query.eq("status", status)
    if search:
        query = query.ilike("name", f"%{search}%")
    offset = (page - 1) * page_size
    result = query.order("created_at", desc=True).range(offset, offset + page_size - 1).execute()
    total = result.count or 0
    return {
        "data": result.data,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
    }


@app.get("/prospects/{prospect_id}", response_model=Prospect)
def get_prospect(prospect_id: str):
    db = get_db()
    result = db.table("prospects").select("*").eq("id", prospect_id).single().execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Prospect not found")
    return result.data


@app.patch("/prospects/{prospect_id}", response_model=Prospect)
def update_prospect(prospect_id: str, body: ProspectUpdate):
    db = get_db()
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    result = db.table("prospects").update(updates).eq("id", prospect_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Prospect not found")
    return result.data[0]


@app.post("/prospects/{prospect_id}/retry", response_model=Prospect)
def retry_research(prospect_id: str, background_tasks: BackgroundTasks):
    db = get_db()
    result = db.table("prospects").select("*").eq("id", prospect_id).single().execute()
    prospect = result.data
    if not prospect:
        raise HTTPException(status_code=404, detail="Prospect not found")
    if prospect["status"] != "failed":
        raise HTTPException(status_code=400, detail="Only failed prospects can be retried")

    updated = db.table("prospects").update({
        "status": "pending",
        "error_message": None,
        "research_summary": None,
        "linkedin_connection_request": None,
        "linkedin_message": None,
        "cold_email_subject": None,
        "cold_email_body": None,
    }).eq("id", prospect_id).execute()

    data = {
        "name": prospect["name"],
        "linkedin_url": prospect["linkedin_url"],
        "x_url": prospect["x_url"],
        "email": prospect["email"],
        "notes": prospect["notes"],
        "reference_links": prospect["reference_links"] or [],
        "linkedin_mode": prospect.get("linkedin_mode", "message"),
    }
    background_tasks.add_task(run_research, prospect_id, data)
    return updated.data[0]


@app.post("/prospects/{prospect_id}/send-email", response_model=Prospect)
def send_email(prospect_id: str):
    db = get_db()
    result = db.table("prospects").select("*").eq("id", prospect_id).single().execute()
    prospect = result.data
    if not prospect:
        raise HTTPException(status_code=404, detail="Prospect not found")
    if prospect["email_sent"]:
        raise HTTPException(status_code=409, detail="Email already sent")
    if prospect["status"] != "complete":
        raise HTTPException(status_code=400, detail="Research not complete yet")
    if not prospect.get("email"):
        raise HTTPException(status_code=400, detail="No recipient email address on this prospect")
    if not prospect.get("cold_email_subject") or not prospect.get("cold_email_body"):
        raise HTTPException(status_code=400, detail="No generated email content found")

    resend.api_key = os.environ["RESEND_API_KEY"]
    from_email = os.environ["RESEND_FROM_EMAIL"]

    resend.Emails.send({
        "from": from_email,
        "to": prospect["email"],
        "subject": prospect["cold_email_subject"],
        "text": prospect["cold_email_body"],
    })

    updated = db.table("prospects").update({
        "email_sent": True,
        "email_sent_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", prospect_id).execute()

    return updated.data[0]


@app.get("/health")
def health():
    return {"status": "ok"}
