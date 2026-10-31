import sys, json
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from insertblock import insert_experience_block
r = insert_experience_block(
    r"C:\Users\sanke\Downloads\sanket_dawar_v7.pdf", "sanket_dawar_v7_senior.pdf",
    title="Senior Engineer", dates="Jan 2026 \u2013 Present",
    bullets=[
        ("Worked", "as a full stack engineer across Golang services and React dashboards"),
        ("Built", "verified business buyer flow and the paid buyer dashboard"),
        ("Shipped", "TrustPulse: seller-buyer RFQ to quotation journey on WhatsApp"),
        ("Built", "Smart RFQ form using AI to capture better buyer requirements"),
    ],
    anchor_query="Associate Software Engineer", verify=True)
print("verify ok:", r["verification"]["ok"])
