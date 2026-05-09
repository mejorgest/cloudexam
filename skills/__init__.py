"""
Agent Skills - Reusable Capabilities

Skills are modular, filesystem-based resources that provide specialized functionality.
Each skill packages:
- SKILL.md: Metadata and instructions
- main.py: Executable code
- Optional resources

Usage:
    from skills.skill_name import function_name
    result = function_name(args)

For async skills:
    result = await async_function(args)
"""
import os
import sys

# Add skills directory to path for imports
SKILLS_DIR = os.path.dirname(__file__)
if SKILLS_DIR not in sys.path:
    sys.path.insert(0, SKILLS_DIR)

# Also add parent for accessing servers
PARENT_DIR = os.path.dirname(SKILLS_DIR)
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)






