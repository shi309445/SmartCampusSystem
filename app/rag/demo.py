from app.rag.layer_3_storage import get_child_store
from app.rag.layer_4_retrieve import retrieval
from app.rag.reranker import get_reranker
from app.rag.docstore import MySqlDocStore
from app.database import SessionLocal
from app.config import settings
from langchain_huggingface import HuggingFaceEmbeddings

embeddings = HuggingFaceEmbeddings(
    model_name=str(settings.EMBEDDING_MODEL_PATH),
    model_kwargs={"device": "cpu"},          # 4GB 显存：embedding 留 CPU，reranker 上 GPU
    encode_kwargs={"normalize_embeddings": True},
)
vectorstore = get_child_store(embeddings)
docstore = MySqlDocStore(SessionLocal)
reranker = get_reranker()

results = retrieval("学生申诉的期限是多久",
                    vectorstore=vectorstore, docstore=docstore, reranker=reranker)

for r in results:
    print(r.score, r.citation)          # 0.9746 《普通高等学校学生管理规定》第六章  学生申诉 · 直接相关：第六十四条
    print(r.content[:60], "...")