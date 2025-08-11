import json
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

CONFIG_PATH = "config.json"
REPORT_PATH = "reports/cleanup_report.html"

def load_config(path):
    with open(path, 'r') as f:
        return json.load(f)

def send_email(smtp_config, subject, html_content, recipients, cc):
    if cc is None:
        cc = []

    sign_off = """
    <br><br>
    <p>
      Regards,<br>
      Your Friendly Neighbourhood Hoover Bot
    </p>
    """
    full_html = html_content + sign_off

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = smtp_config['username']
    msg['To'] = ", ".join(recipients)
    msg['Cc'] = ", ".join(cc)  

    part = MIMEText(full_html, 'html')
    msg.attach(part)

    server = smtplib.SMTP(smtp_config['host'], smtp_config['port'])
    if smtp_config.get('use_tls', False):
        server.starttls()
    server.login(smtp_config['username'], smtp_config['password'])
    server.sendmail(msg['From'], recipients + cc, msg.as_string())  # <-- include cc here
    server.quit()

def main():
    config = load_config(CONFIG_PATH)
    
    recipients = config.get('report_recipients', [])
    cc_list = config.get("report_cc", [])

    if not recipients:
        print("No recipients found in config under 'report_recipients'. Exiting.")
        return
        
    if not Path(REPORT_PATH).exists():
        print(f"Report file '{REPORT_PATH}' not found. Exiting.")
        return
    
    with open(REPORT_PATH, 'r') as f:
        report_html = f.read()
    
    smtp_config = config.get('smtp_config')
    if not smtp_config:
        print("SMTP config missing in config.json. Exiting.")
        return
    
    # This function is used to extract secrets from Vault and is to be properly defined before using
      secret = get_secret(
        kv_path="KV PATH",  # adjust your path as needed
        pem_file_path="cert.pem"
    )
    smtp_password = secret.get("password")
    if not smtp_password:
        print("SMTP password not found in Vault secret. Exiting.")
        return
    
    smtp_config['password'] = smtp_password
    
    subject = "Harbor Cleanup Report"
    send_email(smtp_config, subject, report_html, recipients, cc_list)
    print(f"Report sent to: {', '.join(recipients)}")
    if cc_list:
        print(f"CC'd to: {', '.join(cc_list)}")
    else:
        print("No extra recipients were cc'ed in the report.")

if __name__ == "__main__":
    main()
