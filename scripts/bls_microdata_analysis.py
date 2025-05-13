"""
#----------------------------------------------------------------#

BLS Microdata Analysis and Pipeline v1.0

This is a dashboard, whose purpose is to analyze BLS CPS Microdata
to determine trends in underemployment since the year 2025 has 
started. 

Please refer to README.md file for more information.

#----------------------------------------------------------------#

Install the needed dependencies via this command:

pip install -e . 

A requirements.txt file can be found in the root directory of the 
project on the GitHub repo for download. 

*More information on porfolio resource to be added later*

This too can be installed with via cli.

pip install -r requirements.txt

#----------------------------------------------------------------#
"""

# Standard library imports
import os
import sys
from pathlib import Path
import random
from datetime import datetime as dt
from datetime import timedelta
import importlib.metadata as metadata
import importlib.resources as resources
import time

# Third-party imports
import minio
import duckdb
from dotenv import load_dotenv
from dbt.cli.main import dbtRunner
import pandas as pd

# Local imports for constants
from constants import (PROJECT_ROOT, MINIO_ENDPOINT, MINIO_ROOT_USER, MINIO_ROOT_PASSWORD, MINIO_BUCKET_NAME, MINIO_USE_SSL, LOG_DIR, DB_PATH, LOG, DBT_ROOT)


# Initialize paths and configuration
os.environ['DBT_PROFILES_DIR'] = str(PROJECT_ROOT / '.dbt')
load_dotenv(dotenv_path=PROJECT_ROOT / 'pdp_config.env')

# Set logging level
LOG_DIR.mkdir(parents=True, exist_ok=True)



class PipelineState:
    """
    Class to manage the pipeline state.
    """

    def __init__(self):
        """
        Initialize the pipeline state.
        """
        self.cached_data = None

    def reset_state(self):
        """Reset the pipeline state."""
        self.cached_data = None


# Create a global instance (optional)
pipeline_state = PipelineState()


# Functions

def minio_client():
    """
    Initializes the Minio client.
    """
    try:
        client = minio.Minio(
            MINIO_ENDPOINT,
            access_key=MINIO_ROOT_USER,
            secret_key=MINIO_ROOT_PASSWORD,
            secure=MINIO_USE_SSL
        )
        LOG.info("Connected to %s", MINIO_ENDPOINT)
        return client
    except Exception as e:
        LOG.error("MinIO connection error: %s", str(e))
        raise


def ellipsis(process_name="Loading", num_dots=3, interval=0.5) -> None:
    """
    Prints static loading messages with trailing periods.
    Unnecessary, but for flare, why not? I like it.

    Args:
        process_name(str): The name of the process to display.
        num_dots(int): The number of dots to print.
        interval(int): The interval between dots in seconds.
    """
    try:
        # Print out the process name
        sys.stdout.write(process_name)
        sys.stdout.flush()

        # Prints out trailing ellipses with a delay
        for _ in range(num_dots):
            sys.stdout.write(".")
            sys.stdout.flush()
            time.sleep(interval)

        # Move to the next line
        sys.stdout.write("\n")
    except Exception as e:
        LOG.error("Error in ellipsis function: %s", str(e))
        raise


def check_dependencies() -> None:
    """
    Verifies if the required packages are installed.
    Exits the program if any are missing.

    This version uses:
      - importlib.metadata to list installed packages, and
      - importlib.resources to load a file containing dependency info.
 """

    try:
        # Handle case where __package__ is None (script run directly)
        package_path = Path(__file__).parent
        if __package__ is None:
            package_path = Path(__file__).parent
        else:
            package_path = Path(__file__).parent / resources.files(__package__)
        with (package_path / "dependencies.txt").open() as f:
            required_data = f.read()

        # Each line is assumed to be "package==version" or a comment.
        required = {
            line.split("==")[0].strip()
            for line in required_data.splitlines()
            if line.strip() and not line.strip().startswith("#")
        }
    except (FileNotFoundError, TypeError):
        # Fallback to hardcoded required packages if no resource file is found.
        required = {
            'user-agents',
            'duckdb',
            'minio',
            'pandas',
            'faker',
            'dbt-core',
            'dbt-duckdb'
        }

    # Use importlib.metadata to get a set of installed package names.
    installed = {
        dist.metadata.get('Name', '').lower()
        for dist in metadata.distributions()
        if dist.metadata.get('Name')
    }

    # Identify missing packages (case-insensitively).
    missing = {pkg for pkg in required if pkg.lower() not in installed}

    if missing:
        LOG.error("Missing packages: %s", missing)
        LOG.info(
            "Please ensure that the virtual environment is created, "
            "and then install dependencies:"
        )
        LOG.info("  venv creation: python -m venv .venv")
        LOG.info(
            "  venv activation: source .venv/bin/activate  # or "
            ".venv\\Scripts\\activate on Windows")
        LOG.info("  dependencies: pip install -r requirements.txt")
        sys.exit(1)
        

def generate_data() -> pd.DataFrame:
    """
    Generates synthetic data for the pipeline.

    Args:
        DEFAULT_NUM_ROWS (int): The number of rows to generate,
        can be set in .env file, as well as START_DATETIME and END_DATETIME.
    """