"""Regression tests for knowledge repository persistence and indexing failures."""

from unittest.mock import Mock

import pytest

from repositories.knowledge_repository import KnowledgeRepository


class FailingQuery:
    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def upsert(self, *_args, **_kwargs):
        return self

    def execute(self):
        raise OSError("simulated Supabase outage")


class FailingSupabase:
    def table(self, _name):
        return FailingQuery()


def test_save_raises_when_supabase_persistence_fails():
    repo = KnowledgeRepository(supabase_client=FailingSupabase())
    entity = Mock()
    entity.id = "kb-test-record"
    entity.model_dump.return_value = {"id": entity.id}

    with pytest.raises(RuntimeError, match="could not be persisted"):
        repo.save(entity)

    assert entity.id not in repo._in_memory


def test_find_by_brand_raises_when_supabase_read_fails():
    repo = KnowledgeRepository(supabase_client=FailingSupabase())

    with pytest.raises(RuntimeError, match="could not be loaded"):
        repo.find_by_brand("brand-shipsandshrugs-550cb543")
