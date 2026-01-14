import os
from google import genai
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("GOOGLE_API_KEY")
if not api_key:
    print("No JAVA/GOOGLE_API_KEY found")
    exit(1)

client = genai.Client(api_key=api_key)

print("Listing available models...")
try:
    for m in client.models.list():
        print(f"{m.name} ({m.display_name})")
except Exception as e:
    print(f"Error: {e}")
