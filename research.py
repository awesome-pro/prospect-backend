import os
import json
import logging
from xai_sdk import Client
from xai_sdk.chat import user, system
from xai_sdk.tools import web_search
from database import get_db
from system_prompt import CONNECTION_REQUEST_SYSTEM_PROMPT, MESSAGE_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


def _grok_client() -> Client:
    return Client(api_key=os.environ["XAI_API_KEY"])


def _build_research_prompt(data: dict) -> str:
    parts = [
        "Research this person thoroughly for professional outreach:",
        f"Name: {data['name']}",
    ]
    if data.get("linkedin_url"):
        parts.append(f"LinkedIn: {data['linkedin_url']}")
    if data.get("x_url"):
        parts.append(f"X/Twitter: {data['x_url']}")
    if data.get("email"):
        parts.append(f"Email: {data['email']}")
    if data.get("notes"):
        parts.append(f"Sender's notes about this person: {data['notes']}")
    if data.get("reference_links"):
        parts.append("Reference links (read and summarize each one):")
        for link in data["reference_links"]:
            parts.append(f"  - {link}")

    parts.append("""
Find and report in detail:
1. Professional background, current role, key responsibilities
2. Recent LinkedIn posts, X/Twitter activity, blog posts, interviews — specific topics, exact titles or quotes where possible, dates
3. Current company: what it builds, tech stack, recent news, funding, open roles, hiring signals
4. Opinions they hold strongly, problems they're publicly wrestling with, ideas they've championed
5. Any context from the reference links above

Prioritize specifics over summaries. A verbatim post title beats "they post about AI." This research drives highly personalized outreach — every vague generality wastes the opportunity.
""")
    return "\n".join(parts)


def _build_generation_user_message(research_text: str, prospect_name: str, linkedin_mode: str) -> str:
    if linkedin_mode == "connection_request":
        json_shape = '{"linkedin_connection_request": "...", "cold_email_subject": "...", "cold_email_body": "...", "warnings": ""}'
    else:
        json_shape = '{"linkedin_message": "...", "cold_email_subject": "...", "cold_email_body": "...", "warnings": ""}'

    return f"""Here is the research on {prospect_name}:

{research_text}

Generate the outreach now. Return ONLY valid JSON matching this shape, nothing else:
{json_shape}"""


def run_research(prospect_id: str, data: dict) -> None:
    db = get_db()

    try:
        db.table("prospects").update({"status": "researching"}).eq("id", prospect_id).execute()

        client = _grok_client()

        # Step 1: Research with live web search
        research_prompt = _build_research_prompt(data)
        research_chat = client.chat.create(
            model="grok-4.3",
            tools=[web_search()],
        )
        research_chat.append(user(research_prompt))
        research_response = research_chat.sample()
        research_text = research_response.content

        # Step 2: Generate outreach — system prompt is fully mode-specific, no rule bleed
        linkedin_mode = data.get("linkedin_mode", "message")
        sys_prompt = (
            CONNECTION_REQUEST_SYSTEM_PROMPT
            if linkedin_mode == "connection_request"
            else MESSAGE_SYSTEM_PROMPT
        )
        gen_message = _build_generation_user_message(research_text, data["name"], linkedin_mode)

        gen_chat = client.chat.create(model="grok-4.3")
        gen_chat.append(system(sys_prompt))
        gen_chat.append(user(gen_message))
        gen_response = gen_chat.sample()
        raw = gen_response.content.strip()

        # Strip markdown fences if model wrapped output
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        outputs = json.loads(raw)

        if outputs.get("warnings"):
            logger.warning("Generation warnings for prospect %s: %s", prospect_id, outputs["warnings"])

        conn_req = None
        linkedin_msg = None
        if linkedin_mode == "connection_request":
            conn_req = outputs.get("linkedin_connection_request", "")
            if len(conn_req) > 300:
                conn_req = conn_req[:297] + "..."
        else:
            linkedin_msg = outputs.get("linkedin_message", "")

        db.table("prospects").update({
            "status": "complete",
            "research_summary": research_text,
            "linkedin_connection_request": conn_req,
            "linkedin_message": linkedin_msg,
            "cold_email_subject": outputs.get("cold_email_subject", ""),
            "cold_email_body": outputs.get("cold_email_body", ""),
        }).eq("id", prospect_id).execute()

    except Exception as e:
        logger.exception("Research failed for prospect %s", prospect_id)
        db.table("prospects").update({
            "status": "failed",
            "error_message": str(e),
        }).eq("id", prospect_id).execute()
