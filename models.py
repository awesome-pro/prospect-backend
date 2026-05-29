from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class ProspectCreate(BaseModel):
    name: str
    linkedin_url: Optional[str] = None
    x_url: Optional[str] = None
    email: Optional[str] = None
    notes: Optional[str] = None
    reference_links: list[str] = []


class Prospect(BaseModel):
    id: str
    name: str
    linkedin_url: Optional[str] = None
    x_url: Optional[str] = None
    email: Optional[str] = None
    notes: Optional[str] = None
    reference_links: list[str] = []
    status: str
    research_summary: Optional[str] = None
    linkedin_connection_request: Optional[str] = None
    linkedin_message: Optional[str] = None
    cold_email_subject: Optional[str] = None
    cold_email_body: Optional[str] = None
    error_message: Optional[str] = None
    email_sent: bool = False
    email_sent_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
