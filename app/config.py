from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    # 数据库连接串
    DATABASE_URL: str
    DB_ECHO: bool = False

    # JWT 鉴权配置（登录令牌的签名密钥 / 算法 / 有效期）
    JWT_SECRET_KEY: str = Field(..., min_length=32, description="令牌签名密钥，至少 32 位")
    JWT_ALGORITHM: Literal["HS256", "HS384", "HS512"] = "HS256"
    JWT_EXPIRE_HOURS: int = Field(24, gt=0, le=720, description="令牌有效期(小时)")

    # 嵌入模型路径
    EMBEDDING_MODEL_PATH: Path = BASE_DIR / "models/BAAI/bge-m3"
    RARANKER_MODEL_PATH: Path = BASE_DIR / "models/BAAI/bge-reranker-v2-m3"

    # ---------------------------------------------------------------- 接口层
    # 允许跨域的前端源（开发默认：vite 的 5173）。生产务必改成真实域名，不要用 ["*"]；
    # .env 里要写成 JSON 数组：CORS_ORIGINS=["http://localhost:5173"]
    CORS_ORIGINS: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
    # 每个学生每分钟最多发多少条聊天消息（内存滑动窗口，单机够用；0 = 不限流）
    CHAT_RATE_LIMIT_PER_MINUTE: int = Field(20, ge=0)

    # 首次部署可用它初始化一个管理员账号（都不填就跳过，代码里不留默认口令）
    BOOTSTRAP_ADMIN_USERNAME: str | None = None
    BOOTSTRAP_ADMIN_PASSWORD: str | None = None
    BOOTSTRAP_ADMIN_NAME: str | None = None

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env", env_file_encoding="utf-8",
        extra="ignore"  # ← 忽略 .env 里未定义的字段
    )

    @field_validator("DATABASE_URL")
    @classmethod
    def check_database_url(cls, v: str) -> str:
        """启动时就校验 DSN，避免拼错到第一次查询才暴露"""
        if not v.startswith("mysql+"):
            raise ValueError("DATABASE_URL 必须是 mysql+pymysql:// 开头的连接串")
        if "charset=" not in v:
            v += ("&" if "?" in v else "?") + "charset=utf8mb4"
        return v


settings = Settings()
