#!/bin/sh
# One-time setup per clone: use the versioned hooks in .githooks/ (tests run after every commit).
cd "$(git rev-parse --show-toplevel)" || exit 1
git config core.hooksPath .githooks
echo "Git hooks enabled: .githooks/ (post-commit runs the test suite)"
