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
import glob
from pathlib import Path
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
from scripts.constants import (
    PROJECT_ROOT, MINIO_ENDPOINT, MINIO_ROOT_USER, MINIO_ROOT_PASSWORD,
    MINIO_BUCKET_NAME, MINIO_USE_SSL, LOG_DIR, DB_PATH, LOG, DBT_ROOT)


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


def reformat_columns() -> pd.DataFrame:
    """Reads all CSV files in a directory and assigns them to pandas DataFrames.

    Args:
        directory (str): The path to the directory containing the CSV files.

    Returns:
        dict: A dictionary where keys are filenames and values are pandas DataFrames.
    """

    col_list = ['pemlr', 'puwk', 'pehruslt', 'pehrwant', 'pehrrsn1', 'prwkstat',
                'pruntype', 'prunedur', 'pemjot', 'pulay', 'pulay6m', 'prtage',
                'pesex', 'pemaritl', 'peeduca', 'ptdtrace', 'prcitshp',
                'pehspnon', 'penatvty', 'prfamnum', 'hrhhid']

    folder_path = Path(__file__).parent.parent / 'data'
    all_files = glob.glob(str(folder_path / '*.csv'))
    dfs = {}
    for file in all_files:
        filename = os.path.basename(file).replace('.csv', '')
        df = pd.read_csv(file, engine='pyarrow')
        df.columns = df.columns.str.strip().str.lower()
        df = df[col_list]
        dfs[filename] = df


def process_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    Process the data, via string replacement of readable values.
    """

    col_list = ['pemlr', 'puwk', 'pehruslt', 'pehrwant', 'pehrrsn1', 'prwkstat',
                'pruntype', 'prunedur', 'pemjot', 'pulay', 'pulay6m', 'prtage',
                'pesex', 'pemaritl', 'peeduca', 'ptdtrace', 'prcitshp',
                'pehspnon', 'penatvty', 'prfamnum', 'hrhhid']

    folder_path = Path(__file__).parent.parent / 'data'
    all_files = glob.glob(str(folder_path / '*.csv'))
    dfs = {}
    for file in all_files:
        filename = os.path.basename(file).replace('.csv', '')
        df = pd.read_csv(file, engine='pyarrow')
        df.columns = df.columns.str.strip().str.lower()
        df = df[col_list]
        dfs[filename] = df

    value_maps = {
        'pemlr': {
            1: 'Employed - At Work',
            2: 'Employed - Absent',
            3: 'Unemployed - On Layoff',
            4: 'Unemployed - Looking',
            5: 'Not in Labor Force - Retired',
            6: 'Not in Labor Force - Disabled',
            7: 'Not in Labor Force - Other',
        },
        'puwk': {
            1: 'Yes',
            2: 'No',
            3: 'Retired',
            4: 'Disabled',
            5: 'Unable to Work',
        },
        'pehruslt': {},  # Unchanged
        'pehrwant': {
            1: 'Yes',
            2: 'No',
            3: 'Regular Hours are Full-Time',
        },
        'pehrrsn1': {
            1: 'Slack Work/Business Conditions',
            2: 'Could Only Find Part-Time Work',
            3: 'Seasonal Work',
            4: 'Child Care Problems',
            5: 'Other Family/Personal Obligations',
            6: 'Health/Medical Limitations',
            7: 'School/Training',
            8: 'Retired/Social Security Limit on Earnings',
            9: 'Full-Time Workweek is Less Than 35 Hrs',
            10: 'Other - Specify',
        },
        'prwkstat': {
            1: 'Not in Labor Force',
            2: 'FT Hours (35+), Usually FT',
            3: 'PT for Economic Reasons, Usually FT',
            4: 'PT for Non-Economic Reasons, Usually FT',
            5: 'Not at Work, Usually FT',
            6: 'PT Hrs, Usually PT for Economic Reasons',
            7: 'PT Hrs, Usually PT for Non-Economic Reasons',
            8: 'FT Hrs, Usually PT for Economic Reasons',
            9: 'FT Hrs, Usually PT for Non-Economic',
            10: 'Not at Work, Usually Part-Time',
            11: 'Unemployed FT',
            12: 'Unemployed PT',
        },
        'pruntype': {
            1:	'Job Loser On Layoff',
            2:	'Other Job Loser',
            3:	'Temporary Job Ended',
            4:	'Job Leaver',
            5:	'Re-Entrant',
            6:	'New-Entrant',
        },
        'prunedur': {},  # Duration, no mapping
        'pemjot': {
            1: 'Yes',
            2: 'No',
        },
        'pulay': {
            1: 'Yes',
            2: 'No',
            3: 'Retired',
            4: 'Disabled',
            5: 'Unable to Work',
        },
        'pulay6m': {
            1: 'Yes',
            2: 'No',
        },
        'prtage': {},
        'pesex': {
            1: 'Male',
            2: 'Female',
        },
        'pemaritl': {
            1: 'Married - Spouse Present',
            2: 'Married - Spouse Absent',
            3: 'Widowed',
            4: 'Divorced',
            5: 'Separated',
            6: 'Never Married',
        },
        'peeduca': {
            31: 'Less than 1st Grade',
            32: '1st-4th Grade',
            33: '5th or 6th Grade',
            34: '7th or 8th Grade',
            35: '9th Grade',
            36: '10th Grade',
            37: '11th Grade',
            38: '12th Grade No Diploma',
            39: 'High School Grad - Diploma or GED',
            40: 'Some College, No Degree',
            41: 'Associate Degree - Occupational/Vocational',
            42: 'Associate Degree - Academic Program',
            43: 'Bachelor\'s Degree (BA, AB, BS)',
            44: 'Master\'s Degree (MA, MS, MEng, MEd, MSW)',
            45: 'Professional School Degree (MD, DDS, DVM)',
            46: 'Doctorate Degree (PhD, EdD)',
        },
        'ptdtrace': {
            1: 'White Only',
            2: 'Black Only',
            3: 'American Indian, Alaskan Native Only',
            4: 'Asian Only',
            5: 'Hawaiian/Pacific Islander Only',
            6: 'White-Black',
            7: 'White-AI',
            8: 'White-Asian',
            9: 'White-HP',
            10: 'Black-AI',
            11: 'Black-Asian',
            12: 'Black-HP',
            13: 'AI-Asian',
            14: 'AI-HP',
            15: 'Asian-HP',
            16: 'W-B-AI',
            17: 'W-B-A',
            18: 'W-B-HP',
            19: 'W-AI-A',
            20: 'W-AI-HP',
            21: 'W-A-HP',
            22: 'B-AI-A',
            23: 'W-B-AI-A',
            24: 'W-AI-A-HP',
            25: 'Other 3 Race Combinations',
            26: 'Other 4 and 5 Race Combinations',
        },
        'prcitshp': {
            1: 'Native, Born in the United States',
            2: 'Native, Born in Puerto Rico or Other U.S. Island Areas',
            3: 'Native, Born Abroad of American Parent(s)',
            4: 'Foreign Born, U.S. Citizen by Naturalization',
            5: 'Foreign Born, Not a Citizen of the United States',
        },
        'pehspnon': {
            1: 'Hispanic',
            2: 'Non-Hispanic',
        },
        'penatvty': {
            57: 'United States',
            60: 'American Samoa',
            66: 'Guam',
            73: 'Puerto Rico',
            78: 'U.S. Virgin Islands',
            96: 'Other U.S. Island Area',
            555: 'Elsewhere',  # 100-554: 'Foreign Country (see appendix)',
        },
        'prfamnum': {
            0: 'Not a Family Member',
            1: 'Primary Family Member Only',
            2: 'Subfamily No. 2 Member',
            3: 'Subfamily No. 3 Member',
            4: 'Subfamily No. 4 Member',
            5: 'Subfamily No. 5 Member',
            6: 'Subfamily No. 6 Member',
            7: 'Subfamily No. 7 Member',
            8: 'Subfamily No. 8 Member',
            9: 'Subfamily No. 9 Member',
            10: 'Subfamily No. 10 Member',
            11: 'Subfamily No. 11 Member',
            12: 'Subfamily No. 12 Member',
            13: 'Subfamily No. 13 Member',
            14: 'Subfamily No. 14 Member',
            15: 'Subfamily No. 15 Member',
            16: 'Subfamily No. 16 Member',
            17: 'Subfamily No. 17 Member',
            18: 'Subfamily No. 18 Member',
            19: 'Subfamily No. 19 Member',
        },
        'hrhhid': {},  # Household ID, no mapping
    }

    for col, mapping in value_maps.items():
        if mapping and col in df.columns:
            df[col] = df[col].replace(mapping)

    col_names = ['employment_status', 'worked_lastweek',
                 'totalhrs_worked_weekly', 'desire_ft_wk', 'reason_pt_wk',
                 'ft_pt_wkstatus', 'unemploy_reason', 'unemploy_duration',
                 'multi_job_status', 'layoff_status', 'expected_recall_wk',
                 'age', 'sex', 'marital_status', 'highest_ed_comp', 'race',
                 'citizenship_status', 'hisp_nonhisp', 'birth_country',
                 'num_fam_househld', 'househld_id']

    for key, df in dfs.items():
        df.columns = col_names
        print(f"Processing file: {key}")
        print(df)
        return df


def save_data_formats(df: pd.DataFrame, project_root: Path
                      ) -> tuple[Path, Path, Path]:
    """
    Save cleaned data in multiple formats (CSV, JSON, Parquet)
    and saves to the data directory, locally.

    Args:
        df (pd.DataFrame): The dataframe to save.
        project_root (Path): The root directory of the project.
    """
    try:
        # Create data directory if it doesn't exist
        data_dir = project_root / 'data'
        data_dir.mkdir(parents=True, exist_ok=True)

        # Define file paths
        csv_path = data_dir / 'cleaned_data.csv'
        json_path = data_dir / 'cleaned_data.json'
        parquet_path = data_dir / 'cleaned_data.parquet'

        # Save data in different formats
        df.to_csv(csv_path, index=False)
        df.to_json(json_path, orient='records', date_format='iso')
        df.to_parquet(parquet_path, index=False)

        # Log success
        LOG.info("Data saved in CSV, JSON, "
                 "and Parquet formats at %s", data_dir)

        # Return paths for further use
        return csv_path, json_path, parquet_path
    except Exception as e:
        LOG.error("Error saving data formats: %s", str(e))
        raise


def create_main_schema(
        df: pd.DataFrame, con: duckdb.DuckDBPyConnection) -> None:
    """Creates the source table in DuckDB."""
    try:
        # Drop existing table
        con.execute("DROP TABLE IF EXISTS main_schema")

        # Create table from DataFrame
        con.register('temp_df', df)
        con.execute("""
            CREATE SCHEMA IF NOT EXISTS raw_data;
            CREATE TABLE main_schema AS
            SELECT * FROM temp_df;
        """)

        count = con.execute(
            "SELECT COUNT(*) FROM main_schema").fetchone()[0]
        LOG.info("Created main_schema table with %s records", count)

    except Exception as e:
        LOG.error("Error creating main_schema table: %s", str(e))
        raise
    finally:
        if 'con' in locals():
            con.close()


def run_dbt_ops() -> None:
    """
    Runs dbt deps and build model tables for data transformation.

    Executes the following commands:
        - dbt deps
        - dbt run --target dev --full-refresh
    """
    try:
        # Store original directory
        original_dir = os.getcwd()

        # Change to dbt project directory
        os.chdir(DBT_ROOT)
        LOG.info("Changed working directory to %s", DBT_ROOT)

        # Clear dbt cache
        dbt = dbtRunner()
        dbt.invoke(["clean"])

        # Run dbt deps
        deps_result = dbt.invoke(["deps"])
        if not deps_result.success:
            LOG.error("Failed to run dbt deps")
            raise RuntimeError("Failed to run dbt deps")

        # Verify packages.yml exists
        if not (DBT_ROOT / 'packages.yml').exists():
            raise FileNotFoundError("packages.yml not "
                                    "found in dbt project")

        # Verify dbt_packages directory exists
        dbt_packages_dir = DBT_ROOT / 'dbt_packages'
        if not dbt_packages_dir.exists():
            raise FileNotFoundError(
                f"dbt_packages directory not found at {dbt_packages_dir}")

        # Run dbt commands
        result = dbt.invoke([
            "run",
            "--target", "dev",
            "--full-refresh"
        ])

        if not result.success or not deps_result.success:
            LOG.error("Failed to run dbt models")
            raise RuntimeError("Failed to run dbt models")
        else:
            LOG.info("Successfully ran dbt models")

        # Change back to original directory
        os.chdir(original_dir)
        LOG.info("Changed back to original directory: %s",
                 original_dir)

    except Exception as e:
        LOG.error("Error running dbt models: %s", str(e))
        raise


def generate_reports() -> None:
    """
    Generate analytics reports from transformed data.

    Runs all of the imported analytics queries from the
    analytics_queries.py file.

    Saves the reports to the reports directory.
    """
    try:
        reports_dir = PROJECT_ROOT / 'reports'
        reports_dir.mkdir(parents=True, exist_ok=True)

        con = duckdb.connect(str(DB_PATH))

        # Run analytics
        analysis_results = {
            "lifecycle_analysis": run_lifecycle_analysis(con),
            "purchase_analysis": run_purchase_analysis(con),
            "demographics_analysis": run_demographics_analysis(con),
            "business_analysis": run_business_analysis(con),
            "engagement_analysis": run_engagement_analysis(con),
            "churn_analysis": run_churn_analysis(con),
        }

        save_analysis_results(analysis_results, reports_dir)

    except Exception as e:
        LOG.error("Error generating reports: %s", str(e))
        raise


def upload_data() -> str:
    """
    Uploads transformed data to MinIO.

    Uses DuckDB to get the final data, and then uploads it to S3.

    Returns:
        str: 'success' if the upload is successful, 'failed' otherwise.
    """
    try:
        con = duckdb.connect(str(DB_PATH))
        # Get final data
        final_df = con.sql(f"""
            SELECT * FROM {PRODUCT_SCHEMA}
        """).df()

        # Upload to MinIO
        client = minio_client()

        # Save data locally, temporarily
        final_df.to_json('temp_upload.json', orient='records')
        final_df.to_parquet('temp_upload.parquet', index=False)

        # Upload to S3
        client.fput_object(
            bucket_name=MINIO_BUCKET_NAME,
            object_name='cleaned_data.json',
            file_path='temp_upload.json'
        )
        client.fput_object(
            bucket_name=MINIO_BUCKET_NAME,
            object_name='cleaned_data.parquet',
            file_path='temp_upload.parquet'
        )

        # Clean up local files
        os.remove('temp_upload.json')
        os.remove('temp_upload.parquet')

        LOG.info("Data uploaded to S3 successfully: "
                 "%s rows", len(final_df))
        return 'success'

    except (minio.error.S3Error, IOError, ValueError) as e:
        LOG.error("Error uploading data: %s", str(e))
        return 'failed'
    finally:
        if 'con' in locals():
            con.close()


def main() -> None:
    """
    Main function that orchestrates the data pipeline.
    """
    process_data()


if __name__ == "__main__":
    main()
