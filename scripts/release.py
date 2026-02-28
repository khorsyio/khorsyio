import os
import sys
import httpx
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

try:
    import tomllib
except ImportError:
    import tomli as tomllib

GITHUB_API_URL = "https://api.github.com"
REPO = "khorsyio/khorsyio"

def get_version() -> str:
    pyproject_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "pyproject.toml")
    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)
        return data["project"]["version"]

def check_release_exists(client: httpx.Client, tag_name: str) -> bool:
    url = f"{GITHUB_API_URL}/repos/{REPO}/releases/tags/{tag_name}"
    response = client.get(url)
    if response.status_code == 200:
        return True
    elif response.status_code == 404:
        return False
    else:
        response.raise_for_status()
        return False

def create_release(client: httpx.Client, tag_name: str, name: str, body: str):
    url = f"{GITHUB_API_URL}/repos/{REPO}/releases"
    payload = {
        "tag_name": tag_name,
        "name": name,
        "body": body,
        "draft": False,
        "prerelease": False,
        "generate_release_notes": True
    }
    response = client.post(url, json=payload)
    response.raise_for_status()
    return response.json()

def main():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("Error: GITHUB_TOKEN environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    version = get_version()
    tag_name = f"v{version}"
    release_name = f"Release {tag_name}"
    
    print(f"Targeting repository: {REPO}")
    print(f"Current version from pyproject.toml: {version}")
    print(f"Checking if release {tag_name} exists...")

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28"
    }

    with httpx.Client(headers=headers, timeout=10.0) as client:
        try:
            if check_release_exists(client, tag_name):
                print(f"Release {tag_name} already exists. Skipping creation.")
                sys.exit(0)
            
            print(f"Release {tag_name} does not exist. Creating it now...")
            release_data = create_release(client, tag_name, release_name, f"Automated release for version {version}")
            
            print(f"Successfully created release: {release_data.get('html_url')}")
            
        except httpx.HTTPStatusError as e:
            print(f"GitHub API Error: {e.response.status_code} - {e.response.text}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"Unexpected error: {e}", file=sys.stderr)
            sys.exit(1)

if __name__ == "__main__":
    main()

"""
uv run python scripts/release.py
"""
