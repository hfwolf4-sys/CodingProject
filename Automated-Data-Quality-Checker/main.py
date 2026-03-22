import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from checker import load_data, run_all_checks
from report_generator import generate_pdf_report


def main():
    print("=" * 50)
    print("   AUTOMATED DATA QUALITY CHECKER")
    print("=" * 50)

    # --- Accept a CSV file from command line OR prompt the user ---
    if len(sys.argv) > 1:
        # User passed a file path as argument e.g: python main.py data/myfile.csv
        filepath = sys.argv[1]
    else:
        # Ask the user to type in the file path
        print("\nEnter the path to your CSV file.")
        print("Example: data/sample.csv")
        filepath = input("\nCSV file path: ").strip()

    # Check if the file actually exists
    if not os.path.exists(filepath):
        print(f"\n❌ File not found: '{filepath}'")
        print("Make sure the file path is correct and try again.")
        return

    # Load data
    df = load_data(filepath)
    if df is None:
        return

    # Run all checks
    results = run_all_checks(df)

    # Generate PDF report
    filename = os.path.basename(filepath)
    report_path = generate_pdf_report(results, filename)

    print(f"\n🎉 Done! Open your report at: {report_path}")


if __name__ == "__main__":
    main()
