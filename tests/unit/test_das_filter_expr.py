"""Grammar tests for the DAS Explorer filter expression language. parse_expression
returns a tuple AST precisely so these run with no database column involved."""
import pytest

from data_viz.das_filter_expr import (
    MAX_EXPRESSION_LENGTH,
    MAX_NESTING_DEPTH,
    FilterSyntaxError,
    _escape_like,
    _walk,
    parse_expression,
)


class TestTerms:
    def test_single_term(self):
        assert parse_expression("fentanyl") == ("term", "fentanyl")

    def test_adjacent_bare_words_merge_into_one_phrase(self):
        assert parse_expression("black tar heroin") == ("term", "black tar heroin")

    def test_quoted_phrase_is_verbatim(self):
        assert parse_expression('"black tar heroin"') == ("term", "black tar heroin")

    def test_adjacent_quoted_phrases_are_an_error_not_an_implicit_and(self):
        # Only bare words merge; combining phrases needs an explicit operator.
        with pytest.raises(FilterSyntaxError, match="after the expression"):
            parse_expression('"black tar" "heroin"')

    def test_quoted_phrase_adjacent_to_bare_word_is_an_error(self):
        with pytest.raises(FilterSyntaxError, match="after the expression"):
            parse_expression('"black tar" heroin')

    def test_whitespace_runs_collapse_in_merged_phrase(self):
        assert parse_expression("black   tar") == ("term", "black tar")

    def test_quoted_keyword_is_a_literal_term(self):
        assert parse_expression('"and"') == ("term", "and")

    def test_embedded_star_stays_literal(self):
        assert parse_expression("fent*") == ("term", "fent*")

    def test_quoted_star_stays_literal(self):
        assert parse_expression('"*"') == ("term", "*")

    def test_standalone_star_is_wildcard_node(self):
        assert parse_expression("*") == ("star",)


class TestOperators:
    def test_and(self):
        assert parse_expression("a AND b") == ("and", [("term", "a"), ("term", "b")])

    def test_or(self):
        assert parse_expression("a OR b") == ("or", [("term", "a"), ("term", "b")])

    def test_not(self):
        assert parse_expression("NOT a") == ("not", ("term", "a"))

    def test_keywords_case_insensitive(self):
        assert parse_expression("a and b") == parse_expression("a AND b")
        assert parse_expression("a Or b") == parse_expression("a OR b")
        assert parse_expression("not a") == parse_expression("NOT a")

    def test_precedence_not_over_and_over_or(self):
        # a OR b AND NOT c  ->  or(a, and(b, not(c)))
        assert parse_expression("a OR b AND NOT c") == (
            "or", [("term", "a"), ("and", [("term", "b"), ("not", ("term", "c"))])])

    def test_parens_override_precedence(self):
        assert parse_expression("(a OR b) AND c") == (
            "and", [("or", [("term", "a"), ("term", "b")]), ("term", "c")])

    def test_infix_not_implies_and(self):
        assert parse_expression("a NOT b") == parse_expression("a AND NOT b")

    def test_double_not(self):
        assert parse_expression("NOT NOT a") == ("not", ("not", ("term", "a")))

    def test_chained_and_flattens(self):
        assert parse_expression("a AND b AND c") == (
            "and", [("term", "a"), ("term", "b"), ("term", "c")])

    def test_star_composes_with_not(self):
        # The documented "only cocaine" idiom.
        assert parse_expression("cocaine NOT *") == (
            "and", [("term", "cocaine"), ("not", ("star",))])


class TestErrors:
    @pytest.mark.parametrize("text,message", [
        ("", "empty expression"),
        ("   ", "empty expression"),
        ('"unterminated', "unterminated quote"),
        ('""', "empty quoted phrase"),
        ('"   "', "empty quoted phrase"),
        ("(a OR b", "missing closing parenthesis"),
        ("a AND", "expression ends after an operator"),
        ("NOT", "expression ends after an operator"),
        ("AND a", "unexpected 'AND'"),
        (")", "unexpected ')'"),
        ("a b) c", "unexpected ')' after the expression"),
    ])
    def test_malformed_expressions(self, text, message):
        with pytest.raises(FilterSyntaxError, match=message.replace("(", r"\(").replace(")", r"\)")):
            parse_expression(text)

    def test_length_limit_boundary(self):
        assert parse_expression("a" * MAX_EXPRESSION_LENGTH) == ("term", "a" * MAX_EXPRESSION_LENGTH)
        with pytest.raises(FilterSyntaxError, match=f"longer than {MAX_EXPRESSION_LENGTH}"):
            parse_expression("a" * (MAX_EXPRESSION_LENGTH + 1))

    def test_nesting_limit_boundary(self):
        ok = "(" * MAX_NESTING_DEPTH + "a" + ")" * MAX_NESTING_DEPTH
        assert parse_expression(ok) == ("term", "a")
        too_deep = "(" * (MAX_NESTING_DEPTH + 1) + "a" + ")" * (MAX_NESTING_DEPTH + 1)
        with pytest.raises(FilterSyntaxError, match=f"more than {MAX_NESTING_DEPTH} nested groups"):
            parse_expression(too_deep)


class TestHelpers:
    @pytest.mark.parametrize("raw,escaped", [
        ("100%", "100\\%"),
        ("a_b", "a\\_b"),
        ("back\\slash", "back\\\\slash"),
        ("plain", "plain"),
    ])
    def test_escape_like(self, raw, escaped):
        assert _escape_like(raw) == escaped

    def test_walk_yields_every_node(self):
        tree = parse_expression("a OR (b AND NOT c)")
        kinds = sorted(node[0] for node in _walk(tree))
        assert kinds == ["and", "not", "or", "term", "term", "term"]
