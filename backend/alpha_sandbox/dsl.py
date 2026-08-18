"""
Alpha DSL — restricted expression language for candidate alphas (Phase 1).

Alphas are plain math expressions over OHLCV columns. The expression is parsed
with `ast` and EVERY node is checked against a strict whitelist before
evaluation, so arbitrary code (imports, attribute access, subscripts, loops)
can never run. All operators are causal (rolling/shift/ewm with min_periods)
— no look-ahead by construction.
"""
import ast
import logging

import pandas as pd

logger = logging.getLogger(__name__)

COLUMNS = {"open", "high", "low", "close", "volume"}


# ── Causal indicator library ────────────────────────────────────────────────
def _sma(x: pd.Series, n) -> pd.Series:
    n = int(n)
    return x.rolling(n, min_periods=n).mean()

def _ema(x: pd.Series, n) -> pd.Series:
    n = int(n)
    return x.ewm(span=n, min_periods=n, adjust=False).mean()

def _std(x: pd.Series, n) -> pd.Series:
    n = int(n)
    return x.rolling(n, min_periods=n).std()

def _delta(x: pd.Series, n) -> pd.Series:
    return x - x.shift(int(n))

def _zscore(x: pd.Series, n) -> pd.Series:
    return (x - _sma(x, n)) / _std(x, n).replace(0, pd.NA)

def _rank(x: pd.Series, n) -> pd.Series:
    """Rolling percentile rank of current value within last n bars (0..1)."""
    return x.rolling(int(n), min_periods=int(n)).rank(pct=True)

def _rsi(x: pd.Series, n) -> pd.Series:
    n = int(n)
    d = x.diff()
    gain = d.clip(lower=0).ewm(alpha=1.0 / n, min_periods=n, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1.0 / n, min_periods=n, adjust=False).mean()
    rs = gain / loss.replace(0, pd.NA)
    return 100 - 100 / (1 + rs)

def _macd(x: pd.Series) -> pd.Series:
    return _ema(x, 12) - _ema(x, 26)

def _macd_hist(x: pd.Series) -> pd.Series:
    m = _macd(x)
    return m - _ema(m, 9)

def _volume_ratio(x: pd.Series, n) -> pd.Series:
    return x / _sma(x, n).replace(0, pd.NA)

FUNCTIONS = {
    "sma": _sma, "ema": _ema, "std": _std, "delta": _delta,
    "zscore": _zscore, "rank": _rank, "rsi": _rsi,
    "macd": _macd, "macd_hist": _macd_hist, "volume_ratio": _volume_ratio,
    "abs": lambda x: x.abs(),
}

_ALLOWED_BIN = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.BitAnd, ast.BitOr)
_ALLOWED_UNARY = (ast.USub, ast.Invert)
_ALLOWED_CMP = (ast.Gt, ast.Lt, ast.GtE, ast.LtE, ast.Eq, ast.NotEq)


class DSLValidationError(ValueError):
    """Raised when an alpha expression uses forbidden syntax."""


class _LogicToBitwise(ast.NodeTransformer):
    """`and`/`or`/`not` on Series would call __bool__ → rewrite to & | ~."""

    def visit_BoolOp(self, node):
        self.generic_visit(node)
        cls = ast.BitAnd if isinstance(node.op, ast.And) else ast.BitOr
        expr = node.values[0]
        for value in node.values[1:]:
            expr = ast.BinOp(left=expr, op=cls(), right=value)
        return expr

    def visit_UnaryOp(self, node):
        self.generic_visit(node)
        if isinstance(node.op, ast.Not):
            return ast.UnaryOp(op=ast.Invert(), operand=node.operand)
        return node


def _check_node(node):
    if isinstance(node, (ast.Expression, ast.Load, *_ALLOWED_BIN, *_ALLOWED_UNARY, *_ALLOWED_CMP)):
        return
    if isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float, bool)):
            raise DSLValidationError(f"forbidden constant: {node.value!r}")
        return
    if isinstance(node, ast.Name):
        if node.id not in COLUMNS and node.id not in FUNCTIONS:
            raise DSLValidationError(f"unknown name '{node.id}'")
        return
    if isinstance(node, ast.BinOp):
        if not isinstance(node.op, _ALLOWED_BIN):
            raise DSLValidationError(f"forbidden operator: {type(node.op).__name__}")
        return
    if isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, _ALLOWED_UNARY):
            raise DSLValidationError("forbidden unary operator")
        return
    if isinstance(node, ast.Compare):
        for op in node.ops:
            if not isinstance(op, _ALLOWED_CMP):
                raise DSLValidationError(f"forbidden comparison: {type(op).__name__}")
        return
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS:
            raise DSLValidationError("only whitelisted function calls are allowed")
        if node.keywords:
            raise DSLValidationError("keyword arguments are not allowed")
        return
    raise DSLValidationError(f"forbidden syntax: {type(node).__name__}")


def _prepare(expr: str) -> ast.Expression:
    tree = _LogicToBitwise().visit(ast.parse(expr, mode="eval"))
    ast.fix_missing_locations(tree)
    for node in ast.walk(tree):
        _check_node(node)
    return tree


def validate_expression(expr: str):
    """Return (ok, error_message). Never raises."""
    try:
        _prepare(expr)
    except SyntaxError as e:
        return False, f"syntax error: {e}"
    except DSLValidationError as e:
        return False, str(e)
    return True, None


def evaluate_expression(expr: str, df: pd.DataFrame) -> pd.Series:
    """Safely evaluate a validated expression over an OHLCV DataFrame."""
    tree = _prepare(expr)  # raises DSLValidationError on forbidden syntax
    env = {col: df[col].astype(float) for col in COLUMNS if col in df.columns}
    env.update(FUNCTIONS)
    code = compile(tree, "<alpha>", "eval")
    result = eval(code, {"__builtins__": {}}, env)
    if isinstance(result, (int, float, bool)):
        result = pd.Series(float(result), index=df.index)
    if not isinstance(result, pd.Series):
        raise DSLValidationError("expression must produce a series")
    return result.replace([float("inf"), float("-inf")], float("nan"))
