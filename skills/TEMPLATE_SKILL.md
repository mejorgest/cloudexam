---
name: example-skill
description: Brief description of what this Skill does and when to use it. Be specific about trigger conditions.
created: 2025-01-01T00:00:00
function_name: example_function
is_async: false
---

# Example Skill

## Description

This is a template for creating new skills. A skill packages instructions, metadata, and code that the agent can use automatically when relevant.

**When to use this skill:**
- Describe specific situations that should trigger this skill
- Include keywords the user might say
- Be explicit about the task domain

## Instructions

### Quick Start

```python
from skills.example_skill import example_function

# Basic usage
result = example_function(arg1, arg2)
print(result)
```

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| arg1 | str | Yes | Description of first argument |
| arg2 | int | No | Description of second argument (default: 10) |

### Step-by-step Workflow

1. First, import the skill function
2. Prepare your input data
3. Call the function with appropriate arguments
4. Handle the returned result

## Examples

### Example 1: Basic Usage

```python
from skills.example_skill import example_function

# Simple call
result = example_function("hello", 5)
print(f"Result: {result}")
```

### Example 2: With Error Handling

```python
from skills.example_skill import example_function

try:
    result = example_function("data", 10)
    if result:
        print("Success:", result)
except Exception as e:
    print(f"Error: {e}")
```

### Example 3: Async Usage (if applicable)

```python
from skills.async_example_skill import async_function

# Async skills must be awaited
result = await async_function("param")
print(result)
```

## Notes

- Additional tips or caveats about using this skill
- Known limitations
- Related skills that might be useful

## Resources

This skill uses the following dependencies:
- `servers.filesystem_service.file_operations` - For file persistence
- Any other required modules

## Changelog

- 2025-01-01: Initial version






