import os
from typing import Final


def data_path(filename: str) -> str:
    """Return an absolute path under the repo's ./data folder.

    Ensures the folder exists so callers can read/write safely.
    """

    base_dir: Final[str] = os.path.dirname(__file__)
    data_dir: Final[str] = os.path.join(base_dir, "data")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, filename)
