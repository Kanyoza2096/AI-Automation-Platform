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

    def delete(self):
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


class EmptyResult:
    data = []


class EmptyUpsertQuery:
    def upsert(self, *_args, **_kwargs):
        return self

    def execute(self):
        return EmptyResult()


class EmptyUpsertSupabase:
    def table(self, _name):
        return EmptyUpsertQuery()


def test_save_raises_when_upsert_is_not_confirmed():
    repo = KnowledgeRepository(supabase_client=EmptyUpsertSupabase())
    entity = Mock()
    entity.id = "kb-unconfirmed-record"
    entity.model_dump.return_value = {"id": entity.id}

    with pytest.raises(RuntimeError, match="could not be persisted"):
        repo.save(entity)

    assert entity.id not in repo._in_memory


def test_find_by_id_raises_when_supabase_read_fails():
    repo = KnowledgeRepository(supabase_client=FailingSupabase())

    with pytest.raises(RuntimeError, match="could not be loaded"):
        repo.find_by_id("kb-test-record")


def test_find_by_workspace_raises_when_supabase_read_fails():
    repo = KnowledgeRepository(supabase_client=FailingSupabase())

    with pytest.raises(RuntimeError, match="could not be loaded"):
        repo.find_by_workspace("ws-test")


def test_delete_raises_when_supabase_delete_fails():
    repo = KnowledgeRepository(supabase_client=FailingSupabase())

    with pytest.raises(RuntimeError, match="could not be deleted"):
        repo.delete("kb-test-record")
