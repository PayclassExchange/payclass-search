from app.models import EntityBackground, WebSearch, task_adapter
from app.planner import COURTLISTENER, SEC_EDGAR, SEARXNG_GENERAL, SEARXNG_NEWS, plan


def test_web_search_maps_to_one_general_step():
    task = task_adapter.validate_python({"type": "web_search", "query": "payclass"})
    steps = plan(task)
    assert [s.resource for s in steps] == [SEARXNG_GENERAL]


def test_entity_background_fans_out_to_three_independent_steps():
    task = task_adapter.validate_python({"type": "entity_background", "name": "Acme Corp"})
    steps = plan(task)
    assert {s.resource for s in steps} == {SEARXNG_NEWS, COURTLISTENER, SEC_EDGAR}
    assert all(s.depends_on == [] for s in steps)


def test_unknown_task_type_is_rejected():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        task_adapter.validate_python({"type": "people_search", "name": "x"})


def test_query_length_is_enforced():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        WebSearch(type="web_search", query="x")
    assert isinstance(
        EntityBackground(type="entity_background", name="Acme"), EntityBackground
    )
