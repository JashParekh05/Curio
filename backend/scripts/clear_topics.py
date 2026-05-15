"""
Delete clips (and topic rows) for specified slugs so seed_clips.py will re-process them.

Usage:
    cd backend
    python -m scripts.clear_topics neural-networks-basics gradient-descent ...
    python -m scripts.clear_topics --all    # clears every topic
"""
import sys
from dotenv import load_dotenv
load_dotenv()

from app.db.supabase import get_client

REPLACEABLE = [
    "neural-networks-basics",
    "gradient-descent",
    "backpropagation",
    "transformers-attention",
    "linear-algebra-vectors",
    "linear-algebra-matrices",
    "calculus-derivatives",
    "calculus-chain-rule",
]

def main():
    db = get_client()
    args = sys.argv[1:]
    if "--all" in args:
        slugs = REPLACEABLE
    elif args:
        slugs = args
    else:
        slugs = REPLACEABLE

    for slug in slugs:
        db.table("clips").delete().eq("topic_slug", slug).execute()
        print(f"Cleared clips for {slug}")

if __name__ == "__main__":
    main()
