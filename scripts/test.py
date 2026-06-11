def basic_cleaning(df: pd.DataFrame) -> pd.DataFrame:
    """
    Handle basic data cleaning operations.

    Args:
        df (pd.DataFrame): The dataframe to process.
    """
    # Generate transaction ID
    df['transact_id'] = df.apply(
        lambda row: f"txn_{row['user_id']}_{pd.to_datetime(
            row['login_time']).strftime('%Y%m%d%H%M%S')}"
        if pd.notnull(row['login_time']) else None,
        axis=1
    )

    # Basic cleaning steps
    for col in df.select_dtypes(include=['object']).columns:
        df[col] = df[col].str.strip()

    df = df.drop_duplicates(subset=['email'])

    # Remove irrelevant columns
    drop_columns = ['phone_number', 'city', 'postal_code', 'product_id']
    df = df.drop([col for col in drop_columns if col in df.columns], axis=1)

    df['country'] = 'United States'
    df['is_active'] = df['is_active'].replace({0: 'no', 1: 'yes'})

    return df[['user_id', 'transact_id', 'first_name', 'last_name',
               'email', 'date_of_birth', 'address', 'state', 'country',
               'company', 'job_title', 'ip_address', 'is_active',
               'login_time', 'logout_time', 'account_created',
               'account_updated', 'account_deleted',
               'session_duration_minutes', 'product_name', 'price',
               'purchase_status', 'user_agent']]


def advanced_cleaning(df: pd.DataFrame) -> pd.DataFrame:
    """
    Handle advanced data processing and validation.

    Parses user_agents(str) into the following fields:
        - device_type(str)
        - os(str)
        - browser(str)
    """
    # Process user agent data
    df[['device_type', 'os', 'browser']] = df['user_agent'].apply(
        lambda ua: pd.Series(
            {
                'device_type': parse(ua).device.family,
                'os': parse(ua).os.family,
                'browser': parse(ua).browser.family
            }))
    df['device_type'] = df['device_type'].str.replace('Other', 'Desktop')

    # Validate and filter data
    df = df[df.apply(validate_timestamps, axis=1)]
    df['price'] = pd.to_numeric(df['price'], errors='coerce')
    df = df[df['price'] > 0]
    df['purchase_status'] = df['purchase_status'].str.lower()
    df = df[df['purchase_status'].isin(VALID_STATUSES)]
    return df


def add_analysis_fields(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add additional analysis fields to the dataframe.

    Produces an additive dataframe with the following fields:
        - cohort_date
        - user_age_days
        - engagement_level
        - price_tier
        - customer_lifetime_value

    Needed to make the data more useful for analysis.
    """

    # Ensure datetime columns are properly formatted
    date_columns = ['account_created', 'login_time',
                    'logout_time', 'account_updated', 'account_deleted']
    for col in date_columns:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors='coerce')

    # Safe string format for cohort date
    df['cohort_date'] = df['account_created'].dt.strftime('%Y-%m')

    # Handle potential NaT in date differences
    df['user_age_days'] = (
        df['login_time'] - df['account_created']).dt.days.fillna(0)

    # Handle potential nulls in numeric cuts
    df['engagement_level'] = pd.cut(
        df['session_duration_minutes'].fillna(0),
        bins=[0, 30, 60, 120, float('inf')],
        labels=['Very Low', 'Low', 'Medium', 'High']
    )

    # Handle potential empty groups in qcut
    try:
        df['price_tier'] = pd.qcut(
            df['price'],
            q=4,
            labels=['Budget', 'Standard', 'Premium', 'Luxury']
        )
    except ValueError:
        # Fallback if not enough distinct values
        df['price_tier'] = 'Standard'

    # Safe CLV calculation
    user_purchases = df.groupby(
        'user_id')['price'].sum().fillna(0).reset_index()
    df = df.merge(user_purchases, on='user_id',
                  suffixes=('', '_total'), how='left')
    df['customer_lifetime_value'] = df['price_total'].fillna(0)
    df = df.drop('price_total', axis=1)

    return df
