import streamlit as st
import os
import time
import json
import io
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from google import genai
from google.api_core import exceptions

# --- CONFIGURATION ---
PAGE_TITLE = "Redline AI | Enterprise"
PAGE_ICON = "🏢"
DB_FILE = "clients.json"

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

# --- HUMAN ERROR TRANSLATOR (Your Custom Messages) ---
def translate_error(e):
    """Converts computer crashes into the exact polite messages you defined."""
    err_str = str(e).lower()
    
    # 1. Internet / Connection
    if "11001" in err_str or "connection" in err_str or "socket" in err_str or "gaierror" in err_str:
        return "🌐 No Internet Connection. Please check your WiFi."
    
    # 2. API Key / Permission
    elif "403" in err_str or "api key" in err_str:
        return "🔑 Invalid License Key. Please check your settings."
    
    # 3. Quota / Busy (Caught by retry loop mostly, but here as backup)
    elif "429" in err_str or "resource" in err_str or "quota" in err_str:
        return "⏳ Server is busy. Retrying automatically..."
    
    # 4. PDF Issues
    elif "pdf" in err_str or "syntax" in err_str or "corrupted" in err_str:
        return "📄 This PDF is corrupted or password protected."
    
    # 5. File Access (Rare in Web App, but included for consistency)
    elif "permission" in err_str or "errno 13" in err_str:
        return "📂 The file is currently in use. Please close it and try again."
    
    # 6. JSON / Parsing Errors
    elif "json" in err_str:
        return "⚠️ AI Confusion. The model failed to structure the data. Please hit 'Run' again."
        
    # 7. Catch-All (The Safety Net)
    else:
        return "⚠️ Something went wrong. Please try again."

# --- DATABASE LOGIC ---
def load_db():
    if not os.path.exists(DB_FILE):
        default_db = {
            "BRASACAP": {"limit": 20, "used": 0, "active": True},
            "DEMO": {"limit": 3, "used": 0, "active": True}
        }
        with open(DB_FILE, "w") as f:
            json.dump(default_db, f)
    with open(DB_FILE, "r") as f:
        return json.load(f)

def save_db(db):
    with open(DB_FILE, "w") as f:
        json.dump(db, f)

def check_access(code):
    db = load_db()
    if code in db:
        client = db[code]
        if not client["active"]:
            return "❌ Account Deactivated. Contact Support."
        if client["used"] >= client["limit"]:
            return "⚠️ Monthly Limit Reached (20/20). Please renew subscription."
        return "OK"
    return "❌ Invalid Access Code."

def increment_usage(code):
    db = load_db()
    if code in db:
        db[code]["used"] += 1
        save_db(db)

# --- BACKEND LOGIC ---
def get_gemini_client():
    try:
        api_key = st.secrets["GOOGLE_API_KEY"]
        return genai.Client(api_key=api_key)
    except Exception:
        # Fallback for local testing if secrets.toml is missing
        st.error("🔑 Invalid License Key. Please check your settings (secrets.toml).")
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
    if not client: return None, None
    
    # 1. Upload Phase
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

    # 2. Processing Wait
    while cloud_file.state.name == "PROCESSING":
        time.sleep(1)
        cloud_file = client.files.get(name=cloud_file.name)

    if cloud_file.state.name == "FAILED":
        st.error(translate_error("PDFSyntaxError")) # Force the PDF error message
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
    
    # 3. AI Analysis (With Auto-Retry)
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
                # Use your specific message here in the toast
                st.toast(f"⏳ Server is busy. Retrying automatically ({attempt+1}/{max_retries})...")
                time.sleep(5) 
                
            except Exception as e:
                st.error(translate_error(e))
                break
        
        # Cleanup
        try:
            client.files.delete(name=cloud_file.name)
            os.remove(temp_filename)
        except:
            pass

    if not data:
        if not st.session_state.get('error_shown'):
             st.error("⚠️ Something went wrong. Please try again.")
        return None, None

    return data, create_excel_bytes(uploaded_file.name, data)

# --- FRONTEND UI ---
st.title("🏢 Redline AI | Enterprise")
st.markdown("### Automated Lease Abstraction Engine")

if 'error_shown' not in st.session_state:
    st.session_state['error_shown'] = False

# 1. Sidebar Login
with st.sidebar:
    st.header("Client Portal")
    password = st.text_input("Access Code", type="password")
    
    if password:
        db = load_db()
        if password in db:
            client_data = db[password]
            st.markdown("---")
            st.success(f"**Logged in as:** {password}")
            st.markdown(f"**Credits:** {client_data['used']} / {client_data['limit']}")
            st.progress(client_data['used'] / client_data['limit'])
        else:
            st.error("Access Code Not Found")

# 2. Main App Logic
if password:
    access_status = check_access(password)
    
    if access_status == "OK":
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
    elif password:
        st.error(access_status)
else:
    st.info("Please enter your Client Access Code to unlock the engine.")