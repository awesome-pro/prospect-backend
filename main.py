import os
from datetime import datetime, timezone
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import resend
from models import ProspectCreate, Prospect
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
        "status": "pending",
    }).execute()

    prospect = result.data[0]
    background_tasks.add_task(run_research, prospect["id"], body.model_dump())
    return prospect


@app.get("/prospects", response_model=list[Prospect])
def list_prospects():
    db = get_db()
    result = db.table("prospects").select("*").order("created_at", desc=True).execute()
    return result.data


@app.get("/prospects/{prospect_id}", response_model=Prospect)
def get_prospect(prospect_id: str):
    db = get_db()
    result = db.table("prospects").select("*").eq("id", prospect_id).single().execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Prospect not found")
    return result.data


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
