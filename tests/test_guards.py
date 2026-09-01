import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tutor.guards import scan, redact

CASES = [
    # (label, expected_action, text)
    ("syntax illustration (Java decl)", "allow",
     "An ArrayList is declared like `ArrayList<String> names = new ArrayList<>();` "
     "-- the angle brackets are the type parameter."),

    ("syntax illustration (fenced, 1 line)", "allow",
     "The signature looks like:\n```java\npublic static int findMax(int[] arr)\n```\n"
     "What do you think the body needs to do first?"),

    ("full python solution", "block",
     "Here you go:\n```python\ndef find_max(arr):\n    m = arr[0]\n    for x in arr:\n"
     "        if x > m:\n            m = x\n    return m\n```"),

    ("java solution", "block",
     "```java\npublic static int findMax(int[] a) {\n    int m = a[0];\n"
     "    for (int i = 1; i < a.length; i++) { if (a[i] > m) m = a[i]; }\n    return m;\n}\n```"),

    ("unfenced python (no backticks)", "block",
     "Sure, just write this:\n\ndef solve(n):\n    total = 0\n    for i in range(n):\n"
     "        total += i\n    return total\n\nThat should do it."),

    ("bare loop, no fence", "block",
     "You'd do:\n    for i in range(len(arr)):\n        if arr[i] > best:\n            best = arr[i]\n"),

    ("pure prose tutoring", "allow",
     "Good question. Think about what has to be true before the loop starts -- "
     "what value should your running maximum hold on the very first comparison? "
     "Check Module 4.2 on loop invariants."),

    ("KNOWN GAP: prose algorithm", "allow",   # documents the evasion, does not fix it
     "Loop through the array, keep a variable holding the largest seen so far, "
     "compare each element to it, update when bigger, then return it at the end."),

    ("inline function call", "allow",
     "You can get the length with `len(arr)` -- no loop needed for that part."),
]

fails = 0
for label, expected, text in CASES:
    v = scan(text)
    ok = v.action == expected
    fails += (not ok)
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {label:38} -> {v.action:5} (want {expected})")
    if v.reasons:
        print(f"        reasons: {'; '.join(v.reasons)}")

print()
print("=== redaction demo ===")
sol = CASES[2][2]
print(redact(sol, scan(sol)))
print()
print(f"{len(CASES)-fails}/{len(CASES)} passed")
sys.exit(1 if fails else 0)
