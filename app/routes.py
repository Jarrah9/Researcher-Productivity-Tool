from fastapi import APIRouter, Request, Path, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from contextlib import redirect_stdout
from app.scrapers.update import update_all
from app.scrapers.helpers.util import match_journals
from app.scripts.CSV_imports import print_issns_in_batches
from app.helpers.researchers_funcs import get_researcher_data
from app.helpers.researcher_profile_funcs import get_researcher_profile
from app.helpers.universities_funcs import get_university_data
from app.helpers.admin_funcs import (
    download_master_csv,
    download_ABDC_template,
    download_clarivate_template,
    download_UWA_staff_field_template,
    save_uploaded_file,
    replace_ABDC_rankings,
    import_clarivate,
    update_UWA_staff_fields,
    reupload_master_spreadsheet,
    switch_db
)
from app.helpers.auth_funcs import authenticate_user

import io
import sys
import threading
import traceback
import os

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

# --- New Global State Variable with 'logs' key ---
# This dictionary now holds logs in addition to progress and messages.
scraper_status_data = {"progress": 0, "message": "Not started", "logs": []}
RESEARCHER_STATS_CACHE = None
UNIVERSITY_STATS_CACHE = None

#------------------------
# Helper function
#------------------------
def competition_rank(sorted_rows, value_fn):
    out = []
    prev = object()
    rank = 0
    for i, row in enumerate(sorted_rows, start=1):
        val = value_fn(row) or 0
        if val != prev:
            rank = i
            prev = val
        out.append((rank, row))
    return out


# ------------------------
# Home page
# ------------------------
@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {"request": request}
    )


# ------------------------
# Documentation page
# ------------------------
@router.get("/documentation", response_class=HTMLResponse)
def documentation(request: Request):
    return templates.TemplateResponse(
        "documentation.html",
        {"request": request}
    )


# ------------------------
# Researcher level ranking page
# ------------------------
@router.get("/researchers", response_class=HTMLResponse)
def researchers(request: Request):
    global RESEARCHER_STATS_CACHE
    researcher_list, variable_label, RESEARCHER_STATS_CACHE = get_researcher_data(request, RESEARCHER_STATS_CACHE)
    researcher_list = sorted(
        researcher_list,
        key=lambda d: d.get("variable_value") or 0,
        reverse=True
    )

    ranked = competition_rank(
        researcher_list,
        value_fn=lambda d: d.get("variable_value") or 0
    )
    researchers_with_rank = [{**d, "rank": rk} for rk, d in ranked]

    return templates.TemplateResponse(
        "researchers.html",
        {
            "request": request,
            "researchers": researchers_with_rank, 
            "variable_label": variable_label
        }
    )


# ------------------------
# Researcher profile/detail page
# ------------------------
@router.get("/researchers/{researcher_id}", response_class=HTMLResponse)
def researcher_profile(request: Request, researcher_id: int = Path(...)):
    researcher_data, pub_list = get_researcher_profile(researcher_id)
    return templates.TemplateResponse(
        "researcher_profile.html",
        {"request": request, "researcher": researcher_data, "publications": pub_list},
    )


# ------------------------
# University ranking page (split researchers into Accounting vs Finance)
# ------------------------
@router.get("/universities", response_class=HTMLResponse)
def universities(request: Request):
    global UNIVERSITY_STATS_CACHE
    university_list, variable_label, UNIVERSITY_STATS_CACHE = get_university_data(request, UNIVERSITY_STATS_CACHE)
    sort_by = request.query_params.get("sort_by", "total_researchers")
    ranked = competition_rank(
        sorted(university_list, key=lambda u: u.get("variable_value") or 0, reverse=True),
        value_fn=lambda u: u.get("variable_value") or 0
    )
    universities_with_rank = [{**u, "rank": rk} for rk, u in ranked]

    return templates.TemplateResponse(
        "universities.html",
        {
            "request": request,
            "universities": universities_with_rank,
            "variable_label": variable_label,
            "sort_by": sort_by
        }
    )


# ------------------------
# Admin page
# ------------------------
@router.get("/admin", response_class=HTMLResponse)
async def admin(request: Request):
    user = request.session.get("user")
    flash = request.session.pop("flash", None)
    if not user:
        # Not logged in, redirect to login or show error
        return templates.TemplateResponse("login.html", {"request": request, "error": None})
    # Find all .db files in app/ folder
    from pathlib import Path
    db_files = list(Path("app").glob("*.db"))
    db_list = [f.stem for f in db_files]

    from app import database as db_module
    env_db = os.getenv("DATABASE_NAME")
    current_db = getattr(db_module, "CURRENT_DB_NAME", None) or env_db or "main"
    if current_db not in db_list:
        current_db = "main"

    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "user": user,
            "flash": flash,
            "db_list": db_list,
            "current_db": current_db
        }
    )

@router.post("/admin/switch-db")
async def switch_db_route(request: Request):
    form = await request.form()
    db_name = form.get("db_name")
    user = request.session.get("user")
    if not user or not db_name:
        return RedirectResponse(url="/", status_code=303)
    switch_db(db_name)
    global RESEARCHER_STATS_CACHE, UNIVERSITY_STATS_CACHE
    RESEARCHER_STATS_CACHE = None  # Clear researcher cache to reflect updated data
    UNIVERSITY_STATS_CACHE = None  # Clear university cache to reflect updated data
    request.session["flash"] = f"Switched to database '{db_name}'."
    return RedirectResponse(url="/admin", status_code=303)

@router.post("/admin/delete-db")
async def delete_db_route(request: Request):
    form = await request.form()
    db_name = form.get("db_name")
    user = request.session.get("user")
    if not user or not db_name:
        return RedirectResponse(url="/", status_code=303)
    from app.helpers.admin_funcs import delete_db
    try:
        delete_db(db_name)
        request.session["flash"] = f"Database '{db_name}' deleted successfully."
    except Exception as e:
        request.session["flash"] = f"Error deleting database '{db_name}': {e}"
    return RedirectResponse(url="/admin", status_code=303)

@router.post("/admin/rename-db")
async def rename_db_route(request: Request):
    form = await request.form()
    old_db_name = form.get("old_db_name")
    new_db_name = form.get("new_db_name")
    user = request.session.get("user")
    if not user or not old_db_name or not new_db_name:
        return RedirectResponse(url="/", status_code=303)
    from app.helpers.admin_funcs import rename_db
    try:
        rename_db(old_db_name, new_db_name)
        request.session["flash"] = f"Database '{old_db_name}' renamed to '{new_db_name}' successfully."
    except Exception as e:
        request.session["flash"] = f"Error renaming database '{old_db_name}': {e}"
    return RedirectResponse(url="/admin", status_code=303)

@router.post("/login")
async def login_post(request: Request):
    form = await request.form()
    username = form.get("username")
    password = form.get("password")
    if authenticate_user(username, password):
        request.session["user"] = username
        return templates.TemplateResponse(
            "admin.html",
            {"request": request, "user": username}
        )
    else:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid username or password."}
        )


@router.post("/logout")
def logout_post(request: Request):
    request.session.pop("user", None)
    return RedirectResponse(url="/", status_code=303)


# ------------------------
# Admin Download Functionalities
# ------------------------
@router.get("/admin/download/researchers.csv")
def download_master_csv_route(request: Request):
    return download_master_csv(request)


@router.get("/admin/download/abdc_template.csv")
def abdc_template_route():
    return download_ABDC_template()


@router.get("/admin/download/clarivate_template.csv")
def clarivate_template_route():
    return download_clarivate_template()


@router.get("/admin/download/UWA_staff_field_template.csv")
def uwa_staff_field_template_route():
    return download_UWA_staff_field_template()

# ------------------------
# Admin Upload Functionalities
# ------------------------

@router.post("/admin/upload/master_csv")
async def upload_master_csv(
    request: Request,
    master_csv: UploadFile = File(None)
):
    if not master_csv:
        return templates.TemplateResponse(
            "admin.html",
            {"request": request, "user": request.session.get("user"), "error": "No file uploaded."}
        )
    file_path = save_uploaded_file(master_csv, "master_spreadsheet_upload.csv")
    # Flash message
    request.session["flash"] = (
        f"File '{master_csv.filename}' uploaded successfully."
    )
    try:
        reupload_master_spreadsheet(file_path)
    except:
        request.session["flash"] += f" However, there was an error processing the file. Please ensure it is correctly formatted."
    global RESEARCHER_STATS_CACHE, UNIVERSITY_STATS_CACHE
    RESEARCHER_STATS_CACHE = None  # Clear researcher cache to reflect updated data
    UNIVERSITY_STATS_CACHE = None  # Clear university cache to reflect updated data
    return RedirectResponse(url="/admin", status_code=303)

@router.post("/admin/upload/abdc")
async def upload_abdc(
    request: Request,
    abdc_csv: UploadFile = File(None)
):
    if not abdc_csv:
        return templates.TemplateResponse(
            "admin.html",
            {"request": request, "user": request.session.get("user"), "error": "No file uploaded."}
        )
    file_path = save_uploaded_file(abdc_csv, "ABDC_upload.csv")
    # Flash message
    request.session["flash"] = (
        f"File '{abdc_csv.filename}' uploaded successfully."
    )
    try:
        replace_ABDC_rankings(file_path)
    except Exception as e:
        request.session["flash"] += f" However, there was an error processing the file. Please ensure it is correctly formatted."
    try:
        import_clarivate("/app/files/uploads_current/clarivate_upload.csv")  # Re-import all JIF data to refresh journal matches
    except Exception as e:
        request.session["flash"] += f"No clarivate data found, please upload clarivate data as well."
    match_journals(force=True)  # Re-match journals after ABDC update
    global RESEARCHER_STATS_CACHE, UNIVERSITY_STATS_CACHE
    RESEARCHER_STATS_CACHE = None  # Clear researcher cache to reflect updated journal data
    UNIVERSITY_STATS_CACHE = None  # Clear university cache to reflect updated journal data
    return RedirectResponse(url="/admin", status_code=303)

@router.post("/admin/upload/clarivate")
async def upload_clarivate(
    request: Request,
    clarivate_csv: UploadFile = File(None)
):
    if not clarivate_csv:
        return templates.TemplateResponse(
            "admin.html",
            {"request": request, "user": request.session.get("user"), "error": "No file uploaded."}
        )
    # Save the uploaded file (you may want to implement a save_uploaded_file for clarivate as well)
    file_path = save_uploaded_file(clarivate_csv, "clarivate_upload.csv")
    # Flash message
    request.session["flash"] = (
        f"File '{clarivate_csv.filename}' uploaded successfully"
    )
    try:
        import_clarivate(file_path)
    except Exception as e:
        request.session["flash"] += f" However, there was an error processing the file. Please ensure it is correctly formatted."
    global RESEARCHER_STATS_CACHE, UNIVERSITY_STATS_CACHE
    RESEARCHER_STATS_CACHE = None  # Clear researcher cache to reflect updated journal data
    UNIVERSITY_STATS_CACHE = None  # Clear university cache to reflect updated journal data
    return RedirectResponse(url="/admin", status_code=303)

@router.post("/admin/upload/uwa_staff_field")
async def upload_uwa_staff_field(
    request: Request,
    uwa_staff_field_csv: UploadFile = File(None)
):
    if not uwa_staff_field_csv:
        return templates.TemplateResponse(
            "admin.html",
            {"request": request, "user": request.session.get("user"), "error": "No file uploaded."}
        )
    # Save the uploaded file
    file_path = save_uploaded_file(uwa_staff_field_csv, "UWA_staff_field_upload.csv")
    # Flash message
    request.session["flash"] = (
        f"File '{uwa_staff_field_csv.filename}' uploaded successfully."
    )
    try:
        update_UWA_staff_fields(file_path)
    except Exception as e:
        request.session["flash"] += f" However, there was an error processing the file. Please ensure it is correctly formatted."
    global RESEARCHER_STATS_CACHE, UNIVERSITY_STATS_CACHE
    RESEARCHER_STATS_CACHE = None  # Clear researcher cache to reflect updated journal data
    UNIVERSITY_STATS_CACHE = None  # Clear university cache to reflect updated journal data
    return RedirectResponse(url="/admin", status_code=303)

@router.get("/admin/issn_batches")
async def issn_batches(request: Request):
    user = request.session.get("user")
    flash = request.session.pop("flash", None)
    issn_batches_content = ""
    try:
        with open("app/files/temp/issn_batches.txt", "r", encoding="utf-8") as f:
            issn_batches_content = f.read()
    except Exception as e:
        print_issns_in_batches()  # Generate the file if it doesn't exist
        try:
            with open("app/files/temp/issn_batches.txt", "r", encoding="utf-8") as f:
                issn_batches_content = f.read()
        except Exception as e:
            issn_batches_content = f"Error reading ISSN batches: {e}"
    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "user": user,
            "flash": flash,
            "issn_batches_content": issn_batches_content
        }
    )

# ------------------------
# Scraper Endpoints
# ------------------------

class FrontendLogHandler(io.StringIO):
    """
    A custom stream handler that captures print statements
    and appends them to our global status dictionary's log list.
    """
    def write(self, s):
        global scraper_status_data
        line = s.strip()
        if line:
            scraper_status_data["logs"].append(line)
        # Also write to the actual stdout to see logs in the terminal
        sys.__stdout__.write(s)
        sys.__stdout__.flush()

def run_scraper_task():
    """
    This function runs in a separate thread and uses the FrontendLogHandler
    to capture all print outputs.
    """
    global scraper_status_data
    scraper_status_data['progress'] = 0
    scraper_status_data['message'] = 'Scraping started...'
    scraper_status_data['logs'] = [] # Reset logs for a new run
    
    log_capture = FrontendLogHandler()
    
    try:
        # Redirect all standard output within this block to our handler
        with redirect_stdout(log_capture):
            update_all(progress_callback=update_progress)
        
        if scraper_status_data.get('progress') != -1:
             scraper_status_data['message'] = 'Completed successfully!'

    except Exception as e:
        # Capture any exceptions as well
        error_message = traceback.format_exc()
        scraper_status_data['logs'].append(error_message)
        scraper_status_data['progress'] = -1
        scraper_status_data['message'] = f"An error occurred: {e}"

def update_progress(progress):
    """Callback function to update the global progress status."""
    global scraper_status_data
    scraper_status_data['progress'] = progress

@router.post("/admin/run-scraper")
async def run_scraper(request: Request):
    """Endpoint to start the scraper thread."""
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/", status_code=303)
    
    if any("run_scraper_task" in t.name for t in threading.enumerate()):
        return JSONResponse(content={"message": "Scraper is already running."}, status_code=409)

    global scraper_status_data
    scraper_status_data = {"progress": 0, "message": "Not started", "logs": []}
    
    thread = threading.Thread(target=run_scraper_task, name="run_scraper_task")
    thread.start()
    
    return JSONResponse(content={"message": "Scraper started"})

@router.get("/admin/scraper-status")
async def scraper_status(request: Request):
    """Endpoint for the frontend to poll for scraper progress and logs."""
    global scraper_status_data
    return JSONResponse(content=scraper_status_data)