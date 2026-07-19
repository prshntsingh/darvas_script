from google import genai
import json
import os
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from config import VERTEX_PROJECT_ID, VERTEX_LOCATION

# Initialize a global client
_client = None

def init_vertex():
    global _client
    if not VERTEX_PROJECT_ID:
        return
        
    creds = None
    vertex_creds_env = os.environ.get("VERTEX_CREDENTIALS_JSON")
    
    try:
        if vertex_creds_env:
            creds_info = json.loads(vertex_creds_env)
            if "refresh_token" in creds_info:
                creds = Credentials.from_authorized_user_info(creds_info)
            else:
                creds = service_account.Credentials.from_service_account_info(creds_info)
    except Exception as e:
        print(f"Failed to load user credentials for Vertex: {e}")

    try:
        _client = genai.Client(
            vertexai=True,
            project=VERTEX_PROJECT_ID,
            location=VERTEX_LOCATION,
            credentials=creds
        )
    except Exception as e:
        print(f"Failed to initialize google-genai Client: {e}")

init_vertex()

async def extract_trade_async(text):
    """
    Analyzes the text using Vertex AI Gemini and extracts trade details if present.
    Returns a dictionary or None.
    """
    if not _client:
        print("Vertex AI Client missing. Skipping trade extraction.")
        return None

    # Using the new Google GenAI SDK and stable model identifier
    model_name = "gemini-2.5-flash"
    
    prompt = f"""
    You are an expert financial analyst. Analyze the following Telegram message and determine if it contains a stock or cryptocurrency trade setup.
    If it does contain a trade setup, extract the following details:
    - NAME: The exact ticker symbol for Google Finance. Since these are mostly Indian stocks, if a business name is given (e.g. "Reliance"), map it to the correct NSE ticker symbol and prefix it with "NSE:" (e.g., "NSE:RELIANCE").
    - Buying Price: The exact entry/buying price mentioned. If NO specific price is mentioned, use an empty string "".
    - TARGET: The absolute target price (🎯) mentioned. If only a percentage target is given (e.g., "target 10%"), leave this as "" and fill TARGET_PERCENT instead.
    - TARGET_PERCENT: If the target is mentioned as a percentage (e.g., "10% target", "upside of 15%"), extract just the number (e.g., "10" or "15"). If an absolute target price is given or no target at all, use "".
    - Reason: Any rationale or reason mentioned for the trade.

    Format your response EXACTLY as a JSON object with the following schema:
    {{
        "is_trade": boolean,
        "NAME": string,
        "Buying Price": string,
        "TARGET": string,
        "TARGET_PERCENT": string,
        "Reason": string
    }}
    
    If any field is missing, use an empty string "".
    If it is NOT a trade setup, return {{"is_trade": false}}.
    Return ONLY the raw JSON object, no markdown blocks or extra text.

    Message to analyze:
    {text}
    """

    try:
        # Use aio for async requests
        response = await _client.aio.models.generate_content(
            model=model_name,
            contents=prompt
        )
        result_text = response.text.strip()
        
        # Remove potential markdown formatting just in case
        if result_text.startswith("```json"):
            result_text = result_text[7:]
        if result_text.startswith("```"):
            result_text = result_text[3:]
        if result_text.endswith("```"):
            result_text = result_text[:-3]
            
        data = json.loads(result_text.strip())
        
        if data.get("is_trade"):
            print(f"Vertex AI identified a trade: {data.get('NAME')}")
            return data
        else:
            print("Vertex AI analyzed message: Not a trade.")
            return None
            
    except Exception as e:
        print(f"Vertex AI analysis failed: {e}")
        return None
