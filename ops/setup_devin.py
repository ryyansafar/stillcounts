"""One-time setup: create the playbook and save its id (never the token)."""
import json
import sys
from pathlib import Path

from ops.devin_client import DevinClient, load_env

ROOT = Path(__file__).resolve().parent.parent


def main(env_path: str) -> None:
    env = load_env(env_path)
    client = DevinClient(env["DEVIN_TOKEN"], env["DEVIN_ORG"])
    ids_path = ROOT / ".devin_ids.json"
    ids = json.loads(ids_path.read_text()) if ids_path.exists() else {}
    if "!hostile-net" not in ids:
        out = client.create_playbook("Hostile network (scout and writer)", (ROOT / "devin/hostile-net.md").read_text(),
                                     "!hostile-net", json.loads((ROOT / "devin/scout.schema.json").read_text()))
        ids["!hostile-net"] = out.get("playbook_id") or out.get("id") or out
        print("!hostile-net ->", ids["!hostile-net"])
    ids_path.write_text(json.dumps(ids, indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
