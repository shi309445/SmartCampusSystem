"""RAG 建库 —— 第 1 层：数据层

对齐 data/RAG 项目六层总图.md 的第 1 层：
    1.1 来源识别 / 1.2 接入 / 1.3 采集 / 1.4 登记 / 1.5 治理

约定（第 3 层是「契约面」，离线在线两侧必须一致）：
    - 语料根目录：项目根/docs，目录下**只允许 md**，其余格式在外部先转成 md
    - docs/ 的**一级目录 = 知识库标识（kb）**，不是存储分区，也不做检索过滤
    - 本层只产出**文件级身份元数据**：source / kb / file_name
    - 块级元数据（doc_id / parent_id / chunk_level / chunk_index / start_index）
      由第 2 层 data_treating() 追加

为什么不用 langchain-community 的 DirectoryLoader：
    - 它已进入 sunset，导入即告警「no longer actively maintained」
    - 它的默认 loader_cls 是 UnstructuredFileLoader，读个 md 要拖进 unstructured
      及其系统依赖（poppler / tesseract），不划算
    这里直接用 langchain_core 的 Document，读文件、编码回落、元数据都自己控制。
"""

from langchain_core.documents import Document
from app.rag.layer_1_data import get_data, scan_docs
from app.rag.layer_2_treated import data_treating
from app.rag.layer_3_storage import rebuild_all
from app.database import SessionLocal
from app.config import settings

from langchain_huggingface import HuggingFaceEmbeddings



model_path = str(settings.EMBEDDING_MODEL_PATH)
embeddings = HuggingFaceEmbeddings(
    model_name=model_path,
    model_kwargs={"device": "cpu"},       # 有 GPU 就 "cuda"
    encode_kwargs={"normalize_embeddings": True},  # bge 系列建议归一化
)


rebuild_all(embeddings=embeddings, session_factory=SessionLocal)
