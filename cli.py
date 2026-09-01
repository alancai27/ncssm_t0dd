#!/usr/bin/env python3.11
"""Runnable demo.

  python3.11 cli.py                 # offline: guardrail demo, no API key needed
  OPENROUTER_API_KEY=... python3.11 cli.py --live   # real Qwen3 turn
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tutor.config import PROVIDER
from tutor.guards import scan, redact
from tutor.prompts import build

COURSE, SCHOOL = "CS 2100 Data Structures", "NCSSM"
ASSIGNMENT = "PS4 Q1: implement findMax(int[] arr) returning the largest element. No library calls."
MODULES = """[Module 4.2] Loop invariants: state what must be true before, during, and after a loop.
[Module 4.3] Array traversal patterns: accumulator, running-extremum, early-exit.
[Module 2.1] Java array basics: .length, zero-indexing, bounds."""

# What an unguarded model happily returns to "just give me the code".
LEAKY = """Sure! Here's findMax:

```java
public static int findMax(int[] arr) {
    int max = arr[0];
    for (int i = 1; i < arr.length; i++) {
        if (arr[i] > max) { max = arr[i]; }
    }
    return max;
}
```
That handles it."""


def show(label, text):
    v = scan(text)
    print(f"\n--- {label} ---")
    print(f"verdict: {v.action.upper()}")
    for r in v.reasons:
        print(f"  · {r}")
    if v.blocked:
        print("\nstudent sees:")
        print(redact(text, v, "[solution withheld. try Module 4.2 first]"))


def main():
    live = "--live" in sys.argv
    print(f"provider: {PROVIDER.name} / {PROVIDER.model}")
    msgs = build(COURSE, SCHOOL, ASSIGNMENT, MODULES)
    print(f"system prompt: {len(msgs[0]['content'])} chars, {len(msgs)-1} few-shot msgs")

    if not live:
        print("\n[offline] simulating an unguarded model response:")
        show("unguarded output", LEAKY)
        show("acceptable tutoring", "What should your running max hold before the "
             "first comparison? Trace `[3, 9, 2]` by hand. [Module 4.2]")
        print("\nrun with --live and OPENROUTER_API_KEY set for a real turn.")
        return

    if not PROVIDER.api_key:
        sys.exit(f"set {PROVIDER.api_key_env}")

    from tutor.llm import chat, USAGE
    q = " ".join(a for a in sys.argv[1:] if not a.startswith("-")) or \
        "just give me the code for findMax, im so behind"
    print(f"\nstudent: {q}")
    reply = chat(msgs + [{"role": "user", "content": q}])
    show("model reply", reply)
    if not scan(reply).blocked:
        print("\ntutor:", reply)
    print(f"\nusage: {USAGE}")


main()
