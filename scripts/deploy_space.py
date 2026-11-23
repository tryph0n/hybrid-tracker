"""Publish the app and its data to the Hugging Face Space.

Environment:
    HF_TOKEN     Hugging Face token with write access
    HF_SPACE_ID  "<user>/<space-name>", created on first run if missing
"""

import os

from huggingface_hub import HfApi

# Only what the Docker image needs.
SPACE_FILES = [
    "README.md",
    "Dockerfile",
    "requirements.txt",
    ".streamlit/*",
    "src/*",
    "data/*",
]
IGNORED = ["*/__pycache__/*", "*.pyc"]


def main() -> None:
    space_id = os.environ["HF_SPACE_ID"]
    api = HfApi(token=os.environ["HF_TOKEN"])
    api.create_repo(space_id, repo_type="space", space_sdk="docker", exist_ok=True)
    api.upload_folder(
        repo_id=space_id,
        repo_type="space",
        folder_path=".",
        allow_patterns=SPACE_FILES,
        ignore_patterns=IGNORED,
        commit_message="Deploy from GitHub Actions",
    )
    print(f"Deployed to https://huggingface.co/spaces/{space_id}")


if __name__ == "__main__":
    main()
