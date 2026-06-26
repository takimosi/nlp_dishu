import json
import hashlib
import math
import os
import re
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent
HTML_FILE = ROOT / "dishu_diary_apartment.html"
SYMBOL_LIBRARY = ROOT / "地书日记生成器" / "data" / "symbol_library.json"
ASSET_DIR = ROOT / "地书标注系统_V1.0 (1)" / "地书标注系统 V1.0" / "images"
LOCAL_ASSET_DIR = ROOT / "assets"
HOST = "127.0.0.1"
PORT = int(os.environ.get("DISHU_APARTMENT_PORT", "8000"))

# 运行期缓存：避免每次请求都重复读取符号库、构建索引或加载模型。
_COMPACT_LIBRARY = None
_VECTOR_INDEX = None
_EMBEDDING_MODEL = None
_EMBEDDING_INDEX = None
_EMBEDDING_ERROR = None
_FAISS_MODULE = None
_FAISS_ERROR = None

# 本地语义模型路径与 FAISS 磁盘缓存路径。
# 模型负责把文本转成 384 维向量；FAISS 文件保存地书符号向量索引。
EMBEDDING_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
EMBEDDING_MODEL_PATH = ROOT.parent / "game" / "models" / EMBEDDING_MODEL_NAME
EMBEDDING_CACHE_DIR = ROOT / ".cache" / "embedding_index"
FAISS_INDEX_FILE = EMBEDDING_CACHE_DIR / "symbol_embeddings.faiss"
FAISS_META_FILE = EMBEDDING_CACHE_DIR / "symbol_embedding_meta.json"

MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".svg": "image/svg+xml",
}


def json_response(handler, payload, status=200):
    """统一返回 JSON，保证中文字段不被 ASCII 转义。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def compact_symbol_library():
    """读取原始地书符号库，过滤成检索需要的轻量结构。

    这里只保留有图片路径、有文本语义描述的符号；后续 TF-IDF 和 embedding
    都基于这个 compact 版本构建。
    """
    global _COMPACT_LIBRARY
    if _COMPACT_LIBRARY is not None:
        return _COMPACT_LIBRARY

    raw = json.loads(SYMBOL_LIBRARY.read_text(encoding="utf-8"))
    compact = []
    for symbol in raw:
        image_paths = symbol.get("image_paths") or []
        text_fields = [
            symbol.get("free_translation"),
            symbol.get("literal_gloss"),
            symbol.get("pragmatic_meaning"),
            " ".join(symbol.get("semantic_primitives") or []),
            symbol.get("search_text"),
        ]
        search_text = " ".join(str(item) for item in text_fields if item).strip()
        if not image_paths or not search_text or search_text.lower() in {"无明显关系 / none", "none"}:
            continue
        compact.append({
            "group_id": symbol.get("group_id"),
            "image_paths": image_paths[:10],
            "page": symbol.get("page"),
            "leaf_start": symbol.get("leaf_start"),
            "free_translation": symbol.get("free_translation"),
            "literal_gloss": symbol.get("literal_gloss"),
            "pragmatic_meaning": symbol.get("pragmatic_meaning"),
            "pos_like_category": symbol.get("pos_like_category") or [],
            "semantic_primitives": symbol.get("semantic_primitives") or [],
            "search_text": search_text,
        })
    _COMPACT_LIBRARY = compact
    return _COMPACT_LIBRARY


def tokenize_text(text):
    """为 TF-IDF 建索引做简单分词。

    中文长词会额外切成 2-gram/3-gram，提升短语和局部词的召回能力。
    """
    text = str(text or "").lower()
    matches = re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z0-9_]+", text)
    tokens = []
    for chunk in matches:
        if re.fullmatch(r"[\u4e00-\u9fff]+", chunk):
            if len(chunk) > 3:
                for index in range(len(chunk) - 1):
                    tokens.append(chunk[index:index + 2])
                for index in range(len(chunk) - 2):
                    tokens.append(chunk[index:index + 3])
            tokens.append(chunk)
        else:
            tokens.append(chunk)
    return tokens


def symbol_text(symbol):
    """把一个地书符号的多种标注字段融合成一段检索文本。"""
    return " ".join(str(item) for item in [
        symbol.get("search_text"),
        symbol.get("free_translation"),
        symbol.get("literal_gloss"),
        symbol.get("pragmatic_meaning"),
        " ".join(symbol.get("semantic_primitives") or []),
        " ".join(symbol.get("pos_like_category") or []),
    ] if item)


def event_query_text(event):
    """把结构化事件 JSON 拼成查询文本，并补充少量人工扩展词。"""
    keywords = event.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [keywords]
    base = " ".join(str(item) for item in [
        event.get("time"),
        event.get("action"),
        event.get("object") or event.get("subject"),
        event.get("location"),
        event.get("emotion"),
        event.get("modifier"),
        event.get("raw_text"),
        " ".join(str(word) for word in keywords),
    ] if item)
    expansions = []
    if re.search(r"去|到|回|走|骑|坐|地铁|公交|打车|通勤|路上", base):
        expansions.extend(["移动", "交通", "路径", "城市", "空间"])
    if re.search(r"吃|喝|饭|面|外卖|咖啡|奶茶|餐", base):
        expansions.extend(["食物", "生理", "消费", "餐饮"])
    if re.search(r"会|汇报|聊天|电话|消息|说|问|组会", base):
        expansions.extend(["交流", "交际", "对话", "多人", "屏幕"])
    if re.search(r"手机|电脑|代码|视频|屏幕|微信", base):
        expansions.extend(["技术", "电子", "媒介", "屏幕"])
    if re.search(r"紧张|焦虑|疲惫|开心|高兴|难过|迷茫|无聊|思考", base):
        expansions.extend(["情绪", "心理", "感受", "人物"])
    return " ".join([base, *expansions])


def build_vector_index():
    """构建传统 TF-IDF 稀疏向量索引，作为关键词匹配和兜底检索。"""
    global _VECTOR_INDEX
    if _VECTOR_INDEX is not None:
        return _VECTOR_INDEX

    docs = compact_symbol_library()
    doc_term_counts = []
    doc_freq = {}

    for symbol in docs:
        counts = {}
        for token in tokenize_text(symbol_text(symbol)):
            counts[token] = counts.get(token, 0) + 1
        doc_term_counts.append(counts)
        for token in counts:
            doc_freq[token] = doc_freq.get(token, 0) + 1

    doc_count = max(len(docs), 1)
    idf = {
        token: math.log((1 + doc_count) / (1 + freq)) + 1
        for token, freq in doc_freq.items()
    }
    postings = {}

    for doc_index, counts in enumerate(doc_term_counts):
        weighted = {}
        for token, count in counts.items():
            if token not in idf:
                continue
            weighted[token] = (1 + math.log(count)) * idf[token]
        norm = math.sqrt(sum(weight * weight for weight in weighted.values())) or 1.0
        for token, weight in weighted.items():
            postings.setdefault(token, []).append((doc_index, weight / norm))

    _VECTOR_INDEX = {
        "docs": docs,
        "idf": idf,
        "postings": postings,
        "doc_count": doc_count,
    }
    return _VECTOR_INDEX


def load_embedding_model():
    """懒加载本地 SentenceTransformer 模型；失败时回退到 TF-IDF。"""
    global _EMBEDDING_MODEL, _EMBEDDING_ERROR
    if _EMBEDDING_MODEL is not None:
        return _EMBEDDING_MODEL
    if _EMBEDDING_ERROR is not None:
        return None

    try:
        if not EMBEDDING_MODEL_PATH.exists():
            raise FileNotFoundError(f"embedding model not found: {EMBEDDING_MODEL_PATH}")

        from sentence_transformers import SentenceTransformer

        _EMBEDDING_MODEL = SentenceTransformer(str(EMBEDDING_MODEL_PATH))
        return _EMBEDDING_MODEL
    except Exception as err:
        _EMBEDDING_ERROR = str(err)
        print(f"Embedding model unavailable, fallback to TF-IDF: {_EMBEDDING_ERROR}")
        return None


def load_faiss():
    """懒加载 FAISS；如果 faiss-cpu 不可用，则使用 NumPy 内存检索兜底。"""
    global _FAISS_MODULE, _FAISS_ERROR
    if _FAISS_MODULE is not None:
        return _FAISS_MODULE
    if _FAISS_ERROR is not None:
        return None

    try:
        import faiss

        _FAISS_MODULE = faiss
        return _FAISS_MODULE
    except Exception as err:
        _FAISS_ERROR = str(err)
        print(f"FAISS unavailable, fallback to in-memory embedding search: {_FAISS_ERROR}")
        return None


def symbol_text_hash(docs):
    """计算符号文本指纹，用于判断磁盘 FAISS 缓存是否仍然有效。"""
    digest = hashlib.sha256()
    for symbol in docs:
        digest.update(str(symbol.get("group_id") or "").encode("utf-8"))
        digest.update(b"\0")
        digest.update(symbol_text(symbol).encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def embedding_cache_meta(docs):
    """生成 embedding 索引缓存元数据。"""
    stat = SYMBOL_LIBRARY.stat()
    return {
        "model": EMBEDDING_MODEL_NAME,
        "symbol_library_size": stat.st_size,
        "symbol_library_mtime_ns": stat.st_mtime_ns,
        "count": len(docs),
        "text_hash": symbol_text_hash(docs),
    }


def read_embedding_cache_meta():
    """读取 FAISS 缓存元数据；读取失败时视为无可用缓存。"""
    if not FAISS_META_FILE.exists():
        return None
    try:
        return json.loads(FAISS_META_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def cache_matches(expected, cached):
    """比较当前符号库状态与缓存元数据是否一致。"""
    if not cached:
        return False
    keys = ["model", "symbol_library_size", "symbol_library_mtime_ns", "count", "text_hash"]
    return all(cached.get(key) == expected.get(key) for key in keys)


def build_embedding_index():
    """构建或读取地书符号 embedding 索引。

    优先读取磁盘中的 .faiss + meta 缓存；缓存不存在或失效时，才重新对
    符号文本批量向量化。向量已 L2 归一化，因此内积等价于余弦相似度。
    """
    global _EMBEDDING_INDEX, _EMBEDDING_ERROR
    if _EMBEDDING_INDEX is not None:
        return _EMBEDDING_INDEX

    model = load_embedding_model()
    if model is None:
        return None

    docs = compact_symbol_library()
    expected_meta = embedding_cache_meta(docs)
    faiss = load_faiss()

    if faiss is not None and FAISS_INDEX_FILE.exists() and cache_matches(expected_meta, read_embedding_cache_meta()):
        try:
            # 缓存命中：直接读取本地 FAISS 索引，不重复编码全部符号。
            index = faiss.read_index(str(FAISS_INDEX_FILE))
            _EMBEDDING_INDEX = {
                "docs": docs,
                "faiss_index": index,
                "backend": "faiss",
                "cache": "disk",
                "dimension": index.d,
                "model": EMBEDDING_MODEL_NAME,
            }
            return _EMBEDDING_INDEX
        except Exception as err:
            print(f"FAISS cache read failed, rebuilding index: {err}")

    try:
        texts = [symbol_text(symbol) for symbol in docs]
        import numpy as np

        # 首次建库或缓存失效时，对所有地书符号文本批量生成 384 维语义向量。
        embeddings = model.encode(
            texts,
            batch_size=64,
            show_progress_bar=False,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        embeddings = np.ascontiguousarray(embeddings.astype("float32"))

        if faiss is not None:
            # IndexFlatIP 是精确内积索引；向量已归一化，所以内积就是 cosine。
            index = faiss.IndexFlatIP(embeddings.shape[1])
            index.add(embeddings)
            EMBEDDING_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            faiss.write_index(index, str(FAISS_INDEX_FILE))
            FAISS_META_FILE.write_text(
                json.dumps({
                    **expected_meta,
                    "backend": "faiss",
                    "dimension": int(embeddings.shape[1]),
                }, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            _EMBEDDING_INDEX = {
                "docs": docs,
                "faiss_index": index,
                "backend": "faiss",
                "cache": "rebuilt",
                "dimension": int(embeddings.shape[1]),
                "model": EMBEDDING_MODEL_NAME,
            }
            return _EMBEDDING_INDEX

        _EMBEDDING_INDEX = {
            "docs": docs,
            "embeddings": embeddings,
            "backend": "numpy",
            "cache": "memory",
            "dimension": int(embeddings.shape[1]),
            "model": EMBEDDING_MODEL_NAME,
        }
        return _EMBEDDING_INDEX
    except Exception as err:
        _EMBEDDING_ERROR = str(err)
        print(f"Embedding index unavailable, fallback to TF-IDF: {_EMBEDDING_ERROR}")
        return None


def embedding_status():
    """返回 embedding/FAISS 状态，供 /api/vector-stats 调试展示。"""
    model = load_embedding_model()
    faiss = load_faiss()
    cached_meta = read_embedding_cache_meta()
    status = {
        "embedding": model is not None,
        "embedding_model": EMBEDDING_MODEL_NAME,
        "faiss": faiss is not None,
        "vector_store": _EMBEDDING_INDEX.get("backend") if _EMBEDDING_INDEX else ("faiss_pending" if faiss is not None else "embedding_memory_fallback"),
        "cache_ready": FAISS_INDEX_FILE.exists() and cache_matches(embedding_cache_meta(compact_symbol_library()), cached_meta),
        "cache_path": str(EMBEDDING_CACHE_DIR),
        "fallback": "tfidf",
    }
    if _EMBEDDING_ERROR:
        status["embedding_error"] = _EMBEDDING_ERROR
    if _FAISS_ERROR:
        status["faiss_error"] = _FAISS_ERROR
    return status


def embedding_search_scores(query_text, top_k):
    """搜索与用户事件查询文本最相似的符号向量。"""
    global _EMBEDDING_ERROR
    index = build_embedding_index()
    if index is None:
        return {}

    try:
        import numpy as np

        # 用户事件实时编码为查询向量；符号向量来自内存索引或磁盘 FAISS 缓存。
        query_embedding = _EMBEDDING_MODEL.encode(
            query_text,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        query_embedding = np.ascontiguousarray(query_embedding.astype("float32"))
        top_k = max(1, min(int(top_k), len(index["docs"])))

        if index.get("backend") == "faiss":
            scores, doc_ids = index["faiss_index"].search(query_embedding.reshape(1, -1), top_k)
            return {
                int(doc_index): max(float(score), 0.0)
                for doc_index, score in zip(doc_ids[0], scores[0])
                if int(doc_index) >= 0
            }

        scores = index["embeddings"].dot(query_embedding)
        ranked_ids = np.argsort(scores)[::-1][:top_k]
        return {
            int(doc_index): max(float(scores[doc_index]), 0.0)
            for doc_index in ranked_ids
        }
    except Exception as err:
        _EMBEDDING_ERROR = str(err)
        print(f"Embedding search unavailable, fallback to TF-IDF: {_EMBEDDING_ERROR}")
        return {}


def tfidf_search_data(query_text):
    """计算查询文本与符号文本的 TF-IDF 相似度，并记录命中的 token。"""
    index = build_vector_index()
    idf = index["idf"]
    postings = index["postings"]
    query_counts = {}
    for token in tokenize_text(query_text):
        if token in idf:
            query_counts[token] = query_counts.get(token, 0) + 1

    query_vector = {
        token: (1 + math.log(count)) * idf[token]
        for token, count in query_counts.items()
    }
    query_norm = math.sqrt(sum(weight * weight for weight in query_vector.values())) or 1.0
    scores = {}
    token_hits = {}

    for token, weight in query_vector.items():
        query_weight = weight / query_norm
        for doc_index, doc_weight in postings.get(token, []):
            scores[doc_index] = scores.get(doc_index, 0.0) + query_weight * doc_weight
            token_hits.setdefault(doc_index, []).append(token)

    return scores, token_hits


def build_candidate(symbol, query_text, token_hits, tfidf_score, embedding_score=None):
    """组装单个候选符号，并计算最终混合重排分。"""
    doc_text = symbol_text(symbol)
    exact_hits = [
        token
        for token in token_hits
        if len(token) >= 2 and token in doc_text.lower()
    ]
    lexical_score = min(len(set(exact_hits)) / 10.0, 0.32)
    rule_score = heuristic_score(query_text, doc_text)

    if embedding_score is None:
        total = tfidf_score * 0.72 + lexical_score * 0.18 + rule_score * 0.10
        reasons = [f"向量相似 {tfidf_score:.3f}"]
    else:
        total = (
            embedding_score * 0.58
            + tfidf_score * 0.24
            + lexical_score * 0.10
            + rule_score * 0.08
        )
        reasons = [
            f"语义相似 {embedding_score:.3f}",
            f"关键词相似 {tfidf_score:.3f}",
        ]

    reasons.extend(
        f"命中“{token}”"
        for token in sorted(set(exact_hits), key=len, reverse=True)[:3]
    )

    return {
        **symbol,
        "score": round(total, 4),
        "vector_score": round(tfidf_score, 4),
        "tfidf_score": round(tfidf_score, 4),
        "embedding_score": round(embedding_score or 0.0, 4),
        "lexical_score": round(lexical_score, 4),
        "reasons": reasons,
    }


def heuristic_score(query_text, doc_text):
    """人工规则分：交通、饮食、交流、技术、情绪等类别的轻量加权。"""
    score = 0.0
    if re.search(r"去|到|回|走|骑|坐|地铁|交通|通勤", query_text) and re.search(r"motion|运动|空间|路径|交通|车|飞机|地铁", doc_text, re.I):
        score += 0.16
    if re.search(r"吃|喝|饭|面|外卖|咖啡", query_text) and re.search(r"bodily|生理|食|喝|餐|咖啡|酒", doc_text, re.I):
        score += 0.16
    if re.search(r"会|汇报|聊天|电话|消息|说|问|组会", query_text) and re.search(r"communication|交际|交流|对话|电话|说|问", doc_text, re.I):
        score += 0.16
    if re.search(r"手机|电脑|代码|屏幕|视频", query_text) and re.search(r"technology|技术|电脑|屏幕|手机|电子", doc_text, re.I):
        score += 0.12
    if re.search(r"紧张|焦虑|疲惫|开心|高兴|难过|迷茫|无聊|思考", query_text) and re.search(r"emotion|情感|心理|感觉|人物", doc_text, re.I):
        score += 0.12
    return score


def search_symbols_vector(event, limit=5):
    """地书符号检索主入口：事件 JSON -> 候选符号 Top-N。

    内部会同时取 embedding Top-K 和 TF-IDF Top-K，合并去重后再混合重排。
    """
    index = build_vector_index()
    docs = index["docs"]
    query_text = event_query_text(event)
    candidate_pool_size = max(limit * 40, 80)
    tfidf_scores, token_hits = tfidf_search_data(query_text)
    embedding_scores = embedding_search_scores(query_text, candidate_pool_size)

    if not tfidf_scores and not embedding_scores:
        return fallback_vector_candidates(query_text, limit)

    candidates = []
    if embedding_scores:
        tfidf_top = [
            doc_index
            for doc_index, _ in sorted(tfidf_scores.items(), key=lambda item: item[1], reverse=True)[:candidate_pool_size]
        ]
        embedding_top = [
            doc_index
            for doc_index, _ in sorted(embedding_scores.items(), key=lambda item: item[1], reverse=True)[:candidate_pool_size]
        ]
        candidate_ids = list(dict.fromkeys(embedding_top + tfidf_top))
        for doc_index in candidate_ids:
            symbol = docs[doc_index]
            candidates.append(build_candidate(
                symbol,
                query_text,
                token_hits.get(doc_index, []),
                tfidf_scores.get(doc_index, 0.0),
                embedding_scores.get(doc_index, 0.0),
            ))
    else:
        tfidf_top = sorted(tfidf_scores.items(), key=lambda item: item[1], reverse=True)[:candidate_pool_size]
        for doc_index, vector_score in tfidf_top:
            symbol = docs[doc_index]
            candidates.append(build_candidate(
                symbol,
                query_text,
                token_hits.get(doc_index, []),
                vector_score,
            ))

    candidates.sort(key=lambda item: (-item["score"], int(item.get("leaf_start") or 0)))
    return candidates[:limit]


def fallback_vector_candidates(query_text, limit):
    """极端兜底：当所有检索都失败时，返回稳定的伪随机候选。"""
    docs = compact_symbol_library()
    rich = [symbol for symbol in docs if symbol.get("image_paths")]
    if not rich:
        return []
    seed = sum(ord(char) for char in query_text) % len(rich)
    selected = (rich[seed:] + rich[:seed])[:limit]
    return [{
        **symbol,
        "score": round(0.12 - index * 0.01, 4),
        "vector_score": 0,
        "tfidf_score": 0,
        "embedding_score": 0,
        "lexical_score": 0,
        "reasons": ["向量召回为空，低置信度补全"],
    } for index, symbol in enumerate(selected)]


def read_json(handler):
    """读取 POST 请求体中的 JSON 数据。"""
    size = int(handler.headers.get("Content-Length", "0"))
    raw = handler.rfile.read(size).decode("utf-8")
    return json.loads(raw or "{}")


def extract_json_array(text):
    """从 LLM 回复中提取 JSON 数组，兼容回复前后夹杂说明文字的情况。"""
    text = text.strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            return parsed.get("events", [])
    except Exception:
        pass

    match = re.search(r"\[[\s\S]*\]", text)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            return []
    return []


def normalize_event(event, raw_text=""):
    """统一事件字段结构，保证前后端都能使用固定字段。"""
    keywords = event.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [item.strip() for item in re.split(r"[,，、\s]+", keywords) if item.strip()]
    return {
        "time": str(event.get("time") or "未知"),
        "action": str(event.get("action") or "事件"),
        "object": str(event.get("object") or event.get("subject") or ""),
        "location": str(event.get("location") or ""),
        "emotion": str(event.get("emotion") or "平静"),
        "modifier": str(event.get("modifier") or event.get("duration") or ""),
        "raw_text": str(event.get("raw_text") or raw_text or ""),
        "keywords": [str(item) for item in keywords if str(item).strip()],
    }


def fallback_structure(text):
    """LLM 不可用时的规则拆分：按标点、时间词、动作词粗略生成事件。"""
    pieces = [
        p.strip(" ，。！？；;,.!?\n\t")
        for p in re.split(r"[。！？；;\n]+", text)
        if p.strip(" ，。！？；;,.!?\n\t")
    ]
    if not pieces and text.strip():
        pieces = [text.strip()]

    time_words = ["凌晨", "早上", "上午", "中午", "下午", "傍晚", "晚上", "夜里", "今天", "昨天"]
    action_words = [
        "起床", "坐地铁", "打车", "骑车", "走路", "上班", "下班", "开会", "汇报", "吃饭",
        "喝咖啡", "学习", "写代码", "购物", "等待", "回家", "睡觉", "刷手机", "点外卖", "跑步"
    ]
    emotion_words = ["开心", "高兴", "难过", "紧张", "焦虑", "无奈", "疲惫", "生气", "惊讶", "迷茫", "平静"]
    location_words = ["公司", "家", "宿舍", "公寓", "医院", "机场", "地铁", "咖啡馆", "学校", "商场", "楼下"]

    events = []
    for piece in pieces[:12]:
        time = next((word for word in time_words if word in piece), "未知")
        explicit_time = re.search(r"(\d{1,2}[:：]\d{2}|\d{1,2}点)", piece)
        if explicit_time:
            time = explicit_time.group(1).replace("：", ":")
        action = next((word for word in action_words if word in piece), "事件")
        emotion = next((word for word in emotion_words if word in piece), "平静")
        location = next((word for word in location_words if word in piece), "")
        keywords = [item for item in [time, action, emotion, location] if item and item != "未知"]
        events.append(normalize_event({
            "time": time,
            "action": action,
            "location": location,
            "emotion": emotion,
            "raw_text": piece,
            "keywords": keywords,
        }, piece))
    return events


def call_deepseek(messages, temperature=0.25):
    """调用 DeepSeek 兼容 Chat Completions 的接口。"""
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not set")

    base = os.environ.get("DEEPSEEK_API_BASE", "https://api.deepseek.com")
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
    url = urllib.parse.urljoin(base.rstrip("/") + "/", "chat/completions")
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 1200,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=35) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def structure_with_llm(text):
    """用 LLM 将自然语言日记拆成适合图形叙事的结构化事件。"""
    system_prompt = (
        "你是《地书日记》作品中的事件结构化模块。请把用户日记拆成3到8个适合图形叙事的事件，"
        "只返回JSON数组，不要Markdown。每个事件固定包含time, action, object, location, emotion, "
        "modifier, raw_text, keywords。keywords给出3到6个用于检索《地书》图形素材的中文关键词。"
        "字段缺失时写空字符串；time未知写“未知”；emotion优先使用平静、紧张、疲惫、开心、难过、思考、无聊。"
    )
    content = call_deepseek([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": text},
    ])
    events = extract_json_array(content)
    return [normalize_event(item) for item in events if isinstance(item, dict)] or fallback_structure(text)


def explain_with_llm(event, symbol):
    """用 LLM 为事件和候选符号生成简短解释。"""
    system_prompt = (
        "你是《地书日记》作品的策展说明模块。请用两句简短中文解释："
        "为什么这个《地书》图形片段适合表达该用户事件，以及这种映射保留了什么日常经验。"
    )
    content = call_deepseek([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps({"event": event, "symbol": symbol}, ensure_ascii=False)},
    ], temperature=0.45)
    return content.strip()


def fallback_explain(event, symbol):
    """LLM 解释失败时的本地说明模板。"""
    label = symbol.get("free_translation") or symbol.get("pragmatic_meaning") or symbol.get("literal_gloss") or "相近图形片段"
    return (
        f"系统把“{event.get('action') or '事件'}”映射到“{label}”，依据是标注文本、语义基元和事件关键词的重合。"
        "这是一种从个人日记到通用图形语言的近似翻译。"
    )


class Handler(BaseHTTPRequestHandler):
    """轻量 HTTP 服务：提供页面资源和地书日记相关 API。"""
    def do_GET(self):
        """处理页面、静态资源、符号库和向量状态查询。"""
        path = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
        if path in ("/", "/dishu_diary_apartment.html"):
            return self.serve_file(HTML_FILE)
        if path == "/api/vector-stats":
            return json_response(self, {
                "count": len(compact_symbol_library()),
                "index": "local_faiss_embedding_tfidf_hybrid",
                "hybrid": True,
                **embedding_status(),
            })
        if path == "/api/symbol-library":
            return json_response(self, compact_symbol_library())
        if path == "/data/symbol_library.json":
            return self.serve_file(SYMBOL_LIBRARY)
        if path.startswith("/assets/"):
            return self.serve_file(LOCAL_ASSET_DIR / path.removeprefix("/assets/"))
        if path.startswith("/dishu-assets/"):
            return self.serve_file(ASSET_DIR / path.removeprefix("/dishu-assets/"))
        self.send_error(404)

    def do_POST(self):
        """处理结构化、解释、符号检索三个核心 POST 接口。"""
        try:
            data = read_json(self)
            if self.path == "/api/structure":
                # 自然语言日记 -> 事件 JSON。
                text = str(data.get("text") or "")
                try:
                    events = structure_with_llm(text)
                    return json_response(self, {"events": events, "source": "deepseek"})
                except Exception as err:
                    return json_response(self, {
                        "events": fallback_structure(text),
                        "source": "fallback",
                        "error": str(err),
                    })

            if self.path == "/api/explain":
                # 事件 + 符号 -> 简短解释文本。
                event = data.get("event") or {}
                symbol = data.get("symbol") or {}
                try:
                    explanation = explain_with_llm(event, symbol)
                    return json_response(self, {"explanation": explanation, "source": "deepseek"})
                except Exception as err:
                    return json_response(self, {
                        "explanation": fallback_explain(event, symbol),
                        "source": "fallback",
                        "error": str(err),
                    })

            if self.path == "/api/search-symbols":
                # 单个结构化事件 -> 地书符号候选列表。
                event = data.get("event") or {}
                limit = int(data.get("limit") or 5)
                limit = max(1, min(limit, 12))
                candidates = search_symbols_vector(event, limit)
                status = embedding_status()
                return json_response(self, {
                    "candidates": candidates,
                    "source": (
                        "local_faiss_embedding_tfidf_hybrid"
                        if status["embedding"] and status["faiss"]
                        else "local_embedding_tfidf_hybrid"
                        if status["embedding"]
                        else "local_tfidf_vector_db"
                    ),
                    "count": len(candidates),
                })

            return json_response(self, {"error": "Not found"}, 404)
        except Exception as err:
            return json_response(self, {"error": str(err)}, 500)

    def serve_file(self, path):
        resolved = path.resolve()
        allowed_roots = [ROOT.resolve(), ASSET_DIR.resolve(), LOCAL_ASSET_DIR.resolve()]
        if not any(str(resolved).startswith(str(root)) for root in allowed_roots):
            self.send_error(403)
            return
        if not resolved.is_file():
            self.send_error(404)
            return
        body = resolved.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", MIME_TYPES.get(resolved.suffix.lower(), "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}")


if __name__ == "__main__":
    print(f"《地书日记 · 公寓》running at http://{HOST}:{PORT}")
    print("DeepSeek: set DEEPSEEK_API_KEY; optional DEEPSEEK_MODEL defaults to deepseek-v4-flash.")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
