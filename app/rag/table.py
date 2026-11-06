from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Text

from app.database import Base


class RagDocStore(Base):
    """父块存储表

    doc_key 必须是**唯一主键**：父块按 doc_id 一对一取回，如果它和自增 id 组成
    复合主键，同一个 doc_id 就能存进两行，而 mget 按 key 建字典只保留最后一行，
    多出来的那份永远读不到、且不报错。
    """

    __tablename__ = "rag_doc_store"
    __table_args__ = {"comment": "切分父块存储表"}

    doc_key: Mapped[str] = mapped_column(String(255), primary_key=True, comment="父块 doc_id")
    content: Mapped[str] = mapped_column(Text, comment="父块内容")
    doc_metadata: Mapped[str] = mapped_column(Text, comment="父块元数据")
