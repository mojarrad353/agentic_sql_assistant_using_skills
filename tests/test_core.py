import pytest
from src.sql_assistant.config import Settings
from src.sql_assistant.skills.repository import SkillRepository

def test_settings_loading():
    settings = Settings(OPENAI_API_KEY="test-key", OPENAI_MODEL_NAME="gpt-5-mini-test")
    assert settings.OPENAI_API_KEY == "test-key"
    assert settings.OPENAI_MODEL_NAME == "gpt-5-mini-test"
    assert settings.LANGSMITH_TRACING is True  # Default

def test_skill_repository_file_based():
    repo = SkillRepository()
    skills = repo.list_skills()
    
    # We expect 'sales_analytics' and 'inventory_management' folders to be picked up
    assert len(skills) >= 2
    
    # Check that we can find the sales skill
    skill_names = [s["name"] for s in skills]
    assert "sales_analytics" in skill_names
    assert "inventory_management" in skill_names
    
    # Verify we can load content
    sales = repo.get_skill("sales_analytics")
    assert sales is not None
    assert sales["name"] == "sales_analytics"
    assert "Database schema" in sales["description"]
    assert "CREATE TABLE" in sales["content"] or "Schema" in sales["content"]

    # Verify missing skill
    missing = repo.get_skill("non_existent")
    assert missing is None
