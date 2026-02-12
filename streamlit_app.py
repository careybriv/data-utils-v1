import streamlit as st
import time
import json
import io
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from google import genai
from google.api_core import exceptions
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- CONFIGURATION ---
PAGE_TITLE = "Redline AI | Enterprise"
PAGE_ICON = "🏢"

# --- SETUP PAGE ---
st.set_page_config(page_title=PAGE_TITLE, page_icon=PAGE_ICON, layout="centered")

# --- HIDE BRANDING ---
hide_st_style = """
            <style>
            #MainMenu {visibility: hidden;}
            footer {visibility: hidden;}
            header {visibility: hidden;}
            </style>
            """
st.markdown(hide_st_style, unsafe_allow_html=True)

# --- HUMAN ERROR TRANSLATOR ---
def translate_error(e):
    err_str = str(e).lower()
    if "11001" in err_str or "connection" in err_str or "socket" in err_str:
        return "🌐 No Internet Connection. Please check your WiFi."
    elif "403" in err_str or "api key" in err_str:
        return "🔑 Invalid License Key. Please check your settings."
    elif "429" in err_str or "resource" in err_str:
        return "⏳ Server is busy. Retrying automatically..."
    elif "pdf" in err_str or "syntax" in err_str:
        return "📄 This PDF is corrupted or password protected."
    else:
        return f"⚠️ Error: {str(e)}"

# --- DATABASE LOGIC (GOOGLE SHEETS) ---
def connect_to_sheet():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        sheet_url = st.secrets["private_sheet_url"]
        return client.open_by_url(sheet_url).sheet1
    except Exception as e:
        return None

def check_access(code):
    sheet = connect_to_sheet()
    if not sheet:
        return "⚠️ Database Connection Failed.", None, None
    try:
        records = sheet.get_all_records()
        for row in records:
            if str(row['username']) == code:
                if str(row['active']).upper() != "TRUE":
                    return "❌ Account Deactivated. Contact Support.", None, None
                used = int(row['used'])
                limit = int(row['limit'])
                if used >= limit:
                    return f"⚠️ Monthly Limit Reached ({used}/{limit}).", used, limit
                return "OK", used, limit
        return "❌ Invalid Access Code.", None, None
    except Exception as e:
        return "⚠️ Database Error. Try again.", None, None

def increment_usage(code):
    try:
        sheet = connect_to_sheet()
        cell = sheet.find(code)
        current_val = int(sheet.cell(cell.row, 2).value)
        sheet.update_cell(cell.row, 2, current_val + 1)
    except:
        pass 

# --- BACKEND LOGIC ---
def get_gemini_client():
    try:
        api_key = st.secrets["GOOGLE_API_KEY"]
        return genai.Client(api_key=api_key)
    except:
        return None

def create_excel_bytes(filename, data):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Redline Analysis"
    
    headers = ["Tenant", "Rent", "Deposit", "Risk Score", "Risk Summary"]
    ws.append(headers)
    ws.append([
        data.get("tenant_name"), data.get("monthly_rent"), 
        data.get("security_deposit"), data.get("risk_score"), 
        data.get("risk_flags")
    ])
    
    header_fill = PatternFill(start_color="8B0000", end_color="8B0000", fill_type="solid")
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal='center', vertical='center')
    
    ws.column_dimensions['A'].width = 30
    ws.column_dimensions['B'].width = 50
    ws.column_dimensions['C'].width = 50
    ws.column_dimensions['D'].width = 15
    ws.column_dimensions['E'].width = 100
    
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer

def analyze_lease(uploaded_file):
    client = get_gemini_client()
    if not client: 
        st.error(translate_error("403 API key not valid"))
        return None, None
    
    with st.spinner("Encrypting & Uploading to Neural Engine..."):
        try:
            bytes_data = uploaded_file.getvalue()
            # Use a generic name to avoid path issues
            temp_filename = "temp_upload.pdf" 
            with open(temp_filename, "wb") as f:
                f.write(bytes_data)
            cloud_file = client.files.upload(file=temp_filename)
        except Exception as e:
            st.error(translate_error(e))
            return None, None

    while cloud_file.state.name == "PROCESSING":
        time.sleep(1)
        cloud_file = client.files.get(name=cloud_file.name)

    if cloud_file.state.name == "FAILED":
        st.error(translate_error("pdfminer.pdfparser.PDFSyntaxError"))
        return None, None

    # THE BRAIN (Use secrets if available, else fallback to hardcoded for testing)
    try:
        sys_prompt = st.secrets["prompts"]["system_instruction"]
    except:
        sys_prompt = """
        Role: Real Estate Attorney. Extract: tenant_name, monthly_rent, security_deposit, risk_score (0-10), risk_flags.
        Output JSON only.
        """
    
    data = None
    max_retries = 3
    
    with st.spinner("🤖 AI Auditing Lease (Gemini 2.0 Flash)..."):
        for attempt in range(max_retries):
            try:
                response = client.models.generate_content(
                    model="gemini-2.0-flash",
                    contents=[cloud_file, sys_prompt]
                )
                text = response.text.replace("```json", "").replace("```", "").strip()
                data = json.loads(text)
                break 
            except exceptions.ResourceExhausted:
                time.sleep(5) 
            except Exception as e:
                st.error(translate_error(e))
                break
        
        try:
            client.files.delete(name=cloud_file.name)
        except:
            pass

    return data, create_excel_bytes(uploaded_file.name, data)

# --- FRONTEND UI ---
st.title("🏢 Redline AI | Enterprise")
st.markdown("### Automated Lease Abstraction Engine")

# 1. Sidebar Login
with st.sidebar:
    st.header("Client Portal")
    password = st.text_input("Access Code", type="password")
    
    status = "WAITING"
    if password:
        status, used, limit = check_access(password)
        if status == "OK":
            st.markdown("---")
            st.markdown(f"**Status:** ✅ Active")
            st.markdown(f"**Quota:** {used} / {limit}")
            st.progress(used / limit)
        else:
            st.error(status)

# 2. Main Logic
if password and status == "OK":
    uploaded_file = st.file_uploader("Upload Lease Agreement (PDF)", type=["pdf"])

    # Reset state if new file uploaded
    if "last_file_id" not in st.session_state:
        st.session_state["last_file_id"] = None
        
    if uploaded_file and uploaded_file.file_id != st.session_state["last_file_id"]:
        # New file detected, clear old results
        st.session_state["last_file_id"] = uploaded_file.file_id
        if "audit_result" in st.session_state:
            del st.session_state["audit_result"]
        if "audit_excel" in st.session_state:
            del st.session_state["audit_excel"]

    # The Logic Block
    if uploaded_file is not None:
        
        # A. The Trigger Button
        if st.button("🚀 Run Audit (-1 Credit)", type="primary"):
            data, excel_file = analyze_lease(uploaded_file)
            
            if data:
                # 1. Save to Memory (Session State)
                st.session_state["audit_result"] = data
                st.session_state["audit_excel"] = excel_file
                
                # 2. Deduct Credit
                increment_usage(password)
                st.toast("✅ Credit Deducted")
            else:
                st.error("Audit Failed. No credit deducted.")

        # B. The Persistent Display (Outside the button)
        if "audit_result" in st.session_state:
            data = st.session_state["audit_result"]
            excel_bytes = st.session_state["audit_excel"]
            
            st.success("✅ Audit Complete!")
            
            # Display Metrics
            rent_val = str(data.get("monthly_rent", "N/A"))
            risk_val = str(data.get("risk_score", "0"))
            deposit_val = str(data.get("security_deposit", "N/A"))

            col1, col2, col3 = st.columns(3)
            col1.metric("Risk Score", f"{risk_val}/10")
            col2.metric("Monthly Rent", rent_val) 
            col3.metric("Deposit", deposit_val)
            
            st.divider()
            
            # The Persistent Download Button
            st.download_button(
                label="📥 Download Excel Report",
                data=excel_bytes,
                file_name=f"AUDIT_REPORT.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
