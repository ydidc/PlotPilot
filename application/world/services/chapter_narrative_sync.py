"""章节保存后：LLM 生成章末总结 → 节拍沿用既有规划 → StoryKnowledge → 向量索引。

节拍来源（按优先级，不由 LLM 现编）：
1. 知识库里该章已有 beat_sections（宏观规划 / 用户手填）
2. 结构树中该章节点 outline（规划节拍，按换行/分号拆条）
3. 仍无则保持空列表，仅写 summary。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from typing import Any, List, Optional, Tuple

from domain.ai.services.llm_service import LLMService, GenerationConfig
from domain.ai.value_objects.prompt import Prompt
from domain.novel.value_objects.foreshadowing import (
    Foreshadowing,
    ForeshadowingStatus,
    ImportanceLevel,
)
from domain.novel.value_objects.novel_id import NovelId
from domain.structure.story_node import NodeType

logger = logging.getLogger(__name__)


def _extract_json_object(text: str) -> dict:
    """从模型输出中解析 JSON 对象。"""
    s = (text or "").strip()
    if not s:
        return {}
    if "```" in s:
        if "```json" in s:
            start = s.find("```json") + 7
            end = s.find("```", start)
            if end != -1:
                s = s[start:end].strip()
        else:
            start = s.find("```") + 3
            end = s.rfind("```")
            if end > start:
                s = s[start:end].strip()
    if not s.startswith("{"):
        i = s.find("{")
        if i != -1:
            s = s[i:]
    return json.loads(s)


def _beats_from_structure_outline(novel_id: str, chapter_number: int) -> List[str]:
    """从结构树章节节点的 outline 拆成节拍条（规划层本来就有）。"""
    try:
        from application.paths import get_db_path
        from infrastructure.persistence.database.story_node_repository import StoryNodeRepository

        repo = StoryNodeRepository(str(get_db_path()))
        nodes = repo.get_by_novel_sync(novel_id)
        for n in nodes:
            if n.node_type != NodeType.CHAPTER:
                continue
            if int(n.number) != int(chapter_number):
                continue
            outline = (n.outline or "").strip()
            if not outline:
                return []
            parts = re.split(r"[\n\r；;]+", outline)
            return [p.strip() for p in parts if p.strip()][:32]
    except Exception as e:
        logger.debug("从结构树取 outline 失败 novel=%s ch=%s: %s", novel_id, chapter_number, e)
    return []


def _resolve_beat_sections(
    novel_id: str,
    chapter_number: int,
    existing_beats: List[str],
) -> List[str]:
    """节拍：优先已有知识库条；否则用结构树 outline。"""
    cleaned = [str(b).strip() for b in (existing_beats or []) if str(b).strip()]
    if cleaned:
        return cleaned
    return _beats_from_structure_outline(novel_id, chapter_number)


async def llm_chapter_extract_bundle(
    llm: LLMService,
    chapter_content: str,
    chapter_number: int,
) -> dict:
    """一次 LLM 调用：叙事摘要 + 关键事件/埋线 + 人物关系三元组 + 伏笔线索（与后台抽取同源，避免两次调用）。"""
    body = chapter_content.strip()
    if len(body) > 24000:
        body = body[:24000] + "\n\n…（正文过长已截断）"

    system = """你是网文叙事编辑与信息抽取。根据章节正文输出**一个** JSON 对象（不要其它说明文字）：
{
  "summary": "string，200～500 字，章末叙事总结，便于检索与衔接",
  "key_events": "string",
  "open_threads": "string",
  "relation_triples": [ {"subject": "主体", "predicate": "关系", "object": "客体"} ],
  "foreshadow_hints": [ {"description": "伏笔或悬念描述"} ]
}
约束：
- relation_triples：只写文中明确出现的关系，最多 8 条；无则 []。
- foreshadow_hints：潜在伏笔/未解悬念，最多 4 条；无则 []。
- 不要编造 beat 列表；summary/key_events/open_threads 用中文；严格合法 JSON。"""

    user = f"第 {chapter_number} 章正文如下：\n\n{body}"

    prompt = Prompt(system=system, user=user)
    config = GenerationConfig(max_tokens=3072, temperature=0.45)

    result = await llm.generate(prompt, config)
    raw = result.content if hasattr(result, "content") else str(result)
    data = _extract_json_object(raw)

    triples_raw = data.get("relation_triples") or data.get("triples") or []
    if not isinstance(triples_raw, list):
        triples_raw = []
    hints_raw = data.get("foreshadow_hints") or data.get("foreshadows") or []
    if not isinstance(hints_raw, list):
        hints_raw = []

    return {
        "summary": str(data.get("summary", "")).strip(),
        "key_events": str(data.get("key_events", "")).strip(),
        "open_threads": str(data.get("open_threads", "")).strip(),
        "relation_triples": triples_raw[:8],
        "foreshadow_hints": hints_raw[:4],
    }


def persist_bundle_triples_and_foreshadows(
    novel_id: str,
    chapter_number: int,
    bundle: dict,
    triple_repository: Any,
    foreshadowing_repo: Any,
) -> None:
    """将 bundle 中的三元组与伏笔写入表（与旧 BG 两任务等价，但只解析一次 JSON）。"""
    triples = bundle.get("relation_triples") or []
    hints = bundle.get("foreshadow_hints") or []

    if triple_repository and triples:
        kr = getattr(triple_repository, "_kr", None)
        if kr is None:
            logger.warning("triple_repository 无 _kr，跳过三元组落库")
        else:
            for item in triples:
                if not isinstance(item, dict):
                    continue
                s = str(item.get("subject", "")).strip()
                p = str(item.get("predicate", "")).strip()
                o = str(item.get("object", "")).strip()
                if not (s and p and o):
                    continue
                row = {
                    "id": str(uuid.uuid4()),
                    "subject": s,
                    "predicate": p,
                    "object": o,
                    "chapter_number": chapter_number,
                    "source_type": "autopilot_extract",
                    "confidence": 0.7,
                    "entity_type": "character",
                    "note": "",
                }
                try:
                    kr.save_triple(novel_id, row)
                except Exception as e:
                    logger.debug("三元组落库跳过: %s", e)

    if foreshadowing_repo and hints:
        try:
            registry = foreshadowing_repo.get_by_novel_id(NovelId(novel_id))
            if not registry:
                return
            for h in hints:
                if not isinstance(h, dict):
                    desc = str(h).strip()
                else:
                    desc = str(h.get("description", "")).strip()
                if not desc:
                    continue
                try:
                    registry.register(
                        Foreshadowing(
                            id=str(uuid.uuid4()),
                            planted_in_chapter=max(1, chapter_number),
                            description=desc,
                            importance=ImportanceLevel.MEDIUM,
                            status=ForeshadowingStatus.PLANTED,
                        )
                    )
                except Exception:
                    pass
            foreshadowing_repo.save(registry)
        except Exception as e:
            logger.warning("伏笔落库失败 novel=%s ch=%s: %s", novel_id, chapter_number, e)


async def sync_chapter_narrative_after_save(
    novel_id: str,
    chapter_number: int,
    content: str,
    knowledge_service: Any,
    indexing_svc: Any,
    llm_service: LLMService,
    triple_repository: Any = None,
    foreshadowing_repo: Any = None,
) -> None:
    """异步：一次 LLM 写 summary/事件/埋线 + 可选三元组与伏笔 → 节拍来自规划 → upsert knowledge → 向量索引。"""
    if not content or not str(content).strip():
        logger.debug("跳过叙事同步：正文为空 novel=%s ch=%s", novel_id, chapter_number)
        return

    existing = None
    existing_beats: List[str] = []
    try:
        k = knowledge_service.get_knowledge(novel_id)
        for ch in getattr(k, "chapters", []) or []:
            if getattr(ch, "chapter_id", None) == chapter_number:
                existing = ch
                break
        if existing and getattr(existing, "beat_sections", None):
            existing_beats = list(existing.beat_sections or [])
    except Exception:
        pass

    try:
        bundle = await llm_chapter_extract_bundle(llm_service, content, chapter_number)
        summary = bundle.get("summary") or ""
        key_events = bundle.get("key_events") or ""
        open_threads = bundle.get("open_threads") or ""
    except Exception as e:
        logger.warning("LLM 章末 bundle 失败 novel=%s ch=%s: %s", novel_id, chapter_number, e)
        summary, key_events, open_threads = "", "", ""
        bundle = {"relation_triples": [], "foreshadow_hints": []}

    consistency_note = ""
    if existing:
        consistency_note = (existing.consistency_note or "") or ""
        if not key_events:
            key_events = existing.key_events or ""
        if not open_threads:
            open_threads = existing.open_threads or ""

    beat_sections = _resolve_beat_sections(novel_id, chapter_number, existing_beats)

    knowledge_service.upsert_chapter_summary(
        novel_id=novel_id,
        chapter_id=chapter_number,
        summary=summary,
        key_events=key_events or "（未提取）",
        open_threads=open_threads or "无",
        consistency_note=consistency_note,
        beat_sections=beat_sections,
        sync_status="synced" if summary else "draft",
    )

    if triple_repository is not None or foreshadowing_repo is not None:
        try:
            persist_bundle_triples_and_foreshadows(
                novel_id,
                chapter_number,
                bundle,
                triple_repository,
                foreshadowing_repo,
            )
        except Exception as e:
            logger.warning(
                "bundle 三元组/伏笔落库失败 novel=%s ch=%s: %s", novel_id, chapter_number, e
            )
    logger.info(
        "分章叙事已落库 novel=%s ch=%s beats=%d(src=planning/knowledge) summary_len=%d",
        novel_id,
        chapter_number,
        len(beat_sections),
        len(summary),
    )

    if indexing_svc is None:
        return
    text_for_vector = summary.strip() if summary.strip() else "；".join(beat_sections) if beat_sections else content[:800]
    try:
        await indexing_svc.ensure_collection(novel_id)
        await indexing_svc.index_chapter_summary(novel_id, chapter_number, text_for_vector)
        logger.debug("章节向量索引完成 novel=%s ch=%s", novel_id, chapter_number)
    except Exception as e:
        logger.warning("章节向量索引失败 novel=%s ch=%s: %s", novel_id, chapter_number, e)


def sync_chapter_narrative_after_save_blocking(
    novel_id: str,
    chapter_number: int,
    content: str,
    knowledge_service: Any,
    indexing_svc: Any,
    llm_service: LLMService,
    triple_repository: Any = None,
    foreshadowing_repo: Any = None,
) -> None:
    """供 FastAPI BackgroundTasks 同步入口调用。"""
    try:
        asyncio.run(
            sync_chapter_narrative_after_save(
                novel_id,
                chapter_number,
                content,
                knowledge_service,
                indexing_svc,
                llm_service,
                triple_repository=triple_repository,
                foreshadowing_repo=foreshadowing_repo,
            )
        )
    except RuntimeError as e:
        if "asyncio.run() cannot be called from a running event loop" in str(e):
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(
                    sync_chapter_narrative_after_save(
                        novel_id,
                        chapter_number,
                        content,
                        knowledge_service,
                        indexing_svc,
                        llm_service,
                        triple_repository=triple_repository,
                        foreshadowing_repo=foreshadowing_repo,
                    )
                )
            finally:
                loop.close()
        else:
            raise
    except Exception as e:
        logger.warning("分章叙事同步失败 novel=%s ch=%s: %s", novel_id, chapter_number, e)
