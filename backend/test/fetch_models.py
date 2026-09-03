"""Ad-hoc helper: list the models available from the configured Groq account.

Usage:  GROQ_API_KEY=sk_... python test/fetch_models.py
"""
import urllib.request
import json
import os
import sys

api_key = os.environ.get("GROQ_API_KEY")
if not api_key:
    sys.exit("Set GROQ_API_KEY in the environment before running this script.")

req = urllib.request.Request(
    "https://api.groq.com/openai/v1/models",
    headers={"Authorization": f"Bearer {api_key}"}
)
try:
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read().decode())
        ids = [m["id"] for m in data.get("data", [])]
        print("\n".join(ids))
except Exception as e:
    print(f"Failed: {e}")
