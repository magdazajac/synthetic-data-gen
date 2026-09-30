import json
from google import genai
from google.genai import types

client = genai.Client(
    vertexai=True,
    project='gd-gcp-gridu-genai', 
    location='us-central1'
)

with open("library_mgm_schema.ddl", "r", encoding="utf-8") as f:
    ddl_schema = f.read()

prompt = f"""
You are a PostgreSQL database expert.
Analyze the following DDL schema:

{ddl_schema}

Generate 3 realistic rows of data for each table.
Ensure foreign key consistency.
Return the result as pure JSON, where the key is the table name and the value is a list of objects/rows.
"""

print("Sending request to Gemini...")

response = client.models.generate_content(
    model='gemini-2.5-flash',
    contents=prompt,
    config=types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=0.2,
    ),
)

print("\n--- RESPONSE FROM GEMINI ---")
print(response.text)