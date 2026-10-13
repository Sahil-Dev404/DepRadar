
import json
import urllib.request
from datetime import date
from pathlib import Path

OUTPUT_DIR = Path("data/sample_lockfiles")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

sources = {
    "expressjs-website": (
        "https://raw.githubusercontent.com/expressjs/expressjs.com/main/package-lock.json"
    ),
    "arcane-demo": (
        "https://raw.githubusercontent.com/getarcaneapp/demo/master/package-lock.json"
    ),
    "digitalgov": (
        "https://raw.githubusercontent.com/GSA/digitalgov.gov/main/package-lock.json"
    ),
    "modernweb": (
        "https://raw.githubusercontent.com/modernweb-dev/web/master/package-lock.json"
    ),
    "wger": (
        "https://raw.githubusercontent.com/wger-project/wger/master/package-lock.json"
    ),
}

successful = []

for name, url in sources.items():
    destination = OUTPUT_DIR / f"{name}.json"
    print(f"\nDownloading {name}...")

    try:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "DepRadar-lockfile-collector"}
        )

        with urllib.request.urlopen(request, timeout=30) as response:
            content = response.read()

        # Validate JSON before saving it.
        data = json.loads(content)

        if not isinstance(data, dict):
            raise ValueError("JSON root is not an object")

        if not (
            isinstance(data.get("packages"), dict)
            or isinstance(data.get("dependencies"), dict)
        ):
            raise ValueError("No npm lockfile package data found")

        # Save only after validation succeeds.
        destination.write_bytes(content)
        successful.append((name, url))

        package_count = len(
            data.get("packages", data.get("dependencies", {}))
        )

        print(f"SUCCESS: {destination}")
        print(f"Lockfile version: {data.get('lockfileVersion', 'legacy')}")
        print(f"Top-level package entries: {package_count}")
        print(f"File size: {len(content):,} bytes")

    except Exception as exc:
        print(f"FAILED: {name}: {exc}")
        destination.unlink(missing_ok=True)

# Record only successful downloads.
readme = [
    "# Sample npm lockfiles",
    "",
    f"Collection date: {date.today().isoformat()}",
    "",
    "These files are collected for local DepRadar testing.",
    "",
    "## Sources",
    "",
]

for name, url in successful:
    readme.append(f"- `{name}.json` — {url}")

readme.extend([
    "",
    f"Successful downloads: {len(successful)} / {len(sources)}",
    "",
])

(OUTPUT_DIR / "README.md").write_text(
    "\n".join(readme),
    encoding="utf-8"
)

print(f"\nCompleted: {len(successful)} / {len(sources)} valid lockfiles.")
print(f"Output directory: {OUTPUT_DIR.resolve()}")

if len(successful) < 5:
    print("Some sources failed. Replace failed URLs before considering Part C complete.")
