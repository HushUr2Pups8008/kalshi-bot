# Fail-Closed Source Timestamp Plan

1. Add failing RSS tests for absent and malformed timestamps, valid `updated`
   fallback, and `poll_feed` propagation of `published=None`.
2. Change RSS `_parse_date` to model absent or malformed metadata as `None`.
3. Add failing GDELT parser and callback-propagation tests, then change
   `_parse_seendate` to return `None` on invalid or missing values.
4. Run focused lint and tests covering RSS/search, GDELT, and the central
   `missing_timestamp` gate; independently review the full diff.
5. Publish only after CI. Restart under the existing safety controls and verify
   invalid metadata reaches `missing_timestamp`, with no order-mode changes.
