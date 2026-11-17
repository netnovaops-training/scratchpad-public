"""Load GitHub facts into one person's Hermes memory, through Hermes' own MemoryStore.

    docker exec -i hermes python /app/load_github_facts.py user@example.com < facts.json

facts.json is made by github_to_hermes.py: a list of {"summary": ..., "body": ...}. Each becomes
one fact (kind "github", source "github-snapshot"). Saving the same summary again replaces the
fact, so re-running after GitHub changes refreshes the memory instead of piling up copies.
"""
import json
import os
import sys

sys.path.insert(0, "/app")
from shared.memory import MemoryStore  # noqa: E402

store = MemoryStore(os.environ.get("HERMES_MEMORY_ROOT", "/memory"), sys.argv[1], "Jason")
facts = json.load(sys.stdin)
for f in facts:
    store.remember(f["summary"], f["body"], kind="github", source="github-snapshot")
print(f"{len(facts)} GitHub facts saved for {sys.argv[1]}")
