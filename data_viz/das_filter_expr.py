# External Dependency Imports
from sqlalchemy import and_, or_, not_, false, func, select

# The DAS Explorer's text-filter expression language: AND / OR / NOT, parentheses, and quoted
# phrases over case-insensitive substring terms, e.g.
#     (fentanyl AND cocaine) OR lsd
#     fentanyl AND NOT cocaine
#     "black tar heroin" OR opium
# Plain spaces are literal -- adjacent bare words form ONE phrase ("black tar heroin" needs no
# quotes); combining terms always takes an explicit operator, except that an infix NOT implies
# the AND ("cocaine NOT fentanyl" == "cocaine AND NOT fentanyl"). Keywords match
# case-insensitively, so a standalone literal and/or/not must be quoted.
# Precedence: NOT > AND > OR.
#
# Fields that opt in (allow_star; currently only Drugs Identified) also get a * wildcard meaning
# "anything else": * matches any entry of the "; "-joined list value that no term elsewhere in the
# expression matches, so `cocaine NOT *` reads "cocaine and nothing else". Only a standalone bare
# * is the wildcard -- inside a quoted phrase or embedded in a word (fent*) it stays a literal
# character. On fields that don't opt in, a standalone * is a FilterSyntaxError.
#
# parse_expression() returns a tuple AST so the grammar is testable without a database column;
# compile_expression() turns an expression into a SQLAlchemy clause for one column. The client
# keeps a mirror validator (dasValidExpression in dasExplorer.js) purely as typing-time UX --
# this module is the authority, and both APIs 400 on FilterSyntaxError (see main.py).

MAX_EXPRESSION_LENGTH = 300
MAX_NESTING_DEPTH = 10

_KEYWORDS = {"AND", "OR", "NOT"}


class FilterSyntaxError(ValueError):
    """A filter expression that doesn't parse. Routes surface it as a 400 -- a malformed
    expression must never silently degrade into a different (wrong) filter."""


def _tokenize(text):
    """-> list of ("op", "AND"|"OR"|"NOT") | ("paren", "("|")") | ("phrase", str) | ("star", "*").

    Bare words merge with their bare-word neighbors into a single space-joined phrase; quoted
    phrases are verbatim and never merge."""
    tokens = []
    words = []   # pending run of bare words -> one phrase

    def flush_words():
        if words:
            tokens.append(("phrase", " ".join(words)))
            words.clear()

    i = 0
    while i < len(text):
        char = text[i]
        if char.isspace():
            i += 1
        elif char in "()":
            flush_words()
            tokens.append(("paren", char))
            i += 1
        elif char == '"':
            end = text.find('"', i + 1)
            if end == -1:
                raise FilterSyntaxError("unterminated quote")
            phrase = text[i + 1:end].strip()
            if not phrase:
                raise FilterSyntaxError("empty quoted phrase")
            flush_words()
            tokens.append(("phrase", phrase))
            i = end + 1
        else:
            end = i
            while end < len(text) and not text[end].isspace() and text[end] not in '()"':
                end += 1
            word = text[i:end]
            if word == "*":
                flush_words()
                tokens.append(("star", "*"))
            elif word.upper() in _KEYWORDS:
                flush_words()
                tokens.append(("op", word.upper()))
            else:
                words.append(word)
            i = end
    flush_words()
    return tokens


def parse_expression(text):
    """Parse a filter expression into a tuple AST:
        ("term", phrase) | ("star",) | ("and", [nodes]) | ("or", [nodes]) | ("not", node)
    Raises FilterSyntaxError on anything malformed."""
    if len(text) > MAX_EXPRESSION_LENGTH:
        raise FilterSyntaxError(f"expression longer than {MAX_EXPRESSION_LENGTH} characters")
    tokens = _tokenize(text)
    if not tokens:
        raise FilterSyntaxError("empty expression")
    pos = 0

    def peek():
        return tokens[pos] if pos < len(tokens) else (None, None)

    def parse_or(depth):
        nodes = [parse_and(depth)]
        while peek() == ("op", "OR"):
            nonlocal pos
            pos += 1
            nodes.append(parse_and(depth))
        return nodes[0] if len(nodes) == 1 else ("or", nodes)

    def parse_and(depth):
        # An infix NOT implies the AND ("a NOT b" == "a AND NOT b"); parse_not consumes it.
        nodes = [parse_not(depth)]
        while peek() in (("op", "AND"), ("op", "NOT")):
            nonlocal pos
            if peek() == ("op", "AND"):
                pos += 1
            nodes.append(parse_not(depth))
        return nodes[0] if len(nodes) == 1 else ("and", nodes)

    def parse_not(depth):
        nonlocal pos
        if peek() == ("op", "NOT"):
            pos += 1
            return ("not", parse_not(depth))
        return parse_atom(depth)

    def parse_atom(depth):
        nonlocal pos
        kind, value = peek()
        if kind == "paren" and value == "(":
            if depth >= MAX_NESTING_DEPTH:
                raise FilterSyntaxError(f"more than {MAX_NESTING_DEPTH} nested groups")
            pos += 1
            node = parse_or(depth + 1)
            if peek() != ("paren", ")"):
                raise FilterSyntaxError("missing closing parenthesis")
            pos += 1
            return node
        if kind == "phrase":
            pos += 1
            return ("term", value)
        if kind == "star":
            pos += 1
            return ("star",)
        if kind is None:
            raise FilterSyntaxError("expression ends after an operator")
        raise FilterSyntaxError(f"unexpected '{value}'")

    tree = parse_or(0)
    if pos < len(tokens):
        raise FilterSyntaxError(f"unexpected '{tokens[pos][1]}' after the expression")
    return tree


def _escape_like(phrase):
    """Escape SQL LIKE wildcards so a term like 100% matches literally."""
    return phrase.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _walk(tree):
    """Yield every AST node, any depth."""
    stack = [tree]
    while stack:
        node = stack.pop()
        yield node
        kind = node[0]
        if kind in ("and", "or"):
            stack.extend(node[1])
        elif kind == "not":
            stack.append(node[1])


def _star_clause(column, terms, separator):
    """EXISTS an entry of the separator-joined list value that no term matches. `terms` is every
    term in the expression regardless of NOT polarity -- the documented "any entry not matched by
    any term you typed" rule. With no terms at all, any entry qualifies, so a bare * just means
    the field is non-empty (and NOT * matches only NULL/empty values)."""
    entry = func.unnest(func.string_to_array(column, separator)).column_valued("entry")
    matched = (or_(*(entry.ilike(f"%{_escape_like(t)}%", escape="\\") for t in terms))
               if terms else false())
    return select(entry).where(not_(matched)).exists()


def compile_expression(column, text, allow_star=False, entry_separator="; "):
    """A SQLAlchemy boolean clause applying the expression to `column` (substring terms).
    `allow_star` enables the * "anything else" wildcard for list-valued columns."""
    tree = parse_expression(text)
    star = None
    if any(node[0] == "star" for node in _walk(tree)):
        if not allow_star:
            raise FilterSyntaxError("'*' is not supported in this filter")
        terms = [node[1] for node in _walk(tree) if node[0] == "term"]
        star = _star_clause(column, terms, entry_separator)

    def clause(node):
        kind = node[0]
        if kind == "term":
            return column.ilike(f"%{_escape_like(node[1])}%", escape="\\")
        if kind == "star":
            return star
        if kind == "and":
            return and_(*(clause(child) for child in node[1]))
        if kind == "or":
            return or_(*(clause(child) for child in node[1]))
        return not_(clause(node[1]))   # "not"

    return clause(tree)
