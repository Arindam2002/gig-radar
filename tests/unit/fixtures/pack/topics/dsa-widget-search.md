# DSA: widget search

## Concept

A widget scan walks the list once and keeps a running summary of everything
behind it, so the second pass the brute force needs never happens.

The pointer only moves forward, which is what makes the whole scan linear
rather than quadratic.

## Worked example

Given `[3, 1, 4, 1, 5]`, the running minimum is `3, 1, 1, 1, 1` and the best
gap is `4`.

## Pitfalls

Resetting the running summary inside the loop throws away the work that made
the scan linear in the first place.
