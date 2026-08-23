from pathlib import Path
import subprocess
import sys

PROJECT_DIR = Path(__file__).resolve().parent

def run_script(script_name: str) -> bool:
    script_path = PROJECT_DIR / script_name

    try:
        subprocess.run(
            [sys.executable, str(script_path)],
            cwd=PROJECT_DIR,
            check=True,
        )
    except subprocess.CalledProcessError as error:
        print(f"\n{script_name} failed with exit code {error.returncode}.")
        return False
    except OSError as error:
        print(f"\nCould not start {script_name}: {error}")
        return False

    return True

def main() -> int:
    print("Starting PDF processing...")
    if not run_script("pdf_reader.py"):
        print("The query application was not started.")
        return 1

    print("\nPDF processing completed. Starting the query application...")
    if not run_script("query.py"):
        return 1

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
