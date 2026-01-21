import os
from fastapi import FastAPI,HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from azure.identity import ClientSecretCredential, DefaultAzureCredential, get_bearer_token_provider
from azure.ai.projects import AIProjectClient
import requests

import psycopg2
from psycopg2.extras import RealDictCursor
from typing import Optional, List, Dict
from uuid import UUID
from sqlalchemy.orm import Session
from models import AgentPhoneScopeMap
from supabase import create_client
from database import get_db
import json
from openai import OpenAI
from dotenv import load_dotenv

# -------------------------------------------------
# FastAPI + CORS SETUP
# -------------------------------------------------

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],        # for dev; restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------------------------
# AZURE CONFIG (from Suresh)
# -------------------------------------------------


load_dotenv()

tenant_id = os.getenv("AZURE_TENANT_ID")
client_id = os.getenv("AZURE_CLIENT_ID")
client_secret = os.getenv("AZURE_CLIENT_SECRET")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
API_KEY = os.getenv("API_KEY")

myEndpoint = "https://suresh-3120-multiplyfinancials-r.services.ai.azure.com/api/projects/suresh-3120-multiplyfinancials"

# Project endpoint (without the stray %22 at the end)
AZURE_PROJECT_ENDPOINT = (
    "https://suresh-3120-multiplyfinancials-r.services.ai.azure.com"
    "/api/projects/suresh-3120-multiplyfinancials"
)

# Known Azure agent names
AZURE_PERSONA_NAME = "03-Personas"
AZURE_FINANCIALS_NAME = "01-Financials"  # version is 01-Financials:3


# Map frontend agent IDs -> Azure agent names
FRONTEND_TO_AZURE_AGENT_NAME = {
    "persona": AZURE_PERSONA_NAME,
    "financials": AZURE_FINANCIALS_NAME,
    # until Industry / Outlook agents exist, point them to Financials or Persona
    "industry": AZURE_FINANCIALS_NAME,
    "outlook": AZURE_FINANCIALS_NAME,
}

# Default agent if frontend doesn't send one
DEFAULT_AGENT_NAME = AZURE_PERSONA_NAME

# -------------------------------------------------
# AZURE CLIENT SETUP (SERVICE PRINCIPAL)
# -------------------------------------------------
# Using the values Suresh gave you. For local dev this is OK;
# later we can move them back into environment variables.

credential = ClientSecretCredential(
    tenant_id=tenant_id,
    client_id=client_id,
    client_secret=client_secret,
)
project_client = AIProjectClient(
    endpoint= myEndpoint,
    credential=credential,
)

openai_client = project_client.get_openai_client()
# token_provider = get_bearer_token_provider(
#     credential,
#     "https://ai.azure.com/.default"
# )
# print("token", token_provider())

SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

API_KEY = os.getenv("API_KEY")
BASE_URL = os.getenv("ENDPOINT")
# -------------------------------------------------
# REQUEST / RESPONSE MODELS
# -------------------------------------------------
def call_agent(agent_name: str, message: str):
    url = f"{BASE_URL}/responses"
    print("url---", url)
    headers = {
        "api-key": API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "input": [{"role": "user", "content": message}],
        "extra_body": {"agent": {"name": agent_name, "type": "agent_reference"}}
    }

    response = requests.post(url, headers=headers, json=payload)
    print(response.status_code, response.text)


class ChatBody(BaseModel):
    message: str
    # Frontend sends: "persona" | "financials" | "industry" | "outlook"
    agentId: str | None = None
class AgentCreateRequest(BaseModel):
    tenant_id: str = None
    phone_e164: str
    agent_id: str
    agent_name: Optional[str] = None
    allowed_scopes: List[str]
    scope_meta: Optional[dict] = None
    client_id: Optional[str] = None
    api_key_id: Optional[str] = None
    x509_cert_fingerprint: Optional[str] = None
    public_key_fingerprint: Optional[str] = None
    endpoint_url: Optional[str] = None
    endpoint_protocol: Optional[str] = None
    endpoint_port: Optional[int] = None
    endpoint_path: Optional[str] = None
    endpoint_tls_fingerprint: Optional[str] = None
    endpoint_meta: Optional[dict] = None
    reverse_learning_allowed: Optional[bool] = False
    agent_status: Optional[str] = "active"
    metadata: Optional[dict] = None

class AgentResponse(BaseModel):
    id: UUID
    tenant_id: str | None
    phone_e164: str
    agent_id: List[str]
    agent_name: List[str]
    allowed_scopes: List[str]
    endpoint_url: Optional[str] = None
    endpoint_protocol: Optional[str] = None
    endpoint_port: Optional[int] = None
    endpoint_path: Optional[str] = None
    agent_status: str

    class Config:
        from_attributes = True

class AgentPhoneRequest(BaseModel):
    phone: str

class ChatRequest(BaseModel):
    message: str
# -------------------------------------------------
# HELPERS
# -------------------------------------------------

# client = FoundryAgentChat(
#     endpoint="https://suresh-3120-multiplyfinancials-r.services.ai.azure.com",
#     credential=credential
# )
# agent_request = {
#     "input": [{"role": "user", "content": "Tell me a joke"}]
# }
# response = project_client.agents.send_request(
#     agent_id="01-Persona:2",
#     request=agent_request
# )

# print(response)

def get_valid_agent_name(agent_id: str):
    """Validate agentId and return the exact Azure agent name if it exists"""
    agents = project_client.agents.list()
    for a in agents:
        if a.name.lower() == agent_id.lower():  # case-insensitive match
            return a.name
    return None  # invalid agent


def get_db_connection():
    return psycopg2.connect(
        host="localhost",
        port=5433,
        database="multipiai",
        user="postgres",
        password="abhi"
    )

def _resolve_agent_name_from_frontend_value(frontend_value: str | None) -> str:
    """
    Convert frontend ids (persona, financials, industry, outlook)
    into Azure agent *names* (01-Persona, 01-Financials, ...).
    Fallback to DEFAULT_AGENT_NAME if unknown or None.
    """
    if frontend_value is None:
        return DEFAULT_AGENT_NAME
    return FRONTEND_TO_AZURE_AGENT_NAME.get(frontend_value, DEFAULT_AGENT_NAME)

def fetch_agents_from_azure(phone_number: str):
    import json

    prompt = f"Given the phone number {phone_number}, return the list of agents for this user in JSON format."

    response = openai_client.responses.create(
        model="gpt-4.1-mini",
        input=[{"role": "user", "content": f"List all agents for phone {phone_number}"}]
        # Don't specify a default agent here
    )

    # Assuming the response is JSON with agent names
    import json
    agents_text = response.output_text.strip()
    try:
      agents_list = json.loads(agents_text)  # try JSON first
    except json.JSONDecodeError:
      agents_list = [agent.strip() for agent in agents_text.split(",")]

def save_agents_to_db(db: Session, phone_number: str, agents: list[str]):
    from models import UserAgents
    for agent_name in agents:
        db.add(UserAgents(phone_number=phone_number, agent_name=agent_name))
    db.commit()

def fetch_all_agents_from_azure():
    """
    Fetches all agents deployed in the Azure project.
    Returns a list of agent names.
    """
    agents_list = []
    agents_phone = []
    
    # Get all agents from the Azure project
    agents = project_client.agents.list_agents()  # Make sure project_client is initialized
    print("Fetched agents:", agents)
    for agent in agents:
        print("dict", agent.__dict__)
        agents_list.append(agent.name)  # Each agent object has a 'name'
        print("agents_list", agents_list)
    
    return agents_list
def save_agents_to_db(db: Session, agents_list: list):
    """
    Saves all fetched agents to DB.
    """
    print(f"Saving {len(agents_list)} agents to DB...", agents_list)
    for agent_name in agents_list:
        print(f"- {agent_name}")
    # Optional: Clear old agents first
    db.query(UserAgents).delete()
    
    for agent_name in agents_list:
        db_agent = UserAgents()  # phone is None since this is global
        db.add(db_agent)
    
    db.commit()
def get_agents_by_phone(db, phone: str):
    return (
        db.query(AgentPhoneScopeMap)
        .filter(
            AgentPhoneScopeMap.phone_e164 == phone,
            AgentPhoneScopeMap.is_deleted == false,
            AgentPhoneScopeMap.agent_status == "active"
        )
        .all()
    )

def parse_list(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return [value]
    elif isinstance(value, list):
        return value
    return []

# -------------------------------------------------
# ROUTES
# -------------------------------------------------

@app.get("/deployments")
def list_deployments():
    try:
        deployments = project_client.deployments.list()
        return [
            {"name": d.name, "model": d.model_name}
            for d in deployments
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
async def root():
    return {"status": "ok", "message": "MultipIAI backend is running"}


# @app.post("/chat")
# async def chat(body: ChatBody):
#     """
#     Receives: { "message": "...", "agentId": "persona|financials|industry|outlook" }
#     Uses the mapped Azure agent and returns: { "reply": "..." }
#     """
#     try:
#         # 1) Map frontend agentId -> Azure agent name
#         # agent_name = _resolve_agent_name_from_frontend_value(body.agentId)
#         # print("agent_name: ", agent_name)

#         agent_obj = project_client.agents.get_agent(agent_id=body.agentId)

#         # Convert to dict for JSON
#         agent_info = {
#             "id": agent_obj.id,
#             "name": agent_obj.name,
#             "model": agent_obj.model,
#             "instructions": agent_obj.instructions,
#             "created_at": agent_obj.created_at,
#         }
#         response = openai_client.responses.create(
#             input=[{"role": "user", "content": body.message}],
#             extra_body={"agent": {"name": agent_info.id, "type": "agent_reference"}},
#         )

#         print("response: ", response.output_text)


#         # 4) Return the plain text output
#         return {
#             "agent": agent_info,
#             "reply": response.output_text
#         }

#     except Exception as e:
#         # If Azure fails, don't crash – just send the message back to the client
#         print("Azure error:", repr(e))
#         return {"reply": f"Azure error: {e}"}


@app.post("/get-user-agents", response_model=AgentResponse)
def get_user_agents(request: AgentPhoneRequest):
    existing = supabase.table("agent_phone_scope_map") \
                       .select("*") \
                       .eq("phone_e164", request.phone) \
                       .execute()

    if not existing.data:
        return []

    record = existing.data[0]
    print("record", record)
    return AgentResponse(
        id=UUID(record["id"]),
        tenant_id=record.get("tenant_id"),
        phone_e164=record["phone_e164"],
        agent_id=json.loads(record.get("agent_id") or "[]"),
        agent_name=json.loads(record.get("agent_name") or "[]"),
        allowed_scopes=record.get("allowed_scopes") or [],
        endpoint_url=record.get("endpoint_url"),
        endpoint_protocol=record.get("endpoint_protocol"),
        endpoint_port=record.get("endpoint_port"),
        endpoint_path=record.get("endpoint_path"),
        agent_status=record.get("agent_status"),
    )


# @app.post("/get-agents")
# def get_agents(request: AgentsRequest, db: Session = Depends(get_db)):
#     phone = request.phone
#     try:
#         agents_list = fetch_agents_from_azure(phone)  # Step 1
#         save_agents_to_db(db, phone, agents_list)     # Step 2
#         return {"phone": phone, "agents": agents_list}
#     except Exception as e:
#         return {"error": str(e)}

@app.post("/get-all-agents")
def get_all_agents(db: Session = Depends(get_db)):
    try:
        agents_list = fetch_all_agents_from_azure()
        # save_agents_to_db(db, agents_list)
        return {"agents": agents_list}
    except Exception as e:
        return {"error": str(e)}

@app.post("/agents", response_model=AgentResponse)
def create_agent(payload: AgentCreateRequest):
    existing = supabase.table("agent_phone_scope_map").select("*").eq("phone_e164", payload.phone_e164).execute()
    if existing.data and len(existing.data) > 0:
        record = existing.data[0]
        # Convert agent_id and agent_name from string to list if needed
        agent_ids = parse_list(record.get("agent_id"))
        agent_names = parse_list(record.get("agent_name"))

        new_id = payload.agent_id.strip()
        if new_id not in agent_ids:
            agent_ids.append(new_id)

        if payload.agent_name:
            new_name = payload.agent_name.strip()
            if new_name not in agent_names:
                agent_names.append(new_name)

        updated = supabase.table("agent_phone_scope_map").update({
            "agent_id": agent_ids,
            "agent_name": agent_names,
            "allowed_scopes": payload.allowed_scopes,
            "endpoint_url": payload.endpoint_url,
            "endpoint_protocol": payload.endpoint_protocol,
            "endpoint_port": payload.endpoint_port,
            "endpoint_path": payload.endpoint_path,
            "reverse_learning_allowed": payload.reverse_learning_allowed,
            "agent_status": payload.agent_status
        }).eq("phone_e164", payload.phone_e164).execute()

        record = updated.data[0]

    else:
        record = {
            "tenant_id": str(payload.tenant_id) if payload.tenant_id else None,
            "phone_e164": payload.phone_e164,
            "agent_id": [payload.agent_id.strip()],
            "agent_name": [payload.agent_name.strip()] if payload.agent_name else [],
            "allowed_scopes": payload.allowed_scopes,
            "endpoint_url": payload.endpoint_url,
            "endpoint_protocol": payload.endpoint_protocol,
            "endpoint_port": payload.endpoint_port,
            "endpoint_path": payload.endpoint_path,
            "reverse_learning_allowed": payload.reverse_learning_allowed,
            "agent_status": payload.agent_status
        }
        inserted = supabase.table("agent_phone_scope_map").insert(record).execute()
        record = inserted.data[0]

    # Ensure lists are proper Python lists
    record["agent_id"] = parse_list(record.get("agent_id"))
    record["agent_name"] = parse_list(record.get("agent_name"))

    return record

class FoundryAgentChat:
    def __init__(self, project_name, agent_id, endpoint, api_version="2025-12-01"):
        self.project_name = project_name
        self.agent_id = agent_id
        self.endpoint = endpoint
        self.api_version = api_version
        self.token = self._get_access_token()

    def _get_access_token(self):
        token_provider = get_bearer_token_provider(
            credential,
            "https://ai.azure.com/.default"
        )
        print("token", token_provider())
        return token_provider()

    def _create_thread(self):
        url = f"{self.endpoint}/api/projects/{self.project_name}/threads?api-version={self.api_version}"
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        body = {
            "display_name": "MyConversation"
        }
        resp = requests.post(url, headers=headers, json=body)
        print("resp", resp)
        resp.raise_for_status()
        return resp

    def _send_message(self, thread_id, message):
        url = f"{self.endpoint}/api/projects/{self.project_name}/threads/{thread_id}/messages?api-version={self.api_version}"
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        payload = {"role": "user", "content": message}
        resp = requests.post(url, headers=headers, json=payload)
        resp.raise_for_status()

    def _run_agent(self, thread_id):
        url = f"{self.endpoint}/api/projects/{self.project_name}/threads/{thread_id}/runs?api-version={self.api_version}"
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        payload = {"agent_id": self.agent_id}
        resp = requests.post(url, headers=headers, json=payload)
        resp.raise_for_status()

    def _get_agent_response(self, thread_id):
        url = f"{self.endpoint}/api/projects/{self.project_name}/threads/{thread_id}/messages?api-version={self.api_version}"
        headers = {"Authorization": f"Bearer {self.token}"}
        resp = requests.get(url, headers=headers)
        resp.raise_for_status()
        messages = resp.json().get("messages", [])
        for msg in messages:
            if msg["role"] == "assistant":
                return msg["content"]
        return "No response from agent."

    # -------------------------
    # Public method
    # -------------------------
    def chat(self, message: str):
        thread_id = self._create_thread()
        print("thread_id", thread_id)
        self._send_message(thread_id, message)
        self._run_agent(thread_id)
        return self._get_agent_response(thread_id)

# PROJECT_NAME = "suresh-3120-multiplyfinancials"
# AGENT_ID = "01-Persona:2"
# ENDPOINT = "https://suresh-3120-multiplyfinancials-r.services.ai.azure.com"

# agent = FoundryAgentChat(PROJECT_NAME, AGENT_ID, ENDPOINT)
# reply = agent.chat("Hello! Can you introduce yourself?")
# print("Agent Reply:", reply)

# @app.post("/chat")
# def chat(req: ChatRequest):
#     try:
#         reply = agent.chat(req.message)
#         return {"reply": reply}
#     except Exception as e:
#         return {"error": str(e)}

@app.post("/chat")
async def chat(body: ChatBody):
    """
    Receives: { "message": "...", "agentId": "persona|financials|industry|outlook" }
    Uses the mapped Azure agent and returns: { "reply": "..." }
    """
    try:
        # 1) Map frontend agentId -> Azure agent name
        agent_name = _resolve_agent_name_from_frontend_value(body.agentId)
        print("agent_name: ", agent_name)

        # 2) Get existing agent from Azure
        agent = project_client.agents.get_agent(agent_id=agent_name)
        print("agent: ", agent)

        # 3) Call responses API with agent reference
        response = openai_client.responses.create(
            input=[{"role": "user", "content": body.message}],
            extra_body={"agent": {"name": agent.name, "type": "agent_reference"}},
        )

        # 4) Return the plain text output
        return {"reply": response.output_text}

    except Exception as e:
        # If Azure fails, don't crash – just send the message back to the client
        print("Azure error:", repr(e))
        return {"reply": f"Azure error: {e}"}
