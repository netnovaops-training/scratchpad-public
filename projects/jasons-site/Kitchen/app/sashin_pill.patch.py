#!/usr/bin/env python3
"""Name the pill after the person it is for. Run from Kitchen/.

Jason, 2026-09-07: "with /kitchen I would like to rework the recipes in
them, for those that have no beef or pork I'd like a Sashin friendly pill."

THE MACHINERY WAS ALREADY THERE and it works: diet.py reads the ingredient
list of every recipe and classifies it, the result is injected into the
recipe's own labels so it flows through the existing facet counts and label
filter, and it is computed at read time so nothing goes stale. Measured
today across the whole box: 249 recipes, 190 clean, 58 adaptable, 1
unreadable.

WHAT WAS ACTUALLY MISSING WAS THE NAME. The pill read "no pork or beef",
which describes a property of the food. Jason does not scan the page
thinking about pork; he scans it thinking about whether Sashin can eat it.
A label that answers the question being asked gets read; one that has to be
translated first gets skipped.

"Sashin friendly" and "Sashin with a swap" also keep the pair legible next
to each other, which "no pork or beef" and "swap to make it his" did not --
the second did not obviously belong with the first.

WHAT DOES NOT CHANGE, deliberately: the rule underneath. It is still earned
by a READABLE ingredient list with neither meat in it, never by the absence
of a list -- an empty list proves nothing. And it is still not a vegetarian
filter: Sashin eats meat, so turkey bacon, chicken sausage and lamb
meatballs all keep the pill.

ONE CONSEQUENCE WORTH KNOWING: the label is also the filter value in the
URL, so a bookmarked link filtered on the old text stops matching. Nothing
stored changes -- the labels are computed per request -- so the only cost is
an old bookmark, and there is no migration to run.
"""
import io
import sys

P = "app/diet.py"
s = io.open(P, encoding="utf-8").read()
if "Sashin friendly" in s:
    sys.exit("already patched")

OLD = '''PILL = "no pork or beef"'''
NEW = '''# NAMED FOR THE PERSON, NOT THE PROPERTY (2026-09-07). This read "no pork
# or beef", which is a fact about the food. Jason does not scan the page
# thinking about pork -- he scans it wondering whether Sashin can eat it,
# and a label that answers the question being asked gets read where one
# that has to be translated first gets skipped.
#
# The label is also the filter value in the URL, so an old bookmark
# filtered on the previous wording stops matching. Nothing is stored --
# these are computed per request -- so that is the whole cost.
PILL = "Sashin friendly"'''
assert OLD in s, "PILL not found"
s = s.replace(OLD, NEW, 1)

OLD2 = '''PILL_SWAP = "swap to make it his"'''
NEW2 = '''# Renamed alongside PILL so the two read as a pair. "no pork or beef" and
# "swap to make it his" did not obviously belong together on the same card;
# these do, and the difference between them is legible at a glance.
PILL_SWAP = "Sashin with a swap"'''
assert OLD2 in s, "PILL_SWAP not found"
s = s.replace(OLD2, NEW2, 1)

io.open(P, "w", encoding="utf-8", newline="\n").write(s)
print("diet.py: pills renamed to 'Sashin friendly' / 'Sashin with a swap'")
