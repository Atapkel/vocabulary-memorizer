import os
from dotenv import load_dotenv

load_dotenv()
DB_PATH = os.getenv("VOCAB_DB_PATH", "vocab.db")
BOT_TOKEN = os.getenv("BOT_TOKEN")
ALLOWED_USER_ID = os.getenv("ALLOWED_USER_ID")
LEARNING_STEPS_MIN = [1, 10]
MAX_IMPORT_BYTES = 2 * 1024 * 1024
MAX_IMPORT_CARDS = 500
MAX_CONTEXT = 500
MAX_WORD = 80
PRIORITIES = {"high", "normal", "low"}
LEECH_THRESHOLD = 8
ANSWER_MATCH_THRESHOLD = 0.82
