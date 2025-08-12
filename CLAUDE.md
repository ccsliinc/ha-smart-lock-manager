# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Python-based lock manager project. The repository is currently in its initial state with only IDE configuration files present.

## Development Setup

This project appears to be set up for Python development. When implementing the lock manager:

- Follow Python best practices and PEP 8 style guidelines
- Use type hints for better code documentation and IDE support
- Consider using a virtual environment for dependency management

## Common Development Commands

Since this is a new Python project, typical commands will likely include:

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # On macOS/Linux
# or
venv\Scripts\activate  # On Windows

# Install dependencies (once requirements.txt exists)
pip install -r requirements.txt

# Run tests (once test framework is chosen)
python -m pytest  # if using pytest
# or
python -m unittest discover  # if using unittest

# Code formatting and linting (common tools)
black .  # code formatting
flake8 .  # linting
mypy .  # type checking
```

## Architecture Considerations

As a lock manager, this project will likely need to handle:

- Concurrent access control mechanisms
- Resource locking and unlocking operations
- Deadlock detection and prevention
- Lock timeout and expiration handling
- Thread safety and synchronization

Consider implementing:
- A clean separation between lock acquisition logic and business logic
- Proper error handling for lock conflicts and timeouts
- Comprehensive logging for lock operations and debugging
- Unit tests covering both normal operations and edge cases (deadlocks, timeouts)

## Project Structure Recommendations

When developing this lock manager, consider organizing code with:
- Core lock management classes in a main module
- Separate modules for different lock types (if needed)
- Configuration management for lock timeouts and policies
- Comprehensive test coverage for concurrent scenarios