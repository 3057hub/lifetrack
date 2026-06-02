import os
import json
import re
import secrets
import hashlib
import logging
from datetime import datetime, date, timedelta, timezone
from typing import Optional
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Depends, HTTPException, Header, Request, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Boolean
from sqlalchemy.orm import Session, declarative_base
from pydantic import BaseModel, Field

# ── logging ──────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("lifetrack")

CHINA_TZ = timezone(timedelta(hours=8))

# ── database ──────────────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lifetrack.db")
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, nullable=False, unique=True)
    password_hash = Column(String, nullable=False)
    salt = Column(String, nullable=False)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class Token(Base):
    __tablename__ = "tokens"
    id = Column(Integer, primary_key=True, autoincrement=True)
    token = Column(String, nullable=False, unique=True, index=True)
    user_id = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class Activity(Base):
    __tablename__ = "activities"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True, default=1)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=True)
    description = Column(Text, default="")
    tags = Column(String, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class Report(Base):
    __tablename__ = "reports"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True, default=1)
    period_start = Column(DateTime, nullable=False)
    period_end = Column(DateTime, nullable=False)
    summary_text = Column(Text, default="")
    memory_abstract = Column(Text, default="")
    user_feedback = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class Goal(Base):
    __tablename__ = "goals"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True, default=1)
    content = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


Base.metadata.create_all(bind=engine)


# ── auth helpers ──────────────────────────────────────────────


def hash_password(password: str, salt: str = None) -> tuple[str, str]:
    if salt is None:
        salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000)
    return h.hex(), salt


def get_current_user(db: Session, token_str: str) -> Optional[User]:
    t = db.query(Token).filter(Token.token == token_str).first()
    if not t:
        return None
    return db.query(User).filter(User.id == t.user_id).first()


def get_db():
    db = Session(engine)
    try:
        yield db
    finally:
        db.close()


# ── pydantic schemas ──────────────────────────────────────────


class ActivityCreate(BaseModel):
    start_time: str  # ISO 8601
    end_time: Optional[str] = None
    description: str = Field(default="", max_length=500)
    tags: str = Field(default="", max_length=200)


class ActivityUpdate(BaseModel):
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    description: Optional[str] = Field(default=None, max_length=500)
    tags: Optional[str] = Field(default=None, max_length=200)


class PasswordCheck(BaseModel):
    password: str


class GoalCreate(BaseModel):
    content: str = Field(..., max_length=500)


class ReportGenerate(BaseModel):
    period: str = "day"  # "day" or "week"


class ReportFeedback(BaseModel):
    feedback: str  # "helpful", "executed", "ignored"


# ── deepseek config ───────────────────────────────────────────

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"


# ── auth ──────────────────────────────────────────────────────

APP_PASSWORD = os.environ.get("APP_PASSWORD", "")


def verify_token(authorization: Optional[str] = Header(None), db: Session = Depends(get_db)):
    """Token-based auth. Falls back to old APP_PASSWORD for backward compat."""
    if not authorization or not authorization.startswith("Bearer "):
        # fallback: old password header
        pass
    else:
        token_str = authorization[7:]
        user = get_current_user(db, token_str)
        if user:
            return user
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")

    # Legacy: check X-App-Password header for backward compat
    return None


def verify_legacy_password(x_app_password: Optional[str] = Header(None)):
    if APP_PASSWORD and x_app_password != APP_PASSWORD:
        raise HTTPException(status_code=401, detail="密码错误")


# ── helpers ───────────────────────────────────────────────────


def parse_iso(s: str) -> datetime:
    s = s.replace("Z", "+00:00")
    return datetime.fromisoformat(s)


def activity_to_dict(a: Activity) -> dict:
    return {
        "id": a.id,
        "start_time": a.start_time.isoformat() + "Z",
        "end_time": (a.end_time.isoformat() + "Z") if a.end_time else None,
        "description": a.description,
        "tags": a.tags,
        "created_at": (a.created_at.isoformat() + "Z") if a.created_at else None,
    }


# ── app ───────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("LifeTrack 启动")
    yield
    logger.info("LifeTrack 关闭")


app = FastAPI(title="LifeTrack", lifespan=lifespan)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"未捕获异常: {exc}", exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "服务器内部错误"})


# ── auth routes ───────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str

class UserCreate(BaseModel):
    username: str = Field(..., max_length=50)
    password: str = Field(..., max_length=100)


@app.post("/api/auth/login")
def auth_login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == body.username).first()
    if not user:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    h, _ = hash_password(body.password, user.salt)
    if h != user.password_hash:
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    # create token
    token_str = secrets.token_hex(32)
    token = Token(token=token_str, user_id=user.id)
    db.add(token)
    db.commit()
    return {"token": token_str, "username": user.username, "is_admin": user.is_admin}


@app.post("/api/auth/register")
def auth_register(body: UserCreate, db: Session = Depends(get_db), user: Optional[User] = Depends(verify_token)):
    # only admin can create users (or first user with legacy password)
    if user and not user.is_admin:
        raise HTTPException(status_code=403, detail="仅管理员可创建用户")
    if not user:
        verify_legacy_password()

    existing = db.query(User).filter(User.username == body.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="用户名已存在")

    pw_hash, salt = hash_password(body.password)
    new_user = User(username=body.username, password_hash=pw_hash, salt=salt, is_admin=False)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    logger.info(f"用户已创建: {body.username} id={new_user.id}")
    return {"ok": True, "id": new_user.id, "username": new_user.username}


@app.post("/api/auth/verify")
def auth_verify(body: PasswordCheck):
    # Legacy endpoint — kept for backward compatibility
    if not APP_PASSWORD:
        return {"valid": True, "message": "未设置密码，允许访问"}
    if body.password == APP_PASSWORD:
        return {"valid": True, "message": "验证通过"}
    return JSONResponse(status_code=401, content={"valid": False, "detail": "密码错误"})


def get_user_id(user: Optional[User] = Depends(verify_token), x_app_password: Optional[str] = Header(None)) -> int:
    """Returns user_id from token, or falls back to legacy password for existing data (user_id=1)."""
    if user:
        return user.id
    # Legacy: use APP_PASSWORD, assign to user_id=1 (default admin)
    verify_legacy_password(x_app_password)
    return 1


# ── activity routes ───────────────────────────────────────────


@app.post("/api/activities")
def create_activity(body: ActivityCreate, db: Session = Depends(get_db), user_id: int = Depends(get_user_id)):
    logger.info(f"创建活动: user={user_id} start={body.start_time}")
    try:
        start = parse_iso(body.start_time)
    except Exception:
        raise HTTPException(status_code=400, detail="start_time 格式错误，需要 ISO 8601")

    end = None
    if body.end_time:
        try:
            end = parse_iso(body.end_time)
        except Exception:
            raise HTTPException(status_code=400, detail="end_time 格式错误，需要 ISO 8601")

    activity = Activity(user_id=user_id, start_time=start, end_time=end, description=body.description, tags=body.tags)
    db.add(activity)
    db.commit()
    db.refresh(activity)
    logger.info(f"活动已创建 id={activity.id}")
    return activity_to_dict(activity)


@app.get("/api/activities")
def list_activities(date_param: Optional[str] = None, db: Session = Depends(get_db), user_id: int = Depends(get_user_id)):
    logger.info(f"查询活动 user={user_id} date={date_param}")
    query = db.query(Activity).filter(Activity.user_id == user_id).order_by(Activity.start_time.desc())

    if date_param:
        try:
            target_date = date.fromisoformat(date_param)
            day_start = datetime(target_date.year, target_date.month, target_date.day)
            day_end = day_start + timedelta(days=1)
            query = query.filter(Activity.start_time >= day_start, Activity.start_time < day_end)
        except Exception:
            raise HTTPException(status_code=400, detail="date 格式错误，需要 YYYY-MM-DD")

    activities = query.limit(200).all()
    logger.info(f"返回 {len(activities)} 条记录")
    return [activity_to_dict(a) for a in activities]


@app.get("/api/activities/active")
def get_active_activity(db: Session = Depends(get_db), user_id: int = Depends(get_user_id)):
    active = db.query(Activity).filter(Activity.user_id == user_id, Activity.end_time == None).order_by(Activity.start_time.desc()).first()
    if not active:
        return {"active": None}
    return {"active": activity_to_dict(active)}


@app.put("/api/activities/{activity_id}")
def update_activity(activity_id: int, body: ActivityUpdate, db: Session = Depends(get_db), user_id: int = Depends(get_user_id)):
    logger.info(f"更新活动 id={activity_id} user={user_id}")
    activity = db.query(Activity).filter(Activity.id == activity_id, Activity.user_id == user_id).first()
    if not activity:
        raise HTTPException(status_code=404, detail="活动不存在")

    if body.start_time is not None:
        try:
            activity.start_time = parse_iso(body.start_time)
        except Exception:
            raise HTTPException(status_code=400, detail="start_time 格式错误")
    if body.end_time is not None:
        try:
            activity.end_time = parse_iso(body.end_time)
        except Exception:
            raise HTTPException(status_code=400, detail="end_time 格式错误")
    if body.description is not None:
        activity.description = body.description
    if body.tags is not None:
        activity.tags = body.tags

    db.commit()
    db.refresh(activity)
    logger.info(f"活动已更新 id={activity_id}")
    return activity_to_dict(activity)


@app.delete("/api/activities/{activity_id}")
def delete_activity(activity_id: int, db: Session = Depends(get_db), user_id: int = Depends(get_user_id)):
    logger.info(f"删除活动 id={activity_id} user={user_id}")
    activity = db.query(Activity).filter(Activity.id == activity_id, Activity.user_id == user_id).first()
    if not activity:
        raise HTTPException(status_code=404, detail="活动不存在")
    db.delete(activity)
    db.commit()
    logger.info(f"活动已删除 id={activity_id}")
    return {"ok": True}


# ── goal routes ───────────────────────────────────────────────


@app.get("/api/goals")
def list_goals(db: Session = Depends(get_db), user_id: int = Depends(get_user_id)):
    goals = db.query(Goal).filter(Goal.user_id == user_id).order_by(Goal.is_active.desc(), Goal.created_at.desc()).all()
    return [{"id": g.id, "content": g.content, "is_active": g.is_active, "created_at": g.created_at.isoformat() + "Z"} for g in goals]


@app.post("/api/goals")
def create_goal(body: GoalCreate, db: Session = Depends(get_db), user_id: int = Depends(get_user_id)):
    logger.info(f"创建目标: {body.content[:50]}")
    goal = Goal(user_id=user_id, content=body.content.strip())
    if not goal.content:
        raise HTTPException(status_code=400, detail="目标内容不能为空")
    db.add(goal)
    db.commit()
    db.refresh(goal)
    return {"id": goal.id, "content": goal.content, "is_active": goal.is_active}


@app.put("/api/goals/{goal_id}/deactivate")
def deactivate_goal(goal_id: int, db: Session = Depends(get_db), user_id: int = Depends(get_user_id)):
    goal = db.query(Goal).filter(Goal.id == goal_id, Goal.user_id == user_id).first()
    if not goal:
        raise HTTPException(status_code=404, detail="目标不存在")
    goal.is_active = False
    db.commit()
    return {"ok": True}


@app.put("/api/goals/{goal_id}/activate")
def activate_goal(goal_id: int, db: Session = Depends(get_db), user_id: int = Depends(get_user_id)):
    goal = db.query(Goal).filter(Goal.id == goal_id, Goal.user_id == user_id).first()
    if not goal:
        raise HTTPException(status_code=404, detail="目标不存在")
    goal.is_active = True
    db.commit()
    return {"ok": True}


# ── report helpers ────────────────────────────────────────────


def get_period_range(period: str) -> tuple[datetime, datetime]:
    now = datetime.now(CHINA_TZ)
    if period == "week":
        monday = now - timedelta(days=now.weekday())
        return datetime(monday.year, monday.month, monday.day, tzinfo=CHINA_TZ), now
    else:
        return datetime(now.year, now.month, now.day, tzinfo=CHINA_TZ), now


def build_prompt(activities: list, goals: list, memory: str, feedback: str, period: str) -> str:
    period_text = "今天" if period == "day" else "本周"

    lines = []
    for a in activities:
        start_local = a.start_time.replace(tzinfo=timezone.utc).astimezone(CHINA_TZ)
        start = start_local.strftime("%H:%M")
        if a.end_time:
            end_local = a.end_time.replace(tzinfo=timezone.utc).astimezone(CHINA_TZ)
            end = end_local.strftime("%H:%M")
        else:
            end = "进行中"
        duration = ""
        if a.end_time:
            mins = int((a.end_time - a.start_time).total_seconds() / 60)
            if mins >= 60:
                duration = f" ({mins // 60}小时{mins % 60}分钟)"
            else:
                duration = f" ({mins}分钟)"
        tag_str = f" [{a.tags}]" if a.tags else ""
        lines.append(f"- {start} → {end}{duration}: {a.description or '(无描述)'}{tag_str}")

    activity_text = "\n".join(lines) if lines else "（暂无活动记录）"

    goal_text = "\n".join(f"- {g.content}" for g in goals) if goals else "（未设定目标）"

    memory_section = ""
    if memory:
        memory_section = f"\n[记忆模块]\n上次建议摘要：{memory}\n我对上次建议的反馈：{feedback or '无'}\n"

    return f"""你是一位专业的时间管理与行为分析教练。以下是我在{period_text}的活动记录：
{activity_text}

我当前的目标是：
{goal_text}
{memory_section}
请基于以上信息，提供：

1. 行为模式总结（时间分配、习惯趋势）
2. 识别时间黑洞或低效模式
3. 结合历史记忆和反馈，给出2-3条具体的、可执行的迭代优化建议（建议应不同于以往，或说明延续性）
4. 一条给未来自己的简短记忆摘要（不超过300字，纯文本，用于下次分析上下文）

请用友好的口吻，严格使用以下 Markdown 结构输出（每个 # 标题独占一行，段落之间空一行）：

# 行为模式总结
（分析时间分配和习惯趋势，使用列表或段落呈现数据）

# 时间黑洞与低效模式
（识别问题，每个问题用一句话点出）

# 迭代优化建议
（2-3条具体的、可执行的建议，使用有序列表，每条建议说明为什么和改进方向）

# 未来记忆摘要
（给下次分析的简短提醒，1-2句话即可）

最后，将以上整个 Markdown 内容作为 summary_markdown 字段，将"未来记忆摘要"的内容复制为 memory_abstract 字段，严格按 JSON 输出：
{{"summary_markdown": "完整的分析报告 markdown", "memory_abstract": "纯文本摘要"}}"""


async def call_deepseek(prompt: str) -> dict:
    if not DEEPSEEK_API_KEY:
        raise HTTPException(status_code=500, detail="未配置 DEEPSEEK_API_KEY 环境变量")

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": "你是一个 JSON 输出机器人。你必须只输出合法的 JSON，不包含任何其他文字、解释或 markdown 标记。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.7,
        "max_tokens": 2048,
        "response_format": {"type": "json_object"},
    }

    async with httpx.AsyncClient(timeout=90.0) as client:
        logger.info(f"调用 DeepSeek API, prompt 长度={len(prompt)}")
        resp = await client.post(DEEPSEEK_URL, headers=headers, json=body)
        if resp.status_code != 200:
            logger.error(f"DeepSeek API 错误: {resp.status_code} {resp.text[:300]}")
            raise HTTPException(status_code=502, detail=f"AI 服务返回错误: {resp.status_code}")

        data = resp.json()
        content = data["choices"][0]["message"]["content"].strip()
        logger.info(f"DeepSeek 返回长度={len(content)}")

    # 尝试直接解析 JSON
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    # 尝试从 markdown 代码块中提取
    code_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', content)
    if code_match:
        try:
            return json.loads(code_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # 尝试从文本中提取第一个 JSON 对象
    json_match = re.search(r'\{[\s\S]*"summary_markdown"[\s\S]*"memory_abstract"[\s\S]*?\}', content)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass

    logger.error(f"无法解析 AI 返回: {content[:800]}")
    raise HTTPException(status_code=502, detail="AI 返回格式异常，请重试")


# ── report routes ─────────────────────────────────────────────


@app.post("/api/reports/generate")
async def generate_report(body: ReportGenerate, db: Session = Depends(get_db), user_id: int = Depends(get_user_id)):
    logger.info(f"生成报告 period={body.period} user={user_id}")
    period_start, period_end = get_period_range(body.period)

    activities = db.query(Activity).filter(
        Activity.user_id == user_id,
        Activity.start_time >= period_start,
        Activity.start_time <= period_end,
    ).order_by(Activity.start_time.asc()).all()

    active_goals = db.query(Goal).filter(Goal.user_id == user_id, Goal.is_active == True).all()

    last_report = db.query(Report).filter(Report.user_id == user_id).order_by(Report.created_at.desc()).first()
    memory = last_report.memory_abstract if last_report else ""
    feedback = last_report.user_feedback if last_report else ""

    prompt = build_prompt(activities, active_goals, memory, feedback, body.period)
    result = await call_deepseek(prompt)

    report = Report(
        user_id=user_id,
        period_start=period_start,
        period_end=period_end,
        summary_text=result["summary_markdown"],
        memory_abstract=result["memory_abstract"],
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    logger.info(f"报告已生成 id={report.id}")

    return {
        "id": report.id,
        "period_start": report.period_start.isoformat() + "Z",
        "period_end": report.period_end.isoformat() + "Z",
        "summary_text": report.summary_text,
        "memory_abstract": report.memory_abstract,
        "user_feedback": report.user_feedback,
        "created_at": report.created_at.isoformat() + "Z",
    }


@app.get("/api/reports/latest")
def get_latest_report(db: Session = Depends(get_db), user_id: int = Depends(get_user_id)):
    report = db.query(Report).filter(Report.user_id == user_id).order_by(Report.created_at.desc()).first()
    if not report:
        return {"report": None}
    return {
        "id": report.id,
        "period_start": report.period_start.isoformat() + "Z",
        "period_end": report.period_end.isoformat() + "Z",
        "summary_text": report.summary_text,
        "memory_abstract": report.memory_abstract,
        "user_feedback": report.user_feedback,
        "created_at": report.created_at.isoformat() + "Z",
    }


@app.get("/api/reports")
def list_reports(limit: int = Query(default=5, ge=1, le=50), db: Session = Depends(get_db), user_id: int = Depends(get_user_id)):
    reports = db.query(Report).filter(Report.user_id == user_id).order_by(Report.created_at.desc()).limit(limit).all()
    return [{
        "id": r.id,
        "period_start": r.period_start.isoformat() + "Z",
        "period_end": r.period_end.isoformat() + "Z",
        "summary_text": r.summary_text,
        "memory_abstract": r.memory_abstract,
        "user_feedback": r.user_feedback,
        "created_at": r.created_at.isoformat() + "Z",
    } for r in reports]


@app.put("/api/reports/{report_id}/feedback")
def update_report_feedback(report_id: int, body: ReportFeedback, db: Session = Depends(get_db), user_id: int = Depends(get_user_id)):
    logger.info(f"更新报告反馈 id={report_id} feedback={body.feedback}")
    report = db.query(Report).filter(Report.id == report_id, Report.user_id == user_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")
    if body.feedback not in ("helpful", "executed", "ignored"):
        raise HTTPException(status_code=400, detail="feedback 必须是 helpful/executed/ignored")
    report.user_feedback = body.feedback
    db.commit()
    return {"ok": True}


@app.delete("/api/reports/{report_id}")
def delete_report(report_id: int, db: Session = Depends(get_db), user_id: int = Depends(get_user_id)):
    report = db.query(Report).filter(Report.id == report_id, Report.user_id == user_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")
    db.delete(report)
    db.commit()
    logger.info(f"报告已删除 id={report_id}")
    return {"ok": True}


# ── static files (dev) ────────────────────────────────────────

if os.path.isdir("static"):
    app.mount("/", StaticFiles(directory="static", html=True), name="static")
