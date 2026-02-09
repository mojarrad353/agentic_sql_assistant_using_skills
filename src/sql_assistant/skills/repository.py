from typing import TypedDict
import os
from pathlib import Path

class Skill(TypedDict):
    """A skill that can be progressively disclosed to the agent."""
    name: str
    description: str
    content: str

class SkillRepository:
    def __init__(self):
        # We'll determine the skills directory based on this file's location
        # src/sql_assistant/skills/repository.py -> parent is 'skills'
        self.skills_dir = Path(__file__).parent

    def list_skills(self) -> list[Skill]:
        """Return a list of all available skills by scanning directories."""
        skills = []
        
        # Scan subdirectories in the skills folder
        if not self.skills_dir.exists():
            return []

        for item in self.skills_dir.iterdir():
            if item.is_dir() and not item.name.startswith("__"):
                description_path = item / "description.txt"
                if description_path.exists():
                    description = description_path.read_text(encoding="utf-8").strip()
                    skills.append({
                        "name": item.name,
                        "description": description,
                        "content": ""  # Don't load full content yet
                    })
        return skills

    def get_skill(self, skill_name: str) -> Skill | None:
        """Get a full skill by name, loading content from file."""
        skill_dir = self.skills_dir / skill_name
        if not skill_dir.exists() or not skill_dir.is_dir():
            return None
            
        description_path = skill_dir / "description.txt"
        content_path = skill_dir / "content.md"
        
        if not description_path.exists() or not content_path.exists():
            return None
            
        return {
            "name": skill_name,
            "description": description_path.read_text(encoding="utf-8").strip(),
            "content": content_path.read_text(encoding="utf-8").strip()
        }

    def get_skill_names(self) -> str:
        """Return comma-separated list of skill names."""
        skills = self.list_skills()
        return ", ".join([s["name"] for s in skills])

_repository = SkillRepository()

def get_skill_repository() -> SkillRepository:
    return _repository
