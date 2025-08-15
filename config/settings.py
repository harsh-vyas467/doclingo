import os

# Read Gemini API key from environment variable
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Optional: Raise an error if not set
if GEMINI_API_KEY is None:
    raise ValueError("GEMINI_API_KEY environment variable not set")
