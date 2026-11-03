import os
import traceback
from pathlib import Path

import uvicorn
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from auth import (
    COOKIE_NAME,
    TOKEN_EXPIRE_HOURS,
    authenticate_user,
    claim_thread,
    create_access_token,
    create_user,
    decode_access_token,
    init_users_table,
    user_owns_thread,
    validate_signup,
)
from backend import resume_travel_agent, run_travel_agent
from email_service import send_welcome_email

BASE_DIR = Path(__file__).resolve().parent

# On Render (https) set COOKIE_SECURE=true. On your laptop keep it false.
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"

app = FastAPI(
    title="TripMate AI",
    description=(
        "LangGraph Multi-Agent Travel Planner with Supervisor, Guardrails, "
        "Human-in-the-Loop, and FastAPI Frontend"
    ),
    version="2.0.0",
)

app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static",
)

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Creates the users and thread_owners tables if they do not exist yet
init_users_table()


# ---------------------------------------------------------------------------
# Request shapes
# ---------------------------------------------------------------------------
class TravelRequest(BaseModel):
    message: str
    thread_id: str | None = None


class ApprovalRequest(BaseModel):
    thread_id: str = Field(min_length=1)
    approved: bool
    feedback: str = ""


class SignupRequest(BaseModel):
    name: str
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


# ---------------------------------------------------------------------------
# Helper: who is logged in?
# ---------------------------------------------------------------------------
def get_current_user(request: Request) -> dict | None:
    """Read the login cookie and return the user, or None if not logged in."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    return decode_access_token(token)


def not_logged_in_response() -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"success": False, "error": "Please log in again."},
    )


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    user = get_current_user(request)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"user_name": user["name"]},
    )


@app.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request):
    if get_current_user(request) is not None:
        return RedirectResponse(url="/", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="signup.html",
        context={},
    )


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if get_current_user(request) is not None:
        return RedirectResponse(url="/", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={},
    )


@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


# ---------------------------------------------------------------------------
# Signup / login API
# ---------------------------------------------------------------------------
@app.post("/api/signup")
def signup(request_data: SignupRequest, background_tasks: BackgroundTasks):
    error = validate_signup(
        request_data.name, request_data.email, request_data.password
    )
    if error:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": error},
        )

    try:
        user = create_user(
            request_data.name, request_data.email, request_data.password
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=409,
            content={"success": False, "error": str(exc)},
        )
    except Exception as exc:
        print("SIGNUP ERROR:", exc)
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "Signup failed. Please try again."},
        )

    # Sent in the background so signup replies instantly
    background_tasks.add_task(send_welcome_email, user["email"], user["name"])

    return JSONResponse(
        content={"success": True, "message": "Account created successfully!"}
    )


@app.post("/api/login")
def login(request_data: LoginRequest):
    try:
        user = authenticate_user(request_data.email, request_data.password)
    except Exception as exc:
        print("LOGIN ERROR:", exc)
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "Login failed. Please try again."},
        )

    if user is None:
        return JSONResponse(
            status_code=401,
            content={"success": False, "error": "Invalid email or password."},
        )

    token = create_access_token(user["id"], user["name"], user["email"])

    response = JSONResponse(content={"success": True, "name": user["name"]})
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
        max_age=TOKEN_EXPIRE_HOURS * 3600,
        path="/",
    )
    return response


@app.get("/api/me")
async def me(request: Request):
    user = get_current_user(request)
    if user is None:
        return not_logged_in_response()
    return {"success": True, **user}


# ---------------------------------------------------------------------------
# Travel agent API (login required)
# ---------------------------------------------------------------------------
@app.post("/api/travel")
def travel_planner(request_data: TravelRequest, request: Request):
    user = get_current_user(request)
    if user is None:
        return not_logged_in_response()

    try:
        user_message = request_data.message.strip()

        if not user_message:
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": "Message cannot be empty."},
            )

        # if request_data.thread_id and not claim_thread(
        #     request_data.thread_id, user["id"]
        # ):
        #     return JSONResponse(
        #         status_code=403,
        #         content={
        #             "success": False,
        #             "error": "This plan belongs to another account.",
        #         },
        #     )

        # result = run_travel_agent(
        #     user_input=user_message,
        #     thread_id=request_data.thread_id,
        # )
        
        thread_id = request_data.thread_id
        if thread_id and not claim_thread(thread_id, user["id"]):
            thread_id = None  # not this user's plan: start a fresh one

        result = run_travel_agent(
            user_input=user_message,
            thread_id=thread_id,
        )

        claim_thread(result["thread_id"], user["id"])

        return JSONResponse(content={"success": True, **result})

    except Exception as exc:
        print("ERROR:", exc)
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(exc)},
        )


@app.post("/api/travel/approve")
def approve_travel_plan(request_data: ApprovalRequest, request: Request):
    user = get_current_user(request)
    if user is None:
        return not_logged_in_response()

    try:
        if not request_data.approved and not request_data.feedback.strip():
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "error": "Please provide revision feedback when rejecting the draft.",
                },
            )

        if not user_owns_thread(request_data.thread_id, user["id"]):
            return JSONResponse(
                status_code=403,
                content={
                    "success": False,
                    "error": "Plan not found for this account.",
                },
            )

        result = resume_travel_agent(
            thread_id=request_data.thread_id,
            approved=request_data.approved,
            feedback=request_data.feedback,
        )

        return JSONResponse(content={"success": True, **result})

    except Exception as exc:
        print("APPROVAL ERROR:", exc)
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(exc)},
        )


# ---------------------------------------------------------------------------
# Public utility routes
# ---------------------------------------------------------------------------
@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "message": "TripMate AI API is running",
        "features": [
            "supervisor_agent",
            "input_guardrail",
            "human_in_the_loop",
        ],
    }


@app.get("/favicon.ico")
async def favicon():
    return JSONResponse(content={})


if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host="127.0.0.1",
        port=8001,
        reload=True,
    )