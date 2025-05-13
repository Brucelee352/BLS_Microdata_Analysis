"""
Constants for project scripts and configurations.

"""
import os
import sys
import logging
from pathlib import Path
from datetime import datetime as dt
from faker import Faker


# MinIO/S3 Configuration
MINIO_ENDPOINT = 's3.brucea-lee.com'
MINIO_ROOT_USER = "admin"
MINIO_ROOT_PASSWORD = "code_earth420"
MINIO_BUCKET_NAME = 'sim-api-data'
MINIO_USE_SSL = True
MINIO_URL_STYLE = 'path'

# Path configuration
PROJECT_ROOT = Path(__file__).parents[2]
DBT_ROOT = PROJECT_ROOT / 'dbt_pipeline_demo'
DB_DIR = DBT_ROOT / 'databases'
DB_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DB_DIR / 'dbt_pipeline_demo.duckdb'
REPORTS_DIR = PROJECT_ROOT / 'reports'
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
DBT_PROFILES_DIR = PROJECT_ROOT / '.dbt'


# Database Configuration
DATA_SCHEMA = 'main.data_schema'

# Logging Configuration
LOG = logging.getLogger(':')
LOG.setLevel(os.getenv('LOG_LEVEL', 'INFO'))
LOG_DIR = PROJECT_ROOT / 'logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=LOG.level,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / 'pipeline.log'),
        logging.StreamHandler(sys.stdout)
    ]
)


def main():
    """
    Main function to print all constants.
    """
    print(f"PROJECT_ROOT: {PROJECT_ROOT}")
    print(f"DB_PATH: {DB_PATH}")
    print(f"REPORTS_DIR: {REPORTS_DIR}")
    print(f"LOG_DIR: {LOG_DIR}")
    print(f"LOG_LEVEL: {LOG.level}")
    print(f"MINIO_ENDPOINT: {MINIO_ENDPOINT}")
    print(f"MINIO_ROOT_USER: {MINIO_ROOT_USER}")
    print(f"MINIO_ROOT_PASSWORD: {MINIO_ROOT_PASSWORD}")
    print(f"MINIO_BUCKET_NAME: {MINIO_BUCKET_NAME}")
    print(f"MINIO_USE_SSL: {MINIO_USE_SSL}")
    print(f"MINIO_URL_STYLE: {MINIO_URL_STYLE}")


if __name__ == "__main__":
    main()
