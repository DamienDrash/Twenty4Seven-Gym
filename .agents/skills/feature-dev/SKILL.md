---
name: feature-dev
description: "Use when the user wants a structured feature development workflow: discover requirements, explore the codebase, ask clarifying questions, design the implementation, then implement and review."
---

# Feature Development

Use this skill for feature work that benefits from a deliberate process instead of jumping straight into code.

## Goals

- Understand the request and the surrounding code before editing.
- Expose ambiguities early and resolve them explicitly.
- Design an approach that fits the existing codebase.
- Implement only after the direction is clear.
- Review for bugs, regressions, and convention mismatches before closing.

## Workflow

### 1. Discovery

- Restate the requested feature in concrete terms.
- Identify the problem, intended behavior, constraints, and success criteria.
- If the request is vague, ask focused clarification questions.

### 2. Codebase Exploration

- Inspect relevant entry points, similar features, abstractions, and tests.
- Build a concise map of the code path:
  - key files
  - control flow
  - extension points
  - integration boundaries
- When delegation is explicitly requested and available, use well-scoped explorer agents for parallel read-only analysis.

### 3. Clarifying Questions

- Review what is still unspecified after exploration.
- Ask about material gaps before coding:
  - scope limits
  - edge cases
  - compatibility expectations
  - API or schema impact
  - UX details
  - testing expectations
- Do not proceed to implementation while important behavior remains ambiguous.

### 4. Architecture Design

- Propose the implementation approach that best fits the repository.
- If there are meaningful alternatives, summarize the trade-offs and recommend one.
- Make the plan concrete:
  - files to change
  - new components or functions
  - data flow
  - error handling
  - test strategy
- Wait for explicit user approval before implementing.

### 5. Implementation

- Implement the approved approach.
- Follow repository conventions closely.
- Keep changes scoped and coherent.
- Add or update tests when behavior changes.

### 6. Quality Review

- Review the result for:
  - correctness
  - regression risk
  - security issues
  - unnecessary complexity
  - convention violations
  - missing tests
- When delegation is explicitly requested and available, use reviewer agents with distinct focuses.

### 7. Summary

- Summarize what changed, key decisions, and verification performed.
- Call out remaining risks or unverified areas.

## Operating Rules

- Prefer targeted questions over silent assumptions.
- Read the code identified as relevant before making architectural claims.
- Match the depth of the workflow to the task: use the full process for real feature work, and compress it for smaller changes.
- If the user wants direct implementation without ceremony and the scope is clear, keep the workflow lightweight while preserving discovery, planning, and review discipline.
