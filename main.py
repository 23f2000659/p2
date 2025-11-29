import os
import sys
import json
import base64
import subprocess
import requests
from urllib.parse import urljoin, urlparse
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from openai import OpenAI
from playwright.async_api import async_playwright

# --- CONFIGURATION ---
app = FastAPI()

# UPDATE THESE WITH YOUR DETAILS
student_email = "23f2000659@ds.study.iitm.ac.in"
student_secret = "birorarishi" 
aipipe_token = os.environ.get("OPENAI_API_KEY") 
base_url=os.environ.get("OPENAI_BASE_URL")

# Check for token
if not aipipe_token:
    print("ERROR: TOKEN environment variable is missing!")

client = OpenAI(
    base_url=base_url,
    api_key=aipipe_token
)
class TaskRequest(BaseModel):
    email: str
    secret: str
    url: str

# --- HELPER FUNCTIONS ---
async def get_page_content(url):
    """Uses Playwright to get the full JS-rendered HTML."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.goto(url, timeout=60000)
            await page.wait_for_load_state("networkidle")
            content = await page.content()
        except Exception as e:
            print(f"Page Load Error: {e}")
            content = ""
        await browser.close()
        return content

def run_solver_script(script_content):
    """Executes the LLM-generated script safely."""
    filename = "solver_script.py"
    
    # Prepend common imports to prevent NameErrors if LLM forgets them
    safety_imports = (
        "import requests\n"
        "import base64\n"
        "import re\n"
        "import pandas as pd\n"
        "import io\n"
        "import csv\n"
        "import json\n\n"
    )

    with open(filename, "w") as f:
        f.write(safety_imports + script_content)
    
    try:
        result = subprocess.run(
            [sys.executable, filename], 
            capture_output=True, 
            text=True, 
            timeout=30
        )
        if result.returncode != 0:
            print(f"Script Error: {result.stderr}")
            return None
        return result.stdout.strip()
    except Exception as e:
        print(f"Execution Error: {e}")
        return None

# --- MAIN AGENT LOGIC ---
async def process_quiz_loop(start_url):
    current_url = start_url
    print(f"Starting Agent Task for: {start_url}")
    
    # Run for a max of 10 levels to prevent infinite loops
    for level in range(1, 11): 
        print(f"--- Processing Level {level}: {current_url} ---")
        
        # 1. Scrape the Page
        html = await get_page_content(current_url)
        if not html:
            print("Failed to retrieve HTML. Stopping.")
            break

        parsed = urlparse(current_url)
        base_domain = f"{parsed.scheme}://{parsed.netloc}"

        # 2. Construct System Prompt (Optimized for Requirements)
        prompt = f"""
        I am an autonomous data analyst agent.
        
        HTML CONTENT:
        -------------------------------------------------
        {html[:15000]} 
        -------------------------------------------------
        
        CONTEXT:
        - Base Domain: {base_domain}
        - Current URL: {current_url}
        - My Email: "{student_email}"
        
        YOUR MISSION:
        1. Decode any Base64 instructions.
        2. Identify the specific task (e.g., "Sum column A", "Find the secret code").
        3. Write Python code to calculate the answer.
        
        STRICT RULES:
        1. **Variable Replacement:** If text mentions `$EMAIL` or `{{email}}`, replace it with `"{student_email}"` in your script.
        2. **Ignore Example Data:** The HTML often contains example JSON (e.g., `{{ "secret": "your secret" }}`). IGNORE IT. You must download the REAL data from the link described in the text.
        3. **Data Processing:** - If the task requires data (CSV/PDF), use `requests.get` to download it.
           - Use `pandas` for calculations (sum, mean, count).
           - Always use headers: `headers = {{"User-Agent": "Mozilla/5.0"}}`
        4. **Output:** Print ONLY the final result value. Do not print debug info.
        
        OUTPUT JSON FORMAT:
        {{
            "submit_link": "THE_SUBMIT_URL_PATH", 
            "python_code": "YOUR_PYTHON_SCRIPT"
        }}
        """
        
        try:
            completion = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"}
            )
            llm_data = json.loads(completion.choices[0].message.content)
        except Exception as e:
            print(f"LLM API Error: {e}")
            break

        # 3. Extract Details
        raw_link = llm_data.get('submit_link')
        code = llm_data.get('python_code', "")
        
        submit_url = urljoin(current_url, raw_link) if raw_link else current_url

        # 4. Run Solver
        answer = run_solver_script(code)
        print(f"Calculated Answer: {answer}")
        
        if not answer:
            print("Error: Solver returned no answer.")
            break

        # 5. Submit
        final_answer = int(answer) if answer.isdigit() else answer
        
        payload = {
            "email": student_email,
            "secret": student_secret,
            "url": current_url,
            "answer": final_answer
        }
        
        try:
            submission = requests.post(submit_url, json=payload)
            response_data = submission.json()
            print(f"Submission Result: {response_data}")
            
            # Handle Next Steps
            next_url = response_data.get("url")
            is_correct = response_data.get("correct")

            if is_correct:
                if next_url:
                    current_url = next_url
                else:
                    print("SUCCESS: Quiz Completed!")
                    break
            else:
                # If wrong but a next URL is provided, skip to it (per project rules)
                if next_url:
                    print(f"Answer incorrect. Skipping to next level: {next_url}")
                    current_url = next_url
                else:
                    print("Failed and no next URL. Stopping.")
                    break
                    
        except Exception as e:
            print(f"Submission Failed: {e}")
            break

# --- API ENDPOINT ---
@app.post("/")
async def start_agent(request: TaskRequest, background_tasks: BackgroundTasks):
    # Requirement: Verify secret
    if request.secret != student_secret:
        raise HTTPException(status_code=403, detail="Invalid Secret")

    # Requirement: Respond 200 immediately, run task in background
    background_tasks.add_task(process_quiz_loop, request.url)
    return {"message": "Agent started", "status": "processing"}

if __name__ == "__main__":
    import uvicorn
    # Host 0.0.0.0 is required for Render/Docker environments
    uvicorn.run(app, host="0.0.0.0", port=8000)
