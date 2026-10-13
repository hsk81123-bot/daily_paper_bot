"""논문 레코드, 식별자 정규화, 중복 제거."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from difflib import SequenceMatcher

ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?")
ARXIV_DOI_RE = re.compile(r"10\.48550/arxiv\.(\d{4}\.\d{4,5})", re.I)


@dataclass
class Paper:
    title: str
    url: str
    published: datetime
    source: str                      # 수집 경로: arXiv / OpenAlex / RSS 이름
    authors: list[str] = field(default_factory=list)
    abstract: str = ""
    venue: str = ""                  # 저널명 또는 "arXiv"
    doi: str = ""
    arxiv_id: str = ""
    pdf_url: str = ""
    categories: list[str] = field(default_factory=list)
    # 아래는 Claude가 채움
    title_ko: str = ""
    summary: str = ""
    why: str = ""

    @property
    def key(self) -> str:
        """논문 고유 키. DOI > arXiv ID > 정규화한 제목 순."""
        if self.arxiv_id:
            return f"arxiv:{self.arxiv_id}"
        if self.doi:
            return f"doi:{self.doi}"
        return title_key(self.title)

    @property
    def keys(self) -> set[str]:
        """기록 비교용 키 전체(arXiv 버전이 저널로 나와도 다시 보내지 않도록 제목 키 포함)."""
        out = {title_key(self.title)}
        if self.arxiv_id:
            out.add(f"arxiv:{self.arxiv_id}")
        if self.doi:
            out.add(f"doi:{self.doi}")
        return out

    def author_line(self, limit: int = 3) -> str:
        if not self.authors:
            return ""
        if len(self.authors) <= limit:
            return ", ".join(self.authors)
        return f"{', '.join(self.authors[:limit])} 외 {len(self.authors) - limit}명"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["published"] = self.published.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Paper":
        d = dict(d)
        d["published"] = datetime.fromisoformat(d["published"])
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})


def normalize_doi(doi: str | None) -> str:
    if not doi:
        return ""
    doi = doi.strip().lower()
    doi = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", doi)
    doi = re.sub(r"^doi:\s*", "", doi)
    return doi


def arxiv_id_from(text: str | None) -> str:
    """arXiv URL·ID·DOI에서 버전 없는 ID를 꺼낸다."""
    if not text:
        return ""
    m = ARXIV_DOI_RE.search(text)
    if m:
        return m.group(1)
    if "arxiv" in text.lower() or re.fullmatch(r"\s*\d{4}\.\d{4,5}(v\d+)?\s*", text):
        m = ARXIV_ID_RE.search(text)
        if m:
            return m.group(1)
    return ""


def clean_text(text: str | None) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)       # RSS/JATS 태그 제거
    return re.sub(r"\s+", " ", text).strip()


def title_key(title: str) -> str:
    norm = re.sub(r"[^a-z0-9]+", "", title.lower())
    return f"t:{norm[:120]}"


def similar_titles(a: str, b: str, threshold: float = 0.92) -> bool:
    na, nb = title_key(a)[2:], title_key(b)[2:]
    if not na or not nb:
        return False
    if na == nb:
        return True
    return SequenceMatcher(None, na, nb).ratio() >= threshold


def _merge(keep: Paper, other: Paper) -> Paper:
    """같은 논문이 arXiv와 저널 양쪽에서 잡히면 정보를 합친다(저널 정보 우선)."""
    if keep.venue in ("", "arXiv") and other.venue not in ("", "arXiv"):
        keep, other = other, keep
    keep.doi = keep.doi or other.doi
    keep.arxiv_id = keep.arxiv_id or other.arxiv_id
    keep.pdf_url = keep.pdf_url or other.pdf_url
    if len(other.abstract) > len(keep.abstract):
        keep.abstract = other.abstract
    keep.authors = keep.authors or other.authors
    keep.published = min(keep.published, other.published)
    return keep


def dedupe(papers: list[Paper]) -> list[Paper]:
    out: list[Paper] = []
    for p in papers:
        for i, q in enumerate(out):
            same_id = (p.doi and p.doi == q.doi) or (p.arxiv_id and p.arxiv_id == q.arxiv_id)
            if same_id or similar_titles(p.title, q.title):
                out[i] = _merge(q, p)
                break
        else:
            out.append(p)
    return out
