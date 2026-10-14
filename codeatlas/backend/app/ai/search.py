"""코드 검색 색인 (BM25).

함수 이름(snake_case·camelCase 분리), docstring, AI 요약, 호출하는 이름들을 문서로 삼는다.
한국어는 형태소 분석기 없이 '글자 2개씩(bigram)'으로 쪼개서 "로그인은"과 "로그인을"이 서로 맞도록 한다.
"""
from __future__ import annotations

import math
import re
from collections import Counter

from ..analyzer.graph import CodeGraph

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[가-힣]+|\d+")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
_HANGUL = re.compile(r"[가-힣]+")

EN_STOP = frozenset("""
the a an of to in and or is are be for on with by as at it this that from into self cls none true false
def return py init get set new value values data args kwargs obj
""".split())
KO_STOP = frozenset("""
어디 어디서 어디에 어떻게 어떤 무엇 뭐야 뭐 왜 해줘 알려줘 설명 설명해줘 코드 부분 하는 되는 있는 있어 이 그 저 것 수 등
돼 되나 되나요 하나요 인가요 에서 으로
""".split())


# LLM 없이도 한국어 질문으로 영어 코드를 찾을 수 있게 하는 작은 용어 사전 (자유롭게 늘려 쓰세요)
KO_EN: dict[str, str] = {
    "라우팅": "route routing url rule dispatch", "라우터": "router route", "경로": "path route url",
    "요청": "request", "응답": "response", "세션": "session", "쿠키": "cookie", "헤더": "header headers",
    "로그인": "login auth signin", "로그아웃": "logout", "인증": "auth authenticate authentication",
    "권한": "permission authorize role", "토큰": "token", "비밀번호": "password", "암호화": "encrypt hash",
    "해시": "hash", "사용자": "user", "회원": "user member account", "계정": "account user",
    "설정": "config settings setup", "환경": "env environment config", "초기화": "init setup initialize",
    "데이터베이스": "database db", "디비": "db database", "쿼리": "query", "저장": "save store write",
    "불러오기": "load read", "읽기": "read load", "쓰기": "write", "삭제": "delete remove", "수정": "update edit",
    "생성": "create new build", "조회": "get query find fetch", "검색": "search find query", "정렬": "sort order",
    "변환": "convert transform", "파싱": "parse", "직렬화": "serialize dump", "역직렬화": "deserialize load",
    "에러": "error exception", "오류": "error exception", "예외": "exception error", "로그": "log logger logging",
    "템플릿": "template render", "렌더링": "render", "화면": "view render template", "뷰": "view",
    "모델": "model", "스키마": "schema", "검증": "validate validation check", "유효성": "validate validation",
    "폼": "form", "파일": "file", "업로드": "upload", "다운로드": "download", "이미지": "image",
    "캐시": "cache", "연결": "connect connection", "서버": "server serve", "클라이언트": "client",
    "실행": "run execute main", "시작": "start run main", "종료": "close shutdown stop exit",
    "이벤트": "event signal", "신호": "signal", "콜백": "callback", "훅": "hook", "미들웨어": "middleware",
    "블루프린트": "blueprint", "확장": "extension extend", "플러그인": "plugin", "등록": "register add",
    "명령": "command cli", "명령어": "command cli", "인자": "argument arg param", "옵션": "option",
    "리다이렉트": "redirect", "상태": "status state", "컨텍스트": "context ctx", "앱": "app application",
    "어플리케이션": "app application", "애플리케이션": "app application", "테스트": "test",
    "비동기": "async await", "스레드": "thread", "큐": "queue", "작업": "task job", "스케줄": "schedule",
    "메시지": "message", "알림": "notification notify", "이메일": "email mail", "결제": "payment pay charge",
    "주문": "order", "상품": "product item", "장바구니": "cart", "게시글": "post article", "댓글": "comment",
    "그래프": "graph", "노드": "node", "간선": "edge", "분석": "analyze parse", "요약": "summary summarize",
    "학습": "train fit", "예측": "predict", "모델링": "model", "데이터": "data dataset", "전처리": "preprocess",
    "인코딩": "encode encoding", "디코딩": "decode", "압축": "compress", "다시시도": "retry", "재시도": "retry",
    "타임아웃": "timeout", "프록시": "proxy", "어댑터": "adapter", "리디렉션": "redirect", "쿠키저장소": "cookiejar",
}


def korean_keywords(text: str) -> list[str]:
    """질문 속 한국어 단어를 사전으로 영어 키워드로 바꾼다 ("라우팅은" → route routing ...)."""
    out: list[str] = []
    for word in _HANGUL.findall(text or ""):
        for ko, en in KO_EN.items():
            if ko in word:
                out.extend(en.split())
    return list(dict.fromkeys(out))


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for word in _WORD.findall(text or ""):
        if _HANGUL.fullmatch(word):
            if word in KO_STOP:
                continue
            tokens.append(word)
            if len(word) >= 2:
                tokens.extend(word[i:i + 2] for i in range(len(word) - 1))
            continue
        pieces = [p.lower() for chunk in word.split("_") for p in _CAMEL.findall(chunk)]
        tokens.extend(p for p in pieces if len(p) > 1 and p not in EN_STOP)
        if len(pieces) > 1:
            tokens.append(word.lower())  # 전체 식별자도 하나의 토큰으로
    return tokens


class BM25Index:
    def __init__(self, docs: dict[str, list[str]], k1: float = 1.4, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf = {doc_id: Counter(toks) for doc_id, toks in docs.items()}
        self.len = {doc_id: len(toks) for doc_id, toks in docs.items()}
        self.avg = (sum(self.len.values()) / len(self.len)) if self.len else 1.0
        df: Counter = Counter()
        for tf in self.tf.values():
            df.update(tf.keys())
        n = max(1, len(self.tf))
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def search(self, query_tokens: list[str], k: int = 10, allow=None) -> list[tuple[str, float]]:
        q = Counter(t for t in query_tokens if t in self.idf)
        if not q:
            return []
        scores: dict[str, float] = {}
        for doc_id, tf in self.tf.items():
            if allow is not None and not allow(doc_id):
                continue
            s = 0.0
            dl = self.len[doc_id] or 1
            for t, qn in q.items():
                f = tf.get(t)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avg))
            if s > 0:
                scores[doc_id] = s
        return sorted(scores.items(), key=lambda kv: -kv[1])[:k]


def node_document(cg: CodeGraph, nid: str) -> list[str]:
    d = cg.node(nid)
    name_tokens = tokenize(d.get("name", ""))
    toks = name_tokens * 3  # 이름이 맞으면 가장 중요하므로 가중치 3배
    toks += tokenize(d.get("qualname", "").replace(".", " "))
    toks += tokenize(d.get("path", "").replace("/", " ").replace(".py", ""))
    toks += tokenize(d.get("docstring", ""))
    toks += tokenize(d.get("summary", "")) * 2
    if d["kind"] in ("function", "method"):
        for callee in cg.callees(nid)[:20]:
            toks += tokenize(cg.node(callee).get("name", ""))
    return toks


def build_index(cg: CodeGraph) -> BM25Index:
    docs = {nid: node_document(cg, nid) for nid in cg.ids("file", "class", "function", "method")}
    return BM25Index(docs)
