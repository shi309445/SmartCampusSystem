# get_data/ —— 爬虫原始输出（**不是**知识库语料）

这里放的是 `getting_data.py` 从学校「规章制度」栏目抓下来的原始 Markdown，
共 18 篇，用于留档 / 对比，**运行时代码不读这个目录**。

## 真正的语料是 `docs/management_rules/`

- `app/rag/layer_1_data.py` 的 `DOCS_DIR = <repo>/docs`，只扫 `docs/` 下的文件；
- 检索时还按 `kb == "management_rules"` 过滤，对应 `docs/management_rules/` 这 15 篇；
- 所以**改知识库要改 `docs/management_rules/`**，改这里不会生效。

## 两者关系

| 目录 | 篇数 | 谁在用 | 说明 |
|---|---|---|---|
| `docs/management_rules/` | 15 | RAG 全流程 | 人工整理过的语料（统一命名、去掉抓取残留），**唯一来源** |
| `get_data/` | 18 | 没人（历史留档） | `python getting_data.py` 的原始产物，含 3 篇未入选的文件 |

## 想重新抓一遍

```powershell
.\.venv\Scripts\python.exe getting_data.py --limit 3     # 先验证抓取
.\.venv\Scripts\python.exe getting_data.py               # 全量抓到 get_data/
# 然后手工把要用的文件整理进 docs/management_rules/，并重建索引（见 README）
```

> 如果确认这 18 篇原始文件已经没用了，可以直接删掉整个目录 —— 但删之前先确认
> `docs/management_rules/` 里的 15 篇是完整的（它们是唯一被检索的语料）。
