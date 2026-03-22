import pandas as pd
import numpy as np


def load_data(filepath):
    """Load CSV file and return DataFrame."""
    try:
        df = pd.read_csv(filepath)
        print(f"Loaded {len(df)} rows and {len(df.columns)} columns.")
        return df
    except Exception as e:
        print(f"Error loading file: {e}")
        return None


def check_missing_values(df):
    """Check for missing/null values in each column."""
    missing = df.isnull().sum()
    missing_pct = (missing / len(df) * 100).round(2)
    result = pd.DataFrame({
        'Missing Count': missing,
        'Missing %': missing_pct
    })
    return result[result['Missing Count'] > 0]


def check_duplicates(df):
    """Identify duplicate rows."""
    total_dupes = df.duplicated().sum()
    dupe_rows = df[df.duplicated(keep=False)]
    return total_dupes, dupe_rows


def check_outliers(df):
    """Detect outliers in numeric columns using IQR method."""
    outlier_summary = {}
    numeric_cols = df.select_dtypes(include=[np.number]).columns

    for col in numeric_cols:
        Q1 = df[col].quantile(0.25)
        Q3 = df[col].quantile(0.75)
        IQR = Q3 - Q1
        lower = Q1 - 1.5 * IQR
        upper = Q3 + 1.5 * IQR
        outliers = df[(df[col] < lower) | (df[col] > upper)]
        if len(outliers) > 0:
            outlier_summary[col] = {
                'count': len(outliers),
                'lower_bound': round(lower, 2),
                'upper_bound': round(upper, 2),
                # OLD: 'outlier_values': outliers[col].tolist()
                # -- stored ALL outlier values, could be thousands of rows
                'outlier_values': outliers[col].head(5).tolist()
                # NEW: only store up to 5 sample values
            }
    return outlier_summary


def check_inconsistent_formatting(df):
    """Find inconsistent capitalization in string columns."""
    issues = {}
    string_cols = df.select_dtypes(include=['object']).columns

    for col in string_cols:
        unique_vals = df[col].dropna().unique()
        lower_vals = [str(v).strip().lower() for v in unique_vals]
        if len(lower_vals) != len(set(lower_vals)):

            # OLD: issues[col] = list(unique_vals)
            # -- stored every single unique value in the column
            # -- with 55,500 rows this meant thousands of names printed in the PDF

            affected = df[col].dropna().apply(
                lambda x: str(x).strip() != str(x).strip().title()
            ).sum()
            issues[col] = {
                # NEW: only store 5 sample examples plus summary counts
                'samples': list(unique_vals[:5]),
                'affected_rows': int(affected),
                'total_unique': len(unique_vals)
            }
    return issues


def check_negative_values(df):
    """Flag negative values in numeric columns."""
    neg_summary = {}
    numeric_cols = df.select_dtypes(include=[np.number]).columns

    for col in numeric_cols:
        neg_count = (df[col] < 0).sum()
        if neg_count > 0:
            neg_summary[col] = int(neg_count)
    return neg_summary


def check_invalid_dates(df):
    """Detect columns that look like dates but contain invalid entries."""
    date_issues = {}
    string_cols = df.select_dtypes(include=['object']).columns

    for col in string_cols:
        if 'date' in col.lower() or 'time' in col.lower():
            parsed = pd.to_datetime(df[col], errors='coerce')
            invalid_count = parsed.isnull().sum()
            if invalid_count > 0:
                date_issues[col] = int(invalid_count)
    return date_issues


def run_all_checks(df):
    """Run all checks and return a complete results dictionary."""
    print("\nRunning data quality checks...\n")

    results = {
        'shape': df.shape,
        'columns': list(df.columns),
        'dtypes': df.dtypes.astype(str).to_dict(),
        'missing': check_missing_values(df),
        'duplicates': check_duplicates(df),
        'outliers': check_outliers(df),
        'formatting': check_inconsistent_formatting(df),
        'negatives': check_negative_values(df),
        'invalid_dates': check_invalid_dates(df),
    }

    print(f"  Shape: {results['shape'][0]} rows x {results['shape'][1]} columns")
    print(f"  Missing values found in {len(results['missing'])} columns")
    print(f"  Duplicate rows: {results['duplicates'][0]}")
    print(f"  Columns with outliers: {len(results['outliers'])}")
    print(f"  Formatting issues: {len(results['formatting'])} columns")
    print(f"  Columns with negatives: {len(results['negatives'])}")
    print(f"  Date columns with invalid entries: {len(results['invalid_dates'])}")

    return results
