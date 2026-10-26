"""The ER diagram of a project.

The diagram is worked out from the schema, and then named by the model if
one is there to do it. The labels are kept against the schema they were
made for, because a schema that has not changed cannot have different
labels, and because asking a model the same question twice costs money
twice.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import OrderedDict

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import LLMError
from app.llm.service import LLMService
from app.schemas.er import ErModel
from app.services.er_model import apply_annotations, build_model

logger = logging.getLogger(__name__)

# How many labelled diagrams to keep. The number is small because a
# project changes its schema now and then, and a stale entry costs a
# little memory.
_CACHE_SIZE = 64
_CACHE: OrderedDict[str, dict] = OrderedDict()


class ErService:
    """The diagram of a project's schema."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def diagram(self, schema_doc: dict, *, llm: LLMService) -> ErModel:
        """The diagram of a schema, named as well as the schema can name it.

        A model that is not there, or that fails, costs the diagram its
        labels and nothing else. The entities, the attributes and the
        relationships are all worked out from the schema, so they are there
        either way.
        """
        model = build_model(schema_doc)

        annotations = await self._annotations(schema_doc, llm)
        if annotations:
            model = apply_annotations(model, annotations)
        return model

    async def _annotations(
        self, schema_doc: dict, llm: LLMService
    ) -> dict | None:
        """What the model called things, if a model was asked."""
        if not llm.is_configured:
            return None

        key = _cache_key(schema_doc)
        cached = _CACHE.get(key)
        if cached is not None:
            _CACHE.move_to_end(key)
            return cached

        try:
            annotations = await llm.annotate_er(schema_doc=schema_doc)
        except LLMError as exc:
            logger.warning(
                "ER labelling failed (%s); drawing the diagram unlabelled.", exc
            )
            return None

        _CACHE[key] = annotations
        if len(_CACHE) > _CACHE_SIZE:
            _CACHE.popitem(last=False)
        return annotations


def _cache_key(schema_doc: dict) -> str:
    """What the labels were made for."""
    raw = json.dumps(schema_doc, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()
