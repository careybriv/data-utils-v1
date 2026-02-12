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
        return "🔑 Invalid License Key. Check Google Cloud Console billing."
    elif "429" in err_str or "resource" in err_str:
        return "⏳ Server is busy. Retrying automatically..."
    elif "pdf" in err_str or "syntax" in err_str:
        return "📄 This PDF is corrupted or password protected."
    else:
        return "⚠️ Something went wrong. Please try again."

# --- DATABASE LOGIC (GOOGLE SHEETS) ---
def connect_to_sheet():
    # Connects to Google Sheets using secrets
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    # Opens the sheet by URL
    sheet_url = st.secrets["private_sheet_url"]
    return client.open_by_url(sheet_url).sheet1

def check_access(code):
    try:
        sheet = connect_to_sheet()
        # Get all records to find the user
        records = sheet.get_all_records()
        
        for i, row in enumerate(records):
            # Check if code matches (Column A: username)
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
        return f"⚠️ Database Error: {str(e)}", None, None

def increment_usage(code):
    try:
        sheet = connect_to_sheet()
        cell = sheet.find(code)
        # 'used' is in the 2nd column (Column B), so we update that row, col 2
        current_val = int(sheet.cell(cell.row, 2).value)
        sheet.update_cell(cell.row, 2, current_val + 1)
    except:
        pass # Fail silently if increment fails to avoid user panic

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
    
    for row in ws.iter_rows(min_row=2, max_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical='top')

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer

def analyze_lease(uploaded_file):
    client = get_gemini_client()
    if not client: 
        st.error("🔑 Invalid License Key.")
        return None, None
    
    with st.spinner("Encrypting & Uploading to Neural Engine..."):
        try:
            bytes_data = uploaded_file.getvalue()
            temp_filename = "temp_" + uploaded_file.name
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
        st.error(translate_error("PDFSyntaxError"))
        return None, None

    prompt = """
    Role: Senior Real Estate Attorney.
    Task: Audit lease for Deal Killers.
    CRITICAL:
    1. RENT: Calculate Total Monthly Liability (Base + NNN).
    2. DEPOSIT: Find Existing/Transferred Deposits.
    3. RISK: Check for Gross-Up clauses.
    Output JSON only.
    """
    
    data = None
    max_retries = 3
    
    with st.spinner("🤖 AI Auditing Lease (Gemini 2.0 Flash)..."):
        for attempt in range(max_retries):
            try:
                response = client.models.generate_content(
                    model="gemini-2.0-flash",
                    contents=[cloud_file, prompt]
                )
                text = response.text.replace("```json", "").replace("```", "").strip()
                data = json.loads(text)
                break 
            except exceptions.ResourceExhausted:
                st.toast(f"⏳ Server busy. Retrying ({attempt+1}/{max_retries})...")
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

if 'error_shown' not in st.session_state:
    st.session_state['error_shown'] = False

# 1. Sidebar Login (HIDDEN USERNAME)
with st.sidebar:
    st.header("Client Portal")
    password = st.text_input("Access Code", type="password")
    
    if password:
        # Check Sheet Database
        status, used, limit = check_access(password)
        
        if status == "OK":
            st.markdown("---")
            # PRIVACY FIX: We do NOT show "Logged in as..."
            st.markdown(f"**Status:** ✅ Active")
            st.markdown(f"**Quota:** {used} / {limit}")
            st.progress(used / limit)
        else:
            st.error(status)

# 2. Main App Logic
if password:
    if status == "OK":
        uploaded_file = st.file_uploader("Upload Lease Agreement (PDF)", type=["pdf"])

        if uploaded_file is not None:
            if st.button("🚀 Run Audit (-1 Credit)", type="primary"):
                st.session_state['error_shown'] = False
                
                data, excel_file = analyze_lease(uploaded_file)
                
                if data:
                    increment_usage(password)
                    st.success("✅ Audit Complete! Credit Deducted.")
                    
                    col1, col2, col3 = st.columns(3)
                    col1.metric("Risk Score", f"{data.get('risk_score')}/10")
                    col2.metric("Monthly Rent", data.get("monthly_rent").split("$")[0] + "...") 
                    col3.metric("Deposit", data.get("security_deposit"))
                    
                    st.download_button(
                        label="📥 Download Excel Report",
                        data=excel_file,
                        file_name=f"AUDIT_{uploaded_file.name}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
                    time.sleep(1)
                    st.rerun()
