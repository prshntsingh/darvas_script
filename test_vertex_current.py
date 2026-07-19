import os
import json
import asyncio
from google.oauth2.credentials import Credentials
import vertexai
from vertexai.generative_models import GenerativeModel
from googleapiclient.discovery import build

async def main():
    with open(".env", "r") as f:
        env_vars = {}
        for line in f:
            if '=' in line:
                k, v = line.strip().split("=", 1)
                env_vars[k] = v
                
    vertex_creds_env = env_vars.get("VERTEX_CREDENTIALS_JSON")
    project_id = env_vars.get("VERTEX_PROJECT_ID")
    location = env_vars.get("VERTEX_LOCATION", "us-central1")
    
    if not vertex_creds_env:
        print("VERTEX_CREDENTIALS_JSON not found.")
        return
        
    creds_info = json.loads(vertex_creds_env)
    creds = Credentials.from_authorized_user_info(creds_info)
    
    # 1. Check API Status
    print(f"Checking if Vertex AI API is enabled on {project_id}...")
    try:
        service = build('serviceusage', 'v1', credentials=creds)
        request = service.services().get(name=f'projects/{project_id}/services/aiplatform.googleapis.com')
        response = request.execute()
        print(f"Vertex AI API State: {response.get('state')}")
    except Exception as e:
        print(f"Failed to check API state: {e}")

    # 2. Test Model
    print(f"Initializing Vertex AI...")
    vertexai.init(project=project_id, location=location, credentials=creds)
    
    try:
        print(f"Testing gemini-2.5-flash...")
        model = GenerativeModel("gemini-2.5-flash")
        # Run blocking generate_content inside executor
        response = await asyncio.get_event_loop().run_in_executor(None, model.generate_content, "Hello")
        print(f"SUCCESS: {response.text}")
    except Exception as e:
        print(f"FAILED: {e}")

asyncio.run(main())
