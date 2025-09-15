import sys
import subprocess
import time
from pathlib import Path
from typing import List, Tuple, Optional
import shutil
import logging
from enum import Enum

class LogLevel(Enum):
    INFO = "\033[32m"
    ERROR = "\033[31m"
    WARN = "\033[33m"
    DEBUG = "\033[34m"
    HEADER = "\033[95m\033[1m"

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S")

def _coerce_level(level) -> LogLevel:
    if isinstance(level, LogLevel):
        return level
    if isinstance(level, str):
        key = level.strip().upper()
        if key == "WARNING":
            key = "WARN"
        try:
            return LogLevel[key]
        except KeyError:
            return LogLevel.INFO
    return LogLevel.INFO

def log(level: LogLevel | str, msg: str, *, fatal: bool = False, file=sys.stdout):
    """Log with ANSI color; only exits when fatal=True."""
    lvl = _coerce_level(level)
    color = lvl.value
    stream = file if lvl != LogLevel.ERROR else sys.stderr
    print(f"{color}{msg}\033[0m", file=stream)
    if fatal:
        sys.exit(1)

    

def run_cmd(cmd: List[str], cwd: Optional[Path] = None, timeout: int = 300, show: bool = True) -> Tuple[bool, str, str]:
    """Run command, return (success, stdout, stderr). Non-fatal on error."""
    if show:
        log(LogLevel.DEBUG, f"Running: {' '.join(map(str, cmd))}")
    try:
        result = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout, check=False)
        ok = (result.returncode == 0)
        if not ok and show:
            log(LogLevel.WARN, f"Command failed (rc={result.returncode}): {' '.join(map(str, cmd))}\n{result.stderr}")
        return ok, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return False, "", f"Timeout after {timeout}s"
    except Exception as e:
        return False, "", f"Exception: {e}"

def get_time() -> float:
    """Return current time in seconds."""
    return time.time()

def ensure_dir(path: Path) -> Path:
    """Create directory if it doesn't exist."""
    path.mkdir(parents=True, exist_ok=True)
    return path

def clean_dir(path: Path) -> Path:
    """Clean and recreate directory."""
    if path.exists():
        shutil.rmtree(path)
    return ensure_dir(path)

def validate_tool(tool: str | list[str]) -> bool:
    """Check tool(s) exist in PATH; warn if any missing; do not exit."""
    if isinstance(tool, str):
        tools = [tool]
    else:
        tools = tool
    ok = True
    for t in tools:
        if not shutil.which(t):
            log(LogLevel.WARN, f"Tool not found: {t}")
            ok = False
    return ok

def file_exists(path: Path | str) -> bool:
    """Check if file exists."""
    return Path(path).exists()

def read_file_lines(path: Path) -> List[str]:
    """Read non-empty lines from file."""
    if not file_exists(path):
        log(LogLevel.ERROR, f"File not found: {path}")
    return [line.strip() for line in path.read_text().splitlines() if line.strip()]

def write_file_lines(path: Path, lines: List[str]):
    """Write lines to file."""
    path.write_text("\n".join(lines) + "\n")
