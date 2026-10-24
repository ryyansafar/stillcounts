"""Start three read-only scout sessions, each attacking a different area of the offline sync."""
import json
import sys
from pathlib import Path

from ops.devin_client import DevinClient, load_env

ROOT = Path(__file__).resolve().parent.parent
AREAS = {
    "SCOUT-NETWORK": "NETWORK FAULTS: partitions that heal late, duplicated and reordered delivery, partial syncs cut off mid-batch, repeated retries, huge offline backlogs, a replica that syncs twice at once.",
    "SCOUT-IDS-CLOCKS": "IDS AND CLOCKS: Lamport ties across replicas, two operations with the same id but different content, replica id collisions, enormous or skewed Lamport values, empty or unicode text, negative or huge quantities, tombstone and restore races.",
    "SCOUT-APP-LIFECYCLE": "APP LIFECYCLE (the planned browser client keeps an operation log in IndexedDB or localStorage and a service worker caches the app): app killed mid-sync, two tabs on the same replica id, storage quota reached, service worker serving a stale app after an update while offline, clock changes on the phone, reinstall losing the replica id.",
}


def main(env_path: str) -> None:
    env = load_env(env_path)
    ids = json.loads((ROOT / ".devin_ids.json").read_text())
    schema = json.loads((ROOT / "devin/scout.schema.json").read_text())
    client = DevinClient(env["DEVIN_TOKEN"], env["DEVIN_ORG"])
    out = {}
    for role, area in AREAS.items():
        prompt = (f"@playbook:{ids['!hostile-net']}\nTASK: SCOUT (read-only). REPO: https://github.com/ryyansafar/stillcounts (main branch).\n"
                  f"AREA: {area}\nFollow the SCOUT procedure in the playbook. Return only the structured JSON. "
                  "If a message starts with INTERRUPT, write your notes, end them with NEXT:, and reply STOPPED AT CHECKPOINT.")
        res = client.create_session(prompt, title=role, tags=["stillcounts", "scout", role.lower()],
                                    max_acu_limit=3, playbook_id=ids["!hostile-net"], structured_output_schema=schema)
        out[role] = {"session_id": res.get("session_id"), "url": res.get("url")}
        print(role, "->", res.get("url"))
    (ROOT / ".sessions.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
