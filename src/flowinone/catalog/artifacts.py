"""Optional local OCR and anonymous person-cluster services."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import text

from src.flowinone.resource_library.database import ResourceDatabase
from src.flowinone.resource_library.models import new_id, utc_now_text


class OCRProvider(Protocol):
    name: str
    model: str

    @property
    def available(self) -> bool: ...

    def extract(self, path: Path) -> str: ...


class TesseractOCRProvider:
    """Optional local OCR provider; imported only when explicitly invoked."""

    name = "tesseract"
    model = "local-default"

    @property
    def available(self) -> bool:
        try:
            import pytesseract  # noqa: F401
            return True
        except ImportError:
            return False

    def extract(self, path: Path) -> str:
        if not self.available:
            raise RuntimeError("pytesseract 尚未安裝；OCR 是可選功能")
        import pytesseract
        from PIL import Image

        with Image.open(path) as image:
            return str(pytesseract.image_to_string(image)).strip()


class CatalogArtifactService:
    def __init__(self, database: ResourceDatabase):
        self.database = database

    def store(
        self,
        item_id: str,
        artifact_type: str,
        *,
        content_text: str = "",
        content: dict[str, Any] | None = None,
        provider: str = "local",
        model: str = "",
        version: str = "1",
        input_fingerprint: str = "",
    ) -> dict[str, Any]:
        if artifact_type not in {"ocr", "face_detection", "face_embedding"}:
            raise ValueError("不支援的 Catalog artifact")
        artifact_id = new_id()
        now = utc_now_text()
        with self.database.engine.begin() as conn:
            if conn.execute(text("SELECT 1 FROM catalog_items WHERE id=:id"), {"id": item_id}).first() is None:
                raise LookupError(item_id)
            conn.execute(text("UPDATE catalog_artifacts SET is_current=0 WHERE catalog_item_id=:item AND artifact_type=:type"), {"item": item_id, "type": artifact_type})
            conn.execute(
                text("INSERT INTO catalog_artifacts(id,catalog_item_id,artifact_type,content_text,content_json,provider,model,version,input_fingerprint,is_current,created_at) VALUES(:id,:item,:type,:text,:json,:provider,:model,:version,:fingerprint,1,:created)"),
                {"id": artifact_id, "item": item_id, "type": artifact_type, "text": content_text or None, "json": json.dumps(content or {}, ensure_ascii=False), "provider": provider, "model": model or None, "version": version, "fingerprint": input_fingerprint or None, "created": now},
            )
            if artifact_type == "ocr" and content_text:
                current = conn.execute(text("SELECT extracted_text FROM catalog_fts WHERE catalog_item_id=:id"), {"id": item_id}).scalar_one_or_none() or ""
                merged = current if content_text in current else f"{current}\n{content_text}".strip()
                conn.execute(text("UPDATE catalog_fts SET extracted_text=:text WHERE catalog_item_id=:id"), {"id": item_id, "text": merged[:1_000_000]})
        return self.get(artifact_id)

    def get(self, artifact_id: str) -> dict[str, Any]:
        with self.database.engine.connect() as conn:
            row = conn.execute(text("SELECT * FROM catalog_artifacts WHERE id=:id"), {"id": artifact_id}).mappings().first()
        if row is None:
            raise LookupError(artifact_id)
        result = dict(row)
        result["content"] = json.loads(result.pop("content_json") or "{}")
        result["is_current"] = bool(result["is_current"])
        return result

    def run_ocr(self, item_id: str, path: Path, provider: OCRProvider | None = None) -> dict[str, Any]:
        provider = provider or TesseractOCRProvider()
        text_value = provider.extract(path)
        return self.store(item_id, "ocr", content_text=text_value, provider=provider.name, model=provider.model, input_fingerprint=str(path.stat().st_mtime_ns))


class PersonService:
    """Human-controlled anonymous clusters; no sensitive attribute inference."""

    def __init__(self, database: ResourceDatabase):
        self.database = database

    def create(self, display_name: str = "") -> dict[str, Any]:
        person_id = new_id()
        now = utc_now_text()
        with self.database.engine.begin() as conn:
            conn.execute(text("INSERT INTO people(id,display_name,status,metadata_json,created_at,updated_at) VALUES(:id,:name,:status,'{}',:now,:now)"), {"id": person_id, "name": display_name.strip() or None, "status": "named" if display_name.strip() else "anonymous", "now": now})
        return self.get(person_id)

    def get(self, person_id: str) -> dict[str, Any]:
        with self.database.engine.connect() as conn:
            row = conn.execute(text("SELECT * FROM people WHERE id=:id"), {"id": person_id}).mappings().first()
        if row is None:
            raise LookupError(person_id)
        result = dict(row)
        result["metadata"] = json.loads(result.pop("metadata_json") or "{}")
        return result

    def rename(self, person_id: str, display_name: str) -> dict[str, Any]:
        name = display_name.strip()
        if not name:
            raise ValueError("人物名稱不可空白")
        with self.database.engine.begin() as conn:
            result = conn.execute(text("UPDATE people SET display_name=:name,status='named',updated_at=:now WHERE id=:id"), {"id": person_id, "name": name, "now": utc_now_text()})
            if not result.rowcount:
                raise LookupError(person_id)
        return self.get(person_id)

    def link(self, person_id: str, item_id: str, *, confidence: float | None = None, source: str = "user") -> None:
        with self.database.engine.begin() as conn:
            conn.execute(text("INSERT OR REPLACE INTO item_people(catalog_item_id,person_id,confidence,source,created_at) VALUES(:item,:person,:confidence,:source,:created)"), {"item": item_id, "person": person_id, "confidence": confidence, "source": source[:24], "created": utc_now_text()})


__all__ = ["CatalogArtifactService", "OCRProvider", "PersonService", "TesseractOCRProvider"]
