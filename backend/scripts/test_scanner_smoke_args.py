# tests/test_scanner_smoke_args.py
from scripts.scanner_smoke import build_arg_parser, build_overrides_from_args
def test_no_duplicate_explain_cache():
    p = build_arg_parser()
    args = p.parse_args(["-x","gate","--preset","balanced","--depth-levels-bps","5,10","--min-depth5-usd","2000"])
    overrides = build_overrides_from_args(args)
    assert "explain" not in overrides and "use_cache" not in overrides
    assert overrides["depth_levels_bps"] == [5,10]
    assert overrides["min_depth5_usd"] == 2000.0
