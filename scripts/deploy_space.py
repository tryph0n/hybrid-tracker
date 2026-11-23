"""Point the Hugging Face Space at the published Docker image.

The Space gets the README (its configuration) and a one-line Dockerfile that
starts from the image, so it runs exactly what the pipeline built and tested.
An unchanged image makes no new Space commit.

Environment:
    HF_TOKEN     Hugging Face token with write access
    HF_SPACE_ID  "<user>/<space-name>", created on first run if missing
    IMAGE        public image reference, "<user>/<repository>:<tag>"
"""

import os
import shutil
import tempfile
from pathlib import Path

from huggingface_hub import HfApi


def main() -> None:
    space_id = os.environ["HF_SPACE_ID"]
    image = os.environ["IMAGE"]
    api = HfApi(token=os.environ["HF_TOKEN"])
    api.create_repo(space_id, repo_type="space", space_sdk="docker", exist_ok=True)
    with tempfile.TemporaryDirectory() as folder:
        shutil.copy("README.md", folder)
        Path(folder, "Dockerfile").write_text(f"FROM {image}\n")
        api.upload_folder(
            repo_id=space_id,
            repo_type="space",
            folder_path=folder,
            commit_message=f"Deploy {image}",
        )
    print(f"Deployed {image} to https://huggingface.co/spaces/{space_id}")


if __name__ == "__main__":
    main()
