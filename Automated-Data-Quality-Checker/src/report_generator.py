from fpdf import FPDF
from datetime import datetime
import os


def clean(text):
    """Replace special characters that Helvetica cannot render."""
    return (str(text)
            .replace('\u2014', '-')
            .replace('\u2013', '-')
            .replace('\u2018', "'")
            .replace('\u2019', "'")
            .replace('\u201c', '"')
            .replace('\u201d', '"')
            .replace('\u2022', '*')
            .replace('\u2265', '>=')
            .replace('\u2264', '<='))


class DataQualityReport(FPDF):

    def header(self):
        self.set_fill_color(30, 90, 160)
        self.rect(0, 0, 210, 20, 'F')
        self.set_font('Helvetica', 'B', 14)
        self.set_text_color(255, 255, 255)
        self.cell(0, 20, 'Automated Data Quality Report', align='C', ln=True)
        self.set_text_color(0, 0, 0)

    def footer(self):
        self.set_y(-15)
        self.set_font('Helvetica', 'I', 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f'Generated: {datetime.now().strftime("%Y-%m-%d %H:%M")}  |  Page {self.page_no()}', align='C')

    def section_title(self, title):
        self.ln(6)
        self.set_fill_color(230, 240, 255)
        self.set_font('Helvetica', 'B', 12)
        self.set_text_color(30, 90, 160)
        self.cell(0, 10, clean(title), ln=True, fill=True)
        self.set_text_color(0, 0, 0)
        self.ln(2)

    def body_text(self, text):
        self.set_font('Helvetica', '', 10)
        self.multi_cell(0, 7, clean(text))
        self.ln(1)

    def success_text(self, text):
        self.set_font('Helvetica', '', 10)
        self.set_text_color(0, 140, 0)
        self.multi_cell(0, 7, clean(text))
        self.set_text_color(0, 0, 0)

    def warning_text(self, text):
        self.set_font('Helvetica', '', 10)
        self.set_text_color(200, 100, 0)
        self.multi_cell(0, 7, clean(text))
        self.set_text_color(0, 0, 0)

    def error_text(self, text):
        self.set_font('Helvetica', '', 10)
        self.set_text_color(180, 0, 0)
        self.multi_cell(0, 7, clean(text))
        self.set_text_color(0, 0, 0)


def generate_pdf_report(results, filename, output_dir='reports'):
    pdf = DataQualityReport()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_left_margin(15)
    pdf.set_right_margin(15)

    # --- OVERVIEW ---
    pdf.section_title('1. Dataset Overview')
    rows, cols = results['shape']
    pdf.body_text(f"File: {filename}")
    pdf.body_text(f"Total Rows: {rows:,}")
    pdf.body_text(f"Total Columns: {cols}")
    pdf.body_text(f"Columns: {', '.join(results['columns'])}")

    # --- MISSING VALUES ---
    pdf.section_title('2. Missing Values')
    missing_df = results['missing']
    if missing_df.empty:
        pdf.success_text("No missing values detected.")
    else:
        for col, row in missing_df.iterrows():
            pdf.warning_text(
                f"  {col}: {int(row['Missing Count']):,} missing values ({row['Missing %']}%)"
            )

    # --- DUPLICATES ---
    pdf.section_title('3. Duplicate Rows')
    total_dupes, _ = results['duplicates']
    if total_dupes == 0:
        pdf.success_text("No duplicate rows detected.")
    else:
        pdf.error_text(
            f"{total_dupes:,} duplicate rows found "
            f"({round(total_dupes / rows * 100, 2)}% of dataset). "
            f"Review and consider removing."
        )

    # --- OUTLIERS ---
    pdf.section_title('4. Outliers (IQR Method)')
    outliers = results['outliers']
    if not outliers:
        pdf.success_text("No outliers detected in numeric columns.")
    else:
        for col, info in outliers.items():
            pdf.warning_text(
                f"  {col}: {info['count']:,} outlier(s) detected "
                f"(expected range: {info['lower_bound']} - {info['upper_bound']})\n"
                # OLD: f"  Values: {info['outlier_values']}"
                # -- printed ALL outlier values, could be a huge list
                f"  Sample outlier values (up to 5): {info['outlier_values']}"
                # NEW: label clarifies only 5 samples are shown
            )

    # --- FORMATTING ---
    pdf.section_title('5. Inconsistent Formatting')
    formatting = results['formatting']
    if not formatting:
        pdf.success_text("No formatting inconsistencies detected.")
    else:
        for col, info in formatting.items():
            # OLD: pdf.warning_text(f"  {col}: inconsistent casing detected - {vals}")
            # -- vals was the full list of unique values, causing hundreds of pages
            # -- e.g. 10,000+ unique patient names all printed one by one

            pdf.warning_text(
                # NEW: show a clean 3-line summary instead
                f"  {col}: Inconsistent casing detected\n"
                f"  Affected rows: ~{info['affected_rows']:,} | "
                f"Total unique values: {info['total_unique']:,}\n"
                f"  Sample examples: {', '.join(str(v) for v in info['samples'])}"
            )

    # --- NEGATIVE VALUES ---
    pdf.section_title('6. Negative Values')
    negatives = results['negatives']
    if not negatives:
        pdf.success_text("No unexpected negative values found.")
    else:
        for col, count in negatives.items():
            pdf.error_text(f"  {col}: {count:,} negative value(s) found.")

    # --- INVALID DATES ---
    pdf.section_title('7. Invalid Date Entries')
    dates = results['invalid_dates']
    if not dates:
        pdf.success_text("No invalid date entries detected.")
    else:
        for col, count in dates.items():
            pdf.error_text(f"  {col}: {count:,} invalid date(s) found.")

    # --- SUMMARY SCORE ---
    pdf.section_title('8. Overall Data Quality Score')
    issues = (
        len(missing_df) +
        (1 if total_dupes > 0 else 0) +
        len(outliers) +
        len(formatting) +
        len(negatives) +
        len(dates)
    )
    max_issues = max(cols * 6, 1)
    score = max(0, round((1 - issues / max_issues) * 100))

    if score >= 80:
        pdf.success_text(f"Data Quality Score: {score}/100 - Good")
    elif score >= 50:
        pdf.warning_text(f"Data Quality Score: {score}/100 - Needs Attention")
    else:
        pdf.error_text(f"Data Quality Score: {score}/100 - Poor Quality")

    # Save
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(
        output_dir,
        f"quality_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    )
    pdf.output(output_path)
    print(f"\nReport saved to: {output_path}")
    return output_path
