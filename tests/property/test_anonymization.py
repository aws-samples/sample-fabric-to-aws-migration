"""Property tests: SQL anonymization never leaks literal/credential material.

This is the AppSec-load-bearing invariant for the tool: whatever SQL the surface
analyzer ingests, no original single-quoted string literal survives into the
anonymized output, so a secret pasted into a view/query body cannot reach a
report. Mirrors the reference tool's anonymization property suite.
"""
from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from fabric_assess.core.sql_surface import SQLSurfaceAnalyzer

_analyzer = SQLSurfaceAnalyzer()

# A secret value: distinctive alphanumeric content that cannot collide with SQL
# punctuation/operators. We prefix a fixed marker so the generated value is always
# a multi-character token with no standalone meaning in the surrounding SQL.
_secret_body = st.text(
    alphabet=st.characters(min_codepoint=48, max_codepoint=122, blacklist_characters="'"),
    min_size=1,
    max_size=40,
)


@given(body=_secret_body)
def test_string_literal_never_survives(body):
    """A quoted literal value never appears verbatim in anonymized output."""
    secret = f"SEKRET_{body}_END"
    sql = f"SELECT * FROM t WHERE token = '{secret}'"  # nosec B608 - test input for anonymize(); never executed
    out = _analyzer.anonymize(sql)
    assert secret not in out
    assert "'?'" in out


@given(
    user_body=_secret_body,
    pwd_body=_secret_body,
)
def test_multiple_literals_all_stripped(user_body, pwd_body):
    user = f"USER_{user_body}_U"
    pwd = f"PWD_{pwd_body}_P"
    sql = f"SELECT * FROM users WHERE name = '{user}' AND password = '{pwd}'"  # nosec B608 - test input for anonymize(); never executed
    out = _analyzer.anonymize(sql)
    assert user not in out
    assert pwd not in out


@given(n=st.integers(min_value=1000, max_value=10**12))
def test_numeric_literal_stripped_after_operator(n):
    out = _analyzer.anonymize(f"WHERE amount > {n}")
    # The specific numeric value must not survive as a standalone comparison operand.
    assert str(n) not in out


@given(sql=st.text(max_size=200))
def test_anonymize_is_total(sql):
    """Anonymize never raises and always returns a string for any input."""
    out = _analyzer.anonymize(sql)
    assert isinstance(out, str)


@given(sql=st.text(max_size=200))
def test_detect_is_total(sql):
    """Detect never raises and returns a list for any input."""
    result = _analyzer.detect(sql)
    assert isinstance(result, list)
