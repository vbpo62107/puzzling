import os
import subprocess


def wget_dl(url: str) -> str:
    """
    Download the resource at ``url`` using the system ``wget`` command.

    Returns the downloaded filename on success.

    Raises:
        RuntimeError: when ``wget`` is not available or the download fails.
    """
    filename = os.path.basename(url)
    if not filename:
        raise RuntimeError("Unable to determine target filename from URL.")

    print("Downloading Started")

    cmd = ["wget", "--output-document", filename, url]

    try:
        subprocess.check_output(cmd, stderr=subprocess.STDOUT)
    except FileNotFoundError as exc:
        # Propagate a more descriptive error for the caller to handle.
        raise RuntimeError("wget command not found on this system.") from exc
    except subprocess.CalledProcessError as exc:
        output = exc.output
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        elif output is None:
            output = ""
        raise RuntimeError(
            f"wget failed with exit code {exc.returncode}. Output: {output}"
        ) from exc

    print("Downloading Complete", filename)
    return filename
