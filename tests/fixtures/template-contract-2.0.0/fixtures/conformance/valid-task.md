---
id: T-20260609184046Z
title: "Review payment retries"
status: doing
---

# Review payment retries

## Goal

Prevent a duplicate charge when the payment provider retries a callback.

## Handoff

- Next exact step: Compare the retry handler with the recorded provider event.
- Done when: A retried event reuses the original charge receipt.
