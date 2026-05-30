import os
import json
import logging
from xai_sdk import Client
from xai_sdk.chat import user
from xai_sdk.tools import web_search
from database import get_db
from system_prompt import SYSTEM_PROMPT

logger = logging.getLogger(__name__)


def _grok_client() -> Client:
    return Client(api_key=os.environ["XAI_API_KEY"])


def _build_research_prompt(data: dict) -> str:
    parts = ["I am an agentic AI engineer with the :", f"Name: {data['name']}"]
    if data.get("linkedin_url"):
        parts.append(f"LinkedIn: {data['linkedin_url']}")
    if data.get("x_url"):
        parts.append(f"X/Twitter: {data['x_url']}")
    if data.get("email"):
        parts.append(f"Email: {data['email']}")
    if data.get("notes"):
        parts.append(f"Personal Notes: {data['notes']}")
    if data.get("reference_links"):
        parts.append("Reference links (rea:")
        for link in data["reference_links"]:
            parts.append(f"  - {link}")

    parts.append("""
Find and report in detail:
1. Their professional background, current role, and key responsibilities
2. Recent LinkedIn posts or X/Twitter activity — specific topics they've engaged with
3. Their current company: what it builds, tech stack, recent news, funding rounds, open roles, hiring signals
4. Any public signals of what they value in collaborators, team members, or hires
5. Relevant context from any reference links provided above

Be thorough and specific. This research will drive highly personalized outreach messages.
""")
    return "\n".join(parts)


def _build_generation_prompt(research_text: str, prospect_name: str, linkedin_mode: str) -> str:
    if linkedin_mode == "connection_request":
        linkedin_instruction = "linkedin_connection_request (≤300 characters strict — count carefully). Do NOT include linkedin_message."
        json_shape = '{"linkedin_connection_request": "...", "cold_email_subject": "...", "cold_email_body": "..."}'
    else:
        linkedin_instruction = "linkedin_message (150-250 words). Do NOT include linkedin_connection_request."
        json_shape = '{"linkedin_message": "...", "cold_email_subject": "...", "cold_email_body": "..."}'

    return f"""Here is the research on {prospect_name} and their company:

{research_text}

Now generate the outreach pieces as described. LinkedIn mode: {linkedin_mode}
- Generate {linkedin_instruction}
- cold_email_body should be ≤200 words
- Every piece must reference specific, real details from the research above

Output ONLY this JSON shape, nothing else:
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

        # Step 2: Generate outreach content (system prompt embedded in user message)
        linkedin_mode = data.get("linkedin_mode", "message")
        gen_prompt = _build_generation_prompt(research_text, data["name"], linkedin_mode)
        full_gen_message = f"{SYSTEM_PROMPT}\n\n---\n\n{gen_prompt}"
        gen_chat = client.chat.create(model="grok-4.3")
        gen_chat.append(user(full_gen_message))
        gen_response = gen_chat.sample()
        raw = gen_response.content.strip()

        # Strip markdown fences if model wrapped in them
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        outputs = json.loads(raw)

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
        logger.exception(f"Research failed for prospect {prospect_id}")
        db.table("prospects").update({
            "status": "failed",
            "error_message": str(e),
        }).eq("id", prospect_id).execute()
